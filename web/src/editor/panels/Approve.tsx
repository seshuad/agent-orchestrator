// Approve: a person decides before anything changes. The safe choice is always first.
import type { Json } from '../../api'
import { Area, Block, Cel, Check, FieldErrors, Icon, RefPicker, Select, Text } from '../../ui'
import { StepHeader, useStep } from './common'

const PASSES = ['none', 'pre-selected', 'all', 'picked']
const PASS_LABEL = { none: 'passes nothing', 'pre-selected': 'passes the pre-selected', all: 'passes all', picked: 'passes what they pick' }

export default function Approve() {
  const { step, set, p, refs } = useStep()
  const choices: Json[] = step.choices ?? []
  const notify: string[] = step.notify ?? []
  return (
    <>
      <StepHeader />
      <div className="notice warn"><Icon name="lock" size={16} /><span><strong>A person checks first:</strong> nothing after this step runs until they choose, and the first choice always changes nothing.</span></div>
      <Block title="Who approves">
        <span className="row"><Text width={170} value={step.approver} onChange={(v) => set(['approver'], v)} label="Approver" /><span className="muted">notified by</span></span>
        <span className="row" style={{ gap: 14 }}>{['email', 'web', 'slack'].map((n) => (
          <Check key={n} checked={notify.includes(n)} onChange={(on) => set(['notify'], on ? [...notify, n] : notify.filter((x) => x !== n))}>{n}</Check>
        ))}</span>
        <span className="faint">In this prototype approvals are answered in the designer (Approvals, or the run's page).</span>
      </Block>
      <Block title="What they review">
        <span className="row"><span className="muted">Items</span><RefPicker value={step.items ?? ''} refs={refs} onChange={(v) => set(['items'], v || undefined)} path={p('items')} /></span>
        {step.items && <span className="row"><span className="muted">Each item's id field</span><Text width={120} value={step.item_id} onChange={(v) => set(['item_id'], v || undefined)} label="Item id" /></span>}
        {step.items && (
          <>
            <span className="muted">Pre-select an item when (CEL over <code className="mono">item</code>):</span>
            <Cel value={step.pre_select} onChange={(v) => set(['pre_select'], v || undefined)} path={p('pre_select')} rows={2} placeholder="size(item.flags) == 0" />
          </>
        )}
        <span className="muted">The text they see. <code className="mono">{'{step.field}'}</code> shows a value; a line <code className="mono">{'{items: … {field} …}'}</code> repeats per item.</span>
        <Area value={step.review} onChange={(v) => set(['review'], v)} rows={6} path={p('review')} mono label="Review text" />
      </Block>
      <Block title="Choices, in order">
        {choices.map((c, i) => (
          <div key={i} className="item-row">
            <span className="numbered">{i + 1}</span>
            <input className="input grow" value={c.label} aria-label="Choice label" onChange={(e) => set(['choices', i, 'label'], e.target.value)} />
            <Select value={c.passes} options={PASSES} labels={PASS_LABEL} onChange={(v) => set(['choices', i, 'passes'], v)} label="Passes" />
            {i > 0 && <button className="icon-btn" aria-label="Remove choice" onClick={() => set(['choices'], choices.filter((_, k) => k !== i))}><Icon name="x" size={12} /></button>}
          </div>
        ))}
        <FieldErrors path={p('choices')} /><FieldErrors path={p()} exact />
        <button className="link" onClick={() => set(['choices'], [...choices, { label: 'Another choice', passes: 'all' }])}><Icon name="plus" size={13} width={2} />Add choice</button>
        <span className="row"><span className="muted">If nobody answers within</span><Text width={48} value={step.timeout_hours} onChange={(v) => set(['timeout_hours'], Number(v) || 0)} label="Hours" /><span className="muted">hours, use choice 1</span></span>
      </Block>
    </>
  )
}
