// New agent: name it, say what it should do, and start blank or from a vetted pattern.
import { useContext, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { Block, Icon, Select, SessionContext } from '../ui'
import DraftProgress, { useDraftJob } from './DraftProgress'

export default function NewAgent() {
  const navigate = useNavigate()
  const session = useContext(SessionContext)
  const [job, setJob] = useState<string | null>(null)
  const progress = useDraftJob(job, (j) => {
    if (j.status === 'done' && j.result) navigate(`/agents/${j.result.agent}`)
    else { setBusy(false); setError(j.error ?? 'Drafting failed.') }
  })
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [start, setStart] = useState('blank')
  const [templates, setTemplates] = useState<{ key: string; name: string; description: string }[]>([])
  const [sets, setSets] = useState<string[]>([])
  const [sample, setSample] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.templates().then(setTemplates)
    api.sampleSets().then((s) => { setSets(s); setSample(s[0] ?? '') })
  }, [])

  const slug = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
  const describing = start === 'describe'
  const create = async () => {
    setBusy(true); setError(null)
    if (describing) {
      try { setJob((await api.describeAgent({ description, name: slug, sample_set: sample || null })).job) }
      catch (e: any) { setError(e.message); setBusy(false) }
      return
    }
    try {
      await api.createAgent({ name: slug, description, start, sample_set: sample || null })
      navigate(`/agents/${slug}`)
    } catch (e: any) { setError(e.message) } finally { setBusy(false) }
  }
  const option = (key: string, icon: string, title: string, text: string, disabled = false) => (
    <button type="button" key={key} disabled={disabled} onClick={() => { setStart(key); if (key === 'describe') setSample(''); else if (!sample) setSample(sets[0] ?? '') }} className="card pad grow"
      style={{ textAlign: 'left', border: start === key ? '2px solid var(--acc)' : undefined, background: start === key ? 'var(--acc-soft)' : '#fff', opacity: disabled ? 0.55 : 1, cursor: disabled ? 'not-allowed' : 'pointer' }}>
      <Icon name={icon} size={20} color={start === key ? 'var(--acc)' : 'var(--muted)'} />
      <strong style={{ fontSize: 14 }}>{title}</strong>
      <span className="muted">{text}</span>
    </button>
  )

  return (
    <div className="page" style={{ alignItems: 'center' }}>
      <div className="card pad" style={{ width: 760, padding: '24px 28px', gap: 16 }}>
        <div className="stack" style={{ gap: 4 }}>
          <h1>New agent</h1>
          <span className="muted" style={{ fontSize: 13 }}>An agent starts on a trigger, works through its steps, then finishes. You can run it on sample data before publishing.</span>
        </div>
        <Block title={describing ? 'Name (optional)' : 'Name'}>
          <input className="input" style={{ fontSize: 14, padding: '8px 10px' }} value={name} onChange={(e) => setName(e.target.value)} placeholder={describing ? 'Leave empty and Claude names it' : 'e.g. vendor-onboarding'} aria-label="Name" />
          {slug && slug !== name && <span className="faint">Saved as <code className="mono">{slug}</code></span>}
        </Block>
        <Block title="What should it do?">
          <textarea className="textarea" rows={describing ? 6 : 2} value={description} onChange={(e) => setDescription(e.target.value)} aria-label="What should it do?"
            placeholder={describing
              ? 'Say what starts it, what it reads, what it should check, and what it changes. For example: every weekday at 8, read water leak alert emails from northpeakwater.com, pull out the property, severity and reading, and add a row per leak to the Leaks sheet. A person approves before anything is added.'
              : 'Find travel bookings in my email, double-check them, and add the trips I approve to my calendar.'} />
          {describing && <span className="faint">Claude sees the workspace's accounts, their permissions and tools, and the sample sets, and drafts every step. You review it in the editor; nothing runs until you do.</span>}
        </Block>
        <Block title="How do you want to start?">
          <div className="row" style={{ alignItems: 'stretch', flexWrap: 'nowrap', gap: 10 }}>
            {option('describe', 'spark', 'Describe it', session?.claude_api ? 'Claude drafts the steps from your description, checked like any agent you save.'
              : 'Claude drafts the steps from your description. Needs the service to have a Claude API key.', !session?.claude_api)}
            {option('blank', 'blank', 'Start blank', 'Add the trigger, connections, record types and steps yourself.')}
          </div>
          <span className="muted" style={{ marginTop: 6 }}>Or start from a vetted pattern:</span>
          <div className="row" style={{ alignItems: 'stretch', flexWrap: 'nowrap', gap: 10 }}>
            {templates.map((t) => option(t.key, 'template', t.name, t.description))}
          </div>
        </Block>
        <Block title="Test data">
          <Select value={sample} options={describing ? ['', ...sets] : sets} onChange={setSample} label="Test data" labels={{ '': 'Let Claude pick' }} />
          <span className="muted">Test runs use sample accounts, never real ones. You can change this in the agent's settings.</span>
        </Block>
        {job && <DraftProgress job={progress} />}
        {error && <div className="notice bad">{error}</div>}
        <div className="row" style={{ justifyContent: 'flex-end' }}>
          <button className="btn" onClick={() => navigate('/')}>Cancel</button>
          <button className="btn primary" disabled={(describing ? !description.trim() : !slug) || busy} onClick={create}>
            {busy ? (describing ? 'Drafting…' : 'Creating…') : describing ? <><Icon name="spark" size={13} color="#fff" />Draft with Claude</> : 'Create agent'}</button>
        </div>
      </div>
    </div>
  )
}
