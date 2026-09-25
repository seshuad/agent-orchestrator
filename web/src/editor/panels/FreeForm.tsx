// Free-form block: free within the order its data sets. The planner chooses the rest while the agent runs.
import type { Json } from '../../api'
import { Area, Block, Cel, Chips, FieldErrors, Guarantees, Icon, KIND_ICON, Pill, RefPicker, Select, Text } from '../../ui'
import { MODELS, MODEL_LABEL } from '../model'
import { FieldsEditor, StepHeader, useStep } from './common'

export default function FreeForm() {
  const { step, set, p, select, path, refs, feedback } = useStep()
  const inner: Json[] = step.steps ?? []
  const rules: Json[] = step.before_finishing ?? []
  const returns: Json = step.returns ?? {}
  const collect: Json = step.collect ?? {}
  const graph = feedback.graphs[step.id]
  const needs = (s: Json) => Object.values(s.takes ?? {}).flatMap((v: any) => (Array.isArray(v) ? [v.join(' or ')] : [v])).filter(Boolean).join(', ') || 'nothing'
  return (
    <>
      <StepHeader note="The order is decided while the agent runs, within the order its data sets: a step can run once what it needs exists." />
      <Block title="Goal"><Area value={step.goal} onChange={(v) => set(['goal'], v)} rows={4} path={p('goal')} label="Goal" /></Block>
      <Block title="Planning model">
        <Select value={step.planning_model} options={MODELS} labels={MODEL_LABEL} onChange={(v) => set(['planning_model'], v)} label="Planning model" />
        <span className="muted">It sees short summaries of each step's results, never the email text.</span>
        <FieldErrors path={p('planning_model')} />
      </Block>
      <Block title="Steps and what they need" aside="order worked out from these">
        {inner.map((s, j) => {
          const mark = graph?.nodes.find((n) => n.id === s.id)?.mark
          return (
            <button key={s.id + j} type="button" className="op" style={{ textAlign: 'left' }} onClick={() => select({ type: 'step', path: [...path, 'steps', j] })}>
              <span className="row" style={{ flexWrap: 'nowrap' }}><Icon name={KIND_ICON[s.kind]} size={14} color="var(--muted)" /><strong className="grow">{s.name}</strong><Pill kind={s.kind} /></span>
              <span className="row faint" style={{ gap: 6, paddingLeft: 22 }}>needs {needs(s)}{mark && <Pill kind={mark === 'required' ? 'waiting' : mark === 'auto' ? 'stopped' : 'free-form'}>{mark === 'required' ? 'required to finish' : mark === 'auto' ? 're-runs by itself' : 'planner sets focus'}</Pill>}</span>
            </button>
          )
        })}
        {inner.length === 0 && <span className="muted">Add steps from the step list: Ask and Built-in steps can go inside.</span>}
        <div className="notice" style={{ border: '1px dashed var(--line)', color: 'var(--muted)' }}><Icon name="lock" size={14} />Approve and Act steps can't go inside. They run after this block, in a fixed order.</div>
      </Block>
      <Block title="Before finishing" aside="rules the planner can't skip">
        <span className="muted">CEL over <code className="mono">steps.&lt;id&gt;</code> (absent until it runs: test with <code className="mono">has(steps.x)</code>), <code className="mono">planner</code>, <code className="mono">collected</code> and <code className="mono">run</code>.</span>
        {rules.map((r, i) => (
          <div key={i} className="op">
            <span className="spread"><span className="muted">Rule {i + 1}</span><button className="link" style={{ color: 'var(--faint)' }} onClick={() => set(['before_finishing'], rules.filter((_, k) => k !== i))}>remove</button></span>
            <Cel value={r.rule} onChange={(v) => set(['before_finishing', i, 'rule'], v)} path={p('before_finishing', i, 'rule')} rows={2} />
            <span className="row" style={{ flexWrap: 'nowrap' }}><span className="muted">If not, tell the planner</span>
              <input className="input grow" value={r.message} aria-label="Message" onChange={(e) => set(['before_finishing', i, 'message'], e.target.value)} /></span>
          </div>
        ))}
        <button className="link" onClick={() => set(['before_finishing'], [...rules, { rule: '', message: '' }])}><Icon name="plus" size={13} width={2} />Add rule</button>
      </Block>
      <Block title="Finishes with one outcome" aside="optional">
        <Chips values={step.outcomes ?? []} onChange={(v) => set(['outcomes'], v.length ? v : undefined)} placeholder="Add an outcome" />
        <span className="muted">The planner must pick one; the approver sees it, and rules can depend on it.</span>
      </Block>
      <Block title="The planner also decides" aside="fields steps and rules can use">
        <FieldsEditor fields={step.planner_returns ?? {}} path={[...p().split('.'), 'planner_returns']} onChange={(f) => set(['planner_returns'], Object.keys(f).length ? f : undefined)} />
      </Block>
      <Block title="Keep results across runs" aside="collected">
        <span className="muted">A step run again replaces its last result. Collect results to keep all of them.</span>
        {Object.entries(collect).map(([name, sources]: [string, any]) => (
          <div key={name} className="op">
            <span className="spread"><code className="mono" style={{ fontWeight: 600 }}>collected.{name}</code>
              <button className="link" style={{ color: 'var(--faint)' }} onClick={() => { const c = { ...collect }; delete c[name]; set(['collect'], Object.keys(c).length ? c : undefined) }}>remove</button></span>
            {(sources as string[]).map((s, i) => (
              <span key={i} className="row" style={{ flexWrap: 'nowrap' }}>
                <RefPicker value={s} refs={refs.filter((r) => !r.ref.startsWith('collected.') && !r.ref.startsWith('planner.') && !r.ref.startsWith('run.'))}
                  onChange={(v) => set(['collect', name], sources.map((x: string, k: number) => (k === i ? v : x)))} />
                <button className="icon-btn" aria-label="Remove" onClick={() => set(['collect', name], sources.filter((_: string, k: number) => k !== i))}><Icon name="x" size={12} /></button>
              </span>
            ))}
            <button className="link" onClick={() => set(['collect', name], [...sources, ''])}><Icon name="plus" size={13} width={2} />Add a source</button>
          </div>
        ))}
        <button className="link" onClick={() => { let n = 1; while (collect[`results_${n}`]) n++; set(['collect', `results_${n}`], ['']) }}><Icon name="plus" size={13} width={2} />Collect results</button>
      </Block>
      <Block title="Limits">
        <span className="row"><span className="muted">Run Ask steps at most</span><Text width={48} value={step.limits?.ask_runs} onChange={(v) => set(['limits', 'ask_runs'], Number(v) || 0)} label="Ask step runs" /><span className="muted">times</span></span>
        <span className="row"><span className="muted">Stop planning after</span><Text width={48} value={step.limits?.turns} onChange={(v) => set(['limits', 'turns'], Number(v) || 0)} label="Turns" /><span className="muted">turns</span></span>
        <span className="muted">Built-in steps have no model and don't count. At a limit, the block finishes with what it has if the rules hold, and stops the run if not.</span>
      </Block>
      <Block title="Next steps receive" aside="CEL">
        {Object.entries(returns).map(([name, cel]) => (
          <div key={name} className="stack" style={{ gap: 3 }}>
            <span className="spread"><code className="mono" style={{ fontWeight: 600 }}>{step.id}.{name}</code>
              <button className="link" style={{ color: 'var(--faint)' }} onClick={() => { const c = { ...returns }; delete c[name]; set(['returns'], c) }}>remove</button></span>
            <Cel value={cel as string} onChange={(v) => set(['returns', name], v)} path={p('returns', name)} />
          </div>
        ))}
        <span className="row"><input className="input" placeholder="new output name" aria-label="New output name" id="new-output"
          onKeyDown={(e) => { const v = (e.target as HTMLInputElement).value.trim(); if (e.key === 'Enter' && v) { set(['returns', v.replace(/\s+/g, '_')], ''); (e.target as HTMLInputElement).value = '' } }} />
          <span className="faint">press Enter to add</span></span>
      </Block>
      <Block title="Checked by the service">
        <Guarantees items={['The planner can’t change anything outside the agent: it can only run the steps above.', 'Every output is checked against its record type, the same as in a fixed order.', 'The run log shows each step the planner ran and the reason it gave.']} />
      </Block>
    </>
  )
}
