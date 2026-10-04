# Core concepts in depth

The [README](../README.md#core-concepts) introduces the step types. This page covers the rest of the model: which
engine to use for computation, how data moves between steps, the security boundaries, and the difference between
state and memory.

## Deterministic computation tiers

When a step's job is computation rather than judgment, use a deterministic engine instead of a model. There are
three tiers. Each gives more capability for a weaker guarantee:

| Tier | Can do | Can't do | Guarantee | Here |
|---|---|---|---|---|
| **CEL operators** | Keep, add fields, check, remove duplicates, sort and take, summarize (count, sum, avg, min, max, by group), match two lists, link related items | Loops of your own, string building beyond expressions, anything stateful | Each rule is CEL: not Turing-complete, always terminates, checked as you type. The operator does the iterating | Built-in → CEL rules; and as plain rules in Branch conditions, hard rules, pre-selection, "Before finishing", test expectations |
| **Script** (JavaScript in QuickJS) | Any logic over the inputs | Use arbitrary libraries | Deterministic and sandboxed: no files, network or other programs; 2 seconds and 64 MB per run | Built-in → JavaScript |
| **Free-form code execution** (Python) | Anything, including reading current library docs (e.g. via Context7) and iterating on errors | — | Agentic; bounded only by a step limit; needs a real sandbox | Not built yet |

**Use the narrowest tier that can do the job.** CEL on its own judges one thing at a time: it can't sort, total or
group. The CEL operators close that gap without code: the operator iterates, sorts and totals, and CEL supplies one
small rule per operator. Reach for JavaScript only for logic the operators can't express, and for code execution only
when a task needs an external, changing API surface.

**The CEL operators** run in order, each feeding the next, over the step's `items`:

| Operator | You write | It does |
|---|---|---|
| Keep | a rule: `item.amount > 0` | keeps the items it's true for, noting how many it left out |
| Add fields | named expressions: `aov: item.revenue / item.orders` | computes new fields on each item, in order |
| Check | rules with a message, and on failure: drop, flag or fail the run; per item, or once on the whole list | applies each rule, keeping a reason |
| Remove duplicates | a key, and optionally which to keep | keeps one item per key |
| Sort and take | a value, highest or lowest first, how many | orders the list, keeps the first N |
| Summarize | totals: `count()`, `count(rule)`, `sum(…)`, `avg(…)`, `min(…)`, `max(…)`; optionally by group | replaces the list with the totals, or saves them (`save_as`) for later rules |
| Match | a key on each side, and another list from Takes | adds each item's match from that list (absent when none: `has(item.match)`) |
| Link related | a pair rule over `a` and `b` | joins items into clusters when it holds for any pair |

Every rule sees `item` (or `a` and `b`), `run`, the step's other inputs by name, and anything Summarize saved. Numbers
mix freely: an integer meeting a decimal is treated as a decimal (BigQuery counts are integers, amounts decimals);
dividing two whole numbers gives a whole number, as in CEL. The step returns `items`, `notes` and what it saved.

**Try it** runs a JavaScript step on sample inputs in the editor, or on the inputs it had in the latest run. A thrown
error, a timeout or a missing return field fails the step and says why.

## Data flow between steps

A step's **typed output** is the only channel data moves through. Record types (Booking, Issue, Invoice) describe
the shape; a step's **Takes** are picked references to earlier outputs, never typed free text:

```
read_invoice.invoice.po_number        an earlier step's field
group_trips.trips[*].bookings         every item's field, flattened into one list
run.dry_run · trigger.sender_domain   a run option · the email that started the run
[a.x, b.x]  ·  a.x?                   any of these · optional
```

There is no second, informal channel. A step that needs an earlier step's reasoning gets it as an explicit field
(e.g. `classify.reason`), never as shared context. Two patterns follow:

- **Branch with a shared core.** When a Branch's paths produce different shapes (a flight, a hotel, a portal booking),
  every path should still emit the same core fields (sender, booking reference, travel date) plus a details object
  with the type-specific extras. Later steps reason only about the core, and don't care how many paths feed them.
- **Map.** Some sources return a fixed field set per item and need a second call per item for the rest. GitHub's list
  of pull requests, for example, omits `changed_files`, which only the single-PR call returns. A Parallel block for
  each item runs that call once per item and collects the results as `<block>.results`. Each model-decided Branch
  inside also hands on `<block>.<branch>.decisions` and `.by_path.<path>`: the items that took each path. Its cost
  grows with the list, so concurrency is an explicit setting.

## Sandboxing and security boundaries

Every step reaches the outside world only through a **connection**, and every connection call goes through the
**connector gateway**:

- **Least-privilege tools.** A step's tools are an explicit checklist. An unticked action is absent from the step's
  tool list entirely, not just discouraged in its instructions: the request shape enforces it, not the model's
  compliance.
- **Signed limits.** At run start the service mints one HMAC-signed limits token per connection use: which actions,
  which senders and date range, which repositories, sheets or datasets, which argument values. The gateway checks
  every call against it and logs it, allowed or refused. Some limits only exist mid-run: Double-check may open only
  the emails Tidy up cited.
- **Read vs. act.** Ask steps and Branches can only read. Only Act steps change anything, only with checked fields,
  and after an Approve step if the builder puts one first. Approval is available, never required: an Act step that
  emails text a model wrote, or acts on content other people wrote, with no Approve step before it, gets a suggestion
  on its panel, not a warning.
- **Untrusted text is data.** Email, issue and tool text is labelled as data written by other people, never
  instructions, in every prompt that sees it. Safety checks are "Before finishing" rules and hard rules, not prompt
  wording.
- **Pinned MCP tools.** An admin marks each MCP tool *read*, *act* or *not offered*. Approved tools are pinned: if a
  server changes a tool's description or arguments, the gateway refuses it until an admin reviews it.
- **Secrets stay in the vault.** OAuth tokens, API keys and service-account keys live in the workspace vault. They are
  never sent to the browser, and no model sees them.

CEL's guarantees are stronger than a sandbox's because they're structural, not environmental. CEL has no syntax for
I/O and no unbounded loops, so "can't reach the network" and "always terminates" are properties of the language, not
of what's around it. The same discipline carries to code execution: dependencies are baked into the image at build
time, nothing is installed at run time, and code reads and writes only through connections.

## State vs. memory

These are two different mechanisms, deliberately separate, and each attaches to different step types.

| | State (a checkpoint) | Memory (learned precedent) |
|---|---|---|
| Lives in | The orchestrator | A reviewable store: the agent's **Memory** tab |
| Used by | The orchestrator, to parameterize the next call | The model, shown past cases before it decides |
| Applies to | Ask steps with a filterable or pollable source | Model-decided Branches and Free-form blocks only |
| Update rule | Fixed and mechanical, e.g. watermark = max(seen ids) | The agent proposes; a person answers or corrects before it counts |
| Risk | None: no reasoning surface, nothing to audit | Real: it can silently drift a step's behavior, hence the review |
| Status | Not built yet | Built |

Temporal and Netflix Conductor both draw the same line. Their durable persistence exists to survive a crash *within*
one execution, never to carry state *across* executions implicitly. Cross-run continuity is always an explicit act.
State here should take the same shape: an explicit checkpoint, read at the start of a run and written at the end, held
outside Conductor's own within-run durability.

**Memory never applies to Linear or Act steps.** Their value is the promise that the same input produces the same
output. Memory would be a second, undeclared input, breaking the guarantee that tests and step composability depend
on. How memory works where it applies:

- **Recall.** Before deciding, a `<id>_recall` step finds the most similar confirmed cases. They're matched on the
  fields the builder picks (e.g. `sender_domain`, `issue.author`), most matching fields first, then the newest. The
  model is told they are data from earlier runs, not instructions.
- **Asking by exception.** Every decision says how sure it was (sure, leaning, unsure) and, when unsure, the other
  answer it would pick. A person is asked only about the unsure ones, and about a random 5% of the rest (adjustable)
  so confident mistakes surface too. With 300 issues that's the borderline dozen, not 300.
- **Correcting anything.** Any other decision can be corrected from its line in the run log. Nothing waits on it.
- **Nothing reaches a later run unreviewed.** Only answers and corrections a person gave are recalled. A run works
  from a snapshot of memory taken when it starts (`memory.json` in its folder), so it can be repeated exactly.
- **Staying small.** A newer answer about the same item replaces older ones. Each step keeps at most 200 remembered
  cases (corrections outlast confirmations), 100 waiting and 50 skipped.
