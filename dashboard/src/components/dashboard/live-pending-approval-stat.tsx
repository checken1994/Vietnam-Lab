"use client"

/**
 * LivePendingApprovalStat — ô chỉ số "đang chờ bạn duyệt" đọc SỐ LIVE.
 *
 * [FIX · 2026-10-22] Thẻ "Đã fix R7 … N cần human approval" trong Stats là số
 * TĨNH đếm từ dataset audit round-7 (lib/audit-data/round7.ts) — đúng về mặt
 * lịch sử nhưng dễ bị hiểu là hàng đợi hiện tại. Ô này hiển thị hàng đợi THẬT
 * đang chờ (GET /api/scp/permissions → count) và ghi rõ nguồn.
 *
 * Fail-closed: không đọc được backend ⇒ hiện "—" + "không đọc được", KHÔNG hiện
 * "0" (0 và "không biết" là hai khẳng định khác nhau — DNA #22).
 */
import { useEffect, useState } from "react"
import { Gavel } from "lucide-react"
import { Card } from "@/components/ui/card"

const REFRESH_MS = 15_000

interface PermissionsResponse {
  count?: number
  error?: string
}

export function LivePendingApprovalStat() {
  const [count, setCount] = useState<number | null>(null)
  const [unreadable, setUnreadable] = useState(false)

  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        const res = await fetch("/api/scp/permissions", { cache: "no-store" })
        const data = (await res.json().catch(() => null)) as PermissionsResponse | null
        if (!alive) return
        if (!res.ok || !data || data.error) {
          setUnreadable(true)
          setCount(null)
          return
        }
        setUnreadable(false)
        setCount(typeof data.count === "number" ? data.count : 0)
      } catch {
        if (!alive) return
        setUnreadable(true)
        setCount(null)
      }
    }
    void load()
    const timer = window.setInterval(() => void load(), REFRESH_MS)
    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [])

  const value = unreadable ? "—" : count === null ? "—" : count
  const tone = unreadable
    ? "border-amber-500/40 bg-amber-500/5"
    : count && count > 0
      ? "border-fuchsia-500/40 bg-fuchsia-500/5"
      : "border-emerald-500/40 bg-emerald-500/5"
  const valueTone = unreadable
    ? "text-amber-600 dark:text-amber-400"
    : count && count > 0
      ? "text-fuchsia-600 dark:text-fuchsia-400"
      : "text-emerald-600 dark:text-emerald-400"

  return (
    <Card className={`p-5 ${tone}`} data-testid="live-pending-stat">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <p className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
            Đang chờ bạn duyệt
          </p>
          <p className={`text-2xl font-semibold ${valueTone}`}>{value}</p>
          <p className="text-xs text-muted-foreground">
            {unreadable
              ? "Không đọc được hàng đợi — không phải là 0"
              : "Số yêu cầu quyền đang chờ (live, /v105/autofix/permissions)"}
          </p>
        </div>
        <Gavel className="h-4 w-4 shrink-0 text-fuchsia-500/70" aria-hidden="true" />
      </div>
    </Card>
  )
}

export default LivePendingApprovalStat
