// Save a finished run as a test case: the same inputs and approvals, and what the run should produce, as CEL rules.
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type Expectation, type RunDetail } from '../api'
import { Dialog, Icon } from '../ui'

export function ExpectationsEditor({ value, onChange }: { value: Expectation[]; onChange: (v: Expectation[]) => void }) {
  const set = (i: number, patch: Partial<Expectation>) => onChange(value.map((e, j) => (j === i ? { ...e, ...patch } : e)))
  return (
    <div className="stack" style={{ gap: 6 }}>
      {value.map((e, i) => (
        <div key={i} className="row" style={{ flexWrap: 'nowrap', gap: 6 }}>
          <input className="input" style={{ width: 230 }} value={e.name} onChange={(x) => set(i, { name: x.target.value })} aria-label="What it checks" placeholder="What it checks" />
          <input className="input cel grow" value={e.rule} onChange={(x) => set(i, { rule: x.target.value })} aria-label="Rule" placeholder="steps.<step>.<field> == ..." />
          <button className="icon-btn" aria-label={`Remove ${e.name}`} onClick={() => onChange(value.filter((_, j) => j !== i))}><Icon name="x" size={12} /></button>
        </div>
      ))}
      <button className="link" onClick={() => onChange([...value, { name: '', rule: '' }])}><Icon name="plus" size={13} width={2} />Add an expectation</button>
      <span className="faint">CEL, like any rule: <code className="mono">status</code>, <code className="mono">steps.&lt;step&gt;.&lt;field&gt;</code> (a Free-form block's results under its id), <code className="mono">calls.count</code>, <code className="mono">calls.refused</code>.</span>
    </div>
  )
}

export default function SaveAsTest({ run, onClose }: { run: RunDetail; onClose: () => void }) {
  const navigate = useNavigate()
  const [name, setName] = useState('')
  const [expect, setExpect] = useState<Expectation[] | null>(null)
  const [summary, setSummary] = useState<string>('')
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    api.testSuggestion(run.id).then((s) => {
      setName(s.name); setExpect(s.expect)
      const gates = Object.entries(s.approvals).map(([g, a]) => `${g}: ${a.choice}${a.ids ? ` (${a.ids})` : ''}`)
      setSummary([s.email_id && `email ${s.email_id}`, Object.keys(s.inputs).length && Object.entries(s.inputs).map(([k, v]) => `${k}=${v}`).join(', '),
        gates.length && `approvals answered: ${gates.join('; ')}`, s.scripted ? 'scripted answers' : 'Claude API', s.source === 'live' ? 'real accounts' : 'sample data']
        .filter(Boolean).join(' · '))
    }).catch((e) => setError(e.message))
  }, [run.id])
  const save = async () => {
    setError(null)
    try { await api.addTest(run.agent, run.id, name, (expect ?? []).filter((e) => e.rule.trim())); navigate(`/agents/${run.agent}/tests`) }
    catch (e: any) { setError(e.message) }
  }
  return (
    <Dialog title="Save as a test" sub="Run it again any time against the draft: same inputs, approvals answered the same way, and these expectations checked." onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={!expect || !name.trim()} onClick={save}>Save test</button></>}>
      <label className="stack" style={{ gap: 3 }}><span className="muted">Name</span>
        <input className="input" value={name} onChange={(e) => setName(e.target.value)} aria-label="Test name" /></label>
      <span className="faint">Repeats: {summary || '…'}</span>
      <span className="muted" style={{ fontWeight: 600, marginTop: 4 }}>Expectations, suggested from this run</span>
      {expect === null ? <span className="spinner" /> : <ExpectationsEditor value={expect} onChange={setExpect} />}
      {error && <div className="notice bad"><Icon name="alert" size={15} /><span>{error}</span></div>}
    </Dialog>
  )
}
