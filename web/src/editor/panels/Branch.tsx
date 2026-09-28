// Branch: pick one path. By rules (CEL, in order, the first match) or by a model (one judgment among named paths),
// once per run or once for each item of a list.
import type { Json } from '../../api'
import { Area, Block, Cel, FieldErrors, Guarantees, Icon, RefPicker, Segmented, Select, Text } from '../../ui'
import { MODELS, MODEL_LABEL } from '../model'
import { MemoryEditor, ReadsEditor, StepHeader, TakesEditor, useStep } from './common'

export default function Branch() {
  const { step, set, p, draft, refs } = useStep()
  const paths: Json[] = step.paths ?? []
  const byModel = step.decide === 'model'
  const targets = ['next', 'end', ...(draft.steps ?? []).map((s: Json) => s.id).filter((id: string) => id !== step.id)]
  const labels = { next: 'The next step', end: 'End the run', ...Object.fromEntries((draft.steps ?? []).map((s: Json) => [s.id, s.name])) }
  const hard: Json[] = step.rules_first ?? []
  const each: Json | undefined = step.for_each
  const lists = refs.filter((r) => r.type.startsWith('list'))
  const toEach = (on: boolean) => {
    if (on) {
      set(['for_each'], { over: lists[0]?.ref ?? '', as: 'item', at_once: 5 }); set(['rules_first'], undefined)
      set(['paths'], paths.map((x) => ({ ...x, then: 'next' })))
    } else set(['for_each'], undefined)
  }
  const toModel = () => {
    const named = paths.map((x, i) => ({ name: i === paths.length - 1 && x.name === 'Otherwise' ? 'Not sure' : x.name, when_true: x.when_true ?? '', then: x.then }))
    set(['decide'], 'model'); set(['model'], step.model ?? 'claude-sonnet-5'); set(['question'], step.question ?? '')
    set(['paths'], named.length >= 2 ? named : [{ name: 'Yes', when_true: '', then: 'next' }, { name: 'Not sure', then: 'end' }])
  }
  const toRules = () => {
    set(['decide'], undefined); set(['model'], undefined); set(['question'], undefined); set(['takes'], undefined)
    set(['rules_first'], undefined); set(['memory'], undefined); set(['uses'], undefined); set(['for_each'], undefined)
    set(['paths'], paths.map((x, i) => (i === paths.length - 1 ? { name: 'Otherwise', then: x.then } : { name: x.name, when: x.when ?? '', then: x.then })))
  }
  return (
    <>
      <StepHeader note={each ? `A model picks one path for each ${each.as || 'item'} of a list.` : byModel ? 'A model reads the inputs and picks exactly one path.' : 'Paths are checked in order and the first match runs.'} />
      <Block title="Decide">
        <Segmented options={['rules', 'model']} value={byModel ? 'model' : 'rules'} onChange={(v) => (v === 'model' ? toModel() : toRules())}
          labels={{ rules: 'By rules (CEL)', model: 'By a model (a judgment)' }} />
        <span className="faint">{byModel ? `For decisions a rule can\u2019t express, like "is this legitimate?". One small model call ${each ? `per ${each.as || 'item'}` : 'per run'}.` : 'Free, instant and exact. No model.'}</span>
      </Block>
      {byModel && (
        <Block title="Decide for">
          <Segmented options={['run', 'each']} value={each ? 'each' : 'run'} onChange={(v) => toEach(v === 'each')}
            labels={{ run: 'The run, once', each: 'Each item in a list' }} />
          {each && (
            <>
              <span className="row"><span className="muted">Each</span>
                <Text width={90} value={each.as ?? 'item'} onChange={(v) => set(['for_each', 'as'], v.trim().replace(/\s+/g, '_'))} label="Item name" />
                <span className="muted">in</span>
                <RefPicker value={each.over ?? ''} refs={lists} onChange={(v) => set(['for_each', 'over'], v)} path={p('for_each', 'over')} /></span>
              <span className="row"><span className="muted">Decide</span>
                <Text width={44} value={each.at_once ?? 5} onChange={(v) => set(['for_each', 'at_once'], Number(v) || 5)} label="At once" />
                <span className="muted">at the same time</span></span>
              <span className="faint">Each {each.as || 'item'} gets its path, reason and evidence, and the run goes on to the next step. A path needs no steps of
                its own: <code className="mono">{step.id}.decisions</code> has every decision. For steps that should handle only one path, take
                {' '}<code className="mono">{step.id}.by_path.&lt;path&gt;</code>: the {each.as || 'item'}s that took it. In the question, inputs and memory,
                {' '}<code className="mono">{each.as || 'item'}</code> is one {each.as || 'item'}.</span>
              <FieldErrors path={p('for_each')} />
            </>
          )}
        </Block>
      )}
      {byModel && (
        <>
          <Block title="The question"><Area value={step.question} onChange={(v) => set(['question'], v)} rows={2} path={p('question')} label="Question" /></Block>
          <Block title="Model"><Select value={step.model ?? 'claude-sonnet-5'} options={MODELS} labels={MODEL_LABEL} onChange={(v) => set(['model'], v)} label="Model" /></Block>
          <Block title="Decides on" aside={each ? `the ${each.as || 'item'} itself, plus` : 'picked from earlier results'}><TakesEditor /></Block>
          <Block title="May read" aside="to look things up before deciding">
            <ReadsEditor />
            <span className="faint">Read-only. {each ? `Handy when the list only names each ${each.as || 'item'}: it reads the rest itself.` : 'It decides on its inputs alone otherwise.'}</span>
          </Block>
        </>
      )}
      <Block title={byModel ? 'Paths' : 'Paths, in order'}>
        {paths.map((path, i) => {
          const last = i === paths.length - 1
          return (
            <div key={i} className="op">
              <span className="row" style={{ flexWrap: 'nowrap' }}><span className="numbered">{i + 1}</span>
                <input className="input grow" value={path.name} aria-label="Path name" onChange={(e) => set(['paths', i, 'name'], e.target.value)} disabled={last && !byModel} />
                {!last && <button className="icon-btn" aria-label="Remove path" onClick={() => set(['paths'], paths.filter((_, k) => k !== i))}><Icon name="x" size={12} /></button>}</span>
              {!last && !byModel && <><span className="muted">when</span><Cel value={path.when} onChange={(v) => set(['paths', i, 'when'], v)} path={p('paths', i, 'when')} /></>}
              {byModel && (last
                ? <span className="faint">The safe default: used when no other path clearly applies, or the answer isn't one of the paths.</span>
                : <><span className="muted">when this is true</span>
                    <input className="input" value={path.when_true ?? ''} aria-label={`When ${path.name} applies`} placeholder="e.g. From the airline or a known agency; the details are consistent"
                      onChange={(e) => set(['paths', i, 'when_true'], e.target.value)} /></>)}
              {!each && <span className="row"><span className="muted">then go to</span><Select value={path.then} options={targets} labels={labels} onChange={(v) => set(['paths', i, 'then'], v)} label="Then" /></span>}
            </div>
          )
        })}
        <button className="link" onClick={() => set(['paths'], [...paths.slice(0, -1), byModel ? { name: `Path ${paths.length}`, when_true: '', then: 'next' } : { name: `Path ${paths.length}`, when: '', then: 'next' }, paths[paths.length - 1]])}>
          <Icon name="plus" size={13} width={2} />Add a path</button>
        {!byModel && <span className="muted">Conditions read <code className="mono">steps.&lt;id&gt;</code> for earlier results, e.g. <code className="mono">size(steps.find_and_check.trips) == 0</code>.</span>}
        <FieldErrors path={p('paths')} />
      </Block>
      {byModel && !each && (
        <Block title="Hard rules first" aside="outcomes that aren't up to judgment">
          {hard.map((r, i) => (
            <div key={i} className="op">
              <span className="spread"><span className="muted">If</span><button className="link" style={{ color: 'var(--faint)' }} onClick={() => set(['rules_first'], hard.filter((_, k) => k !== i))}>remove</button></span>
              <Cel value={r.when} onChange={(v) => set(['rules_first', i, 'when'], v)} path={p('rules_first', i, 'when')} />
              <span className="row"><span className="muted">go to</span><Select value={r.then} options={targets} labels={labels} onChange={(v) => set(['rules_first', i, 'then'], v)} label="Then" /></span>
            </div>
          ))}
          <button className="link" onClick={() => set(['rules_first'], [...hard, { when: '', then: 'end' }])}><Icon name="plus" size={13} width={2} />Add a hard rule</button>
          <span className="faint">Checked before the model is asked, e.g. amounts over a limit always go to review.</span>
        </Block>
      )}
      {byModel && <Block title="Memory" aside="past confirmed decisions"><MemoryEditor where="branch" /></Block>}
      <Block title="Checked by the service">
        <Guarantees items={byModel
          ? ['The model can only answer with one of these paths, a reason and evidence; anything else takes the last path.',
             step.uses ? 'It can only read, within the limits above, and changes nothing.' : 'It has no tools and changes nothing: it decides on the inputs it\u2019s given.',
             'Its text inputs are treated as data, never instructions.',
             ...(each ? [`If one ${each.as || 'item'} can\u2019t be decided, it takes the last path and says so; the others still count.`] : [])]
          : ['Conditions compare results from earlier steps, never free text a model wrote.', 'There is always an Otherwise path, so every run goes somewhere.']} />
      </Block>
    </>
  )
}
