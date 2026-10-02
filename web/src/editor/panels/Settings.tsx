// Agent settings: trigger, run options, limits, test data, shared instructions.
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, when, type Connection, type Json, type PubSubStatus } from '../../api'
import { Area, Block, Cel, FieldErrors, Icon, Pill, Segmented, Select, Text } from '../../ui'
import { FieldsEditor } from './common'
import { useEditor } from '../Editor'

export function triggerText(t: Json | undefined): string {
  if (!t) return 'No trigger'
  if (t.kind === 'schedule') return `${({ weekday: 'Weekdays', day: 'Every day', week: 'Weekly' } as Record<string, string>)[t.every] ?? t.every ?? ''} at ${t.at ?? ''}`
  if (t.kind === 'email') return `Email to ${t.to ?? '…'}`
  if (t.kind === 'webhook') return 'When a webhook is called'
  if (t.kind === 'pubsub') return `Pub/Sub: ${(t.subscription ?? '').split('/').pop() || '…'}`
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
        <Segmented options={['schedule', 'email', 'pubsub', 'manual']} value={t.kind} onChange={(k) => update(['trigger'], k === 'schedule' ? { kind: k, every: 'weekday', at: '07:00', time_zone: 'Pacific time' } : k === 'email' ? { kind: k, to: '' }
          : k === 'pubsub' ? { kind: k, subscription: '', message: { table: { type: 'text' } }, sample: { table: '' } } : { kind: k })}
          labels={{ schedule: 'On a schedule', email: 'An email arrives', pubsub: 'A Pub/Sub message', manual: 'Only when I run it' }} />
        {t.kind === 'pubsub' && <PubSubTrigger name={name} t={t} setTrigger={setTrigger} />}
        {t.kind === 'schedule' && (
          <span className="row"><span className="muted">Every</span>
            <Select value={t.every ?? 'weekday'} options={['weekday', 'day', 'week']} onChange={(v) => setTrigger('every', v)} label="Repeat" />
            <span className="muted">at</span><Text width={64} value={t.at} onChange={(v) => setTrigger('at', v)} label="Time" />
            <Select value={t.time_zone ?? 'Pacific time'} options={['Pacific time', 'Mountain time', 'Central time', 'Eastern time', 'UTC']} onChange={(v) => setTrigger('time_zone', v)} label="Time zone" />
          </span>
        )}
        {t.kind === 'email' && <span className="row"><span className="muted">Email to</span><Text width={240} value={t.to} onChange={(v) => setTrigger('to', v)} label="Address" /></span>}
        {t.kind !== 'pubsub' && <span className="faint">Schedules and email triggers don't fire in this prototype; use Run now or Test run.</span>}
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

/** A Pub/Sub trigger: the subscription, the account that pulls, the message's fields, a filter, a sample; and, once
    published, whether it's listening and what arrived. */
function PubSubTrigger({ name, t, setTrigger }: { name: string; t: Json; setTrigger: (k: string, v: unknown) => void }) {
  const [accounts, setAccounts] = useState<Connection[]>([])
  const [status, setStatus] = useState<PubSubStatus | null>(null)
  const [checked, setChecked] = useState<{ ok: boolean; topic?: string; message: string } | null>(null)
  const [sample, setSample] = useState(JSON.stringify(t.sample ?? {}, null, 1))
  const [sampleError, setSampleError] = useState<string | null>(null)
  useEffect(() => { api.connections().then((cs) => setAccounts(cs.filter((c) => c.service === 'bigquery'))) }, [])
  useEffect(() => { const load = () => api.pubsub(name).then(setStatus).catch(() => {}); load(); const id = setInterval(load, 5000); return () => clearInterval(id) }, [name])
  const check = async () => { setChecked(null); try { setChecked(await api.pubsubCheck(name)) } catch (e: any) { setChecked({ ok: false, message: e.message }) } }
  const outcome: Record<string, string> = { started: 'succeeded', skipped: 'stopped', duplicate: 'stopped', failed: 'failed' }
  return (
    <div className="stack" style={{ gap: 8 }}>
      <span className="row" style={{ flexWrap: 'nowrap' }}><span className="muted" style={{ width: 92 }}>Subscription</span>
        <input className="input mono grow" value={t.subscription ?? ''} placeholder="projects/my-project/subscriptions/etl-done" aria-label="Subscription"
          onChange={(e) => setTrigger('subscription', e.target.value.trim())} /></span>
      <span className="row"><span className="muted" style={{ width: 92 }}>Pulls with</span>
        <Select value={t.account ?? ''} options={['', ...accounts.map((a) => a.id)]} labels={{ '': 'Pick an account', ...Object.fromEntries(accounts.map((a) => [a.id, `${a.label} (${a.connector_name ?? 'BigQuery'})`])) }}
          onChange={(v) => setTrigger('account', v || undefined)} label="Account" />
        <button className="btn small" disabled={!t.subscription || !t.account} onClick={check}>Check the subscription</button></span>
      <span className="faint">The account's Google Cloud credentials (from its BigQuery connector) need Pub/Sub Subscriber on the subscription.</span>
      {checked && <span className={checked.ok ? 'faint' : 'field-error'}>{checked.ok ? `Readable. Topic: ${checked.topic}` : checked.message}</span>}
      <span className="eyebrow">The message carries</span>
      <FieldsEditor fields={t.message ?? {}} path={['trigger', 'message']} onChange={(f) => setTrigger('message', f)} choiceOf={false} />
      <span className="faint">Read from its JSON data, then its attributes. Steps use them as <code className="mono">trigger.&lt;field&gt;</code>, with
        {' '}<code className="mono">trigger.message_id</code> and <code className="mono">trigger.published_at</code>.</span>
      <span className="eyebrow">Only when</span>
      <Cel value={t.when} onChange={(v) => setTrigger('when', v || undefined)} path="trigger.when" placeholder="message.table.startsWith('sales_processed.')" />
      <span className="faint">Optional. Messages it's false for are acknowledged and skipped.</span>
      <span className="eyebrow">Sample message, for test runs</span>
      <textarea className="input mono" rows={4} value={sample} aria-label="Sample message"
        onChange={(e) => { setSample(e.target.value); try { setTrigger('sample', JSON.parse(e.target.value || '{}')); setSampleError(null) } catch { setSampleError('Not valid JSON yet.') } }} />
      {sampleError && <span className="field-error">{sampleError}</span>}
      {status && (
        <div className="stack" style={{ gap: 5, padding: '8px 10px', background: 'var(--soft)', border: '1px solid var(--line)', borderRadius: 8 }}>
          <span className="row" style={{ flexWrap: 'nowrap' }}>
            <strong className="grow" style={{ fontSize: 12.5 }}>{status.published_subscription
              ? (status.paused ? 'Paused' : 'Listening') + ` to ${status.published_subscription.split('/').pop()}`
              : 'Not listening: publish the agent to start'}</strong>
            {status.published_subscription && <button className="btn small" onClick={async () => setStatus(await api.pubsubPause(name, !status.paused))}>{status.paused ? 'Resume' : 'Pause'}</button>}
          </span>
          <span className="faint">Only the published version runs on messages, on real accounts.{status.last_pull_at ? ` Last checked ${when(status.last_pull_at)}.` : ''}</span>
          {status.last_error && <span className="field-error">{status.last_error}</span>}
          {status.messages.slice(0, 6).map((m, i) => (
            <span key={i} className="row" style={{ gap: 6, flexWrap: 'nowrap' }}>
              <Pill kind={outcome[m.outcome]}>{m.outcome}</Pill>
              <span className="faint grow" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{when(m.at)} · {m.message_id}{m.detail ? ` · ${m.detail}` : ''}</span>
              {m.run && <a href={`/agents/${name}/runs/${m.run}`}>run {m.run}</a>}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
