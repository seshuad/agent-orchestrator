// Pieces every panel uses: the step being edited, its header, the fields editor, the inputs editor.
import type { ReactNode } from 'react'
import type { Json } from '../../api'
import { Block, Check, Chips, FieldErrors, Icon, KIND_ICON, NameInput, Pill, RefPicker, Select, Text } from '../../ui'
import { useEditor } from '../Editor'
import { FLOW_KINDS, KIND_LABEL, STEP_KINDS, TYPES, getIn, pathStr, type Path } from '../model'

export function useStep() {
  const ed = useEditor()
  const path = ed.selection.type === 'step' ? ed.selection.path : []
  const step: Json = getIn(ed.draft, path) ?? {}
  const set = (sub: Path, value: unknown) => ed.update([...path, ...sub], value)
  const p = (...sub: Path) => pathStr([...path, ...sub])     // error path for a field
  const inside = path.length === 4
  const block: Json | null = inside ? getIn(ed.draft, path.slice(0, 2)) : null
  return { ...ed, path, step, set, p, inside, block }
}

const MEANING: Record<string, string> = Object.fromEntries([...STEP_KINDS, ...FLOW_KINDS].map((k) => [k.kind, k.text]))

export function StepHeader({ note }: { note?: ReactNode }) {
  const { step, set, path, remove, update, draft, select, p } = useStep()
  const parent: Json[] = getIn(draft, path.slice(0, -1)) ?? []
  const i = Number(path[path.length - 1])
  const move = (to: number) => {
    const list = [...parent]
    const [x] = list.splice(i, 1)
    list.splice(to, 0, x)
    update(path.slice(0, -1), list)
    select({ type: 'step', path: [...path.slice(0, -1), to] })
  }
  const flow = step.kind === 'branch' || step.kind === 'free-form'
  return (
    <>
      <div className="stack" style={{ gap: 2 }}>
        <span className="spread">
          <span className="eyebrow">{flow ? 'Configure flow block' : 'Configure step'}</span>
          <span className="row" style={{ gap: 4 }}>
            <button className="icon-btn" aria-label="Move up" disabled={i === 0} onClick={() => move(i - 1)}><Icon name="up" size={13} /></button>
            <button className="icon-btn" aria-label="Move down" disabled={i >= parent.length - 1} onClick={() => move(i + 1)}><Icon name="down" size={13} /></button>
            <button className="icon-btn" aria-label="Delete" onClick={() => { if (confirm(`Delete ${step.name}?`)) { remove(path); select({ type: 'settings' }) } }}><Icon name="trash" size={13} /></button>
          </span>
        </span>
        <input className="title-input" aria-label="Name" value={step.name ?? ''} onChange={(e) => set(['name'], e.target.value)} />
        <span className="faint">id <code className="mono">{step.id}</code> · other steps refer to it by this</span>
        <FieldErrors path={p('name')} exact /><FieldErrors path={p('id')} exact /><FieldErrors path={p()} exact />
      </div>
      <div className="notice" style={{ background: flow ? '#FDF5F8' : 'var(--soft)', border: `1px solid ${flow ? '#F0D5DF' : 'var(--line)'}`, flexDirection: 'column', gap: 4 }}>
        <span className="row" style={{ gap: 7 }}><Icon name={KIND_ICON[step.kind]} size={15} color={flow ? 'var(--flow)' : 'var(--muted)'} /><Pill kind={step.kind}>{KIND_LABEL[step.kind]}</Pill><strong style={{ fontSize: 12.5 }}>{MEANING[step.kind]}</strong></span>
        {note && <span className="muted">{note}</span>}
      </div>
    </>
  )
}

/** Record fields, an Ask step's outputs, a planner's extra outputs: name, type, optional, hint. */
export function FieldsEditor({ fields, path, onChange, choiceOf = true }: { fields: Json; path: Path; onChange: (f: Json) => void; choiceOf?: boolean }) {
  const { draft } = useEditor()
  const records = Object.keys(draft.records ?? {})
  const types = [...TYPES, ...records, ...records.map((r) => `list of ${r}`)]
  const entries = Object.entries(fields ?? {})
  const rename = (from: string, to: string) => onChange(Object.fromEntries(entries.map(([k, v]) => [k === from ? to : k, v])))
  const setField = (k: string, key: string, v: unknown) => onChange({ ...fields, [k]: { ...(fields[k] as Json), [key]: v === '' ? undefined : v } })
  return (
    <div className="stack" style={{ gap: 6 }}>
      {entries.map(([k, f]: [string, any], i) => (
        <div key={i} className="op">
          <span className="row" style={{ gap: 6, flexWrap: 'nowrap' }}>
            <NameInput value={k} taken={entries.map(([x]) => x)} onRename={(v) => rename(k, v)} label="Field name" normalize={(v) => v.replace(/\s+/g, '_')} />
            <Select value={f.type} options={types} onChange={(v) => setField(k, 'type', v)} label="Type" />
            <Check checked={!!f.optional} onChange={(v) => setField(k, 'optional', v || undefined)}>optional</Check>
            <button className="icon-btn" aria-label={`Remove ${k}`} onClick={() => { const c = { ...fields }; delete c[k]; onChange(c) }}><Icon name="x" size={12} /></button>
          </span>
          {f.type === 'choice' && choiceOf && <Chips values={f.of ?? []} onChange={(v) => setField(k, 'of', v)} placeholder="Add a choice" />}
          <input className="input full" placeholder="Hint for the model (optional)" value={f.hint ?? ''} aria-label="Hint" onChange={(e) => setField(k, 'hint', e.target.value)} />
          <FieldErrors path={pathStr([...path, k])} />
        </div>
      ))}
      <button className="link" onClick={() => { let n = entries.length + 1; while (fields?.[`field_${n}`]) n++; onChange({ ...fields, [`field_${n}`]: { type: 'text' } }) }}>
        <Icon name="plus" size={13} width={2} />Add field</button>
    </div>
  )
}

/** A step's inputs: each is a picked reference; a list means "any of these"; `?` makes it optional. */
export function TakesEditor({ fixed, allowAdd = true }: { fixed?: string[]; allowAdd?: boolean }) {
  const { step, set, refs, p } = useStep()
  const takes: Json = step.takes ?? {}
  const names = fixed ?? Object.keys(takes)
  const setTake = (k: string, v: unknown) => set(['takes'], { ...takes, [k]: v })
  return (
    <div className="stack" style={{ gap: 8 }}>
      {names.map((k) => {
        const v = takes[k] ?? ''
        const list = Array.isArray(v)
        return (
          <div key={k} className="stack" style={{ gap: 4 }}>
            <span className="row" style={{ gap: 6 }}>
              <code className="mono" style={{ fontWeight: 600 }}>{k}</code>
              {list && <span className="faint">any of these</span>}
              {!fixed && <button className="link" style={{ color: 'var(--faint)' }} onClick={() => { const c = { ...takes }; delete c[k]; set(['takes'], c) }}>remove</button>}
            </span>
            {list ? (
              <>
                {(v as string[]).map((r, i) => (
                  <span key={i} className="row" style={{ gap: 4, flexWrap: 'nowrap' }}>
                    <RefPicker value={r} refs={refs} onChange={(nv) => setTake(k, (v as string[]).map((x, j) => (j === i ? nv : x)))} />
                    <button className="icon-btn" aria-label="Remove" onClick={() => setTake(k, (v as string[]).filter((_, j) => j !== i))}><Icon name="x" size={12} /></button>
                  </span>
                ))}
                <button className="link" onClick={() => setTake(k, [...(v as string[]), ''])}><Icon name="plus" size={13} width={2} />Or from…</button>
              </>
            ) : (
              <RefPicker value={v} refs={refs} optional={v.endsWith('?')} onOptional={(o) => setTake(k, v.replace(/\?$/, '') + (o ? '?' : ''))}
                onChange={(nv) => setTake(k, nv)} path={p('takes', k)} />
            )}
            <FieldErrors path={p('takes', k)} />
          </div>
        )
      })}
      {allowAdd && !fixed && (
        <button className="link" onClick={() => { let n = names.length + 1; while (takes[`input_${n}`] !== undefined) n++; setTake(`input_${n}`, '') }}>
          <Icon name="plus" size={13} width={2} />Add input</button>
      )}
    </div>
  )
}

/** Which connection a step uses, which of its actions, and the limits the gateway enforces. */
export function UsesEditor({ actions, limits = [] }: { actions: string[]; limits?: ('senders' | 'lookback_days' | 'only_message' | 'from_domain' | 'only_cited_by' | 'sheets' | 'calendar')[] }) {
  const { step, set, draft, p, block } = useStep()
  const uses: Json | undefined = step.uses
  const conns = Object.keys(draft.connections ?? {})
  if (!uses) {
    return (
      <div className="stack">
        <span className="muted">This step doesn't use a connection.</span>
        {conns.length > 0 && <button className="link" onClick={() => set(['uses'], { connection: conns[0], actions: [actions[0]] })}><Icon name="plus" size={13} width={2} />Use a connection</button>}
        {conns.length === 0 && <span className="faint">Add a connection in the agent's Connections first.</span>}
      </div>
    )
  }
  const setU = (k: string, v: unknown) => set(['uses'], { ...uses, [k]: v === '' || (Array.isArray(v) && !v.length && k !== 'actions') ? undefined : v })
  const stepIds = (block?.steps ?? []).map((s: Json) => s.id).filter((id: string) => id !== step.id)
  return (
    <div className="stack" style={{ gap: 8 }}>
      <span className="row">
        <Select value={uses.connection} options={conns} onChange={(v) => setU('connection', v)} label="Connection" />
        <span className="faint">{draft.connections?.[uses.connection]?.service}</span>
        <button className="link" style={{ color: 'var(--faint)', marginLeft: 'auto' }} onClick={() => set(['uses'], undefined)}>Don't use a connection</button>
      </span>
      {actions.map((a) => <Check key={a} checked={uses.actions?.includes(a)} onChange={(on) => setU('actions', on ? [...(uses.actions ?? []), a] : uses.actions.filter((x: string) => x !== a))}>{a.replace('_', ' ')}</Check>)}
      <FieldErrors path={p('uses')} />
      {limits.length > 0 && (
        <div className="stack" style={{ gap: 7, marginLeft: 22, padding: '8px 10px', background: 'var(--soft)', border: '1px solid var(--line)', borderRadius: 8 }}>
          <span className="faint">Limits, enforced by the service, not by the instructions:</span>
          {limits.includes('senders') && <span className="row"><span className="muted">From</span><Chips values={uses.senders ?? []} onChange={(v) => setU('senders', v)} placeholder="Add a sender domain" /></span>}
          {limits.includes('lookback_days') && <span className="row"><span className="muted">Sent in the last</span><Text width={56} value={uses.lookback_days ?? ''} onChange={(v) => setU('lookback_days', v ? Number(v) : '')} label="Days" /><span className="muted">days</span></span>}
          {limits.includes('only_message') && <Check checked={!!uses.only_message} onChange={(on) => setU('only_message', on ? 'trigger.email_id' : '')}>Only the email that started the run</Check>}
          {limits.includes('from_domain') && <Check checked={!!uses.from_domain} onChange={(on) => setU('from_domain', on ? 'trigger.sender_domain' : '')}>Only emails from the same sender as that email</Check>}
          {limits.includes('only_cited_by') && (
            <span className="row"><span className="muted">Only emails named in the output of</span>
              <Select value={uses.only_cited_by ?? ''} options={['', ...stepIds]} onChange={(v) => setU('only_cited_by', v)} label="Step" labels={{ '': 'any step (no limit)' }} /></span>
          )}
          {limits.includes('sheets') && <span className="row"><span className="muted">Sheets</span><Chips values={uses.sheets ?? []} onChange={(v) => setU('sheets', v)} placeholder="Add a sheet" /></span>}
          {limits.includes('calendar') && <span className="row"><span className="muted">Calendar</span><Text value={uses.calendar ?? ''} onChange={(v) => setU('calendar', v)} label="Calendar" /></span>}
        </div>
      )}
    </div>
  )
}

export function Section({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return <Block title={title} aside={aside}>{children}</Block>
}
