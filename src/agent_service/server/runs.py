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

import hashlib
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
from . import inspect
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
        """Atomically: the page and the run's watcher read and write this at the same time, and a reader must never see
        a half-written file."""
        path = self.store.runs_root() / rec["id"] / "run.json"
        tmp = path.with_name(f"run.json.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(rec, indent=1))
        tmp.replace(path)

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
              scripted: bool, started_by: str, live: bool = False, trigger_email: dict[str, Any] | None = None,
              trigger_message: dict[str, Any] | None = None, trigger: str = "manual") -> dict[str, Any]:
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
                                  connectors={c["id"]: c for c in self.store.connectors()}, memory=self.store.memory(agent_name),
                                  trigger_message=trigger_message)
        (prepared.run_dir / "agent.yaml").write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
        return self._launch(run_id, agent_name, version, prepared, started_by, live, {"trigger": trigger, "scripted": scripted})

    def _launch(self, run_id: str, agent_name: str, version: int | None, prepared: Any, started_by: str, live: bool,
                extra: dict[str, Any]) -> dict[str, Any]:
        port = _free_port()
        rec = {"id": run_id, "agent": agent_name, "version": version, "started_by": started_by,
               "source": "live" if live else "sample", "inputs": prepared.inputs, "started_at": time.time(), "ended_at": None,
               "status": "running", "port": port, "error": None, "gate": None, **extra}
        self._save(rec)
        env = {**prepared.env, "CONDUCTOR_RUN_ID": run_id}
        proc = subprocess.Popen(prepared.command + ["--web", "--web-port", str(port)], env=env, stdin=subprocess.DEVNULL,
                                stdout=open(prepared.run_dir / "conductor.log", "w"), stderr=subprocess.STDOUT)
        with self.lock:
            self.procs[run_id] = proc
        threading.Thread(target=self._watch, args=(run_id,), daemon=True).start()
        return rec

    def events_path(self, run_id: str) -> Path | None:
        kept = self.store.runs_root() / run_id / "events.jsonl"
        if kept.exists():
            return kept
        hits = sorted(EVENTS_DIR.glob(f"conductor-*-{run_id}.events.jsonl"))
        return hits[-1] if hits else None

    def _keep_events(self, run_id: str) -> None:
        """The system cleans up its temp folder; a run's event log is its record, so it moves in with the run."""
        hits = sorted(EVENTS_DIR.glob(f"conductor-*-{run_id}.events.jsonl"))
        if hits:
            shutil.copyfile(hits[-1], self.store.runs_root() / run_id / "events.jsonl")

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
            if not (self.store.runs_root() / run_id / "run.json").exists():
                return                               # the run's folder is gone: its agent was deleted
            rec = json.loads((self.store.runs_root() / run_id / "run.json").read_text())
            gate = next((e for e in reversed(evs) if e["type"] in ("gate_presented", "gate_resolved")), None)
            waiting = gate is not None and gate["type"] == "gate_presented"
            if rec["status"] in ("running", "waiting"):
                rec["status"] = "waiting" if waiting else "running"
                rec["gate"] = gate["data"] if waiting else None
                self._save(rec)
                if waiting and rec.get("test") and not rec.get("_answering"):
                    step = gate["data"].get("agent_name", "")
                    answer = (rec["test"].get("approvals") or {}).get(step) or {"choice": gate["data"]["option_details"][0]["value"]}
                    rec["_answering"] = True             # a test answers each approval the way its source run did
                    self._save(rec)
                    threading.Thread(target=self._answer, args=(run_id, answer), daemon=True).start()
            if not waiting and rec.get("_answering"):
                rec.pop("_answering", None)
                self._save(rec)
            end = next((e for e in evs if e["type"] in END and not e["data"].get("subworkflow_path")), None)   # not one item's
            if end is not None or proc.poll() is not None:
                break
        if not (self.store.runs_root() / run_id / "run.json").exists():
            return                                   # the agent was deleted while the run ended
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
        rec.pop("_answering", None)
        self._save(rec)
        self._keep_events(run_id)
        try:
            self.remember(run_id)
        except Exception:
            pass                                 # memory is a help, never a reason for a run to fail
        if rec.get("test"):
            rec["test_result"] = self.check_expectations(run_id, rec["test"].get("expect") or [])
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
        evs = [e for e in evs if not e["data"].get("subworkflow_path")]      # one item failing is that item's, not the run's
        if any(e["type"] == "agent_failed" and e["data"].get("agent_name") == "stop_step_failed" for e in evs):
            errored = next((e["data"].get("agent_name") for e in reversed(evs) if e["type"] == "mcp_completed" and e["data"].get("is_error")), None)
            hist = self.store.runs_root() / run_id / "history.jsonl"
            failed = next((h for h in reversed([json.loads(l) for l in hist.read_text().splitlines()] if hist.exists() else [])
                           if h["step"] == errored and isinstance(h.get("output"), dict) and h["output"].get("error")), None)
            if failed:                                       # a Built-in step in a group: its error came back as a result
                return {"title": f"A step failed: {errored}", "why": failed["output"]["error"],
                        "fix": "Check that step's settings in the editor.", "raw": failed["output"]["error"]}
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
        """When the service stops, so do its runs and replay viewers: nothing is left running on its own."""
        for proc, _ in self.replays.values():
            proc.terminate()
        for proc in list(self.procs.values()):
            if proc.poll() is None:
                proc.terminate()

    # -------------------------------------------------------------- the readable log

    def judgments(self, run_id: str) -> list[dict[str, Any]]:
        """Every judgment a finished run made in a step with memory: each model-decided Branch's path (per item, when it
        decides for each item of a list) and each Free-form block's outcome, with its reason, evidence, how sure it was
        and the fields it matches on. `ref` names it: the step id, or <step>#<item index>."""
        run_dir = self.store.runs_root() / run_id
        rec = json.loads((run_dir / "run.json").read_text())
        if rec.get("test") or rec.get("rerun_of") or rec.get("status") != "succeeded":
            return []
        raw = yaml.safe_load((run_dir / "agent.yaml").read_text()) if (run_dir / "agent.yaml").exists() else {}
        evs = self.events(run_id)
        hist = [json.loads(l) for l in (run_dir / "history.jsonl").read_text().splitlines()] if (run_dir / "history.jsonl").exists() else []
        keys_of = lambda step: next((h["output"].get("keys") for h in reversed(hist) if h["step"] == f"{step}_recall"), {}) or {}
        found = []
        for block in raw.get("steps") or []:
            if block.get("kind") != "parallel" or not block.get("for_each"):
                continue
            collected = next((h["output"] for h in reversed(hist) if h["step"] == block["id"]), None) or {}
            for s in block.get("steps") or []:
                if s.get("kind") != "branch" or s.get("decide") != "model" or not s.get("memory"):
                    continue
                for dec in (collected.get(s["id"]) or {}).get("decisions") or []:
                    found.append({"step": s["id"], "step_name": s["name"], "kind": "branch", "choices": [p["name"] for p in s.get("paths") or []],
                                  "ask_sample": s["memory"].get("ask_sample", 0.05), "ref": f"{s['id']}#{dec.get('index')}",
                                  "subject": dec.get("label"), "decision": dec.get("path"), "reason": dec.get("reason"),
                                  "evidence": dec.get("evidence") or [], "keys": dec.get("keys") or {},
                                  "confidence": dec.get("confidence") or "sure", "runner_up": dec.get("runner_up") or ""})
        for s in raw.get("steps") or []:
            if not s.get("memory"):
                continue
            choices = [p["name"] for p in s.get("paths") or []]
            base = {"step": s["id"], "step_name": s["name"], "kind": "branch", "choices": choices,
                    "ask_sample": (s.get("memory") or {}).get("ask_sample", 0.05)}
            if s.get("kind") == "branch" and s.get("decide") == "model" and s.get("for_each"):
                collected = next((h["output"] for h in reversed(hist) if h["step"] == s["id"]), None) or {}
                for i, dec in enumerate(collected.get("decisions") or []):
                    found.append({**base, "ref": f"{s['id']}#{i}", "subject": dec.get("label"), "decision": dec.get("path"),
                                  "reason": dec.get("reason"), "evidence": dec.get("evidence") or [], "keys": dec.get("keys") or {},
                                  "confidence": dec.get("confidence") or "sure", "runner_up": dec.get("runner_up") or ""})
            elif s.get("kind") == "branch" and s.get("decide") == "model":
                for e in evs:
                    d = e["data"]
                    if e["type"] in ("agent_completed", "script_completed") and d.get("agent_name") == s["id"]:
                        out = inspect._value(d.get("output") if e["type"] == "agent_completed" else d.get("stdout")) or {}   # scripted answers
                        found.append({**base, "ref": s["id"], "decision": out.get("path"), "reason": out.get("reason"),
                                      "evidence": out.get("evidence") or [], "keys": keys_of(s["id"]),
                                      "confidence": out.get("confidence") or "sure", "runner_up": out.get("runner_up") or ""})
            elif s.get("kind") == "free-form":
                finish = next((h["output"] for h in reversed(hist) if h["step"] == "finish_check" and h["output"].get("passed")), None)
                if not finish:
                    continue
                inner = {x["id"] for x in s.get("steps") or []}
                ran = [e["data"].get("agent_name", "").removesuffix("__together") for e in evs if e["type"].endswith("_completed")
                       and e["data"].get("agent_name", "").removesuffix("__together") in inner]
                plans = [inspect._value(e["data"].get("output") if e["type"] == "agent_completed" else e["data"].get("stdout")) or {}
                         for e in evs if e["data"].get("agent_name") == "plan" and e["type"] in ("agent_completed", "script_completed")]
                last = plans[-1] if plans else {}
                results = finish.get("results") or {}
                found.append({**base, "kind": "free-form", "ref": s["id"], "choices": s.get("outcomes") or [],
                              "decision": results.get("outcome") or "finished", "reason": last.get("reason"),
                              "summary": f"ran {', '.join(ran) or 'no steps'} in {len(plans)} planner turns",
                              "notes": last.get("notes") or [], "keys": keys_of(s["id"]),
                              "confidence": last.get("confidence") or "sure", "runner_up": last.get("runner_up") or ""})
        return found

    @staticmethod
    def _sampled(run_id: str, ref: str, share: float) -> bool:
        """A steady random pick: the same decision is always in or out of the sample."""
        return int(hashlib.sha256(f"{run_id}:{ref}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF < share

    def remember(self, run_id: str) -> list[dict[str, Any]]:
        """At the end of a run: the judgments a person is asked about. Those the model wasn't sure of, and a small sample
        of the rest, so confident mistakes get noticed too. Everything else is done unless someone corrects it."""
        rec = json.loads((self.store.runs_root() / run_id / "run.json").read_text())
        asked = []
        for j in self.judgments(run_id):
            why = "unsure" if j["confidence"] == "unsure" else "sample" if self._sampled(run_id, j["ref"], j.pop("ask_sample")) else None
            j.pop("ask_sample", None)
            if why:
                asked.append({"id": secrets.token_hex(4), "run": run_id, "at": rec.get("started_at", time.time()),
                              "status": "candidate", "asked_because": why, **j})
        if asked:
            cases = [c for c in self.store.memory(rec["agent"]) if not (c.get("run") == run_id and c.get("status") == "candidate")]
            self.store.save_memory(rec["agent"], prune(cases + asked))
        return asked

    def correct(self, run_id: str, ref: str, decision: str, note: str, who: str) -> dict[str, Any]:
        """A person's answer on any judgment of a run, asked about or not. The same answer as the model's confirms it."""
        rec = self.record(run_id)
        j = next((x for x in self.judgments(run_id) if x["ref"] == ref), None)
        if j is None:
            raise NotFound(f"This run made no remembered judgment {ref!r}.")
        if j["choices"] and decision not in j["choices"]:
            raise ValueError(f"{decision!r} isn't one of {', '.join(j['choices'])}.")
        j.pop("ask_sample", None)
        cases = self.store.memory(rec["agent"])
        case = next((c for c in cases if c.get("run") == run_id and c.get("ref") == ref), None)
        if case is None:
            case = {"id": secrets.token_hex(4), "run": run_id, "at": rec.get("started_at", time.time()), "asked_because": None, **j}
            cases.append(case)
        now = time.time()
        if decision == case.get("decision"):
            case.update(status="confirmed", confirm_note=note.strip() or None, correction=None, confirmed_by=who, confirmed_at=now)
        else:
            case.update(status="corrected", correction={"decision": decision, "note": note.strip()}, confirmed_by=who, confirmed_at=now)
        self.store.save_memory(rec["agent"], prune(cases))
        return case

    def _answer(self, run_id: str, answer: dict[str, Any]) -> None:
        try:
            self.approve(run_id, answer.get("choice", ""), answer.get("ids"))
        except Exception:
            pass

    def check_expectations(self, run_id: str, expect: list[dict[str, str]]) -> dict[str, Any]:
        """A test's expectations (CEL) against what the run produced: status, steps.<id>.<field>, calls."""
        from ..runtime.cel_server import evaluate
        rec = json.loads((self.store.runs_root() / run_id / "run.json").read_text())
        run_dir = self.store.runs_root() / run_id
        calls = [json.loads(l) for l in (run_dir / "gateway.jsonl").read_text().splitlines()] if (run_dir / "gateway.jsonl").exists() else []
        data = self.expectation_data(run_id)
        results = []
        for e in expect:
            one = evaluate([{"name": "x", "cel": e["rule"], "require": e.get("name") or e["rule"]}], data)
            results.append({"name": e.get("name") or e["rule"], "rule": e["rule"], "passed": bool(one["passed"]),
                            "value": one["results"].get("x"), "error": one["error"]})
        return {"passed": bool(results) and all(r["passed"] for r in results), "results": results}

    def expectation_data(self, run_id: str) -> dict[str, Any]:
        """What expectations read: status, steps.<id> (a Free-form block's results under its own id), calls."""
        rec = json.loads((self.store.runs_root() / run_id / "run.json").read_text())
        run_dir = self.store.runs_root() / run_id
        calls = [json.loads(l) for l in (run_dir / "gateway.jsonl").read_text().splitlines()] if (run_dir / "gateway.jsonl").exists() else []
        steps = inspect.outputs(self.events(run_id), run_dir)
        raw = yaml.safe_load((run_dir / "agent.yaml").read_text()) if (run_dir / "agent.yaml").exists() else {}
        for s in raw.get("steps") or []:
            if s.get("kind") == "free-form" and isinstance(steps.get("finish_check"), dict):
                steps[s["id"]] = steps["finish_check"].get("results")
        return {"status": rec["status"], "steps": steps,
                "calls": {"count": len(calls), "refused": sum(c["outcome"] == "refused" for c in calls)}}

    def suggest_test(self, run_id: str) -> dict[str, Any]:
        """A test case from a finished run: its inputs and approvals, and expectations from what it did."""
        rec = self.record(run_id)
        data = self.expectation_data(run_id)
        raw = yaml.safe_load((self._dir(run_id) / "agent.yaml").read_text())
        expect = [{"name": f"The run {rec['status']}", "rule": f"status == '{rec['status']}'"}]
        for s in raw.get("steps") or []:
            out = data["steps"].get(s["id"])
            if s.get("kind") == "free-form" and isinstance(out, dict) and isinstance(out.get("outcome"), str):
                expect.append({"name": f"{s['name']} ends as {out['outcome']}", "rule": f"steps.{s['id']}.outcome == '{out['outcome']}'"})
            elif s.get("kind") == "act" and isinstance(out, dict):
                for key in ("created", "would_create", "added", "would_add", "called", "would_call"):
                    if isinstance(out.get(key), list) and out[key]:
                        expect.append({"name": f"{s['name']}: {len(out[key])} {key.replace('_', ' ')}", "rule": f"size(steps.{s['id']}.{key}) == {len(out[key])}"})
            elif s.get("kind") == "ask" and isinstance(out, dict):
                for key, v in out.items():
                    if isinstance(v, list) and v:
                        expect.append({"name": f"{s['name']} finds some {key}", "rule": f"size(steps.{s['id']}.{key}) > 0"})
        expect.append({"name": "No connection call was refused", "rule": "calls.refused == 0"})
        approvals = {}
        for e in self.events(run_id):
            if e["type"] == "gate_resolved":
                d = e["data"]
                approvals[d.get("agent_name")] = {"choice": d.get("selected_option"), "ids": (d.get("additional_input") or {}).get("ids")}
        inputs = {k: v for k, v in (rec.get("inputs") or {}).items() if k not in ("email_id", "sender_domain")}
        return {"name": f"Like the run on {time.strftime('%b %d %H:%M', time.localtime(rec['started_at']))}", "from_run": run_id,
                "inputs": inputs, "email_id": (rec.get("inputs") or {}).get("email_id"), "scripted": bool(rec.get("scripted")),
                "source": rec.get("source", "sample"), "approvals": approvals, "expect": expect}

    def start_test(self, agent_name: str, test: dict[str, Any], batch: str, started_by: str) -> dict[str, Any]:
        """One test case against the current draft."""
        rec = self.start(agent_name, version=None, inputs=dict(test.get("inputs") or {}), email_id=test.get("email_id"),
                         scripted=bool(test.get("scripted")), started_by=started_by, live=test.get("source") == "live")
        rec.update(trigger=f"test: {test['name']}", test={**test, "batch": batch})
        self._save(rec)
        return rec

    def inspect(self, run_id: str, step: str, n: int) -> dict[str, Any]:
        run_dir = self._dir(run_id)
        out = inspect.inspect_step(self.events(run_id), run_dir, step, n)
        rec = self.record(run_id)
        out["rerun_of"] = rec.get("rerun_of")
        out["reruns"] = [r["id"] for r in self.list(rec["agent"]) if (r.get("rerun_of") or {}).get("run") == run_id
                         and r["rerun_of"]["step"] == step and r["rerun_of"]["n"] == n]
        return out

    def rerun_step(self, run_id: str, step: str, n: int, started_by: str) -> dict[str, Any]:
        """Runs one step again with the agent's current draft, on exactly the inputs it had in `run_id`."""
        source = self.record(run_id)
        src_dir = self._dir(run_id)
        seen = inspect.inspect_step(self.events(run_id), src_dir, step, n)
        if not seen.get("rerunnable"):
            raise runner.RunError("Only model steps and Built-in steps with recorded inputs can run again on their own.")
        meta = self.store.meta(source["agent"])
        draft = self.store.draft(source["agent"])
        agent = definition.Agent.model_validate(draft)
        old_raw = yaml.safe_load((src_dir / "agent.yaml").read_text()) if (src_dir / "agent.yaml").exists() else {}

        def only_this_step(compiled):
            wf = compiled.workflow
            target = next((a for a in wf["agents"] if a["name"] == step), None)
            if target is None:
                raise runner.RunError(f"The draft has no step {step!r} any more.")
            target = {k: v for k, v in target.items() if k not in ("routes", "input")}
            target["input"] = []
            target["routes"] = [{"to": "$end"}]
            if seen["type"] == "agent":
                target["prompt"] = "{% raw %}" + _rebuilt_prompt(seen["prompt"], _task(old_raw, step), _task(draft, step)) + "{% endraw %}"
                servers = {t.split("__")[0] for t in target.get("tools") or []}
            else:
                target["stdin"] = "{% raw %}" + json.dumps(seen["inputs"], ensure_ascii=False) + "{% endraw %}"
                servers = set()
            runtime = dict(wf["workflow"].get("runtime") or {})
            runtime["mcp_servers"] = {k: v for k, v in (runtime.get("mcp_servers") or {}).items() if k in servers}
            if not runtime["mcp_servers"]:
                runtime.pop("mcp_servers")
            doc = {"workflow": {**wf["workflow"], "entry_point": step, "runtime": runtime}, "agents": [target]}
            if target.get("tools"):
                doc["tools"] = target["tools"]
            compiled.workflow = doc
            return compiled

        live = source.get("source") == "live"
        new_id = secrets.token_hex(4)
        prepared = runner.prepare(agent, sample_data=Path(meta["sample_data"]) if meta.get("sample_data") else runner.EMPTY,
                                  runs_root=self.store.runs_root(), inputs={k: v for k, v in (source.get("inputs") or {}).items()
                                                                            if k not in ("email_id", "sender_domain")},
                                  email_id=(source.get("inputs") or {}).get("email_id"), run_id=new_id,
                                  vault=self.store.home / "vault", live=live, accounts=self.store.accounts(),
                                  connectors={c["id"]: c for c in self.store.connectors()}, transform=only_this_step,
                                  trigger_email={"id": source["inputs"]["email_id"], "from": "x@" + source["inputs"].get("sender_domain", "")}
                                  if (source.get("inputs") or {}).get("email_id") else None)
        (prepared.run_dir / "agent.yaml").write_text(yaml.safe_dump(draft, sort_keys=False, allow_unicode=True))
        extra = {"trigger": f"re-run of {step}", "rerun_of": {"run": run_id, "step": step, "n": n}, "scripted": False}
        return self._launch(new_id, source["agent"], None, prepared, started_by, live, extra)

    def detail(self, run_id: str) -> dict[str, Any]:
        rec = self.record(run_id)
        run_dir = self._dir(run_id)
        raw = yaml.safe_load((run_dir / "agent.yaml").read_text()) if (run_dir / "agent.yaml").exists() else {}
        evs = self.events(run_id)
        entries, totals = build_log(evs, raw, run_dir)
        calls = [json.loads(l) for l in (run_dir / "gateway.jsonl").read_text().splitlines()] if (run_dir / "gateway.jsonl").exists() else []
        return {**rec, **totals, "log": entries,
                "checks": {"calls": len(calls), "refused": [c for c in calls if c["outcome"] == "refused"]},
                "outcome": _outcome(run_dir), "events_file": str(self.events_path(run_id) or ""),
                "data": self.data_used(rec, raw, run_dir)}

    def data_used(self, rec: dict[str, Any], raw: dict[str, Any], run_dir: Path) -> dict[str, Any]:
        """Which systems a run read or changed for real, and which on sample data, from the connections its agent used."""
        from .connections import SERVICES
        from .store import SAMPLE_SETS
        from .. import runner
        accounts = self.store.accounts()
        connectors = {c["id"]: c for c in self.store.connectors()}
        real, sample = [], []
        for conn in (raw.get("connections") or {}).values():
            service = conn.get("service", "")
            account = accounts.get(conn.get("account") or "", {})
            name = (connectors.get(account.get("connector") or "", {}).get("name") if service == "mcp"
                    else SERVICES.get(service, {}).get("name", service))
            is_real = service in runner.ALWAYS_LIVE or (rec.get("source") == "live" and service in runner.LIVE_SERVICES)
            label = name + (f" ({account.get('signed_in_as') or account.get('account')})" if is_real and service != "mcp"
                            and (account.get("signed_in_as") or account.get("account")) else "")
            (real if is_real else sample).append(label)
        sets = {str(v): k for k, v in SAMPLE_SETS.items()}
        sample_set = next((sets.get(str(Path(v)), "") for v in [self.store.meta(rec["agent"]).get("sample_data")] if v), "") if not real or sample else ""
        dedupe = lambda xs: list(dict.fromkeys(xs))
        real, sample = dedupe(real), dedupe(sample)
        if real and sample:
            text = f"Real: {', '.join(real)} · sample: {', '.join(sample)}"
        elif real:
            text = f"Real: {', '.join(real)}"
        else:
            text = "Sample data" + (f" ({sample_set})" if sample_set else "")
        return {"real": real, "sample": sample, "sample_set": sample_set, "text": text, "short": "real data" if real else "sample data"}


KEEP_REMEMBERED, KEEP_WAITING, KEEP_DISMISSED = 200, 100, 50       # per step


def prune(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keeps memory small. A newer answer about the same item replaces older ones; each step keeps its newest 200
    remembered cases (corrections outlast confirmations), 100 waiting and 50 dismissed."""
    remembered = lambda c: c.get("status") in ("confirmed", "corrected")
    newest = sorted(cases, key=lambda c: (c.get("confirmed_at") or c.get("at") or 0), reverse=True)
    seen, kept = set(), []
    for c in newest:
        same = (c.get("step"), c.get("subject") or c.get("ref") or c["id"]) if remembered(c) and c.get("subject") else None
        if same and same in seen:
            continue
        if same:
            seen.add(same)
        kept.append(c)
    out, counts = [], {}
    for c in sorted(kept, key=lambda c: (c.get("status") != "corrected", -(c.get("confirmed_at") or c.get("at") or 0))):
        bucket = "remembered" if remembered(c) else "waiting" if c.get("status") == "candidate" else "dismissed"
        n = counts[(c.get("step"), bucket)] = counts.get((c.get("step"), bucket), 0) + 1
        if n <= {"remembered": KEEP_REMEMBERED, "waiting": KEEP_WAITING, "dismissed": KEEP_DISMISSED}[bucket]:
            out.append(c)
    return sorted(out, key=lambda c: c.get("at") or 0)


def _task(raw: dict[str, Any], step: str) -> str:
    for s in raw.get("steps") or []:
        for x in [s, *(s.get("steps") or [])]:
            if x.get("id") == step:
                return x.get("task") or ""
    return ""


def _rebuilt_prompt(recorded: str, old_task: str, new_task: str) -> str:
    """The prompt a step saw, with its task text swapped for the draft's: the inputs stay exactly as they were."""
    if old_task and new_task and recorded.startswith(old_task):
        return new_task + recorded[len(old_task):]
    return recorded


def _names(raw: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """Conductor step name -> (what the builder calls it, kind)."""
    out: dict[str, tuple[str, str]] = {"plan": ("Plan", "planner"), "finish_check": ("Before finishing", "rules"),
                                       "not_ready": ("Refused", "rules"), "collect": ("Collect results", "plumbing")}

    def walk(steps: list[dict[str, Any]]) -> None:
        for s in steps or []:
            shows = s.get("kind") == "built-in" and "show" in (s.get("operation") or {})
            charts = s.get("kind") == "built-in" and "chart" in (s.get("operation") or {})
            out[s["id"]] = (s.get("name", s["id"]), "show" if shows else "chart" if charts else s.get("kind", ""))
            if s.get("kind") == "approve":
                out[f"{s['id']}_preselect"] = (f"{s.get('name')}: pre-select", "rules")
            if s.get("memory"):
                out[f"{s['id']}_recall"] = (f"{s.get('name')}: recall past cases", "memory")
            if s.get("kind") == "parallel":
                if s.get("for_each"):
                    each = s["for_each"].get("as") or "item"
                    out[f"{s['id']}_items"] = (f"{s.get('name')}: the {each}s", "loop-items")
                    out[f"{s['id']}_each"] = (s.get("name", s["id"]), "loop")
                    out[s["id"]] = (f"{s.get('name')}: every {each}'s results", "each-results")
                else:
                    out[f"{s['id']}_record"] = ("Record answers", "plumbing")
            if s.get("kind") == "branch" and s.get("decide") == "model" and s.get("for_each"):
                each = (s["for_each"].get("as") or "item")
                out[f"{s['id']}_items"] = (f"{s.get('name')}: the {each}s to decide", "loop-items")
                out[f"{s['id']}_each"] = (s.get("name", s["id"]), "loop")
                out[s["id"]] = (f"{s.get('name')}: every {each}'s decision", "decisions")
            elif s.get("kind") == "branch" and s.get("decide") == "model":
                out[s["id"]] = (s.get("name", s["id"]), "decision")
                if s.get("rules_first"):
                    out[f"{s['id']}_rules"] = (f"{s.get('name')}: hard rules", "rules")
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
        """The n-th recorded output of a step (Built-in and CEL steps record every run). Not inside a block's run for
        one item: items run at the same time, so their records interleave; those events carry their own output."""
        if state["sub"]:
            return None
        n = seen.get(step, 0)
        seen[step] = n + 1
        outs = [h["output"] for h in history if h["step"] == step]
        return outs[n] if n < len(outs) else None

    entries: list[dict[str, Any]] = []
    tools: dict[str, list[dict[str, Any]]] = {}
    # Judgments in steps with memory can be corrected from the log, asked about or not.
    every = [x for s in raw.get("steps") or [] for x in [s, *(s.get("steps") or [])]]
    remembering = {s["id"]: ([p["name"] for p in s.get("paths") or []] if s.get("kind") == "branch" else s.get("outcomes") or [])
                   for s in every if s.get("memory")}
    parallels = {s["id"]: s for s in raw.get("steps") or [] if s.get("kind") == "parallel"}
    state = {"sub": False}

    def items_of(block: str) -> list[dict[str, Any]]:
        return next((h["output"].get("items") for h in reversed(history) if h["step"] == f"{block}_items"), None) or []
    block_id = next((s["id"] for s in raw.get("steps") or [] if s.get("kind") == "free-form"), None)

    def judged(ref: str, step: str, out: dict[str, Any]) -> dict[str, Any]:
        if step not in remembering or not out:
            return {}
        return {"ref": ref, "choices": remembering[step], "decision": out.get("path") or out.get("outcome"),
                "confidence": out.get("confidence") or "sure", "runner_up": out.get("runner_up") or None}
    cost = tokens = 0.0
    ended: dict[str, int] = {}             # runs of each step finished so far: an entry's `n` for the step inspector
    for e in evs:
        t, d = e["type"], e["data"]
        name = (d.get("agent_name") or d.get("group_name") or "").removesuffix(IN_GROUP)   # a group member runs as a copy of its step
        count_end = t in inspect.ENDS and not (t == "mcp_completed" and d.get("group_name"))
        n = ended.get(name, 0)
        if count_end:
            ended[name] = n + 1
        label, kind = names.get(name, (name, ""))
        at = round(e["timestamp"] - t0, 1)
        base = {"at": at, "step": label, "id": name, "n": n, "kind": kind, "took": round(d.get("elapsed") or 0, 1), "cost": None,
                "detail": "", "why": None, "tone": "", "plumbing": name in PLUMBING, "tools": []}
        sub = d.get("subworkflow_path") or []
        state["sub"] = bool(sub)
        key = None
        if sub:                                  # a step of a Parallel block, running for one item
            if t in ("workflow_started", "workflow_completed", "workflow_failed"):
                continue
            m = re.match(r"(.+)_each\[(.+)\]$", str(sub[0]))
            if m:
                key = m.group(2)
                item = next((i.get("label") for i in items_of(m.group(1)) if i.get("key") == key), None) or f"Item {int(key) + 1}"
                base.update(step=f"{item} › {label}", item=item)
                base.pop("n", None)              # the step inspector reads whole-run steps only
        tkey = d.get("agent_name", "") + (f"@{sub[0]}" if sub else "")
        if t in ("mcp_completed", "agent_completed", "agent_started", "mcp_started") and d.get("item_key") is not None:
            continue                             # one item of a for-each group: its for_each_item_completed follows
        if t == "mcp_completed" and d.get("group_name"):
            continue                             # a scripted step inside a group: its parallel_agent_completed follows
        if t == "for_each_started":
            each = names.get(name.removesuffix("_each") + "_items", ("", ""))[0].rsplit("the ", 1)[-1].removesuffix(" to decide")
            verb = "Runs" if name.removesuffix("_each") in parallels else "Decides"
            entries.append({**base, "kind": "group", "detail": f"{verb} for {d.get('item_count')} {each}, {d.get('max_concurrent')} at a time"})
            continue
        if t in ("for_each_item_completed", "for_each_item_failed") and name.removesuffix("_each") in parallels:
            item = next((i.get("label") for i in items_of(name.removesuffix("_each")) if i.get("key") == str(d.get("item_key"))), None) \
                or f"Item {d.get('item_key')}"
            if t == "for_each_item_failed":
                entries.append({**base, "kind": "group", "step": item, "tone": "bad",
                                "detail": "Failed: " + friendly_error(d.get("message", ""))["title"]})
            else:
                entries.append({**base, "kind": "group", "step": item, "detail": "Finished", "plumbing": True})
            continue
        if t in ("for_each_item_completed", "for_each_item_failed"):
            items = next((h["output"].get("items") for h in reversed(history) if h["step"] == name.removesuffix("_each") + "_items"), None) or []
            label = next((i.get("label") for i in items if i.get("key") == str(d.get("item_key"))), f"Item {d.get('item_key')}")
            tool_calls = tools.pop(f"{name}[{d.get('item_key')}]", [])
            if t == "for_each_item_failed":
                entries.append({**base, "kind": "decision", "step": label, "detail": friendly_error(d.get("message", ""))["title"], "tone": "bad", "tools": tool_calls})
                continue
            decisions = next((h["output"].get("decisions") for h in reversed(history) if h["step"] == name.removesuffix("_each")), None) or []
            index = next((i for i, it in enumerate(items) if it.get("key") == str(d.get("item_key"))), -1)
            out = decisions[index] if 0 <= index < len(decisions) else {}
            c = d.get("cost_usd") or 0.0
            cost += c
            tokens += d.get("tokens") or 0
            entries.append({**base, "kind": "decision", "step": label, "cost": round(c, 4) if c else None, "tokens": d.get("tokens"),
                            "tools": tool_calls, "why": out.get("reason"), **judged(f"{name.removesuffix('_each')}#{index}", name.removesuffix("_each"), out),
                            # Conductor doesn't report a for-each item's answer: it shows once the loop's results are collected.
                            "detail": f"Chose: {out['path']}" if out.get("path") else "Decided"})
            continue
        if t == "for_each_completed":
            continue
        if t == "parallel_started":
            members = ", ".join(names.get(m.removesuffix(IN_GROUP), (m, ""))[0] for m in d.get("agents", []))
            entries.append({**base, "step": "Together", "kind": "group", "detail": f"Runs {members} at the same time", "plumbing": False})
        elif t == "parallel_completed":
            detail = f"All {d.get('success_count')} finished" if not d.get("failure_count") else \
                f"{d.get('success_count')} finished, {d.get('failure_count')} failed"
            entries.append({**base, "step": "Together", "kind": "group", "detail": f"{detail} in {round(d.get('elapsed') or 0, 1)}s",
                            "tone": "warn" if d.get("failure_count") else "", "plumbing": False})
        elif t == "parallel_agent_completed" and kind in ("built-in", "show", "chart"):      # a Built-in step in a group
            out = recorded(name) or {}
            entries.append({**base, "detail": out["error"] if out.get("error") else _brief(out), "tone": "bad" if out.get("error") else "",
                            "why": "In a group", "image": out.get("image") if kind == "chart" else None})
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
            who = tkey + (f"[{d['item_key']}]" if d.get("item_key") is not None else "")
            tools.setdefault(who, []).append({"tool": d["tool_name"].split("__")[-1], "args": d.get("arguments", "")})
        elif t == "script_completed" and kind == "each-results":
            out = recorded(name) or _json(d.get("stdout")) or {}
            parts = [f"{out.get('count', 0)} done" + (f", {out['failed']} failed" if out.get("failed") else "")]
            for b in (x for x in (parallels.get(name) or {}).get("steps") or [] if x.get("kind") == "branch" and x.get("decide") == "model"):
                counts = ", ".join(f"{v} {k}" for k, v in ((out.get(b["id"]) or {}).get("counts") or {}).items() if v)
                if counts:
                    parts.append(f"{b.get('name')}: {counts}")
            entries.append({**base, "detail": " · ".join(parts), "tone": "warn" if out.get("failed") else ""})
        elif t == "script_completed" and kind == "loop-items":
            out = recorded(name) or _json(d.get("stdout")) or {}
            n, r = out.get("count", 0), out.get("recalled", 0)
            if name.removesuffix("_items") in parallels:
                each = (parallels[name.removesuffix("_items")].get("for_each") or {}).get("as") or "item"
                entries.append({**base, "detail": f"{n} {each}{'s' if n != 1 else ''}"})
            else:
                entries.append({**base, "detail": f"{n} to decide" + (f", with {r} past case{'s' if r != 1 else ''} recalled" if r else "")})
        elif t == "script_completed" and kind == "decisions":
            out = recorded(name) or _json(d.get("stdout")) or {}
            counts = ", ".join(f"{v} {k}" for k, v in (out.get("counts") or {}).items() if v)
            entries.append({**base, "detail": counts or "Nothing to decide",
                            "why": f"{out['undecided']} couldn't be decided and took the safe default" if out.get("undecided") else None,
                            "tone": "warn" if out.get("undecided") else ""})
        elif t == "script_completed" and kind == "memory":
            out = recorded(name) or _json(d.get("stdout")) or {}
            cases = out.get("cases") or []
            entries.append({**base, "detail": (f"Recalled {len(cases)} past case{'s' if len(cases) != 1 else ''}"
                                               + ("" if out.get("similar") or not cases else " (the most recent; none similar)")) if cases
                            else "No confirmed past cases yet", "tone": "", "plumbing": bool(sub) and not cases})    # per item, only when it recalled some
        elif t in ("agent_completed", "script_completed") and kind == "decision":
            out = d.get("output") if t == "agent_completed" else _json(d.get("stdout"))
            out = inspect._value(out) if not isinstance(out, dict) else out
            c = d.get("cost_usd") or 0.0
            cost += c
            tokens += d.get("tokens") or 0
            entries.append({**base, "cost": round(c, 4) if c else None, "model": d.get("model") or ("scripted" if t == "script_completed" else None),
                            "tokens": d.get("tokens"), "detail": f"Chose: {(out or {}).get('path')}", "why": (out or {}).get("reason"),
                            "tools": tools.pop(tkey, []), **judged(f"{name}#{key}" if key is not None else name, name, out or {})})
        elif t in ("agent_completed", "script_completed") and kind in ("ask", "planner"):
            out = d.get("output") if t == "agent_completed" else _json(d.get("stdout"))
            c = d.get("cost_usd") or 0.0
            cost += c
            tokens += d.get("tokens") or 0
            entry = {**base, "cost": round(c, 4) if c else None, "model": d.get("model") or ("scripted" if t == "script_completed" else None),
                     "tokens": d.get("tokens"), "tools": tools.pop(tkey, [])}
            if name == "plan" and isinstance(out, dict):
                nxt = out.get("next")
                target = names.get(nxt, (nxt, ""))[0]
                if nxt in groups:
                    target = " and ".join(names.get(m, (m, ""))[0] for m in groups[nxt]) + ", at the same time"
                entry["detail"] = ("Finish" + (f" as {out['outcome']}" if out.get("outcome") else "") if nxt == "finish"
                                   else f"Next: {target}" + (f", focused on {out['focus']}" if out.get("focus") else ""))
                entry["why"] = out.get("reason")
                if nxt == "finish" and block_id:
                    entry.update(judged(block_id, block_id, out))
            else:
                entry["detail"] = _brief(out)
            entries.append(entry)
        elif t == "script_completed" and kind == "chart":
            out = recorded(name) or _json(d.get("stdout")) or {}
            entries.append({**base, "detail": f"Drew {out.get('title') or 'a chart'}" if out.get("image") else (out.get("error") or "No chart"),
                            "image": out.get("image"), "tone": "" if out.get("image") else "bad"})
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
    if (run_dir / "outbox.json").exists():
        out["emails"] = json.loads((run_dir / "outbox.json").read_text())      # sent, or what would be
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
