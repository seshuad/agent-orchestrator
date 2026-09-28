// Parallel: Ask steps at the same time, or a set of steps once for each item of a list (several items at a time).
import type { Json } from '../../api'
import { Block, FieldErrors, Guarantees, Icon, KIND_ICON, Pill, RefPicker, Segmented, Text } from '../../ui'
import { KIND_LABEL } from '../model'
import { StepHeader, useStep } from './common'

export default function Parallel() {
  const { step, set, p, refs, select, path } = useStep()
  const each: Json | undefined = step.for_each
  const inner: Json[] = step.steps ?? []
  const lists = refs.filter((r) => r.type.startsWith('list'))
  const name = each?.as || 'item'
  const toEach = (on: boolean) => set(['for_each'], on ? { over: lists[0]?.ref ?? '', as: 'item', at_once: 5 } : undefined)
  return (
    <>
      <StepHeader note={each ? `Its steps run in order for each ${name}, and several ${name}s at a time.` : 'Its Ask steps all run at the same time.'} />
      <Block title="Runs">
        <Segmented options={['together', 'each']} value={each ? 'each' : 'together'} onChange={(v) => toEach(v === 'each')}
          labels={{ together: 'These steps, together', each: 'For each item in a list' }} />
        {!each && <span className="faint">For reads that don't depend on each other, like three mailboxes at once. Only Ask steps can run together;
          later steps read their results as usual.</span>}
        {each && (
          <>
            <span className="row"><span className="muted">Each</span>
              <Text width={90} value={each.as ?? 'item'} onChange={(v) => set(['for_each', 'as'], v.trim().replace(/\s+/g, '_'))} label="Item name" />
              <span className="muted">in</span>
              <RefPicker value={each.over ?? ''} refs={lists} onChange={(v) => set(['for_each', 'over'], v)} path={p('for_each', 'over')} /></span>
            <span className="row"><span className="muted">Run</span>
              <Text width={44} value={each.at_once ?? 5} onChange={(v) => set(['for_each', 'at_once'], Number(v) || 5)} label="At once" />
              <span className="muted">{name}s at the same time</span></span>
            <span className="faint">Inside, <code className="mono">{name}</code> is one {name}. A Branch can send one {name} to one of the block's steps, on to the
              next, or to the end (of that {name} only). Afterwards, <code className="mono">{step.id}.results</code> has every {name}'s results, and each
              model-decided Branch hands on its decisions and the {name}s that took each path.</span>
            <FieldErrors path={p('for_each')} />
          </>
        )}
      </Block>
      <Block title="If one fails">
        <Segmented options={['stop', 'continue']} value={step.failure ?? 'stop'} onChange={(v) => set(['failure'], v)}
          labels={{ stop: 'Stop the run', continue: `Keep going with the others` }} />
        <span className="faint">{(step.failure ?? 'stop') === 'stop' ? `A failed ${each ? name : 'step'} stops the whole run.`
          : each ? `A failed ${name} is left out of the results (a Branch counts it as its safe default); the run fails only if every ${name} fails.`
          : 'The run goes on if at least one step finishes; a failed step hands on nothing.'}</span>
      </Block>
      <Block title="Steps" aside={each ? `in order, for each ${name}` : 'all at once'}>
        {inner.length === 0 && <span className="muted">No steps yet. Add them from the list on the left.</span>}
        {inner.map((s, j) => (
          <button key={s.id + j} type="button" className="row link" style={{ gap: 7, justifyContent: 'flex-start' }} onClick={() => select({ type: 'step', path: [...path, 'steps', j] })}>
            <Icon name={KIND_ICON[s.kind]} size={14} color="var(--muted)" /><span>{s.name}</span><Pill kind={s.kind}>{KIND_LABEL[s.kind]}</Pill>
          </button>
        ))}
        <FieldErrors path={p('steps')} exact />
      </Block>
      <Block title="Checked by the service">
        <Guarantees items={each
          ? [`Each ${name} runs on its own: what one ${name} says can't change how another is handled.`,
             `At most ${each.at_once ?? 5} ${name}s run at once, within the agent's budget and time limit.`,
             'Approvals happen outside the block, once for the whole run.']
          : ['The steps can only read: nothing changes until after the block.', 'All of them finish (or fail) before the next step starts.']} />
      </Block>
    </>
  )
}
