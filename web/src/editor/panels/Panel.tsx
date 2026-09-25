// The right-hand panel: whatever is selected.
import { Icon } from '../../ui'
import { useEditor } from '../Editor'
import { getIn } from '../model'
import Act from './Act'
import AgentConnections from './AgentConnections'
import Approve from './Approve'
import Ask from './Ask'
import Branch from './Branch'
import BuiltIn from './BuiltIn'
import FreeForm from './FreeForm'
import Record from './Record'
import Settings from './Settings'

const PANELS: Record<string, () => React.JSX.Element> = { ask: Ask, 'built-in': BuiltIn, 'free-form': FreeForm, branch: Branch, approve: Approve, act: Act }

export default function Panel() {
  const { selection, draft, hidePanel } = useEditor()
  let body: React.ReactNode
  if (selection.type === 'settings') body = <Settings />
  else if (selection.type === 'connections') body = <AgentConnections />
  else if (selection.type === 'record') body = <Record name={selection.name} />
  else {
    const step = getIn(draft, selection.path)
    const Kind = step ? PANELS[step.kind] : null
    body = Kind ? <Kind /> : <span className="muted">Select a step.</span>
  }
  return (
    <div className="panel">
      <button type="button" className="panel-hide" onClick={hidePanel} aria-label="Hide details" title="Hide details (Esc)">
        <Icon name="chev" size={13} width={2} />Hide</button>
      <div className="scroll">{body}</div>
    </div>
  )
}
