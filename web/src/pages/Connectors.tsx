// Connectors: the systems this workspace can reach. An admin sets each up once; builders connect accounts through them.
import { useContext, useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api, when, type Connector } from '../api'
import { Block, Check, Chips, Guarantees, Icon, SessionContext } from '../ui'
import { StatusDot } from './ConnectAccount'
import { ConnectionsHeader } from './Connections'

const SERVICE_ICON: Record<string, string> = { gmail: 'mail', 'google-sheets': 'group', 'google-calendar': 'calendar', github: 'code' }

function ConnectorCard({ c, selected, onOpen }: { c: Connector; selected: boolean; onOpen: () => void }) {
  const how = c.type === 'google' ? (c.settings.client_id ? 'OAuth client set' : 'No OAuth client yet')
    : c.type === 'github' ? 'A fine-grained token per account'
    : { oauth: 'OAuth: each builder signs in', shared: 'One shared credential', none: 'No sign-in' }[c.sign_in as string] ?? ''
  const tools = c.type === 'mcp' ? `${c.tools?.length ?? 0} tools: ${c.tools?.filter((t) => t.treat === 'read').length} read, ${c.tools?.filter((t) => t.treat === 'act').length} act` : ''
  return (
    <button type="button" className="card pad" onClick={onOpen} aria-label={`Open ${c.name}`}
      style={{ textAlign: 'left', gap: 8, border: selected ? '2px solid var(--acc)' : undefined, boxShadow: selected ? '0 4px 14px rgba(31,62,99,0.10)' : undefined }}>
      <span className="row" style={{ flexWrap: 'nowrap' }}>
        <Icon name={c.icon} size={20} color="var(--muted)" />
        <span className="stack grow" style={{ gap: 1 }}><strong style={{ fontSize: 14 }}>{c.name}</strong><span className="faint">{c.reach}</span></span>
        <StatusDot state={c.status.state} />
      </span>
      <span className="row muted" style={{ gap: 7 }}><Icon name="key" size={13} color="var(--muted)" />{how}{tools && <span className="faint">· {tools}</span>}</span>
      <span className="spread" style={{ borderTop: '1px solid var(--line-2)', paddingTop: 7 }}>
        <span className={c.status.state === 'attention' ? 'field-error' : 'faint'} style={{ fontSize: 11.5 }}>
          {c.status.message}{c.status.tested_at ? ` · ${when(c.status.tested_at)}${c.status.tested_by ? ` by ${c.status.tested_by}` : ''}` : ''}</span>
        <span className="faint" style={{ flex: 'none' }}>{c.accounts} account{c.accounts === 1 ? '' : 's'}</span>
      </span>
    </button>
  )
}

/** Google Workspace and GitHub: app settings, what builders may ask for, who may connect, and a test. */
function BuiltInPanel({ c, admin, onSaved }: { c: Connector; admin: boolean; onSaved: (c: Connector) => void }) {
  const [clientId, setClientId] = useState(c.settings.client_id ?? '')
  const [apiUrl, setApiUrl] = useState(c.settings.api_url ?? 'https://api.github.com')
  const [secret, setSecret] = useState<string | null>(null)
  const [offered, setOffered] = useState<Record<string, string[]>>(c.offered ?? {})
  const [who, setWho] = useState(c.who)
  const [domains, setDomains] = useState<string[]>(c.domains ?? [])
  const [types, setTypes] = useState<Record<string, any>>({})
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => { api.connectorTypes().then(setTypes) }, [])
  const redirect = `${window.location.origin}/`
  const save = async () => {
    setBusy('save'); setError(null)
    try {
      const out = await api.editConnector(c.id, { settings: c.type === 'google' ? { client_id: clientId.trim(), client_kind: c.settings.client_kind ?? 'web' } : { api_url: apiUrl.trim() },
        secret, offered, who, domains })
      setSecret(null); onSaved(out); return out
    } catch (e: any) { setError(e.message) } finally { setBusy(null) }
  }
  const test = async () => {
    if (admin && !(await save())) return
    setBusy('test')
    try { onSaved(await api.testConnector(c.id)) } catch (e: any) { setError(e.message) } finally { setBusy(null) }
  }
  const services = types[c.type]?.services ?? {}
  return (
    <div className="card pad" style={{ gap: 12, position: 'sticky', top: 16 }}>
      <div className="stack" style={{ gap: 2 }}><span className="eyebrow">Connector</span><span style={{ fontSize: 17, fontWeight: 700 }}>{c.name}</span></div>
      {!admin && <div className="notice info"><Icon name="lock" size={15} /><span>Only a workspace admin can change connectors.</span></div>}
      {c.type === 'google' && (
        <Block title="OAuth client">
          <label className="stack" style={{ gap: 3 }}><span className="muted">Client ID</span>
            <input className="input mono" value={clientId} disabled={!admin} onChange={(e) => setClientId(e.target.value)} placeholder="…apps.googleusercontent.com" aria-label="Client ID" /></label>
          <label className="stack" style={{ gap: 3 }}><span className="muted">Client secret</span>
            {secret === null
              ? <span className="row" style={{ flexWrap: 'nowrap' }}><input className="input mono grow" disabled value={c.secret_set ? `•••••••••••• set${c.secret_set_at ? ' ' + when(c.secret_set_at) : ''}` : 'not set'} aria-label="Client secret" />
                  {admin && <button className="link" onClick={() => setSecret('')}>{c.secret_set ? 'Replace' : 'Add'}</button>}</span>
              : <input className="input mono" type="password" autoComplete="off" value={secret} onChange={(e) => setSecret(e.target.value)} placeholder="GOCSPX-…" aria-label="New client secret" />}
          </label>
          <label className="stack" style={{ gap: 3 }}><span className="muted">Redirect URI: add it to the client in Google Cloud</span>
            <span className="row" style={{ flexWrap: 'nowrap' }}><input className="input mono grow" readOnly value={redirect} aria-label="Redirect URI" />
              <button className="link" onClick={() => navigator.clipboard?.writeText(redirect)}>Copy</button></span></label>
          <span className="faint">Google Cloud Console → Credentials → OAuth client ID (Web application), with the Gmail API enabled. The secret goes to the vault; nobody can read it back.</span>
        </Block>
      )}
      {c.type === 'github' && (
        <Block title="API">
          <label className="stack" style={{ gap: 3 }}><span className="muted">API URL</span>
            <input className="input mono" value={apiUrl} disabled={!admin} onChange={(e) => setApiUrl(e.target.value)} aria-label="API URL" /></label>
          <span className="faint">https://api.github.com, or your GitHub Enterprise server's API. Each account adds its own read-only token.</span>
        </Block>
      )}
      <Block title="What builders may ask for">
        <span className="faint">The most an account can be granted. Builders pick from these when they connect one.</span>
        {Object.entries(services).map(([svc, info]: [string, any]) => (
          <div key={svc} className="stack" style={{ gap: 4, paddingTop: 6, borderTop: '1px solid var(--line-2)' }}>
            <span className="row" style={{ gap: 7 }}><Icon name={SERVICE_ICON[svc] ?? 'plug'} size={14} color="var(--muted)" /><strong style={{ fontSize: 12.5 }}>{info.name}</strong></span>
            {Object.entries(info.permissions).map(([key, p]: [string, any]) => (
              <Check key={key} disabled={!admin} checked={(offered[svc] ?? []).includes(key)} detail={p.scope}
                onChange={(on) => setOffered({ ...offered, [svc]: on ? [...(offered[svc] ?? []), key] : (offered[svc] ?? []).filter((x) => x !== key) })}>{p.label}</Check>
            ))}
          </div>
        ))}
      </Block>
      <Block title="Who can connect accounts">
        <Check disabled={!admin} checked={who === 'builders'} onChange={(on) => setWho(on ? 'builders' : 'admins')} detail="Otherwise only admins can.">Any builder</Check>
        {c.type === 'google' && <span className="row"><span className="muted">Only accounts in</span><Chips values={domains} onChange={(v) => admin && setDomains(v)} placeholder="Add a domain (any if none)" /></span>}
      </Block>
      <Block title="Test">
        {c.status.state === 'ready' && c.status.tested_at ? <Guarantees items={[c.status.message ?? 'Works.']} />
          : <span className={c.status.state === 'attention' ? 'field-error' : 'faint'}>{c.status.message ?? 'Not tested yet.'}</span>}
      </Block>
      {error && <div className="notice bad"><Icon name="alert" size={15} /><span>{error}</span></div>}
      {admin && (
        <span className="row" style={{ justifyContent: 'flex-end' }}>
          <button className="btn" disabled={!!busy} onClick={test}>{busy === 'test' ? 'Testing…' : 'Test'}</button>
          <button className="btn primary" disabled={!!busy} onClick={save}>{busy === 'save' ? 'Saving…' : 'Save connector'}</button>
        </span>
      )}
    </div>
  )
}

export default function Connectors() {
  const navigate = useNavigate()
  const session = useContext(SessionContext)
  const admin = session?.user.role === 'Admin'
  const [items, setItems] = useState<Connector[] | null>(null)
  const [params, setParams] = useSearchParams()
  const [adding, setAdding] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const load = () => api.connectors().then(setItems)
  useEffect(() => { load() }, [])
  const selected = items?.find((c) => c.id === (params.get('c') ?? items?.[0]?.id))
  const open = (c: Connector) => (c.type === 'mcp' ? navigate(`/connections/connectors/${c.id}`) : setParams({ c: c.id }))
  const add = async (type: string) => {
    setAdding(false); setError(null)
    if (type === 'mcp') { navigate('/connections/connectors/new'); return }
    try { const c = await api.addConnector({ type, name: type === 'github' ? 'GitHub Enterprise' : 'Google Workspace' }); await load(); setParams({ c: c.id }) }
    catch (e: any) { setError(e.message) }
  }
  return (
    <div className="page">
      <ConnectionsHeader tab="Connectors" action={admin && (
        <span style={{ position: 'relative' }}>
          <button className="btn primary" onClick={() => setAdding(!adding)}><Icon name="plus" size={14} color="#fff" width={2.2} />Add connector</button>
          {adding && (
            <div className="menu" style={{ right: 0, left: 'auto', top: 40, width: 280 }}>
              <button onClick={() => add('mcp')}><Icon name="plug" size={15} /><span className="stack" style={{ gap: 1, alignItems: 'flex-start' }}><strong>MCP server</strong><span className="faint">Any system with an MCP server</span></span></button>
              <button onClick={() => add('github')}><Icon name="code" size={15} /><span className="stack" style={{ gap: 1, alignItems: 'flex-start' }}><strong>GitHub</strong><span className="faint">Another GitHub, e.g. Enterprise</span></span></button>
              <button onClick={() => add('google')}><Icon name="mail" size={15} /><span className="stack" style={{ gap: 1, alignItems: 'flex-start' }}><strong>Google Workspace</strong><span className="faint">Another OAuth client</span></span></button>
            </div>
          )}
        </span>
      )} />
      {error && <div className="notice bad"><Icon name="alert" size={15} /><span>{error}</span></div>}
      {items === null ? <span className="spinner" /> : (
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 440px', gap: 18, alignItems: 'start' }}>
          <div className="stack" style={{ gap: 14 }}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 14 }}>
              {items.map((c) => <ConnectorCard key={c.id} c={c} selected={selected?.id === c.id} onOpen={() => open(c)} />)}
            </div>
            <span className="faint">Built-in types come with their actions and limits defined. Any other system can be added as an MCP server: {admin ? 'you mark' : 'an admin marks'} each of its tools as read, act or not offered. <Link to="/connections">Accounts →</Link></span>
          </div>
          {selected && selected.type !== 'mcp' && <BuiltInPanel key={selected.id + (selected.status.tested_at ?? '')} c={selected} admin={admin}
            onSaved={(c) => setItems((items ?? []).map((x) => (x.id === c.id ? c : x)))} />}
        </div>
      )}
    </div>
  )
}
