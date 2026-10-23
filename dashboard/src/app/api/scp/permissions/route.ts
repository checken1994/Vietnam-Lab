/**
 * API /api/scp/permissions — danh sách yêu cầu quyền ĐANG CHỜ CON NGƯỜI DUYỆT.
 *
 * GET → GET http://backend:8000/v105/autofix/permissions
 *   Backend trả { pending: [...], count } cho các lỗi logic rủi ro cao mà
 *   permission_gate đã dừng lại chờ quyết định của người.
 *
 * [VÌ SAO CẦN ROUTE NÀY] Backend ĐÃ có sẵn cơ chế duyệt
 *   (GET /v105/autofix/permissions, POST .../{id}/approve, .../{id}/deny) nhưng
 *   dashboard KHÔNG có chỗ nào để người dùng nhìn thấy và bấm duyệt. Ô
 *   "cần human approval" trên dashboard chỉ là số liệu TĨNH từ dataset audit
 *   round-7 (dashboard/src/lib/audit-data/round7.ts) — không phải hàng đợi
 *   đang chờ. Route này + panel tương ứng làm cho vòng đời human-in-the-loop
 *   nằm trọn trong dashboard (DNA #11, #22).
 *
 * Bảo mật:
 *  - Chỉ export GET (không có đường ghi ở route này).
 *  - middleware.ts gate toàn bộ /api/scp/* fail-closed (secret đảo ngược hoặc
 *    dev-mode loopback; không có gì thì 503).
 *  - Credentials: caller-sent Authorization được forward nguyên văn; nếu thiếu
 *    thì chỉ chèn token của operator trong "local posture" (không có proxy
 *    secret) — cùng hợp đồng với lib/scp-service-auth.ts. Secret không bao giờ
 *    được log hay trả về cho trình duyệt.
 *  - SSRF gate: chỉ chấp nhận target trong allowlist probe của dashboard.
 */
import { NextResponse } from "next/server"
import { isAllowedProbeTarget } from "../../../../lib/probe-allowlist"
import { injectServiceAuth } from "../../../../lib/scp-service-auth"

export const dynamic = "force-dynamic"
export const revalidate = 0

const BACKEND_URL = process.env.SCP_INTERNAL_URL ?? "http://127.0.0.1:8000"
const BACKEND_HINT = "SCP backend không phản hồi — kiểm tra container backend (:8000)."

export async function GET(request: Request) {
  const targetUrl = `${BACKEND_URL}/v105/autofix/permissions`

  const extraHosts = (process.env.SCP_HEALTH_ALLOWED_HOSTS ?? "")
    .split(",")
    .map((h) => h.trim().toLowerCase())
    .filter(Boolean)
  const guard = isAllowedProbeTarget(targetUrl, extraHosts)
  if (!guard.allowed) {
    return NextResponse.json(
      { error: `SSRF blocked: ${guard.reason}`, pending: [], count: 0 },
      { status: 403 },
    )
  }

  try {
    const res = await fetch(targetUrl, {
      method: "GET",
      headers: {
        Accept: "application/json",
        ...injectServiceAuth(request),
      },
      cache: "no-store",
      signal: AbortSignal.timeout(8000),
    })

    if (!res.ok) {
      return NextResponse.json(
        {
          error: "Không đọc được danh sách chờ duyệt từ backend",
          httpStatus: res.status,
          hint: BACKEND_HINT,
          pending: [],
          count: 0,
        },
        { status: 502 },
      )
    }

    const data = (await res.json().catch(() => null)) as {
      pending?: unknown
      count?: unknown
    } | null

    return NextResponse.json(
      {
        pending: Array.isArray(data?.pending) ? data.pending : [],
        count:
          typeof data?.count === "number"
            ? data.count
            : Array.isArray(data?.pending)
              ? data.pending.length
              : 0,
        checkedAt: new Date().toISOString(),
      },
      { status: 200 },
    )
  } catch (e) {
    const err = e instanceof Error ? e.message : String(e)
    return NextResponse.json(
      {
        error: err.slice(0, 200),
        hint: BACKEND_HINT,
        pending: [],
        count: 0,
      },
      { status: 503 },
    )
  }
}
