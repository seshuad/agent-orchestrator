// The step list: settings, connections, steps (Free-form blocks hold their own), record types.
import { useState } from 'react'
import type { Json } from '../api'
import { Icon, KIND_ICON, Pill } from '../ui'
import { useEditor } from './Editor'
import { FLOW_KINDS, KIND_LABEL, STEP_KINDS, newStep, pathStr, type Path } from './model'

export function AddMenu({ inside, onPick, onClose, style }: { inside?: boolean; onPick: (kind: string) => void; onClose: () => void; style?: React.CSSProperties }) {
  const item = (k: { kind: string; name: string; text: string }) => (
    <button key={k.kind} type="button" onClick={() => { onPick(k.kind); onClose() }}>
      <span style={{ paddingTop: 1 }}><Icon name={KIND_ICON[k.kind]} size={16} color="var(--muted)" /></span>
      <span className="stack" style={{ gap: 2 }}><strong className={`pill ${k.kind}`} style={{ alignSelf: 'flex-start', fontSize: 12.5 }}>{k.name}</strong><span className="muted">{k.text}</span></span>
    </button>
  )
  return (
    <>
      <div style={{ position: 'fixed', inset: 0, zIndex: 14 }} onClick={onClose} />
      <div className="menu" role="menu" style={style}>
        <span className="eyebrow" style={{ padding: '6px 10px 2px' }}>Steps</span>
        {STEP_KINDS.filter((k) => !inside || k.kind === 'ask' || k.kind === 'built-in').map(item)}
        {!inside && <><div style={{ margin: '6px 10px', borderTop: '1px solid var(--line-2)' }} /><span className="eyebrow" style={{ padding: '2px 10px' }}>Flow</span>{FLOW_KINDS.map(item)}</>}
        {inside && <span className="faint" style={{ padding: '4px 10px' }}>Approve and Act steps run after the block, in a fixed order.</span>}
      </div>
    </>
  )
}

function Row({ label, kind, icon, selected, onClick, pill, errPath }: {
  label: string; kind: string; icon: string; selected: boolean; onClick: () => void; pill?: string; errPath?: string
}) {
  const { feedback } = useEditor()
  const bad = errPath !== undefined && feedback.errors.some((e) => e.path === errPath || e.path.startsWith(errPath + '.'))
  return (
    <button type="button" className={`side-row${selected ? ' sel' : ''}${bad ? ' has-err' : ''}`} onClick={onClick}>
      <span className="label"><Icon name={icon} size={15} color={selected ? 'var(--acc)' : 'var(--faint)'} /><span>{label}</span></span>
      {bad ? <Pill kind="failed">fix</Pill> : <Pill kind={kind}>{pill ?? KIND_LABEL[kind] ?? kind}</Pill>}
    </button>
  )
}

export default function Sidebar() {
  const { draft, selection, select, insert, update } = useEditor()
  const [menu, setMenu] = useState<{ path: Path; index: number; inside: boolean; top: number } | null>(null)
  const isSel = (p: Path) => selection.type === 'step' && pathStr(selection.path) === pathStr(p)
  const steps: Json[] = draft.steps ?? []

  const add = (kind: string) => {
    if (!menu) return
    const step = newStep(kind, draft)
    insert(menu.path, menu.index, step)
    select({ type: 'step', path: [...menu.path, menu.index] })
  }
  const topIndex = selection.type === 'step' ? Number(selection.path[1]) + 1 : steps.length
  const conns = Object.keys(draft.connections ?? {}).length

  return (
    <div className="sidebar">
      <span className="eyebrow" style={{ padding: '0 4px' }}>Agent</span>
      <Row label="Settings" kind="setup" icon="clock" pill={({ schedule: 'schedule', email: 'email', webhook: 'webhook', manual: 'manual' } as Record<string, string>)[draft.trigger?.kind] ?? 'trigger'} selected={selection.type === 'settings'} onClick={() => select({ type: 'settings' })} errPath="trigger" />
      <Row label="Connections" kind="setup" icon="plug" pill={conns ? `${conns} account${conns > 1 ? 's' : ''}` : 'none'} selected={selection.type === 'connections'} onClick={() => select({ type: 'connections' })} errPath="connections" />
      <div className="side-head">
        <span className="eyebrow">Steps</span>
        <button className="icon-btn" aria-label="Add step" onClick={(e) => setMenu({ path: ['steps'], index: Math.min(topIndex, steps.length), inside: false, top: e.currentTarget.getBoundingClientRect().top })}><Icon name="plus" size={14} width={2} /></button>
      </div>
      {steps.length === 0 && <div className="side-group"><strong className="muted">No steps yet</strong><span className="muted">Use + to add the first step.</span></div>}
      {steps.map((s, i) => s.kind === 'free-form' ? (
        <div key={s.id + i} className={`side-group${isSel(['steps', i]) ? ' sel' : ''}`}>
          <Row label={s.name} kind="free-form" icon="free-form" selected={isSel(['steps', i])} onClick={() => select({ type: 'step', path: ['steps', i] })} errPath={`steps.${i}`} />
          {(s.steps ?? []).map((inner: Json, j: number) => (
            <Row key={inner.id + j} label={inner.name} kind={inner.kind} icon={KIND_ICON[inner.kind]} selected={isSel(['steps', i, 'steps', j])}
              onClick={() => select({ type: 'step', path: ['steps', i, 'steps', j] })} errPath={`steps.${i}.steps.${j}`} />
          ))}
          <button className="link" style={{ paddingLeft: 4 }} onClick={(e) => setMenu({ path: ['steps', i, 'steps'], index: (s.steps ?? []).length, inside: true, top: e.currentTarget.getBoundingClientRect().top })}>
            <Icon name="plus" size={13} width={2} />Add a step to this block</button>
        </div>
      ) : (
        <Row key={s.id + i} label={s.name} kind={s.kind} icon={KIND_ICON[s.kind]} selected={isSel(['steps', i])} onClick={() => select({ type: 'step', path: ['steps', i] })} errPath={`steps.${i}`} />
      ))}
      <div className="side-head">
        <span className="eyebrow">Record types</span>
        <button className="icon-btn" aria-label="Add record type" onClick={() => {
          let n = 1; while ((draft.records ?? {})[`Record ${n}`]) n++
          update(['records', `Record ${n}`], { fields: { name: { type: 'text' } } })
          select({ type: 'record', name: `Record ${n}` })
        }}><Icon name="plus" size={14} width={2} /></button>
      </div>
      {Object.keys(draft.records ?? {}).length === 0 && <span className="muted" style={{ padding: '0 4px' }}>The shapes of the data your steps pass along.</span>}
      {Object.keys(draft.records ?? {}).map((r) => (
        <Row key={r} label={r} kind="record" icon="record" pill={`${Object.keys(draft.records[r].fields ?? {}).length} fields`}
          selected={selection.type === 'record' && selection.name === r} onClick={() => select({ type: 'record', name: r })} errPath={`records.${r}`} />
      ))}
      {menu && <AddMenu inside={menu.inside} onPick={add} onClose={() => setMenu(null)} style={{ left: 240, top: Math.min(menu.top, window.innerHeight - 420) }} />}
    </div>
  )
}
