// Built-in step: fixed operations, no model. Every condition is CEL, checked on every save.
import { useState } from 'react'
import { api, type Json } from '../../api'
import { Block, Cel, Check, FieldErrors, Icon, Segmented, Select, Text } from '../../ui'
import { FieldsEditor, StepHeader, TakesEditor, UsesEditor, useStep } from './common'

const OPS = ['tidy', 'lookup', 'filter-rows', 'compare', 'three-way-match', 'show', 'javascript'] as const
const OP_LABEL: Record<string, string> = { tidy: 'Tidy up', lookup: 'Look up', 'filter-rows': 'Filter rows', compare: 'Compare', 'three-way-match': 'Three-way match', show: 'Show value', javascript: 'JavaScript' }
const OP_TAKES: Record<string, Json> = {
  tidy: { records: '' }, lookup: { any_of: [] }, 'filter-rows': { equals: '' }, compare: { value: '', on_file: '' },
  'three-way-match': { invoice: '', purchase_order: '', receipts: '' }, show: { value: '' }, javascript: { items: '' },
}
const JS_TEMPLATE = `// \`inputs\` holds what this step takes, by name (see Takes).
// Return an object with every field listed under Returns.
const items = inputs.items ?? [];
return { count: items.length };`
const OP_DEFAULT: Record<string, Json> = {
  tidy: [], lookup: { sheet: '', column: '', as: 'row' }, 'filter-rows': { sheet: '', column: '', as: 'rows' }, compare: {}, 'three-way-match': {}, show: {},
  javascript: { code: JS_TEMPLATE },
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
  const { step, set, p, inside } = useStep()
  const op = Object.keys(step.operation ?? {})[0] ?? 'lookup'
  const conf = step.operation?.[op] ?? {}
  const setOp = (next: string) => {
    set(['operation'], { [next]: OP_DEFAULT[next] }); set(['takes'], OP_TAKES[next])
    set(['returns'], next === 'javascript' ? { count: { type: 'number' } } : undefined)
  }
  return (
    <>
      <StepHeader />
      <Block title="Operation">
        <Segmented options={[...OPS]} value={op as (typeof OPS)[number]} onChange={setOp} labels={OP_LABEL} />
        <FieldErrors path={p('operation')} exact />
      </Block>
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
      {op === 'three-way-match' && <Block title="Three-way match"><span className="muted">Prices against the purchase order; quantities against the order and, for goods, what was received. Returns <code className="mono">passed</code> and <code className="mono">differences</code>.</span></Block>}
      <Block title="Takes"><TakesEditor fixed={op === 'javascript' ? undefined : Object.keys(OP_TAKES[op] ?? {})} /></Block>
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
