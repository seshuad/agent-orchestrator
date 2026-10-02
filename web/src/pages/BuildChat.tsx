// Build with Claude: a conversation that looks at the data first (tables, folders, files, read-only), suggests agents
// grounded in what's there, and drafts one. The draft opens in the editor; nothing runs or publishes.
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, type BuildChat as Chat } from '../api'
import { Icon, Segmented, Select } from '../ui'

const STARTERS = [
  'What data can you see, and what agents would you suggest?',
  'Look at my sales warehouse and suggest a data quality check.',
  'What’s in the pipeline bucket? Anything that looks off?',
]

function text(s: string): ReactNode {
  // Claude's messages: paragraphs, bullets, numbered lines, **bold** and `code`.
  const inline = (line: string) => line.split(/(\*\*[^*]+\*\*|`[^`]+`)/).map((p, i) =>
    p.startsWith('**') ? <strong key={i}>{p.slice(2, -2)}</strong> : p.startsWith('`') ? <code key={i} className="mono">{p.slice(1, -1)}</code> : p)
  return s.split('\n').map((line, i) => {
    const t = line.trim()
    if (!t) return <div key={i} style={{ height: 6 }} />
    if (/^#{1,3} /.test(t)) return <div key={i} style={{ fontWeight: 700, marginTop: 4 }}>{inline(t.replace(/^#+ /, ''))}</div>
    if (/^[-*] /.test(t)) return <div key={i} style={{ paddingLeft: 16, textIndent: -10 }}>• {inline(t.slice(2))}</div>
    if (/^\d+\. /.test(t)) return <div key={i} style={{ paddingLeft: 18, textIndent: -14 }}>{inline(t)}</div>
    return <div key={i}>{inline(t)}</div>
  })
}

const KIND_ICON: Record<string, string> = { dataset: 'database', table: 'record', folder: 'folder', file: 'blank' }

export default function BuildChat() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [chat, setChat] = useState<Chat | null>(null)
  const [draft, setDraft] = useState('')
  const [source, setSource] = useState<'live' | 'sample'>('live')
  const [sets, setSets] = useState<string[]>([])
  const [sample, setSample] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => { api.sampleSets().then((s) => { setSets(s); setSample(s[0] ?? '') }) }, [])
  useEffect(() => {
    if (!id) { setChat(null); return }
    let stop = false
    const load = () => api.buildGet(id).then((c) => { if (!stop) setChat(c) }).catch((e) => setError(e.message))
    load()
    const t = setInterval(() => { if (chat?.status !== 'idle' || !chat) load() }, 1200)
    return () => { stop = true; clearInterval(t) }
  }, [id, chat?.status])
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }) }, [chat?.transcript.length, chat?.status])

  const send = async (message: string) => {
    if (!message.trim()) return
    setBusy(true); setError(null)
    try {
      if (!chat) {
        const c = await api.buildStart({ message, source, sample_set: source === 'sample' ? sample : null })
        setChat(c); navigate(`/new/chat/${c.id}`, { replace: true })
      } else setChat(await api.buildSay(chat.id, message))
      setDraft('')
    } catch (e: any) { setError(e.message) } finally { setBusy(false) }
  }
  const thinking = chat?.status === 'thinking'
  const stop = async () => { if (chat) { try { setChat(await api.buildStop(chat.id)) } catch (e: any) { setError(e.message) } } }
  const close = async () => { if (thinking) await stop(); navigate(chat?.agent ? `/agents/${chat.agent}` : '/') }
  const lastTool = [...(chat?.transcript ?? [])].reverse().find((t) => t.role === 'tool')

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 340px', minHeight: 0, flex: 1 }}>
      <div className="stack" style={{ minHeight: 0, borderRight: '1px solid var(--line)' }}>
        <div className="spread" style={{ padding: '12px 26px', background: '#fff', borderBottom: '1px solid var(--line)' }}>
          <span className="row" style={{ gap: 12 }}><Link to="/new" style={{ color: 'var(--soft-text)' }}>New agent</Link><span style={{ color: '#B7B7AC' }}>/</span>
            <strong style={{ fontSize: 16 }}>Build with Claude</strong>
            {chat && <span className="faint">{chat.source === 'sample' ? `looking at the sample set ${chat.sample_set}` : 'looking at your real accounts, read-only'} · ${chat.cost_usd.toFixed(2)}</span>}</span>
          <span className="row" style={{ gap: 8 }}>
            {chat?.agent && <button className="btn primary" onClick={() => navigate(`/agents/${chat.agent}`)}><Icon name="edit" size={13} color="#fff" />Open the draft</button>}
            <button className="btn" onClick={close} title={chat?.agent ? 'Back to the draft; the conversation is kept' : 'Leave; the conversation is kept'}>
              <Icon name="x" size={13} />Close</button>
          </span>
        </div>
        <div className="stack" style={{ flex: 1, overflowY: 'auto', padding: '18px 26px', gap: 12 }}>
          {!chat && (
            <div className="card pad" style={{ gap: 12, maxWidth: 760 }}>
              <strong style={{ fontSize: 15 }}>Let's look at your data, then design an agent together</strong>
              <span className="muted">Claude can list tables and folders, read schemas, and sample a few rows or the head of a file, through the same gateway
                and limits as a run, read-only. It suggests agents grounded in what it finds, asks what matters, and drafts one for you to review in the editor.</span>
              <span className="row"><span className="muted">Look at</span>
                <Segmented options={['live', 'sample']} value={source} onChange={setSource} labels={{ live: 'My real accounts', sample: 'A sample set' }} />
                {source === 'sample' && <Select value={sample} options={sets} onChange={setSample} label="Sample set" />}</span>
              <span className="row" style={{ gap: 6 }}>{STARTERS.map((s) => <button key={s} className="btn small" disabled={busy} onClick={() => send(s)}>{s}</button>)}</span>
            </div>
          )}
          {chat?.transcript.map((t, i) => t.role === 'tool' ? (
            <span key={i} className="row faint" style={{ gap: 7, paddingLeft: 4, flexWrap: 'nowrap' }}>
              <Icon name={t.ok ? (t.tool === 'save_draft' ? 'check' : 'search') : 'alert'} size={13} color={t.ok ? 'var(--muted)' : 'var(--bad)'} />
              <span><strong style={{ fontWeight: 600, color: 'var(--muted)' }}>{t.label}</strong> · {t.detail}</span>
              {t.agent && <Link to={`/agents/${t.agent}`}>open {t.agent}</Link>}
            </span>
          ) : (
            <div key={i} className="card pad" style={{ maxWidth: 760, gap: 2, fontSize: 13, lineHeight: 1.55, alignSelf: t.role === 'user' ? 'flex-end' : 'flex-start',
              background: t.role === 'user' ? 'var(--acc-soft)' : '#fff', borderColor: t.role === 'user' ? '#C9D7EA' : undefined }}>
              {t.role === 'assistant' && <span className="eyebrow" style={{ marginBottom: 2 }}>Claude</span>}
              {text(t.text)}
            </div>
          ))}
          {thinking && <span className="row faint" style={{ gap: 8 }}><span className="spinner" />{lastTool && Date.now() / 1000 - lastTool.at < 20 ? 'Looking further…' : 'Thinking…'}
            <button className="btn small" onClick={stop}><Icon name="stop" size={11} />Stop</button></span>}
          {chat?.status === 'error' && <div className="notice bad"><Icon name="alert" size={15} /><span>{chat.error}</span></div>}
          <div ref={bottom} />
        </div>
        <div className="stack" style={{ padding: '12px 26px 16px', borderTop: '1px solid var(--line)', background: '#fff', gap: 6 }}>
          {error && <span className="field-error">{error}</span>}
          <span className="row" style={{ flexWrap: 'nowrap', alignItems: 'flex-end' }}>
            <textarea className="textarea grow" rows={2} value={draft} disabled={thinking} aria-label="Message"
              placeholder={chat?.agent ? 'Ask for a change: Claude saves the draft again (you can undo it in the editor).' : 'Ask about your data, or say what the agent should do.'}
              onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(draft) } }} />
            <button className="btn primary" disabled={busy || thinking || !draft.trim()} onClick={() => send(draft)}>Send</button>
          </span>
          <span className="faint">Enter sends; Shift+Enter for a new line. Nothing runs or publishes from here.</span>
        </div>
      </div>
      <div className="stack" style={{ padding: '16px 18px', gap: 12, overflowY: 'auto', background: '#FAFAF7' }}>
        <span className="eyebrow">What Claude has looked at</span>
        {!chat?.explored.length && <span className="faint">Nothing yet. Everything it reads shows here: which tables and files its suggestions are based on.</span>}
        {chat?.explored.map((e) => (
          <div key={e.name} className="stack" style={{ gap: 2 }}>
            <span className="row" style={{ gap: 6, flexWrap: 'nowrap' }}><Icon name={KIND_ICON[e.kind] ?? 'blank'} size={13} color="var(--muted)" />
              <code className="mono" style={{ fontSize: 11.5, wordBreak: 'break-all' }}>{e.name}</code></span>
            {e.detail && <span className="faint" style={{ paddingLeft: 19 }}>{e.detail}</span>}
          </div>
        ))}
        {chat?.agent && (
          <div className="card pad" style={{ gap: 6, borderColor: '#D5E7DA', background: '#F3F8F4' }}>
            <strong>Draft: {chat.agent}</strong>
            <span className="muted">Checked like any agent you save. Review it in the editor, test it on sample data, then publish.</span>
            <button className="btn small primary" style={{ alignSelf: 'flex-start' }} onClick={() => navigate(`/agents/${chat.agent}`)}>Open in the editor</button>
          </div>
        )}
        <span className="faint" style={{ marginTop: 'auto' }}>Claude reads through the connector gateway, within each connector's limits; every look is logged. An admin can turn off
          sample rows and file contents per connector.</span>
      </div>
    </div>
  )
}
