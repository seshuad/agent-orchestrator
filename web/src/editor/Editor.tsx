// The agent editor: step list, flow graph and the selected item's panel. Edits autosave; every save
// returns the service's feedback (errors pinned to fields, warnings, compiled YAML, Free-form graphs).
import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, type AgentDetail, type Feedback, type Json, type Reference } from '../api'
import { Dialog, Icon, Pill, ProblemsContext, SessionContext, Toast } from '../ui'
import { AiStrip, RefineDialog } from './AiNote'
import DeleteAgent from '../pages/DeleteAgent'
import RunNow from '../pages/RunNow'
import { getIn, pathStr, setIn, type Path, type Selection } from './model'
import Sidebar from './Sidebar'
import Flow from './Flow'
import Panel from './panels/Panel'

interface EditorApi {
  name: string
  draft: Json
  feedback: Feedback
  selection: Selection
  select: (s: Selection) => void
  update: (path: Path, value: unknown) => void
  remove: (path: Path) => void
  insert: (path: Path, index: number, value: Json) => void
  refs: Reference[]
  meta: AgentDetail['meta']
  setTestData: (set: string | null) => void
  hidePanel: () => void
}
export const EditorContext = createContext<EditorApi>(null as unknown as EditorApi)
export const useEditor = () => useContext(EditorContext)

export default function Editor() {
  const { name = '' } = useParams()
  const navigate = useNavigate()
  const [detail, setDetail] = useState<AgentDetail | null>(null)
  const [draft, setDraft] = useState<Json | null>(null)
  const [feedback, setFeedback] = useState<Feedback | null>(null)
  const [selection, setSelection] = useState<Selection>({ type: 'settings' })
  const [refs, setRefs] = useState<Reference[]>([])
  const [saveState, setSaveState] = useState<'saved' | 'saving' | 'error'>('saved')
  const [hasChanges, setHasChanges] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const [dialog, setDialog] = useState<'yaml' | 'publish' | 'run' | 'delete' | 'refine' | null>(null)
  const session = useContext(SessionContext)
  // The details panel can be hidden to give the graph the full width; the choice is remembered.
  const [panelOpen, setPanelOpen] = useState(() => { try { return localStorage.getItem('editor.details') !== 'hidden' } catch { return true } })
  const showPanel = useCallback((open: boolean) => {
    setPanelOpen(open)
    try { localStorage.setItem('editor.details', open ? 'shown' : 'hidden') } catch { /* private window */ }
  }, [])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLElement && ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)
      if (e.key === 'Escape' && !typing && !document.querySelector('.dialog')) showPanel(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [showPanel])
  const [note, setNote] = useState('')
  const dirty = useRef(false)

  const load = useCallback((first: boolean) => {
    api.agent(name).then((d) => {
      dirty.current = false
      setDetail(d); setDraft(d.draft); setFeedback(d.feedback); setHasChanges(d.has_changes)
      if (first && d.draft.steps?.length) setSelection({ type: 'step', path: ['steps', 0] })
      if (!first) setSelection(d.draft.steps?.length ? { type: 'step', path: ['steps', 0] } : { type: 'settings' })
    }).catch(() => navigate('/'))
  }, [name, navigate])
  useEffect(() => { load(true) }, [load])

  // Autosave: a short pause after the last edit, then the service checks and compiles the draft.
  useEffect(() => {
    if (!draft || !dirty.current) return
    setSaveState('saving')
    const t = setTimeout(() => {
      api.saveAgent(name, draft).then((r) => { setFeedback(r.feedback); setHasChanges(r.has_changes); setSaveState('saved') })
        .catch(() => setSaveState('error'))
    }, 600)
    return () => clearTimeout(t)
  }, [draft, name])

  const stepId = selection.type === 'step' && draft ? getIn(draft, selection.path)?.id : null
  useEffect(() => {
    if (saveState === 'saved') api.references(name, stepId).then(setRefs).catch(() => {})
  }, [name, stepId, saveState])

  const change = useCallback((fn: (d: Json) => Json) => { dirty.current = true; setDraft((d) => (d ? fn(d) : d)) }, [])
  const update = useCallback((path: Path, value: unknown) => change((d) => setIn(d, path, value)), [change])
  const remove = useCallback((path: Path) => {
    change((d) => {
      const parent = getIn(d, path.slice(0, -1))
      const key = path[path.length - 1]
      if (Array.isArray(parent)) return setIn(d, path.slice(0, -1), parent.filter((_: unknown, i: number) => i !== key))
      const copy = { ...parent }; delete copy[key]
      return setIn(d, path.slice(0, -1), copy)
    })
  }, [change])
  const insert = useCallback((path: Path, index: number, value: Json) => {
    change((d) => { const list = [...(getIn(d, path) ?? [])]; list.splice(index, 0, value); return setIn(d, path, list) })
  }, [change])

  if (!detail || !draft || !feedback) return <div className="page"><span className="spinner" /></div>

  const publish = async () => {
    try {
      const r = await api.publish(name, note)
      setDetail(r); setHasChanges(false); setDialog(null); setNote(''); setToast(`Published version ${r.version}. Runs use it from now on.`)
    } catch (e: any) { setToast(e.message) }
  }
  const status = detail.meta.published ? (hasChanges ? `Published v${detail.meta.published} · unpublished edits` : `Published v${detail.meta.published}`) : 'Draft'
  const ctx: EditorApi = {
    name, draft, feedback, selection, update, remove, insert, refs, meta: detail.meta,
    select: (s) => { setSelection(s); showPanel(true) },          // picking something shows its details again
    hidePanel: () => showPanel(false),
    setTestData: (set) => api.setTestData(name, set).then((d) => setDetail(d)),
  }

  return (
    <EditorContext.Provider value={ctx}>
      <ProblemsContext.Provider value={feedback.errors}>
        <div className="stack grow" style={{ gap: 0, minHeight: 0 }}>
          <div className="spread" style={{ padding: '10px 22px', background: '#fff', borderBottom: '1px solid var(--line)' }}>
            <div className="row" style={{ gap: 10 }}>
              <Link to="/" style={{ color: 'var(--soft-text)' }}>Agents</Link><span style={{ color: '#B7B7AC' }}>/</span>
              <span style={{ fontSize: 16, fontWeight: 700 }}>{name}</span>
              <Pill kind={detail.meta.published && !hasChanges ? 'published' : 'draft'}>{status}</Pill>
              <nav className="row" style={{ gap: 4, marginLeft: 10 }}>
                <span className="btn small" style={{ border: 'none', background: 'var(--acc-soft)' }}>Design</span>
                <Link to={`/agents/${name}/runs`} className="btn small" style={{ border: 'none' }}>Runs</Link>
              </nav>
            </div>
            <div className="row" style={{ gap: 10 }}>
              <span className="save-state">{saveState === 'saving' ? 'Saving…' : saveState === 'error' ? 'Couldn’t save' : 'All changes saved'}</span>
              {feedback.errors.length > 0
                ? <span className="pill failed" title={feedback.errors.map((e) => e.message).join('\n')}>{feedback.errors.length} to fix</span>
                : <span className="pill succeeded">Ready</span>}
              {session?.claude_api && <button className="btn" onClick={() => setDialog('refine')}><Icon name="spark" size={13} color="var(--acc)" />Refine with AI</button>}
              <button className="btn" onClick={() => setDialog('yaml')}><Icon name="code" size={14} />Compiled YAML</button>
              <button className="btn" onClick={() => setDialog('run')} disabled={!feedback.ok}><Icon name="play" size={12} width={2} />Test run</button>
              <button className="btn primary" disabled={!feedback.ok || !hasChanges} onClick={() => setDialog('publish')}>Publish</button>
              <button className="btn" onClick={() => setDialog('delete')} aria-label="Delete agent" title="Delete agent"><Icon name="trash" size={15} color="var(--muted)" /></button>
            </div>
          </div>
          <AiStrip key={String(detail.meta.ai?.at ?? '')} name={name} meta={detail.meta} onUndo={() => { load(false); setToast('Undid Claude\'s change.') }}
            onDismiss={() => api.clearAiNote(name).then(() => setDetail({ ...detail, meta: { ...detail.meta, ai: null } }))} />
          {feedback.errors.some((e) => !e.path) && (
            <div className="notice bad" style={{ borderRadius: 0, borderLeft: 'none', borderRight: 'none' }}>
              <Icon name="alert" size={14} />{feedback.errors.filter((e) => !e.path).map((e) => e.message).join(' ')}
            </div>
          )}
          {feedback.warnings.length > 0 && (
            <div className="notice warn" style={{ borderRadius: 0, borderLeft: 'none', borderRight: 'none' }}>
              <Icon name="alert" size={14} />{feedback.warnings.map((w) => w.message).join(' ')}
            </div>
          )}
          <div className="row grow" style={{ alignItems: 'stretch', flexWrap: 'nowrap', gap: 0, minHeight: 0 }}>
            <Sidebar />
            <Flow />
            {panelOpen
              ? <Panel key={selection.type === 'step' ? pathStr(selection.path) : selection.type + ((selection as any).name ?? '')} />
              : <button type="button" className="panel-rail" onClick={() => showPanel(true)} aria-label="Show details" title="Show details">
                  <Icon name="chev" size={14} color="var(--muted)" width={2} /><span>Details</span></button>}
          </div>
        </div>
        {dialog === 'yaml' && (
          <Dialog title="Compiled Conductor YAML" sub="What the service runs. Written by the compiler from this draft; nobody edits it." onClose={() => setDialog(null)}
            footer={<button className="btn" onClick={() => setDialog(null)}>Close</button>}>
            {feedback.compiled ? <div className="pre" style={{ maxHeight: '60vh' }}>{feedback.compiled}</div>
              : <div className="notice bad">It doesn't compile yet: {feedback.errors.map((e) => e.message).join(' ')}</div>}
          </Dialog>
        )}
        {dialog === 'publish' && (
          <Dialog title={`Publish ${name}`} sub={`Becomes version ${(detail.meta.versions?.length ?? 0) + 1}. Scheduled and manual runs use it from now on; runs in progress keep their version.`}
            onClose={() => setDialog(null)} footer={<><button className="btn" onClick={() => setDialog(null)}>Cancel</button><button className="btn primary" onClick={publish}>Publish</button></>}>
            {feedback.warnings.length > 0 && (
              <div className="notice warn"><Icon name="alert" size={15} /><span>{feedback.warnings.map((w) => w.message).join(' ')}</span></div>
            )}
            <label className="stack"><span className="muted">What changed (optional)</span>
              <input className="input full" value={note} onChange={(e) => setNote(e.target.value)} aria-label="What changed" /></label>
          </Dialog>
        )}
        {dialog === 'refine' && <RefineDialog name={name} onClose={() => setDialog(null)} onDone={() => { setDialog(null); load(false); setToast('Claude changed the draft. Check it, or undo the change.') }} />}
        {dialog === 'delete' && <DeleteAgent agent={name} onClose={() => setDialog(null)} onDeleted={() => navigate('/')} />}
        {dialog === 'run' && <RunNow agent={name} draftOnly onClose={() => setDialog(null)} onStarted={(r) => navigate(`/agents/${name}/runs/${r.id}`)} />}
        <Toast message={toast} onDone={() => setToast(null)} />
      </ProblemsContext.Provider>
    </EditorContext.Provider>
  )
}
