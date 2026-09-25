// Act: changes something outside the agent, using only checked fields. No model involved.
import type { Json } from '../../api'
import { Block, Chips, FieldErrors, Guarantees, Icon, RefPicker, Segmented, Select, Text } from '../../ui'
import { StepHeader, UsesEditor, useStep } from './common'

export default function Act() {
  const { step, set, p, refs, draft } = useStep()
  const action = step.create_events ? 'create_events' : 'add_row'
  const ce: Json = step.create_events ?? {}
  const row: Json = step.add_row?.row ?? {}
  const yesNo = Object.entries(draft.run_options ?? {}).filter(([, o]: [string, any]) => o.type === 'yes/no').map(([k]) => `run.${k}`)
  const switchTo = (a: string) => {
    const service = a === 'create_events' ? 'google-calendar' : 'google-sheets'
    const conn = Object.entries(draft.connections ?? {}).find(([, c]: [string, any]) => c.service === service)?.[0] ?? ''
    set(['create_events'], a === 'create_events' ? { calendar: 'Personal', templates: { default: { title: '', starts: '{start}', ends: '{end}' } }, never_twice: { match_fields: [] } } : undefined)
    set(['add_row'], a === 'add_row' ? { sheet: '', row: {} } : undefined)
    set(['uses'], a === 'create_events' ? { connection: conn, actions: ['create_event'], calendar: 'Personal' } : { connection: conn, actions: ['append_row'], sheets: [] })
    set(['takes'], a === 'create_events' ? { records: '' } : undefined)
  }
  const templates: Json = ce.templates ?? {}
  const forEach: string | undefined = step.add_row?.for_each
  const listType = refs.find((r) => r.ref === forEach)?.type ?? ''
  const recordType = listType.replace(/^list of /, '')
  const recordFields = Object.keys(draft.records?.[recordType]?.fields ?? {})
  return (
    <>
      <StepHeader note="The only kind of step that changes the outside world, and it takes no text a model wrote: only checked fields." />
      <Block title="Does">
        <Segmented options={['create_events', 'add_row']} value={action} onChange={switchTo} labels={{ create_events: 'Create calendar events', add_row: 'Add a row to a sheet' }} />
      </Block>
      <Block title="Uses"><UsesEditor actions={action === 'create_events' ? ['create_event'] : ['append_row']} limits={action === 'create_events' ? ['calendar'] : ['sheets']} /></Block>
      {action === 'create_events' ? (
        <>
          <Block title="For each"><RefPicker value={step.takes?.records ?? ''} refs={refs} onChange={(v) => set(['takes', 'records'], v)} path={p('takes', 'records')} />
            <span className="faint">e.g. <code className="mono">approve_trips.approved[*].bookings</code>: every booking in the approved trips.</span></Block>
          <Block title="Fill in the event" aside="checked fields only">
            <span className="row"><span className="muted">On calendar</span><Text width={140} value={ce.calendar} onChange={(v) => set(['create_events', 'calendar'], v)} label="Calendar" /></span>
            {Object.entries(templates).map(([type, t]: [string, any]) => (
              <div key={type} className="op">
                <span className="spread"><span className="muted">When type is <strong>{type}</strong></span>
                  <button className="link" style={{ color: 'var(--faint)' }} onClick={() => { const c = { ...templates }; delete c[type]; set(['create_events', 'templates'], c) }}>remove</button></span>
                {['title', 'starts', 'ends', 'details'].map((k) => (
                  <span key={k} className="row" style={{ flexWrap: 'nowrap' }}><span className="muted" style={{ width: 52 }}>{k}</span>
                    <input className="input cel grow" value={t[k] ?? ''} aria-label={k} onChange={(e) => set(['create_events', 'templates', type, k], e.target.value)} /></span>
                ))}
              </div>
            ))}
            <span className="row"><input className="input" placeholder="record type, e.g. hotel" aria-label="Record type"
              onKeyDown={(e) => { const v = (e.target as HTMLInputElement).value.trim(); if (e.key === 'Enter' && v) { set(['create_events', 'templates', v], { title: '', starts: '{start}', ends: '{end}' }); (e.target as HTMLInputElement).value = '' } }} />
              <span className="faint">Enter adds a template. <code className="mono">{'{field}'}</code> fills a field in.</span></span>
          </Block>
          <Block title="Never add twice">
            <span className="muted">Skip what this agent already added, and events already on the calendar that day whose title contains:</span>
            <Chips values={ce.never_twice?.match_fields ?? []} onChange={(v) => set(['create_events', 'never_twice'], { match_fields: v })} placeholder="Add a field" />
          </Block>
        </>
      ) : (
        <Block title="The row">
          <span className="row"><span className="muted">Sheet</span><Text width={160} value={step.add_row?.sheet} onChange={(v) => set(['add_row', 'sheet'], v)} label="Sheet" /></span>
          <span className="row"><span className="muted">For each</span>
            <RefPicker value={step.add_row?.for_each ?? ''} refs={refs.filter((r) => r.type.startsWith('list'))} onChange={(v) => set(['add_row', 'for_each'], v || undefined)} path={p('add_row', 'for_each')} /></span>
          <span className="faint">{forEach ? 'One row per item. Columns fill in from each item\u2019s checked fields.' : 'Leave empty to add a single row; pick a list to add one row per item.'}</span>
          {forEach && recordFields.length > 0 && (
            <span className="row" style={{ gap: 4 }}><span className="faint">Fields of {recordType}:</span>
              {recordFields.map((f) => <code key={f} className="ref">{`{${f}}`}</code>)}</span>
          )}
          {Object.entries(row).map(([col, value]) => (
            <span key={col} className="row" style={{ flexWrap: 'nowrap' }}>
              <code className="mono" style={{ width: 130, flex: 'none' }}>{col}</code>
              {forEach
                ? <input className="input cel grow" value={value as string} aria-label={col} placeholder="{field}" onChange={(e) => set(['add_row', 'row', col], e.target.value)} />
                : <RefPicker value={value as string} refs={refs} onChange={(v) => set(['add_row', 'row', col], v)} path={p('add_row', 'row', col)} />}
              <button className="icon-btn" aria-label={`Remove ${col}`} onClick={() => { const c = { ...row }; delete c[col]; set(['add_row', 'row'], c) }}><Icon name="x" size={12} /></button>
            </span>
          ))}
          <span className="row"><input className="input" placeholder="column name" aria-label="Column name"
            onKeyDown={(e) => { const v = (e.target as HTMLInputElement).value.trim(); if (e.key === 'Enter' && v) { set(['add_row', 'row', v.replace(/\s+/g, '_')], forEach ? `{${v.replace(/\s+/g, '_')}}` : ''); (e.target as HTMLInputElement).value = '' } }} />
            <span className="faint">Enter adds a column</span></span>
          {forEach && recordFields.length > 0 && Object.keys(row).length === 0 && (
            <button className="link" onClick={() => set(['add_row', 'row'], Object.fromEntries(recordFields.map((f) => [f, `{${f}}`])))}>
              <Icon name="plus" size={13} width={2} />A column for every field of {recordType}</button>
          )}
        </Block>
      )}
      <Block title="Dry run">
        <span className="row"><span className="muted">Follows the run option</span><Select value={step.follows_dry_run ?? ''} options={['', ...yesNo, ...(step.follows_dry_run && !yesNo.includes(step.follows_dry_run) ? [step.follows_dry_run] : [])]}
          onChange={(v) => set(['follows_dry_run'], v || undefined)} label="Dry run option"
          labels={{ '': 'None: always makes the changes', ...(step.follows_dry_run && !yesNo.includes(step.follows_dry_run) ? { [step.follows_dry_run]: `${step.follows_dry_run} (removed)` } : {}) }} /></span>
        <span className="muted">{step.follows_dry_run ? 'On a dry run it lists what it would do, and changes nothing.' : 'Every run makes the changes. Pick a yes/no run option to allow dry runs.'}</span>
        <FieldErrors path={p('follows_dry_run')} />
      </Block>
      <Guarantees items={['Only checked fields go outside the agent, never free text a model wrote.',
        'Runs without a person checking first, unless you add an Approve step before it: your choice.']} />
    </>
  )
}
