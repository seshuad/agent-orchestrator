// Connections: the accounts builders have connected (Accounts), and the systems an admin has set up (Connectors).
import { useContext, useEffect, useState, type ReactNode } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api, when, type Connection } from '../api'
import { Dialog, Icon, Pill, SessionContext } from '../ui'
import ConnectAccount from './ConnectAccount'

export function ConnectionsHeader({ tab, action }: { tab: 'Accounts' | 'Connectors'; action?: ReactNode }) {
  return (
    <>
      <div className="spread" style={{ alignItems: 'flex-end' }}>
        <div className="stack" style={{ gap: 4 }}>
          <h1>Connections</h1>
          <span className="muted" style={{ fontSize: 13, maxWidth: 780 }}>
            {tab === 'Accounts'
              ? 'Accounts agents can use. You connect an account through a connector your admin has set up, and choose what it may do; tokens stay in the vault and only the connector gateway reads them.'
              : 'The systems this workspace can reach. An admin sets each one up once: its app credentials, what builders may ask it for, and who may connect accounts.'}
          </span>
        </div>
        {action}
      </div>
      <nav className="tabs">
        <Link to="/connections" className={tab === 'Accounts' ? 'on' : ''}>Accounts</Link>
        <Link to="/connections/connectors" className={tab === 'Connectors' ? 'on' : ''}>Connectors</Link>
      </nav>
    </>
  )
}

export const SERVICE_ICON: Record<string, string> = { gmail: 'mail', 'google-sheets': 'group', 'google-calendar': 'calendar', github: 'code', mcp: 'plug' }

export default function Connections() {
  const navigate = useNavigate()
  const session = useContext(SessionContext)
  const [conns, setConns] = useState<Connection[] | null>(null)
  const [removing, setRemoving] = useState<Connection | null>(null)
  const [connecting, setConnecting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [params, setParams] = useSearchParams()
  const [tokens, setTokens] = useState<Record<string, string>>({})
  const [tokenError, setTokenError] = useState<Record<string, string>>({})
  const load = () => api.connections().then(setConns)
  useEffect(() => { load() }, [])
  const signIn = async (c: Connection) => {
    setError(null)
    try { window.location.href = (await (c.sign_in === 'oauth' ? api.mcpStart(c.id) : api.googleStart(c.id))).url } catch (e: any) { setError(e.message) }
  }
  const signOut = async (c: Connection) => { await api.signOut(c.id); load() }
  const saveToken = async (c: Connection) => {
    setTokenError({ ...tokenError, [c.id]: '' })
    try { await api.githubToken(c.id, tokens[c.id] ?? ''); setTokens({ ...tokens, [c.id]: '' }); load() }
    catch (e: any) { setTokenError({ ...tokenError, [c.id]: e.message }) }
  }
  const back = params.get('signed_in'), failed = params.get('sign_in_error') ?? params.get('google_error')
  const remove = async () => {
    if (!removing) return
    try { await api.removeConnection(removing.id); setRemoving(null); load() } catch (e: any) { setError(e.message) }
  }
  const box = (on: boolean) => ({ padding: '8px 10px', background: on ? '#F3F8F4' : 'var(--soft)', border: `1px solid ${on ? '#D5E7DA' : 'var(--line)'}`, borderRadius: 8 })

  return (
    <div className="page">
      <ConnectionsHeader tab="Accounts" action={<button className="btn primary" onClick={() => setConnecting(true)}><Icon name="plus" size={14} color="#fff" width={2.2} />Connect an account</button>} />
      {back && <div className="notice info" style={{ background: '#F3F8F4', borderColor: '#D5E7DA', color: '#1F4D33' }}><Icon name="check" size={15} />
        <span>Signed in. Runs can now use this account for real, within each step's limits.</span>
        <button className="link" style={{ marginLeft: 'auto' }} onClick={() => setParams({})}>Dismiss</button></div>}
      {failed && <div className="notice bad"><Icon name="alert" size={15} /><span>The sign-in didn't finish: {failed}</span>
        <button className="link" style={{ marginLeft: 'auto' }} onClick={() => setParams({})}>Dismiss</button></div>}
      {error && !removing && <div className="notice bad"><Icon name="alert" size={15} /><span>{error}</span></div>}
      {conns === null && <span className="spinner" />}
      {conns?.length === 0 && <div className="card pad muted">No accounts yet. Connect one to let agents read email, sheets, calendars or anything a connector reaches.</div>}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(420px, 1fr))', gap: 14 }}>
        {conns?.map((c) => (
          <div key={c.id} className="card pad" style={{ gap: 10 }}>
            <span className="row" style={{ flexWrap: 'nowrap' }}>
              <Icon name={SERVICE_ICON[c.service] ?? 'plug'} size={22} color="var(--muted)" />
              <span className="stack grow" style={{ gap: 1 }}><strong style={{ fontSize: 14 }}>{c.label}</strong>
                <span className="faint">{c.service_name}{c.connector_name && c.connector_name !== c.service_name ? ` · ${c.connector_name}` : ''}{c.account ? ` · ${c.account}` : ''}</span></span>
              <span className="row" style={{ gap: 5, flexWrap: 'nowrap' }}><span style={{ width: 7, height: 7, borderRadius: '50%', background: '#2E8B57' }} /><span style={{ fontSize: 11, color: '#2E6B47', fontWeight: 600 }}>Connected</span></span>
            </span>
            <span className="row" style={{ gap: 6 }}>
              <Icon name="lock" size={13} color="var(--muted)" /><span className="muted">May:</span>
              {c.permissions.map((p) => <Pill key={p} kind="published">{c.catalog.permissions[p]?.label ?? p}</Pill>)}
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
            {(c.sign_in === 'google' || c.sign_in === 'oauth') && c.can_sign_in && (
              <span className="spread" style={box(c.signed_in)}>
                {c.signed_in
                  ? <span className="stack" style={{ gap: 1 }}><strong style={{ fontSize: 12.5, color: '#1F4D33' }}>{c.sign_in === 'google' ? `Signed in to Google as ${c.signed_in_as}` : `Signed in to ${c.connector_name}`}</strong>
                      <span className="faint">Runs use this account for real, within each step's limits</span></span>
                  : <span className="stack" style={{ gap: 1 }}><strong style={{ fontSize: 12.5 }}>{c.sign_in === 'google' ? 'Sample data only' : 'Not signed in'}</strong>
                      <span className="faint">{c.sign_in === 'google' ? 'Sign in so runs on real accounts can use it' : 'Sign in before an agent can use it'}</span></span>}
                {c.signed_in
                  ? <button className="btn small" onClick={() => signOut(c)}>Sign out</button>
                  : <button className="btn small primary" onClick={() => signIn(c)}>{c.sign_in === 'google' ? 'Sign in with Google' : `Sign in to ${c.connector_name}`}</button>}
              </span>
            )}
            {c.sign_in === 'token' && (
              <div className="stack" style={{ gap: 6, ...box(c.signed_in) }}>
                {c.signed_in
                  ? <span className="spread"><span className="stack" style={{ gap: 1 }}><strong style={{ fontSize: 12.5, color: '#1F4D33' }}>Token for GitHub user {c.signed_in_as}</strong>
                      <span className="faint">Real runs read the repositories each step names, read only</span></span>
                      <button className="btn small" onClick={() => signOut(c)}>Remove token</button></span>
                  : <>
                      <span className="stack" style={{ gap: 1 }}><strong style={{ fontSize: 12.5 }}>Sample data only</strong>
                        <span className="faint">Add a <a href="https://github.com/settings/personal-access-tokens/new" target="_blank" rel="noreferrer">fine-grained token</a> with read-only access to Issues, Pull requests and Contents.</span></span>
                      <span className="row" style={{ flexWrap: 'nowrap' }}>
                        <input className="input grow" type="password" autoComplete="off" placeholder="github_pat_…" aria-label={`GitHub token for ${c.label}`}
                          value={tokens[c.id] ?? ''} onChange={(e) => setTokens({ ...tokens, [c.id]: e.target.value })} onKeyDown={(e) => { if (e.key === 'Enter') saveToken(c) }} />
                        <button className="btn small primary" disabled={!(tokens[c.id] ?? '').trim()} onClick={() => saveToken(c)}>Save token</button>
                      </span>
                      {tokenError[c.id] && <span className="field-error">{tokenError[c.id]}</span>}
                    </>}
              </div>
            )}
            {c.sign_in === 'shared' && (
              <span className="stack" style={{ gap: 1, ...box(c.signed_in) }}>
                <strong style={{ fontSize: 12.5, color: c.signed_in ? '#1F4D33' : undefined }}>{c.signed_in ? `Uses ${c.connector_name}'s shared credential` : `${c.connector_name} has no credential yet`}</strong>
                <span className="faint">{c.signed_in ? 'Set by an admin on the connector; nothing to sign in to' : 'An admin adds it under Connectors'}</span>
              </span>
            )}
            <span className="spread" style={{ borderTop: '1px solid var(--line-2)', paddingTop: 9 }}>
              <span className="faint">Connected {when(c.connected_at)} by {c.connected_by}</span>
              <span className="row" style={{ gap: 8, flexWrap: 'nowrap', flexShrink: 0 }}>
                <button className="btn small" onClick={() => navigate(`/connections/${c.id}`)}><Icon name="edit" size={12} />Edit</button>
                <button className="btn small danger" onClick={() => { setError(null); setRemoving(c) }}><Icon name="trash" size={12} />Remove</button>
              </span>
            </span>
          </div>
        ))}
      </div>
      {connecting && <ConnectAccount isAdmin={session?.user.role === 'Admin'} onClose={() => setConnecting(false)} onDone={() => { setConnecting(false); load() }} />}
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
