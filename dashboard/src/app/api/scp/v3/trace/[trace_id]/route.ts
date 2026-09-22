import { NextResponse } from "next/server"
import { resolveScpApiBase } from "../../../../../../lib/scp-backend-url"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

export async function GET(
  request: Request,
  { params }: { params: Promise<{ trace_id: string }> }
) {
  try {
    const { trace_id } = await params
    const headers: Record<string, string> = { Accept: "application/json" }
    const token = process.env.SCP_AUTH_TOKEN_SECRET || process.env.SCP_ADMIN_KEY || process.env.SCP_PC_CONTROLLER_TOKEN || ""
    if (token) {
      headers["X-SCP-PC-Token"] = process.env.SCP_PC_CONTROLLER_TOKEN || token
      headers["Authorization"] = "Bearer " + token
    }
    const base = resolveScpApiBase()
    const response = await fetch(`${base}/v3/trace/${encodeURIComponent(trace_id)}`, {
      cache: "no-store",
      headers,
      signal: AbortSignal.timeout(10000),
    })
    const data = await response.json().catch(() => ({ error: "Invalid trace response" }))
    return NextResponse.json(data, { status: response.status })
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Trace retrieval failed" },
      { status: 502 }
    )
  }
}
