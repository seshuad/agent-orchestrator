# Agent Service for Non-Programmers: Design

> Snapshot of the living Claude Docs document, taken 2026-09-24. The live version is the source of truth:
> https://claude.ai/code/artifact/8dd90b77-a44b-454a-a27b-1e1dda2c1a95


 · 

Summary

We build a hosted service where people who do not write code create, run and debug agents from a guided builder. Each agent is saved in our own restricted format, checked against safety rules, compiled to a 
Microsoft Conductor
 workflow, and run in an isolated worker. Users never see or edit Conductor YAML.

The design is based on a working prototype: travel-sync, which reads travel bookings from Gmail, verifies them and adds approved trips to Google Calendar. Rewriting it in Conductor showed which parts a format can express and which parts still needed code. Those gaps become this service's built-in features.

Goals

A non-programmer builds and schedules a working multi-step agent (travel-sync scale) in under 30 minutes, with no YAML and no code.

Users connect accounts (Gmail, Google Calendar, Slack, and so on) once through OAuth. After that, neither the user nor any agent can read the token.

Every run can be inspected step by step: inputs, tool calls, outputs, approvals, cost and errors, including runs that finished weeks ago.

Safe by construction: the service will not run an agent that combines private data, untrusted content and an outward action unless the agent has an approval step.

Non-goals for v1

User-supplied code, containers or MCP servers.

Orchestration where a model decides which step runs next. v1 workflows run steps in an order fixed by the definition.

Self-hosted or on-premises deployment.

Chat-style assistants. v1 agents run on a trigger and finish.

Users and their jobs

Three roles use the service. One person can hold all three in a personal workspace. In a team workspace, they are usually different people.

Role

Who

Main jobs

Builder

An operations person, analyst or assistant who knows the task but does not code

Describe an agent, connect accounts, test it on sample data, schedule it, fix it when a run goes wrong

Approver

The person an agent acts for, or their manager

Review what a run proposes (events to add, emails to send) and approve or reject it from web, Slack, email or phone

Admin

IT or security, in team workspaces

Choose which connectors and models are allowed, set spending limits, review audit logs, revoke access

Builders are the design center. They understand their own task well but cannot read a stack trace or reason about prompt injection. Every screen should use their words ("read my airline emails"), not ours ("MCP tool with sender filter").

Why Conductor, and where it stops

Conductor is a good execution engine for this service, but not a product in itself. It runs one workflow on one machine from a command line. We use it inside each run worker and build everything around it.

What Conductor gives us

Capability

How we use it

YAML workflows with steps in a fixed order, routes and parallel groups

The compile target for our format

Claude provider with a per-agent tool allowlist (tools: [] = none)

Each step gets exactly the tools the compiler grants

MCP servers over stdio

How agents reach our connector gateway

Script, set and MCP steps (no model call)

Built-in deterministic steps: validate, filter, group, write

Human gates, checkpoints and resume

Approvals that can wait hours, then resume the run

Structured JSONL event log, cost tracking, budget limits

The raw feed for run monitoring and debugging

Web dashboard with a workflow graph and replay

A reference for our run viewer; not exposed to users directly

What it lacks

No service layer. No multi-tenant API, no accounts, no scheduler, no stored agent versions.

No credential management. Secrets come from environment variables on the host.

No argument-level limits. YAML can say which tools an agent may call, but not with which arguments. travel-sync needed one MCP server per sender scope to limit a reader to airline emails.

Output schemas check shape only. travel-sync still needed Pydantic to reject timestamps without a UTC offset.

An unsafe default. An agent that omits tools: gets every MCP tool in the workflow. Nothing warns about it.

Why users cannot upload Conductor YAML

A Conductor file can start any program: every MCP server and script step is a command: with arguments. Accepting user YAML would make the service a code-execution platform, with sandboxing and supply-chain risk to match. Instead, users edit our restricted format, and only our compiler writes Conductor YAML. Every command: it emits points at code we ship.

Architecture

A control plane stores agents and decides when they run. Each run executes in its own short-lived worker that runs Conductor. All access to outside systems goes through one connector gateway that holds the tokens.
builder, runs, approvals] --> API[Control plane API]
 API --> DB[(Agents, versions,runs)]
 API --> CMP[Policy check+ compiler]
 SCH[Scheduler+ triggers] --> Q[Run queue]
 API --> Q
 Q --> W[Run workerConductor + step library]
 W --> LLM[Claude API]
 W --> GW[Connector gateway]
 GW --> V[(Credential vault)]
 GW --> EXT[Gmail, Calendar,Slack, ...]
 W --> EV[(Event store)]
 W --> AP[Approval service]
 AP --> UI]]>
The diagram reads left to right: builders work in the web app; the control plane compiles and queues runs; workers execute them and stream events back.

Component

Responsibility

Notes

Control plane API

Agents, versions, connections, schedules, runs, permissions

Multi-tenant; the only thing the web app talks to

Policy check + compiler

Validates an agent definition, applies safety rules, emits Conductor YAML

Pure function of (definition version, workspace policy); output stored with the run

Scheduler + triggers

Cron schedules, webhooks, manual runs

Puts run requests on the queue with the pinned agent version

Run worker

One container per run: Conductor, our step library, a stdio shim to the gateway

No credentials inside; network limited to the Claude API, the gateway and the event store

Connector gateway

Executes every tool call against real services, enforces argument limits, logs each call

Fetches short-lived tokens from the vault per call

Credential vault

Stores OAuth refresh tokens and API keys, encrypted per workspace

Only the gateway can read it

Event store

Ingests Conductor's JSONL event stream plus gateway call logs

Powers run monitoring, debugging and replay

Approval service

Turns human gates into notifications and records answers

Answers a waiting gate through Conductor's gate API

The key boundary: a run worker never holds a token. It can only ask the gateway to perform an allowed call, so a compromised or misled agent cannot take credentials out of the run.

The agent format

An agent is a versioned document in a small format we own. It can express what builders need and nothing that runs arbitrary code. The builder UI edits this document; the compiler turns it into Conductor YAML.

Building blocks

Block

What it declares

Compiles to (Conductor)

trigger

Schedule, webhook, email arrival or manual

Nothing in YAML; the scheduler starts the run

connection

A connected account plus the limits for this agent, such as gmail: read, senders: [united.com]

A stdio MCP server entry pointing at our gateway shim, with a signed limits token

ask step

A model task: instructions, model, allowed connection actions, output shape

An agent step with system_prompt, model, an explicit tools: list (never omitted) and output

Built-in step

validate, filter, dedupe, group_by_date, merge, format

A script step running our step library

approve step

What the approver sees, the choices, who approves, timeout

A human_gate; the safe choice ("do nothing") always first

act step

An outward action with fixed arguments from earlier outputs, such as "create calendar events"

A script or mcp step calling the gateway; no model involved

branch

A condition on an earlier output

routes with when:

parallel

Steps that run at the same time, and what happens if one fails

A parallel group with failure_mode

Rules the format enforces

Only act steps change the outside world, and they take no model-written free text as arguments, only validated fields.

ask steps never get write actions. A model can read and propose; code acts after approval.

Output shapes are strict. Types include datetime_with_offset, email, url and enums; unknown fields are rejected. The compiler adds a validate step after every ask, so checking does not rely on the model.

Everything is referenced by name, never by path or command. There is no field that takes a program, a file path or an environment variable.

Compiler guarantees

The compiler is deterministic: the same definition version and workspace policy always produce the same YAML. Every compiled workflow is stored with its run, so debugging always shows exactly what ran. We pin one Conductor version per release and run its conductor validate in CI against every template.

Creating and editing agents

Builders start from a description or a template, then refine the agent in a step editor that always shows a plain-language summary of what the agent can do. Nothing reaches production without a test run.

Three ways to start

Describe it. The builder writes what they want ("each night, find my travel bookings in email and add them to my calendar after I approve"). A model drafts a definition in our format. The compiler and policy check run on the draft before the builder sees it, so the model cannot propose anything the format forbids.

Pick a template. Vetted patterns such as "read, verify, approve, act" (the travel-sync shape), "summarize and post" and "triage and route". Templates carry good defaults for limits and approvals.

Copy an existing agent in the workspace.

The editor

Flow view. The steps as a graph (the same shape Conductor's dashboard draws), with parallel branches and the approval step clearly marked.

Step panel. A form per step: instructions in plain text, the connection actions it may use (checkboxes with limits, such as sender lists), and the output fields it must return.

Access summary, always visible: "This agent can read emails from 25 sender domains, create events on your calendar after you approve, and spend up to $2 per run." Every change updates it.

Inline warnings from the policy check, written for builders: "This step reads email from outside your company and can post to Slack. Add an approval step, or remove Slack."

Testing

Sample runs on recorded or synthetic data (the travel-sync fixtures pattern), with every act step in dry-run mode: it reports what it would do.

Live read-only runs against the builder's real accounts, still with act steps in dry-run mode.

A new version can only be published after one passing test run, where passing means it finished without errors or failed validation.

Versions

Drafts and published versions. Each run is pinned to the version it started with.

A plain-language diff between versions ("added hotels.com to the senders the hotel reader can read"), with the compiled YAML diff one click away for admins.

One-click rollback to any earlier published version.

Connections and access tokens

Users connect an account once. The token goes into the vault and is used only by the connector gateway. Agents never receive tokens, only the right to ask the gateway for specific actions.

Connecting

OAuth connectors (Google, Microsoft 365, Slack, Notion, and so on) use our registered apps. The builder clicks Connect, signs in with the provider, and sees the exact permissions in plain words.

Narrowest scopes. Each connector asks only for what its actions need: gmail.readonly for reading, calendar.events for creating events. Reading and writing are separate connections with separate tokens, as in travel-sync, so one leaked token cannot do both.

API-key connectors take a pasted key through a write-only field. After saving, the UI shows only the last 4 characters.

Storage and use

Concern

Design

Where tokens live

The vault, encrypted per workspace with a key in a cloud KMS; refresh tokens only

Who can read them

Only the gateway, through a narrow internal API; not the web app, workers or support staff

Use at run time

For each call, the gateway gets a short-lived access token, calls the provider, and discards the token

What is logged

Every gateway call: run, step, connection, action, arguments and result size. Never the token.

Sharing and permissions

A connection belongs to a person or to the workspace. A person's connection can be used only by agents that person owns, unless they share it with named agents.

Using a connection in an agent requires choosing its limits, such as senders, folders, calendars or channels. The limits are saved in the agent version, so changing them is a reviewed change with a diff.

Admins choose which connectors and scopes a workspace may use.

Keeping connections healthy

The gateway detects expired or revoked tokens and marks the connection "needs reconnect". Owners of affected agents are told before the next scheduled run fails.

A "Test connection" button makes one harmless read call.

Disconnecting revokes the token at the provider, deletes it from the vault, and pauses every agent that uses it.

Cost to plan for: Google requires app verification and a yearly third-party security assessment for restricted scopes such as Gmail read access. That is weeks of lead time before launch, not a configuration step.

Safety model

Safety comes from structure, not from asking the model to behave. The policy check refuses risky combinations before a run starts, the gateway enforces limits during the run, and a person approves anything that changes the outside world.

1. Policy check before every publish and run

Each connector action is labeled with what it touches. The compiler traces which steps' outputs can reach which other steps.

Label

Examples

Private data

Read email, read files, read CRM records

Untrusted content

Email bodies, web pages, documents written by outsiders

Outward action

Send email, post to Slack, create calendar events, call a webhook

Rule

Enforced by

If untrusted content can reach an outward action, an approve step must sit between them

Refuse to publish, with a builder-friendly explanation

ask steps never get outward actions; only act steps do, with arguments taken from validated fields

Format and compiler

Every ask step has an explicit tool list; a missing list means none, never all

Compiler (fixes Conductor's unsafe default)

Every agent has a per-run spending limit, enforced

Compiler sets Conductor budget_mode: enforce

2. Enforcement during the run

The gateway rejects any call outside the agent version's limits (wrong sender, wrong calendar, unlisted action) and logs the attempt. The limits travel as a token signed by the control plane, so a worker cannot widen them.

Rate limits per connection and per run stop a looping agent from running up huge API usage.

Workers have no credentials, and their network access is limited to the Claude API, the gateway and the event store.

3. Handling untrusted content

The gateway wraps untrusted content in tags that mark it as data, and the compiler adds a standard notice to every ask step that reads it. This lowers the risk but is not relied on: the structural rules above hold even if a model follows an injected instruction.

4. Approvals

The approver sees exactly what the act step will do ("create these 4 events on calendar Personal"), plus warnings from validation and verification.

A timeout or an unattended run always takes the safe choice: do nothing.

Every decision is logged with the approver, the time and what was shown.

Running agents

Every run is a fresh, isolated worker running one compiled workflow. Runs that wait for approval save a checkpoint and release their worker, so a run can wait hours without holding compute.

Triggers

Trigger

Example

Notes

Schedule

Every weekday at 7:00 in the builder's time zone

Missed runs during an outage run once, not once per missed slot

Manual

Run now, from the editor or the run list

Can override inputs, such as the dry-run flag

Webhook

A form submission starts a run

Payload is untrusted content for the policy check

New item

A new email matching a filter

The gateway polls or subscribes; v1 may ship polling only

Run lifecycle
 Queued
 Queued --> Running: worker starts
 Running --> Waiting: approval step
 Waiting --> Running: approved, resume from checkpoint
 Waiting --> Done: rejected or timed out
 Running --> Done: finished
 Running --> Failed: error or limit hit
 Running --> Cancelled: user stops it
 Failed --> Queued: retry]]>
A run moves through these states; Waiting is the only state that can last hours or days.

Isolation and limits

One container per run in a hardened runtime such as gVisor or Firecracker, with a pinned image containing Conductor and our step library. The filesystem is deleted when the run ends.

Waiting for approval uses Conductor's checkpoint and resume: the worker saves state and exits at the gate, and a new worker resumes after the answer. This needs an early spike (see open questions).

Per-workspace quotas: concurrent runs, maximum run time (Conductor timeout_seconds), and monthly spend.

Retries: steps use Conductor's retry policy for provider errors and timeouts. A failed run can be retried from the last successful step. act steps use idempotency keys (as travel-sync's writer does), so a retry never creates duplicates.

Monitoring and debugging runs

A builder should be able to answer "what happened and how do I fix it" without reading logs. Each run keeps two event streams: Conductor's step events and the gateway's call log. The run viewer merges them and explains failures in plain words.

Views

View

Shows

Main question it answers

Runs list

Status, trigger, agent version, start time, duration, cost, warning count; filter by agent, status, date

Is everything running?

Run graph

The workflow graph with each step's state, like Conductor's dashboard

Where did it stop?

Step inspector

Instructions as sent, model, every tool call with arguments and result, output, validation result, tokens, cost

Why did this step do that?

Approval record

What the approver saw, their choice, when

Who allowed this?

Compiled workflow

The exact Conductor YAML that ran (admins only)

What exactly executed?

Explaining failures

Raw errors are mapped to a cause and a fix. For example, the travel-sync test run failed with "Failed to connect to claude provider" because no API key was set. In this service, that error would read:

Raw error

What the builder sees

Suggested fix

Provider connection or auth failure

"The service could not reach the AI model. This is on our side; the run will retry."

None; paged to on-call if it repeats

OAuth token revoked or expired

"Your Gmail connection needs to be reconnected."

Reconnect button

Gateway limit rejection

"The hotel reader tried to read an email from booking.com, which is outside its allowed senders."

Add the sender, or ignore

Output validation failure

"The airline reader returned a flight time without a time zone."

Open the step; the output is highlighted

Budget reached

"This run stopped at its $2 limit after the verifier step."

Raise the limit or narrow the search

Quality signals, not just errors

A run can finish "successfully" and still be wrong. travel-sync found a reader that marked every email as skipped without opening any of them. The viewer computes checks from the gateway log, which records what actually happened rather than what the model reported:

"Search found 12 emails; this step read 0 of them."

"This step returned no results, but earlier versions found about 5 per run."

"3 of 7 bookings failed verification."

Fixing and replaying

Replay: step through a finished run event by event (built on Conductor's replay).

Re-run a step with changes: edit a step's instructions and re-run just that step on the recorded inputs, in dry-run mode, then compare outputs side by side. This is the fastest way to fix a prompt.

Save as test case: turn a problem run's inputs into a sample for future test runs.

Alerts and cost

Alerts by email or Slack for a failed run, a connection needing reconnection, an approval waiting more than a set time, and spend passing a threshold.

Cost per step, per run and per agent per month, from Conductor's cost tracking.

Retention and privacy

Run records contain email text and other personal data. Default retention is 30 days, configurable per workspace. Run records are visible only to the agent's owner, its approvers and admins. Deleting an agent deletes its run records.

Walkthrough: an agent that finds travel bookings

Alex, a builder with no coding background, creates an agent that reads their email and lists their upcoming travel bookings. It takes five screens and about 10 minutes. The results in part 3 come from a real run of the prototype on the project's 10 sample emails.

Part 1: Create the agent

1. Describe it. Alex clicks New agent and types:

Every morning, look through my email for flight, hotel and car bookings and show me my upcoming trips.

2. Review the draft. The service drafts an agent, runs the policy check on it, and shows the plan in plain words:
 D[Tidy upremove duplicates, group into trips]
 B[Read hotel emails] --> D
 C[Read booking-siteand car emails] --> D
 D --> E[Double-check each bookingagainst its email]
 E --> F[Show results]]]>
The three readers run at the same time, and each can open only its own senders' emails. The double-check step uses a stronger model and can only open emails the readers cited.

3. Connect Gmail. The draft needs one connection. Alex clicks Connect Gmail, signs in with Google, and approves one permission: "Read your email" (gmail.readonly). The service never asks for permission to send or delete.

4. Check the limits. The template fills in sensible limits, and Alex can edit each one:

Setting

Filled in

Alex's change

Airline senders

united.com, delta.com, aa.com and 7 more

None

Hotel senders

marriott.com, hilton.com, hyatt.com and 4 more

None

Booking-site and car senders

expedia.com, booking.com, hertz.com and 5 more

None

How far back to look

180 days

None

Schedule

Every day at 7:00, Alex's time zone

Weekdays only

Spending limit

$0.50 per run

None

5. Read the access summary. Before saving, the service shows exactly what the agent can do:

This agent can read emails from 25 travel senders in the last 180 days. It cannot send, delete or change anything, so it needs no approval step. It can spend up to $0.50 per run.

If Alex had added "and email me the list", the policy check would have said: "This agent reads email written by outsiders and would send email. Add an approval step, or use a notification instead." Service notifications are not a connector action, so they need no approval.

Part 2: Test, publish and run

6. Test on sample emails. Alex clicks Try it on sample emails. The service runs the agent against a built-in sample inbox of 10 travel emails: real-looking confirmations, a promotion, a forwarded booking for someone else, and one email with hidden instructions aimed at the AI. Nothing touches Alex's real mailbox. The test passes (no errors, every output valid), which unlocks Publish.

7. Publish. Alex publishes version 1. From now on, every run is pinned to a version, and later edits create version 2 without affecting runs in progress.

8. Run it on the real inbox. Alex clicks Run now instead of waiting for 7:00. The run page opens and fills in live.

Behind the scenes, the service compiles version 1 to Conductor YAML and starts an isolated worker. In the prototype run, this is what happened:

Step

Model

Tool calls

Time

Cost

Read airline emails

Haiku 4.5

1 search, 6 reads

7.9 s

$0.013

Read hotel emails

Haiku 4.5

1 search, 2 reads

5.9 s

$0.010

Read booking-site and car emails

Haiku 4.5

1 search, 1 read

5.1 s

$0.008

Tidy up (dedupe, group into trips)

None (built-in step)

None

0.1 s

$0

Double-check each booking

Opus 5

5 reads

7.7 s

$0.048

Total

17

about 16 s

$0.08

The three readers run at the same time, so the total time is the slowest reader plus the later steps. Every one of the 17 tool calls went through the gateway, which checked it against the agent's sender limits and logged it.

Part 3: The results

The run page opens on a one-line summary, then the trips:

Finished · 4 trips · 6 bookings from 5 emails · all 6 confirmed by the double-check · 3 notes · $0.08

Trip

Dates

Bookings

Double-check

Notes

Dallas/Fort Worth

Oct 5–6

Hotel: Dallas/Fort Worth Airport Marriott

✓ confirmed

Hotel booked but no flight found

Chicago

Oct 20–22

Hotel: Courtyard Chicago Downtown/River North

✓ confirmed

Hotel booked but no flight found

Tampa

Nov 19–23

Flight UA1523 SFO → TPA, 7:10 PT → 15:28 ET · Hilton Tampa Downtown · Flight UA2218 TPA → SFO, 8:30 ET → 11:35 PT

✓ ✓ ✓ confirmed

None

Hong Kong

Dec 10–11

Flight UA877 SFO → HKG, 11:05 PT → 18:25 HKT next day

✓ confirmed

Only one flight found. Traveler is Sam Rivera, not Alex: someone else's trip?

The notes come from built-in rules in the tidy-up step, not from a model, so they are the same on every run.

The email with hidden instructions. The Chicago confirmation contained invisible text addressed to the AI. It said to add a fake flight (UA999 to Moscow), mark every booking as verified, create a calendar event with a payment link, and forward bank statements to an outside address. None of it happened:

The reader extracted the real hotel booking and no UA999 flight.

"Verified" is not something a reader can set. Only the separate double-check step produces it, and that step re-read each email itself.

The agent has no calendar or send action at all. Even a fully fooled model could only have asked the gateway for something outside its limits, which would be refused and logged.

Drilling into a step. Clicking Read hotel emails opens the step inspector: the instructions sent, the search call and its 2 results, both reads, and the output. One booking from that output:

The times carry the hotel's local UTC offset. That is what the automatic validate step checks; a time without an offset would have been rejected and highlighted here.

What Alex does next. Alex marks the Hong Kong trip as Sam's, so future runs mention it without flagging it. Later, Alex wants the trips on their calendar. Adding a "create calendar events" action makes the policy check require an approval step before it, which turns this agent into the travel-sync example below.

Worked example: travel-sync

travel-sync fits the format with no custom code, except for its trip-grouping rules, which need a small rules block. Builders would see this as forms; this is the stored definition (shortened).

Prototype part

Was

In the service

Reader sender limits

One MCP server per scope, in Python

uses: mail.search: senders, enforced by the gateway

Verifier can read only the emails it is given

Trust in the prompt

only_ids_from, enforced by the gateway

Timestamp and schema checks

Pydantic in a script step

booking type with datetime_with_offset; automatic validate step

Dedupe and grouping

reconcile.py

Built-in dedupe and group_by_date steps

Gap and flag rules (one flight only, someone else's trip)

reconcile.py

Not expressible in v1; needs a rules block or an ask step (open question)

Reader skipped emails without reading

Retry logic in the orchestrator

Quality signal in the run viewer; optional automatic retry

Approval

Terminal prompt or Conductor gate

approve step with notifications

Calendar writer

Python, idempotent

act step with an idempotency key

The policy check passes this agent because the only outward action, creating calendar events, comes after an approve step, and no ask step has a write action.

Phasing, risks and open questions

Start with spikes that test the riskiest assumptions, then a private beta for individuals on Google accounts only.

Phases

Phase

Scope

Exit criterion

0. Spikes

Compiler for the travel-sync definition; gateway shim with signed limits; checkpoint and resume at an approval step; start Google app verification

travel-sync runs end to end from the format in an isolated worker

1. Private beta

Personal workspaces, Gmail and Calendar, 3 templates, step editor, test runs, run viewer, approvals by web and email

20 builders each run an agent on a schedule for 2 weeks

2. Teams

Team workspaces, admin controls, Slack and Microsoft 365, alerts, re-run step with changes

First paying team workspace

3. Breadth

Describe-it drafting, rules block, webhook and new-item triggers, more connectors

Most new agents start from a description

Risks

Risk

Why it matters

Mitigation

Conductor changes under us

It is young and moving fast; its own docs were already out of date for approval-step options when we built travel-sync

Pin a version; run the compiler's templates through conductor validate in CI; keep the compiler target swappable

One container per run is slow or costly

Conductor is a single-machine command-line tool

Measure cold start in phase 0; pre-warmed pools; release workers while waiting for approval

The format is too narrow

Builders hit a wall and want code

Grow built-in steps from real requests; the rules block; an ask step as the fallback

Google verification delays launch

Restricted Gmail scopes need a security assessment

Start in phase 0; beta under Google's test-user limits

Agents fail without errors

A run can finish while doing the wrong thing

Quality signals from the gateway log; test cases from past runs

Stored personal data

Run records hold email content

Short default retention, per-workspace encryption, strict access

Open questions

Rules block or ask step for domain rules like travel-sync's gap checks? A rules block is predictable but adds a mini-language for builders to learn.

Keep Conductor as the only engine, or also compile to Managed Agents, which already provides scheduling, credential vaults and hosted sessions?

Does Conductor's checkpoint and resume work across workers at a waiting approval step, or do we need to add that?

Pricing: per run, per agent, or pass-through model cost plus a platform fee?

Should low-risk agents (no untrusted content, or no outward action) run with no approval at all?