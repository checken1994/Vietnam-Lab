import { NextResponse } from "next/server"
import { resolveScpApiBase } from "../../../../../../lib/scp-backend-url"
import { extractCallerAuth } from "../../../../../../lib/auth-helper"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

export async function GET(
  request: Request,
  { params }: { params: Promise<{ trace_id: string }> }
) {
  try {
    const auth = extractCallerAuth(request)
    if (!auth.authenticated || auth.errorResponse) {
      return auth.errorResponse || NextResponse.json({ error: "Unauthorized" }, { status: 401 })
    }

    const { trace_id } = await params
    const headers: Record<string, string> = {
      Accept: "application/json",
      Authorization: auth.authHeader,
    }
    if (auth.pcToken) {
      headers["X-SCP-PC-Token"] = auth.pcToken
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
