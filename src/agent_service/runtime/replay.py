"""A scripted stand-in for model steps and approvals, for testing whole workflows in Conductor.

    agent-service-replay <step>      prints the step's next scripted output as JSON

The script (AGENT_SERVICE_REPLAY, a YAML file) lists each step's outputs in the order they are
used: {plan: [{next: read_invoice, ...}, ...], approve_payment: [{selected: all}]}. Everything
else in the run is real: Conductor's routing, the Built-in steps, the CEL evaluator, the gateway.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import yaml

from .runstate import record_step, run_dir


def main() -> None:
    step = sys.argv[1]
    script = yaml.safe_load(Path(os.environ["AGENT_SERVICE_REPLAY"]).read_text())
    counter = run_dir() / "replay" / step
    counter.parent.mkdir(exist_ok=True)
    n = int(counter.read_text()) if counter.exists() else 0
    outputs = script.get(step, [])
    if n >= len(outputs):
        sys.exit(f"The replay script has no output #{n + 1} for {step!r}.")
    counter.write_text(str(n + 1))
    record_step(step, outputs[n])
    json.dump(outputs[n], sys.stdout)


if __name__ == "__main__":
    main()
