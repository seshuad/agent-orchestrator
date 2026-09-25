// An MCP server as a connector: where it runs, how it signs in, and each tool marked read, act or not offered.
import { useContext, useEffect, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api, when, type Connector, type McpTool } from '../api'
import { Block, Guarantees, Icon, Pill, Segmented, SessionContext } from '../ui'
import { StatusDot } from './ConnectAccount'

const AUTH: [string, string, string][] = [
  ['oauth', 'OAuth: each builder signs in', "Found in the server's metadata; the client registers itself."],
  ['bearer', 'One shared token for the workspace', 'Kept in the vault; every account uses it.'],
  ['header', 'A custom header, such as an API key', 'Kept in the vault; every account uses it.'],
  ['none', 'None', 'Only for servers on a private network, or local commands.'],
]

export default function McpConnector() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const session = useContext(SessionContext)
  const admin = session?.user.role === 'Admin'
  const creating = id === 'new'
  const [c, setC] = useState<Connector | null>(null)
  const [name, setName] = useState('')
  const [transport, setTransport] = useState<'url' | 'command'>('url')
  const [url, setUrl] = useState('')
  const [command, setCommand] = useState('')
  const [args, setArgs] = useState('')
  const [auth, setAuth] = useState('oauth')
  const [header, setHeader] = useState('X-Api-Key')
  const [secret, setSecret] = useState<string | null>(null)
  const [tools, setTools] = useState<McpTool[]>([])
  const [who, setWho] = useState('builders')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const adopt = (x: Connector) => {
    setC(x); setName(x.name)
    const s = x.settings.server ?? {}
    setTransport(s.transport ?? 'url'); setUrl(s.url ?? ''); setCommand(s.command ?? ''); setArgs((s.args ?? []).join(' '))
    setAuth(x.settings.auth?.kind ?? 'none'); setHeader(x.settings.auth?.header ?? 'X-Api-Key'); setTools(x.tools ?? []); setWho(x.who)
  }
  useEffect(() => { if (!creating) api.connector(id!).then(adopt).catch((e) => setError(e.message)) }, [id, creating])

  const settings = () => ({ server: transport === 'url' ? { transport, url: url.trim() } : { transport, command: command.trim(), args: args.trim() ? args.trim().split(/\s+/) : [] },
    auth: { kind: auth, ...(auth === 'header' ? { header } : {}) } })
  const choices = () => tools.map((t) => ({ name: t.name, treat: t.treat, limits: t.limits }))
  const saveSettings = async (): Promise<Connector | null> => {
    setError(null)
    try {
      const body = { name: name.trim(), settings: settings(), secret, who }
      const out = creating ? await api.addConnector({ type: 'mcp', ...body }) : await api.editConnector(c!.id, body)
      setSecret(null); adopt(out)
      if (creating) navigate(`/connections/connectors/${out.id}`, { replace: true })
      return out
    } catch (e: any) { setError(e.message); return null }
  }
  const listTools = async () => {
    setBusy('list')
    const saved = await saveSettings()
    if (saved) {
      try { adopt(await api.testConnector(saved.id)) } catch (e: any) { setError(e.message) }
    }
    setBusy(null)
  }
  const approve = async () => {
    if (!c) return
    setBusy('save'); setError(null)
    try { adopt(await api.editConnector(c.id, { name: name.trim(), settings: settings(), secret, who, tools: choices() })); setSecret(null) }
    catch (e: any) { setError(e.message) } finally { setBusy(null) }
  }
  const signIn = async () => {
    const saved = await saveSettings()
    if (!saved) return
    try { window.location.href = (await api.connectorOauthStart(saved.id)).url } catch (e: any) { setError(e.message) }
  }
  const remove = async () => {
    if (!c || !confirm(`Remove ${c.name}? Accounts connected through it must be removed first.`)) return
    try { await api.removeConnector(c.id); navigate('/connections/connectors') } catch (e: any) { setError(e.message) }
  }
  const setTool = (n: string, patch: Partial<McpTool>) => setTools(tools.map((t) => (t.name === n ? { ...t, ...patch } : t)))
  const pending = tools.some((t) => t.new || t.changed) || (c?.tools ?? []).some((t) => {
    const now = tools.find((x) => x.name === t.name)
    return now && (now.treat !== t.treat || now.limits.join() !== t.limits.join())
  })
  const offered = tools.filter((t) => t.treat !== 'off')
  const cols = '150px minmax(0, 1fr) 130px 190px'

  return (
    <div className="page">
      <div className="spread" style={{ alignItems: 'flex-end' }}>
        <div className="stack" style={{ gap: 4 }}>
          <Link to="/connections/connectors" className="faint">← Connectors</Link>
          <h1>{creating ? 'Add an MCP server' : name || 'MCP server'}</h1>
          <span className="muted" style={{ fontSize: 13, maxWidth: 760 }}>Any system with an MCP server can become a connector. Its tools reach agents only through the gateway, and only the ones marked here: read tools in Ask steps, act tools in Act steps.</span>
        </div>
        {c && <StatusDot state={c.status.state} />}
      </div>
      {!admin && <div className="notice info"><Icon name="lock" size={15} /><span>Only a workspace admin can change connectors.</span></div>}
      {params.get('signed_in') && <div className="notice info" style={{ background: '#F3F8F4', borderColor: '#D5E7DA', color: '#1F4D33' }}><Icon name="check" size={15} /><span>Signed in. List the tools to review them.</span></div>}
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 400px', gap: 18, alignItems: 'start' }}>
        <div className="stack" style={{ gap: 14 }}>
          <div className="card pad" style={{ gap: 10 }}>
            <label className="stack" style={{ gap: 3 }}><span className="muted">Name</span>
              <input className="input" value={name} disabled={!admin} onChange={(e) => setName(e.target.value)} placeholder="e.g. Linear" aria-label="Connector name" /></label>
            <strong style={{ fontSize: 13 }}>1. Where it runs</strong>
            <Segmented options={['url', 'command'] as ('url' | 'command')[]} value={transport} onChange={(v) => admin && setTransport(v)}
              labels={{ url: 'Remote: a URL', command: 'Local: a command the service starts' }} />
            {transport === 'url'
              ? <input className="input mono" value={url} disabled={!admin} onChange={(e) => setUrl(e.target.value)} placeholder="https://mcp.example.com/mcp" aria-label="Server URL" />
              : <span className="row" style={{ flexWrap: 'nowrap' }}>
                  <input className="input mono" style={{ width: 220 }} value={command} disabled={!admin} onChange={(e) => setCommand(e.target.value)} placeholder="npx" aria-label="Command" />
                  <input className="input mono grow" value={args} disabled={!admin} onChange={(e) => setArgs(e.target.value)} placeholder="arguments, separated by spaces" aria-label="Arguments" />
                </span>}
            {transport === 'command' && <span className="faint">It runs on the service's own machine, as the service. Only add commands you trust.</span>}
          </div>
          <div className="card pad" style={{ gap: 8 }}>
            <strong style={{ fontSize: 13 }}>2. How it signs in</strong>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px 18px' }}>
              {AUTH.filter(([k]) => transport === 'url' || k === 'none' || k === 'bearer').map(([k, label, detail]) => (
                <label key={k} className="row" style={{ alignItems: 'flex-start', flexWrap: 'nowrap', gap: 8 }}>
                  <input type="radio" name="auth" checked={auth === k} disabled={!admin} onChange={() => setAuth(k)} style={{ marginTop: 3 }} />
                  <span className="stack" style={{ gap: 1 }}><span>{label}</span><span className="faint">{transport === 'command' && k === 'bearer' ? 'Passed to the command as UPSTREAM_TOKEN.' : detail}</span></span>
                </label>
              ))}
            </div>
            {auth === 'header' && <label className="stack" style={{ gap: 3 }}><span className="muted">Header name</span>
              <input className="input mono" value={header} disabled={!admin} onChange={(e) => setHeader(e.target.value)} aria-label="Header name" /></label>}
            {(auth === 'bearer' || auth === 'header') && (
              <label className="stack" style={{ gap: 3 }}><span className="muted">{auth === 'bearer' ? 'Token' : 'Header value'}</span>
                {secret === null
                  ? <span className="row" style={{ flexWrap: 'nowrap' }}><input className="input mono grow" disabled value={c?.secret_set ? `•••••••••••• set${c.secret_set_at ? ' ' + when(c.secret_set_at) : ''}` : 'not set'} aria-label="Token" />
                      {admin && <button className="link" onClick={() => setSecret('')}>{c?.secret_set ? 'Replace' : 'Add'}</button>}</span>
                  : <input className="input mono" type="password" autoComplete="off" value={secret} onChange={(e) => setSecret(e.target.value)} aria-label="New token" />}
              </label>
            )}
            {auth === 'oauth' && (
              <span className="row">
                <span className="faint">{c?.admin_signed_in ? 'You are signed in, so the service can list the tools.' : 'Sign in once yourself so the service can list the tools. Builders sign in with their own accounts later.'}</span>
                {admin && <button className="btn small" onClick={signIn}>{c?.admin_signed_in ? 'Sign in again' : 'Sign in to list tools'}</button>}
              </span>
            )}
          </div>
          <div className="card pad" style={{ gap: 6 }}>
            <span className="spread"><strong style={{ fontSize: 13 }}>3. Tools</strong>
              {admin && <button className="btn small" disabled={!!busy || (transport === 'url' ? !url.trim() : !command.trim())} onClick={listTools}>{busy === 'list' ? 'Listing…' : tools.length ? 'List again' : 'Connect and list tools'}</button>}</span>
            <span className="faint">New tools start as not offered until you review them. Descriptions are pinned: if the server changes one, the connector pauses until an admin approves the change.</span>
            {tools.length > 0 && (
              <>
                <div style={{ display: 'grid', gridTemplateColumns: cols, gap: 10, paddingTop: 4 }}>
                  {['Tool', 'What it does', 'Treat as', 'Steps can limit'].map((h) => <span key={h} className="eyebrow">{h}</span>)}
                </div>
                {tools.map((t) => (
                  <div key={t.name} style={{ display: 'grid', gridTemplateColumns: cols, gap: 10, alignItems: 'center', padding: '6px 0', borderTop: '1px solid var(--line-2)' }}>
                    <span className="stack" style={{ gap: 2 }}><code className="mono" style={{ fontWeight: 600 }}>{t.name}</code>
                      {t.new && <Pill kind="draft">new</Pill>}{t.changed && <Pill kind="failed">changed</Pill>}</span>
                    <span className="muted" style={{ fontSize: 12 }}>{t.description}</span>
                    <select className="select" value={t.treat} disabled={!admin} aria-label={`Treat ${t.name} as`} onChange={(e) => setTool(t.name, { treat: e.target.value as McpTool['treat'] })}>
                      <option value="read">Read</option><option value="act">Act</option><option value="off">Not offered</option>
                    </select>
                    <span className="row" style={{ gap: 4 }}>
                      {t.limitable.length === 0 && <span className="faint">—</span>}
                      {t.limitable.map((a) => (
                        <label key={a} className="row" style={{ gap: 3, flexWrap: 'nowrap' }}>
                          <input type="checkbox" checked={t.limits.includes(a)} disabled={!admin || t.treat === 'off'}
                            onChange={(e) => setTool(t.name, { limits: e.target.checked ? [...t.limits, a] : t.limits.filter((x) => x !== a) })} />
                          <code className="mono" style={{ fontSize: 11.5 }}>{a}</code></label>
                      ))}
                    </span>
                  </div>
                ))}
              </>
            )}
          </div>
        </div>
        <div className="card pad" style={{ gap: 12, position: 'sticky', top: 16 }}>
          <Block title="What steps will get">
            {offered.length ? <span className="row" style={{ gap: 5 }}>{offered.map((t) => <Pill key={t.name} kind={t.treat === 'read' ? 'ask' : 'act'}>{t.name} · {t.treat}</Pill>)}</span>
              : <span className="faint">Nothing yet: mark tools read or act.</span>}
            {tools.some((t) => t.treat === 'off') && <span className="faint">{tools.filter((t) => t.treat === 'off').map((t) => t.name).join(', ')}: not offered. No step can call them, whatever a builder ticks.</span>}
          </Block>
          <Block title="How a step uses it">
            <span className="faint">Ask steps pick read tools, Act steps act tools. A step can restrict each checked argument to a list of values; the gateway refuses any call outside it, or without it.</span>
          </Block>
          <Block title="Who can connect accounts">
            <label className="row" style={{ gap: 8 }}><input type="checkbox" checked={who === 'builders'} disabled={!admin} onChange={(e) => setWho(e.target.checked ? 'builders' : 'admins')} />Any builder (otherwise only admins)</label>
          </Block>
          <Block title="Content from this connector"><Guarantees items={['Treated as untrusted: other people may have written it.', "Every call goes through the gateway, within the step's limits, and is logged."]} /></Block>
          {c && <Block title="Test">
            {c.status.state === 'ready' && c.status.tested_at ? <Guarantees items={[c.status.message ?? 'Works.']} />
              : <span className={c.status.state === 'attention' ? 'field-error' : 'faint'}>{c.status.message}</span>}
          </Block>}
          {error && <div className="notice bad"><Icon name="alert" size={15} /><span>{error}</span></div>}
          {admin && (
            <span className="row" style={{ justifyContent: 'flex-end' }}>
              {c && <button className="btn danger" onClick={remove}>Remove</button>}
              <button className="btn primary" disabled={!!busy || !c || !tools.length} onClick={approve}
                title={!tools.length ? 'List the tools first' : undefined}>{busy === 'save' ? 'Saving…' : pending ? 'Approve and save' : 'Save connector'}</button>
            </span>
          )}
        </div>
      </div>
    </div>
  )
}
