// Deleting an agent: its draft, every published version and its run history. Refused while a run is in progress.
import { useEffect, useState } from 'react'
import { api } from '../api'
import { Dialog, Icon } from '../ui'

export default function DeleteAgent({ agent, onClose, onDeleted }: { agent: string; onClose: () => void; onDeleted: () => void }) {
  const [runs, setRuns] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => { api.runs(agent).then((r) => setRuns(r.length)).catch(() => setRuns(null)) }, [agent])

  const remove = async () => {
    setBusy(true); setError(null)
    try { await api.deleteAgent(agent); onDeleted() } catch (e: any) { setError(e.message); setBusy(false) }
  }

  return (
    <Dialog title={`Delete ${agent}?`} sub="This can't be undone." onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn danger" disabled={busy} onClick={remove}><Icon name="trash" size={13} />{busy ? 'Deleting…' : 'Delete agent'}</button></>}>
      <span className="muted">
        {runs ? `Deletes the draft, every published version, and the history of its ${runs} run${runs > 1 ? 's' : ''}, logs included.` : 'Deletes the draft and every published version.'}{' '}
        Connections stay in the workspace.
      </span>
      {error && <div className="notice bad">{error}</div>}
    </Dialog>
  )
}
