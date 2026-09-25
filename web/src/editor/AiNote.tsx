// What Claude did to this agent (drafted it, or changed it as asked), and asking it for the next change.
import { useState } from 'react'
import { api, type AgentDetail } from '../api'
import { Dialog, Icon } from '../ui'
import DraftProgress, { useDraftJob } from '../pages/DraftProgress'

export function AiStrip({ name, meta, onUndo, onDismiss }: { name: string; meta: AgentDetail['meta']; onUndo: () => void; onDismiss: () => void }) {
  const [open, setOpen] = useState(meta.ai?.kind === 'created')
  const ai = meta.ai
  if (!ai) return null
  const open_items = ai.assumptions.length + ai.questions.length
  return (
    <div className="ai-strip">
      <span className="row" style={{ flexWrap: 'nowrap', gap: 8 }}>
        <Icon name="spark" size={14} color="var(--acc)" />
        <span className="grow"><strong>{ai.kind === 'created' ? 'Drafted by Claude' : 'Changed by Claude'}:</strong> {ai.summary}
          {ai.errors.length > 0 && <span className="field-error"> {ai.errors.length} problem{ai.errors.length > 1 ? 's' : ''} it couldn't fix are marked in the editor.</span>}</span>
        {open_items > 0 && <button className="link" onClick={() => setOpen(!open)}>{open ? 'Hide' : `${ai.assumptions.length} assumptions, ${ai.questions.length} questions`}</button>}
        {meta.can_undo_ai && <button className="btn small" onClick={async () => { await api.undoRefine(name); onUndo() }}>Undo this change</button>}
        <button className="link" style={{ color: 'var(--faint)' }} onClick={onDismiss}>Dismiss</button>
      </span>
      {open && open_items > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 18, padding: '6px 0 2px 22px' }}>
          <div className="stack" style={{ gap: 3 }}><span className="eyebrow">Check these assumptions</span>
            {ai.assumptions.length ? ai.assumptions.map((a, i) => <span key={i} className="muted">• {a}</span>) : <span className="faint">None</span>}</div>
          <div className="stack" style={{ gap: 3 }}><span className="eyebrow">Questions for you</span>
            {ai.questions.length ? ai.questions.map((q, i) => <span key={i} className="muted">• {q}</span>) : <span className="faint">None</span>}
            {ai.questions.length > 0 && <span className="faint">Answer them with Refine with AI, or change the steps yourself.</span>}</div>
        </div>
      )}
      <span className="faint" style={{ paddingLeft: 22 }}>You asked: “{ai.request.length > 160 ? ai.request.slice(0, 160) + '…' : ai.request}” · {ai.model} · ${ai.cost_usd.toFixed(2)}</span>
    </div>
  )
}

export function RefineDialog({ name, onClose, onDone }: { name: string; onClose: () => void; onDone: () => void }) {
  const [instruction, setInstruction] = useState('')
  const [job, setJob] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const progress = useDraftJob(job, (j) => { if (j.status === 'done') onDone(); else setError(j.error ?? 'It failed.') })
  const start = async () => {
    setError(null)
    try { setJob((await api.refineAgent(name, instruction)).job) } catch (e: any) { setError(e.message) }
  }
  return (
    <Dialog title="Refine with AI" sub="Say what to change. Claude changes the draft, the service checks it, and you can undo the change." onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>{job ? 'Close' : 'Cancel'}</button>
        <button className="btn primary" disabled={!instruction.trim() || (!!job && progress?.status === 'running')} onClick={start}>
          <Icon name="spark" size={13} color="#fff" />{job && progress?.status === 'running' ? 'Changing…' : 'Change the draft'}</button></>}>
      <textarea className="textarea" rows={5} autoFocus value={instruction} onChange={(e) => setInstruction(e.target.value)} aria-label="What to change"
        placeholder="e.g. Add an approval before rows are added, showing each leak's property and severity. Only read alerts from the last 7 days." />
      <span className="faint">It sees the current draft, the workspace's accounts and tools, and the sample sets. Published versions don't change until you publish.</span>
      {job && <DraftProgress job={progress} />}
      {error && <div className="notice bad"><Icon name="alert" size={15} /><span>{error}</span></div>}
    </Dialog>
  )
}
