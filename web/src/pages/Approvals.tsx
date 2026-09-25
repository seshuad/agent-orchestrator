// Approvals waiting for a person, across the workspace. Each opens its run, where the choices are.
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, when } from '../api'
import { Icon } from '../ui'

export default function Approvals() {
  const [items, setItems] = useState<Awaited<ReturnType<typeof api.approvals>> | null>(null)
  useEffect(() => {
    const load = () => api.approvals().then(setItems).catch(() => setItems([]))
    load()
    const t = setInterval(load, 3000)
    return () => clearInterval(t)
  }, [])
  return (
    <div className="page">
      <div className="stack" style={{ gap: 4 }}>
        <h1>Approvals</h1>
        <span className="muted" style={{ fontSize: 13 }}>Runs waiting for a person before they change anything outside the agent. The safe choice is always first.</span>
      </div>
      {items === null && <span className="spinner" />}
      {items?.length === 0 && <div className="card pad muted">Nothing is waiting for you.</div>}
      {items?.map((a) => (
        <Link key={a.run} to={`/agents/${a.agent}/runs/${a.run}`} className="card pad" style={{ color: 'var(--ink)' }}>
          <span className="spread">
            <span className="row"><Icon name="person" size={16} color="var(--warn)" /><strong>{a.agent}</strong><span className="faint">run started {when(a.started_at)}</span></span>
            <span className="btn small primary">Review</span>
          </span>
          <span className="muted">{a.gate.prompt.split('\n').find((l) => l.startsWith('## '))?.slice(3) ?? 'Approval needed'}</span>
          <span className="faint">Choices: {a.gate.option_details.map((o) => o.label).join(' · ')}</span>
        </Link>
      ))}
    </div>
  )
}
