// Memory: the judgments an agent remembers. A run's decisions wait here (and on the run's page) until a person
// confirms or corrects them; only those are recalled by later runs.
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, when, type MemoryCase } from '../api'
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
  const [label, kind] = STATUS[c.status] ?? [c.status, 'stopped']
  return (
    <div className="card pad" style={{ gap: 6 }}>
      <span className="row" style={{ flexWrap: 'nowrap', gap: 8 }}>
        <Icon name={c.kind === 'branch' ? 'branch' : 'free-form'} size={14} color="var(--flow)" />
        <strong className="grow">{c.step_name}{c.subject ? <span style={{ fontWeight: 500 }}> · {c.subject}</span> : null}: {c.status === 'corrected' ? <><s>{c.decision}</s> → {c.correction?.decision}</> : c.decision}</strong>
        <Pill kind={kind}>{label}</Pill>
      </span>
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
      {mode === 'none' && (
        <span className="row" style={{ gap: 8 }}>
          {c.status !== 'confirmed' && <button className="btn small" onClick={() => setMode('confirm')}><Icon name="check" size={12} width={2} />{c.status === 'candidate' ? 'Right: remember it' : 'Confirm instead'}</button>}
          {c.choices.length > 1 && <button className="btn small" onClick={() => setMode('correct')}><Icon name="edit" size={12} />{c.status === 'corrected' ? 'Correct again' : 'Wrong: correct it'}</button>}
          {c.status === 'candidate' && <button className="btn small" onClick={() => judge('reject')}>Don't remember</button>}
          <button className="link" style={{ color: 'var(--faint)', marginLeft: 'auto' }} onClick={() => judge('forget')}>Forget</button>
        </span>
      )}
      {mode !== 'none' && (
        <div className="stack" style={{ gap: 6, padding: '8px 10px', background: 'var(--soft)', borderRadius: 8 }}>
          {mode === 'correct' && (
            <span className="row"><span className="muted">It should have been</span>
              <Segmented options={c.choices.filter((x) => x !== c.decision)} value={choice} onChange={setChoice} /></span>
          )}
          <input className="input" value={note} onChange={(e) => setNote(e.target.value)} aria-label="Note"
            placeholder={mode === 'correct' ? 'Why (future runs see this), e.g. "their corporate agency books from this domain"' : 'Optional: why it was right'} />
          <span className="row" style={{ gap: 8 }}>
            <button className="btn small primary" disabled={mode === 'correct' && !choice} onClick={() => judge(mode, mode === 'correct' ? { decision: choice, note } : { note })}>
              {mode === 'correct' ? 'Save the correction' : 'Remember it'}</button>
            <button className="btn small" onClick={() => setMode('none')}>Cancel</button>
          </span>
          <span className="faint">Future runs recall it as a past case: data to weigh, not a rule.</span>
        </div>
      )}
      {error && <span className="field-error">{error}</span>}
    </div>
  )
}

export default function Memory() {
  const { name = '' } = useParams()
  const [cases, setCases] = useState<MemoryCase[] | null>(null)
  const [filter, setFilter] = useState('Waiting')
  useEffect(() => { api.memory(name).then(setCases) }, [name])
  const groups: Record<string, (c: MemoryCase) => boolean> = {
    Waiting: (c) => c.status === 'candidate', Remembered: (c) => c.status === 'confirmed' || c.status === 'corrected',
    Rejected: (c) => c.status === 'rejected', All: () => true,
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
          <span className="muted" style={{ maxWidth: 860 }}>Judgments this agent can learn from: the paths its model-decided Branches chose, and the outcomes of its Free-form blocks.
            Each waits for a person. Confirmed and corrected cases are shown to later runs as past cases, most similar first; nothing else is.</span>
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
