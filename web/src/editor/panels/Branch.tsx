// Branch: paths checked in order; the first that matches runs. No model decides the path.
import type { Json } from '../../api'
import { Block, Cel, Guarantees, Icon, Select } from '../../ui'
import { StepHeader, useStep } from './common'

export default function Branch() {
  const { step, set, p, draft } = useStep()
  const paths: Json[] = step.paths ?? []
  const targets = ['next', 'end', ...(draft.steps ?? []).map((s: Json) => s.id).filter((id: string) => id !== step.id)]
  const labels = { next: 'The next step', end: 'End the run', ...Object.fromEntries((draft.steps ?? []).map((s: Json) => [s.id, s.name])) }
  return (
    <>
      <StepHeader note="Paths are checked in order and the first match runs." />
      <Block title="Paths, in order">
        {paths.map((path, i) => {
          const last = i === paths.length - 1
          return (
            <div key={i} className="op">
              <span className="row" style={{ flexWrap: 'nowrap' }}><span className="numbered">{i + 1}</span>
                <input className="input grow" value={path.name} aria-label="Path name" onChange={(e) => set(['paths', i, 'name'], e.target.value)} disabled={last} />
                {!last && <button className="icon-btn" aria-label="Remove path" onClick={() => set(['paths'], paths.filter((_, k) => k !== i))}><Icon name="x" size={12} /></button>}</span>
              {!last && <><span className="muted">when</span><Cel value={path.when} onChange={(v) => set(['paths', i, 'when'], v)} path={p('paths', i, 'when')} /></>}
              <span className="row"><span className="muted">then go to</span><Select value={path.then} options={targets} labels={labels} onChange={(v) => set(['paths', i, 'then'], v)} label="Then" /></span>
            </div>
          )
        })}
        <button className="link" onClick={() => set(['paths'], [...paths.slice(0, -1), { name: `Path ${paths.length}`, when: '', then: 'next' }, paths[paths.length - 1]])}>
          <Icon name="plus" size={13} width={2} />Add a path</button>
        <span className="muted">Conditions read <code className="mono">steps.&lt;id&gt;</code> for earlier results, e.g. <code className="mono">size(steps.find_and_check.trips) == 0</code>.</span>
      </Block>
      <Block title="Checked by the service">
        <Guarantees items={['Conditions compare results from earlier steps, never free text a model wrote.', 'There is always an Otherwise path, so every run goes somewhere.']} />
      </Block>
    </>
  )
}
