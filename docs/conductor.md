# How the canvas maps onto Conductor

Nobody edits the Conductor YAML. The compiler (`src/agent_service/compiler.py`) turns a definition into a workflow
plus a **limits spec** (one entry per connection use). The compiled YAML is one click away in the editor, and
`tests/` fails if a checked-in example drifts from its definition.

| On the canvas | In Conductor |
|---|---|
| Ask | an `agent` step with an explicit `tools:` list and an output schema |
| Built-in | a `script` step running the step library (`agent-service-steps`) |
| Approve | a `human_gate` |
| Act | a `script` or `mcp` step calling the connector gateway |
| Branch, by rules | an `mcp` step on the CEL evaluator, with `routes:` on its results |
| Branch, by a model | an `agent` step whose output is `{path, reason, evidence, confidence, runner_up}`, routed on `output.path`; hard rules and recall run before it |
| Parallel, together | a `parallel:` group (a Built-in member becomes a call to the step library's MCP server, `agent-service-steps --mcp`), then a step that records each Ask answer for the run log |
| Parallel, for each item | a `for_each` group running a per-item workflow (`<id>.item.yaml`, written next to `workflow.yaml`), in which Branches route as usual; then a step that collects every item's results |
| Free-form | a planner `agent` routing to each inner step once its inputs exist, each routing back; `parallel:` groups for rows that run together; "Before finishing" checked by the CEL evaluator |

Every `command:` in a compiled workflow is one of the service's own programs:

| Program | What it is |
|---|---|
| `agent-service-gateway` | The connector gateway's stdio shim. Every limit comes from the signed token; every call is checked and logged. Serves sample data for test runs; forwards to the real system (Gmail, GitHub, BigQuery, Cloud Storage, SharePoint, SMTP, Trino, Dataproc, MCP servers) for runs on real accounts |
| `agent-service-steps` | The Built-in step library: the CEL operators (`cel`), `javascript`, the SQL engines (`bigquery`, `trino`, `spark-sql`), `chart`, the file operations (`gcs-list`, `gcs-read`, `sharepoint-list`, `sharepoint-read`, `sharepoint-items`), `lookup`, `filter-rows`, `compare`, `three-way-match`, the Act operations, memory recall and the Parallel item and collect steps (`tidy` stays for agent versions published before it was retired) |
| `agent-service-cel` | The CEL evaluator (MCP). Returns `passed`, `failed`, `results` and `error`; on an error the run stops and names the rule, rather than guess |
| `agent-service-replay` | For tests: stands in for model steps and approvals with scripted answers |

A run is `conductor run` in web mode on its own port. The service follows its event log, answers approvals with
`conductor gate respond`, and keeps everything in the run's folder: `events.jsonl`, `history.jsonl` (each step's
inputs and outputs), `gateway.jsonl` (every connection call) and anything written.

What compiling to Conductor taught us:

- **Results collected across runs of a step.** Conductor keeps only a step's latest output, so a reader run again with
  a focus would drop what it found first. A Free-form block's `collect` keeps running lists.
- **Group members can't route.** Conductor won't put routes on a parallel group's members, so inside Free-form each
  member runs as a route-less copy (`read_airline__together`).
- **Only model, `set` and MCP steps run in a group.** So a Built-in step in a *together* block runs as an MCP call on
  the step library's own server (one per step, carrying its signed limits), and *for each item* compiles its steps to
  a per-item workflow. A tool error comes back as a result Conductor counts as success, so with "stop" the step after
  the group checks each Built-in member and stops the run, naming the step.
- **Portable CEL.** A step that hasn't run is absent from the rules' data and tested with `has(steps.<id>)`, which
  every CEL implementation supports.
- **Rules must cover every outcome.** An early replay let the planner finish as "amounts differ" without running the
  three-way match; the rule now requires it for any outcome that claims a match result.
- **Outputs can't be optional at the top level**, so fields such as `confidence` are required, and old scripted
  answers without them count as "sure".
