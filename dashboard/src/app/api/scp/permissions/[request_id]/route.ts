/**
 * API /api/scp/permissions/[request_id] — QUYẾT ĐỊNH của con người cho một
 * yêu cầu quyền đang chờ.
 *
 * POST body: { action: "approve" | "deny", note: string }
 *   → POST http://backend:8000/v105/autofix/permissions/{request_id}/approve?note=...
 *   → POST http://backend:8000/v105/autofix/permissions/{request_id}/deny?note=...
 *
 * [DNA #11 · human-in-the-loop có hiểu biết] `note` là BẮT BUỘC và phải là
 * do người viết, giải thích bản vá làm gì. Backend chạy UnderstandingChecker
 * và trả 400 nếu note rỗng/hời hợt/copy-paste từ patch; route này chặn sớm
 * ở biên để không gửi yêu cầu vô nghĩa, và UI yêu cầu người dùng nhập tay.
 *
 * [Bảo mật]
 *  - Chỉ export POST → mọi phương thức khác bị Next từ chối 405.
 *  - middleware.ts gate toàn bộ /api/scp/* fail-closed.
 *  - Credentials: injectServiceAuth — caller-sent Authorization được forward
 *    nguyên văn, chỉ chèn token của operator ở local posture. Secret không
 *    bao giờ log/trả về.
 *  - SSRF gate + request_id được encode trước khi ghép URL.
 *  - `decided_by` phía backend ghi "api_admin" — vì vậy route này phải nằm sau
 *    đúng ranh giới quyền của dashboard (secret/dev-mode), không được mở
 *    public.
 */
import { NextResponse } from "next/server"
import { isAllowedProbeTarget } from "../../../../../lib/probe-allowlist"
import { injectServiceAuth } from "../../../../../lib/scp-service-auth"

export const dynamic = "force-dynamic"
export const revalidate = 0

const BACKEND_URL = process.env.SCP_INTERNAL_URL ?? "http://127.0.0.1:8000"
const BACKEND_HINT = "SCP backend không phản hồi — kiểm tra container backend (:8000)."
const MIN_NOTE_LENGTH = 12

interface RouteContext {
  params: Promise<{ request_id: string }>
}

export async function POST(request: Request, context: RouteContext) {
  const { request_id } = await context.params
  const rawId = String(request_id ?? "").trim()
  if (!rawId || rawId.length > 200) {
    return NextResponse.json({ error: "request_id không hợp lệ" }, { status: 400 })
  }

  let body: { action?: unknown; note?: unknown } = {}
  try {
    body = (await request.json()) as { action?: unknown; note?: unknown }
  } catch {
    body = {}
  }

  const action = body.action === "deny" ? "deny" : "approve"
  const note = typeof body.note === "string" ? body.note.trim() : ""

  if (note.length < MIN_NOTE_LENGTH) {
    return NextResponse.json(
      {
        error:
          "Phải giải thích bằng lời của bạn bản vá này làm gì (tối thiểu " +
          `${MIN_NOTE_LENGTH} ký tự). Đây là điều kiện human-in-the-loop có hiểu biết.`,
        field: "note",
      },
      { status: 400 },
    )
  }

  const targetUrl =
    `${BACKEND_URL}/v105/autofix/permissions/${encodeURIComponent(rawId)}/${action}` +
    `?note=${encodeURIComponent(note)}`

  const extraHosts = (process.env.SCP_HEALTH_ALLOWED_HOSTS ?? "")
    .split(",")
    .map((h) => h.trim().toLowerCase())
    .filter(Boolean)
  const guard = isAllowedProbeTarget(targetUrl, extraHosts)
  if (!guard.allowed) {
    return NextResponse.json(
      { error: `SSRF blocked: ${guard.reason}` },
      { status: 403 },
    )
  }

  try {
    const res = await fetch(targetUrl, {
      method: "POST",
      headers: {
        Accept: "application/json",
        ...injectServiceAuth(request),
      },
      cache: "no-store",
      signal: AbortSignal.timeout(15_000),
    })

    const payload = (await res.json().catch(() => null)) as Record<string, unknown> | null

    if (!res.ok) {
      // Trả nguyên lý do của backend (404 = không có trong hàng đợi,
      // 400 = note chưa chứng minh hiểu biết) — không che giấu, không bịa.
      return NextResponse.json(
        {
          error:
            (payload && typeof payload.detail === "string" && payload.detail) ||
            `Backend từ chối yêu cầu (HTTP ${res.status}).`,
          httpStatus: res.status,
          decided: false,
        },
        { status: res.status === 404 ? 404 : 502 },
      )
    }

    return NextResponse.json(
      {
        decided: true,
        action,
        request_id: rawId,
        backend: payload ?? {},
        decidedAt: new Date().toISOString(),
      },
      { status: 200 },
    )
  } catch (e) {
    const err = e instanceof Error ? e.message : String(e)
    return NextResponse.json(
      { error: err.slice(0, 200), hint: BACKEND_HINT, decided: false },
      { status: 503 },
    )
  }
}
