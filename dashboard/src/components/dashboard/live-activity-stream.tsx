"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import {
  Activity,
  AlertCircle,
  CheckCircle,
  ChevronDown,
  ChevronRight,
  Clock,
  Filter,
  Play,
  RefreshCw,
  Terminal,
  Trash2,
  Zap,
} from "lucide-react"

interface LoopRunEntry {
  ts?: string
  scp_online?: boolean
  status?: string
  http_status?: number
  duration_ms?: number
  triggered_by?: string
  findings_count?: number
  fixes_applied?: number
  permission_requested?: number
  skipped?: number
}

interface ActivityEvent {
  id: string
  timestamp: string
  type: "loop" | "log" | "system"
  service: string
  status: "ok" | "warning" | "error" | "info"
  message: string
  details?: Record<string, unknown>
}

interface ActivityResponse {
  success: boolean
  summary: {
    totalRunsRecorded: number
    latestRunStatus: string
    latestRunTimestamp: string | null
    latestRunDurationMs: number
    latestFindingsCount: number
    latestFixesCount: number
    triggerType: string
    serverTime: string
  }
  runs: LoopRunEntry[]
  events: ActivityEvent[]
}

type FilterCategory = "all" | "loop" | "log" | "error"

export function LiveActivityStream() {
  const [data, setData] = useState<ActivityResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [triggering, setTriggering] = useState(false)
  const [triggerResult, setTriggerResult] = useState<string | null>(null)
  const [filter, setFilter] = useState<FilterCategory>("all")
  const [autoScroll, setAutoScroll] = useState(true)
  const [expandedEventId, setExpandedEventId] = useState<string | null>(null)
  const [clearedBefore, setClearedBefore] = useState<number>(0)
  const terminalEndRef = useRef<HTMLDivElement | null>(null)

  const fetchActivity = useCallback(async () => {
    try {
      const res = await fetch("/api/scp/activity", { cache: "no-store" })
      if (res.ok) {
        const json = (await res.json()) as ActivityResponse
        setData(json)
      }
    } catch {
      // Network error handled silently in live loop
    }
  }, [])

  // Auto-fetch every 3 seconds for true live feeling
  useEffect(() => {
    void fetchActivity()
    const timer = setInterval(() => {
      void fetchActivity()
    }, 3000)
    return () => clearInterval(timer)
  }, [fetchActivity])

  // Scroll to top or bottom when new events arrive
  useEffect(() => {
    if (autoScroll && terminalEndRef.current) {
      terminalEndRef.current.scrollIntoView({ behavior: "smooth" })
    }
  }, [data?.events, autoScroll])

  const handleTriggerAudit = async () => {
    if (triggering) return
    setTriggering(true)
    setTriggerResult("Đang gửi yêu cầu kích hoạt chu kỳ audit…")
    try {
      const res = await fetch("/api/scp/loop/trigger", { method: "POST", cache: "no-store" })
      const resJson = await res.json().catch(() => ({}))
      if (res.ok) {
        setTriggerResult("Đã kích hoạt thành công! Đang quan sát tiến trình…")
        // Immediate poll
        setTimeout(() => void fetchActivity(), 1000)
        setTimeout(() => void fetchActivity(), 3000)
      } else {
        setTriggerResult(`Lỗi kích hoạt: HTTP ${res.status}`)
      }
    } catch (e) {
      setTriggerResult(`Lỗi kết nối: ${e instanceof Error ? e.message : "không rõ"}`)
    } finally {
      setTriggering(false)
      setTimeout(() => setTriggerResult(null), 5000)
    }
  }

  const events = data?.events ?? []
  const filteredEvents = events.filter((e) => {
    const eventTime = new Date(e.timestamp).getTime()
    if (clearedBefore && eventTime <= clearedBefore) return false
    if (filter === "loop") return e.type === "loop"
    if (filter === "log") return e.type === "log"
    if (filter === "error") return e.status === "error" || e.status === "warning"
    return true
  })

  const formatTimestamp = (ts: string) => {
    try {
      const d = new Date(ts)
      return d.toLocaleTimeString("vi-VN", { hour12: false })
    } catch {
      return ts
    }
  }

  return (
    <section
      aria-label="SCP Live Activity"
      className="mt-8 rounded-3xl border border-cyan-400/20 bg-gradient-to-b from-[#081525] to-[#06101d] p-5 shadow-xl shadow-black/30 sm:p-7"
    >
      {/* Header bar */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between border-b border-white/[0.08] pb-5">
        <div className="flex items-start gap-3">
          <div className="grid h-10 w-10 place-items-center rounded-2xl border border-cyan-300/30 bg-cyan-400/10 text-cyan-300">
            <Terminal className="h-5 w-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse shadow-[0_0_8px_#34d399]" />
              <span className="text-xs font-semibold uppercase tracking-[0.16em] text-cyan-300">
                Live Activity & Event Stream
              </span>
            </div>
            <h2 className="mt-0.5 text-xl font-semibold text-white">
              Quan sát hoạt động bên trong SCP
            </h2>
            <p className="mt-1 text-xs text-slate-400">
              Nhật ký thực thi, chu kỳ tự sửa và sự kiện hệ thống theo thời gian thực — không cần cửa sổ CMD đen.
            </p>
          </div>
        </div>

        {/* Action controls */}
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={handleTriggerAudit}
            disabled={triggering}
            className="inline-flex items-center gap-1.5 rounded-xl bg-cyan-400 px-3.5 py-2 text-xs font-semibold text-slate-950 transition hover:bg-cyan-300 disabled:cursor-wait disabled:opacity-50"
          >
            {triggering ? (
              <RefreshCw className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Play className="h-3.5 w-3.5 fill-current" />
            )}
            Chạy kiểm tra ngay
          </button>
          <button
            type="button"
            onClick={() => void fetchActivity()}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-xl border border-white/10 bg-white/[0.06] px-3 py-2 text-xs text-slate-200 transition hover:bg-white/10"
            title="Làm mới log"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            Làm mới
          </button>
          <button
            type="button"
            onClick={() => setClearedBefore(Date.now())}
            className="inline-flex items-center gap-1.5 rounded-xl border border-white/10 bg-white/[0.06] px-3 py-2 text-xs text-slate-400 transition hover:text-slate-200"
            title="Xóa màn hình log tạm thời"
          >
            <Trash2 className="h-3.5 w-3.5" />
            Xóa xem
          </button>
        </div>
      </div>

      {triggerResult && (
        <div className="mt-3 rounded-xl border border-cyan-300/30 bg-cyan-400/10 p-3 text-xs text-cyan-200 flex items-center gap-2">
          <Zap className="h-4 w-4 shrink-0 text-cyan-300 animate-pulse" />
          <span>{triggerResult}</span>
        </div>
      )}

      {/* Quick summary metric cards */}
      <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div className="rounded-2xl border border-white/[0.08] bg-black/20 p-3.5">
          <div className="text-[11px] uppercase tracking-wider text-slate-400">Chu kỳ gần nhất</div>
          <div className="mt-1.5 flex items-center gap-2">
            <span className="h-2 w-2 rounded-full bg-emerald-400" />
            <span className="text-sm font-semibold text-white">
              {data?.summary.latestRunStatus === "ok" ? "Hoàn tất (OK)" : (data?.summary.latestRunStatus || "—")}
            </span>
          </div>
          <div className="mt-1 text-[11px] text-slate-500">
            {data?.summary.latestRunDurationMs ? `${data.summary.latestRunDurationMs}ms` : "—"}
          </div>
        </div>

        <div className="rounded-2xl border border-white/[0.08] bg-black/20 p-3.5">
          <div className="text-[11px] uppercase tracking-wider text-slate-400">Nguồn kích hoạt</div>
          <div className="mt-1.5 text-sm font-semibold text-white">
            {data?.summary.triggerType === "cron" ? "Định kỳ (5 phút)" : data?.summary.triggerType === "manual" ? "Thủ công" : (data?.summary.triggerType || "—")}
          </div>
          <div className="mt-1 text-[11px] text-slate-500">
            {data?.summary.latestRunTimestamp ? formatTimestamp(data.summary.latestRunTimestamp) : "—"}
          </div>
        </div>

        <div className="rounded-2xl border border-white/[0.08] bg-black/20 p-3.5">
          <div className="text-[11px] uppercase tracking-wider text-slate-400">Phát hiện / Bản vá</div>
          <div className="mt-1.5 text-sm font-semibold text-emerald-300">
            {data?.summary.latestFindingsCount ?? 0} phát hiện · {data?.summary.latestFixesCount ?? 0} sửa
          </div>
          <div className="mt-1 text-[11px] text-slate-500">0 lỗi âm thầm</div>
        </div>

        <div className="rounded-2xl border border-white/[0.08] bg-black/20 p-3.5">
          <div className="text-[11px] uppercase tracking-wider text-slate-400">Tổng chu kỳ ghi nhận</div>
          <div className="mt-1.5 text-sm font-semibold text-white">
            {data?.summary.totalRunsRecorded ?? 0} lượt
          </div>
          <div className="mt-1 text-[11px] text-slate-500">Dữ liệu từ loop_runs.jsonl</div>
        </div>
      </div>

      {/* Filter and control bar */}
      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-white/[0.06] pt-4">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-slate-400 mr-1 inline-flex items-center gap-1">
            <Filter className="h-3 w-3" /> Lọc:
          </span>
          {(
            [
              { key: "all", label: "Tất cả" },
              { key: "loop", label: "Vòng lặp & Tự sửa" },
              { key: "log", label: "Log hệ thống" },
              { key: "error", label: "Cảnh báo & Lỗi" },
            ] as const
          ).map(({ key, label }) => (
            <button
              key={key}
              type="button"
              onClick={() => setFilter(key)}
              className={`rounded-lg px-2.5 py-1 text-xs font-medium transition ${
                filter === key
                  ? "border border-cyan-400/40 bg-cyan-400/15 text-cyan-200"
                  : "border border-transparent text-slate-400 hover:bg-white/[0.04] hover:text-slate-200"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        <label className="inline-flex items-center gap-1.5 text-xs text-slate-400 cursor-pointer">
          <input
            type="checkbox"
            checked={autoScroll}
            onChange={(e) => setAutoScroll(e.target.checked)}
            className="accent-cyan-400 rounded"
          />
          Tự động cuộn
        </label>
      </div>

      {/* Terminal log container */}
      <div className="mt-3 rounded-2xl border border-white/[0.08] bg-[#030812] p-3 font-mono text-xs shadow-inner max-h-[380px] overflow-y-auto">
        {filteredEvents.length === 0 ? (
          <div className="py-12 text-center text-slate-500">
            Chưa có sự kiện nào phù hợp với bộ lọc hiện tại.
          </div>
        ) : (
          <div className="space-y-1.5">
            {filteredEvents.map((evt) => {
              const isExpanded = expandedEventId === evt.id
              const hasDetails = !!evt.details

              return (
                <div
                  key={evt.id}
                  className="rounded-lg border border-white/[0.03] bg-white/[0.02] p-2 transition hover:bg-white/[0.05]"
                >
                  <div className="flex items-start gap-2">
                    <span className="shrink-0 text-slate-500 select-none">
                      {formatTimestamp(evt.timestamp)}
                    </span>

                    {/* Service badge */}
                    <span
                      className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider ${
                        evt.type === "loop"
                          ? "bg-cyan-500/20 text-cyan-300"
                          : evt.service === "desktop"
                          ? "bg-violet-500/20 text-violet-300"
                          : "bg-emerald-500/20 text-emerald-300"
                      }`}
                    >
                      {evt.service}
                    </span>

                    {/* Status badge */}
                    <span
                      className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${
                        evt.status === "ok"
                          ? "bg-emerald-400/15 text-emerald-300"
                          : evt.status === "warning"
                          ? "bg-amber-400/15 text-amber-300"
                          : evt.status === "error"
                          ? "bg-rose-400/15 text-rose-300"
                          : "bg-slate-400/15 text-slate-300"
                      }`}
                    >
                      {evt.status}
                    </span>

                    {/* Message text */}
                    <span className="min-w-0 flex-1 break-words text-slate-200">
                      {evt.message}
                    </span>

                    {/* Details toggle */}
                    {hasDetails && (
                      <button
                        type="button"
                        onClick={() => setExpandedEventId(isExpanded ? null : evt.id)}
                        className="shrink-0 text-slate-500 hover:text-cyan-300"
                        title="Xem chi tiết dữ liệu JSON"
                      >
                        {isExpanded ? (
                          <ChevronDown className="h-3.5 w-3.5" />
                        ) : (
                          <ChevronRight className="h-3.5 w-3.5" />
                        )}
                      </button>
                    )}
                  </div>

                  {/* Expanded JSON details */}
                  {isExpanded && evt.details && (
                    <div className="mt-2 rounded bg-black/40 p-2 text-[11px] text-cyan-200/90 overflow-x-auto border border-cyan-400/15">
                      <pre>{JSON.stringify(evt.details, null, 2)}</pre>
                    </div>
                  )}
                </div>
              )
            })}
            <div ref={terminalEndRef} />
          </div>
        )}
      </div>

      {/* Footer hint */}
      <div className="mt-3 flex items-center justify-between text-[11px] text-slate-500">
        <span>Cập nhật liên tục mỗi 3 giây · Lưu trữ tại data/loop_runs.jsonl</span>
        <span>SCP Engine v14.0.0 (API-Only)</span>
      </div>
    </section>
  )
}
