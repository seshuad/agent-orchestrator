// Shared pieces: icons, pills, form controls with field-level errors, pickers, the app bar.
import { createContext, useContext, useEffect, useId, useState, type ReactNode } from 'react'
import { NavLink, Link } from 'react-router-dom'
import type { Problem, Reference, Session } from './api'

const PATHS: Record<string, string> = {
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>',
  layers: '<path d="M12 3l9 5-9 5-9-5 9-5z"/><path d="M3 13l9 5 9-5"/>',
  shield: '<path d="M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6l7-3z"/><path d="M9 12l2 2 4-4"/>',
  person: '<circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18"/><path d="M8 3v4"/><path d="M16 3v4"/>',
  record: '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8"/><path d="M8 12h8"/><path d="M8 16h5"/>',
  plus: '<path d="M12 5v14"/><path d="M5 12h14"/>',
  down: '<path d="M12 5v14"/><path d="M19 12l-7 7-7-7"/>',
  check: '<path d="M5 12l5 5L20 7"/>',
  lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  parallel: '<path d="M12 3v5"/><path d="M12 8L5 14v7"/><path d="M12 8v13"/><path d="M12 8l7 6v7"/>',
  branch: '<path d="M12 3l9 9-9 9-9-9 9-9z"/>',
  stop: '<circle cx="12" cy="12" r="9"/><rect x="9" y="9" width="6" height="6" rx="1"/>',
  plug: '<path d="M9 3v5"/><path d="M15 3v5"/><path d="M6 8h12v3a6 6 0 0 1-12 0V8z"/><path d="M12 17v4"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 0 1 4.9.7c0 1.7-2.4 2.1-2.4 3.8"/><path d="M12 17v.01"/>',
  spark: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3z"/>',
  'free-form': '<circle cx="12" cy="5" r="2"/><circle cx="5" cy="17" r="2"/><circle cx="19" cy="17" r="2"/><path d="M10.5 6.5L6 15"/><path d="M13.5 6.5L18 15"/><path d="M7 17h10"/>',
  template: '<rect x="3" y="3" width="8" height="8" rx="1.5"/><rect x="13" y="3" width="8" height="8" rx="1.5"/><rect x="3" y="13" width="8" height="8" rx="1.5"/><rect x="13" y="13" width="8" height="8" rx="1.5"/>',
  blank: '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/>',
  flag: '<path d="M5 21V4"/><path d="M5 4h11l-2 4 2 4H5"/>',
  filter: '<path d="M4 5h16l-6 7v6l-4 2v-8L4 5z"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
  group: '<rect x="3" y="4" width="18" height="6" rx="1.5"/><rect x="3" y="14" width="18" height="6" rx="1.5"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9"/><path d="M17 6l3 3"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  grip: '<circle cx="9" cy="6" r="1"/><circle cx="15" cy="6" r="1"/><circle cx="9" cy="12" r="1"/><circle cx="15" cy="12" r="1"/><circle cx="9" cy="18" r="1"/><circle cx="15" cy="18" r="1"/>',
  play: '<path d="M7 4.5v15l12-7.5-12-7.5z"/>',
  chev: '<path d="M6 9l6 6 6-6"/>',
  building: '<rect x="4" y="3" width="16" height="18" rx="1.5"/><path d="M9 7h2"/><path d="M13 7h2"/><path d="M9 11h2"/><path d="M13 11h2"/><path d="M10 21v-4h4v4"/>',
  list: '<path d="M9 6h11"/><path d="M9 12h11"/><path d="M9 18h11"/><circle cx="4.5" cy="6" r="1"/><circle cx="4.5" cy="12" r="1"/><circle cx="4.5" cy="18" r="1"/>',
  alert: '<path d="M12 3l10 18H2L12 3z"/><path d="M12 10v5"/><path d="M12 18v.01"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
  edit: '<path d="M4 20h4l11-11-4-4L4 16v4z"/>',
  trash: '<path d="M4 7h16"/><path d="M9 7V4h6v3"/><path d="M6 7l1 13h10l1-13"/>',
  up: '<path d="M12 19V5"/><path d="M5 12l7-7 7 7"/>',
  x: '<path d="M6 6l12 12"/><path d="M18 6L6 18"/>',
  code: '<path d="M8 8l-5 4 5 4"/><path d="M16 8l5 4-5 4"/>',
}

export const KIND_ICON: Record<string, string> = {
  ask: 'mail', 'built-in': 'layers', approve: 'person', act: 'calendar', branch: 'branch', 'free-form': 'free-form', parallel: 'parallel',
}

export function Icon({ name, size = 15, color = 'currentColor', width = 1.8 }: { name: string; size?: number; color?: string; width?: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke={color} strokeWidth={width}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ flex: 'none' }}
      dangerouslySetInnerHTML={{ __html: PATHS[name] ?? '' }} />
  )
}

export function Pill({ kind, children }: { kind: string; children?: ReactNode }) {
  return <span className={`pill ${kind}`}>{children ?? kind}</span>
}

export function Avatar({ initials, size = 28, color }: { initials: string; size?: number; color?: string }) {
  return <span className="avatar" style={{ width: size, height: size, fontSize: Math.round(size * 0.38), background: color }}>{initials}</span>
}

export function Segmented<T extends string>({ options, value, onChange, labels }: {
  options: T[]; value: T; onChange: (v: T) => void; labels?: Record<string, string>
}) {
  return (
    <div className="seg">
      {options.map((o) => (
        <button key={o} type="button" className={o === value ? 'on' : ''} onClick={() => onChange(o)}>{labels?.[o] ?? o}</button>
      ))}
    </div>
  )
}

export function Block({ title, aside, children }: { title: ReactNode; aside?: ReactNode; children: ReactNode }) {
  return (
    <div className="block">
      <div className="head"><span>{title}</span>{aside && <span className="faint">{aside}</span>}</div>
      {children}
    </div>
  )
}

// Errors from the service are pinned to paths like "steps.0.steps.1.uses.actions".
export const ProblemsContext = createContext<Problem[]>([])

export function useProblems(path: string, exact = false): Problem[] {
  const all = useContext(ProblemsContext)
  return all.filter((p) => (exact ? p.path === path : p.path === path || p.path.startsWith(path + '.')))
}

export function FieldErrors({ path, exact }: { path: string; exact?: boolean }) {
  const ps = useProblems(path, exact)
  return <>{ps.map((p, i) => <span key={i} className="field-error">{p.message}</span>)}</>
}

export function Text({ value, onChange, path, placeholder, width, mono, label }: {
  value: string | number | null | undefined; onChange: (v: string) => void; path?: string; placeholder?: string
  width?: number | string; mono?: boolean; label?: string
}) {
  const errs = useProblems(path ?? '__none__', true)
  return (
    <span className={errs.length ? 'has-error stack' : 'stack'} style={{ gap: 3, width: width === '100%' ? '100%' : undefined }}>
      <input aria-label={label ?? placeholder} className={`input${mono ? ' cel' : ''}`} value={value ?? ''} placeholder={placeholder}
        style={{ width: width ?? 180 }} onChange={(e) => onChange(e.target.value)} />
      {errs.map((p, i) => <span key={i} className="field-error">{p.message}</span>)}
    </span>
  )
}

export function Area({ value, onChange, path, rows = 3, mono, label }: {
  value: string | undefined; onChange: (v: string) => void; path?: string; rows?: number; mono?: boolean; label?: string
}) {
  const errs = useProblems(path ?? '__none__', true)
  return (
    <span className={errs.length ? 'has-error stack' : 'stack'} style={{ gap: 3 }}>
      <textarea aria-label={label} className={`textarea${mono ? ' cel' : ''}`} rows={rows} value={value ?? ''} onChange={(e) => onChange(e.target.value)} />
      {errs.map((p, i) => <span key={i} className="field-error">{p.message}</span>)}
    </span>
  )
}

export function Select({ value, options, onChange, label, labels }: {
  value: string; options: string[]; onChange: (v: string) => void; label?: string; labels?: Record<string, string>
}) {
  return (
    <select aria-label={label} className="select" value={value} onChange={(e) => onChange(e.target.value)}>
      {!options.includes(value) && <option value={value}>{labels?.[value] ?? (value || '—')}</option>}
      {options.map((o) => <option key={o} value={o}>{labels?.[o] ?? o}</option>)}
    </select>
  )
}

export function Check({ checked, onChange, children, detail, disabled }: {
  checked: boolean; onChange: (v: boolean) => void; children: ReactNode; detail?: ReactNode; disabled?: boolean
}) {
  const id = useId()
  return (
    <div className="check">
      <input id={id} type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <label htmlFor={id}>{children}{detail && <span className="faint" style={{ display: 'block', marginTop: 1 }}>{detail}</span>}</label>
    </div>
  )
}

/** A name other things are keyed by (a field, a connection). Edited in place and applied when you
 *  leave the box or press Enter, so the row isn't rebuilt on every keystroke; Esc puts it back. */
export function NameInput({ value, taken, onRename, width = 130, label, normalize = (v) => v }: {
  value: string; taken: string[]; onRename: (v: string) => void; width?: number; label: string; normalize?: (v: string) => string
}) {
  const [draft, setDraft] = useState(value)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => setDraft(value), [value])
  const commit = () => {
    const v = normalize(draft.trim())
    if (v === value) { setDraft(value); setError(null); return }
    if (!v) { setDraft(value); setError('A name is needed.'); return }
    if (taken.includes(v)) { setDraft(value); setError(`“${v}” is already used.`); return }
    setError(null)
    onRename(v)
  }
  return (
    <span className={error ? 'has-error stack' : 'stack'} style={{ gap: 3 }}>
      <input className="input cel" style={{ width }} value={draft} aria-label={label} spellCheck={false}
        onChange={(e) => setDraft(e.target.value)} onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') { e.preventDefault(); (e.target as HTMLInputElement).blur() }
          if (e.key === 'Escape') { setDraft(value); setError(null) }
        }} />
      {error && <span className="field-error">{error}</span>}
    </span>
  )
}

/** A CEL rule: typed, checked by the service on every save (errors show under it). */
export function Cel({ value, onChange, path, rows = 1, placeholder }: {
  value: string | undefined; onChange: (v: string) => void; path: string; rows?: number; placeholder?: string
}) {
  const errs = useProblems(path, true)
  return (
    <span className={errs.length ? 'has-error stack' : 'stack'} style={{ gap: 3 }}>
      <textarea aria-label="CEL rule" className="textarea cel" rows={rows} value={value ?? ''} placeholder={placeholder}
        spellCheck={false} onChange={(e) => onChange(e.target.value)} />
      {errs.map((p, i) => <span key={i} className="field-error">{p.message}</span>)}
    </span>
  )
}

/** Pick a reference from what the step can see; typing a reference is allowed too. */
export function RefPicker({ value, refs, onChange, path, optional, onOptional }: {
  value: string; refs: Reference[]; onChange: (v: string) => void; path?: string; optional?: boolean; onOptional?: (v: boolean) => void
}) {
  const list = useId()
  const errs = useProblems(path ?? '__none__', true)
  const bare = value.replace(/\?$/, '')
  const known = refs.find((r) => r.ref === bare)
  return (
    <span className={errs.length ? 'has-error stack' : 'stack'} style={{ gap: 3 }}>
      <span className="row" style={{ gap: 6, flexWrap: 'nowrap' }}>
        <input aria-label="Reference" className="input cel" list={list} value={bare} style={{ width: 230 }}
          onChange={(e) => onChange(e.target.value + (optional ? '?' : ''))} />
        <datalist id={list}>{refs.map((r) => <option key={r.ref} value={r.ref}>{r.label}</option>)}</datalist>
        {onOptional && <Check checked={!!optional} onChange={onOptional}>optional</Check>}
      </span>
      {known && <span className="faint">{known.label} · {known.type}</span>}
      {errs.map((p, i) => <span key={i} className="field-error">{p.message}</span>)}
    </span>
  )
}

export function Chips({ values, onChange, placeholder }: { values: string[]; onChange: (v: string[]) => void; placeholder?: string }) {
  const [draft, setDraft] = useState('')
  const add = () => {
    const parts = draft.split(',').map((s) => s.trim()).filter(Boolean)
    if (parts.length) onChange([...values, ...parts.filter((p) => !values.includes(p))])
    setDraft('')
  }
  return (
    <div className="row" style={{ gap: 5 }}>
      {values.map((v) => (
        <span key={v} className="chip">{v}<button type="button" aria-label={`Remove ${v}`} onClick={() => onChange(values.filter((x) => x !== v))}>×</button></span>
      ))}
      <input className="input" style={{ width: 150 }} value={draft} placeholder={placeholder ?? 'Add…'} aria-label={placeholder ?? 'Add'}
        onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add() } }} onBlur={add} />
    </div>
  )
}

export function Guarantees({ items }: { items: string[] }) {
  return <div className="guarantees">{items.map((t) => <span key={t} className="row" style={{ flexWrap: 'nowrap', alignItems: 'flex-start' }}><Icon name="check" size={14} color="#2E6B47" width={2.2} />{t}</span>)}</div>
}

export function Dialog({ title, sub, children, footer, onClose }: { title: string; sub?: string; children: ReactNode; footer: ReactNode; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="dialog" role="dialog" aria-label={title}>
        <div className="body" style={{ paddingBottom: 0 }}><h2>{title}</h2>{sub && <span className="muted">{sub}</span>}</div>
        <div className="body">{children}</div>
        <div className="foot">{footer}</div>
      </div>
    </div>
  )
}

export const SessionContext = createContext<Session | null>(null)

export function AppBar({ approvals }: { approvals: number }) {
  const session = useContext(SessionContext)
  return (
    <div className="appbar">
      <div className="left">
        <Link to="/" className="brand"><span className="brand-mark"><Icon name="spark" size={15} color="#fff" width={2} /></span>Agent Orchestrator</Link>
        {session && (
          <span className="ws-switch" title="Workspaces you belong to">
            <Icon name="building" size={15} color="var(--muted)" />{session.workspace.name}<Pill kind="stopped">{session.workspace.kind}</Pill>
            <Icon name="chev" size={13} color="var(--faint)" width={2} />
          </span>
        )}
        <nav className="nav">
          <NavLink to="/" end>Agents</NavLink>
          <NavLink to="/runs">Runs</NavLink>
          <NavLink to="/approvals">Approvals{approvals > 0 && <span className="badge">{approvals}</span>}</NavLink>
          <NavLink to="/connections">Connections</NavLink>
        </nav>
      </div>
      {session && (
        <span className="user">
          <Avatar initials={session.user.initials} size={30} />
          <span className="who"><span>{session.user.name}</span><span>{session.user.role} · {session.user.email}</span></span>
        </span>
      )}
    </div>
  )
}

export function Toast({ message, onDone }: { message: string | null; onDone: () => void }) {
  useEffect(() => {
    if (!message) return
    const t = setTimeout(onDone, 3500)
    return () => clearTimeout(t)
  }, [message, onDone])
  return message ? <div className="toast" role="status">{message}</div> : null
}

export function HistoryDots({ states }: { states: string[] }) {
  const colors: Record<string, string> = { succeeded: '#3E9B72', failed: '#C2553A', waiting: '#D69A2D', stopped: '#B7B7AC', running: '#7C9CCB' }
  if (!states.length) return <span className="faint">—</span>
  return <span className="dots" aria-label="Recent runs, oldest first">{states.map((s, i) => <span key={i} title={s} style={{ background: colors[s] ?? '#ccc' }} />)}</span>
}
