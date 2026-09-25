// Following a drafting job: what Claude is doing now, how long it has taken, and what it has cost so far.
import { useEffect, useState } from 'react'
import { api, type DraftJob } from '../api'

export function useDraftJob(job: string | null, onDone: (j: DraftJob) => void): DraftJob | null {
  const [state, setState] = useState<DraftJob | null>(null)
  useEffect(() => {
    if (!job) { setState(null); return }
    let stopped = false
    const tick = async () => {
      if (stopped) return
      try {
        const j = await api.draftJob(job)
        setState(j)
        if (j.status !== 'running') { onDone(j); return }
      } catch { /* keep polling */ }
      setTimeout(tick, 1200)
    }
    tick()
    return () => { stopped = true }
  }, [job])
  return state
}

export default function DraftProgress({ job }: { job: DraftJob | null }) {
  const [now, setNow] = useState(Date.now() / 1000)
  useEffect(() => { const t = setInterval(() => setNow(Date.now() / 1000), 500); return () => clearInterval(t) }, [])
  if (!job) return <div className="row"><span className="spinner" />Starting…</div>
  const took = Math.round((job.ended_at ?? now) - job.started_at)
  return (
    <div className="stack" style={{ gap: 6, padding: '10px 12px', background: 'var(--acc-soft)', border: '1px solid #D6E1EE', borderRadius: 10 }}>
      <span className="row" style={{ flexWrap: 'nowrap' }}>{job.status === 'running' && <span className="spinner" />}
        <strong style={{ fontSize: 13 }}>{job.status === 'failed' ? 'Drafting failed' : job.stage}</strong></span>
      <span className="faint">Claude Opus 5 · {took}s · ${job.cost_usd.toFixed(2)} so far{job.attempts > 1 ? ` · draft ${job.attempts}` : ''}.
        Each draft is checked like any agent you save; problems go back to Claude to fix, up to three times.</span>
      {job.error && <span className="field-error">{job.error}</span>}
    </div>
  )
}
