// The accounts this agent uses, picked from the workspace's connections. Steps pick actions on their own panels.
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Connection, type Json } from '../../api'
import { Block, FieldErrors, Guarantees, Icon, NameInput, Pill, Select } from '../../ui'
import { useEditor } from '../Editor'

export default function AgentConnections() {
  const { draft, update } = useEditor()
  const [accounts, setAccounts] = useState<Connection[]>([])
  useEffect(() => { api.connections().then(setAccounts) }, [])
  const conns: Json = draft.connections ?? {}
  const byId = Object.fromEntries(accounts.map((a) => [a.id, a]))
  const usedBy = (id: string) => {
    const out: string[] = []
    const walk = (steps: Json[] = []) => steps.forEach((s) => { if (s.uses?.connection === id) out.push(s.name); walk(s.steps) })
    walk(draft.steps)
    return out
  }
  // Renaming a connection renames it in the steps that use it too.
  const rename = (from: string, to: string) => {
    const relink = (steps: Json[] = []): Json[] => steps.map((s) => ({
      ...s, ...(s.uses?.connection === from ? { uses: { ...s.uses, connection: to } } : {}), ...(s.steps ? { steps: relink(s.steps) } : {}),
    }))
    update([], { ...draft, connections: Object.fromEntries(Object.entries(conns).map(([k, v]) => [k === from ? to : k, v])), steps: relink(draft.steps) })
  }
  const pick = (id: string, accountId: string) => {
    const a = byId[accountId]
    if (a) update(['connections', id], { service: a.service, permission: a.permissions.join(', '), account: a.id })
  }
  const add = () => {
    let n = 1; while (conns[`connection-${n}`]) n++
    const first = accounts[0]
    update(['connections', `connection-${n}`], first ? { service: first.service, permission: first.permissions.join(', '), account: first.id } : { service: 'gmail', permission: '' })
  }
  return (
    <>
      <div className="stack" style={{ gap: 2 }}><span className="eyebrow">Agent</span><span style={{ fontSize: 17, fontWeight: 700 }}>Connections</span></div>
      <span className="muted">Which of the workspace's connected accounts this agent uses. Each step then gets only the actions you tick on that step, within the account's permissions.</span>
      {Object.entries(conns).map(([id, c]: [string, any], i) => {
        const a = c.account ? byId[c.account] : undefined
        return (
          <Block key={i} title={<span className="row"><Icon name={c.service === 'gmail' ? 'mail' : c.service === 'google-calendar' ? 'calendar' : c.service === 'github' ? 'code' : 'group'} size={16} color="var(--muted)" />{a?.label ?? 'Pick an account'}</span>}
            aside={<button className="link" style={{ color: 'var(--faint)' }} onClick={() => { const x = { ...conns }; delete x[id]; update(['connections'], x) }}>remove</button>}>
            <span className="row"><span className="muted" style={{ width: 70 }}>Account</span>
              <Select value={c.account ?? ''} options={accounts.map((x) => x.id)} onChange={(v) => pick(id, v)} label="Account"
                labels={{ '': 'Pick an account', ...Object.fromEntries(accounts.map((x) => [x.id, `${x.label} · ${x.service_name} · ${x.account}`])) }} /></span>
            {a && <span className="row" style={{ gap: 6 }}><span className="muted" style={{ width: 70 }}>May</span>{a.permissions.map((p) => <Pill key={p} kind="published">{p}</Pill>)}</span>}
            <span className="row"><span className="muted" style={{ width: 70 }}>Steps call it</span><NameInput value={id} taken={Object.keys(conns)} onRename={(v) => rename(id, v)} normalize={(v) => v.replace(/\s+/g, '-')} width={180} label="Name used by steps" /></span>
            <span className="faint">Used by: {usedBy(id).join(', ') || 'no step yet'}</span>
            <FieldErrors path={`connections.${id}`} />
          </Block>
        )
      })}
      <span className="row" style={{ gap: 16 }}>
        <button className="link" onClick={add}><Icon name="plus" size={13} width={2} />Use another account</button>
        <Link to="/connections" style={{ fontSize: 12, fontWeight: 600 }}>Manage the workspace's connections ↗</Link>
      </span>
      <Block title="Content from these connections">
        <Guarantees items={['Email is treated as untrusted: other people wrote it.', 'Whether a person approves before the agent changes anything is your choice: add an Approve step if they should.']} />
      </Block>
    </>
  )
}
