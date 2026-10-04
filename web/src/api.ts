// The designer's API client. Every call goes to the Python service (src/agent_service/server/app.py).

export type Json = Record<string, any>

export interface Session {
  user: { name: string; initials: string; email: string; role: string }
  workspace: { name: string; kind: string; members: { name: string; initials: string; role: string }[]; spend_limit_usd: number }
  claude_api: boolean
  spend_usd: number
  week: Record<string, number>
}

export interface AgentSummary {
  name: string; description: string; owner: string; status: 'published' | 'draft'; version: number | null
  has_changes: boolean; trigger: Json; trigger_text: string; next_run: string | null
  last_run: { id: string; status: string; started_at: number; started_by: string; trigger: string } | null
  recent: string[]
}

export interface Problem { path: string; message: string }
export interface GraphNode { id: string; name: string; kind: string; row: number; mark: string | null }
export interface Graph {
  nodes: GraphNode[]
  edges: { from: string; to: string; label: string; optional: boolean; either: boolean }[]
  loops: { from: string; to: string; label: string }[]
  groups?: { name: string; members: string[] }[]     // rows the planner can run at the same time
}
export interface Feedback { ok: boolean; errors: Problem[]; warnings: Problem[]; suggestions?: Problem[]; compiled: string | null; graphs: Record<string, Graph> }

export interface AgentDetail {
  meta: Json & { owner: string; published: number | null; versions: { version: number; published_at: number; note: string }[]; sample_set: string | null; replay: string | null; ai?: AiNote | null; can_undo_ai?: boolean }
  draft: Json
  has_changes: boolean
  feedback: Feedback
  summary: AgentSummary
}

export interface Reference { ref: string; label: string; type: string }

export interface ServiceInfo {
  name: string; icon: string; never: string; sign_in?: 'google' | 'token'
  permissions: Record<string, { label: string; actions: string[]; scope: string; detail: string }>
}
export interface AiNote {
  kind: 'created' | 'changed'; request: string; at: number; summary: string; assumptions: string[]; questions: string[]
  errors: Problem[]; model: string; cost_usd: number; chat?: string
}
export interface DraftJob {
  id: string; kind: 'create' | 'refine'; status: 'running' | 'done' | 'failed'; stage: string; started_at: number; ended_at?: number
  attempts: number; cost_usd: number; error: string | null; result: (Omit<AiNote, 'kind' | 'request' | 'at'> & { agent: string }) | null
}
export interface StepDetail {
  step: string; n: number; runs: number; type: 'agent' | 'script' | 'mcp' | 'set' | 'human_gate'; took: number; at: number; error: string | null
  model?: string; tokens?: number; input_tokens?: number; output_tokens?: number; cost?: number; system_prompt?: string; prompt?: string
  tools?: { tool: string; args: unknown; result: string | null; truncated: { kept: number; of: number } | null; gateway?: { outcome: string; detail: string } }[]
  repairs?: string[]; output?: unknown; inputs?: unknown; in_group?: string | null; stderr?: string | null; scripted?: boolean
  calls?: { action: string; outcome: string; detail: string; args: unknown; result?: string }[]
  options?: { value: string; label: string }[]; chosen?: string; additional?: { ids?: string }
  rerunnable: boolean; rerun_of?: { run: string; step: string; n: number } | null; reruns?: string[]
}
export interface Expectation { name: string; rule: string }
export interface TestCase {
  id: string; name: string; from_run: string; inputs: Record<string, string>; email_id: string | null; scripted: boolean; source: string
  approvals: Record<string, { choice: string; ids: string | null }>; expect: Expectation[]
  last: null | { run: string; status: string; at: number; stale: boolean; cost_usd: number
    result: null | { passed: boolean; results: (Expectation & { passed: boolean; value: unknown; error: string | null })[] } }
}
export const chartUrl = (run: string, image: string) => `/api/runs/${run}/charts/${image.split('/').pop()}`

export interface BuildChat {
  id: string; status: 'idle' | 'thinking' | 'error'; error: string | null; source: 'live' | 'sample'; sample_set: string | null
  agent: string | null; cost_usd: number; created_at: number
  transcript: ({ role: 'user' | 'assistant'; text: string; at: number } | { role: 'tool'; at: number; tool: string; label: string; detail: string; ok: boolean; agent?: string })[]
  explored: { kind: 'dataset' | 'table' | 'folder' | 'file'; name: string; detail: string; account: string; at: number }[]
}

export interface PubSubStatus {
  listening: boolean; paused: boolean; published_subscription: string | null; last_pull_at: number | null; last_error: string | null
  messages: { at: number; message_id: string; outcome: 'started' | 'skipped' | 'duplicate' | 'failed'; run?: string; detail?: string; fields?: Json }[]
}
export interface MemoryCase {
  id: string; run: string; at: number; step: string; step_name: string; kind: 'branch' | 'free-form'
  status: 'candidate' | 'confirmed' | 'corrected' | 'rejected'; decision: string; reason?: string | null; evidence?: string[]
  summary?: string; notes?: string[]; keys: Record<string, unknown>; choices: string[]; subject?: string | null
  ref?: string; asked_because?: 'unsure' | 'sample' | null; confidence?: string; runner_up?: string
  correction?: { decision: string; note: string } | null; confirm_note?: string | null; confirmed_by?: string; confirmed_at?: number
}
export interface Catalog { name: string; icon: string; never: string; permissions: Record<string, { label: string; actions: string[]; scope: string; detail: string }> }
export interface McpTool {
  name: string; description: string; input_schema: Json; treat: 'read' | 'act' | 'off'; limits: string[]; limitable: string[]
  pin?: string; approved_pin?: string | null; new?: boolean; changed?: boolean
}
export interface Connector {
  id: string; type: 'google' | 'github' | 'mcp' | 'bigquery' | 'gcs' | 'microsoft365' | 'trino' | 'dataproc'; type_name: string; name: string; icon: string; reach: string
  secrets_set?: { client_secret: boolean; smtp_password: boolean }
  settings: Json; offered?: Record<string, string[]>; tools?: McpTool[]; who: 'builders' | 'admins'; domains: string[]
  status: { state: 'ready' | 'attention' | 'setup'; message?: string; tested_at?: number | null; tested_by?: string; reason?: string }
  secret_set: boolean; secret_set_at?: number | null; accounts: number; created_by?: string
  services: Record<string, Catalog>; sign_in: 'google' | 'token' | 'oauth' | 'shared' | 'none'; admin_signed_in?: boolean
}
export type ConnectorIn = { type?: string; name?: string; settings?: Json; secret?: string | null; offered?: Record<string, string[]>; who?: string; domains?: string[]; tools?: { name: string; treat: string; limits: string[] }[] }
export interface Connection {
  id: string; service: string; service_name: string; account: string; label: string; permissions: string[]
  connector?: string | null; connector_name?: string | null; catalog: Catalog
  allowed: string[]; connected_at: number; connected_by: string
  used_by: { agent: string; in: string; steps: string[]; actions: string[] }[]
  can_sign_in: boolean; sign_in: 'google' | 'token' | 'oauth' | 'shared' | 'none'; signed_in: boolean; signed_in_as?: string | null; signed_in_at?: number | null
}
export interface GoogleStatus { configured: boolean; client_file: string | null; client_type: string | null; live_services: string[] }
export type ConnectionIn = { connector?: string | null; service: string; account?: string; label: string; permissions: string[]; force?: boolean }

export interface LogEntry {
  at: number; step: string; id: string; n?: number; kind: string; took: number; cost: number | null; detail: string
  why: string | null; tone: string; plumbing: boolean; model?: string | null; tokens?: number
  tools: { tool: string; args: string }[]; options?: { label: string; value: string }[]; value?: string
  ref?: string; choices?: string[]; decision?: string | null; confidence?: 'sure' | 'leaning' | 'unsure'; runner_up?: string | null
  image?: string | null
}

export interface RunError { title: string; why: string; fix: string; raw: string }

export interface Run {
  id: string; agent: string; version: number | null; status: string; started_at: number; ended_at: number | null
  started_by: string; trigger: string; scripted: boolean; source?: string; cost_usd: number; duration: number
  error: RunError | null; gate: { agent_name: string; prompt: string; options: string[]; option_details: { label: string; value: string; prompt_for?: string | null }[] } | null
  inputs?: Record<string, string>
  rerun_of?: { run: string; step: string; n: number } | null
  data?: { real: string[]; sample: string[]; sample_set: string; text: string; short: string }
  test?: { id: string; name: string } | null
  test_result?: TestCase['last'] extends infer L ? (L extends { result: infer R } ? R : never) : never
}

export interface RunDetail extends Run {
  tokens: number; log: LogEntry[]
  checks: { calls: number; refused: Json[] }
  outcome: Json
  events_file: string
}

async function call<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).detail ?? detail } catch { /* not JSON */ }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return res.json() as Promise<T>
}

export const api = {
  session: () => call<Session>('GET', '/api/session'),
  agents: () => call<AgentSummary[]>('GET', '/api/agents'),
  agent: (name: string) => call<AgentDetail>('GET', `/api/agents/${name}`),
  createAgent: (body: { name: string; description: string; start: string; sample_set: string | null }) =>
    call<AgentDetail>('POST', '/api/agents', body),
  saveAgent: (name: string, draft: Json) => call<{ feedback: Feedback; has_changes: boolean }>('PUT', `/api/agents/${name}`, { draft }),
  memory: (name: string) => call<MemoryCase[]>('GET', `/api/agents/${name}/memory`),
  runMemory: (run: string) => call<MemoryCase[]>('GET', `/api/runs/${run}/memory`),
  correctJudgment: (run: string, body: { ref: string; decision: string; note?: string }) => call<MemoryCase>('POST', `/api/runs/${run}/judgments`, body),
  judgeMemory: (name: string, id: string, body: { verdict: string; decision?: string; note?: string }) => call<MemoryCase>('POST', `/api/agents/${name}/memory/${id}`, body),
  renameAgent: (name: string, newName: string) => call<AgentDetail>('POST', `/api/agents/${name}/rename`, { name: newName }),
  deleteAgent: (name: string) => call<Json>('DELETE', `/api/agents/${name}`),
  describeAgent: (body: { description: string; name: string; sample_set: string | null }) => call<{ job: string }>('POST', '/api/agents/describe', body),
  refineAgent: (name: string, instruction: string) => call<{ job: string }>('POST', `/api/agents/${name}/refine`, { instruction }),
  suggest: (name: string, draft: Json, path: (string | number)[], field: string) =>
    call<{ text: string; model: string; cost_usd: number }>('POST', `/api/agents/${name}/suggest`, { draft, path, field }),
  clearAiNote: (name: string) => call<Json>('POST', `/api/agents/${name}/ai-note/clear`),
  undoRefine: (name: string) => call<AgentDetail>('POST', `/api/agents/${name}/refine/undo`),
  draftJob: (job: string) => call<DraftJob>('GET', `/api/drafts/${job}`),
  buildStart: (body: { message: string; source: string; sample_set: string | null }) => call<BuildChat>('POST', '/api/build', body),
  buildGet: (id: string) => call<BuildChat>('GET', `/api/build/${id}`),
  buildSay: (id: string, text: string) => call<BuildChat>('POST', `/api/build/${id}/message`, { text }),
  buildStop: (id: string) => call<BuildChat>('POST', `/api/build/${id}/stop`),
  publish: (name: string, note: string) => call<AgentDetail & { version: number }>('POST', `/api/agents/${name}/publish`, { note }),
  references: (name: string, step: string | null) =>
    call<Reference[]>('GET', `/api/agents/${name}/references${step ? `?step=${encodeURIComponent(step)}` : ''}`),
  setTestData: (name: string, sample_set: string | null) => call<AgentDetail>('PUT', `/api/agents/${name}/test-data`, { sample_set }),
  emails: (name: string, source = 'sample') => call<{ id: string; from: string; date: string; subject: string }[]>('GET', `/api/agents/${name}/emails?source=${source}`),
  googleStatus: () => call<GoogleStatus>('GET', '/api/google/status'),
  googleStart: (id: string) => call<{ url: string }>('POST', `/api/connections/${id}/google/start`),
  googleSignOut: (id: string) => call<Connection>('POST', `/api/connections/${id}/google/sign-out`),
  githubToken: (id: string, token: string) => call<Connection>('POST', `/api/connections/${id}/github/token`, { token }),
  signOut: (id: string) => call<Connection>('POST', `/api/connections/${id}/sign-out`),
  templates: () => call<{ key: string; name: string; description: string }[]>('GET', '/api/templates'),
  sampleSets: () => call<string[]>('GET', '/api/sample-sets'),
  services: () => call<Record<string, ServiceInfo>>('GET', '/api/services'),
  connections: () => call<Connection[]>('GET', '/api/connections'),
  pubsub: (name: string) => call<PubSubStatus>('GET', `/api/agents/${name}/pubsub`),
  pubsubPause: (name: string, paused: boolean) => call<PubSubStatus>('POST', `/api/agents/${name}/pubsub`, { paused }),
  pubsubCheck: (name: string) => call<{ ok: boolean; topic?: string; message: string }>('POST', `/api/agents/${name}/pubsub/check`),
  connection: (id: string) => call<Connection>('GET', `/api/connections/${id}`),
  addConnection: (body: ConnectionIn) => call<Connection>('POST', '/api/connections', body),
  editConnection: (id: string, body: ConnectionIn) => call<Connection>('PUT', `/api/connections/${id}`, body),
  removeConnection: (id: string) => call<Json>('DELETE', `/api/connections/${id}`),
  mcpStart: (id: string) => call<{ url: string }>('POST', `/api/connections/${id}/mcp/start`),
  connectors: () => call<Connector[]>('GET', '/api/connectors'),
  connectorTypes: () => call<Record<string, { name: string; icon: string; services: Record<string, ServiceInfo> }>>('GET', '/api/connector-types'),
  connector: (id: string) => call<Connector>('GET', `/api/connectors/${id}`),
  addConnector: (body: ConnectorIn) => call<Connector>('POST', '/api/connectors', body),
  editConnector: (id: string, body: ConnectorIn) => call<Connector>('PUT', `/api/connectors/${id}`, body),
  removeConnector: (id: string) => call<Json>('DELETE', `/api/connectors/${id}`),
  testConnector: (id: string) => call<Connector>('POST', `/api/connectors/${id}/test`),
  connectorOauthStart: (id: string) => call<{ url: string }>('POST', `/api/connectors/${id}/oauth/start`),
  startRun: (name: string, body: { version: number | null; inputs: Record<string, string>; email_id: string | null; scripted: boolean; source: string; message?: Json | null }) =>
    call<Run>('POST', `/api/agents/${name}/runs`, body),
  runs: (agent?: string) => call<Run[]>('GET', `/api/runs${agent ? `?agent=${agent}` : ''}`),
  run: (id: string) => call<RunDetail>('GET', `/api/runs/${id}`),
  approve: (id: string, choice: string, ids?: string) => call<Run>('POST', `/api/runs/${id}/approve`, { choice, ids: ids ?? null }),
  stop: (id: string) => call<Run>('POST', `/api/runs/${id}/stop`),
  conductorUi: (id: string) => call<{ url: string; mode: 'live' | 'replay' }>('POST', `/api/runs/${id}/conductor`),
  bqEstimate: (name: string, body: { sql: string; connection: string; params: Json; datasets?: string[]; max_bytes?: string }) =>
    call<{ ok: boolean; error?: string; statement?: string; tables: string[]; bytes: number; human: string; cost_usd: number; limit: string; problems: string[] }>('POST', `/api/agents/${name}/bigquery/estimate`, body),
  tryJs: (code: string, inputs: Json, returns: string[]) =>
    call<{ ok: boolean; output?: unknown; error?: string; took: number }>('POST', '/api/javascript/try', { code, inputs, returns }),
  inspectStep: (run: string, step: string, n: number) => call<StepDetail>('GET', `/api/runs/${run}/steps/${step}/${n}`),
  rerunStep: (run: string, step: string, n: number) => call<Run>('POST', `/api/runs/${run}/steps/${step}/${n}/rerun`),
  testSuggestion: (run: string) => call<Omit<TestCase, 'id' | 'last'>>('GET', `/api/runs/${run}/test-suggestion`),
  tests: (name: string) => call<TestCase[]>('GET', `/api/agents/${name}/tests`),
  addTest: (name: string, from_run: string, testName: string, expect: Expectation[]) => call<TestCase[]>('POST', `/api/agents/${name}/tests`, { from_run, name: testName, expect }),
  editTest: (name: string, id: string, body: { name?: string; expect?: Expectation[] }) => call<TestCase[]>('PUT', `/api/agents/${name}/tests/${id}`, body),
  removeTest: (name: string, id: string) => call<TestCase[]>('DELETE', `/api/agents/${name}/tests/${id}`),
  runTests: (name: string, only?: string) => call<{ batch: string; runs: string[] }>('POST', `/api/agents/${name}/tests/run${only ? `?only=${only}` : ''}`),
  approvals: () => call<{ run: string; agent: string; started_at: number; gate: NonNullable<Run['gate']> }[]>('GET', '/api/approvals'),
}

/** Follow a run live over server-sent events until it ends. Returns a function that stops listening. */
export function followRun(id: string, onUpdate: (d: RunDetail) => void): () => void {
  const source = new EventSource(`/api/runs/${id}/stream`)
  source.onmessage = (e) => {
    const d = JSON.parse(e.data) as RunDetail
    onUpdate(d)
    if (d.status !== 'running' && d.status !== 'waiting') source.close()
  }
  source.onerror = () => source.close()
  return () => source.close()
}

export function when(ts: number | null | undefined): string {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  const today = new Date()
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  if (d.toDateString() === today.toDateString()) return `Today ${time}`
  return `${d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' })}, ${time}`
}

export function duration(s: number | null | undefined): string {
  if (!s) return '0s'
  if (s < 60) return `${Math.round(s)}s`
  const m = Math.floor(s / 60)
  return m < 60 ? `${m}m ${Math.round(s % 60)}s` : `${Math.floor(m / 60)}h ${m % 60}m`
}

export const STATUS_LABEL: Record<string, string> = {
  succeeded: 'Succeeded', failed: 'Failed', stopped: 'Stopped', waiting: 'Waiting for approval', running: 'Running',
}
