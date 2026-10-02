// Act: changes something outside the agent, using only checked fields. No model involved.
import type { Json } from '../../api'
import { Area, Block, Chips, FieldErrors, Guarantees, Icon, RefPicker, Segmented, Select, Text } from '../../ui'
import { ArgLimits, StepHeader, TakesEditor, UsesEditor, useMcpTools, useStep } from './common'

export default function Act() {
  const { step, set, p, refs, draft } = useStep()
  const action = step.create_events ? 'create_events' : step.call_tool ? 'call_tool' : step.insert_rows ? 'insert_rows' : step.send_email ? 'send_email' : step.write_object ? 'write_object' : 'add_row'
  const gmailConn = Object.entries(draft.connections ?? {}).find(([, c]: [string, any]) => c.service === 'gmail')?.[0]
  const gcsConn = Object.entries(draft.connections ?? {}).find(([, c]: [string, any]) => c.service === 'gcs')?.[0]
  const wo: Json = step.write_object ?? {}
  const se: Json = step.send_email ?? {}
  const seType = refs.find((r) => r.ref === se.for_each)?.type.replace(/^list of /, '') ?? ''
  const seFields = Object.keys(draft.records?.[seType]?.fields ?? {})
  const mcpConn = Object.entries(draft.connections ?? {}).find(([, c]: [string, any]) => c.service === 'mcp')?.[0]
  const bqConn = Object.entries(draft.connections ?? {}).find(([, c]: [string, any]) => c.service === 'bigquery')?.[0]
  const ir: Json = step.insert_rows ?? {}
  const irType = refs.find((r) => r.ref === ir.for_each)?.type.replace(/^list of /, '') ?? ''
  const irFields = Object.keys(draft.records?.[irType]?.fields ?? {})
  const tools = useMcpTools(action === 'call_tool' ? step.uses?.connection : undefined)
  const ce: Json = step.create_events ?? {}
  const row: Json = step.add_row?.row ?? {}
  const yesNo = Object.entries(draft.run_options ?? {}).filter(([, o]: [string, any]) => o.type === 'yes/no').map(([k]) => `run.${k}`)
  const switchTo = (a: string) => {
    const service = a === 'create_events' ? 'google-calendar' : a === 'call_tool' ? 'mcp' : a === 'insert_rows' ? 'bigquery' : a === 'send_email' ? 'gmail' : a === 'write_object' ? 'gcs' : 'google-sheets'
    const conn = Object.entries(draft.connections ?? {}).find(([, c]: [string, any]) => c.service === service)?.[0] ?? ''
    set(['create_events'], a === 'create_events' ? { calendar: 'Personal', templates: { default: { title: '', starts: '{start}', ends: '{end}' } }, never_twice: { match_fields: [] } } : undefined)
    set(['add_row'], a === 'add_row' ? { sheet: '', row: {} } : undefined)
    set(['call_tool'], a === 'call_tool' ? { tool: '', arguments: {} } : undefined)
    set(['insert_rows'], a === 'insert_rows' ? { table: '', row: {} } : undefined)
    set(['send_email'], a === 'send_email' ? { to: [], subject: '', body: '' } : undefined)
    set(['write_object'], a === 'write_object' ? { path: '', format: 'json' } : undefined)
    set(['uses'], a === 'create_events' ? { connection: conn, actions: ['create_event'], calendar: 'Personal' }
      : a === 'call_tool' ? { connection: conn, actions: [] } : a === 'insert_rows' ? { connection: conn, actions: ['insert_rows'], tables: [] }
      : a === 'send_email' ? { connection: conn, actions: ['send'], recipients: [] }
      : a === 'write_object' ? { connection: conn, actions: ['write_object'], paths: [] }
      : { connection: conn, actions: ['append_row'], sheets: [] })
    set(['takes'], a === 'create_events' ? { records: '' } : a === 'send_email' ? {} : a === 'write_object' ? { content: '' } : undefined)
  }
  const ct: Json = step.call_tool ?? {}
  const actTools = tools.filter((t) => t.treat === 'act')
  const tool = tools.find((t) => t.name === ct.tool)
  const toolArgs: [string, any][] = Object.entries(tool?.input_schema?.properties ?? {})
  const required: string[] = tool?.input_schema?.required ?? []
  const pickTool = (name: string) => {
    set(['call_tool'], { ...ct, tool: name, arguments: {} })
    set(['uses'], { ...(step.uses ?? {}), actions: name ? [name] : [], arg_limits: undefined })
  }
  const ctType = refs.find((r) => r.ref === ct.for_each)?.type.replace(/^list of /, '') ?? ''
  const ctFields = Object.keys(draft.records?.[ctType]?.fields ?? {})
  const templates: Json = ce.templates ?? {}
  const forEach: string | undefined = step.add_row?.for_each
  const listType = refs.find((r) => r.ref === forEach)?.type ?? ''
  const recordType = listType.replace(/^list of /, '')
  const recordFields = Object.keys(draft.records?.[recordType]?.fields ?? {})
  return (
    <>
      <StepHeader note="The only kind of step that changes the outside world, and it takes no text a model wrote: only checked fields." />
      <Block title="Does">
        <Segmented options={['create_events', 'add_row', ...(mcpConn || action === 'call_tool' ? ['call_tool'] : []), ...(bqConn || action === 'insert_rows' ? ['insert_rows'] : []), ...(gmailConn || action === 'send_email' ? ['send_email'] : []), ...(gcsConn || action === 'write_object' ? ['write_object'] : [])]} value={action} onChange={switchTo}
          labels={{ create_events: 'Create calendar events', add_row: 'Add a row to a sheet', call_tool: 'Call a tool', insert_rows: 'Insert rows into BigQuery', send_email: 'Send an email', write_object: 'Write a file (GCS)' }} />
      </Block>
      {action === 'write_object' ? (
        <>
          <Block title="Uses"><UsesEditor actions={['write_object']} limits={['paths']} />
            <span className="faint">It can only add new files under these prefixes: never overwrite or delete one.</span></Block>
          <Block title="Fills in from" aside="content, plus values for the path"><TakesEditor />
            <span className="faint"><code className="mono">content</code> is what goes in the file. Other inputs fill <code className="mono">{'{name}'}</code> in the path, e.g. <code className="mono">load_id: trigger.load_id</code>.</span></Block>
          <Block title="The file">
            <span className="row" style={{ flexWrap: 'nowrap' }}><span className="muted" style={{ width: 60 }}>Path</span>
              <input className="input mono grow" value={wo.path ?? ''} placeholder="bucket/reports/{load_id}/summary.json" aria-label="Path" onChange={(e) => set(['write_object', 'path'], e.target.value)} /></span>
            <span className="row"><span className="muted" style={{ width: 60 }}>As</span>
              <Select value={wo.format ?? 'json'} options={['json', 'jsonl', 'csv', 'text', 'png']} onChange={(v) => set(['write_object', 'format'], v)} label="Format"
                labels={{ json: 'JSON', jsonl: 'JSON lines', csv: 'CSV (from a list of records)', text: 'Text', png: 'A chart (its image)' }} /></span>
            <FieldErrors path={p('write_object')} />
          </Block>
        </>
      ) : action === 'send_email' ? (
        <>
          <Block title="Uses"><UsesEditor actions={['send']} limits={['recipients']} />
            <span className="faint">The gateway refuses any recipient not on this list, whatever the templates say.</span></Block>
          <Block title="Fills in from" aside="named values for the templates"><TakesEditor />
            <span className="faint">Each input is a <code className="mono">{'{name}'}</code> in the email: e.g. <code className="mono">summary: write_report.summary</code>. Lists become bullet lines.</span></Block>
          <Block title="The email">
            <span className="row"><span className="muted" style={{ width: 52 }}>To</span><Chips values={se.to ?? []} onChange={(v) => set(['send_email', 'to'], v)} placeholder="address, or {field}" /></span>
            <span className="row"><span className="muted" style={{ width: 52 }}>Cc</span><Chips values={se.cc ?? []} onChange={(v) => set(['send_email', 'cc'], v.length ? v : undefined)} placeholder="optional" /></span>
            <span className="row" style={{ flexWrap: 'nowrap' }}><span className="muted" style={{ width: 52 }}>Subject</span>
              <input className="input grow" value={se.subject ?? ''} aria-label="Subject" placeholder="e.g. Sales load {load_id}: {headline}" onChange={(e) => set(['send_email', 'subject'], e.target.value)} /></span>
            <Area value={se.body ?? ''} onChange={(v) => set(['send_email', 'body'], v)} rows={7} label="Body" path={p('send_email', 'body')} />
            <span className="row" style={{ alignItems: 'flex-start' }}><span className="muted" style={{ width: 52 }}>Charts</span>
              <span className="stack grow" style={{ gap: 4 }}>
                {(se.charts ?? []).map((ref: string, i: number) => (
                  <span key={i} className="row" style={{ flexWrap: 'nowrap' }}>
                    <RefPicker value={ref} refs={refs.filter((r) => r.type === 'chart')} onChange={(v) => set(['send_email', 'charts', i], v)} />
                    <button className="icon-btn" aria-label="Remove chart" onClick={() => set(['send_email', 'charts'], (se.charts ?? []).filter((_: string, k: number) => k !== i))}><Icon name="x" size={12} /></button>
                  </span>
                ))}
                <button className="link" style={{ alignSelf: 'flex-start' }} onClick={() => set(['send_email', 'charts'], [...(se.charts ?? []), refs.find((r) => r.type === 'chart')?.ref ?? ''])}>
                  <Icon name="plus" size={13} width={2} />Include a chart</button>
              </span></span>
            <span className="row"><span className="muted">One per item of</span>
              <RefPicker value={se.for_each ?? ''} refs={refs.filter((r) => r.type.startsWith('list'))} onChange={(v) => set(['send_email', 'for_each'], v || undefined)} path={p('send_email', 'for_each')} /></span>
            <span className="faint">{se.for_each ? 'One email per item; each item\u2019s fields fill in too.' : 'Leave empty to send one email; pick a list to send one per item.'}</span>
            {seFields.length > 0 && <span className="row" style={{ gap: 4 }}><span className="faint">Fields of {seType}:</span>{seFields.map((f) => <code key={f} className="ref">{`{${f}}`}</code>)}</span>}
            <FieldErrors path={p('send_email')} />
          </Block>
        </>
      ) : action === 'insert_rows' ? (
        <>
          <Block title="Uses"><UsesEditor actions={['insert_rows']} limits={[]} /></Block>
          <Block title="Insert">
            <span className="row"><span className="muted">Into table</span>
              <input className="input mono grow" value={ir.table ?? ''} placeholder="project.dataset.table" aria-label="Table"
                onChange={(e) => { set(['insert_rows', 'table'], e.target.value); set(['uses', 'tables'], e.target.value ? [e.target.value] : []) }} /></span>
            <span className="row"><span className="muted">For each</span>
              <RefPicker value={ir.for_each ?? ''} refs={refs.filter((r) => r.type.startsWith('list'))} onChange={(v) => set(['insert_rows', 'for_each'], v || undefined)} path={p('insert_rows', 'for_each')} /></span>
            <span className="faint">One row per item; columns fill from each item's checked fields. Append only: it never updates or deletes.</span>
            {irFields.length > 0 && <span className="row" style={{ gap: 4 }}><span className="faint">Fields of {irType}:</span>{irFields.map((f) => <code key={f} className="ref">{`{${f}}`}</code>)}</span>}
            {Object.entries(ir.row ?? {}).map(([col, value]) => (
              <span key={col} className="row" style={{ flexWrap: 'nowrap' }}>
                <code className="mono" style={{ width: 130, flex: 'none' }}>{col}</code>
                <input className="input cel grow" value={value as string} aria-label={col} placeholder="{field}" onChange={(e) => set(['insert_rows', 'row', col], e.target.value)} />
                <button className="icon-btn" aria-label={`Remove ${col}`} onClick={() => { const c = { ...ir.row }; delete c[col]; set(['insert_rows', 'row'], c) }}><Icon name="x" size={12} /></button>
              </span>
            ))}
            <span className="row"><input className="input" placeholder="column name" aria-label="Column name"
              onKeyDown={(e) => { const v = (e.target as HTMLInputElement).value.trim(); if (e.key === 'Enter' && v) { set(['insert_rows', 'row', v], `{${v}}`); (e.target as HTMLInputElement).value = '' } }} />
              <span className="faint">Enter adds a column</span></span>
            {irFields.length > 0 && Object.keys(ir.row ?? {}).length === 0 && (
              <button className="link" onClick={() => set(['insert_rows', 'row'], Object.fromEntries(irFields.map((f) => [f, `{${f}}`])))}>
                <Icon name="plus" size={13} width={2} />A column for every field of {irType}</button>
            )}
            <FieldErrors path={p('insert_rows')} />
          </Block>
        </>
      ) : action === 'call_tool' ? (
        <>
          <Block title="Uses">
            <span className="row"><Select value={step.uses?.connection ?? ''} options={Object.entries(draft.connections ?? {}).filter(([, c]: [string, any]) => c.service === 'mcp').map(([k]) => k)}
              onChange={(v) => set(['uses'], { connection: v, actions: [] })} label="Connection" />
              <Select value={ct.tool ?? ''} options={['', ...actTools.map((t) => t.name)]} onChange={pickTool} label="Tool" labels={{ '': 'Pick a tool' }} /></span>
            {tool && <span className="faint">{tool.description}</span>}
            {!actTools.length && <span className="faint">This connector's admin offers no act tools.</span>}
            {tool && tool.limits.length > 0 && (
              <div className="stack" style={{ gap: 7, padding: '8px 10px', background: 'var(--soft)', border: '1px solid var(--line)', borderRadius: 8 }}>
                <span className="faint">Limits, enforced by the service on every call:</span><ArgLimits tools={tools} />
              </div>
            )}
            <FieldErrors path={p('uses')} />
          </Block>
          <Block title="Call it">
            <span className="row"><span className="muted">For each</span>
              <RefPicker value={ct.for_each ?? ''} refs={refs.filter((r) => r.type.startsWith('list'))} onChange={(v) => set(['call_tool', 'for_each'], v || undefined)} path={p('call_tool', 'for_each')} /></span>
            <span className="faint">{ct.for_each ? 'One call per item. Arguments fill in from each item’s checked fields.' : 'Leave empty to call it once; pick a list to call it once per item.'}</span>
            {ctFields.length > 0 && <span className="row" style={{ gap: 4 }}><span className="faint">Fields of {ctType}:</span>{ctFields.map((f) => <code key={f} className="ref">{`{${f}}`}</code>)}</span>}
            {toolArgs.map(([arg, schema]) => (
              <span key={arg} className="row" style={{ flexWrap: 'nowrap' }}>
                <code className="mono" style={{ width: 130, flex: 'none' }}>{arg}{required.includes(arg) ? ' *' : ''}</code>
                <input className="input cel grow" value={ct.arguments?.[arg] ?? ''} aria-label={arg} placeholder={schema.description ?? (ct.for_each ? '{field} or text' : 'text')}
                  onChange={(e) => { const next = { ...(ct.arguments ?? {}), [arg]: e.target.value }; if (!e.target.value) delete next[arg]; set(['call_tool', 'arguments'], next) }} />
              </span>
            ))}
            {tool && !toolArgs.length && <span className="faint">It takes no arguments.</span>}
            <FieldErrors path={p('call_tool')} />
          </Block>
        </>
      ) : <Block title="Uses"><UsesEditor actions={action === 'create_events' ? ['create_event'] : ['append_row']} limits={action === 'create_events' ? ['calendar'] : ['sheets']} /></Block>}
      {action === 'call_tool' || action === 'insert_rows' || action === 'send_email' || action === 'write_object' ? null : action === 'create_events' ? (
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
      <Guarantees items={action === 'send_email' ? ['Only to the recipients listed under Uses, and at most the emails per run set there.',
          'Text a model wrote can go in the body: add an Approve step before this one so a person reads the email first.',
          'Test runs and dry runs put the email in the run\u2019s outbox; nothing is sent.'] : ['Only checked fields go outside the agent, never free text a model wrote.',
        'Runs without a person checking first, unless you add an Approve step before it: your choice.']} />
    </>
  )
}
