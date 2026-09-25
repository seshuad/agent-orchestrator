// Connections: accounts connected to the workspace, what each may do, and which agents use them.
import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api, when, type Connection, type GoogleStatus, type ServiceInfo } from '../api'
import { Dialog, Icon, Pill } from '../ui'

export default function Connections() {
  const navigate = useNavigate()
  const [conns, setConns] = useState<Connection[] | null>(null)
  const [services, setServices] = useState<Record<string, ServiceInfo>>({})
  const [removing, setRemoving] = useState<Connection | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [google, setGoogle] = useState<GoogleStatus | null>(null)
  const [params, setParams] = useSearchParams()
  const load = () => api.connections().then(setConns)
  useEffect(() => { load(); api.services().then(setServices); api.googleStatus().then(setGoogle) }, [])
  const signIn = async (c: Connection) => {
    try { window.location.href = (await api.googleStart(c.id)).url } catch (e: any) { setError(e.message) }
  }
  const signOut = async (c: Connection) => { await api.googleSignOut(c.id); load() }
  const back = params.get('signed_in'), failed = params.get('google_error')

  const remove = async () => {
    if (!removing) return
    try { await api.removeConnection(removing.id); setRemoving(null); load() } catch (e: any) { setError(e.message) }
  }

  return (
    <div className="page">
      <div className="spread" style={{ alignItems: 'flex-end' }}>
        <div className="stack" style={{ gap: 4 }}>
          <h1>Connections</h1>
          <span className="muted" style={{ fontSize: 13, maxWidth: 760 }}>
            Accounts agents can use. You connect an account once for the workspace and choose what it may do; tokens stay in the vault
            and only the connector gateway reads them. Each step then picks actions within those permissions, with its own limits.
          </span>
        </div>
        <Link to="/connections/new" className="btn primary"><Icon name="plus" size={14} color="#fff" width={2.2} />Add connection</Link>
      </div>
      {back && <div className="notice info" style={{ background: '#F3F8F4', borderColor: '#D5E7DA', color: '#1F4D33' }}><Icon name="check" size={15} />
        <span>Signed in to Google. Runs on <strong>Real accounts</strong> can now read this Gmail (read only).</span>
        <button className="link" style={{ marginLeft: 'auto' }} onClick={() => setParams({})}>Dismiss</button></div>}
      {failed && <div className="notice bad"><Icon name="alert" size={15} /><span>Google sign-in didn't finish: {failed}</span>
        <button className="link" style={{ marginLeft: 'auto' }} onClick={() => setParams({})}>Dismiss</button></div>}
      {google && !google.configured && (
        <div className="notice warn" style={{ flexDirection: 'column', gap: 4 }}>
          <strong>To sign in to Gmail, set up a Google OAuth client once:</strong>
          <span>1. In Google Cloud Console, create a project and enable the <strong>Gmail API</strong>.</span>
          <span>2. OAuth consent screen: add your Google account as a test user (or publish the app, so sign-ins don't expire after 7 days).</span>
          <span>3. Credentials → Create OAuth client ID → <strong>Desktop app</strong>. Download the JSON as <code className="mono">~/.config/agent-service/client_secret.json</code>, then reload this page.</span>
          {google.client_file && google.client_type !== 'installed' && <span>The file at {google.client_file} is a “{google.client_type}” client; it must be a Desktop app client.</span>}
        </div>
      )}
      <div className="notice info"><Icon name="lock" size={15} /><span>Gmail connections can sign in to Google (read only) for runs on <strong>Real accounts</strong>. Sheets and Calendar stay on sample data for now. Tokens are kept in the workspace's vault; only the service reads them.</span></div>
      {conns === null && <span className="spinner" />}
      {conns?.length === 0 && <div className="card pad muted">No connections yet. Add one to let agents read email, sheets or calendars.</div>}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(420px, 1fr))', gap: 14 }}>
        {conns?.map((c) => {
          const svc = services[c.service]
          return (
            <div key={c.id} className="card pad" style={{ gap: 10 }}>
              <span className="row" style={{ flexWrap: 'nowrap' }}>
                <Icon name={svc?.icon ?? 'plug'} size={22} color="var(--muted)" />
                <span className="stack grow" style={{ gap: 1 }}><strong style={{ fontSize: 14 }}>{c.label}</strong><span className="faint">{c.service_name} · {c.account}</span></span>
                <span className="row" style={{ gap: 5, flexWrap: 'nowrap' }}><span style={{ width: 7, height: 7, borderRadius: '50%', background: '#2E8B57' }} /><span style={{ fontSize: 11, color: '#2E6B47', fontWeight: 600 }}>Connected</span></span>
              </span>
              <span className="row" style={{ gap: 6 }}>
                <Icon name="lock" size={13} color="var(--muted)" /><span className="muted">May:</span>
                {c.permissions.map((p) => <Pill key={p} kind="published">{svc?.permissions[p]?.label ?? p}</Pill>)}
                {c.permissions.length === 0 && <span className="faint">nothing yet</span>}
              </span>
              <div className="stack" style={{ gap: 4 }}>
                <span className="eyebrow">Used by</span>
                {c.used_by.length === 0 && <span className="faint">No agent yet</span>}
                {c.used_by.map((u) => (
                  <span key={u.agent} className="row" style={{ gap: 6 }}>
                    <Link to={`/agents/${u.agent}`} style={{ fontWeight: 600 }}>{u.agent}</Link>
                    <span className="faint">{u.steps.join(', ') || 'no step yet'}{u.actions.length ? ` · ${u.actions.map((a) => a.replace('_', ' ')).join(', ')}` : ''}</span>
                  </span>
                ))}
              </div>
              {c.can_sign_in && (
                <span className="spread" style={{ padding: '8px 10px', background: c.signed_in ? '#F3F8F4' : 'var(--soft)', border: `1px solid ${c.signed_in ? '#D5E7DA' : 'var(--line)'}`, borderRadius: 8 }}>
                  {c.signed_in
                    ? <span className="stack" style={{ gap: 1 }}><strong style={{ fontSize: 12.5, color: '#1F4D33' }}>Signed in to Google as {c.signed_in_as}</strong>
                        {c.signed_in_as && c.signed_in_as.toLowerCase() !== c.account.toLowerCase() && <span className="field-error">That's not {c.account}: runs will read {c.signed_in_as}.</span>}
                        <span className="faint">Real runs read this inbox, read only</span></span>
                    : <span className="stack" style={{ gap: 1 }}><strong style={{ fontSize: 12.5 }}>Sample data only</strong><span className="faint">Sign in so runs on real accounts can read this inbox</span></span>}
                  {c.signed_in
                    ? <button className="btn small" onClick={() => signOut(c)}>Sign out</button>
                    : <button className="btn small primary" disabled={!google?.configured} onClick={() => signIn(c)}>Sign in with Google</button>}
                </span>
              )}
              <span className="spread" style={{ borderTop: '1px solid var(--line-2)', paddingTop: 9 }}>
                <span className="faint">Connected {when(c.connected_at)} by {c.connected_by}</span>
                <span className="row" style={{ gap: 8 }}>
                  <button className="btn small" onClick={() => navigate(`/connections/${c.id}`)}><Icon name="edit" size={12} />Edit</button>
                  <button className="btn small danger" onClick={() => { setError(null); setRemoving(c) }}><Icon name="trash" size={12} />Remove</button>
                </span>
              </span>
            </div>
          )
        })}
      </div>
      {removing && (
        <Dialog title={`Remove ${removing.label}?`} onClose={() => setRemoving(null)}
          sub="Its token is deleted from the vault and no agent can use the account any more."
          footer={<><button className="btn" onClick={() => setRemoving(null)}>Cancel</button>
            <button className="btn danger" disabled={removing.used_by.length > 0} onClick={remove}>Remove connection</button></>}>
          {removing.used_by.length > 0
            ? <div className="notice warn"><Icon name="alert" size={15} /><span>Still used by {removing.used_by.map((u) => u.agent).join(', ')}. Pick another account in those agents' Connections first.</span></div>
            : <span className="muted">No agent uses it.</span>}
          {error && <div className="notice bad">{error}</div>}
        </Dialog>
      )}
    </div>
  )
}
