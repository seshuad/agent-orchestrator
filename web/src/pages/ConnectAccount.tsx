// Connect an account: pick a connector an admin has set up, what the account may do, a name; then sign in.
import { useEffect, useState } from 'react'
import { api, type Connector } from '../api'
import { Block, Check, Dialog, Icon, Segmented } from '../ui'

const STATE: Record<string, [string, string]> = { ready: ['Ready', '#2E8B57'], attention: ['Needs attention', '#C2553A'], setup: ['Not set up', '#8A8A80'] }
const SERVICE_NAME: Record<string, string> = { gmail: 'Gmail', 'google-sheets': 'Google Sheets', 'google-calendar': 'Google Calendar' }

export function StatusDot({ state }: { state: string }) {
  const [text, color] = STATE[state] ?? STATE.setup
  return <span className="row" style={{ gap: 5, flexWrap: 'nowrap' }}><span style={{ width: 7, height: 7, borderRadius: '50%', background: color }} /><span style={{ fontSize: 11, fontWeight: 600, color }}>{text}</span></span>
}

export default function ConnectAccount({ isAdmin, onClose, onDone }: { isAdmin: boolean; onClose: () => void; onDone: () => void }) {
  const [connectors, setConnectors] = useState<Connector[] | null>(null)
  const [picked, setPicked] = useState<string | null>(null)
  const [service, setService] = useState('')
  const [perms, setPerms] = useState<string[]>([])
  const [label, setLabel] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { api.connectors().then(setConnectors) }, [])

  const usable = (c: Connector) => c.status.state !== 'attention' && (c.who === 'builders' || isAdmin)
    && (c.type !== 'mcp' || Object.keys(c.services.mcp?.permissions ?? {}).length > 0)
  const connector = connectors?.find((c) => c.id === picked)
  const services = connector ? Object.keys(connector.services) : []
  const catalog = connector?.services[service]
  const pick = (c: Connector) => {
    setPicked(c.id); setError(null)
    const svc = Object.keys(c.services)[0]
    setService(svc); setPerms(Object.keys(c.services[svc]?.permissions ?? {}).slice(0, 1))
  }
  const pickService = (svc: string) => { setService(svc); setPerms(Object.keys(connector?.services[svc]?.permissions ?? {}).slice(0, 1)) }

  const connect = async () => {
    if (!connector) return
    setBusy(true); setError(null)
    try {
      const conn = await api.addConnection({ connector: connector.id, service, label, permissions: perms })
      if (connector.sign_in === 'oauth') { window.location.href = (await api.mcpStart(conn.id)).url; return }
      if (connector.sign_in === 'google' && service === 'gmail' && connector.status.state === 'ready') {
        window.location.href = (await api.googleStart(conn.id)).url; return
      }
      onDone()                                     // tokens and shared credentials: nothing more here
    } catch (e: any) { setError(e.message); setBusy(false) }
  }
  const next = !connector ? '' : connector.sign_in === 'oauth' ? `Next: sign in to ${connector.name}.`
    : connector.sign_in === 'google' && service === 'gmail' && connector.status.state === 'ready' ? 'Next: sign in with Google. The account is the one Google reports.'
    : connector.sign_in === 'google' ? 'Runs use sample data for it; only Gmail signs in for real so far.'
    : connector.sign_in === 'token' ? 'Next: add a read-only token on its card. GitHub reports whose it is.'
    : connector.sign_in === 'shared' ? `It uses ${connector.name}'s shared credential: nothing to sign in to.` : 'Nothing to sign in to.'

  return (
    <Dialog title="Connect an account" sub="Pick a connector your admin has set up, choose what agents may do with the account, then sign in." onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={!connector || !perms.length || busy} onClick={connect}>{busy ? 'Connecting…' : 'Connect account'}</button></>}>
      <Block title="1. Connector">
        {connectors === null ? <span className="spinner" /> : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))', gap: 8 }}>
            {connectors.map((c) => (
              <button key={c.id} type="button" disabled={!usable(c)} onClick={() => pick(c)} className="card pad"
                style={{ textAlign: 'left', gap: 5, border: picked === c.id ? '2px solid var(--acc)' : undefined, background: picked === c.id ? 'var(--acc-soft)' : '#fff', opacity: usable(c) ? 1 : 0.5 }}>
                <Icon name={c.icon} size={18} color={picked === c.id ? 'var(--acc)' : 'var(--muted)'} /><strong style={{ fontSize: 12.5 }}>{c.name}</strong><StatusDot state={c.status.state} />
              </button>
            ))}
          </div>
        )}
        <span className="faint">{connectors?.some((c) => !usable(c)) ? 'Greyed out: needs an admin first, or only admins may connect it. ' : ''}Missing a system? Ask an admin to add a connector.</span>
      </Block>
      {connector && services.length > 1 && (
        <Block title="Which service"><Segmented options={services} value={service} onChange={pickService} labels={SERVICE_NAME} /></Block>
      )}
      {connector && catalog && (
        <Block title="2. What agents may do with it">
          {Object.entries(catalog.permissions).map(([key, p]) => (
            <Check key={key} checked={perms.includes(key)} onChange={(on) => setPerms(on ? [...perms, key] : perms.filter((x) => x !== key))}
              detail={<>{p.detail} <code className="mono">{p.scope}</code></>}>{p.label}</Check>
          ))}
          <span className="row faint" style={{ gap: 6 }}><Icon name="lock" size={12} />Only what the admin offered. {catalog.never}</span>
        </Block>
      )}
      {connector && (
        <Block title="3. Name">
          <input className="input" value={label} placeholder={`e.g. ${connector.type === 'google' ? 'Finance sheets' : `Team ${connector.name}`}`} onChange={(e) => setLabel(e.target.value)} aria-label="Name" />
          <span className="faint">{next}</span>
        </Block>
      )}
      {error && <div className="notice bad"><Icon name="alert" size={15} /><span>{error}</span></div>}
    </Dialog>
  )
}
