// Runs: the list, and one run in detail — live while it runs, with its approval, outcome, checks and log.
import { Fragment, useEffect, useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, duration, followRun, when, STATUS_LABEL, type Run, type RunDetail } from '../api'
import { Block, Guarantees, Icon, Pill, Segmented, Check } from '../ui'
import RunNow from './RunNow'

type Filter = 'All' | 'Succeeded' | 'Failed' | 'Stopped' | 'In progress'
const matches = (r: Run, f: Filter) => f === 'All' || (f === 'In progress' ? ['running', 'waiting'].includes(r.status) : r.status === f.toLowerCase())

export default function RunsPage() {
  const { name, runId } = useParams()
  const navigate = useNavigate()
  const [runs, setRuns] = useState<Run[] | null>(null)
  const [filter, setFilter] = useState<Filter>('All')
  const [showRunNow, setShowRunNow] = useState(false)

  useEffect(() => {
    const load = () => api.runs(name).then(setRuns).catch(() => setRuns([]))
    load()
    const t = setInterval(load, 3000)
    return () => clearInterval(t)
  }, [name])

  const selected = runId ?? runs?.[0]?.id
  const base = name ? `/agents/${name}/runs` : '/runs'
  const counts = Object.fromEntries((['All', 'Succeeded', 'Failed', 'Stopped', 'In progress'] as Filter[]).map((f) => [f, `${f} ${runs?.filter((r) => matches(r, f)).length ?? 0}`]))

  return (
    <div className="stack grow" style={{ gap: 0, minHeight: 0 }}>
      {name && (
        <div className="spread" style={{ padding: '12px 26px', background: '#fff', borderBottom: '1px solid var(--line)' }}>
          <div className="row" style={{ gap: 12 }}>
            <Link to="/" style={{ color: 'var(--soft-text)' }}>Agents</Link><span style={{ color: '#B7B7AC' }}>/</span>
            <span style={{ fontSize: 16, fontWeight: 700 }}>{name}</span>
            <nav className="row" style={{ gap: 4, marginLeft: 12 }}>
              <Link to={`/agents/${name}`} className="btn small" style={{ border: 'none' }}>Design</Link>
              <span className="btn small" style={{ border: 'none', background: 'var(--acc-soft)' }}>Runs</span>
            </nav>
          </div>
          <button className="btn primary" onClick={() => setShowRunNow(true)}><Icon name="play" size={12} color="#fff" width={2} />Run now</button>
        </div>
      )}
      <div className="row grow" style={{ alignItems: 'stretch', flexWrap: 'nowrap', gap: 0, minHeight: 0 }}>
        <div className="runs-list">
          <div className="stack" style={{ padding: '12px 14px' }}>
            <span style={{ fontWeight: 700 }}>{name ? 'Runs' : 'All runs in the workspace'}</span>
            <Segmented options={['All', 'Succeeded', 'Failed', 'Stopped', 'In progress'] as Filter[]} value={filter} onChange={setFilter} labels={counts} />
          </div>
          <div style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}>
            {runs?.length === 0 && <div className="muted" style={{ padding: 14 }}>No runs yet. Use Run now to start one.</div>}
            {runs?.filter((r) => matches(r, filter)).map((r) => (
              <Link key={r.id} to={`${base}/${r.id}`} className={`run-row${r.id === selected ? ' sel' : ''}`}>
                <span className="spread"><span style={{ fontWeight: 700 }}>{when(r.started_at)}</span>
                  <span className="row" style={{ gap: 6 }}>{['running', 'waiting'].includes(r.status) && <span className="spinner" />}<Pill kind={r.status}>{STATUS_LABEL[r.status] ?? r.status}</Pill></span></span>
                <span className="muted">{!name && <strong style={{ color: 'var(--ink)' }}>{r.agent} · </strong>}{r.error?.title ?? (r.gate ? 'Waiting for approval' : r.status === 'running' ? 'In progress' : 'Finished')}</span>
                <span className="row faint" style={{ gap: 12 }}>
                  <span>{r.trigger === 'manual' ? `Manual · ${r.started_by.split(' ')[0]}` : r.trigger}</span>
                  <span>{r.version ? `v${r.version}` : 'draft'}{r.scripted ? ' · scripted' : ''}{r.source === 'live' ? ' · real Gmail' : ''}</span>
                  <span>{duration(r.duration)}</span><span>${(r.cost_usd ?? 0).toFixed(2)}</span>
                </span>
              </Link>
            ))}
          </div>
        </div>
        {selected ? <RunView id={selected} key={selected} /> : <div className="page muted">Select a run.</div>}
      </div>
      {showRunNow && name && <RunNow agent={name} onClose={() => setShowRunNow(false)} onStarted={(r) => { setShowRunNow(false); navigate(`${base}/${r.id}`) }} />}
    </div>
  )
}

function md(text: string): ReactNode {
  // The approval review is Markdown the agent's review template produced: headings, bullets, bold.
  const inline = (s: string) => s.split(/(\*\*[^*]+\*\*)/).map((p, i) => p.startsWith('**') ? <strong key={i}>{p.slice(2, -2)}</strong> : p)
  return text.split('\n').filter((l, i, all) => l.trim() || (i > 0 && all[i - 1].trim())).map((line, i) => {
    if (line.startsWith('## ')) return <h3 key={i} style={{ margin: '0 0 4px', fontSize: 15 }}>{line.slice(3)}</h3>
    if (line.startsWith('- ')) return <div key={i} style={{ paddingLeft: 14, textIndent: -10 }}>• {inline(line.slice(2))}</div>
    return <div key={i}>{inline(line) || ' '}</div>
  })
}

function RunView({ id }: { id: string }) {
  const [d, setD] = useState<RunDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [everyStep, setEveryStep] = useState(false)
  const [open, setOpen] = useState<number | null>(null)
  const [picked, setPicked] = useState('')
  const [busy, setBusy] = useState(false)
  const [opening, setOpening] = useState(false)
  const openConductor = async () => {
    const tab = window.open('about:blank', '_blank')       // open now, so the browser doesn't block it as a pop-up
    setOpening(true)
    try {
      const { url } = await api.conductorUi(id)
      if (tab) tab.location.href = url
      else window.open(url, '_blank')
    } catch (e: any) { tab?.close(); setError(e.message) } finally { setOpening(false) }
  }

  const isLive = d !== null && ['running', 'waiting'].includes(d.status)
  useEffect(() => { api.run(id).then(setD).catch((e) => setError(e.message)) }, [id])
  useEffect(() => (isLive ? followRun(id, setD) : undefined), [id, isLive])

  if (error) return <div className="page"><div className="notice bad">{error}</div></div>
  if (!d) return <div className="page"><span className="spinner" /></div>

  const live = ['running', 'waiting'].includes(d.status)
  const entries = d.log.filter((e) => everyStep || !e.plumbing)
  const block = d.outcome.block as Record<string, any> | undefined
  const act = d.outcome.act as Record<string, any> | undefined
  const decide = async (choice: string) => {
    setBusy(true)
    try { await api.approve(d.id, choice, picked || undefined); setD({ ...d, gate: null, status: 'running' }) } catch (e: any) { setError(e.message) } finally { setBusy(false) }
  }
  const costliest = d.log.reduce((m, e) => ((e.cost ?? 0) > (m?.cost ?? 0) ? e : m), null as RunDetail['log'][number] | null)

  return (
    <div className="page" style={{ gap: 14 }}>
      <div className="spread">
        <div className="row" style={{ gap: 10 }}>
          <h2>Run on {new Date(d.started_at * 1000).toLocaleDateString([], { month: 'short', day: 'numeric' })} at {new Date(d.started_at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</h2>
          <Pill kind={d.status}>{STATUS_LABEL[d.status] ?? d.status}</Pill>{live && <span className="spinner" />}
        </div>
        <span className="row" style={{ gap: 14 }}>
          {live && <button className="btn small danger" onClick={() => api.stop(d.id).then(() => api.run(d.id).then(setD))}>Stop run</button>}
          {(live || d.events_file) && (
            <button className="link" disabled={opening} title={live ? 'Conductor\u2019s live dashboard for this run' : 'A replay of this run in Conductor\u2019s dashboard, from its event log'}
              onClick={openConductor}>
              {opening ? <span className="spinner" /> : <Icon name="eye" size={13} width={2} />}
              {opening ? 'Starting Conductor\u2019s viewer…' : live ? 'Open live in Conductor' : 'Open in Conductor'} ↗
            </button>
          )}
        </span>
      </div>
      <div className="meta">
        {[['Agent', d.agent], ['Started', `Manually by ${d.started_by}`], ['Version', d.version ? `v${d.version}` : 'Draft (test run)'],
          ['Data', d.source === 'live' ? 'Real Gmail' : 'Sample data'], ['Model steps', d.scripted ? 'Scripted answers' : 'Claude API'], ['Dry run', d.inputs?.dry_run === 'false' ? 'No' : d.inputs?.dry_run ? 'Yes' : '—'],
          ['Took', duration(d.duration)], ['Cost', `$${(d.cost_usd ?? 0).toFixed(2)}`], ['Tokens', d.tokens ? `${Math.round(d.tokens / 1000)}K` : '0']]
          .map(([k, v]) => <span key={k}><span>{k}</span><span>{v}</span></span>)}
      </div>

      {d.gate && (
        <div className="card pad" style={{ borderColor: '#EBD3A6', background: '#FFFCF6' }}>
          <span className="row"><Icon name="person" size={16} color="var(--warn)" /><strong>Waiting for your approval</strong></span>
          <div style={{ fontSize: 12.5, lineHeight: 1.55 }}>{md(d.gate.prompt)}</div>
          {d.gate.option_details.some((o) => o.prompt_for) && (
            <label className="row"><span className="muted">Items to pick (ids, comma-separated):</span>
              <input className="input" value={picked} onChange={(e) => setPicked(e.target.value)} aria-label="Items to pick" /></label>
          )}
          <div className="row">{d.gate.option_details.map((o, i) => (
            <button key={o.value} className={`btn${i === 0 ? '' : ' primary'}`} disabled={busy || (!!o.prompt_for && !picked)} onClick={() => decide(o.value)}>{o.label}</button>
          ))}</div>
        </div>
      )}

      {d.error && (
        <div className="card pad" style={{ background: '#FDF1EE', borderColor: '#EEC5BA' }}>
          <span className="row"><Icon name="alert" size={16} color="var(--bad)" /><strong style={{ color: '#7A2A14', fontSize: 14 }}>{d.error.title}</strong></span>
          <span style={{ fontSize: 13, lineHeight: 1.55, color: 'var(--muted)' }}>{d.error.why}</span>
          {d.error.fix && <span className="muted"><strong>How to fix it:</strong> {d.error.fix}</span>}
          {d.error.raw && <details><summary className="faint" style={{ cursor: 'pointer' }}>Technical details</summary><div className="pre" style={{ marginTop: 6 }}>{d.error.raw}</div></details>}
        </div>
      )}

      {(block || act) && (
        <div className="row" style={{ alignItems: 'stretch', flexWrap: 'nowrap', gap: 12 }}>
          {block && (
            <div className="card pad grow">
              <strong>Outcome</strong>
              {Object.entries(block).filter(([k, v]) => !k.startsWith('rule_') && v !== null && typeof v !== 'object').map(([k, v]) => <span key={k} className="muted"><strong>{k.replace(/_/g, ' ')}:</strong> {String(v)}</span>)}
              {Object.entries(block).filter(([, v]) => Array.isArray(v) && v.length && typeof v[0] !== 'string').map(([k, v]) => <span key={k} className="muted"><strong>{k}:</strong> {(v as any[]).length}</span>)}
              {Array.isArray(block.notes) && block.notes.length > 0 && <details><summary className="faint" style={{ cursor: 'pointer' }}>Notes for the approver ({block.notes.length})</summary><ul className="muted" style={{ margin: '6px 0 0', paddingLeft: 18 }}>{block.notes.map((n: string, i: number) => <li key={i}>{n}</li>)}</ul></details>}
            </div>
          )}
          {act && (
            <div className="card pad grow">
              <strong>{act.would_create?.length ? 'Would change (dry run)' : 'Changed outside the agent'}</strong>
              {[...(act.created ?? []), ...(act.would_create ?? [])].map((t: string) => <span key={t} className="muted">• {t}</span>)}
              {act.skipped?.length > 0 && <span className="faint">Skipped: {act.skipped.join('; ')}</span>}
              {!act.created?.length && !act.would_create?.length && <span className="muted">Nothing.</span>}
            </div>
          )}
        </div>
      )}

      {!live && (
        <Block title="Checked by the service">
          <Guarantees items={[
            `${d.checks.calls} connection call${d.checks.calls === 1 ? '' : 's'}, ${d.checks.refused.length ? `${d.checks.refused.length} refused as outside their limits` : 'all within their limits'}.`,
            ...(d.log.some((e) => e.id === 'finish_check' && e.detail === 'Passed') ? ['Every Before finishing rule held before the block finished.'] : []),
            ...(d.inputs?.dry_run === 'true' ? ['Dry run: nothing outside the agent changed.'] : []),
          ]} />
        </Block>
      )}

      <div className="spread"><strong>Log</strong><Check checked={everyStep} onChange={setEveryStep}>Show every step ({d.log.length})</Check></div>
      <div className="card" style={{ overflow: 'hidden', flex: 'none' }}>
        <div className="log-row head"><span>At</span><span>Step</span><span>What happened</span><span style={{ textAlign: 'right' }}>Took</span><span style={{ textAlign: 'right' }}>Cost</span></div>
        {entries.length === 0 && <div className="log-row"><span /><span className="muted">{live ? 'Starting…' : 'No steps ran.'}</span></div>}
        {entries.map((e, i) => (
          <Fragment key={i}>
            <div className={`log-row ${e === costliest && (e.cost ?? 0) > 0.05 ? 'hot' : e.tone}`} onClick={() => setOpen(open === i ? null : i)} style={{ cursor: e.tools.length ? 'pointer' : undefined }}>
              <span className="faint mono">{Math.floor(e.at / 60)}:{String(Math.floor(e.at % 60)).padStart(2, '0')}</span>
              <span className="stack" style={{ gap: 3, alignItems: 'flex-start' }}>
                <span style={{ fontWeight: 600 }}>{e.step}</span>
                {(e.kind || e.model) && <Pill kind={e.kind || 'ask'}>{[e.model?.replace('claude-', ''), e.kind === 'planner' ? 'planner' : ''].filter(Boolean).join(' · ') || e.kind}</Pill>}
              </span>
              <span className="muted">
                {e.detail}
                {e.why && <span className="why"><Icon name="spark" size={12} color="var(--flow)" /><span><strong>Why:</strong> {e.why}</span></span>}
                {e.tools.length > 0 && <span className="faint" style={{ display: 'block' }}>{e.tools.length} connection call{e.tools.length > 1 ? 's' : ''} {open === i ? '▾' : '▸'}</span>}
                {e.value !== undefined && (
                  <details open onClick={(ev) => ev.stopPropagation()} style={{ marginTop: 4 }}>
                    <summary className="faint" style={{ cursor: 'pointer' }}>The value</summary>
                    <div className="pre" style={{ marginTop: 4, maxHeight: 360 }}>{e.value}</div>
                  </details>
                )}
                {open === i && e.tools.map((t, j) => <span key={j} className="mono faint" style={{ display: 'block' }}>{t.tool}({t.args})</span>)}
                {e === costliest && (e.cost ?? 0) > 0.05 && <span className="faint" style={{ display: 'block' }}>The most expensive step of the run.</span>}
              </span>
              <span className="faint" style={{ textAlign: 'right' }}>{e.took ? `${e.took}s` : ''}</span>
              <span className="faint" style={{ textAlign: 'right' }}>{e.cost ? `$${e.cost.toFixed(3)}` : ''}</span>
            </div>
          </Fragment>
        ))}
      </div>
    </div>
  )
}
