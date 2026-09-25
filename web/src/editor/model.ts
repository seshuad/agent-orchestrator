// The editor's model: the agent definition as plain JSON, edited by path; what's selected; new-step templates.
import type { Json } from '../api'

export type Path = (string | number)[]
export const pathStr = (p: Path) => p.join('.')

export function getIn(obj: any, path: Path): any {
  return path.reduce((o, k) => (o == null ? undefined : o[k]), obj)
}

export function setIn<T>(obj: T, path: Path, value: unknown): T {
  if (!path.length) return value as T
  const [k, ...rest] = path
  const copy: any = Array.isArray(obj) ? [...(obj as any)] : { ...(obj as any) }
  copy[k] = setIn(copy[k] ?? (typeof rest[0] === 'number' ? [] : {}), rest, value)
  if (value === undefined && !rest.length) delete copy[k]
  return copy
}

export type Selection =
  | { type: 'settings' }
  | { type: 'connections' }
  | { type: 'record'; name: string }
  | { type: 'step'; path: Path }

export const STEP_KINDS = [
  { kind: 'ask', name: 'Ask', text: 'A model reads and extracts. It can never change anything.' },
  { kind: 'built-in', name: 'Built-in', text: 'Fixed operations, no model: tidy up, look up, compare, match.' },
  { kind: 'approve', name: 'Approve', text: 'A person decides before anything changes.' },
  { kind: 'act', name: 'Act', text: 'Changes something outside the agent, using only checked fields.' },
] as const
export const FLOW_KINDS = [
  { kind: 'branch', name: 'Branch', text: 'Pick one path based on an earlier result.' },
  { kind: 'free-form', name: 'Free-form', text: 'A model picks which of these steps to run, and how often, toward a goal.' },
] as const

export const MODELS = ['claude-opus-5', 'claude-sonnet-5', 'claude-haiku-4-5']
export const MODEL_LABEL: Record<string, string> = {
  'claude-opus-5': 'Claude Opus 5 · thorough', 'claude-sonnet-5': 'Claude Sonnet 5 · balanced', 'claude-haiku-4-5': 'Claude Haiku 4.5 · fast, low cost',
  'claude-opus-5-5': 'Claude Opus 5.5 (not supported by the run engine)',
}

export function allIds(draft: Json): string[] {
  const out: string[] = []
  const walk = (steps: Json[] = []) => steps.forEach((s) => { out.push(s.id); walk(s.steps) })
  walk(draft.steps)
  return out
}

function uniqueId(draft: Json, base: string): string {
  const ids = new Set(allIds(draft))
  let id = base, n = 2
  while (ids.has(id)) id = `${base}_${n++}`
  return id
}

export function newStep(kind: string, draft: Json): Json {
  const firstConn = (service: string) => Object.entries(draft.connections ?? {}).find(([, c]: [string, any]) => c.service === service)?.[0] ?? ''
  switch (kind) {
    case 'ask':
      return { id: uniqueId(draft, 'read'), kind, name: 'New Ask step', model: 'claude-sonnet-5', takes: {}, instructions: '', task: '',
        returns: { result: { type: 'text' } } }
    case 'built-in':
      return { id: uniqueId(draft, 'look_up'), kind, name: 'New Built-in step', operation: { lookup: { sheet: '', column: '', as: 'row' } },
        takes: { any_of: [] } }
    case 'approve':
      return { id: uniqueId(draft, 'approve'), kind, name: 'Approve', approver: 'you', notify: ['email', 'web'], timeout_hours: 24,
        review: '## Go ahead?\n', choices: [{ label: 'Do nothing', passes: 'none' }, { label: 'Go ahead', passes: 'all' }] }
    case 'act':
      return { id: uniqueId(draft, 'act'), kind, name: 'New Act step', uses: { connection: firstConn('google-sheets'), actions: ['append_row'], sheets: [] },
        add_row: { sheet: '', row: {} }, follows_dry_run: 'run.dry_run' }
    case 'branch':
      return { id: uniqueId(draft, 'branch'), kind, name: 'New Branch', paths: [{ name: 'First path', when: 'true', then: 'next' }, { name: 'Otherwise', then: 'next' }] }
    case 'free-form':
      return { id: uniqueId(draft, 'find'), kind, name: 'New Free-form block', goal: '', planning_model: 'claude-opus-5',
        limits: { ask_runs: 6, turns: 12 }, steps: [], before_finishing: [], returns: {} }
  }
  throw new Error(kind)
}

export const KIND_LABEL: Record<string, string> = {
  ask: 'Ask', 'built-in': 'Built-in', approve: 'Approve', act: 'Act', branch: 'Branch', 'free-form': 'Free-form',
}

export const TYPES = ['text', 'number', 'yes/no', 'choice', 'date & time with time zone', 'list of text']
