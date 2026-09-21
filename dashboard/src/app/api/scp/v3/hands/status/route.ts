import { NextResponse } from "next/server"
// [S6b security sweep] Base URL is resolved AND validated in
// scp-backend-url.ts (single PEP, no fetch sink there); this handler fetches
// only the validated base it returns.
import { resolveScpApiBase } from "../../../../../../lib/scp-backend-url"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

export async function GET() {
  try {
    const headers: Record<string, string> = { Accept: "application/json" }
    const token = process.env.SCP_PC_CONTROLLER_TOKEN || process.env.SCP_AUTH_TOKEN_SECRET || ""
    if (token) {
      headers["X-SCP-PC-Token"] = token
    }
    // [S6b security sweep] Resolve + allowlist-validate the backend base
    // BEFORE fetch (single PEP in scp-backend-url.ts). A blocked target
    // throws into the existing catch — offline shape unchanged.
    const base = resolveScpApiBase()
    const response = await fetch(`${base}/v3/hands/status`, {
      cache: "no-store",
      headers,
      signal: AbortSignal.timeout(5000),
    })
    if (response.status === 403 || response.status === 401) {
      return NextResponse.json({ hands: "offline", planner: { planner: "offline" }, reason: "Chưa cấu hình token hoặc chưa kích hoạt" }, { status: 200 })
    }
    const data = await response.json().catch(() => ({ hands: "offline" }))
    return NextResponse.json(data, { status: response.ok ? 200 : response.status })
  } catch (error) {
    return NextResponse.json({ hands: "offline", version: "3.2", error: error instanceof Error ? error.message : "Hands unavailable" }, { status: 503 })
  }
}
