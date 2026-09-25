// Edit a connected account: its name, and what it may do within what its connector offers.
import { useEffect, useState } from 'react'
import { Link, Navigate, useNavigate, useParams } from 'react-router-dom'
import { api, type Connection } from '../api'
import { Block, Check, Icon } from '../ui'

export default function ConnectionForm() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [existing, setExisting] = useState<Connection | null>(null)
  const [label, setLabel] = useState('')
  const [perms, setPerms] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [needsForce, setNeedsForce] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (id && id !== 'new') api.connection(id).then((c) => { setExisting(c); setLabel(c.label); setPerms(c.permissions) }).catch((e) => setError(e.message))
  }, [id])
  if (id === 'new') return <Navigate to="/connections" replace />

  const inUse = new Set(existing?.used_by.flatMap((u) => u.actions) ?? [])
  const save = async (force = false) => {
    if (!existing) return
    setBusy(true); setError(null)
    try {
      await api.editConnection(existing.id, { service: existing.service, label, permissions: perms, force })
      navigate('/connections')
    } catch (e: any) {
      setError(e.message); setNeedsForce(e.message.includes('Save anyway'))
    } finally { setBusy(false) }
  }

  return (
    <div className="page" style={{ alignItems: 'center' }}>
      <div className="card pad" style={{ width: 720, padding: '22px 26px', gap: 14 }}>
        <div className="stack" style={{ gap: 4 }}>
          <Link to="/connections" className="faint">← Connections</Link>
          <h1>Edit {existing?.label ?? ''}</h1>
          <span className="muted" style={{ fontSize: 13 }}>
            {existing ? `${existing.service_name}${existing.connector_name && existing.connector_name !== existing.service_name ? ` through ${existing.connector_name}` : ''}${existing.account ? ` · ${existing.account}` : ''}. ` : ''}
            Change its name or what it may do. The account itself can't change: connect a new one instead.
          </span>
        </div>
        <Block title="Name">
          <input className="input" value={label} onChange={(e) => setLabel(e.target.value)} aria-label="Name" />
          <span className="faint">What builders see when they pick an account for an agent.</span>
        </Block>
        {existing && (
          <Block title="What it may do">
            {Object.entries(existing.catalog.permissions).map(([key, p]) => (
              <div key={key} className="stack" style={{ gap: 2 }}>
                <Check checked={perms.includes(key)} onChange={(on) => setPerms(on ? [...perms, key] : perms.filter((x) => x !== key))}
                  detail={<>{p.detail} <code className="mono">{p.scope}</code></>}>{p.label}</Check>
                {!perms.includes(key) && p.actions.some((a) => inUse.has(a)) && (
                  <span className="field-error" style={{ marginLeft: 23 }}>Agents use this: {existing.used_by.filter((u) => u.actions.some((a) => p.actions.includes(a))).map((u) => u.agent).join(', ')}</span>
                )}
              </div>
            ))}
            <span className="row faint" style={{ gap: 6 }}><Icon name="lock" size={12} />Only what the connector's admin offers. {existing.catalog.never}</span>
          </Block>
        )}
        {existing && (
          <Block title="Used by">
            {existing.used_by.length === 0 ? <span className="faint">No agent yet</span> : existing.used_by.map((u) => (
              <span key={u.agent} className="row"><Link to={`/agents/${u.agent}`} style={{ fontWeight: 600 }}>{u.agent}</Link><span className="faint">{u.steps.join(', ')}</span></span>
            ))}
          </Block>
        )}
        {error && <div className="notice bad"><Icon name="alert" size={15} /><span>{error}</span></div>}
        <div className="row" style={{ justifyContent: 'flex-end' }}>
          <button className="btn" onClick={() => navigate('/connections')}>Cancel</button>
          {needsForce && <button className="btn danger" disabled={busy} onClick={() => save(true)}>Save anyway</button>}
          <button className="btn primary" disabled={busy || !existing || perms.length === 0} onClick={() => save(false)}>{busy ? 'Saving…' : 'Save changes'}</button>
        </div>
      </div>
    </div>
  )
}
