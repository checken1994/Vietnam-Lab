/**
 * Dashboard database facade — [ARC-03 Remediation].
 *
 * Direct database access from the Next.js dashboard is completely decommissioned.
 * Single Source of Truth for all state machines, leases, and mutations is the
 * SCP FastAPI backend (TaskKernel) accessed exclusively over HTTP API with
 * Capability Token verification. Prisma and direct database drivers are retired.
 * This facade remains fail-closed to prevent accidental local DB coupling.
 */
export type DashboardDatabaseUnavailable = {
  readonly unavailable: true
  readonly reason: "DIRECT_DATABASE_ACCESS_RETIRED_USE_FASTAPI_HTTP_API"
}

export const db: DashboardDatabaseUnavailable = {
  unavailable: true,
  reason: "DIRECT_DATABASE_ACCESS_RETIRED_USE_FASTAPI_HTTP_API",
}
