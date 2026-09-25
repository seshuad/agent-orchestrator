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
}
export interface Feedback { ok: boolean; errors: Problem[]; warnings: Problem[]; compiled: string | null; graphs: Record<string, Graph> }

export interface AgentDetail {
  meta: Json & { owner: string; published: number | null; versions: { version: number; published_at: number; note: string }[]; sample_set: string | null; replay: string | null }
  draft: Json
  has_changes: boolean
  feedback: Feedback
  summary: AgentSummary
}

export interface Reference { ref: string; label: string; type: string }

export interface ServiceInfo {
  name: string; icon: string; never: string
  permissions: Record<string, { label: string; actions: string[]; scope: string; detail: string }>
}
export interface Connection {
  id: string; service: string; service_name: string; account: string; label: string; permissions: string[]
  allowed: string[]; connected_at: number; connected_by: string
  used_by: { agent: string; in: string; steps: string[]; actions: string[] }[]
  can_sign_in: boolean; signed_in: boolean; signed_in_as?: string | null; signed_in_at?: number | null
}
export interface GoogleStatus { configured: boolean; client_file: string | null; client_type: string | null; live_services: string[] }
export type ConnectionIn = { service: string; account: string; label: string; permissions: string[]; force?: boolean }

export interface LogEntry {
  at: number; step: string; id: string; kind: string; took: number; cost: number | null; detail: string
  why: string | null; tone: string; plumbing: boolean; model?: string | null; tokens?: number
  tools: { tool: string; args: string }[]; options?: { label: string; value: string }[]; value?: string
}

export interface RunError { title: string; why: string; fix: string; raw: string }

export interface Run {
  id: string; agent: string; version: number | null; status: string; started_at: number; ended_at: number | null
  started_by: string; trigger: string; scripted: boolean; source?: string; cost_usd: number; duration: number
  error: RunError | null; gate: { agent_name: string; prompt: string; options: string[]; option_details: { label: string; value: string; prompt_for?: string | null }[] } | null
  inputs?: Record<string, string>
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
  deleteAgent: (name: string) => call<Json>('DELETE', `/api/agents/${name}`),
  publish: (name: string, note: string) => call<AgentDetail & { version: number }>('POST', `/api/agents/${name}/publish`, { note }),
  references: (name: string, step: string | null) =>
    call<Reference[]>('GET', `/api/agents/${name}/references${step ? `?step=${encodeURIComponent(step)}` : ''}`),
  setTestData: (name: string, sample_set: string | null) => call<AgentDetail>('PUT', `/api/agents/${name}/test-data`, { sample_set }),
  emails: (name: string, source = 'sample') => call<{ id: string; from: string; date: string; subject: string }[]>('GET', `/api/agents/${name}/emails?source=${source}`),
  googleStatus: () => call<GoogleStatus>('GET', '/api/google/status'),
  googleStart: (id: string) => call<{ url: string }>('POST', `/api/connections/${id}/google/start`),
  googleSignOut: (id: string) => call<Connection>('POST', `/api/connections/${id}/google/sign-out`),
  templates: () => call<{ key: string; name: string; description: string }[]>('GET', '/api/templates'),
  sampleSets: () => call<string[]>('GET', '/api/sample-sets'),
  services: () => call<Record<string, ServiceInfo>>('GET', '/api/services'),
  connections: () => call<Connection[]>('GET', '/api/connections'),
  connection: (id: string) => call<Connection>('GET', `/api/connections/${id}`),
  addConnection: (body: ConnectionIn) => call<Connection>('POST', '/api/connections', body),
  editConnection: (id: string, body: ConnectionIn) => call<Connection>('PUT', `/api/connections/${id}`, body),
  removeConnection: (id: string) => call<Json>('DELETE', `/api/connections/${id}`),
  startRun: (name: string, body: { version: number | null; inputs: Record<string, string>; email_id: string | null; scripted: boolean; source: string }) =>
    call<Run>('POST', `/api/agents/${name}/runs`, body),
  runs: (agent?: string) => call<Run[]>('GET', `/api/runs${agent ? `?agent=${agent}` : ''}`),
  run: (id: string) => call<RunDetail>('GET', `/api/runs/${id}`),
  approve: (id: string, choice: string, ids?: string) => call<Run>('POST', `/api/runs/${id}/approve`, { choice, ids: ids ?? null }),
  stop: (id: string) => call<Run>('POST', `/api/runs/${id}/stop`),
  conductorUi: (id: string) => call<{ url: string; mode: 'live' | 'replay' }>('POST', `/api/runs/${id}/conductor`),
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
