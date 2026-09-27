// Agent settings: trigger, run options, limits, test data, shared instructions.
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type Json } from '../../api'
import { Area, Block, FieldErrors, Icon, Segmented, Select, Text } from '../../ui'
import { useEditor } from '../Editor'

export function triggerText(t: Json | undefined): string {
  if (!t) return 'No trigger'
  if (t.kind === 'schedule') return `${({ weekday: 'Weekdays', day: 'Every day', week: 'Weekly' } as Record<string, string>)[t.every] ?? t.every ?? ''} at ${t.at ?? ''}`
  if (t.kind === 'email') return `Email to ${t.to ?? '…'}`
  if (t.kind === 'webhook') return 'When a webhook is called'
  return 'Only when run manually'
}

export default function Settings() {
  const { draft, update, meta, setTestData, name } = useEditor()
  const navigate = useNavigate()
  const [newName, setNewName] = useState(name)
  const [renaming, setRenaming] = useState(false)
  const [renameError, setRenameError] = useState<string | null>(null)
  useEffect(() => { setNewName(name); setRenameError(null) }, [name])
  const slug = newName.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
  const rename = async () => {
    setRenaming(true); setRenameError(null)
    try {
      await api.saveAgent(name, draft)                // keep edits the autosave hasn't sent yet
      await api.renameAgent(name, slug)
      navigate(`/agents/${slug}`, { replace: true })
    } catch (e: any) { setRenameError(e.message) } finally { setRenaming(false) }
  }
  const [sets, setSets] = useState<string[]>([])
  useEffect(() => { api.sampleSets().then(setSets) }, [])
  const t = draft.trigger ?? { kind: 'manual' }
  const opts: Json = draft.run_options ?? {}
  const shared: Json = draft.shared_instructions ?? {}
  const setTrigger = (k: string, v: unknown) => update(['trigger'], { ...t, [k]: v })

  return (
    <>
      <div className="stack" style={{ gap: 2 }}>
        <span className="eyebrow">Agent settings</span>
        <span style={{ fontSize: 17, fontWeight: 700 }}>{draft.name}</span>
      </div>
      <Block title="Name">
        <span className="row" style={{ flexWrap: 'nowrap' }}>
          <input className="input grow" value={newName} onChange={(e) => setNewName(e.target.value)} aria-label="Agent name"
            onKeyDown={(e) => { if (e.key === 'Enter' && slug && slug !== name) rename() }} />
          <button className="btn small" disabled={!slug || slug === name || renaming} onClick={rename}>{renaming ? 'Renaming…' : 'Rename'}</button>
        </span>
        {slug && slug !== newName.trim() && slug !== name && <span className="faint">Saved as <code className="mono">{slug}</code></span>}
        <span className="faint">Renaming keeps its published versions, runs and tests. Not while one of its runs is in progress.</span>
        {renameError && <span className="field-error">{renameError}</span>}
      </Block>
      <Block title="Description"><Area value={draft.description} onChange={(v) => update(['description'], v)} rows={2} path="description" label="Description" /></Block>
      <Block title="Starts when">
        <Segmented options={['schedule', 'email', 'webhook', 'manual']} value={t.kind} onChange={(k) => update(['trigger'], k === 'schedule' ? { kind: k, every: 'weekday', at: '07:00', time_zone: 'Pacific time' } : k === 'email' ? { kind: k, to: '' } : { kind: k })}
          labels={{ schedule: 'On a schedule', email: 'An email arrives', webhook: 'A webhook is called', manual: 'Only when I run it' }} />
        {t.kind === 'schedule' && (
          <span className="row"><span className="muted">Every</span>
            <Select value={t.every ?? 'weekday'} options={['weekday', 'day', 'week']} onChange={(v) => setTrigger('every', v)} label="Repeat" />
            <span className="muted">at</span><Text width={64} value={t.at} onChange={(v) => setTrigger('at', v)} label="Time" />
            <Select value={t.time_zone ?? 'Pacific time'} options={['Pacific time', 'Mountain time', 'Central time', 'Eastern time', 'UTC']} onChange={(v) => setTrigger('time_zone', v)} label="Time zone" />
          </span>
        )}
        {t.kind === 'email' && <span className="row"><span className="muted">Email to</span><Text width={240} value={t.to} onChange={(v) => setTrigger('to', v)} label="Address" /></span>}
        <span className="faint">Schedules and email triggers don't fire in this prototype; use Run now or Test run.</span>
        <FieldErrors path="trigger" />
      </Block>
      <Block title="Run options">
        <span className="muted">Values a run starts with. Steps can use them, like Act steps following dry run.</span>
        {Object.entries(opts).map(([name, o]: [string, any]) => (
          <div key={name} className="op">
            <span className="row" style={{ gap: 6 }}>
              <code className="mono" style={{ fontWeight: 600, width: 110 }}>{name}</code>
              <Select value={o.type} options={['yes/no', 'text', 'number']} label="Type"
                onChange={(v) => update(['run_options', name], { ...o, type: v, default: v === 'yes/no' ? true : v === 'number' ? (Number.isFinite(Number(o.default)) && o.default !== '' ? Number(o.default) : undefined) : String(o.default ?? '') })} />
              {o.type === 'yes/no'
                ? <Select value={String(o.default ?? true)} options={['true', 'false']} labels={{ true: 'Default: yes', false: 'Default: no' }} onChange={(v) => update(['run_options', name, 'default'], v === 'true')} label="Default" />
                : <Text width={140} value={o.default === undefined || o.default === null ? '' : String(o.default)} placeholder="Default"
                    onChange={(v) => update(['run_options', name, 'default'],
                      o.type === 'number' ? (v.trim() === '' ? undefined : Number.isFinite(Number(v)) ? Number(v) : v) : v)} />}
              <button className="icon-btn" aria-label={`Remove ${name}`} onClick={() => { const c = { ...opts }; delete c[name]; update(['run_options'], c) }}><Icon name="x" size={12} /></button>
            </span>
            <input className="input full" placeholder="What it's for" value={o.description ?? ''} aria-label="Description" onChange={(e) => update(['run_options', name, 'description'], e.target.value)} />
          </div>
        ))}
        <button className="link" onClick={() => { let n = 1; while (opts[`option_${n}`]) n++; update(['run_options', `option_${n}`], { type: 'text', default: '' }) }}><Icon name="plus" size={13} width={2} />Add run option</button>
      </Block>
      <Block title="Limits">
        <span className="row"><span className="muted">Spend up to $</span><Text width={64} value={draft.limits?.budget_usd} onChange={(v) => update(['limits', 'budget_usd'], Number(v) || 0)} path="limits.budget_usd" label="Budget" /><span className="muted">per run</span></span>
        <span className="row"><span className="muted">Stop after</span><Text width={52} value={draft.limits?.timeout_minutes ?? ''} onChange={(v) => update(['limits', 'timeout_minutes'], v ? Number(v) : undefined)} label="Minutes" /><span className="muted">minutes</span></span>
        <span className="muted">If a limit is reached, the run stops and nothing outside the agent changes.</span>
      </Block>
      <Block title="Test data">
        <Select value={meta.sample_set ?? ''} options={['', ...sets]} labels={{ '': 'None: pick one to run this agent' }} onChange={(v) => setTestData(v || null)} label="Test data" />
        <span className="muted">Runs use sample accounts, never real ones.{meta.replay ? ' This agent also has scripted answers for testing without the Claude API.' : ''}</span>
      </Block>
      <Block title="Shared instructions" aside="used by several Ask steps">
        {Object.entries(shared).map(([name, text]) => (
          <div key={name} className="stack" style={{ gap: 4 }}>
            <span className="spread"><strong style={{ fontSize: 12 }}>{name}</strong><button className="link" style={{ color: 'var(--faint)' }} onClick={() => { const c = { ...shared }; delete c[name]; update(['shared_instructions'], c) }}>remove</button></span>
            <Area value={text as string} onChange={(v) => update(['shared_instructions', name], v)} rows={3} label={name} />
          </div>
        ))}
        <button className="link" onClick={() => { let n = 1; while (shared[`Instructions ${n}`]) n++; update(['shared_instructions', `Instructions ${n}`], '') }}><Icon name="plus" size={13} width={2} />Add shared instructions</button>
      </Block>
    </>
  )
}
