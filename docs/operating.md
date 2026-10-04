# Operating it

## Path to enterprise-ready

This is a personal prototype. Most of the step-type and sandboxing decisions above already assume a multi-tenant
service, so what remains is mostly a storage-layer swap, not a redesign:

| Area | Now | Open design question |
|---|---|---|
| Workspace state | Files on one machine (`.workspace/`) | Per-tenant schemas vs. a database per tenant; which state is transactional (step config) and which eventually consistent (run logs) |
| AuthN / AuthZ | One signed-in user with an Admin role; connector-level "who may connect" | Real identity (OIDC / SAML); authorization scoped to connections: who may attach a Gmail account to a step someone else built |
| Deployment | Local, or one pod on GKE Autopilot (`deploy/gke/`); one `conductor run` process per run | The code-execution sandbox as ephemeral per-run pods vs. a warm pool |
| Observability | Per-run event log, gateway log and step inspector | A span per step for Linear and Branch; a span per turn, with retries and tool calls, for Free-form |

Tenant isolation and connection-scoped authorization have the most open design surface. Kubernetes deployment is
familiar ground.

Not built yet:

- Schedules and email triggers that fire on their own (Pub/Sub triggers do)
- Repair actions for pipelines (re-run a job, quarantine a file), narrowly scoped, with an Approve step available
- Native Iceberg metadata reads in the Cloud Storage connector; Avro files
- Notifications for approvals
- The state checkpoint
- Free-form code execution
- Sheets and Calendar on real accounts
- More than one Free-form block per agent

## Operating it in production

The step types exist partly for incident response. A failing Linear step is a contained problem, bad output for a
known input, and can be reverted on its own without touching the rest of the agent. A single mega-prompt offers no such
isolation.

An AI assistant can triage fast when the observability is there: correlating an error spike with a recent publish,
reading a stack trace, spotting a timing-out connection. It is least reliable exactly where confidence doesn't signal
correctness: a generated fix reads as fluent and certain whether or not it's right. The mitigations, in order:

1. **Roll back before fixing live.** Going back to the last good version is faster and safer than diagnosing under
   pressure, for a person or an AI. Published versions never change, and Run now can pick any of them.
2. **A real code owner, even part-time**, who reviews changes closely enough to build real understanding over time.
3. **Rehearsed reviews, not only reactive ones.** Break a step in staging now and then, and have the code owner judge
   whether a proposed fix would have been right. That calibrates trust with evidence.
4. **Review by blast radius, not by reading everything.** A fix that touches only one step's declared inputs and
   outputs is a small, checkable claim, even for a reviewer who couldn't have written it. This is what the step-type
   contracts are for.
