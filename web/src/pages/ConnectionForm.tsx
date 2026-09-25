// Add a connection, or edit one: the service, the account, and what it may do.
import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, type Connection, type ServiceInfo } from '../api'
import { Block, Check, Icon } from '../ui'

export default function ConnectionForm() {
  const { id } = useParams()
  const navigate = useNavigate()
  const editing = id !== undefined && id !== 'new'
  const [services, setServices] = useState<Record<string, ServiceInfo>>({})
  const [existing, setExisting] = useState<Connection | null>(null)
  const [service, setService] = useState('gmail')
  const [account, setAccount] = useState('')
  const [label, setLabel] = useState('')
  const [perms, setPerms] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [needsForce, setNeedsForce] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.services().then((s) => {
      setServices(s)
      if (!editing) setPerms(Object.keys(s.gmail.permissions).slice(0, 1))
    })
    if (editing) api.connection(id!).then((c) => {
      setExisting(c); setService(c.service); setAccount(c.account); setLabel(c.label); setPerms(c.permissions)
    }).catch((e) => setError(e.message))
  }, [id, editing])

  const svc = services[service]
  const pickService = (s: string) => { setService(s); setPerms(Object.keys(services[s].permissions).slice(0, 1)) }
  const inUse = new Set(existing?.used_by.flatMap((u) => u.actions) ?? [])
  const save = async (force = false) => {
    setBusy(true); setError(null)
    try {
      const body = { service, account, label, permissions: perms, force }
      if (editing) await api.editConnection(id!, body)
      else await api.addConnection(body)
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
          <h1>{editing ? `Edit ${existing?.label ?? ''}` : 'Add a connection'}</h1>
          <span className="muted" style={{ fontSize: 13 }}>
            {editing ? 'Change the name or what this account may do. The account itself can’t change: connect a new one instead.'
              : 'Connect an account once; agents in the workspace can then use it, within the permissions you choose here.'}
          </span>
        </div>
        <Block title="Service">
          <div className="row" style={{ alignItems: 'stretch', flexWrap: 'nowrap', gap: 10 }}>
            {Object.entries(services).map(([key, s]) => (
              <button key={key} type="button" disabled={editing && key !== service} onClick={() => pickService(key)} className="card pad grow"
                style={{ textAlign: 'left', border: key === service ? '2px solid var(--acc)' : undefined, background: key === service ? 'var(--acc-soft)' : '#fff', opacity: editing && key !== service ? 0.45 : 1 }}>
                <Icon name={s.icon} size={20} color={key === service ? 'var(--acc)' : 'var(--muted)'} /><strong>{s.name}</strong>
              </button>
            ))}
          </div>
        </Block>
        <Block title="Account">
          <input className="input" style={{ fontSize: 14, padding: '8px 10px' }} value={account} disabled={editing} placeholder={service === 'github' ? 'GitHub username or organization' : 'name@company.com'}
            onChange={(e) => setAccount(e.target.value)} aria-label="Account" />
          {!editing && <span className="faint">{svc?.sign_in === 'token'
            ? 'After connecting, add a read-only token on the Connections page. It goes straight to the vault.'
            : 'After connecting, sign in with Google on the Connections page. The token goes straight to the vault.'}</span>}
        </Block>
        <Block title="Name">
          <input className="input" value={label} placeholder={account || (service === 'github' ? 'e.g. Billing repos' : 'e.g. Finance sheets')} onChange={(e) => setLabel(e.target.value)} aria-label="Name" />
          <span className="faint">What builders see when they pick an account for an agent.</span>
        </Block>
        {svc && (
          <Block title="What it may do">
            {Object.entries(svc.permissions).map(([key, p]) => (
              <div key={key} className="stack" style={{ gap: 2 }}>
                <Check checked={perms.includes(key)} onChange={(on) => setPerms(on ? [...perms, key] : perms.filter((x) => x !== key))}
                  detail={<>{p.detail} <code className="mono">{p.scope}</code></>}>{p.label}</Check>
                {!perms.includes(key) && p.actions.some((a) => inUse.has(a)) && (
                  <span className="field-error" style={{ marginLeft: 23 }}>Agents use this: {existing?.used_by.filter((u) => u.actions.some((a) => p.actions.includes(a))).map((u) => u.agent).join(', ')}</span>
                )}
              </div>
            ))}
            <span className="row faint" style={{ gap: 6 }}><Icon name="lock" size={12} />{svc.never}</span>
          </Block>
        )}
        {editing && existing && (
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
          <button className="btn primary" disabled={busy || !account.trim() || perms.length === 0} onClick={() => save(false)}>
            {busy ? 'Saving…' : editing ? 'Save changes' : 'Connect'}</button>
        </div>
      </div>
    </div>
  )
}
