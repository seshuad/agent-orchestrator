// Built-in step: no model. Three engines (CEL operators over a list, JavaScript, a BigQuery query), plus charts and a
// few fixed operations. Every CEL expression is checked on every save.
import { useState } from 'react'
import { api, type Json } from '../../api'
import { Block, Cel, Check, FieldErrors, Icon, RefPicker, Segmented, Select, Text } from '../../ui'
import { FieldsEditor, StepHeader, TakesEditor, UsesEditor, useStep } from './common'

const OTHER = ['chart', 'lookup', 'filter-rows', 'compare', 'three-way-match', 'show', 'tidy'] as const
const ENGINE_OF = (op: string) => (op === 'cel' ? 'cel' : op === 'javascript' ? 'javascript' : op === 'bigquery' ? 'bigquery' : 'other')
const OP_LABEL: Record<string, string> = { cel: 'CEL rules', tidy: 'Tidy up (retired)', lookup: 'Look up', 'filter-rows': 'Filter rows', compare: 'Compare', 'three-way-match': 'Three-way match', show: 'Show value', javascript: 'JavaScript', bigquery: 'BigQuery query', chart: 'Chart' }
const OP_TAKES: Record<string, Json> = {
  tidy: { records: '' }, lookup: { any_of: [] }, 'filter-rows': { equals: '' }, compare: { value: '', on_file: '' },
  'three-way-match': { invoice: '', purchase_order: '', receipts: '' }, show: { value: '' }, javascript: { items: '' }, bigquery: {}, chart: { rows: '' }, cel: { items: '' },
}
const JS_TEMPLATE = `// \`inputs\` holds what this step takes, by name (see Takes).
// Return an object with every field listed under Returns.
const items = inputs.items ?? [];
return { count: items.length };`
const OP_DEFAULT: Record<string, Json> = {
  tidy: [], lookup: { sheet: '', column: '', as: 'row' }, 'filter-rows': { sheet: '', column: '', as: 'rows' }, compare: {}, 'three-way-match': {}, show: {},
  javascript: { code: JS_TEMPLATE },
  bigquery: { sql: 'SELECT column, COUNT(*) AS n\nFROM `project.dataset.table`\nWHERE column = @value\nGROUP BY column\nORDER BY n DESC' },
  chart: { kind: 'bar', x: '', y: '', title: '' },
  cel: [{ keep: '' }],
}

/** A fixed BigQuery query: the SQL, the step's Takes as @parameters, its limits, and a free cost estimate. */
function BigQueryQuery() {
  const { step, set, p, name, draft } = useStep()
  const sql: string = step.operation.bigquery?.sql ?? ''
  const [est, setEst] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const params = Object.fromEntries(Object.entries(step.takes ?? {}).map(([k, v]: [string, any]) => {
    const opt = typeof v === 'string' && v.startsWith('run.') ? draft.run_options?.[v.slice(4)] : null
    return [k, opt?.default ?? null]
  }))
  const estimate = async () => {
    setBusy(true); setEst(null)
    try { setEst(await api.bqEstimate(name, { sql, connection: step.uses?.connection ?? '', params, datasets: step.uses?.datasets, max_bytes: step.uses?.max_bytes })) }
    catch (e: any) { setEst({ ok: false, error: e.message }) } finally { setBusy(false) }
  }
  return (
    <>
      <Block title="Uses"><UsesEditor actions={['query']} limits={['datasets', 'max_bytes', 'max_rows']} /></Block>
      <Block title="SQL" aside="GoogleSQL; a single SELECT">
        <textarea className="textarea cel js-code" rows={Math.min(20, Math.max(6, sql.split('\n').length + 1))} value={sql} spellCheck={false}
          onChange={(e) => set(['operation', 'bigquery', 'sql'], e.target.value)} aria-label="SQL" />
        <FieldErrors path={p('operation', 'bigquery')} />
        <span className="faint">Name tables in full: <code className="mono">`project.dataset.table`</code>. Each of Takes is a parameter: <code className="mono">@name</code>.
          No model writes this query, so the same inputs always run the same SQL. Before it runs, the service checks it reads only the data above and stays under the byte limit.</span>
        <span className="row" style={{ gap: 12 }}>
          <button className="btn small" disabled={busy || !sql.trim() || !step.uses?.connection} onClick={estimate}><Icon name="database" size={12} />{busy ? 'Estimating…' : 'Estimate cost'}</button>
          <span className="faint">A free dry run on the real data{Object.keys(params).length ? ', with run options at their defaults' : ''}.</span>
        </span>
        {est && (est.ok
          ? <div className="stack" style={{ gap: 3 }}>
              <span className="muted">Reads {est.tables.join(', ') || 'no tables'} · scans <strong>{est.human}</strong> · about <strong>${est.cost_usd.toFixed(4)}</strong> · limit {est.limit}</span>
              {est.problems.map((x: string, i: number) => <span key={i} className="field-error">{x}</span>)}
              {!est.problems.length && <span className="faint">Within this step's limits.</span>}
            </div>
          : <span className="field-error">{est.error}</span>)}
      </Block>
      <Block title="Returns" aside="rows, row_count, truncated, bytes_billed, cost_usd">
        <span className="faint">Give <code className="mono">rows</code> a record type so later steps can pick its fields.</span>
        <FieldsEditor fields={step.returns ?? {}} path={[...p().split('.'), 'returns']} onChange={(f) => set(['returns'], f)} />
      </Block>
    </>
  )
}

/** A JavaScript step: the code, and trying it on sample inputs (or the inputs this step had in the latest run). */
function JavaScript() {
  const { step, set, p, name } = useStep()
  const code: string = step.operation.javascript?.code ?? ''
  const blank = Object.fromEntries(Object.keys(step.takes ?? {}).map((k) => [k, null]))
  const [sample, setSample] = useState(JSON.stringify(blank, null, 1))
  const [result, setResult] = useState<{ ok: boolean; output?: unknown; error?: string; took: number } | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const tab = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key !== 'Tab') return
    e.preventDefault()
    const t = e.currentTarget, a = t.selectionStart, b = t.selectionEnd
    set(['operation', 'javascript', 'code'], code.slice(0, a) + '  ' + code.slice(b))
    requestAnimationFrame(() => { t.selectionStart = t.selectionEnd = a + 2 })
  }
  const tryIt = async () => {
    setBusy(true); setResult(null)
    try {
      const inputs = JSON.parse(sample || '{}')
      setResult(await api.tryJs(code, inputs, Object.keys(step.returns ?? {})))
    } catch (e: any) { setResult({ ok: false, error: e instanceof SyntaxError ? `The sample inputs aren't valid JSON: ${e.message}` : e.message, took: 0 }) }
    finally { setBusy(false) }
  }
  const fromLatestRun = async () => {
    setNote(null)
    try {
      for (const r of (await api.runs(name)).slice(0, 20)) {
        const d = await api.inspectStep(r.id, step.id, 0).catch(() => null)
        if (d?.inputs) { setSample(JSON.stringify(d.inputs, null, 1)); setNote(`The inputs this step had in the run of ${new Date(r.started_at * 1000).toLocaleString()}.`); return }
      }
      setNote('No run of this step has recorded inputs yet. Run the agent once, or type sample inputs.')
    } catch (e: any) { setNote(e.message) }
  }
  return (
    <>
      <Block title="Code" aside="runs in QuickJS: no files, network or other programs">
        <textarea className="textarea cel js-code" rows={Math.min(24, Math.max(8, code.split('\n').length + 1))} value={code} spellCheck={false}
          onChange={(e) => set(['operation', 'javascript', 'code'], e.target.value)} onKeyDown={tab} aria-label="JavaScript" />
        <FieldErrors path={p('operation', 'javascript')} />
        <span className="faint">The body of a function: <code className="mono">inputs.&lt;name&gt;</code> is each of Takes; <code className="mono">return {'{'} … {'}'}</code> every field in Returns.
          Standard JavaScript (arrays, dates, JSON, Math); 2 seconds and 64 MB per run. The same inputs always give the same result, except for <code className="mono">Date.now()</code>.</span>
      </Block>
      <Block title="Try it" aside="the same sandbox and limits as a run">
        <textarea className="textarea cel" rows={Math.min(12, Math.max(3, sample.split('\n').length))} value={sample} spellCheck={false}
          onChange={(e) => setSample(e.target.value)} aria-label="Sample inputs" />
        <span className="row" style={{ gap: 12 }}>
          <button className="btn small" disabled={busy || !code.trim()} onClick={tryIt}><Icon name="play" size={11} width={2} />{busy ? 'Running…' : 'Try it'}</button>
          <button className="link" onClick={fromLatestRun}>Use this step's inputs from the latest run</button>
        </span>
        {note && <span className="faint">{note}</span>}
        {result && (result.ok
          ? <><span className="faint">Returned in {Math.round(result.took * 1000)} ms:</span><div className="pre" style={{ maxHeight: 280 }}>{JSON.stringify(result.output, null, 1)}</div></>
          : <span className="field-error">{result.error}</span>)}
      </Block>
      <Block title="Returns" aside="what later steps can pick"><FieldsEditor fields={step.returns ?? {}} path={[...p().split('.'), 'returns']} onChange={(f) => set(['returns'], f)} /></Block>
    </>
  )
}
const TIDY_OPS: Record<string, { icon: string; label: string; make: (records: string[]) => Json }> = {
  check: { icon: 'check', label: 'Check records', make: (r) => ({ check: r[0] ?? '' }) },
  remove_duplicates: { icon: 'copy', label: 'Remove duplicates', make: (r) => ({ remove_duplicates: { same: `${r[0] ?? ''} identity`, keep_highest: "b.confidence == 'high' ? 3 : 1" } }) },
  filter: { icon: 'filter', label: 'Filter', make: () => ({ filter: { keep: 'b.end > run.started' } }) },
  group: { icon: 'group', label: 'Group', make: () => ({ group: { into: 'Group', together: 'a.confirmation == b.confirmation' } }) },
  flag: { icon: 'flag', label: 'Flag', make: () => ({ flag: [{ when: 'size(trip.bookings) == 1', note: '' }] }) },
}

function Tidy() {
  const { step, set, p, draft } = useStep()
  const ops: Json[] = step.operation.tidy ?? []
  const records = Object.keys(draft.records ?? {})
  const at = (i: number, v: Json) => set(['operation', 'tidy'], ops.map((o, j) => (j === i ? v : o)))
  const move = (i: number, d: number) => { const l = [...ops]; const [x] = l.splice(i, 1); l.splice(i + d, 0, x); set(['operation', 'tidy'], l) }
  return (
    <div className="stack" style={{ gap: 6 }}>
      {ops.map((op, i) => {
        const [kind, conf] = Object.entries(op)[0] as [string, any]
        const base = p('operation', 'tidy', i)
        return (
          <div key={i} className="op">
            <span className="row" style={{ gap: 8, flexWrap: 'nowrap' }}>
              <span className="numbered">{i + 1}</span><Icon name={TIDY_OPS[kind]?.icon ?? 'layers'} size={14} color="var(--muted)" />
              <strong className="grow">{TIDY_OPS[kind]?.label ?? kind}</strong>
              <button className="icon-btn" aria-label="Move up" disabled={i === 0} onClick={() => move(i, -1)}><Icon name="up" size={12} /></button>
              <button className="icon-btn" aria-label="Move down" disabled={i === ops.length - 1} onClick={() => move(i, 1)}><Icon name="down" size={12} /></button>
              <button className="icon-btn" aria-label="Remove operation" onClick={() => set(['operation', 'tidy'], ops.filter((_, j) => j !== i))}><Icon name="x" size={12} /></button>
            </span>
            {kind === 'check' && <span className="row"><span className="muted">Drop any that don't match</span><Select value={conf} options={records} onChange={(v) => at(i, { check: v })} label="Record type" /></span>}
            {kind === 'remove_duplicates' && (
              <>
                <span className="row"><span className="muted">Same identity as</span>
                  <Select value={String(conf.same ?? '').replace(' identity', '')} options={records.filter((r) => draft.records[r].identity)} onChange={(v) => at(i, { remove_duplicates: { ...conf, same: `${v} identity` } })} label="Record type" /></span>
                <span className="muted">Keep the one scoring highest on:</span>
                <Cel value={conf.keep_highest} onChange={(v) => at(i, { remove_duplicates: { ...conf, keep_highest: v } })} path={`${base}.remove_duplicates.keep_highest`} />
              </>
            )}
            {kind === 'filter' && <><span className="muted">Keep records where (<code className="mono">b</code> is a record, <code className="mono">run.started</code> when the run began):</span>
              <Cel value={conf.keep} onChange={(v) => at(i, { filter: { keep: v } })} path={`${base}.filter.keep`} /></>}
            {kind === 'group' && (
              <>
                <span className="row"><span className="muted">Into</span><Text width={120} value={conf.into} onChange={(v) => at(i, { group: { ...conf, into: v } })} label="Group name" /><span className="muted">when two records <code className="mono">a</code>, <code className="mono">b</code> belong together:</span></span>
                <Cel value={conf.together} onChange={(v) => at(i, { group: { ...conf, together: v } })} path={`${base}.group.together`} />
              </>
            )}
            {kind === 'flag' && (
              <>
                <span className="muted">Checked on every group (<code className="mono">trip</code>); a matching rule adds its note.</span>
                {(conf as Json[]).map((r, n) => (
                  <div key={n} className="stack" style={{ gap: 4, padding: '7px 9px', background: 'var(--soft)', border: '1px solid var(--line)', borderRadius: 7 }}>
                    <span className="spread"><span className="muted">When</span><button className="link" style={{ color: 'var(--faint)' }} onClick={() => at(i, { flag: conf.filter((_: Json, k: number) => k !== n) })}>remove</button></span>
                    <Cel value={r.when} onChange={(v) => at(i, { flag: conf.map((x: Json, k: number) => (k === n ? { ...x, when: v } : x)) })} path={`${base}.flag.${n}.when`} />
                    <span className="row" style={{ flexWrap: 'nowrap' }}><span className="muted">flag</span>
                      <input className="input full" value={r.note} aria-label="Note" onChange={(e) => at(i, { flag: conf.map((x: Json, k: number) => (k === n ? { ...x, note: e.target.value } : x)) })} /></span>
                  </div>
                ))}
                <button className="link" onClick={() => at(i, { flag: [...conf, { when: '', note: '' }] })}><Icon name="plus" size={13} width={2} />Add rule</button>
              </>
            )}
          </div>
        )
      })}
      <span className="row" style={{ gap: 10 }}>
        <span className="muted">Add:</span>
        {Object.entries(TIDY_OPS).map(([k, o]) => <button key={k} className="link" onClick={() => set(['operation', 'tidy'], [...ops, o.make(records)])}>{o.label}</button>)}
      </span>
      <span className="faint">Rules can use <code className="mono">is_me(name, run.my_name)</code>, <code className="mono">norm()</code>, <code className="mono">date_of()</code> and the CEL standard library.</span>
    </div>
  )
}

export default function BuiltIn() {
  const { step, set, p, inFreeForm: inside, draft } = useStep()
  const op = Object.keys(step.operation ?? {})[0] ?? 'lookup'
  const conf = step.operation?.[op] ?? {}
  const setOp = (next: string) => {
    set(['operation'], { [next]: OP_DEFAULT[next] }); set(['takes'], OP_TAKES[next])
    set(['returns'], next === 'javascript' ? { count: { type: 'number' } } : next === 'bigquery' ? { rows: { type: 'text' } } : undefined)
    if (next === 'bigquery') {
      const conn = Object.entries(draft.connections ?? {}).find(([, c]: [string, any]) => c.service === 'bigquery')?.[0]
      set(['uses'], conn ? { connection: conn, actions: ['query'], max_bytes: '1GB' } : undefined)
    } else if (step.operation && 'bigquery' in step.operation) set(['uses'], undefined)
  }
  return (
    <>
      <StepHeader />
      <Block title="Engine">
        <Segmented options={['cel', 'javascript', 'bigquery', 'other']} value={ENGINE_OF(op)}
          onChange={(e) => { if (e !== ENGINE_OF(op)) setOp(e === 'other' ? 'chart' : e) }}
          labels={{ cel: 'CEL rules', javascript: 'JavaScript', bigquery: 'BigQuery SQL', other: 'Other' }} />
        <span className="faint">{({ cel: 'Operators over a list: keep, add fields, check, remove duplicates, sort, summarize, match, link. You write one small rule per operator; it does the iterating. No code, always ends, checked as you type.',
          javascript: 'Your own function, in a sandbox, for logic the CEL operators can\u2019t express.',
          bigquery: 'One fixed SELECT on your warehouse, checked and capped before it runs.',
          other: 'A chart, a sheet look-up, or a fixed operation.' } as Record<string, string>)[ENGINE_OF(op)]}</span>
        {ENGINE_OF(op) === 'other' && <Segmented options={OTHER.filter((o) => o !== 'tidy' || op === 'tidy')} value={op as (typeof OTHER)[number]} onChange={setOp} labels={OP_LABEL} />}
        {op === 'tidy' && <span className="faint">Tidy up is retired: use CEL rules for checks, duplicates and filters, or JavaScript. It still runs here so this step keeps working.</span>}
        <FieldErrors path={p('operation')} exact />
      </Block>
      {op === 'cel' && <CelOperators />}
      {(op === 'lookup' || op === 'filter-rows') && (
        <Block title={op === 'lookup' ? 'Find the first row' : 'Find every row'}>
          <span className="row"><span className="muted">in the sheet</span><Text width={150} value={conf.sheet} onChange={(v) => set(['operation', op, 'sheet'], v)} label="Sheet" />
            <span className="muted">where</span><Text width={110} value={conf.column} onChange={(v) => set(['operation', op, 'column'], v)} label="Column" /></span>
          <span className="row"><span className="muted">{op === 'lookup' ? 'matches any of the inputs; return it as' : 'equals the input; return them as'}</span>
            <Text width={130} value={conf.as} onChange={(v) => set(['operation', op, 'as'], v)} label="Output name" /></span>
        </Block>
      )}
      {op === 'tidy' && <Block title="Operations, in order" aside="each one's output feeds the next"><Tidy /></Block>}
      {op === 'compare' && <Block title="Compare"><span className="muted">Returns <code className="mono">status</code>: match, changed, missing, or nothing on file.</span></Block>}
      {op === 'show' && (
        <Block title="Show value">
          <span className="muted">Writes the value you pick into the run's log, in full, so you can see exactly what a step produced. It changes nothing, costs nothing, and passes the value on unchanged as <code className="mono">{step.id}.value</code>. Handy while building; delete it when you're done.</span>
        </Block>
      )}
      {op === 'chart' && <ChartSettings />}
      {op === 'three-way-match' && <Block title="Three-way match"><span className="muted">Prices against the purchase order; quantities against the order and, for goods, what was received. Returns <code className="mono">passed</code> and <code className="mono">differences</code>.</span></Block>}
      <Block title="Takes" aside={op === 'bigquery' ? 'the query\u2019s @parameters' : op === 'chart' ? 'rows, and values the rule or line reads' : op === 'cel' ? 'items: the list; other inputs by name' : undefined}><TakesEditor fixed={op === 'javascript' || op === 'bigquery' || op === 'chart' || op === 'cel' ? undefined : Object.keys(OP_TAKES[op] ?? {})} /></Block>
      {op === 'bigquery' && <BigQueryQuery />}
      {op === 'javascript' && <JavaScript />}
      {(op === 'lookup' || op === 'filter-rows') && <Block title="Can use"><UsesEditor actions={['read']} limits={['sheets']} /></Block>}
      {inside && (
        <Block title="Re-runs">
          <Check checked={!!step.reruns_by_itself} onChange={(v) => set(['reruns_by_itself'], v || undefined)}
            detail="It has no model and costs nothing, so results are never out of date.">Re-run by itself whenever what it needs changes</Check>
        </Block>
      )}
    </>
  )
}

/** Chart: bars or a line over a list of rows, drawn to a PNG with no model. Rows a CEL rule matches are highlighted;
    a value from Takes can draw a dashed reference line. */
function ChartSettings() {
  const { step, set, p, refs, draft } = useStep()
  const conf: Json = step.operation?.chart ?? {}
  const rowsType = refs.find((r) => r.ref === String(step.takes?.rows ?? '').replace(/\?$/, ''))?.type.replace(/^list of /, '') ?? ''
  const fields = Object.keys(draft.records?.[rowsType]?.fields ?? {})
  const values = Object.keys(step.takes ?? {}).filter((k) => k !== 'rows')
  const c = (k: string, v: unknown) => set(['operation', 'chart', k], v === '' ? undefined : v)
  return (
    <Block title="Chart" aside="drawn from checked rows; no model">
      <span className="row"><span className="muted" style={{ width: 70 }}>Rows</span>
        <RefPicker value={step.takes?.rows ?? ''} refs={refs.filter((r) => r.type.startsWith('list'))} onChange={(v) => set(['takes', 'rows'], v)} path={p('takes', 'rows')} /></span>
      <span className="row"><span className="muted" style={{ width: 70 }}>As</span>
        <Segmented options={['bar', 'line']} value={conf.kind ?? 'bar'} onChange={(v) => c('kind', v)} labels={{ bar: 'Bars', line: 'A line' }} /></span>
      <span className="row"><span className="muted" style={{ width: 70 }}>Across</span>
        {fields.length ? <Select value={conf.x ?? ''} options={['', ...fields]} onChange={(v) => c('x', v)} label="X field" labels={{ '': 'Pick a field' }} />
          : <Text width={140} value={conf.x ?? ''} onChange={(v) => c('x', v)} label="X field" />}
        <span className="muted">up</span>
        {fields.length ? <Select value={conf.y ?? ''} options={['', ...fields]} onChange={(v) => c('y', v)} label="Y field" labels={{ '': 'Pick a field' }} />
          : <Text width={140} value={conf.y ?? ''} onChange={(v) => c('y', v)} label="Y field" />}</span>
      <span className="row" style={{ flexWrap: 'nowrap' }}><span className="muted" style={{ width: 70 }}>Title</span>
        <input className="input grow" value={conf.title ?? ''} aria-label="Title" placeholder="e.g. Monthly revenue" onChange={(e) => c('title', e.target.value)} /></span>
      <span className="row"><span className="muted" style={{ width: 70 }}>Numbers</span>
        <Select value={conf.y_format ?? ''} options={['', '$,.0f', ',.0f', '.1%']} onChange={(v) => c('y_format', v)} label="Number format"
          labels={{ '': 'As they are', '$,.0f': 'Dollars ($12,345)', ',.0f': 'Whole numbers (12,345)', '.1%': 'Percent (12.3%)' }} /></span>
      <span className="eyebrow">Highlight rows where</span>
      <Cel value={conf.highlight} onChange={(v) => c('highlight', v)} path={p('operation', 'chart', 'highlight')}
        placeholder={`row.${conf.y || 'total_revenue'} < typical * 0.7`} />
      <span className="faint">Optional. CEL over <code className="mono">row</code> and this step's other inputs{values.length ? ` (${values.join(', ')})` : ''}.</span>
      <span className="row"><span className="muted">Reference line at</span>
        <Select value={conf.reference ?? ''} options={['', ...values]} onChange={(v) => c('reference', v)} label="Reference" labels={{ '': 'None' }} />
        {conf.reference && <><span className="muted">labelled</span><Text width={140} value={conf.reference_label ?? ''} onChange={(v) => c('reference_label', v)} label="Reference label" /></>}</span>
      <span className="faint">Returns <code className="mono">image</code>: an Act step's email can include it, and an approver sees it.</span>
      <FieldErrors path={p('operation', 'chart')} />
    </Block>
  )
}

const CEL_OPS: { kind: string; label: string; text: string; init: unknown }[] = [
  { kind: 'keep', label: 'Keep', text: 'Only the items a rule is true for.', init: '' },
  { kind: 'add_fields', label: 'Add fields', text: 'New fields computed on each item.', init: { field: '' } },
  { kind: 'check', label: 'Check', text: 'Rules each item (or the whole list) must pass: drop, flag, or fail the run.', init: [{ rule: '', message: '', on_fail: 'drop' }] },
  { kind: 'remove_duplicates', label: 'Remove duplicates', text: 'One item per key, keeping the best.', init: { key: '' } },
  { kind: 'sort', label: 'Sort and take', text: 'Order by a value; optionally keep the first N.', init: { by: '', descending: true } },
  { kind: 'summarize', label: 'Summarize', text: 'Totals (count, sum, avg, min, max), overall or by group.', init: { totals: { count: 'count()' } } },
  { kind: 'match', label: 'Match', text: 'Find each item\u2019s match in another list, by key.', init: { with: '', key: '', other_key: '', as: 'match' } },
  { kind: 'link', label: 'Link related', text: 'Join items into clusters when a pair rule holds.', init: { together: '', as: 'members' } },
]

/** The CEL engine: operators over `items`, in order. The operator iterates, sorts and totals; each rule judges one item. */
function CelOperators() {
  const { step, set, p } = useStep()
  const ops: Json[] = Array.isArray(step.operation?.cel) ? step.operation.cel : []
  const [adding, setAdding] = useState(false)
  const inputs = Object.keys(step.takes ?? {}).filter((k) => k !== 'items')
  const saved = ops.map((o) => o.summarize?.save_as).filter(Boolean) as string[]
  const put = (next: Json[]) => set(['operation', 'cel'], next)
  const move = (i: number, d: number) => { const n = [...ops]; const [x] = n.splice(i, 1); n.splice(i + d, 0, x); put(n) }
  const at = (i: number, ...rest: (string | number)[]) => ['operation', 'cel', i, ...rest]
  const pairs = (obj: Json, onChange: (o: Json) => void, path: (k: string) => string, placeholder: string, nameHint: string) => (
    <div className="stack" style={{ gap: 4 }}>
      {Object.entries(obj ?? {}).map(([name, expr]) => (
        <span key={name} className="row" style={{ flexWrap: 'nowrap', alignItems: 'flex-start' }}>
          <input className="input mono" style={{ width: 110, flex: 'none' }} value={name} aria-label="Name"
            onChange={(e) => { const v = e.target.value.replace(/\W/g, '_'); const n: Json = {}; for (const [k, x] of Object.entries(obj)) n[k === name ? v : k] = x; onChange(n) }} />
          <span className="grow"><Cel value={expr as string} onChange={(v) => onChange({ ...obj, [name]: v })} path={path(name)} placeholder={placeholder} /></span>
          <button className="icon-btn" aria-label={`Remove ${name}`} onClick={() => { const n = { ...obj }; delete n[name]; onChange(n) }}><Icon name="x" size={12} /></button>
        </span>
      ))}
      <button className="link" style={{ alignSelf: 'flex-start' }} onClick={() => { let k = nameHint, i = 2; while ((obj ?? {})[k] !== undefined) k = `${nameHint}_${i++}`; onChange({ ...(obj ?? {}), [k]: '' }) }}>
        <Icon name="plus" size={13} width={2} />Add</button>
    </div>
  )
  const body = (o: Json, i: number) => {
    const [[kind, c]] = Object.entries(o)
    const path = (...r: (string | number)[]) => p('operation', 'cel', i, kind, ...r)
    switch (kind) {
      case 'keep': return <Cel value={c} onChange={(v) => set(at(i, 'keep'), v)} path={path()} placeholder="item.amount > 0" />
      case 'add_fields': return pairs(c, (v) => set(at(i, 'add_fields'), v), (n) => path(n), 'item.revenue / item.orders', 'field')
      case 'check': return (
        <div className="stack" style={{ gap: 6 }}>
          {(c ?? []).map((r: Json, k: number) => (
            <div key={k} className="stack" style={{ gap: 4, paddingLeft: 8, borderLeft: '2px solid var(--line)' }}>
              <Cel value={r.rule} onChange={(v) => set(at(i, 'check', k, 'rule'), v)} path={path(k, 'rule')} placeholder={r.once ? 'size(items) > 0' : 'has(item.region)'} />
              <span className="row" style={{ flexWrap: 'nowrap' }}>
                <input className="input grow" value={r.message ?? ''} placeholder="Message when it fails" aria-label="Message" onChange={(e) => set(at(i, 'check', k, 'message'), e.target.value)} />
                <Select value={r.on_fail ?? 'drop'} options={['drop', 'flag', 'fail']} labels={{ drop: 'Drop it', flag: 'Flag it', fail: 'Fail the run' }} onChange={(v) => set(at(i, 'check', k, 'on_fail'), v)} label="On fail" />
                <button className="icon-btn" aria-label="Remove rule" onClick={() => set(at(i, 'check'), c.filter((_: Json, j: number) => j !== k))}><Icon name="x" size={12} /></button>
              </span>
              <Check checked={!!r.once} onChange={(on) => set(at(i, 'check', k, 'once'), on || undefined)}>Once, on the whole list (<code className="mono">items</code>)</Check>
            </div>
          ))}
          <button className="link" style={{ alignSelf: 'flex-start' }} onClick={() => set(at(i, 'check'), [...(c ?? []), { rule: '', message: '', on_fail: 'drop' }])}><Icon name="plus" size={13} width={2} />Add a rule</button>
        </div>)
      case 'remove_duplicates': return (
        <>
          <span className="row" style={{ flexWrap: 'nowrap' }}><span className="muted" style={{ width: 90 }}>Same when</span><span className="grow"><Cel value={c.key} onChange={(v) => set(at(i, kind, 'key'), v)} path={path('key')} placeholder="item.order_id" /></span></span>
          <span className="row" style={{ flexWrap: 'nowrap' }}><span className="muted" style={{ width: 90 }}>Keep highest</span><span className="grow"><Cel value={c.keep_highest} onChange={(v) => set(at(i, kind, 'keep_highest'), v || undefined)} path={path('keep_highest')} placeholder="optional: item.updated_at" /></span></span>
        </>)
      case 'sort': return (
        <>
          <span className="row" style={{ flexWrap: 'nowrap' }}><span className="muted" style={{ width: 90 }}>By</span><span className="grow"><Cel value={c.by} onChange={(v) => set(at(i, kind, 'by'), v)} path={path('by')} placeholder="item.total_revenue" /></span></span>
          <span className="row"><Segmented options={['desc', 'asc']} value={c.descending ? 'desc' : 'asc'} onChange={(v) => set(at(i, kind, 'descending'), v === 'desc')} labels={{ desc: 'Highest first', asc: 'Lowest first' }} />
            <span className="muted">take</span><Text width={50} value={c.take ?? ''} onChange={(v) => set(at(i, kind, 'take'), v ? Number(v) : undefined)} label="Take" placeholder="all" /></span>
        </>)
      case 'summarize': return (
        <>
          <span className="row"><span className="muted" style={{ width: 90 }}>Of</span>
            <Select value={c.of ?? 'items'} options={['items', ...inputs]} onChange={(v) => set(at(i, kind, 'of'), v === 'items' ? undefined : v)} label="Of" /></span>
          <span className="eyebrow">Group by (optional)</span>
          {pairs(c.group_by ?? {}, (v) => set(at(i, kind, 'group_by'), Object.keys(v).length ? v : undefined), (n) => path('group_by', n), 'item.region', 'group')}
          <span className="eyebrow">Totals</span>
          {pairs(c.totals ?? {}, (v) => set(at(i, kind, 'totals'), v), (n) => path('totals', n), 'sum(item.revenue)', 'total')}
          <span className="faint">count(), count(rule), sum(…), avg(…), min(…), max(…) of an expression over <code className="mono">item</code>.</span>
          <span className="row"><span className="muted">Save as</span><Text width={120} value={c.save_as ?? ''} onChange={(v) => set(at(i, kind, 'save_as'), v.replace(/\W/g, '_') || undefined)} label="Save as" placeholder="optional" />
            <span className="faint">{c.save_as ? `The list goes on unchanged; later rules read ${c.save_as}.<total>.` : 'Empty: the totals replace the list.'}</span></span>
        </>)
      case 'match': return (
        <>
          <span className="row"><span className="muted" style={{ width: 90 }}>With</span>
            <Select value={c.with ?? ''} options={['', ...inputs]} labels={{ '': 'Pick an input' }} onChange={(v) => set(at(i, kind, 'with'), v)} label="With" /></span>
          <span className="row" style={{ flexWrap: 'nowrap' }}><span className="muted" style={{ width: 90 }}>Item key</span><span className="grow"><Cel value={c.key} onChange={(v) => set(at(i, kind, 'key'), v)} path={path('key')} placeholder="item.customer_id" /></span></span>
          <span className="row" style={{ flexWrap: 'nowrap' }}><span className="muted" style={{ width: 90 }}>Other key</span><span className="grow"><Cel value={c.other_key} onChange={(v) => set(at(i, kind, 'other_key'), v)} path={path('other_key')} placeholder="other.id" /></span></span>
          <span className="row"><span className="muted" style={{ width: 90 }}>As</span><Text width={120} value={c.as ?? 'match'} onChange={(v) => set(at(i, kind, 'as'), v.replace(/\W/g, '_'))} label="As" />
            <span className="faint">Absent when nothing matched: test with <code className="mono">has(item.{c.as ?? 'match'})</code>.</span></span>
        </>)
      case 'link': return (
        <>
          <Cel value={c.together} onChange={(v) => set(at(i, kind, 'together'), v)} path={path('together')} placeholder="a.confirmation == b.confirmation" />
          <span className="faint">Items join a cluster when the rule holds for a pair (<code className="mono">a</code>, <code className="mono">b</code>). Each cluster becomes an item with <code className="mono">{c.as ?? 'members'}</code> and <code className="mono">size</code>.</span>
        </>)
    }
    return null
  }
  return (
    <Block title="Operators" aside="in order, each feeding the next">
      <span className="faint">Each rule sees <code className="mono">item</code> (one record), <code className="mono">run</code>
        {inputs.length ? <>, {inputs.map((x) => <code key={x} className="mono" style={{ marginRight: 4 }}>{x}</code>)}</> : null}
        {saved.length ? <> and what Summarize saved ({saved.join(', ')})</> : null}. Numbers mix freely; dividing two whole numbers gives a whole number.</span>
      {ops.map((o, i) => {
        const kind = Object.keys(o)[0]
        const meta = CEL_OPS.find((x) => x.kind === kind)
        return (
          <div key={i} className="op">
            <span className="spread"><span className="row" style={{ gap: 6 }}><span className="numbered">{i + 1}</span><strong>{meta?.label ?? kind}</strong><span className="faint">{meta?.text}</span></span>
              <span className="row" style={{ gap: 2, flexWrap: 'nowrap' }}>
                <button className="icon-btn" aria-label="Move up" disabled={i === 0} onClick={() => move(i, -1)}><Icon name="up" size={12} /></button>
                <button className="icon-btn" aria-label="Move down" disabled={i === ops.length - 1} onClick={() => move(i, 1)}><Icon name="down" size={12} /></button>
                <button className="icon-btn" aria-label="Remove operator" onClick={() => put(ops.filter((_, k) => k !== i))}><Icon name="x" size={12} /></button>
              </span></span>
            {body(o, i)}
          </div>
        )
      })}
      {adding ? (
        <div className="stack" style={{ gap: 4 }}>
          {CEL_OPS.map((x) => (
            <button key={x.kind} type="button" className="row link" style={{ gap: 8, justifyContent: 'flex-start', flexWrap: 'nowrap', alignItems: 'flex-start', textAlign: 'left' }}
              onClick={() => { put([...ops, { [x.kind]: x.init }]); setAdding(false) }}>
              <strong style={{ width: 130, flex: 'none' }}>{x.label}</strong><span className="faint">{x.text}</span></button>
          ))}
          <button className="link" style={{ color: 'var(--faint)', alignSelf: 'flex-start' }} onClick={() => setAdding(false)}>Cancel</button>
        </div>
      ) : <button className="link" style={{ alignSelf: 'flex-start' }} onClick={() => setAdding(true)}><Icon name="plus" size={13} width={2} />Add an operator</button>}
      <span className="faint">Returns <code className="mono">items</code>, <code className="mono">notes</code> (what each operator dropped or changed){saved.length ? <> and {saved.map((x) => <code key={x} className="mono" style={{ marginLeft: 4 }}>{x}</code>)}</> : null}.</span>
      <FieldErrors path={p('operation', 'cel')} />
    </Block>
  )
}
