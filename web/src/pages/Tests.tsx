// Tests: an agent's saved test cases, run against the current draft before publishing.
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, when, type Expectation, type TestCase } from '../api'
import { Icon, Pill } from '../ui'
import { ExpectationsEditor } from './SaveAsTest'

export function AgentTabs({ name, tab }: { name: string; tab: 'Design' | 'Runs' | 'Tests' | 'Memory' }) {
  return (
    <nav className="row" style={{ gap: 4, marginLeft: 12 }}>
      {(['Design', 'Runs', 'Tests', 'Memory'] as const).map((t) => t === tab
        ? <span key={t} className="btn small" style={{ border: 'none', background: 'var(--acc-soft)' }}>{t}</span>
        : <Link key={t} to={t === 'Design' ? `/agents/${name}` : `/agents/${name}/${t.toLowerCase()}`} className="btn small" style={{ border: 'none' }}>{t}</Link>)}
    </nav>
  )
}

export function testSummary(tests: TestCase[]): { text: string; tone: 'ok' | 'warn' | 'bad' | 'none' } {
  if (!tests.length) return { text: 'No tests yet. Save a run as a test to check changes before publishing.', tone: 'none' }
  const ran = tests.filter((t) => t.last && !t.last.stale && t.last.result)
  const failed = ran.filter((t) => !t.last!.result!.passed)
  if (failed.length) return { text: `${failed.length} of ${tests.length} tests fail on this draft: ${failed.map((t) => t.name).join(', ')}.`, tone: 'bad' }
  if (ran.length < tests.length) return { text: `${tests.length - ran.length} of ${tests.length} tests haven't run on this draft yet.`, tone: 'warn' }
  return { text: `All ${tests.length} tests pass on this draft.`, tone: 'ok' }
}

function TestRow({ agent, t, onChange, onRun, running }: { agent: string; t: TestCase; onChange: (ts: TestCase[]) => void; onRun: () => void; running: boolean }) {
  const [editing, setEditing] = useState(false)
  const [expect, setExpect] = useState<Expectation[]>(t.expect)
  const r = t.last
  const state = running ? 'running' : !r ? 'not run' : r.status === 'running' || r.status === 'waiting' ? 'running'
    : r.stale ? 'out of date' : r.result?.passed ? 'passed' : 'failed'
  const kind: Record<string, string> = { passed: 'succeeded', failed: 'failed', running: 'waiting', 'not run': 'stopped', 'out of date': 'draft' }
  return (
    <div className="card pad" style={{ gap: 8 }}>
      <span className="row" style={{ flexWrap: 'nowrap' }}>
        <strong className="grow" style={{ fontSize: 14 }}>{t.name}</strong>
        {state === 'running' && <span className="spinner" />}<Pill kind={kind[state]}>{state}</Pill>
        <button className="btn small" disabled={running || state === 'running'} onClick={onRun}><Icon name="play" size={11} width={2} />Run</button>
        <button className="btn small" onClick={() => setEditing(!editing)}><Icon name="edit" size={12} />{editing ? 'Close' : 'Edit'}</button>
        <button className="icon-btn" aria-label={`Delete ${t.name}`} onClick={async () => { if (confirm(`Delete the test ${t.name}?`)) onChange(await api.removeTest(agent, t.id)) }}><Icon name="trash" size={12} /></button>
      </span>
      <span className="faint">
        {[t.email_id && `email ${t.email_id}`, Object.entries(t.inputs).map(([k, v]) => `${k}=${v}`).join(', '),
          Object.entries(t.approvals).map(([g, a]) => `${g}: ${a.choice}`).join('; '), t.scripted ? 'scripted answers' : 'Claude API',
          t.source === 'live' ? 'real accounts' : 'sample data'].filter(Boolean).join(' · ')}
        {' · from '}<Link to={`/agents/${agent}/runs/${t.from_run}`}>run {t.from_run}</Link>
        {r && <> · last run <Link to={`/agents/${agent}/runs/${r.run}`}>{when(r.at)}</Link>{r.cost_usd ? ` · $${r.cost_usd.toFixed(2)}` : ''}</>}
      </span>
      {!editing && (
        <div className="stack" style={{ gap: 3 }}>
          {t.expect.map((e, i) => {
            const res = r && !r.stale ? r.result?.results.find((x) => x.rule === e.rule) : undefined
            return (
              <span key={i} className="row" style={{ gap: 8, flexWrap: 'nowrap' }}>
                <Icon name={!res ? 'dot' : res.passed ? 'check' : 'x'} size={13} width={2.2} color={!res ? 'var(--faint)' : res.passed ? '#2E6B47' : 'var(--bad)'} />
                <span className="grow">{e.name} <code className="mono faint">{e.rule}</code></span>
                {res && !res.passed && <span className="field-error">{res.error ?? `was ${JSON.stringify(res.value)}`}</span>}
              </span>
            )
          })}
        </div>
      )}
      {editing && (
        <>
          <ExpectationsEditor value={expect} onChange={setExpect} />
          <span className="row" style={{ justifyContent: 'flex-end' }}>
            <button className="btn primary small" onClick={async () => { onChange(await api.editTest(agent, t.id, { expect: expect.filter((e) => e.rule.trim()) })); setEditing(false) }}>Save expectations</button>
          </span>
        </>
      )}
    </div>
  )
}

export default function Tests() {
  const { name = '' } = useParams()
  const [tests, setTests] = useState<TestCase[] | null>(null)
  const [running, setRunning] = useState<Set<string>>(new Set())
  const [error, setError] = useState<string | null>(null)
  const load = () => api.tests(name).then((ts) => {
    setTests(ts)
    setRunning((cur) => new Set([...cur].filter((id) => { const l = ts.find((t) => t.id === id)?.last; return !l || ['running', 'waiting'].includes(l.status) || l.stale })))
  }).catch((e) => setError(e.message))
  useEffect(() => { load(); const t = setInterval(load, 2500); return () => clearInterval(t) }, [name])
  const run = async (only?: string) => {
    setError(null)
    try { await api.runTests(name, only); setRunning(new Set(only ? [only] : (tests ?? []).map((t) => t.id))); load() }
    catch (e: any) { setError(e.message) }
  }
  const summary = tests ? (() => {
    const ran = tests.filter((t) => t.last && !t.last.stale && t.last.result)
    return `${ran.filter((t) => t.last!.result!.passed).length} of ${tests.length} pass on the current draft`
  })() : ''
  return (
    <div className="stack grow" style={{ gap: 0, minHeight: 0 }}>
      <div className="spread" style={{ padding: '12px 26px', background: '#fff', borderBottom: '1px solid var(--line)' }}>
        <div className="row" style={{ gap: 12 }}>
          <Link to="/" style={{ color: 'var(--soft-text)' }}>Agents</Link><span style={{ color: '#B7B7AC' }}>/</span>
          <span style={{ fontSize: 16, fontWeight: 700 }}>{name}</span>
          <AgentTabs name={name} tab="Tests" />
        </div>
        <button className="btn primary" disabled={!tests?.length} onClick={() => run()}><Icon name="play" size={12} color="#fff" width={2} />Run all tests</button>
      </div>
      <div className="page" style={{ gap: 12 }}>
        <div className="stack" style={{ gap: 4 }}>
          <h2>Tests</h2>
          <span className="muted" style={{ maxWidth: 820 }}>Each test repeats a saved run against the current draft, answering its approvals the same way, then checks what the run produced.
            Save one from any run (Runs → a run → Save as test). {tests?.length ? summary + '.' : ''}</span>
        </div>
        {error && <div className="notice bad"><Icon name="alert" size={15} /><span>{error}</span></div>}
        {tests === null && <span className="spinner" />}
        {tests?.length === 0 && <div className="card pad muted">No tests yet. Open a run you're happy with and click <strong>Save as test</strong>.</div>}
        {tests?.map((t) => <TestRow key={t.id + (t.last?.run ?? '')} agent={name} t={t} onChange={setTests} running={running.has(t.id)} onRun={() => run(t.id)} />)}
      </div>
    </div>
  )
}
