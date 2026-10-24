"use client"

/**
 * HumanApprovalPanel — HÀNG ĐỢI DUYỆT của con người cho SCP.
 *
 * Vấn đề panel này giải quyết: backend có đầy đủ cơ chế duyệt
 * (GET /v105/autofix/permissions, POST .../{id}/approve|deny) nhưng dashboard
 * không có chỗ nào để người dùng nhìn thấy hàng đợi và bấm quyết định. Ô
 * "cần human approval" sẵn có là số liệu TĨNH từ dataset audit round-7 —
 * không phải hàng đợi đang chờ. Panel này đọc hàng đợi THẬT từ backend và
 * thực hiện quyết định qua proxy chỉ-đọc/có-ghi của dashboard.
 *
 * Nguyên tắc (DNA #11, #22):
 *  - Không có yêu cầu ⇒ hiện rõ "không có gì đang chờ" (không giấu).
 *  - Không đọc được backend ⇒ hiện lỗi + gợi ý, KHÔNG hiện danh sách rỗng
 *    như thể là "không có gì chờ" (fail-closed, phân biệt rõ).
 *  - Quyết định bắt buộc kèm `note` tự viết — giống điều kiện
 *    UnderstandingChecker của backend.
 *  - Không tự động duyệt/từ chối. Không có timer nào bấm nút thay người.
 */
import { useCallback, useEffect, useRef, useState } from "react"
import { AlertTriangle, CheckCircle2, Gavel, RefreshCw, XCircle } from "lucide-react"

interface PermissionRequest {
  request_id: string
  file?: string
  line?: number
  bug_type?: string
  description?: string
  suggested_fix?: string
  timestamp?: number
}

interface PermissionsResponse {
  pending?: PermissionRequest[]
  count?: number
  error?: string
  hint?: string
  checkedAt?: string
}

const REFRESH_MS = 10_000
const MIN_NOTE_LENGTH = 12

function formatTimestamp(value?: number): string {
  if (!value) return "—"
  const ms = value > 1e12 ? value : value * 1000
  const d = new Date(ms)
  if (Number.isNaN(d.getTime())) return "—"
  return d.toLocaleTimeString("vi-VN")
}

export function HumanApprovalPanel() {
  const [pending, setPending] = useState<PermissionRequest[]>([])
  const [count, setCount] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [hint, setHint] = useState<string | null>(null)
  const [checkedAt, setCheckedAt] = useState<string | null>(null)
  const [notes, setNotes] = useState<Record<string, string>>({})
  const [busyId, setBusyId] = useState<string | null>(null)
  const [decision, setDecision] = useState<{ id: string; ok: boolean; message: string } | null>(null)
  const [loading, setLoading] = useState(true)
  const mounted = useRef(true)

  const refresh = useCallback(async () => {
    try {
      const res = await fetch("/api/scp/permissions", { cache: "no-store" })
      const data = (await res.json().catch(() => null)) as PermissionsResponse | null
      if (!mounted.current) return
      if (!res.ok || !data) {
        setError(`Không đọc được hàng đợi duyệt (HTTP ${res.status}).`)
        setHint(data?.hint ?? null)
        setPending([])
        setCount(null)
        return
      }
      if (data.error) {
        setError(data.error)
        setHint(data.hint ?? null)
        setPending([])
        setCount(null)
        return
      }
      setError(null)
      setHint(null)
      setPending(Array.isArray(data.pending) ? data.pending : [])
      setCount(typeof data.count === "number" ? data.count : 0)
      setCheckedAt(data.checkedAt ?? null)
    } catch (e) {
      if (!mounted.current) return
      setError(e instanceof Error ? e.message : String(e))
      setPending([])
      setCount(null)
    } finally {
      if (mounted.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    mounted.current = true
    void refresh()
    const timer = window.setInterval(() => void refresh(), REFRESH_MS)
    return () => {
      mounted.current = false
      window.clearInterval(timer)
    }
  }, [refresh])

  async function decide(request: PermissionRequest, action: "approve" | "deny") {
    const note = (notes[request.request_id] ?? "").trim()
    if (note.length < MIN_NOTE_LENGTH) {
      setDecision({
        id: request.request_id,
        ok: false,
        message: `Phải nhập ghi chú của bạn (tối thiểu ${MIN_NOTE_LENGTH} ký tự) giải thích bản vá làm gì.`,
      })
      return
    }
    setBusyId(request.request_id)
    setDecision(null)
    try {
      const res = await fetch(`/api/scp/permissions/${encodeURIComponent(request.request_id)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, note }),
      })
      const data = (await res.json().catch(() => null)) as
        | { error?: string; decided?: boolean; hint?: string }
        | null
      if (!res.ok) {
        setDecision({
          id: request.request_id,
          ok: false,
          message: data?.error ?? `Quyết định thất bại (HTTP ${res.status}).`,
        })
        return
      }
      setDecision({
        id: request.request_id,
        ok: true,
        message:
          action === "approve"
            ? "Đã duyệt — SCP được phép áp dụng bản vá này."
            : "Đã từ chối — SCP không được áp dụng bản vá này.",
      })
      setNotes((prev) => {
        const next = { ...prev }
        delete next[request.request_id]
        return next
      })
      await refresh()
    } catch (e) {
      setDecision({
        id: request.request_id,
        ok: false,
        message: e instanceof Error ? e.message : String(e),
      })
    } finally {
      setBusyId(null)
    }
  }

  const hasPending = pending.length > 0

  return (
    <section
      className="mt-8 rounded-3xl border border-fuchsia-300/15 bg-fuchsia-300/[0.05] p-5 sm:p-7"
      aria-label="Hàng đợi duyệt của con người"
    >
      <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div className="flex items-start gap-4">
          <span className="mt-1 grid h-12 w-12 shrink-0 place-items-center rounded-2xl bg-fuchsia-300/15 text-fuchsia-200">
            <Gavel className="h-6 w-6" />
          </span>
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.2em] text-fuchsia-200/80">
              Human-in-the-loop · DNA #11
            </div>
            <h2 className="mt-1 text-2xl font-semibold text-white">Yêu cầu chờ bạn duyệt</h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
              Khi SCP gặp lỗi logic rủi ro cao, permission gate dừng lại và chờ quyết
              định của con người. Danh sách dưới đây đọc trực tiếp từ backend
              (<code className="rounded bg-black/25 px-1">/v105/autofix/permissions</code>) —
              không phải số liệu tĩnh. Duyệt hay từ chối đều cần ghi chú của bạn để
              chứng minh bạn hiểu bản vá đó làm gì.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span
            className={`rounded-full px-3 py-1.5 text-sm font-semibold ${
              hasPending
                ? "bg-fuchsia-300/15 text-fuchsia-100"
                : "bg-emerald-300/15 text-emerald-200"
            }`}
            data-testid="pending-permission-count"
          >
            {count === null ? "—" : count} đang chờ
          </span>
          <button
            type="button"
            onClick={() => void refresh()}
            className="inline-flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.06] px-3.5 py-2.5 text-sm font-medium text-slate-200 transition hover:bg-white/10"
            aria-label="Làm mới hàng đợi duyệt"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            Làm mới
          </button>
        </div>
      </div>

      {error && (
        <div className="mt-5 flex items-start gap-3 rounded-2xl border border-rose-400/25 bg-rose-400/10 p-4 text-sm text-rose-100">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <div>
            <div className="font-medium">Không đọc được hàng đợi duyệt</div>
            <div className="mt-1 text-rose-100/70">
              {error}
              {hint ? ` · ${hint}` : ""} — đây KHÔNG phải là “không có gì đang chờ”.
            </div>
          </div>
        </div>
      )}

      {!error && !hasPending && (
        <div className="mt-5 flex items-center gap-3 rounded-2xl border border-emerald-300/20 bg-emerald-300/[0.07] p-4 text-sm text-emerald-100">
          <CheckCircle2 className="h-4 w-4 shrink-0" />
          <span>
            Không có yêu cầu nào đang chờ duyệt — SCP đang tự xử lý trong phạm vi
            được phép.
          </span>
        </div>
      )}

      {hasPending && (
        <div className="mt-5 space-y-4">
          {pending.map((item) => (
            <article
              key={item.request_id}
              className="rounded-2xl border border-white/[0.08] bg-black/20 p-4"
            >
              <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0">
                  <div className="font-mono text-sm text-fuchsia-100">
                    {item.file ?? "—"}
                    {item.line ? `:${item.line}` : ""}
                  </div>
                  <div className="mt-1 text-xs text-slate-500">
                    {item.bug_type ?? "—"} · {formatTimestamp(item.timestamp)} ·{" "}
                    <span className="font-mono">{item.request_id}</span>
                  </div>
                </div>
              </div>

              {item.description && (
                <p className="mt-3 text-sm leading-6 text-slate-300">{item.description}</p>
              )}

              {item.suggested_fix && (
                <details className="mt-3 rounded-xl border border-white/[0.08] bg-black/30 p-3">
                  <summary className="cursor-pointer text-xs font-medium text-slate-400">
                    Xem bản vá đề xuất
                  </summary>
                  <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-5 text-slate-300">
                    {item.suggested_fix}
                  </pre>
                </details>
              )}

              <label className="mt-4 block text-xs font-medium text-slate-300">
                Ghi chú của bạn (bắt buộc — mô tả bản vá này làm gì)
              </label>
              <textarea
                value={notes[item.request_id] ?? ""}
                onChange={(event) =>
                  setNotes((prev) => ({ ...prev, [item.request_id]: event.target.value }))
                }
                rows={3}
                placeholder="VD: thêm guard cho biến chưa khởi tạo trước khi dùng ở dòng 396; không thay đổi hợp đồng response."
                className="mt-1 w-full resize-y rounded-xl border border-white/10 bg-black/30 px-3 py-2 text-sm text-white outline-none placeholder:text-slate-600 focus:border-fuchsia-300/40"
              />

              {decision && decision.id === item.request_id && (
                <div
                  className={`mt-3 rounded-xl border px-3 py-2 text-xs ${
                    decision.ok
                      ? "border-emerald-300/25 bg-emerald-300/10 text-emerald-100"
                      : "border-rose-300/25 bg-rose-300/10 text-rose-100"
                  }`}
                >
                  {decision.message}
                </div>
              )}

              <div className="mt-4 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => void decide(item, "approve")}
                  disabled={busyId === item.request_id}
                  className="inline-flex items-center gap-2 rounded-xl bg-emerald-300 px-4 py-2.5 text-sm font-semibold text-slate-950 transition hover:bg-emerald-200 disabled:cursor-wait disabled:opacity-60"
                >
                  <CheckCircle2 className="h-4 w-4" />
                  Duyệt &amp; cho phép áp dụng
                </button>
                <button
                  type="button"
                  onClick={() => void decide(item, "deny")}
                  disabled={busyId === item.request_id}
                  className="inline-flex items-center gap-2 rounded-xl border border-rose-300/30 bg-rose-300/10 px-4 py-2.5 text-sm font-semibold text-rose-100 transition hover:bg-rose-300/20 disabled:cursor-wait disabled:opacity-60"
                >
                  <XCircle className="h-4 w-4" />
                  Từ chối
                </button>
              </div>
            </article>
          ))}
        </div>
      )}

      <div className="mt-4 text-[11px] text-slate-500">
        {checkedAt ? `Kiểm tra lúc ${new Date(checkedAt).toLocaleTimeString("vi-VN")}` : "—"} ·
        làm mới mỗi {REFRESH_MS / 1000}s · quyết định được ghi vào audit trail của
        backend (decide d_by = api_admin).
      </div>
    </section>
  )
}

export default HumanApprovalPanel
