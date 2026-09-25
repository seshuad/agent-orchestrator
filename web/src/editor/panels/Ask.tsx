// Ask step: a model reads and extracts. It can never change anything.
import { Area, Block, Check, FieldErrors, Select, Text } from '../../ui'
import { MODELS, MODEL_LABEL } from '../model'
import { FieldsEditor, StepHeader, TakesEditor, UsesEditor, useStep } from './common'

export default function Ask() {
  const { step, set, draft, p, inside, block } = useStep()
  const service = step.uses ? draft.connections?.[step.uses.connection]?.service : null
  const shared = Object.keys(draft.shared_instructions ?? {})
  const usesShared = typeof step.instructions === 'object' && step.instructions !== null
  const others = (block?.steps ?? []).filter((s: any) => s.id !== step.id)
  return (
    <>
      <StepHeader note={inside ? 'Runs when the planner picks it and what it needs exists.' : undefined} />
      <Block title="Model">
        <Select value={step.model} options={MODELS} labels={MODEL_LABEL} onChange={(v) => set(['model'], v)} label="Model" />
        <FieldErrors path={p('model')} />
      </Block>
      <Block title="Takes" aside="picked from earlier results">
        <TakesEditor />
      </Block>
      <Block title="Can use">
        <UsesEditor actions={service === 'google-sheets' ? ['read'] : service === 'github' ? ['search', 'open', 'read'] : ['search', 'open']}
          limits={service === 'gmail' ? ['senders', 'lookback_days', 'only_message', 'from_domain', 'only_cited_by'] : service === 'google-sheets' ? ['sheets']
            : service === 'github' ? ['repos', 'lookback_days'] : []} />
        <span className="faint">Ask steps can only read: send, delete and change actions aren't offered.</span>
      </Block>
      <Block title="Instructions">
        {shared.length > 0 && (
          <Select value={usesShared ? step.instructions.shared : ''} options={['', ...shared]} labels={{ '': 'Its own instructions' }} label="Shared instructions"
            onChange={(v) => set(['instructions'], v ? { shared: v, extra: usesShared ? step.instructions.extra : undefined } : (usesShared ? draft.shared_instructions[step.instructions.shared] : step.instructions))} />
        )}
        {usesShared
          ? <><span className="muted">Shared: {draft.shared_instructions?.[step.instructions.shared]?.slice(0, 140)}…</span>
              <Area value={step.instructions.extra ?? ''} onChange={(v) => set(['instructions', 'extra'], v || undefined)} rows={2} label="Extra instructions" /></>
          : <Area value={step.instructions} onChange={(v) => set(['instructions'], v)} rows={4} path={p('instructions')} label="Instructions" />}
      </Block>
      <Block title="Task"><Area value={step.task} onChange={(v) => set(['task'], v)} rows={2} path={p('task')} label="Task" /></Block>
      <Block title="Returns"><FieldsEditor fields={step.returns ?? {}} path={[...p().split('.'), 'returns']} onChange={(f) => set(['returns'], f)} /></Block>
      {inside && (
        <Block title="Run again">
          <Check checked={!!step.repeat} onChange={(on) => set(['repeat'], on ? { planner_sets: 'focus', usually_after: others[0]?.id, when: 'gap found' } : undefined)}
            detail="Draws a dotted line in the block: where the planner can loop back.">The planner can run this again, with a focus</Check>
          {step.repeat && (
            <>
              <span className="row"><span className="muted">Usually after</span>
                <Select value={step.repeat.usually_after ?? ''} options={others.map((s: any) => s.id)} labels={Object.fromEntries(others.map((s: any) => [s.id, s.name]))} onChange={(v) => set(['repeat', 'usually_after'], v)} label="Usually after" />
                <span className="muted">when</span><Text width={140} value={step.repeat.when} onChange={(v) => set(['repeat', 'when'], v)} label="When" /></span>
              <span className="faint">A hint for the planner, not a rule. Add <code className="mono">focus: planner.focus?</code> to Takes so the step receives it.</span>
            </>
          )}
        </Block>
      )}
    </>
  )
}
