/**
 * [K5-W2 · HC-58] Provenance metadata for the hardcoded audit-data snapshot.
 *
 * Problem this fixes: every file under `src/lib/audit-data/*` is a STATIC
 * snapshot of audit findings (bugs, autofix tiers, scanner specs, self-audit
 * rounds) captured at specific audit rounds — but nothing exported WHEN the
 * data was captured. A UI consumer could present year-old findings as if they
 * described the current system, with no way to tell (DNA #22: PASS ≠ TRUE;
 * a hardcoded finding is a model of reality, not reality).
 *
 * Evidence for the value below (git, repo `D:\scp`, branch
 * `feat/runtime-audit-brain-console` at write time):
 *   `git log -1 --format="%cI %h" -- dashboard/src/lib/audit-data/`
 *   → 2026-10-18T15:18:36+07:00  d5385c96
 *
 * That is the commit time of the LAST change touching the audit-data tree —
 * i.e. an upper bound on data age, NOT a live fetch timestamp. Consumers must
 * render SNAPSHOT_GENERATED_AT_LABEL next to any audit-data-derived content so
 * an operator can distinguish historical evidence from live probe results.
 *
 * This is a MANUAL constant on purpose: there is no build step that can derive
 * it from git today. If the audit-data tree changes, bump this value from the
 * same git command — test_openapi_snapshot_fresh.py-style drift is otherwise
 * undetectable here (see ledger note in the final K5-W2 report).
 */

/** ISO-8601 commit time of the last change to `dashboard/src/lib/audit-data/`. */
export const SNAPSHOT_GENERATED_AT = "2026-10-18T15:18:36+07:00"

/** How SNAPSHOT_GENERATED_AT was determined (git provenance, commit hash). */
export const SNAPSHOT_GENERATED_AT_PROVENANCE =
  "git log -1 -- dashboard/src/lib/audit-data/ → d5385c96 (commit time of last audit-data change; upper bound on data age, not a live timestamp)"

/** Human-readable label UI consumers MUST render next to audit-data content. */
export const SNAPSHOT_GENERATED_AT_LABEL =
  "Historical audit snapshot (generated ≤ 2026-10-18) — không phải live data"
