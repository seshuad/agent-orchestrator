// New agent: name it, say what it should do, and start blank or from a vetted pattern.
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { Block, Icon, Select } from '../ui'

export default function NewAgent() {
  const navigate = useNavigate()
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
  const create = async () => {
    setBusy(true); setError(null)
    try {
      await api.createAgent({ name: slug, description, start, sample_set: sample || null })
      navigate(`/agents/${slug}`)
    } catch (e: any) { setError(e.message) } finally { setBusy(false) }
  }
  const option = (key: string, icon: string, title: string, text: string, disabled = false) => (
    <button type="button" key={key} disabled={disabled} onClick={() => setStart(key)} className="card pad grow"
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
        <Block title="Name">
          <input className="input" style={{ fontSize: 14, padding: '8px 10px' }} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. vendor-onboarding" aria-label="Name" />
          {slug && slug !== name && <span className="faint">Saved as <code className="mono">{slug}</code></span>}
        </Block>
        <Block title="What should it do?">
          <textarea className="textarea" rows={2} value={description} onChange={(e) => setDescription(e.target.value)} aria-label="What should it do?"
            placeholder="Find travel bookings in my email, double-check them, and add the trips I approve to my calendar." />
        </Block>
        <Block title="How do you want to start?">
          <div className="row" style={{ alignItems: 'stretch', flexWrap: 'nowrap', gap: 10 }}>
            {option('describe', 'spark', 'Describe it', 'A model drafts the steps from your description. Coming next: it needs the Claude API.', true)}
            {option('blank', 'blank', 'Start blank', 'Add the trigger, connections, record types and steps yourself.')}
          </div>
          <span className="muted" style={{ marginTop: 6 }}>Or start from a vetted pattern:</span>
          <div className="row" style={{ alignItems: 'stretch', flexWrap: 'nowrap', gap: 10 }}>
            {templates.map((t) => option(t.key, 'template', t.name, t.description))}
          </div>
        </Block>
        <Block title="Test data">
          <Select value={sample} options={sets} onChange={setSample} label="Test data" />
          <span className="muted">Test runs use sample accounts, never real ones. You can change this in the agent's settings.</span>
        </Block>
        {error && <div className="notice bad">{error}</div>}
        <div className="row" style={{ justifyContent: 'flex-end' }}>
          <button className="btn" onClick={() => navigate('/')}>Cancel</button>
          <button className="btn primary" disabled={!slug || busy} onClick={create}>{busy ? 'Creating…' : 'Create agent'}</button>
        </div>
      </div>
    </div>
  )
}
