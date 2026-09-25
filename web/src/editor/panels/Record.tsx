// A record type: the shape of data one step hands the next, and what makes two records the same.
import { Block, Cel, Guarantees, Icon } from '../../ui'
import { useEditor } from '../Editor'
import { FieldsEditor } from './common'

export default function Record({ name }: { name: string }) {
  const { draft, update, select } = useEditor()
  const rec = draft.records?.[name]
  if (!rec) return <span className="muted">This record type was removed.</span>
  const rename = (to: string) => {
    if (!to || draft.records[to]) return
    update(['records'], Object.fromEntries(Object.entries(draft.records).map(([k, v]) => [k === name ? to : k, v])))
    select({ type: 'record', name: to })
  }
  return (
    <>
      <div className="stack" style={{ gap: 2 }}>
        <span className="spread"><span className="eyebrow">Record type</span>
          <button className="icon-btn" aria-label="Delete record type" onClick={() => { if (confirm(`Delete ${name}?`)) { const c = { ...draft.records }; delete c[name]; update(['records'], c); select({ type: 'settings' }) } }}><Icon name="trash" size={13} /></button></span>
        <input className="title-input" defaultValue={name} aria-label="Record type name" onBlur={(e) => rename(e.target.value.trim())} />
        <span className="muted">A step that returns a {name} must fill in every required field; anything else is rejected before the next step sees it.</span>
      </div>
      <Block title="Fields"><FieldsEditor fields={rec.fields} path={['records', name, 'fields']} onChange={(f) => update(['records', name, 'fields'], f)} /></Block>
      <Block title="Same record when" aside="CEL over b">
        <Cel value={rec.identity} onChange={(v) => update(['records', name, 'identity'], v || undefined)} path={`records.${name}.identity`} rows={2}
          placeholder="b.type + ':' + norm(b.confirmation)" />
        <span className="muted">Used to remove duplicates and never add something twice. Pick fields a model can't word differently between runs. <code className="mono">norm()</code> ignores spaces, dashes and capitals; <code className="mono">date_of()</code> gives a timestamp's date.</span>
      </Block>
      <Block title="Checked by the service after every step">
        <Guarantees items={['Every date and time has a time zone.', 'Choices hold one of the listed values.', 'Unknown fields are rejected.']} />
      </Block>
    </>
  )
}
