import { NextResponse } from "next/server"

import { buildSystemMapSnapshot } from "@/lib/system-map"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

export async function GET() {
  try {
    const snapshot = buildSystemMapSnapshot()
    return NextResponse.json(snapshot, {
      status: 200,
      headers: {
        "Cache-Control": "no-store",
        "X-SCP-System-Map-Evidence": "STATIC_SOURCE_SCAN",
      },
    })
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown system-map error"
    return NextResponse.json(
      {
        schemaVersion: 1,
        evidenceMode: "SOURCE_SCAN",
        status: "UNKNOWN",
        error: message,
        nodes: [],
        edges: [],
        limitations: ["Source graph unavailable; no architecture state is inferred."],
      },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    )
  }
}
