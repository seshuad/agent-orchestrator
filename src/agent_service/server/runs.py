"""Runs started from the designer: start, watch, read, approve, stop.

Each run is `conductor run` in web mode on its own port, in its own run directory, with the run
id fixed (CONDUCTOR_RUN_ID) so its event log can be found. A watcher thread reads the event log;
when the workflow ends it records the result and stops the process (web mode keeps the
dashboard up otherwise). Approvals are answered with `conductor gate respond`, which finds the
run's gate token itself.

The readable log turns Conductor's events into one line per step a builder cares about: the
planner's decision and reason, what each step returned, rules that held or didn't, the approver's
choice. Plumbing steps (collect, the compiler's helpers) are kept but marked, for "show every step".
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import yaml

from .. import definition, runner
from ..compiler import IN_GROUP
from .store import Conflict, NotFound, Store

EVENTS_DIR = Path(tempfile.gettempdir()) / "conductor"
PLUMBING = {"collect", "not_ready_hidden", "stop_rules_unmet", "stop_rule_error"}
END = {"workflow_completed", "workflow_failed"}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def friendly_error(message: str) -> dict[str, str]:
    """A failure in the builder's terms, plus what to do. The raw message stays in the details."""
    m = message or ""
    if "tool_choice" in m and "not supported" in m:
        model = m.split("model_name: ")[1].split(",")[0] if "model_name: " in m else "This model"
        return {"title": "Claude rejected the request before doing any work",
                "why": f"{model} can't return results in the form this agent's steps need.",
                "fix": "Choose Claude Opus 5, Sonnet 5 or Haiku 4.5 for that step, then run again."}
    if "authentication" in m.lower() or "api_key" in m.lower() or "x-api-key" in m.lower():
        return {"title": "The service couldn't sign in to Claude",
                "why": "No valid Claude API key is set where the service runs.",
                "fix": "Set ANTHROPIC_API_KEY for the service, or run with scripted answers."}
    missing = re.search(r"Missing required workflow input: (\w+)", m)
    if missing:
        return {"title": f"The run option {missing.group(1)!r} is missing",
                "why": f"A step reads the run option {missing.group(1)!r}, but this version of the agent doesn't have it.",
                "fix": "Open the agent: the step that reads it is marked. Pick another option there, or add it back in Settings."}
    if "budget" in m.lower():
        return {"title": "The run reached its spending limit", "why": "It stopped rather than spend more.",
                "fix": "Raise the limit in Settings, or look at the log for the step that used the most."}
    if "WorkflowTerminated" in m or "terminated" in m.lower():
        return {"title": "The run stopped on purpose", "why": m.split(": ", 1)[-1], "fix": "See the step before it in the log."}
    if "command_not_found" in m or "not found" in m.lower() and "command" in m.lower():
        return {"title": "A program the run needs is missing", "why": m, "fix": "Reinstall the service (uv sync)."}
    return {"title": "The run failed", "why": m, "fix": "See the technical details, and report it if it keeps happening."}


class Runs:
    def __init__(self, store: Store):
        self.store = store
        self.procs: dict[str, subprocess.Popen] = {}
        self.replays: dict[str, tuple[subprocess.Popen, int]] = {}    # run id -> Conductor's replay dashboard
        self.lock = threading.Lock()

    # -------------------------------------------------------------- records

    def _dir(self, run_id: str) -> Path:
        d = self.store.runs_root() / run_id
        if not (d / "run.json").exists():
            raise NotFound(f"No run {run_id}.")
        return d

    def record(self, run_id: str) -> dict[str, Any]:
        rec = json.loads((self._dir(run_id) / "run.json").read_text())
        if rec["status"] in ("running", "waiting") and run_id not in self.procs:
            rec.update(status="failed", ended_at=rec.get("ended_at") or time.time(),
                       error={"title": "The service restarted during this run", "why": "The run couldn't be followed after the restart.",
                              "fix": "Run it again.", "raw": ""})
            self._save(rec)
        return rec

    def _save(self, rec: dict[str, Any]) -> None:
        (self.store.runs_root() / rec["id"] / "run.json").write_text(json.dumps(rec, indent=1))

    def list(self, agent: str | None = None) -> list[dict[str, Any]]:
        out = []
        for d in self.store.runs_root().iterdir():
            if (d / "run.json").exists():
                rec = self.record(d.name)
                if agent is None or rec["agent"] == agent:
                    out.append(rec)
        return sorted(out, key=lambda r: r["started_at"], reverse=True)

    def delete_for(self, agent: str) -> int:
        """Deletes an agent's run history; refused while any of its runs is in progress."""
        mine = self.list(agent)
        if any(r["status"] in ("running", "waiting") for r in mine):
            raise Conflict(f"{agent} has a run in progress. Stop it (or answer its approval) first.")
        for r in mine:
            replay = self.replays.pop(r["id"], None)
            if replay is not None:
                replay[0].terminate()
            shutil.rmtree(self.store.runs_root() / r["id"], ignore_errors=True)
        return len(mine)

    # -------------------------------------------------------------- start

    def start(self, agent_name: str, *, version: int | None, inputs: dict[str, str], email_id: str | None,
              scripted: bool, started_by: str, live: bool = False, trigger_email: dict[str, Any] | None = None) -> dict[str, Any]:
        """With `live`, Gmail steps read the real accounts their connections are signed in to."""
        meta = self.store.meta(agent_name)
        raw = self.store.version(agent_name, version)
        agent = definition.Agent.model_validate(raw)
        if not meta.get("sample_data"):
            raise runner.RunError("Pick the test data this agent runs on, in Settings.")
        if scripted and not meta.get("replay"):
            raise runner.RunError("This agent has no scripted answers; run it with the Claude API.")
        run_id = secrets.token_hex(4)
        prepared = runner.prepare(agent, sample_data=Path(meta["sample_data"]), runs_root=self.store.runs_root(),
                                  inputs=inputs, email_id=email_id, replay=Path(meta["replay"]) if scripted else None,
                                  replay_gates=False, run_id=run_id, vault=self.store.home / "vault", live=live,
                                  trigger_email=trigger_email, accounts=self.store.accounts(),
                                  connectors={c["id"]: c for c in self.store.connectors()})
        (prepared.run_dir / "agent.yaml").write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
        port = _free_port()
        rec = {"id": run_id, "agent": agent_name, "version": version, "started_by": started_by, "trigger": "manual",
               "scripted": scripted, "source": "live" if live else "sample", "inputs": prepared.inputs, "started_at": time.time(), "ended_at": None,
               "status": "running", "port": port, "error": None, "gate": None}
        self._save(rec)
        env = {**prepared.env, "CONDUCTOR_RUN_ID": run_id}
        proc = subprocess.Popen(prepared.command + ["--web", "--web-port", str(port)], env=env, stdin=subprocess.DEVNULL,
                                stdout=open(prepared.run_dir / "conductor.log", "w"), stderr=subprocess.STDOUT)
        with self.lock:
            self.procs[run_id] = proc
        threading.Thread(target=self._watch, args=(run_id,), daemon=True).start()
        return rec

    def events_path(self, run_id: str) -> Path | None:
        hits = sorted(EVENTS_DIR.glob(f"conductor-*-{run_id}.events.jsonl"))
        return hits[-1] if hits else None

    def events(self, run_id: str) -> list[dict[str, Any]]:
        path = self.events_path(run_id)
        if path is None:
            return []
        out = []
        for line in path.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass                                 # a line still being written
        return out

    def _watch(self, run_id: str) -> None:
        proc = self.procs[run_id]
        while True:
            time.sleep(0.4)
            evs = self.events(run_id)
            rec = json.loads((self.store.runs_root() / run_id / "run.json").read_text())
            gate = next((e for e in reversed(evs) if e["type"] in ("gate_presented", "gate_resolved")), None)
            waiting = gate is not None and gate["type"] == "gate_presented"
            if rec["status"] in ("running", "waiting"):
                rec["status"] = "waiting" if waiting else "running"
                rec["gate"] = gate["data"] if waiting else None
                self._save(rec)
            end = next((e for e in evs if e["type"] in END), None)
            if end is not None or proc.poll() is not None:
                break
        rec = json.loads((self.store.runs_root() / run_id / "run.json").read_text())
        problem = self._problem(run_id, evs)
        if rec["status"] in ("running", "waiting"):
            if problem is not None:
                rec["status"], rec["error"] = "failed", problem
            elif end is not None and end["type"] == "workflow_completed":
                rec["status"] = "succeeded"
            else:
                raw = end["data"].get("message", "") if end else self._log_tail(run_id)
                stopped = end is not None and end["data"].get("error_type") == "WorkflowTerminated"
                rec["status"] = "stopped" if stopped else "failed"
                rec["error"] = {**friendly_error((end["data"].get("error_type", "") + ": " + raw) if end else raw), "raw": raw}
        rec["ended_at"] = time.time()
        rec["gate"] = None
        self._save(rec)
        if proc.poll() is None:
            proc.terminate()                         # web mode keeps the dashboard up after the run ends
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        with self.lock:
            self.procs.pop(run_id, None)

    def _problem(self, run_id: str, evs: list[dict[str, Any]]) -> dict[str, str] | None:
        """What went wrong that Conductor doesn't count as failure: a connection that never started (its steps ran
        without their tools), or a step that crashed (the compiled workflow stops, but Conductor calls it terminated)."""
        log_path = self.store.runs_root() / run_id / "conductor.log"
        log = log_path.read_text() if log_path.exists() else ""
        m = re.search(r"Failed to connect to MCP server '([^']+)'", log)
        if m:
            cause = next((l.strip() for l in log.splitlines() if re.match(r"\s*\w*(Error|Exception): ", l) and "TaskGroup" not in l), "")
            return {"title": "A connection couldn't start",
                    "why": f"The {m.group(1)} connection didn't start, so the steps that use it ran without it and their results "
                           "can't be trusted." + (f" It said: {cause.split(': ', 1)[-1]}" if cause else ""),
                    "fix": "Check the connection on Connections (for real accounts, that it's signed in), then run again.",
                    "raw": cause or m.group(0)}
        if any(e["type"] == "agent_failed" and e["data"].get("agent_name") == "stop_step_failed" for e in evs):
            crashed = next((e["data"] for e in reversed(evs) if e["type"] == "script_completed" and e["data"].get("exit_code")), {})
            err = (crashed.get("stderr") or "").strip().splitlines()
            return {"title": f"A step failed: {crashed.get('agent_name', 'a step')}",
                    "why": err[-1] if err else "It stopped with an error.", "fix": "Check that step's settings in the editor.",
                    "raw": "\n".join(err[-15:])}
        return None

    def _log_tail(self, run_id: str) -> str:
        path = self.store.runs_root() / run_id / "conductor.log"
        return path.read_text()[-1500:] if path.exists() else "The run ended before Conductor started."

    # -------------------------------------------------------------- approve and stop

    def approve(self, run_id: str, choice: str, ids: str | None) -> dict[str, Any]:
        rec = self.record(run_id)
        if rec["status"] != "waiting" or not rec["gate"]:
            raise runner.RunError("This run isn't waiting for an approval.")
        if choice not in rec["gate"]["options"]:
            raise runner.RunError(f"{choice!r} isn't one of the choices.")
        cmd = ["conductor", "gate", "respond", "-p", str(rec["port"]), "-c", choice, "--agent", rec["gate"]["agent_name"]]
        if ids:
            cmd += ["--input", ids]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            raise runner.RunError(f"The approval couldn't be sent: {(out.stdout + out.stderr).strip()[-300:]}")
        rec["approved_by"] = self.store.workspace()["user"]["name"]
        self._save(rec)
        return rec

    def stop(self, run_id: str) -> dict[str, Any]:
        rec = self.record(run_id)
        proc = self.procs.get(run_id)
        if proc is None or rec["status"] not in ("running", "waiting"):
            raise runner.RunError("This run has already ended.")
        rec.update(status="stopped", error={"title": "Stopped by " + self.store.workspace()["user"]["name"],
                                            "why": "The run was stopped before it finished.", "fix": "", "raw": ""})
        self._save(rec)
        proc.terminate()
        return rec

    # -------------------------------------------------------------- Conductor's own dashboard

    def conductor_ui(self, run_id: str) -> dict[str, str]:
        """Where to see this run in Conductor's dashboard: the live one while it runs, else a replay of its event log."""
        rec = self.record(run_id)
        proc = self.procs.get(run_id)
        if rec["status"] in ("running", "waiting") and proc is not None and proc.poll() is None:
            return {"url": f"http://127.0.0.1:{rec['port']}", "mode": "live"}
        with self.lock:
            existing = self.replays.get(run_id)
            if existing and existing[0].poll() is None:
                return {"url": f"http://127.0.0.1:{existing[1]}", "mode": "replay"}
            events = self.events_path(run_id)
            if events is None:
                raise runner.RunError("This run has no event log to replay.")
            while len(self.replays) >= 3:                     # keep a few replays, stop the oldest
                old, (old_proc, _) = next(iter(self.replays.items()))
                old_proc.terminate()
                del self.replays[old]
            port = _free_port()
            log = open(self._dir(run_id) / "conductor-replay.log", "w")
            replay = subprocess.Popen(["conductor", "replay", "--web-port", str(port), str(events)],
                                      stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            self.replays[run_id] = (replay, port)
        for _ in range(60):                                   # wait until the dashboard answers
            if replay.poll() is not None:
                raise runner.RunError("Conductor's replay viewer didn't start; see conductor-replay.log in the run directory.")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    return {"url": f"http://127.0.0.1:{port}", "mode": "replay"}
            except OSError:
                time.sleep(0.2)
        raise runner.RunError("Conductor's replay viewer is taking too long to start.")

    def close(self) -> None:
        for proc, _ in self.replays.values():
            proc.terminate()

    # -------------------------------------------------------------- the readable log

    def detail(self, run_id: str) -> dict[str, Any]:
        rec = self.record(run_id)
        run_dir = self._dir(run_id)
        raw = yaml.safe_load((run_dir / "agent.yaml").read_text()) if (run_dir / "agent.yaml").exists() else {}
        evs = self.events(run_id)
        entries, totals = build_log(evs, raw, run_dir)
        calls = [json.loads(l) for l in (run_dir / "gateway.jsonl").read_text().splitlines()] if (run_dir / "gateway.jsonl").exists() else []
        return {**rec, **totals, "log": entries,
                "checks": {"calls": len(calls), "refused": [c for c in calls if c["outcome"] == "refused"]},
                "outcome": _outcome(run_dir), "events_file": str(self.events_path(run_id) or "")}


def _names(raw: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """Conductor step name -> (what the builder calls it, kind)."""
    out: dict[str, tuple[str, str]] = {"plan": ("Plan", "planner"), "finish_check": ("Before finishing", "rules"),
                                       "not_ready": ("Refused", "rules"), "collect": ("Collect results", "plumbing")}

    def walk(steps: list[dict[str, Any]]) -> None:
        for s in steps or []:
            shows = s.get("kind") == "built-in" and "show" in (s.get("operation") or {})
            out[s["id"]] = (s.get("name", s["id"]), "show" if shows else s.get("kind", ""))
            if s.get("kind") == "approve":
                out[f"{s['id']}_preselect"] = (f"{s.get('name')}: pre-select", "rules")
            if s.get("kind") == "free-form":
                out[f"{s['id']}_together_record"] = ("Record answers", "plumbing")
                for i in range(2, 10):
                    out[f"{s['id']}_together_{i}_record"] = ("Record answers", "plumbing")
            walk(s.get("steps", []))
    walk(raw.get("steps", []))
    return out


def _brief(output: Any) -> str:
    """One line about a step's output: counts for lists, short values otherwise."""
    if not isinstance(output, dict):
        return ""
    parts = []
    for k, v in output.items():
        if k in ("stdout", "stderr", "exit_code", "reason", "notes", "error", "next", "focus"):
            continue
        if isinstance(v, list):
            parts.append(f"{len(v)} {k.replace('_', ' ')}")
        elif isinstance(v, bool):
            parts.append(f"{k.replace('_', ' ')}: {'yes' if v else 'no'}")
        elif isinstance(v, (int, float, str)) and len(str(v)) < 60:
            parts.append(f"{k.replace('_', ' ')}: {v}")
    return ", ".join(parts)


def build_log(evs: list[dict[str, Any]], raw: dict[str, Any], run_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    names = _names(raw)
    t0 = evs[0]["timestamp"] if evs else time.time()
    wf_path = run_dir / "workflow.yaml"
    wf = yaml.safe_load(wf_path.read_text()) if wf_path.exists() else {}
    groups = {g["name"]: [a.removesuffix(IN_GROUP) for a in g["agents"]] for g in (wf or {}).get("parallel", [])}
    history = [json.loads(l) for l in (run_dir / "history.jsonl").read_text().splitlines()] if (run_dir / "history.jsonl").exists() else []
    seen: dict[str, int] = {}

    def recorded(step: str) -> Any:
        """The n-th recorded output of a step (Built-in and CEL steps record every run)."""
        n = seen.get(step, 0)
        seen[step] = n + 1
        outs = [h["output"] for h in history if h["step"] == step]
        return outs[n] if n < len(outs) else None

    entries: list[dict[str, Any]] = []
    tools: dict[str, list[dict[str, Any]]] = {}
    cost = tokens = 0.0
    for e in evs:
        t, d = e["type"], e["data"]
        name = d.get("agent_name", "").removesuffix(IN_GROUP)      # a group member runs as a copy of its step
        label, kind = names.get(name, (name, ""))
        at = round(e["timestamp"] - t0, 1)
        base = {"at": at, "step": label, "id": name, "kind": kind, "took": round(d.get("elapsed") or 0, 1), "cost": None,
                "detail": "", "why": None, "tone": "", "plumbing": name in PLUMBING, "tools": []}
        if t == "mcp_completed" and d.get("group_name"):
            continue                             # a scripted step inside a group: its parallel_agent_completed follows
        if t == "parallel_started":
            members = ", ".join(names.get(m.removesuffix(IN_GROUP), (m, ""))[0] for m in d.get("agents", []))
            entries.append({**base, "step": "Together", "kind": "group", "detail": f"Runs {members} at the same time", "plumbing": False})
        elif t == "parallel_completed":
            detail = f"All {d.get('success_count')} finished" if not d.get("failure_count") else \
                f"{d.get('success_count')} finished, {d.get('failure_count')} failed"
            entries.append({**base, "step": "Together", "kind": "group", "detail": f"{detail} in {round(d.get('elapsed') or 0, 1)}s",
                            "tone": "warn" if d.get("failure_count") else "", "plumbing": False})
        elif t in ("parallel_agent_completed", "mcp_completed") and kind == "ask":
            out = recorded(name)
            c = d.get("cost_usd") or 0.0
            cost += c
            tokens += d.get("tokens") or 0
            entries.append({**base, "cost": round(c, 4) if c else None, "model": d.get("model") or "scripted",
                            "tokens": d.get("tokens"), "tools": tools.pop(d.get("agent_name", ""), []), "detail": _brief(out),
                            "why": "In a group" if d.get("group_name") else None})
        elif t == "parallel_agent_failed":
            entries.append({**base, "detail": friendly_error(d.get("message", ""))["title"], "tone": "bad"})
        elif t == "agent_tool_start":
            tools.setdefault(d.get("agent_name", ""), []).append({"tool": d["tool_name"].split("__")[-1], "args": d.get("arguments", "")})
        elif t in ("agent_completed", "script_completed") and kind in ("ask", "planner"):
            out = d.get("output") if t == "agent_completed" else _json(d.get("stdout"))
            c = d.get("cost_usd") or 0.0
            cost += c
            tokens += d.get("tokens") or 0
            entry = {**base, "cost": round(c, 4) if c else None, "model": d.get("model") or ("scripted" if t == "script_completed" else None),
                     "tokens": d.get("tokens"), "tools": tools.pop(d.get("agent_name", ""), [])}
            if name == "plan" and isinstance(out, dict):
                nxt = out.get("next")
                target = names.get(nxt, (nxt, ""))[0]
                if nxt in groups:
                    target = " and ".join(names.get(m, (m, ""))[0] for m in groups[nxt]) + ", at the same time"
                entry["detail"] = ("Finish" + (f" as {out['outcome']}" if out.get("outcome") else "") if nxt == "finish"
                                   else f"Next: {target}" + (f", focused on {out['focus']}" if out.get("focus") else ""))
                entry["why"] = out.get("reason")
            else:
                entry["detail"] = _brief(out)
            entries.append(entry)
        elif t == "script_completed" and kind == "show":
            out = recorded(name) or _json(d.get("stdout")) or {}
            value = out.get("value") if isinstance(out, dict) else None
            count = f"{len(value)} item{'s' if len(value) != 1 else ''}" if isinstance(value, list) else "no value" if value is None else "a value"
            entries.append({**base, "detail": f"Shows {count}", "value": json.dumps(value, indent=1, ensure_ascii=False, default=str)})
        elif t == "script_completed":
            out = recorded(name) or _json(d.get("stdout"))
            entries.append({**base, "detail": _brief(out)})
        elif t == "mcp_completed":
            out = recorded(name) or {}
            if kind == "plumbing":
                entries.append({**base, "detail": "Kept each answer for the log", "plumbing": True})
                continue
            if name == "finish_check":
                detail = "Passed" if out.get("passed") else "Refused: " + " ".join(out.get("failed") or [])
                tone = "" if out.get("passed") else "warn"
            elif out.get("error"):
                detail, tone = out["error"], "bad"
            else:
                res = out.get("results", out.get("result"))
                detail, tone = (_brief(res) if isinstance(res, dict) else "Done"), ""
            entries.append({**base, "detail": detail, "tone": tone})
        elif t == "set_completed":
            val = _json(d.get("value_repr")) or {}
            detail = val.get("message", "") if isinstance(val, dict) else ""
            entries.append({**base, "detail": detail, "tone": "warn" if name == "not_ready" else "",
                            "plumbing": name == "collect"})
        elif t == "gate_presented":
            entries.append({**base, "detail": "Waiting for approval", "tone": "wait", "options": d.get("option_details", [])})
        elif t == "gate_resolved":
            label_of = {o.get("value"): o.get("label") for e2 in evs if e2["type"] == "gate_presented" and e2["data"].get("agent_name") == name
                        for o in e2["data"].get("option_details", [])}
            entries.append({**base, "detail": f"Chose: {label_of.get(d.get('selected_option'), d.get('selected_option'))}"})
        elif t in ("agent_failed", "mcp_failed") and d.get("error_type") != "WorkflowTerminated":
            entries.append({**base, "detail": friendly_error(d.get("message", ""))["title"], "tone": "bad"})
        elif t == "agent_parse_recovery":
            entries.append({**base, "detail": "Its answer didn't match the record type; asked it to try again.", "tone": "warn", "plumbing": True})
    return entries, {"cost_usd": round(cost, 4), "tokens": int(tokens),
                     "duration": round((evs[-1]["timestamp"] - t0), 1) if evs else 0}


def _outcome(run_dir: Path) -> dict[str, Any]:
    """The results a builder looks at first: what the Free-form block handed on, and what the Act step did."""
    out: dict[str, Any] = {}
    steps = run_dir / "steps"
    if (steps / "finish_check.json").exists():
        out["block"] = json.loads((steps / "finish_check.json").read_text()).get("results")
    for f in steps.glob("*.json") if steps.exists() else []:
        data = json.loads(f.read_text())
        if isinstance(data, dict) and {"created", "would_create"} & set(data):
            out["act"] = {"step": f.stem, **data}
        elif isinstance(data, dict) and {"added", "would_add"} <= set(data):
            fmt = lambda r: " · ".join(f"{k}: {v}" for k, v in r.items())
            out["act"] = {"step": f.stem, "created": [fmt(r) for r in data["added"]], "would_create": [fmt(r) for r in data["would_add"]], "skipped": []}
    for sheet in (run_dir / "sheets").glob("*.json") if (run_dir / "sheets").exists() else []:
        out.setdefault("rows_added", {})[sheet.stem] = json.loads(sheet.read_text())
    return out


def _json(text: Any) -> Any:
    if not isinstance(text, str):
        return text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
