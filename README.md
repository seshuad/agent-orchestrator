# agent-orchestrator

A service for building, running and debugging multi-step agents without writing
code: a guided designer, a restricted agent format, and Microsoft Conductor as the
execution engine. Builders put an agent together from a small set of building
blocks; the service checks it against safety rules, compiles it to a Conductor
workflow and runs it.

## What's here

| Path | What it is |
|---|---|
| `docs/agent-service-design.md` | Snapshot of the design doc: architecture, agent format, connections, safety model, runs, debugging, phasing. The [live doc](https://claude.ai/code/artifact/8dd90b77-a44b-454a-a27b-1e1dda2c1a95) is the source of truth. |
| `designer/build.py` | Generates the designer's screens from shared pieces (top bar, step list, flow graph), so every screen stays consistent. |
| `designer/canvas/project/` | The screens, mirroring the [Agent Step Designer canvas](https://claude.ai/artifact/YRmM5ZjXFjmFxPjzk3GpnB): one `.dc.html` per screen plus `canvas.json` (layout). |
| `src/agent_service/` | The service prototype: the agent format (`definition.py`), the compiler to Conductor YAML (`compiler.py`), the command line (`cli.py`), and what a run worker ships (`runtime/`). |
| `examples/<agent>/` | Each example agent: its definition (`*.agent.yaml`, what the designer saves), the compiled workflow (`*.yaml`) and limits spec (`*.limits.json`), sample data, and a replay script. |
| `src/agent_service/server/` | The designer's back end (FastAPI): workspace store with draft and published versions, design-time analysis, runs with live logs and approvals. |
| `web/` | The designer (React, TypeScript, Vite): Home, New agent, the agent editor, Run now, runs and logs, approvals, connections. |
| `tests/` | The run-time programs, the compiler, the API, whole runs in Conductor with scripted model steps, and the designer in a real browser. |

## Building blocks

Every agent is made from the same blocks, whatever it does. **Steps** do one piece
of work; **flow blocks** hold steps and decide how they run.

| Block | Kind | What it does | Compiles to (Conductor) |
|---|---|---|---|
| Ask | Step | A model reads and extracts. It can never change anything. | `agent` with an explicit `tools:` list and output schema |
| Built-in | Step | Fixed rules, no model: remove duplicates, filter, group. | `script` step from our step library |
| Approve | Step | A person decides before anything changes. | `human_gate` |
| Act | Step | Changes something outside the agent, using only checked fields. | `script` or `mcp` step calling the connector gateway |
| Parallel | Flow | Runs its steps at the same time. | `parallel:` group with `failure_mode` |
| Branch | Flow | Picks one path based on an earlier result. | `routes:` with `when:` |
| Free-form | Flow | A model picks which of its steps to run, and how often, toward a goal. Holds only Ask and Built-in steps. | a planner `agent` routing to each inner step once its inputs exist, each routing back; "Before finishing" rules checked by the CEL evaluator |

Record types (such as Booking below) describe the data passed between steps.
`AddStep.dc.html` shows the Add menu with steps and flow blocks.

## Example: travel-sync

The screens show the designer with [travel-sync](https://github.com/seshuad/travel-sync)
filled in: a working prototype that reads travel bookings from Gmail, verifies them and
adds approved trips to Google Calendar. It is one example, not the target. Every
control on the screens is general-purpose; the travel-sync values are what a builder
would enter to build it from scratch.

The starting page (`Home`) shows the signed-in user, their workspace and its agents, with Run now on each and New agent at the top. `RunNow` is the manual-run dialog; `Runs` and `RunFailed` show an agent's run history with a successful and a failed run, each with its outcome, the service's checks and a readable log. Both runs are real prototype runs.

Screens for building an agent, in the order a builder would work:

| # | Screen | What the builder does | travel-sync equivalent |
|---|---|---|---|
| 1 | New agent (`Start`) | Names the agent and starts blank (or from a description or template) | — |
| 2 | Trigger (`Trigger`) | Schedule, run options (dry run, my name), budget and time limits | `workflow.input`, `limits` |
| 3 | Connections | Connects Gmail (read) and Google Calendar (create events) once | OAuth in `google_auth.py` |
| 4 | Record type: Booking | Defines fields, types, hints for the model, and the identity used for duplicates | `Booking` in `models.py` |
| 5 | Read emails | Parallel block; keep going if one reader fails | `parallel:` with `continue_on_error` |
| 6 | Read airline emails (`Main`) | Ask step: model, Gmail actions with sender and date limits, shared instructions, outputs, quality checks | reader agents + one MCP server per scope |
| 7 | Tidy up | Built-in step: a stack of operations (check, remove duplicates, filter, group into Trip, flag rules) | `reconcile.py` |
| 8 | Any trips found? | Branch: end the run when there are no trips | reconcile's route to `$end` |
| 9 | Double-check bookings (`Verify`) | Ask step that can open only the emails named in its input | verifier + read-only mail server |
| 10 | Approve trips | Approve step: what the approver reviews, pre-selection, choices and timeout | `review` script + `human_gate` |
| 11 | Add to calendar (`Calendar`) | Act step: map fields to the event, never add twice, follow dry run | `writer.py` + `calendar.py` |

`AddStep.dc.html` shows the Add menu. `FreeForm.dc.html` shows a variant of travel-sync built with a Free-form
block, modeled on its `--mode free`: one block, *Find and check bookings*, holds the three readers, Tidy up and
Double-check. Branch, Approve and Add to calendar still run after it in fixed order, so the planner cannot change
anything outside the agent. The design doc still lists model-chosen order as a non-goal for v1.

## Free-form blocks

A Free-form block is free only within the order its data sets. Three layers decide what runs:

| Layer | Decided by | Example |
|---|---|---|
| Data order | Worked out from what each step needs and returns; a step can run once its inputs exist | Tidy up needs bookings, so it can't run before a reader has returned some |
| Before finishing | The builder: rules the service enforces, not the prompt | Double-check every booking in the proposed trips; always check bank details |
| Everything else | The planning model, while the agent runs | Which follow-up searches, with what focus, what to verify, when to stop |

Limits count only Ask steps (the ones with a model); Built-in steps are free and re-run by themselves when their
inputs change.

### Second example: invoice-check

To pressure-test the block on something other than travel-sync, `InvoiceBlock.dc.html` and `InvoiceTest.dc.html`
show **invoice-check**: an invoice email arrives, a Free-form block (*Match invoice*) reads it, looks up the vendor,
checks bank details, finds the purchase order (searching earlier vendor emails if the PO number is missing), finds
receipts and runs a three-way match, then finance approves and a row is added to a payment queue sheet. The test-run
screen shows three sample invoices taking three different paths, and the planner's reason for each step.

What it added to the design:

1. A record can come from more than one step (a PO number from the invoice, or from vendor emails).
2. Inputs can be optional (services have no delivery receipts).
3. A block finishes with a named outcome, and finishing rules can depend on it ("to finish as matched, the three-way
   match must have passed").
4. Safety checks have to be finishing rules: a planner fooled by untrusted content is more likely to skip a step than
   to run a harmful one, and it can't run harmful ones at all.
5. Tests need a sample per outcome; the test run shows which outcomes haven't been covered.

## Design time and run time (prototype)

The service's path from designer to a finished run, runnable on one machine with sample data:

```
agent definition (*.agent.yaml)  ──compile──>  Conductor YAML + limits spec  ──run──>  conductor run
   what the designer saves           agent-service compile                     agent-service run
```

**Design time.** The designer saves an **agent definition** in our format (`src/agent_service/definition.py`):
trigger, run options, limits, connections, record types and steps. Inputs are picked references
(`read_invoice.invoice.po_number`; a list means "any of these", a trailing `?` optional), and every rule is
[CEL](https://github.com/google/cel-spec). Loading a definition checks the format's safety rules: Ask steps can only
read, an approval's first choice passes nothing, every connection used is connected. Human approval is the builder's
choice: an Act step that runs after reading email, with no Approve step first, is a warning, not an error. The **compiler** turns a
definition into Conductor YAML plus a **limits spec**: one entry per connection use (Gmail for Read airline emails:
search and open, these 10 senders, 180 days). Nobody edits the YAML; `tests/` fails if a checked-in workflow drifts
from its definition.

**Run time.** `agent-service run` does what the control plane and a run worker do between them: make a run directory,
fill in trigger values (for an email trigger, the email's id and its sender's domain, from the headers), mint one
HMAC-signed **limits token** per connection use, and start Conductor. Every `command:` in the YAML is one of four
programs the worker ships:

| Program | What it is |
|---|---|
| `agent-service-gateway` | The connector gateway's stdio shim. Every limit comes from the signed token (flags can only narrow it); every call is checked and logged, allowed or refused. Serves sample data here; in the service it forwards to the gateway, which holds the credentials. |
| `agent-service-steps` | The Built-in step library: `tidy` (check, remove duplicates, filter, group, flag, all configured in CEL), `lookup`, `filter-rows`, `compare`, `three-way-match`, `create-events`. |
| `agent-service-cel` | The CEL evaluator, called from Conductor `mcp` steps for Branch, pre-selection and "Before finishing". Returns `passed`, `failed`, `results`, and `error` (the run stops and names the rule, rather than guess). |
| `agent-service-replay` | For tests: stands in for model steps and approvals with scripted answers. |

Each run's directory is the prototype's event store: `steps/` holds every Built-in and CEL step's output,
`gateway.jsonl` every connection call, and `calendar.json` or `sheets/` anything written.

Things the compilation showed:

- **Results collected across runs.** Conductor keeps only a step's latest output, so a reader run again with a focus
  would drop what it found first. A Free-form block's `collect` keeps running lists.
- **Limits that only exist mid-run.** Double-check may open only emails Tidy up cited; the gateway checks each request
  against the run's recorded outputs.
- **Portable CEL.** A step that hasn't run is absent from the rules' data and tested with `has(steps.<id>)`, which every
  CEL implementation supports (cel-python can't compare a record to `null`).
- **Rules must cover every outcome.** The first replay let the planner finish as "amounts differ" without running the
  three-way match; the rule now requires it for any outcome that claims a match result.

### Running it

```bash
uv sync
uv run --group dev pytest                  # run-time programs, compiler, and both agents end to end in Conductor

uv run agent-service compile examples/invoice-check/invoice-check.agent.yaml -o examples/invoice-check/invoice-check.yaml

# A whole run with scripted model steps: no API key, no person needed.
uv run agent-service run examples/invoice-check/invoice-check.agent.yaml \
  --sample-data examples/invoice-check/sample-data --email-id inv-northwind-2208 \
  --replay examples/invoice-check/replay-northwind.yaml

# A real run: model steps call the Claude API (ANTHROPIC_API_KEY), and you answer the approval in the terminal.
uv run agent-service run examples/travel-sync-free/travel-sync-free.agent.yaml \
  --sample-data examples/travel-sync-free/sample-data
```

Runs use sample data only (travel-sync's fixture emails; three invoices with their Vendors, Purchase orders and
Receiving log sheets) and default to dry run. Not built yet: the control plane, run queue and containers, the approval
service (Conductor gates have no timeout, so the 24-hour default is the service's job), real connections, and the
designer as a working app. The prototype compiles at most one Free-form block per agent.

## The designer

The mocks, built: a web app over the agent format, the compiler and the run environment.

```bash
uv sync
(cd web && npm install && npm run build)
ANTHROPIC_API_KEY=... uv run agent-service serve        # http://127.0.0.1:8700
```

Without an API key everything works except running model steps for real: runs can use scripted answers instead
(both example agents have them). For front-end work, run `npm run dev` in `web/` (port 5173, proxying `/api` to the
service on 8700).

| Screen | What it does |
|---|---|
| Home | The signed-in user, the workspace and its agents: status, trigger, last run, recent runs, Run now, New agent |
| New agent | Name, what it should do, start blank or from a template, pick test data |
| Editor | Step list, flow graph (Free-form blocks drawn by data order, with loops) and a panel for everything: settings, connections, record types, Ask, Built-in (Tidy up's pipeline), Free-form, Branch, Approve, Act. Autosaves; every save returns the service's errors pinned to fields, and publishing is blocked until there are none. Compiled YAML is one click away. |
| Run now / Test run | Pick the version (or the draft), run options, the email for an email trigger, Claude API or scripted answers |
| Runs | Every run, live while it runs: outcome, what an Act step did or would do, the service's checks, and a readable log with the planner's reasons, tool calls, costs, and plain-language failures |
| Approvals | Runs waiting for a person; the choices are on the run's page and go to Conductor's gate |

### Real Gmail

Gmail connections can sign in to Google, read only (`gmail.readonly`), and a run can then use **Real accounts**: its Gmail
steps read your inbox through the gateway, within each step's sender, date and message limits. Sheets and Calendar stay
on sample data for now, so nothing is written to your real accounts.

1. A Google OAuth client of type **Desktop app**, with the Gmail API enabled in its project. The service looks for
   `$AGENT_SERVICE_GOOGLE_CLIENT`, then `~/.config/agent-service/client_secret.json`, then travel-sync's
   `~/.config/travel-sync/client_secret.json`. If the app is in "Testing", add your account as a test user; its sign-ins
   expire after 7 days.
2. Connections → **Sign in with Google** on a Gmail connection. The token goes to `.workspace/vault/` (mode 0600).
3. Run now → Data: **Real accounts**.

The service keeps its workspace in `.workspace/` (agents, versions, runs). Each run is `conductor run` in web mode on
its own port; the service follows its event log, answers approvals with `conductor gate respond`, and stops it when
it ends.

Not built yet: sign-in and more than one user, schedules and email triggers firing on their own, "Describe it"
(a model drafting the agent), notifications for approvals, the Parallel block, real connections (sample data only),
and more than one Free-form block per agent.

## Working on the screens

```bash
uv run --no-project --python 3.13 python designer/build.py   # regenerate designer/canvas/project/*.dc.html
```

Publishing to the canvas is done from Claude Code (the Artifact tool), with
`root` = `designer/canvas` and the changed `project/…` files. Edit `build.py`
rather than the generated HTML, or the next build overwrites the change.
