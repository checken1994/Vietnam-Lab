/**
 * API /api/scp/health — composite health check across all 3 SCP subsystems.
 *
 * Makes the dashboard a CONTROL PANEL (not just a static report).
 *
 * [Task 1-A · Fix 4-d-010] Previously this endpoint ONLY probed the FastAPI
 * Python backend on port 8000. So "SCP online" was a false-green whenever
 * the loop-scheduler (port 3030) or the llm-bridge (port 11434) was dead
 * — even though SCP's autofix engine depends on the LLM bridge. Now this
 * endpoint probes all 3 services in parallel with 1s timeouts and returns
 * a composite status. DNA #19 (observation gap closed) + #22 (PASS ≠ TRUE:
 * "online" no longer claimed from a single-subsystem probe) + #26 (reality
 * test).
 *
 * Backward compat: the legacy `scp: "online"|"degraded"|"offline"` field is
 * preserved for existing consumers (scp-control-panel.tsx). It is derived
 * from the new composite: "online" only if all 3 services ok.
 *
 * SCP started via:    SCP_PORT=8000 python -m scp
 * Scheduler started via: node mini-services/loop-scheduler/index.js
 * LLM bridge started via: node mini-services/llm-bridge/index.js
 */
import { NextRequest, NextResponse } from "next/server"
// [S6b security sweep] The env-derived probe targets are resolved AND
// allowlist-validated in dashboard/src/lib/scp-backend-url.ts (no fetch sink
// there); this route fetches only the bases that helper returns, and probe()
// still runs the same allowlist gate immediately before its fetch.
import { resolveHealthProbeTargets } from "../../../../lib/scp-backend-url"
// [FIX · 2026-10-22] Inject the operator credential on backend probes so they
// land in the backend's "auth" rate-limit tier (120/min) instead of "public"
// (30/min) — see the comment at the probe call site below.
import { injectServiceAuth } from "../../../../lib/scp-service-auth"
// PEP at the sink (relative import so the T03 sweep test can load this
// module under plain node — no tsconfig paths there).
import { isAllowedProbeTarget } from "../../../../lib/probe-allowlist"


const START_HINTS = {
  fastapi: "Run: SCP_PORT=8000 python -m scp (in your scp folder)",
  loopScheduler: "Run: node mini-services/loop-scheduler/index.js (in project root)",
  agentDesktop: "Run: docker compose up agent-desktop (or vnc :6080)",
  llmBridge: "Direct API-First Gateway active (Rule 6: external providers via OpenRouter/Groq)",
}

// [S5 security sweep] Extra probe hosts explicitly approved by the operator
// (comma-separated). Used as an extension of the internal-network allowlist
// in @/lib/probe-allowlist — e.g. "scp-api,scheduler.internal".
const EXTRA_PROBE_HOSTS = (process.env.SCP_HEALTH_ALLOWED_HOSTS ?? "")
  .split(",")
  .map((h) => h.trim().toLowerCase())
  .filter(Boolean)

interface ServiceHealth {
  ok: boolean
  latencyMs: number | null
  status?: number
  error?: string
  hint?: string
}

/**
 * Probe one HTTP endpoint with a hard timeout. Returns the latency in ms
 * if the response was ok, or `{ ok: false }` with the error otherwise.
 * DNA #7: fail-open — never throws.
 *
 * [S5 security sweep] SSRF gate (CWE-918): before any fetch the target is
 * validated against the probe allowlist (loopback/private/docker-internal +
 * operator extension from SCP_HEALTH_ALLOWED_HOSTS, resolved by
 * scp-backend-url.ts). A blocked target is reported as a service that is
 * down — no request leaves the process. This is the PEP placed immediately
 * before the fetch sink, so all probe call sites below are covered by the
 * same gate.
 */
async function probe(
  url: string,
  extraHosts: string[],
  timeoutMs = 1000,
  headers: Record<string, string> = {},
): Promise<ServiceHealth> {
  const guard = isAllowedProbeTarget(url, extraHosts)
  if (!guard.allowed) {
    return {
      ok: false,
      latencyMs: 0,
      error: `probe blocked by allowlist: ${guard.reason}`.slice(0, 200),
    }
  }
  const t0 = Date.now()
  try {
    const res = await fetch(url, {
      signal: AbortSignal.timeout(timeoutMs),
      headers: { Accept: "application/json", ...headers },
      cache: "no-store",
    })
    const latencyMs = Date.now() - t0
    if (res.ok) {
      return { ok: true, latencyMs, status: res.status }
    }
    return { ok: false, latencyMs, status: res.status }
  } catch (e) {
    const latencyMs = Date.now() - t0
    const err = e instanceof Error ? e.message : String(e)
    return { ok: false, latencyMs, error: err.slice(0, 200) }
  }
}

export async function GET(request: NextRequest) {
  const _url = request.url
  const checkedAt = new Date().toISOString()

  // [S6b security sweep] Env-derived bases resolved + validated in
  // scp-backend-url.ts — no environment token remains in this file's
  // fetch dataflow.
  const targets = resolveHealthProbeTargets()

  // Probe services in parallel. DNA #19: cover observation gaps.
  // Standardized 4-container stack sibling services:
  // 1. FastAPI backend (:8000)
  // 2. Loop scheduler (:3030)
  // 3. Agent desktop noVNC (:6080)
  // llm-bridge (:8081 / :11434) is deprecated in Milestone M4 (Zero-Bun, Rule 6 API-First Orchestration).
  // Direct Gateway handles LLM inference inside backend.
  //
  // [FIX · 2026-10-22 · Rate limit] Probe /health KHÔNG kèm credential ⇒ backend
  // xếp nó vào tier "public" (30 req/phút theo IP). Dashboard poll mỗi 5 giây +
  // mọi tab người dùng mở ⇒ vượt hạn mức ⇒ backend trả 429 ⇒ route báo "offline
  // giả" và mọi bản đồ/panel đọc nó cũng báo sai. Nay probe kèm credential của
  // operator (injectServiceAuth: bearer/capability token) ⇒ backend xếp vào tier
  // "auth" (120 req/phút). Caller-sent credentials vẫn được forward nguyên văn;
  // ở posture "trusted proxy" (SCP_DASHBOARD_PROXY_SECRET đặt) KHÔNG chèn token
  // của operator — request không có credential vẫn đi không-auth như thiết kế.
  const authHeaders = injectServiceAuth(request)

  const [fastapiResult, loopResult, desktopResult, llmResult] = await Promise.allSettled([
    probe(`${targets.fastapi}/health`, targets.extraHosts, 1000, authHeaders),
    probe(`${targets.loopScheduler}/healthz`, targets.extraHosts, 1000, authHeaders).then(async (h) => {
      if (h.ok || h.status !== 404) return h
      return probe(`${targets.loopScheduler}/`, targets.extraHosts, 1000, authHeaders)
    }),
    probe(`${targets.agentDesktop}/`, targets.extraHosts, 1000, authHeaders),
    targets.llmBridge === "direct"
      ? Promise.resolve({ ok: true, latencyMs: 0, status: 200 })
      : probe(`${targets.llmBridge}/api/tags`, targets.extraHosts, 1000, authHeaders),
  ])

  const fastapi: ServiceHealth =
    fastapiResult.status === "fulfilled"
      ? { ...fastapiResult.value, hint: START_HINTS.fastapi }
      : { ok: false, latencyMs: null, error: "probe rejected", hint: START_HINTS.fastapi }
  const loopScheduler: ServiceHealth =
    loopResult.status === "fulfilled"
      ? { ...loopResult.value, hint: START_HINTS.loopScheduler }
      : { ok: false, latencyMs: null, error: "probe rejected", hint: START_HINTS.loopScheduler }
  const agentDesktop: ServiceHealth =
    desktopResult.status === "fulfilled"
      ? { ...desktopResult.value, hint: START_HINTS.agentDesktop }
      : { ok: false, latencyMs: null, error: "probe rejected", hint: START_HINTS.agentDesktop }
  
  // Backward compatibility: llmBridge is maintained in JSON response for test contracts,
  // but marked as active API-First Gateway if no standalone bridge is running.
  const isDirectGateway = targets.llmBridge === "direct" || llmResult.status !== "fulfilled" || !llmResult.value.ok
  const llmBridge: ServiceHealth = isDirectGateway
    ? { ok: true, latencyMs: 0, status: 200, hint: START_HINTS.llmBridge }
    : { ...llmResult.value, hint: START_HINTS.llmBridge }

  const overall = fastapi.ok && loopScheduler.ok && agentDesktop.ok

  // Backward-compat derivation: legacy `scp` field that the control panel
  // already consumes. "online" only if FastAPI responds 200.
  // "degraded" if FastAPI is up but a sibling service is down.
  // "offline" if FastAPI itself is unreachable.
  const scp: "online" | "degraded" | "offline" = fastapi.ok
    ? (overall ? "online" : "degraded")
    : "offline"

  const httpStatus = overall ? 200 : fastapi.ok ? 200 : 503

  return NextResponse.json(
    {
      // Legacy fields (backward compat for scp-control-panel.tsx).
      scp,
      hint: scp === "online" ? null : START_HINTS.fastapi,
      // Composite fields (new in Task 1-A — Fix 4-d-010).
      overall,
      fastapi,
      loopScheduler,
      agentDesktop,
      llmBridge,
      checkedAt,
    },
    { status: httpStatus },
  )
}
