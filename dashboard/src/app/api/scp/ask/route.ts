import { NextResponse } from "next/server"
// [S6b security sweep] The env-derived base URL is now resolved AND validated
// inside dashboard/src/lib/scp-backend-url.ts (single PEP, no fetch sink
// there); this handler fetches only the validated base it returns.
import { resolveScpProxyBase } from "../../../../lib/scp-backend-url"
// [AUDIT-FIX low-10] Cap conversation_history (per-item + total byte) trước
// khi forward lên backend — trước đây chỉ slice(-8) theo số item, không có
// size cap → payload upstream phình to tùy ý.
import { capConversationHistory } from "../../../../lib/scp-history"
import { extractCallerAuth } from "../../../../lib/auth-helper"

export const dynamic = "force-dynamic"
export const revalidate = 0

export async function POST(request: Request) {
  try {
    // Require authentication: reject unauthenticated callers with 401 Unauthorized
    const auth = extractCallerAuth(request)
    if (!auth.authenticated || auth.errorResponse) {
      return (
        auth.errorResponse ||
        NextResponse.json(
          { error: "Unauthorized: Missing or invalid authentication credentials" },
          { status: 401 }
        )
      )
    }

    const authHeaderToSend = auth.authHeader

    const body = await request.json() as Record<string, unknown>
    const question = typeof body.question === "string" ? body.question.trim() : ""
    if (!question || question.length > 8000) {
      return NextResponse.json({ error: "Câu hỏi trống hoặc quá dài" }, { status: 400 })
    }
    const imageData = typeof body.image_data === "string" ? body.image_data : null
    if (imageData && imageData.length > 900_000) {
      return NextResponse.json({ error: "Ảnh quá lớn" }, { status: 413 })
    }
    // [AUDIT-FIX low-10] capConversationHistory thay cho slice(-8) thô:
    // max 8 item MỚI NHẤT, mỗi item ≤ 4KB serialized, tổng ≤ 32KB; flag
    // truncated được báo rõ trong payload gửi backend.
    const history = capConversationHistory(body.conversation_history)
    const payload = {
      question,
      domain: typeof body.domain === "string" ? body.domain.slice(0, 80) : "general",
      ai_answer: "",
      source: "desktop_chat",
      session_id: typeof body.session_id === "string" ? body.session_id.slice(0, 120) : undefined,
      conversation_history: history.items,
      conversation_history_truncated: history.truncated,
      image_data: imageData,
    }

    // [S6b security sweep] Resolve + allowlist-validate the backend base
    // BEFORE fetch (single PEP in scp-backend-url.ts). A blocked target throws
    // into the existing catch, so the response shape is unchanged and no
    // request leaves the process.
    const base = resolveScpProxyBase()
    const response = await fetch(`${base}/ask`, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json", Authorization: authHeaderToSend },
      body: JSON.stringify(payload),
      cache: "no-store",
      signal: AbortSignal.timeout(120_000),
    })
    const text = await response.text()
    let data: unknown = {}
    try { data = JSON.parse(text) } catch { data = { error: text.slice(0, 500) } }
    return NextResponse.json(data, { status: response.status })
  } catch (error) {
    const message = error instanceof Error ? error.message : "Không kết nối được SCP"
    return NextResponse.json({ error: message.slice(0, 300) }, { status: 502 })
  }
}
