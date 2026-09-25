// The starting page: the signed-in user's workspace, its agents, and Run now / New agent.
import { useContext, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, when, STATUS_LABEL, type AgentSummary } from '../api'
import { Avatar, HistoryDots, Icon, Pill, Segmented, SessionContext } from '../ui'
import DeleteAgent from './DeleteAgent'
import RunNow from './RunNow'

type Filter = 'All' | 'Mine' | 'Published' | 'Drafts'

export default function Home() {
  const session = useContext(SessionContext)
  const navigate = useNavigate()
  const [agents, setAgents] = useState<AgentSummary[] | null>(null)
  const [filter, setFilter] = useState<Filter>('All')
  const [query, setQuery] = useState('')
  const [running, setRunning] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)
  const [approvals, setApprovals] = useState<{ run: string; agent: string }[]>([])

  const load = () => {
    api.agents().then(setAgents).catch(() => setAgents([]))
    api.approvals().then(setApprovals).catch(() => {})
  }
  useEffect(() => {
    load()
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [])

  const me = session?.user.name
  const shown = (agents ?? []).filter((a) =>
    (filter === 'All' || (filter === 'Mine' && a.owner === me) || (filter === 'Published' && a.status === 'published') || (filter === 'Drafts' && a.status === 'draft'))
    && (a.name + a.description).toLowerCase().includes(query.toLowerCase()))
  const counts = {
    All: agents?.length ?? 0, Mine: agents?.filter((a) => a.owner === me).length ?? 0,
    Published: agents?.filter((a) => a.status === 'published').length ?? 0, Drafts: agents?.filter((a) => a.status === 'draft').length ?? 0,
  }
  const week = session?.week ?? {}
  const weekTotal = Object.values(week).reduce((a, b) => a + b, 0)
  const hour = new Date().getHours()
  const greeting = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening'

  return (
    <div className="page">
      <div className="spread" style={{ alignItems: 'flex-end' }}>
        <div className="stack" style={{ gap: 4 }}>
          <h1>{greeting}, {me?.split(' ')[0] ?? ''}</h1>
          <span className="muted" style={{ fontSize: 13 }}>
            {counts.All} agents in {session?.workspace.name}.{' '}
            {session?.workspace.members.filter((m) => m.name !== me).map((m) => `${m.name} is ${m.role === 'Admin' ? 'the workspace admin' : 'a ' + m.role.toLowerCase()}`).join('; ')}.
          </span>
        </div>
        <div className="row">
          <label className="row input" style={{ width: 240, flexWrap: 'nowrap' }}>
            <Icon name="search" size={14} color="var(--faint)" />
            <input aria-label="Search agents" placeholder="Search agents" value={query} onChange={(e) => setQuery(e.target.value)}
              style={{ border: 'none', outline: 'none', background: 'transparent', width: '100%' }} />
          </label>
          <Link to="/new" className="btn primary"><Icon name="plus" size={14} color="#fff" width={2.2} />New agent</Link>
        </div>
      </div>

      {session && !session.claude_api && (
        <div className="notice info"><Icon name="alert" size={15} />
          <span>The service has no Claude API key, so model steps can only run with scripted answers. Start the service with ANTHROPIC_API_KEY set to run agents for real.</span>
        </div>
      )}

      <div className="row" style={{ gap: 14, alignItems: 'stretch', flexWrap: 'nowrap' }}>
        <div className="card pad grow">
          <span className="eyebrow">Runs, last 7 days</span>
          <span style={{ fontSize: 22, fontWeight: 700 }}>{weekTotal}</span>
          <span className="muted">{week.succeeded ?? 0} succeeded · {week.failed ?? 0} failed · {week.stopped ?? 0} stopped · {(week.waiting ?? 0) + (week.running ?? 0)} in progress</span>
        </div>
        <div className="card pad grow">
          <span className="eyebrow">Waiting for you</span>
          <span style={{ fontSize: 22, fontWeight: 700, color: approvals.length ? 'var(--warn)' : 'var(--ink)' }}>
            {approvals.length ? `${approvals.length} approval${approvals.length > 1 ? 's' : ''}` : 'Nothing'}
          </span>
          {approvals[0] ? <Link to="/approvals">{approvals[0].agent} · Review</Link> : <span className="muted">Approvals appear here when a run needs you.</span>}
        </div>
        <div className="card pad grow">
          <span className="eyebrow">Spend this month</span>
          <span style={{ fontSize: 22, fontWeight: 700 }}>${session?.spend_usd.toFixed(2) ?? '0.00'} <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--soft-text)' }}>of ${session?.workspace.spend_limit_usd.toFixed(2)}</span></span>
          <div style={{ height: 6, background: 'var(--line-2)', borderRadius: 3, overflow: 'hidden' }}>
            <div style={{ width: `${Math.min(100, ((session?.spend_usd ?? 0) / (session?.workspace.spend_limit_usd || 1)) * 100)}%`, height: '100%', background: 'var(--acc)' }} />
          </div>
        </div>
      </div>

      <div className="card" style={{ overflow: 'hidden' }}>
        <div className="spread" style={{ padding: '12px 16px' }}>
          <span style={{ fontSize: 14, fontWeight: 700 }}>Agents</span>
          <Segmented options={['All', 'Mine', 'Published', 'Drafts'] as Filter[]} value={filter} onChange={setFilter}
            labels={Object.fromEntries(Object.entries(counts).map(([k, v]) => [k, `${k} ${v}`]))} />
        </div>
        <table className="table">
          <thead><tr><th>Agent</th><th>Status</th><th>Starts</th><th>Last run</th><th>Recent runs</th><th>Next run</th><th /></tr></thead>
          <tbody>
            {agents === null && <tr><td colSpan={7}><span className="spinner" /> Loading…</td></tr>}
            {agents && shown.length === 0 && <tr><td colSpan={7} className="muted">No agents here yet. <Link to="/new">Create one</Link>.</td></tr>}
            {shown.map((a) => (
              <tr key={a.name}>
                <td style={{ maxWidth: 360 }}>
                  <div className="stack" style={{ gap: 3 }}>
                    <Link to={`/agents/${a.name}`} style={{ fontSize: 14, fontWeight: 700, color: 'var(--ink)' }}>{a.name}</Link>
                    <span className="muted" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{a.description}</span>
                    <span className="row faint" style={{ gap: 6 }}><Avatar initials={a.owner.split(' ').map((p) => p[0]).join('')} size={16} color={a.owner === me ? undefined : '#6E7F99'} />{a.owner}</span>
                  </div>
                </td>
                <td>
                  <span className="row" style={{ gap: 6 }}>
                    <Pill kind={a.status}>{a.status === 'published' ? 'Published' : 'Draft'}</Pill>
                    {a.version && <span className="faint">v{a.version}</span>}
                    {a.status === 'published' && a.has_changes && <span className="faint" title="The draft has changes that aren't published">· edits</span>}
                  </span>
                </td>
                <td><span className="row muted" style={{ flexWrap: 'nowrap', fontSize: 12.5 }}><Icon name={a.trigger.kind === 'email' ? 'mail' : a.trigger.kind === 'schedule' ? 'clock' : 'person'} size={14} color="var(--faint)" />{a.trigger_text}</span></td>
                <td>
                  {a.last_run ? (
                    <Link to={`/agents/${a.name}/runs/${a.last_run.id}`} className="stack" style={{ gap: 3, alignItems: 'flex-start' }}>
                      <Pill kind={a.last_run.status}>{STATUS_LABEL[a.last_run.status] ?? a.last_run.status}</Pill>
                      <span className="faint">{when(a.last_run.started_at)} · {a.last_run.trigger}</span>
                    </Link>
                  ) : <span className="faint">Not run yet</span>}
                </td>
                <td><HistoryDots states={a.recent} /></td>
                <td className="muted" style={{ fontSize: 12.5 }}>{a.next_run ?? '—'}</td>
                <td>
                  <span className="row" style={{ justifyContent: 'flex-end', gap: 14, flexWrap: 'nowrap' }}>
                    {a.last_run && <Link to={`/agents/${a.name}/runs`} style={{ fontWeight: 600 }}>Runs</Link>}
                    {a.status === 'published'
                      ? <button className="btn small" onClick={() => setRunning(a.name)}><Icon name="play" size={12} width={2} />Run now</button>
                      : <button className="btn small" onClick={() => navigate(`/agents/${a.name}`)}><Icon name="edit" size={12} width={2} />Finish setting up</button>}
                    <button className="btn small" onClick={() => setDeleting(a.name)} aria-label={`Delete ${a.name}`} title="Delete agent"><Icon name="trash" size={14} color="var(--faint)" /></button>
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {deleting && <DeleteAgent agent={deleting} onClose={() => setDeleting(null)} onDeleted={() => { setDeleting(null); load() }} />}
      {running && <RunNow agent={running} onClose={() => setRunning(null)} onStarted={(run) => navigate(`/agents/${running}/runs/${run.id}`)} />}
    </div>
  )
}
