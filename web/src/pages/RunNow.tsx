// Run an agent now: pick the version, run options, (for an email trigger) the email, and how model steps run.
import { useContext, useEffect, useState } from 'react'
import { api, type AgentDetail, type Connection, type Run } from '../api'
import { Block, Dialog, Guarantees, Segmented, Select, SessionContext } from '../ui'

export default function RunNow({ agent, draftOnly, onClose, onStarted }: {
  agent: string; draftOnly?: boolean; onClose: () => void; onStarted: (run: Run) => void
}) {
  const session = useContext(SessionContext)
  const [detail, setDetail] = useState<AgentDetail | null>(null)
  const [version, setVersion] = useState<string>('draft')
  const [inputs, setInputs] = useState<Record<string, string>>({})
  const [emails, setEmails] = useState<{ id: string; from: string; subject: string }[]>([])
  const [email, setEmail] = useState('')
  const [scripted, setScripted] = useState(false)
  const [source, setSource] = useState<'sample' | 'live'>('sample')
  const [accounts, setAccounts] = useState<Connection[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.agent(agent).then((d) => {
      setDetail(d)
      const pub = d.meta.published
      setVersion(!draftOnly && pub ? String(pub) : 'draft')
      const opts = d.draft.run_options ?? {}
      setInputs(Object.fromEntries(Object.entries(opts).map(([k, o]: [string, any]) => [k, String(o.default ?? '')])))
      api.connections().then(setAccounts)
      if (!session?.claude_api && d.meta.replay) setScripted(true)
    }).catch((e) => setError(e.message))
  }, [agent, draftOnly, session?.claude_api])

  useEffect(() => {
    if (detail?.draft.trigger?.kind !== 'email') return
    api.emails(agent, source).then((es) => { setEmails(es); setEmail(es[0]?.id ?? '') }).catch((e) => { setEmails([]); setError(e.message) })
  }, [agent, detail, source])

  if (!detail) return <Dialog title={`Run ${agent} now`} onClose={onClose} footer={<button className="btn" onClick={onClose}>Close</button>}>{error ?? 'Loading…'}</Dialog>

  const d = detail.draft
  const versions = [...detail.meta.versions.map((v) => String(v.version)).reverse(), 'draft']
  const labels = Object.fromEntries(detail.meta.versions.map((v) => [String(v.version), `v${v.version} · published ${new Date(v.published_at * 1000).toLocaleDateString([], { month: 'short', day: 'numeric' })}`]))
  labels.draft = 'Draft: a test run of unpublished changes'
  const approves = (d.steps ?? []).some((s: any) => s.kind === 'approve')
  const start = async () => {
    setBusy(true); setError(null)
    try {
      const run = await api.startRun(agent, { version: version === 'draft' ? null : Number(version), inputs, email_id: email || null, scripted, source })
      onStarted(run)
    } catch (e: any) { setError(e.message) } finally { setBusy(false) }
  }

  return (
    <Dialog title={`Run ${agent} now`} sub={d.trigger?.kind === 'schedule' ? 'Runs once, outside its schedule.' : 'Runs once.'} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={busy || !detail.meta.sample_set} onClick={start}>{busy ? 'Starting…' : 'Start run'}</button></>}>
      <Block title="Version"><Select value={version} options={versions} onChange={setVersion} labels={labels} label="Version" /></Block>
      {d.trigger?.kind === 'email' && (
        <Block title="Run it on this email" aside={source === 'live' ? 'from your inbox, last 14 days' : 'from the sample data'}>
          <Select value={email} options={emails.map((e) => e.id)} onChange={setEmail} label="Email"
            labels={Object.fromEntries(emails.map((e) => [e.id, `${e.subject} · ${e.from}`]))} />
        </Block>
      )}
      {Object.entries(d.run_options ?? {}).map(([name, opt]: [string, any]) => (
        <Block key={name} title={name.replace(/_/g, ' ')}>
          {opt.type === 'yes/no'
            ? <Segmented options={['true', 'false']} value={inputs[name] ?? 'true'} onChange={(v) => setInputs({ ...inputs, [name]: v })}
                labels={name === 'dry_run' ? { true: 'Yes: list what it would do', false: 'No: make the changes' } : { true: 'Yes', false: 'No' }} />
            : <input className="input" value={inputs[name] ?? ''} aria-label={name} onChange={(e) => setInputs({ ...inputs, [name]: e.target.value })} />}
          {opt.description && <span className="muted">{opt.description}</span>}
        </Block>
      ))}
      <Block title="Data">
        <Segmented options={['sample', 'live']} value={source} onChange={(v) => { setError(null); setSource(v) }}
          labels={{ sample: `Sample data${detail.meta.sample_set ? ` (${detail.meta.sample_set})` : ''}`, live: 'Real accounts' }} />
        {source === 'live' && (() => {
          const live = Object.values(d.connections ?? {}).filter((c: any) => c.service === 'gmail' || c.service === 'github') as any[]
          const acct = (c: any) => accounts.find((a) => a.id === c.account)
          const notSignedIn = live.filter((c) => !acct(c)?.signed_in)
          return (
            <>
              {notSignedIn.length > 0
                ? <span className="field-error">Sign in first, on Connections: {notSignedIn.map((c) => acct(c)?.label ?? c.account ?? `a ${c.service} connection`).join(', ')}.</span>
                : <span className="muted">{live.map((c) => `${c.service === 'github' ? 'GitHub steps read as' : 'Gmail steps read'} ${acct(c)?.signed_in_as}`).join('; ')}, read only, within each step's limits.</span>}
              <span className="faint">Sheets and Calendar steps still use sample data, so nothing is written to your real accounts.</span>
            </>
          )
        })()}
      </Block>
      <Block title="Model steps">
        <Segmented options={['api', 'scripted']} value={scripted ? 'scripted' : 'api'} onChange={(v) => setScripted(v === 'scripted')}
          labels={{ api: 'Claude API', scripted: 'Scripted answers (testing)' }} />
        {!scripted && !session?.claude_api && <span className="field-error">The service has no Claude API key; this run would be refused.</span>}
        {scripted && !detail.meta.replay && <span className="field-error">This agent has no scripted answers.</span>}
        {scripted && <span className="muted">Model steps give recorded answers; everything else (routing, rules, connections) runs for real.</span>}
      </Block>
      {!detail.meta.sample_set && <div className="notice warn">Pick the test data this agent runs on in its Settings first.</div>}
      <Block title="What to expect">
        <Guarantees items={[
          ...(approves ? ['You’ll be asked to approve before anything outside the agent changes.'] : []),
          `Stops at $${Number(d.limits?.budget_usd ?? 0).toFixed(2)}${d.limits?.timeout_minutes ? ` or ${d.limits.timeout_minutes} minutes` : ''}, whichever comes first.`,
          source === 'live' ? 'Reads your real Gmail and GitHub (read only). Sheets and Calendar stay on sample data.' : `Runs on ${detail.meta.sample_set ?? 'sample data'}: nothing touches a real account.`,
          'Every step and connection call is recorded in the run’s log.',
        ]} />
      </Block>
      {error && <div className="notice bad">{error}</div>}
    </Dialog>
  )
}
