// The flow graph: what the agent can do, its trigger, and its steps in order. Click anything to edit it.
import type { Json } from '../api'
import { Icon, KIND_ICON, Pill } from '../ui'
import { useEditor } from './Editor'
import FreeFormGraph from './FreeFormGraph'
import { KIND_LABEL, MODEL_LABEL, pathStr, type Path } from './model'
import { triggerText } from './panels/Settings'

function caption(s: Json): string {
  switch (s.kind) {
    case 'ask': return `${(MODEL_LABEL[s.model] ?? s.model ?? '').split(' ·')[0].replace('Claude ', '')}${s.uses ? ` · ${s.uses.actions?.join(', ')}` : ''}`
    case 'built-in': return Object.keys(s.operation ?? {})[0] ?? ''
    case 'approve': return `${s.approver}, by ${(s.notify ?? []).join(' or ')}`
    case 'act': return s.create_events ? 'create events' : s.add_row ? `add a row to ${s.add_row.sheet || '…'}` : ''
    case 'parallel': return s.for_each ? `for each ${s.for_each.as ?? 'item'} in ${s.for_each.over || '…'}, ${s.for_each.at_once ?? 5} at a time` : `${(s.steps ?? []).length} steps at the same time`
    case 'branch': return s.decide === 'model' ? `${(s.paths ?? []).length} paths · decided by ${String(s.model ?? 'claude-sonnet-5').replace('claude-', '')}${s.for_each ? ` for each ${s.for_each.as ?? 'item'}` : ''}${s.memory ? ' · memory' : ''}` : `${(s.paths ?? []).length} paths`
    default: return ''
  }
}

function access(draft: Json): string {
  // One plain sentence per kind of access, e.g. "read email from 25 senders".
  const reads: string[] = [], writes: string[] = []
  let senders = 0
  const names: Record<string, string> = {}
  const index = (steps: Json[] = []) => steps.forEach((s) => { names[s.id] = s.name; index(s.steps) })
  index(draft.steps)
  const nameOf = (id: string) => names[id] ?? id
  const walk = (steps: Json[] = []) => steps.forEach((s) => {
    const u = s.uses
    if (u) {
      const service = draft.connections?.[u.connection]?.service
      if (service === 'gmail' && (u.actions ?? []).includes('send')) {
        writes.push(`send email to ${(u.recipients ?? []).join(', ') || 'no one yet'}`)
      } else if (service === 'gmail') {
        if (u.senders) senders += u.senders.length
        else if (u.only_message) reads.push('read the email that started the run')
        else if (u.from_domain) reads.push('read earlier emails from the same sender')
        else if (u.only_cited_by) reads.push(`re-open emails that ${nameOf(u.only_cited_by)} cited`)
        else reads.push('read email')
      } else if (service === 'google-sheets') {
        (u.actions.includes('append_row') ? writes : reads).push(`${u.actions.includes('append_row') ? 'add rows to' : 'read'} ${(u.sheets ?? []).join(', ') || 'a sheet'}`)
      } else if (service === 'google-calendar') writes.push(`create events on ${u.calendar ?? 'a calendar'}`)
      else if (service === 'github') reads.push(`read GitHub ${(u.repos ?? []).join(', ') || '(no repositories yet)'}`)
      else if (service === 'bigquery') (s.kind === 'act' ? writes : reads).push(s.kind === 'act' ? `insert rows into ${(u.tables ?? []).join(', ') || 'a table'}` : `query ${(u.datasets ?? []).join(', ') || 'BigQuery'}`)
      else if (service === 'mcp') (s.kind === 'act' ? writes : reads).push(`${s.kind === 'act' ? 'call' : 'use'} ${(u.actions ?? []).join(', ') || 'no tool yet'} on ${u.connection}`)
    }
    walk(s.steps)
  })
  walk(draft.steps)
  if (senders) reads.unshift(`read email from ${senders} senders`)
  const approved = (draft.steps ?? []).some((s: Json) => s.kind === 'approve')
  const parts = [...reads, ...writes.map((w) => (approved ? `${w} after approval` : w))]
  const unique = [...new Set(parts)]
  return unique.length ? `${unique.join('; ')}. Up to $${Number(draft.limits?.budget_usd ?? 0).toFixed(2)} per run.` : 'Nothing yet: no step uses a connection.'
}

function Arrow({ label }: { label?: string }) {
  return <div className="row" style={{ gap: 6, flexWrap: 'nowrap' }}><Icon name="down" size={15} color="#B7B7AC" width={2} />{label && <span className="arrow-label">{label}</span>}</div>
}

export default function Flow() {
  const { draft, feedback, selection, select } = useEditor()
  const sel = (p: Path) => selection.type === 'step' && pathStr(selection.path) === pathStr(p)
  const bad = (p: Path) => feedback.errors.some((e) => e.path === pathStr(p) || e.path.startsWith(pathStr(p) + '.'))
  const steps: Json[] = draft.steps ?? []

  const node = (s: Json, p: Path) => (
    <button type="button" className={`node${sel(p) ? ' sel' : ''}${bad(p) ? ' err' : ''}`} onClick={() => select({ type: 'step', path: p })}>
      <span className="t"><Icon name={KIND_ICON[s.kind]} size={16} color={sel(p) ? 'var(--acc)' : 'var(--faint)'} />{s.name}</span>
      <span className="c"><Pill kind={s.kind}>{KIND_LABEL[s.kind]}</Pill>{caption(s)}</span>
    </button>
  )

  // A Branch's paths, one line per place they lead: where to, then the paths (or how many). Hover for every name.
  const paths = (s: Json, among: Json[], inBlock: boolean) => {
    const groups = new Map<string, string[]>()
    for (const path of s.paths ?? []) groups.set(path.then ?? 'next', [...(groups.get(path.then ?? 'next') ?? []), path.name])
    const to = (t: string) => t === 'end' ? (inBlock ? 'End (this item)' : 'End run') : t === 'next' ? 'Next step' : among.find((x) => x.id === t)?.name ?? t
    return (
      <div className="stack" style={{ gap: 4, alignItems: 'flex-start' }}>
        {[...groups].map(([t, names]) => (
          <span key={t} className="row" style={{ gap: 6, flexWrap: 'nowrap' }} title={names.join('\n')}>
            <span style={{ width: 14, borderTop: '1.5px dashed #B7B7AC' }} />
            <span className="chip" style={{ whiteSpace: 'nowrap' }}><Icon name={t === 'end' ? 'stop' : 'down'} size={11} />{to(t)}</span>
            <span className="faint" style={{ whiteSpace: 'nowrap', maxWidth: 190, overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {names.length > 2 ? `${names.length} paths` : names.join(', ')}</span>
          </span>
        ))}
      </div>
    )
  }

  const parallel = (s: Json, i: number, p: Path) => {
    const inner: Json[] = s.steps ?? []
    const each = s.for_each
    return (
      <div className={`group${sel(p) ? ' sel' : ''}`} onClick={() => select({ type: 'step', path: p })} style={{ cursor: 'pointer', borderColor: bad(p) ? '#D98A73' : undefined }}>
        <span className="row" style={{ gap: 6 }}><Icon name="parallel" size={14} color="var(--acc)" /><strong>{s.name}</strong><Pill kind="parallel">Parallel</Pill></span>
        <span className="chip"><Icon name="parallel" size={13} color="var(--flow)" /><strong style={{ color: 'var(--ink)' }}>{each
          ? `For each ${each.as ?? 'item'} in ${each.over || '…'}`
          : 'All at the same time'}</strong>{each ? ` · ${each.at_once ?? 5} at a time` : ''}{(s.failure ?? 'stop') === 'continue' ? ' · keeps going if one fails' : ''}</span>
        {inner.length === 0 && <span className="muted">No steps yet: add them from the list on the left.</span>}
        {each ? (
          <div className="stack" style={{ gap: 6, alignItems: 'center' }} onClick={(e) => e.stopPropagation()}>
            {inner.map((x, j) => (
              <div key={x.id + j} className="stack" style={{ gap: 6, alignItems: 'center' }}>
                {j > 0 && <Arrow />}
                <div className="row" style={{ gap: 8, flexWrap: 'nowrap', alignItems: 'center' }}>
                  {node(x, ['steps', i, 'steps', j])}
                  {x.kind === 'branch' && paths(x, inner, true)}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="row" style={{ gap: 8, alignItems: 'stretch', justifyContent: 'center' }} onClick={(e) => e.stopPropagation()}>
            {inner.map((x, j) => <div key={x.id + j} style={{ width: 200 }}>{node(x, ['steps', i, 'steps', j])}</div>)}
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="canvas" onClick={(e) => { if (e.target === e.currentTarget) select({ type: 'settings' }) }}>
      <div className="card" style={{ width: '100%', padding: '11px 14px', display: 'flex', gap: 10, alignItems: 'flex-start' }}>
        <Icon name="lock" size={16} color="var(--muted)" />
        <span style={{ fontSize: 12.5, lineHeight: 1.5, color: 'var(--muted)' }}><strong style={{ color: 'var(--ink)' }}>What this agent can do:</strong> {access(draft)}</span>
      </div>
      <div style={{ height: 4 }} />
      <button type="button" className={`node${selection.type === 'settings' ? ' sel' : ''}`} style={{ width: 'auto', flexDirection: 'row', padding: '7px 14px' }} onClick={() => select({ type: 'settings' })}>
        <Icon name={draft.trigger?.kind === 'email' ? 'mail' : 'clock'} size={14} color="var(--faint)" /><strong style={{ fontSize: 11.5 }}>{triggerText(draft.trigger)}</strong>
      </button>
      {steps.map((s, i) => {
        const p: Path = ['steps', i]
        const prev = steps[i - 1]
        const label = prev?.kind === 'free-form' ? Object.keys(prev.returns ?? {}).join(', ') : prev?.kind === 'branch' ? 'otherwise' : prev?.kind === 'parallel' && prev.for_each ? 'results' : undefined
        return (
          <div key={s.id + i} className="stack" style={{ alignItems: 'center', gap: 7 }}>
            <Arrow label={label} />
            {s.kind === 'free-form' ? (
              <div className={`group${sel(p) ? ' sel' : ''}`} onClick={() => select({ type: 'step', path: p })} style={{ cursor: 'pointer', borderColor: bad(p) ? '#D98A73' : undefined }}>
                <span className="row" style={{ gap: 6 }}><Icon name="free-form" size={14} color="var(--acc)" /><strong>{s.name}</strong><Pill kind="free-form">Free-form</Pill></span>
                <span className="chip"><Icon name="spark" size={13} color="var(--flow)" /><strong style={{ color: 'var(--ink)' }}>{(MODEL_LABEL[s.planning_model] ?? s.planning_model).split(' ·')[0].replace('Claude ', '')} plans the next step</strong> · up to {s.limits?.ask_runs} Ask step runs</span>
                {feedback.graphs[s.id]
                  ? <FreeFormGraph graph={feedback.graphs[s.id]} selected={selection.type === 'step' && selection.path.length === 4 && selection.path[1] === i ? (s.steps?.[selection.path[3] as number]?.id ?? null) : null}
                      onSelect={(id) => select({ type: 'step', path: ['steps', i, 'steps', (s.steps ?? []).findIndex((x: Json) => x.id === id)] })} />
                  : <span className="muted">Fix the errors to see this block's graph.</span>}
                <span className="row faint" style={{ gap: 14 }}>
                  <span className="row" style={{ gap: 5 }}><span style={{ width: 22, borderTop: '1.3px solid #B7B7AC' }} />waits for</span>
                  <span className="row" style={{ gap: 5 }}><span style={{ width: 22, borderTop: '1.6px dashed #C0527D' }} />planner can loop back</span>
                </span>
              </div>
            ) : s.kind === 'parallel' ? parallel(s, i, p) : s.kind === 'branch' ? (
              <div style={{ position: 'relative' }}>
                {node(s, p)}
                <div style={{ position: 'absolute', left: '100%', top: '50%', transform: 'translateY(-50%)', paddingLeft: 6 }}>{paths(s, steps, false)}</div>
              </div>
            ) : node(s, p)}
          </div>
        )
      })}
      {steps.length > 0 && <span className="faint" style={{ marginTop: 4 }}>
        {steps.some((s) => s.kind === 'act') ? 'Only Act steps change anything outside the agent' : 'This agent changes nothing outside itself'}</span>}
    </div>
  )
}
