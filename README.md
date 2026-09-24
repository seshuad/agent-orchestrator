# agent-orchestrator

A service for building, running and debugging multi-step agents without writing
code: a guided designer, a restricted agent format, and Microsoft Conductor as the
execution engine. The design grows out of
[travel-sync](https://github.com/seshuad/travel-sync), a working prototype that
reads travel bookings from Gmail, verifies them and adds approved trips to Google
Calendar.

## What's here

| Path | What it is |
|---|---|
| `docs/agent-service-design.md` | Snapshot of the design doc: architecture, agent format, connections, safety model, runs, debugging, phasing. The [live doc](https://claude.ai/code/artifact/8dd90b77-a44b-454a-a27b-1e1dda2c1a95) is the source of truth. |
| `designer/build.py` | Generates the designer's screens from shared pieces (top bar, step list, flow graph), so every screen stays consistent. |
| `designer/canvas/project/` | The screens, mirroring the [Agent Step Designer canvas](https://claude.ai/artifact/YRmM5ZjXFjmFxPjzk3GpnB): one `.dc.html` per screen plus `canvas.json` (layout). |

## The designer, as modeled on travel-sync

Screens, in run order:

| Screen | Kind of step | travel-sync equivalent |
|---|---|---|
| Read airline / hotel / portal emails (run together) | Ask | `make_reader` in `agents.py`, one per sender scope |
| Tidy up | Built-in | `reconcile.py` |
| Double-check bookings | Ask | `make_verifier` in `agents.py` |
| Approve trips | Approve | CLI approval / Conductor `human_gate` |
| Add to calendar | Act | `writer.py` + `calendar.py` |
| Booking (record type) | Data | `Booking` in `models.py` |

`FreeForm.dc.html` is an earlier exploration of a model-chosen step, kept for
reference (a v2 idea in the design doc). It is hand-made; `build.py` leaves it alone.

## Working on the screens

```bash
uv run --no-project --python 3.13 python designer/build.py   # regenerate designer/canvas/project/*.dc.html
```

Publishing to the canvas is done from Claude Code (the Artifact tool), with
`root` = `designer/canvas` and the changed `project/…` files. Edit `build.py`
rather than the generated HTML, or the next build overwrites the change.
