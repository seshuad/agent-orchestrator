// A Free-form block drawn in the order its data sets: rows by what each step needs, solid lines for
// "needs", dotted lines for where the planner can loop back, and a frame round steps that can run
// at the same time. Laid out from the service's graph.
import type { Graph } from '../api'
import { Icon, KIND_ICON, Pill } from '../ui'

const W = 184, H = 62, PITCH = 100, GAP = 16, LOOP = '#C0527D', LEFT = 96, TOGETHER = '#3D6FB3'   // room on the left for loop lines and labels
const MARK: Record<string, [string, string]> = {
  required: ['required to finish', 'waiting'], auto: ['re-runs by itself', 'stopped'], focus: ['planner sets focus', 'free-form'],
}

export default function FreeFormGraph({ graph, selected, onSelect }: { graph: Graph; selected: string | null; onSelect: (id: string) => void }) {
  const rows: string[][] = []
  graph.nodes.forEach((n) => { (rows[n.row] ??= []).push(n.id) })
  const widest = Math.max(1, ...rows.map((r) => r?.length ?? 0))
  const width = Math.max(420, widest * (W + GAP) + 80) + LEFT
  const pos: Record<string, { x: number; y: number }> = {}
  rows.forEach((row, r) => (row ?? []).forEach((id, i) => {
    const rowWidth = row.length * W + (row.length - 1) * GAP
    pos[id] = { x: LEFT + (width - LEFT - rowWidth) / 2 + i * (W + GAP), y: r * PITCH }
  }))
  const height = Math.max(H, rows.length * PITCH - (PITCH - H))
  if (!graph.nodes.length) return <span className="muted" style={{ padding: 12 }}>No steps yet: add one from the step list.</span>

  const needs = graph.edges.map((e, i) => {
    const a = pos[e.from], b = pos[e.to]
    if (!a || !b) return null
    const x1 = a.x + W / 2, y1 = a.y + H, x2 = b.x + W / 2, y2 = b.y
    const mid = y1 + Math.max(10, (y2 - y1) / 2)
    const d = y2 > y1 ? `M${x1} ${y1} L${x1} ${mid} L${x2} ${mid} L${x2} ${y2}` : `M${a.x} ${a.y + H / 2} L${b.x + W} ${b.y + H / 2}`
    return <path key={`e${i}`} d={d} fill="none" stroke="#B7B7AC" strokeWidth={1.3} strokeDasharray={e.optional ? '2 3' : undefined} markerEnd="url(#ff-need)" />
  })
  // Loops that share a start and a reason are one line, to the leftmost step they can re-run.
  const grouped = new Map<string, { from: string; label: string; to: string[] }>()
  graph.loops.forEach((l) => {
    const k = `${l.from}|${l.label}`
    grouped.set(k, { from: l.from, label: l.label, to: [...(grouped.get(k)?.to ?? []), l.to] })
  })
  const loops = [...grouped.values()].map((g, i) => {
    const a = pos[g.from]
    const targets = g.to.filter((t) => pos[t]).sort((x, y) => pos[x].x - pos[y].x)
    if (!a || !targets.length) return null
    const b = pos[targets[0]]
    const x1 = a.x, y1 = a.y + H / 2, x2 = b.x, y2 = b.y + H / 2
    const bend = Math.min(x1, x2) - 30 - i * 14
    const label = g.to.length > 1 ? `${g.label}: any of ${g.to.length}` : g.label
    return (
      <g key={`l${i}`}>
        <path d={`M${x1} ${y1} C${bend} ${y1}, ${bend} ${y2}, ${x2} ${y2}`} fill="none" stroke={LOOP} strokeWidth={1.5} strokeDasharray="4 4" markerEnd="url(#ff-loop)" />
        <text x={bend + 4} y={(y1 + y2) / 2 + 3} fontSize={10} fill={LOOP} textAnchor="end" fontWeight={600}>{label}</text>
      </g>
    )
  })
  const together = (graph.groups ?? []).map((g) => {
    const xs = g.members.filter((m) => pos[m]).map((m) => pos[m])
    if (xs.length < 2) return null
    const x = Math.min(...xs.map((p) => p.x)) - 8, y = xs[0].y - 20, w = Math.max(...xs.map((p) => p.x)) + W + 8 - x
    return (
      <g key={g.name}>
        <rect x={x} y={y} width={w} height={H + 28} rx={10} fill="#3D6FB30A" stroke={TOGETHER} strokeWidth={1.2} strokeDasharray="5 3" />
        <text x={x + 10} y={y + 12} fontSize={10} fill={TOGETHER} fontWeight={600}>Can run together: the planner may start all {xs.length} at once</text>
      </g>
    )
  })
  return (
    <div style={{ position: 'relative', width, height: height + 4, margin: (graph.groups ?? []).length ? '26px 0 6px' : '6px 0' }}>
      <svg width={width} height={height} style={{ position: 'absolute', inset: 0, overflow: 'visible' }} aria-hidden="true">
        <defs>
          <marker id="ff-need" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 1L9 5L0 9z" fill="#B7B7AC" /></marker>
          <marker id="ff-loop" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 1L9 5L0 9z" fill={LOOP} /></marker>
        </defs>
        {together}{needs}{loops}
      </svg>
      {graph.nodes.map((n) => (
        <button key={n.id} type="button" onClick={(e) => { e.stopPropagation(); onSelect(n.id) }} className={`node${selected === n.id ? ' sel' : ''}`}
          style={{ position: 'absolute', left: pos[n.id].x, top: pos[n.id].y, width: W, height: H, padding: '6px 8px', justifyContent: 'center', gap: 3 }}>
          <span className="t" style={{ fontSize: 12 }}><Icon name={KIND_ICON[n.kind]} size={14} color="var(--faint)" /><span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 140 }}>{n.name}</span></span>
          <span className="row" style={{ gap: 5, justifyContent: 'center' }}><Pill kind={n.kind} />{n.mark && <Pill kind={MARK[n.mark][1]}>{MARK[n.mark][0]}</Pill>}</span>
        </button>
      ))}
    </div>
  )
}
