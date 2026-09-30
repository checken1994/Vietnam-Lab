/**
 * Server-side service auth injection for dashboard → SCP backend proxies.
 *
 * [LOCAL-DEV 2026-10-01] The dashboard's Next.js proxy routes previously
 * forwarded only browser-sent auth headers to the SCP backend. The browser
 * holds no `Authorization` / `X-SCP-PC-Token` credentials, so every proxied
 * call reached the backend unauthenticated (401/403) and the V3.1/V3.5
 * control-plane cards rendered "offline". The dashboard is a trusted local
 * proxy: `src/middleware.ts` already gates every /api/scp/* route fail-closed
 * to loopback hostnames (localhost / 127.0.0.1 / [::1]), so for local access
 * the proxy can inject the backend service tokens configured on the
 * operator's own machine (repo root `.env`).
 *
 * Contract:
 * - Token values are read from the repo root `.env` (same `findScpRoot`
 *   pattern as the activity route) and cached for 30 seconds. Shell
 *   (process env) values override file values.
 * - Browser-sent credentials always take precedence: `injectServiceAuth`
 *   only fills headers the caller did NOT already send.
 * - Secrets are never logged, never echoed into errors, and never returned —
 *   only mapped into outbound header names.
 */
import { existsSync, readFileSync } from "node:fs"
import path from "node:path"

const ENV_CACHE_TTL_MS = 30_000

const PC_TOKEN_KEY = "SCP_PC_CONTROLLER_TOKEN"

// [Evidence 2026-10-01] Backend auth acceptance, probed live against
// 127.0.0.1:8000 (tools probes, no secrets printed):
// - /v3/pc/*, /v3/hands/*, /v3/web/* (`_guard`) compare `X-SCP-PC-Token`
//   against SCP_PC_CONTROLLER_TOKEN → 200 with the token, 403 without.
// - /v3/trace/* (`verify_admin`, scp/security/auth.py) compares the Bearer
//   token against SCP_AUTH_TOKEN_SECRET / SCP_AUTH_PASSWORD only: a live
//   probe showed `Bearer <SCP_ADMIN_KEY>` → 401 while
//   `Bearer <SCP_AUTH_TOKEN_SECRET>` passed (404 = trace not found, i.e.
//   auth accepted). SCP_ADMIN_KEY remains as a fallback (it is accepted by
//   the api-key path `verify_api_key` in scp/security/jwt_guard.py) but
//   AUTH_TOKEN_SECRET wins for the Authorization header.
const BEARER_KEY_PRIORITY = ["SCP_AUTH_TOKEN_SECRET", "SCP_ADMIN_KEY"] as const

interface ServiceAuthValues {
  values: Partial<Record<string, string>>
  loadedAt: number
}
let envCache: ServiceAuthValues | null = null

function findScpRoot(): string {
  if (process.env.SCP_ROOT && existsSync(process.env.SCP_ROOT)) return process.env.SCP_ROOT
  let cur = process.cwd()
  for (let i = 0; i < 4; i++) {
    if (existsSync(path.join(cur, "data")) && (existsSync(path.join(cur, "spec")) || existsSync(path.join(cur, "scp")))) {
      return cur
    }
    cur = path.resolve(cur, "..")
  }
  return "D:\\scp"
}

function parseEnvFile(content: string): Record<string, string> {
  const values: Record<string, string> = {}
  for (const rawLine of content.split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line || line.startsWith("#")) continue
    const eq = line.indexOf("=")
    if (eq <= 0) continue
    const key = line.slice(0, eq).trim()
    let value = line.slice(eq + 1).trim()
    if (
      value.length >= 2 &&
      ((value.startsWith('"') && value.endsWith('"')) ||
        (value.startsWith("'") && value.endsWith("'")))
    ) {
      value = value.slice(1, -1)
    }
    if (key) values[key] = value
  }
  return values
}

function loadServiceAuthValues(): Partial<Record<string, string>> {
  const now = Date.now()
  if (envCache && now - envCache.loadedAt < ENV_CACHE_TTL_MS) return envCache.values

  const values: Partial<Record<string, string>> = {}
  const wanted: readonly string[] = [...BEARER_KEY_PRIORITY, PC_TOKEN_KEY]
  try {
    const envPath = path.join(findScpRoot(), ".env")
    if (existsSync(envPath)) {
      const parsed = parseEnvFile(readFileSync(envPath, "utf8"))
      for (const key of wanted) {
        const value = parsed[key]?.trim()
        if (value) values[key] = value
      }
    }
  } catch {
    // Fail closed to "no injection" rather than a wrong injection: the
    // proxied call then surfaces the backend's own 401/403 offline shape.
  }

  // Shell env overrides file values.
  for (const key of wanted) {
    const value = process.env[key]?.trim()
    if (value) values[key] = value
  }

  envCache = { values, loadedAt: now }
  return values
}

/**
 * Headers to merge into the outgoing proxy fetch. Only fills credentials the
 * browser did not already send (browser-sent headers take precedence).
 * Returns an empty object when no token is configured — callers keep their
 * existing behavior for that case.
 */
export function injectServiceAuth(request: Request): Record<string, string> {
  const headers: Record<string, string> = {}
  const values = loadServiceAuthValues()

  const pcToken = values[PC_TOKEN_KEY]
  if (pcToken && !request.headers.get("x-scp-pc-token")) {
    headers["X-SCP-PC-Token"] = pcToken
  }

  if (!request.headers.get("authorization")) {
    for (const key of BEARER_KEY_PRIORITY) {
      const value = values[key]
      if (value) {
        headers["Authorization"] = `Bearer ${value}`
        break
      }
    }
  }

  return headers
}
