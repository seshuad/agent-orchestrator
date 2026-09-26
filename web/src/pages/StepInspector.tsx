// The step inspector: one run of one step — what it was told, what it was given, what it called and got back, what it
// returned — and re-running it with the current draft on the same inputs, side by side with the original.
import { useEffect, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { api, type StepDetail } from '../api'
import { Icon, Pill } from '../ui'

function Json({ value, max = 420 }: { value: unknown; max?: number }) {
  if (value === undefined || value === null) return <span className="faint">Nothing</span>
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, 1)
  return <div className="pre" style={{ maxHeight: max }}>{text}</div>
}

function Section({ title, aside, children, open = true }: { title: string; aside?: ReactNode; children: ReactNode; open?: boolean }) {
  return (
    <details open={open} className="insp-section">
      <summary><span>{title}</span>{aside && <span className="faint" style={{ marginLeft: 'auto', fontWeight: 400 }}>{aside}</span>}</summary>
      <div className="stack" style={{ gap: 6, paddingTop: 6 }}>{children}</div>
    </details>
  )
}

function Body({ d }: { d: StepDetail }) {
  return (
    <>
      {d.error && <div className="notice bad"><Icon name="alert" size={15} /><span>{d.error}</span></div>}
      {d.type === 'agent' && (
        <>
          <Section title="Instructions (system prompt)" open={false}><Json value={d.system_prompt} /></Section>
          <Section title="What it was given" aside="the task, with its inputs filled in"><Json value={d.prompt} max={300} /></Section>
          {(d.tools?.length ?? 0) > 0 && (
            <Section title={`Tool calls (${d.tools!.length})`}>
              {d.tools!.map((t, i) => (
                <details key={i} className="insp-call">
                  <summary>
                    <code className="mono">{t.tool}</code>
                    {t.gateway && <Pill kind={t.gateway.outcome === 'allowed' ? 'succeeded' : 'failed'}>{t.gateway.outcome}</Pill>}
                    {t.truncated && <span className="faint">result cut to {Math.round(t.truncated.kept / 1000)}K of {Math.round(t.truncated.of / 1000)}K characters for the model</span>}
                  </summary>
                  <span className="eyebrow">Arguments</span><Json value={t.args} max={180} />
                  {t.gateway?.outcome === 'refused' && <span className="field-error">{t.gateway.detail}</span>}
                  <span className="eyebrow">Result</span><Json value={t.result} max={260} />
                </details>
              ))}
            </Section>
          )}
          {(d.repairs?.length ?? 0) > 0 && (
            <Section title={`Answer didn't match the record type (${d.repairs!.length})`} open={false}>
              <span className="faint">Conductor asked it to try again. The details:</span>
              {d.repairs!.map((r, i) => <Json key={i} value={r} max={200} />)}
            </Section>
          )}
        </>
      )}
      {(d.type === 'script' || d.type === 'mcp') && (
        <Section title="What it was given">
          {d.inputs === undefined || d.inputs === null
            ? <span className="faint">{d.scripted ? 'A scripted answer: it takes no input.' : 'Not recorded (runs from before the inspector).'}</span>
            : <Json value={d.inputs} />}
        </Section>
      )}
      {d.type === 'script' && (d.calls?.length ?? 0) > 0 && (
        <Section title={`Connection calls (${d.calls!.length})`}>
          {d.calls!.map((c, i) => (
            <details key={i} className="insp-call">
              <summary><code className="mono">{c.action}</code><Pill kind={c.outcome === 'allowed' ? 'succeeded' : 'failed'}>{c.outcome}</Pill><span className="faint">{c.detail}</span></summary>
              <span className="eyebrow">Arguments</span><Json value={c.args} max={160} />
              {c.result !== undefined && <><span className="eyebrow">Result</span><Json value={c.result} max={220} /></>}
            </details>
          ))}
        </Section>
      )}
      {d.type === 'human_gate' && (
        <Section title="What the approver saw">
          <Json value={d.prompt} max={260} />
          <span className="muted">Chose: <strong>{d.options?.find((o) => o.value === d.chosen)?.label ?? d.chosen ?? 'not yet'}</strong>{d.additional?.ids ? ` (${d.additional.ids})` : ''}</span>
        </Section>
      )}
      {d.type !== 'human_gate' && <Section title="What it returned"><Json value={d.output} /></Section>}
      {d.stderr && <Section title="Error output" open={false}><Json value={d.stderr} /></Section>}
    </>
  )
}

export default function StepInspector({ runId, step, n, label, onClose }: { runId: string; step: string; n: number; label: string; onClose: () => void }) {
  const [d, setD] = useState<StepDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [rerun, setRerun] = useState<{ id: string; status: string; detail: StepDetail | null; error?: string } | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { setD(null); setRerun(null); api.inspectStep(runId, step, n).then(setD).catch((e) => setError(e.message)) }, [runId, step, n])

  // Follow a re-run until it ends, then show its side of the comparison.
  useEffect(() => {
    if (!rerun || ['succeeded', 'failed', 'stopped'].includes(rerun.status)) return
    const t = setTimeout(async () => {
      const r = await api.run(rerun.id)
      if (['running', 'waiting'].includes(r.status)) { setRerun({ ...rerun, status: r.status }); return }
      const detail = await api.inspectStep(rerun.id, step, 0).catch(() => null)
      setRerun({ ...rerun, status: r.status, detail, error: r.error?.title })
    }, 1500)
    return () => clearTimeout(t)
  }, [rerun, step])

  const start = async () => {
    setBusy(true); setError(null)
    try { const r = await api.rerunStep(runId, step, n); setRerun({ id: r.id, status: 'running', detail: null }) }
    catch (e: any) { setError(e.message) } finally { setBusy(false) }
  }

  return (
    <aside className="inspector" aria-label={`Step ${label}`}>
      <div className="spread" style={{ padding: '14px 18px', borderBottom: '1px solid var(--line)' }}>
        <div className="stack" style={{ gap: 2 }}>
          <span className="eyebrow">Step inspector</span>
          <strong style={{ fontSize: 15 }}>{label}{d && d.runs > 1 ? <span className="faint" style={{ fontWeight: 400 }}> · run {n + 1} of {d.runs}</span> : ''}</strong>
        </div>
        <button className="icon-btn" aria-label="Close the inspector" onClick={onClose}><Icon name="x" size={13} /></button>
      </div>
      <div className="stack" style={{ gap: 10, padding: '12px 18px', overflowY: 'auto', flex: 1 }}>
        {error && <div className="notice bad"><Icon name="alert" size={15} /><span>{error}</span></div>}
        {!d && !error && <span className="spinner" />}
        {d && (
          <>
            <div className="meta" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
              {[['Kind', { agent: 'Model step', script: 'Built-in / script', mcp: 'Rules or tool', set: 'Plumbing', human_gate: 'Approval' }[d.type] ?? d.type],
                ['Took', `${d.took}s`], ...(d.model ? [['Model', d.model.replace('claude-', '')]] : []),
                ...(d.tokens ? [['Tokens', `${Math.round(d.tokens / 1000)}K (${Math.round((d.input_tokens ?? 0) / 1000)}K in)`]] : []),
                ...(d.cost ? [['Cost', `$${d.cost.toFixed(3)}`]] : []), ...(d.in_group ? [['Ran in', 'a parallel group']] : [])]
                .map(([k, v]) => <span key={k}><span>{k}</span><span>{v}</span></span>)}
            </div>
            {d.rerun_of && <span className="faint">A re-run of <Link to={`/runs/${d.rerun_of.run}`}>{d.rerun_of.step} in run {d.rerun_of.run}</Link>, with the draft as it was then.</span>}
            {d.rerunnable && !d.rerun_of && (
              <div className="rerun-box">
                <span className="stack" style={{ gap: 2 }}>
                  <strong style={{ fontSize: 12.5 }}>Re-run with the current draft</strong>
                  <span className="faint">{d.type === 'agent' ? 'The same inputs, with the draft’s instructions, task, model and tools. Its connections are called again.' : 'The same inputs, with the draft’s settings.'}</span>
                </span>
                <button className="btn small" disabled={busy || (rerun !== null && !['succeeded', 'failed', 'stopped'].includes(rerun.status))} onClick={start}>
                  <Icon name="play" size={11} width={2} />{rerun ? 'Again' : 'Re-run'}</button>
              </div>
            )}
            {rerun && (
              <div className="compare">
                <div className="stack" style={{ gap: 6, minWidth: 0 }}>
                  <span className="eyebrow">Before (this run)</span><Json value={d.output} max={320} />
                  <span className="faint">{d.cost ? `$${d.cost.toFixed(3)} · ` : ''}{d.took}s</span>
                </div>
                <div className="stack" style={{ gap: 6, minWidth: 0 }}>
                  <span className="eyebrow">Now (current draft) · <Link to={`/runs/${rerun.id}`}>run {rerun.id}</Link></span>
                  {!rerun.detail && !rerun.error ? <span className="row"><span className="spinner" />Running…</span>
                    : rerun.error ? <span className="field-error">{rerun.error}</span> : <Json value={rerun.detail!.output} max={320} />}
                  {rerun.detail && <span className="faint">{rerun.detail.cost ? `$${rerun.detail.cost.toFixed(3)} · ` : ''}{rerun.detail.took}s</span>}
                </div>
              </div>
            )}
            {(d.reruns?.length ?? 0) > 0 && !rerun && <span className="faint">Re-run before: {d.reruns!.map((r, i) => <span key={r}>{i > 0 && ', '}<Link to={`/runs/${r}`}>{r}</Link></span>)}</span>}
            <Body d={d} />
          </>
        )}
      </div>
    </aside>
  )
}
