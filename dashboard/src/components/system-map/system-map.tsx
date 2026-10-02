"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  AlertTriangle,
  Boxes,
  FileCode2,
  Filter,
  GitBranch,
  Maximize2,
  RefreshCw,
  Search,
  ShieldCheck,
  X,
} from "lucide-react"

type MapNode = {
  id: string
  label: string
  path: string
  kind: "python" | "typescript" | "spec" | "test" | "tool"
  subsystem: string
  lineCount: number
}

type MapEdge = {
  id: string
  from: string
  to: string
  relation: "IMPORT"
  evidenceLevel: "STATIC" | "RUNTIME" | "UNKNOWN"
  sourcePath: string
  sourceLine: number
}

type Snapshot = {
  schemaVersion: 1
  generatedAt?: string
  evidenceMode?: string
  status?: string
  error?: string
  limitations?: string[]
  nodes: MapNode[]
  edges: MapEdge[]
  truncated?: boolean
}

type PositionedNode = MapNode & { x: number; y: number }

type Transform = { x: number; y: number; scale: number }

const EMPTY: Snapshot = { schemaVersion: 1, nodes: [], edges: [] }
const WIDTH = 1500
const HEIGHT = 920

const SUBSYSTEM_TONES: Record<string, { fill: string; stroke: string; halo: string }> = {
  Epistemic: { fill: "#4f46e5", stroke: "#a5b4fc", halo: "rgba(79,70,229,.16)" },
  Cognitive: { fill: "#7c3aed", stroke: "#c4b5fd", halo: "rgba(124,58,237,.16)" },
  Knowledge: { fill: "#0f766e", stroke: "#99f6e4", halo: "rgba(15,118,110,.16)" },
  Governance: { fill: "#b45309", stroke: "#fde68a", halo: "rgba(180,83,9,.16)" },
  Security: { fill: "#be123c", stroke: "#fda4af", halo: "rgba(190,18,60,.16)" },
  Execution: { fill: "#0369a1", stroke: "#7dd3fc", halo: "rgba(3,105,161,.16)" },
  Intelligence: { fill: "#4338ca", stroke: "#c7d2fe", halo: "rgba(67,56,202,.16)" },
  "World / Risk": { fill: "#9f1239", stroke: "#f9a8d4", halo: "rgba(159,18,57,.16)" },
  "Self-Improvement": { fill: "#047857", stroke: "#6ee7b7", halo: "rgba(4,120,87,.16)" },
  Interfaces: { fill: "#0e7490", stroke: "#67e8f9", halo: "rgba(14,116,144,.16)" },
  Dashboard: { fill: "#475569", stroke: "#cbd5e1", halo: "rgba(71,85,105,.16)" },
  Tests: { fill: "#365314", stroke: "#bef264", halo: "rgba(54,83,20,.16)" },
  Specifications: { fill: "#854d0e", stroke: "#fde047", halo: "rgba(133,77,14,.16)" },
  Tooling: { fill: "#3f3f46", stroke: "#d4d4d8", halo: "rgba(63,63,70,.16)" },
  "SCP Core": { fill: "#334155", stroke: "#cbd5e1", halo: "rgba(51,65,85,.16)" },
}

function toneFor(subsystem: string) {
  return SUBSYSTEM_TONES[subsystem] ?? SUBSYSTEM_TONES["SCP Core"]
}

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value))
}

function formatTime(value?: string) {
  if (!value) return "UNKNOWN"
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString("vi-VN")
}

function graphLayout(nodes: MapNode[]): PositionedNode[] {
  const groups = new Map<string, MapNode[]>()
  nodes.forEach((node) => {
    const bucket = groups.get(node.subsystem) ?? []
    bucket.push(node)
    groups.set(node.subsystem, bucket)
  })

  const names = [...groups.keys()].sort((a, b) => a.localeCompare(b))
  const columns = Math.max(1, Math.ceil(Math.sqrt(names.length * 1.55)))
  const rows = Math.max(1, Math.ceil(names.length / columns))
  const cellWidth = WIDTH / columns
  const cellHeight = HEIGHT / rows
  const positioned: PositionedNode[] = []

  names.forEach((name, groupIndex) => {
    const group = groups.get(name) ?? []
    const column = groupIndex % columns
    const row = Math.floor(groupIndex / columns)
    const cx = cellWidth * (column + 0.5)
    const cy = cellHeight * (row + 0.5)
    const maxRadius = Math.min(cellWidth, cellHeight) * 0.34
    const ordered = [...group].sort((a, b) => b.lineCount - a.lineCount || a.path.localeCompare(b.path))

    ordered.forEach((node, index) => {
      if (index === 0) {
        positioned.push({ ...node, x: cx, y: cy })
        return
      }
      const ring = Math.floor(Math.sqrt(index))
      const ringStart = ring * ring
      const ringCount = Math.max(5, ring * 6)
      const positionInRing = index - ringStart
      const angle = (positionInRing / ringCount) * Math.PI * 2 + groupIndex * 0.31
      const radius = Math.min(maxRadius, 44 + ring * 34)
      positioned.push({
        ...node,
        x: cx + Math.cos(angle) * radius,
        y: cy + Math.sin(angle) * radius,
      })
    })
  })

  return positioned
}

function NodeBadge({ kind }: { kind: MapNode["kind"] }) {
  const labels = { python: "PY", typescript: "TS", spec: "SPEC", test: "TEST", tool: "TOOL" }
  return <span className="rounded border border-white/10 bg-white/[0.05] px-1.5 py-0.5 text-[9px] font-semibold text-slate-400">{labels[kind]}</span>
}

export function SystemMap() {
  const [snapshot, setSnapshot] = useState<Snapshot>(EMPTY)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState("")
  const [subsystem, setSubsystem] = useState("ALL")
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null)
  const [transform, setTransform] = useState<Transform>({ x: 0, y: 0, scale: 1 })
  const [dragOrigin, setDragOrigin] = useState<{ x: number; y: number; tx: number; ty: number } | null>(null)
  const svgRef = useRef<SVGSVGElement | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await fetch("/api/system-map", { cache: "no-store" })
      const payload = (await response.json()) as Snapshot
      setSnapshot(payload)
      if (!response.ok) setError(payload.error || "System Map source scan unavailable")
    } catch (err) {
      setSnapshot(EMPTY)
      setError(err instanceof Error ? err.message : "System Map source scan unavailable")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const subsystems = useMemo(
    () => [...new Set(snapshot.nodes.map((node) => node.subsystem))].sort((a, b) => a.localeCompare(b)),
    [snapshot.nodes],
  )

  const visibleNodes = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return snapshot.nodes.filter((node) => {
      if (subsystem !== "ALL" && node.subsystem !== subsystem) return false
      if (!needle) return true
      return `${node.label} ${node.path} ${node.subsystem}`.toLowerCase().includes(needle)
    })
  }, [snapshot.nodes, subsystem, query])

  const positioned = useMemo(() => graphLayout(visibleNodes), [visibleNodes])
  const positionById = useMemo(() => new Map(positioned.map((node) => [node.id, node])), [positioned])
  const visibleIds = useMemo(() => new Set(positioned.map((node) => node.id)), [positioned])
  const visibleEdges = useMemo(
    () => snapshot.edges.filter((edge) => visibleIds.has(edge.from) && visibleIds.has(edge.to)),
    [snapshot.edges, visibleIds],
  )

  const selectedNode = selectedNodeId ? snapshot.nodes.find((node) => node.id === selectedNodeId) ?? null : null
  const selectedEdge = selectedEdgeId ? snapshot.edges.find((edge) => edge.id === selectedEdgeId) ?? null : null
  const connectedEdges = useMemo(
    () => selectedNode ? snapshot.edges.filter((edge) => edge.from === selectedNode.id || edge.to === selectedNode.id) : [],
    [selectedNode, snapshot.edges],
  )

  const fit = useCallback(() => setTransform({ x: 0, y: 0, scale: 1 }), [])

  const onWheel = useCallback((event: React.WheelEvent<SVGSVGElement>) => {
    event.preventDefault()
    const nextScale = clamp(transform.scale * (event.deltaY > 0 ? 0.9 : 1.1), 0.45, 3.5)
    setTransform((current) => ({ ...current, scale: nextScale }))
  }, [transform.scale])

  return (
    <main className="min-h-screen bg-[#090d15] text-slate-100">
      <div className="mx-auto max-w-[1800px] px-4 py-5 sm:px-6 lg:px-8">
        <header className="flex flex-col gap-4 border-b border-white/[0.08] pb-5 xl:flex-row xl:items-end xl:justify-between">
          <div>
            <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.24em] text-cyan-300/80">
              <GitBranch className="h-4 w-4" /> SCP Control Center
            </div>
            <h1 className="mt-2 text-3xl font-semibold tracking-tight text-white sm:text-4xl">System Map</h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
              Bản đồ cấu trúc SCP lấy trực tiếp từ source. V1 chỉ hiển thị quan hệ tĩnh đã quan sát được; không biến import thành bằng chứng runtime.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center gap-2 rounded-full border border-indigo-400/20 bg-indigo-400/10 px-3 py-1.5 text-xs text-indigo-200">
              <ShieldCheck className="h-3.5 w-3.5" /> Evidence: STATIC
            </span>
            <button onClick={fit} className="inline-flex items-center gap-2 rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2 text-xs text-slate-300 hover:bg-white/[0.08]">
              <Maximize2 className="h-4 w-4" /> Fit
            </button>
            <button onClick={load} disabled={loading} className="inline-flex items-center gap-2 rounded-lg border border-cyan-400/20 bg-cyan-400/10 px-3 py-2 text-xs text-cyan-100 hover:bg-cyan-400/15 disabled:opacity-50">
              <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} /> Quét lại
            </button>
          </div>
        </header>

        <section className="mt-5 grid gap-3 md:grid-cols-4">
          <div className="rounded-xl border border-white/[0.08] bg-white/[0.035] p-3"><div className="text-[10px] uppercase tracking-[0.18em] text-slate-500">Nodes</div><div className="mt-1 text-xl font-semibold text-white">{loading ? "—" : snapshot.nodes.length}</div></div>
          <div className="rounded-xl border border-white/[0.08] bg-white/[0.035] p-3"><div className="text-[10px] uppercase tracking-[0.18em] text-slate-500">Static edges</div><div className="mt-1 text-xl font-semibold text-white">{loading ? "—" : snapshot.edges.length}</div></div>
          <div className="rounded-xl border border-white/[0.08] bg-white/[0.035] p-3"><div className="text-[10px] uppercase tracking-[0.18em] text-slate-500">Subsystems</div><div className="mt-1 text-xl font-semibold text-white">{loading ? "—" : subsystems.length}</div></div>
          <div className="rounded-xl border border-white/[0.08] bg-white/[0.035] p-3"><div className="text-[10px] uppercase tracking-[0.18em] text-slate-500">Observed</div><div className="mt-1 truncate text-sm font-medium text-slate-200">{loading ? "SCANNING" : formatTime(snapshot.generatedAt)}</div></div>
        </section>

        <section className="mt-4 flex flex-col gap-3 rounded-xl border border-white/[0.08] bg-white/[0.025] p-3 lg:flex-row lg:items-center">
          <div className="relative min-w-0 flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Tìm module, file, subsystem…" className="w-full rounded-lg border border-white/10 bg-black/20 py-2 pl-9 pr-3 text-sm text-slate-100 outline-none placeholder:text-slate-600 focus:border-cyan-400/30" />
          </div>
          <div className="flex items-center gap-2">
            <Filter className="h-4 w-4 text-slate-500" />
            <select value={subsystem} onChange={(event) => setSubsystem(event.target.value)} className="rounded-lg border border-white/10 bg-[#0e1420] px-3 py-2 text-sm text-slate-300 outline-none">
              <option value="ALL">Tất cả subsystem</option>
              {subsystems.map((name) => <option key={name} value={name}>{name}</option>)}
            </select>
          </div>
          <div className="rounded-lg border border-white/[0.07] bg-black/20 px-3 py-2 text-xs text-slate-500">Runtime overlay: <span className="text-amber-300">NOT CONNECTED</span></div>
        </section>

        {error && (
          <div className="mt-4 flex items-start gap-3 rounded-xl border border-amber-400/20 bg-amber-400/[0.07] p-4 text-sm text-amber-100">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <div><div className="font-medium">Không có source graph khả dụng</div><div className="mt-1 text-amber-200/70">{error}. Không suy đoán trạng thái thay thế.</div></div>
          </div>
        )}

        <section className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
          <div className="relative min-h-[720px] overflow-hidden rounded-2xl border border-white/[0.08] bg-[#0b101a] shadow-2xl shadow-black/25">
            <div className="pointer-events-none absolute inset-0 opacity-40" style={{ backgroundImage: "radial-gradient(circle at 1px 1px, rgba(148,163,184,.16) 1px, transparent 0)", backgroundSize: "24px 24px" }} />
            {loading && <div className="absolute inset-0 z-20 grid place-items-center bg-[#0b101a]/70"><div className="flex items-center gap-3 text-sm text-slate-400"><RefreshCw className="h-4 w-4 animate-spin" /> Đang quét source SCP…</div></div>}
            {!loading && positioned.length === 0 && !error && <div className="absolute inset-0 z-20 grid place-items-center text-sm text-slate-500">NO SOURCE NODES</div>}
            <svg
              ref={svgRef}
              viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
              className="relative z-10 h-[720px] w-full cursor-grab touch-none active:cursor-grabbing"
              onWheel={onWheel}
              onPointerDown={(event) => {
                if (event.button !== 0) return
                setDragOrigin({ x: event.clientX, y: event.clientY, tx: transform.x, ty: transform.y })
                event.currentTarget.setPointerCapture(event.pointerId)
              }}
              onPointerMove={(event) => {
                if (!dragOrigin) return
                const rect = event.currentTarget.getBoundingClientRect()
                const scaleX = WIDTH / rect.width
                const scaleY = HEIGHT / rect.height
                setTransform((current) => ({ ...current, x: dragOrigin.tx + (event.clientX - dragOrigin.x) * scaleX, y: dragOrigin.ty + (event.clientY - dragOrigin.y) * scaleY }))
              }}
              onPointerUp={(event) => {
                setDragOrigin(null)
                try { event.currentTarget.releasePointerCapture(event.pointerId) } catch { /* already released */ }
              }}
              onPointerCancel={() => setDragOrigin(null)}
              aria-label="SCP System Map"
            >
              <defs>
                <filter id="nodeGlow" x="-80%" y="-80%" width="260%" height="260%"><feGaussianBlur stdDeviation="5" result="blur" /><feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
                <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="rgba(148,163,184,.45)" /></marker>
              </defs>
              <g transform={`translate(${transform.x} ${transform.y}) scale(${transform.scale})`}>
                {visibleEdges.map((edge) => {
                  const from = positionById.get(edge.from)
                  const to = positionById.get(edge.to)
                  if (!from || !to) return null
                  const active = selectedEdgeId === edge.id || selectedNodeId === edge.from || selectedNodeId === edge.to
                  return (
                    <line
                      key={edge.id}
                      x1={from.x}
                      y1={from.y}
                      x2={to.x}
                      y2={to.y}
                      stroke={active ? "rgba(103,232,249,.68)" : "rgba(100,116,139,.20)"}
                      strokeWidth={active ? 1.7 : 0.85}
                      markerEnd="url(#arrow)"
                      className="cursor-pointer"
                      onPointerDown={(event) => event.stopPropagation()}
                      onClick={(event) => { event.stopPropagation(); setSelectedEdgeId(edge.id) }}
                    />
                  )
                })}

                {positioned.map((node) => {
                  const tone = toneFor(node.subsystem)
                  const selected = selectedNodeId === node.id
                  const radius = clamp(8 + Math.log2(Math.max(2, node.lineCount)) * 1.35, 10, 22)
                  return (
                    <g key={node.id} className="cursor-pointer" onPointerDown={(event) => event.stopPropagation()} onClick={(event) => { event.stopPropagation(); setSelectedNodeId(node.id); setSelectedEdgeId(null) }}>
                      <circle cx={node.x} cy={node.y} r={radius + 10} fill={tone.halo} opacity={selected ? 1 : 0.55} />
                      <circle cx={node.x} cy={node.y} r={radius} fill={tone.fill} stroke={selected ? "#ffffff" : tone.stroke} strokeWidth={selected ? 2.8 : 1.25} filter={selected ? "url(#nodeGlow)" : undefined} />
                      {(selected || radius >= 17) && <text x={node.x} y={node.y + radius + 14} textAnchor="middle" fill="rgba(226,232,240,.88)" fontSize="9" fontWeight="600">{node.label.length > 24 ? `${node.label.slice(0, 21)}…` : node.label}</text>}
                    </g>
                  )
                })}
              </g>
            </svg>
            <div className="absolute bottom-3 left-3 z-20 rounded-lg border border-white/[0.08] bg-[#090d15]/90 px-3 py-2 text-[10px] text-slate-500 backdrop-blur">Wheel: zoom · Kéo nền: pan · Click node/edge: evidence</div>
          </div>

          <aside className="min-h-[720px] rounded-2xl border border-white/[0.08] bg-[#0d131e] p-4 shadow-xl shadow-black/20">
            <div className="flex items-center justify-between gap-3 border-b border-white/[0.08] pb-3">
              <div><div className="text-[10px] font-semibold uppercase tracking-[0.2em] text-slate-500">Inspector</div><div className="mt-1 text-lg font-semibold text-white">{selectedNode ? "Node" : selectedEdge ? "Why this edge?" : "System Map"}</div></div>
              {(selectedNode || selectedEdge) && <button className="rounded-lg border border-white/[0.08] p-1.5 text-slate-500 hover:text-white" onClick={() => { setSelectedNodeId(null); setSelectedEdgeId(null) }}><X className="h-4 w-4" /></button>}
            </div>

            {selectedNode ? (
              <div className="mt-4 space-y-4">
                <div>
                  <div className="flex items-center gap-2"><Boxes className="h-4 w-4 text-cyan-300" /><h2 className="font-semibold text-white">{selectedNode.label}</h2></div>
                  <div className="mt-2 break-all rounded-lg border border-white/[0.07] bg-black/20 p-2 font-mono text-[11px] text-slate-400">{selectedNode.path}</div>
                </div>
                <div className="grid grid-cols-2 gap-2 text-xs">
                  <div className="rounded-lg bg-white/[0.035] p-3"><div className="text-slate-500">Subsystem</div><div className="mt-1 text-slate-200">{selectedNode.subsystem}</div></div>
                  <div className="rounded-lg bg-white/[0.035] p-3"><div className="text-slate-500">Lines</div><div className="mt-1 text-slate-200">{selectedNode.lineCount}</div></div>
                  <div className="rounded-lg bg-white/[0.035] p-3"><div className="text-slate-500">Evidence</div><div className="mt-1 text-indigo-200">STATIC</div></div>
                  <div className="rounded-lg bg-white/[0.035] p-3"><div className="text-slate-500">Runtime</div><div className="mt-1 text-amber-300">UNKNOWN</div></div>
                </div>
                <div>
                  <div className="mb-2 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Relationships ({connectedEdges.length})</div>
                  <div className="max-h-[390px] space-y-2 overflow-y-auto pr-1">
                    {connectedEdges.length === 0 && <div className="rounded-lg border border-dashed border-white/10 p-3 text-xs text-slate-600">Không thấy static import edge cho node này.</div>}
                    {connectedEdges.map((edge) => {
                      const outbound = edge.from === selectedNode.id
                      const otherId = outbound ? edge.to : edge.from
                      const other = snapshot.nodes.find((node) => node.id === otherId)
                      return (
                        <button key={edge.id} onClick={() => { setSelectedEdgeId(edge.id); setSelectedNodeId(null) }} className="w-full rounded-lg border border-white/[0.07] bg-white/[0.025] p-3 text-left hover:bg-white/[0.05]">
                          <div className="flex items-center justify-between gap-2"><span className="text-xs font-medium text-slate-200">{outbound ? "imports →" : "← imported by"} {other?.label ?? otherId}</span><span className="text-[9px] text-indigo-300">STATIC</span></div>
                          <div className="mt-1 truncate text-[10px] text-slate-600">{other?.path ?? "UNKNOWN"}</div>
                        </button>
                      )
                    })}
                  </div>
                </div>
              </div>
            ) : selectedEdge ? (
              <div className="mt-4 space-y-4 text-sm">
                <div className="rounded-xl border border-indigo-400/15 bg-indigo-400/[0.06] p-3">
                  <div className="flex items-center gap-2 text-indigo-200"><GitBranch className="h-4 w-4" /><span className="font-semibold">{selectedEdge.relation}</span></div>
                  <div className="mt-2 text-xs leading-5 text-indigo-100/70">Edge này tồn tại vì scanner quan sát thấy import tĩnh trong source. Nó không chứng minh call/runtime path.</div>
                </div>
                <div className="space-y-2 text-xs">
                  <div className="rounded-lg bg-white/[0.035] p-3"><div className="text-slate-500">Source evidence</div><div className="mt-1 break-all font-mono text-slate-200">{selectedEdge.sourcePath}:{selectedEdge.sourceLine}</div></div>
                  <div className="rounded-lg bg-white/[0.035] p-3"><div className="text-slate-500">Evidence level</div><div className="mt-1 text-indigo-200">{selectedEdge.evidenceLevel}</div></div>
                  <div className="rounded-lg bg-white/[0.035] p-3"><div className="text-slate-500">From</div><div className="mt-1 break-all text-slate-200">{snapshot.nodes.find((node) => node.id === selectedEdge.from)?.path ?? selectedEdge.from}</div></div>
                  <div className="rounded-lg bg-white/[0.035] p-3"><div className="text-slate-500">To</div><div className="mt-1 break-all text-slate-200">{snapshot.nodes.find((node) => node.id === selectedEdge.to)?.path ?? selectedEdge.to}</div></div>
                </div>
              </div>
            ) : (
              <div className="mt-4 space-y-4">
                <div className="rounded-xl border border-white/[0.07] bg-black/15 p-4">
                  <FileCode2 className="h-5 w-5 text-cyan-300" />
                  <p className="mt-3 text-sm leading-6 text-slate-400">Chọn một node để xem source path và các relationship. Chọn edge để xem chính xác dòng source tạo bằng chứng.</p>
                </div>
                <div>
                  <div className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Subsystem legend</div>
                  <div className="mt-3 grid grid-cols-2 gap-2">
                    {subsystems.map((name) => { const tone = toneFor(name); return <button key={name} onClick={() => setSubsystem(name)} className="flex items-center gap-2 rounded-lg border border-white/[0.06] bg-white/[0.02] p-2 text-left text-[11px] text-slate-400 hover:bg-white/[0.05]"><span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: tone.fill, boxShadow: `0 0 0 4px ${tone.halo}` }} />{name}</button> })}
                  </div>
                </div>
                <div className="rounded-xl border border-amber-400/15 bg-amber-400/[0.05] p-3 text-xs leading-5 text-amber-100/70">
                  <div className="font-semibold text-amber-200">V1 boundary</div>
                  <ul className="mt-2 list-disc space-y-1 pl-4">{(snapshot.limitations ?? ["Runtime evidence not connected."]).map((item) => <li key={item}>{item}</li>)}</ul>
                </div>
                {snapshot.truncated && <div className="rounded-lg border border-rose-400/15 bg-rose-400/[0.05] p-3 text-xs text-rose-200">Graph đã chạm safety limit; kết quả là partial source projection.</div>}
              </div>
            )}
          </aside>
        </section>
      </div>
    </main>
  )
}
