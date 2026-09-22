import { readFile } from "node:fs/promises"
import { NextResponse } from "next/server"
// [S6b security sweep] The env-derived base URL is now resolved AND validated
// inside dashboard/src/lib/scp-backend-url.ts (single PEP, no fetch sink
// there); this handler fetches only the validated base it returns.
import { resolveScpProxyBase } from "../../../../lib/scp-backend-url"

export const dynamic = "force-dynamic"
export const revalidate = 0

import { createHmac } from "node:crypto"

const TOKEN_FILE = process.env.SCP_AUTH_TOKEN_SECRET_FILE?.trim()

function createJwt(secret: string): string {
  const h = Buffer.from(JSON.stringify({ alg: "HS256", typ: "JWT" })).toString("base64url")
  const p = Buffer.from(JSON.stringify({ sub: "admin", exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url")
  const sig = createHmac("sha256", secret).update(`${h}.${p}`).digest("base64url")
  return `${h}.${p}.${sig}`
}

async function readAdminToken(): Promise<string> {
  const jwtSecret = process.env.SCP_JWT_SECRET?.trim()
  if (jwtSecret) {
    return createJwt(jwtSecret)
  }
  const direct = process.env.SCP_AUTH_TOKEN_SECRET?.trim()
  if (direct) return direct
  if (!TOKEN_FILE) return ""
  try {
    return (await readFile(TOKEN_FILE, "utf8")).trim()
  } catch {
    return ""
  }
}

export async function POST(request: Request) {
  try {
    const body = await request.json() as Record<string, unknown>
    const question = typeof body.question === "string" ? body.question.trim() : ""
    if (!question || question.length > 8000) {
      return NextResponse.json({ error: "Câu hỏi trống hoặc quá dài" }, { status: 400 })
    }
    const imageData = typeof body.image_data === "string" ? body.image_data : null
    if (imageData && imageData.length > 900_000) {
      return NextResponse.json({ error: "Ảnh quá lớn" }, { status: 413 })
    }
    const history = Array.isArray(body.conversation_history)
      ? body.conversation_history.slice(-8).filter((item) => item && typeof item === "object")
      : []
    const payload = {
      question,
      domain: typeof body.domain === "string" ? body.domain.slice(0, 80) : "general",
      ai_answer: "",
      source: "desktop_chat",
      session_id: typeof body.session_id === "string" ? body.session_id.slice(0, 120) : undefined,
      conversation_history: history,
      image_data: imageData,
    }
    const token = await readAdminToken()
    if (!token) return NextResponse.json({ error: "SCP auth token chưa được cấu hình" }, { status: 503 })
    // [S6b security sweep] Resolve + allowlist-validate the backend base
    // BEFORE fetch (single PEP in scp-backend-url.ts). A blocked target throws
    // into the existing catch, so the response shape is unchanged and no
    // request leaves the process.
    const base = resolveScpProxyBase()
    const response = await fetch(`${base}/ask`, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json", Authorization: `Bearer ${token}` },
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
