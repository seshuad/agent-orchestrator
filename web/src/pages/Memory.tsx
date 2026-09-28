// Memory: the judgments an agent remembers. A run's decisions wait here (and on the run's page) until a person
// confirms or corrects them; only those are recalled by later runs.
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, when, type LogEntry, type MemoryCase } from '../api'
import { Icon, Pill, Segmented } from '../ui'
import { AgentTabs } from './Tests'

const STATUS: Record<string, [string, string]> = {
  candidate: ['waiting for a person', 'waiting'], confirmed: ['confirmed', 'succeeded'], corrected: ['corrected', 'published'], rejected: ['rejected', 'stopped'],
}

export function CaseCard({ agent, c, onChange, showRun = true }: { agent: string; c: MemoryCase; onChange: (c: MemoryCase | null) => void; showRun?: boolean }) {
  const [mode, setMode] = useState<'none' | 'correct' | 'confirm'>('none')
  const [choice, setChoice] = useState(c.choices.find((x) => x !== c.decision) ?? '')
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const judge = async (verdict: string, extra: { decision?: string; note?: string } = {}) => {
    setError(null)
    try {
      if (verdict === 'forget') { await api.judgeMemory(agent, c.id, { verdict }); onChange(null); return }
      onChange(await api.judgeMemory(agent, c.id, { verdict, ...extra })); setMode('none'); setNote('')
    } catch (e: any) { setError(e.message) }
  }
  const answer = (x: string) => (x === c.decision ? judge('confirm', { note }) : judge('correct', { decision: x, note }))
  const asked = c.status === 'candidate'
  const [label, kind] = STATUS[c.status] ?? [c.status, 'stopped']
  const hesitated = c.runner_up && c.runner_up !== c.decision ? c.runner_up : null
  const order = asked ? [c.decision, ...(hesitated ? [hesitated] : []), ...c.choices.filter((x) => x !== c.decision && x !== hesitated)] : []
  return (
    <div className="card pad" style={{ gap: 6 }}>
      <span className="row" style={{ flexWrap: 'nowrap', gap: 8 }}>
        <Icon name={c.kind === 'branch' ? 'branch' : 'free-form'} size={14} color="var(--flow)" />
        <strong className="grow">{c.step_name}{c.subject ? <span style={{ fontWeight: 500 }}> · {c.subject}</span> : null}: {c.status === 'corrected' ? <><s>{c.decision}</s> → {c.correction?.decision}</> : c.decision}</strong>
        <Pill kind={kind}>{asked ? (c.asked_because === 'sample' ? 'picked to check' : c.asked_because === 'unsure' ? 'it wasn’t sure' : 'to check') : label}</Pill>
      </span>
      {asked && <span className="muted">{c.asked_because === undefined || c.asked_because === null ? 'Recorded before it asked only about the decisions it wasn’t sure of.' : c.asked_because === 'sample'
        ? 'It was sure of this one. A few sure decisions are picked at random for a person to check, so confident mistakes get noticed.'
        : hesitated ? `It wasn’t sure: ${c.decision} or ${hesitated}.` : 'It wasn’t sure, or couldn’t decide.'}</span>}
      {c.reason && <span className="muted">Why: {c.reason}</span>}
      {c.summary && <span className="muted">Investigation: {c.summary}</span>}
      {(c.evidence?.length ?? 0) > 0 && <span className="faint">Evidence: {c.evidence!.join(' · ')}</span>}
      {(c.notes?.length ?? 0) > 0 && <span className="faint">Notes: {c.notes!.join(' · ')}</span>}
      <span className="faint">
        {Object.keys(c.keys ?? {}).length ? Object.entries(c.keys).map(([k, v]) => `${k} = ${typeof v === 'string' ? v : JSON.stringify(v)}`).join(' · ') + ' · ' : ''}
        {showRun && <><Link to={`/agents/${agent}/runs/${c.run}`}>run {c.run}</Link> · </>}{when(c.at)}
        {c.confirmed_by && ` · ${c.status} by ${c.confirmed_by}`}
      </span>
      {c.correction?.note && <span className="muted">Correction: {c.correction.note}</span>}
      {c.confirm_note && <span className="muted">Note: {c.confirm_note}</span>}
      {asked && (
        <div className="stack" style={{ gap: 6, padding: '8px 10px', background: 'var(--soft)', borderRadius: 8 }}>
          <span className="row" style={{ gap: 6 }}><span className="muted">It should be</span>
            {order.map((x) => <button key={x} className="btn small" onClick={() => answer(x)}>{x}{x === c.decision ? ' (its answer)' : ''}</button>)}</span>
          <input className="input" value={note} onChange={(e) => setNote(e.target.value)} aria-label="Note"
            placeholder='Optional: why (future runs see this), e.g. "design proposals asking for feedback aren’t ready"' />
          <span className="row" style={{ gap: 8 }}>
            <span className="faint grow">Your answer is remembered either way: it’s the borderline cases later runs learn most from.</span>
            <button className="link" style={{ color: 'var(--faint)' }} onClick={() => judge('reject')}>Skip</button>
          </span>
        </div>
      )}
      {!asked && mode === 'none' && (
        <span className="row" style={{ gap: 8 }}>
          {c.status !== 'confirmed' && <button className="btn small" onClick={() => setMode('confirm')}><Icon name="check" size={12} width={2} />Confirm instead</button>}
          {c.choices.length > 1 && <button className="btn small" onClick={() => setMode('correct')}><Icon name="edit" size={12} />{c.status === 'corrected' ? 'Correct again' : 'Correct it'}</button>}
          <button className="link" style={{ color: 'var(--faint)', marginLeft: 'auto' }} onClick={() => judge('forget')}>Forget</button>
        </span>
      )}
      {!asked && mode !== 'none' && (
        <div className="stack" style={{ gap: 6, padding: '8px 10px', background: 'var(--soft)', borderRadius: 8 }}>
          {mode === 'correct' && (
            <span className="row"><span className="muted">It should have been</span>
              <Segmented options={c.choices.filter((x) => x !== c.decision)} value={choice} onChange={setChoice} /></span>
          )}
          <input className="input" value={note} onChange={(e) => setNote(e.target.value)} aria-label="Note"
            placeholder={mode === 'correct' ? 'Why (future runs see this)' : 'Optional: why it was right'} />
          <span className="row" style={{ gap: 8 }}>
            <button className="btn small primary" disabled={mode === 'correct' && !choice} onClick={() => judge(mode, mode === 'correct' ? { decision: choice, note } : { note })}>
              {mode === 'correct' ? 'Save the correction' : 'Confirm'}</button>
            <button className="btn small" onClick={() => setMode('none')}>Cancel</button>
          </span>
        </div>
      )}
      {error && <span className="field-error">{error}</span>}
    </div>
  )
}

/** On a run's log: correct any judgment, asked about or not. */
export function CorrectRow({ runId, e, cases, onSaved }: { runId: string; e: LogEntry; cases: MemoryCase[]; onSaved: (c: MemoryCase) => void }) {
  const [open, setOpen] = useState(false)
  const [choice, setChoice] = useState('')
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const c = cases.find((x) => x.ref === e.ref)
  if (!e.ref || !e.choices?.length || !e.decision) return null
  const doubt = e.confidence === 'unsure' ? `Not sure${e.runner_up ? `: or ${e.runner_up}` : ''}` : e.confidence === 'leaning' ? `Leaning${e.runner_up ? `; else ${e.runner_up}` : ''}` : null
  const save = async () => {
    setError(null)
    try { onSaved(await api.correctJudgment(runId, { ref: e.ref!, decision: choice, note })); setOpen(false) } catch (err: any) { setError(err.message) }
  }
  return (
    <span className="stack" style={{ gap: 4, marginTop: 4 }} onClick={(ev) => ev.stopPropagation()}>
      <span className="row" style={{ gap: 8 }}>
        {doubt && <Pill kind="waiting">{doubt}</Pill>}
        {c?.status === 'corrected' ? <span className="faint">Corrected to <strong>{c.correction?.decision}</strong>{c.correction?.note ? `: ${c.correction.note}` : ''}</span>
          : c?.status === 'confirmed' ? <span className="faint">Confirmed</span>
          : c?.status === 'candidate' ? <span className="faint">Asked about, above</span>
          : !open && <button className="link" style={{ fontSize: 12 }} onClick={() => { setOpen(true); setChoice(e.choices!.find((x) => x !== e.decision) ?? '') }}>Wrong? Correct it</button>}
      </span>
      {open && (
        <span className="stack" style={{ gap: 6, padding: '8px 10px', background: 'var(--soft)', borderRadius: 8 }}>
          <span className="row"><span className="muted">It should have been</span>
            <Segmented options={e.choices.filter((x) => x !== e.decision)} value={choice} onChange={setChoice} /></span>
          <input className="input" value={note} onChange={(ev) => setNote(ev.target.value)} aria-label="Why" placeholder="Why (future runs see this)" />
          <span className="row" style={{ gap: 8 }}>
            <button className="btn small primary" disabled={!choice} onClick={save}>Save the correction</button>
            <button className="btn small" onClick={() => setOpen(false)}>Cancel</button>
          </span>
          {error && <span className="field-error">{error}</span>}
        </span>
      )}
    </span>
  )
}

export default function Memory() {
  const { name = '' } = useParams()
  const [cases, setCases] = useState<MemoryCase[] | null>(null)
  const [filter, setFilter] = useState('Asked')
  useEffect(() => { api.memory(name).then(setCases) }, [name])
  const groups: Record<string, (c: MemoryCase) => boolean> = {
    Asked: (c) => c.status === 'candidate', Remembered: (c) => c.status === 'confirmed' || c.status === 'corrected',
    Skipped: (c) => c.status === 'rejected', All: () => true,
  }
  const shown = (cases ?? []).filter(groups[filter])
  const update = (id: string, next: MemoryCase | null) => setCases((cases ?? []).flatMap((c) => (c.id === id ? (next ? [next] : []) : [c])))
  return (
    <div className="stack grow" style={{ gap: 0, minHeight: 0 }}>
      <div className="spread" style={{ padding: '12px 26px', background: '#fff', borderBottom: '1px solid var(--line)' }}>
        <div className="row" style={{ gap: 12 }}>
          <Link to="/" style={{ color: 'var(--soft-text)' }}>Agents</Link><span style={{ color: '#B7B7AC' }}>/</span>
          <span style={{ fontSize: 16, fontWeight: 700 }}>{name}</span>
          <AgentTabs name={name} tab="Memory" />
        </div>
      </div>
      <div className="page" style={{ gap: 12 }}>
        <div className="stack" style={{ gap: 4 }}>
          <h2>Memory</h2>
          <span className="muted" style={{ maxWidth: 860 }}>What this agent learns from. It asks about the decisions it wasn’t sure of, and a few picked at random;
            any other decision can be corrected from its run’s log. Answers and corrections are shown to later runs as past cases, most similar first; nothing else is.</span>
        </div>
        <Segmented options={Object.keys(groups)} value={filter} onChange={setFilter}
          labels={Object.fromEntries(Object.entries(groups).map(([k, f]) => [k, `${k} ${(cases ?? []).filter(f).length}`]))} />
        {cases === null && <span className="spinner" />}
        {cases !== null && shown.length === 0 && (
          <div className="card pad muted">{cases.length === 0
            ? 'Nothing remembered yet. Turn on Memory in a model-decided Branch or a Free-form block, then run the agent.'
            : 'Nothing here.'}</div>
        )}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(460px, 1fr))', gap: 12 }}>
          {shown.map((c) => <CaseCard key={c.id} agent={name} c={c} onChange={(n) => update(c.id, n)} />)}
        </div>
      </div>
    </div>
  )
}
