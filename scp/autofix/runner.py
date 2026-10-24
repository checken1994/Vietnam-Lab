# SCP CIRCUIT: M07 — STATUS: CLOSED_WITH_KNOWN_GAP (closure: docs/evidence-summary/M07-closure.json)
"""
SCP Auto-Fix Runner — wires detected bugs into AutoFixEngine.process_bug().

[EXEC-2 M1 / FRESH-3 finding] AutoFixEngine.process_bug() had NO CALLER.
api_server.py instantiated the engine only for permission-management
endpoints (/v105/autofix/permissions, /stats, /attack-mode) — never to
actually process detected bugs. The deep audit (and any future bug
detector) had no way to feed BugReports into the engine. This runner
closes that loop:

    bug source →  runner.run_once()  →  engine.process_bug(bug)  →  patch
                  ^^^^^^^^^^^^^^^^^^
                  THIS FILE (was missing — process_bug was dead code)

Bug sources supported:
  1. Explicit list:  run_once(bugs=[BugReport(...), ...])
  2. JSONL audit trail: run_once(audit_file="data/audit_bugs.jsonl")
     Each line: {"file": "...", "line": 123, "bug_type": "...",
                 "description": "...", "suggested_fix": "..."}
  3. Default: reads data/audit_bugs.jsonl if it exists, else no-op.

The runner uses the singleton engine (get_autofix_engine) so:
  - rate limits apply across runs (_fixes_this_cycle)
  - cooldown applies (_recent_fixes)
  - attack_mode toggle is respected
  - permission_gate._pending is shared

CLI:
    python -m scp.autofix.runner                       # default audit file
    python -m scp.autofix.runner --audit-file path     # custom file
    python -m scp.autofix.runner --max-bugs 5          # limit per run

[Task 10-B Modularity Refactor B] Extracted ~960 LOC into
`scp/autofix/runner_phases/` sub-package:
  - ast_scan.py         (AST scanner classes + helpers)
  - permission_check.py (V9.1 ImpactPrioritization)
  - report.py           (V5.9 + V8.0 full-scan + individual scanners)
  - pre_startup.py      (STARTUP-GATE + scheduled audit)

All public symbols re-exported here — backward compatible.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

# [hygiene F821-fix] logger định nghĩa trước khối try dotenv bên dưới: nhánh
# except ImportError gọi logger.debug — trước đây logger nằm cuối import block
# nên python-dotenv thiếu sẽ bị NameError(logger) che mất trong except handler.
logger = logging.getLogger("scp.autofix.runner")

# [GA-B67-DECOUPLE] Decouple .env from import time.
# Auto-loading .env at import time broke test hermeticity. Now wrapped in
# ensure_runner_env() and invoked explicitly from run_once() and CLI _main().
def ensure_runner_env() -> None:
    """Explicitly load .env for runner executions (GA B67 decouple)."""
    try:
        from dotenv import load_dotenv
        _override = os.environ.get("SCP_ENV_FILE")
        if _override:
            _env_path = Path(_override).expanduser()
            if not _env_path.is_absolute():
                _env_path = Path.cwd() / _env_path
        else:
            _env_path = Path(__file__).resolve().parent.parent.parent / ".env"
        if _env_path.exists() and _env_path.is_file():
            if _override:
                for _line in _env_path.read_text(encoding="utf-8-sig").splitlines():
                    _line = _line.strip()
                    if not _line or _line.startswith("#") or "=" not in _line:
                        continue
                    _key, _, _val = _line.partition("=")
                    _key = _key.strip()
                    _val = _val.strip().strip(chr(34)).strip(chr(39))
                    if _key:
                        os.environ[_key] = _val
            else:
                load_dotenv(_env_path, override=False)
    except ImportError:
        _override = os.environ.get("SCP_ENV_FILE")
        if _override:
            _env_path = Path(_override).expanduser()
            if not _env_path.is_absolute():
                _env_path = Path.cwd() / _env_path
        else:
            _env_path = Path(__file__).resolve().parent.parent.parent / ".env"
        logger.debug("runner: python-dotenv unavailable — using manual .env parser for %s", _env_path, exc_info=True)
        if _env_path.exists() and _env_path.is_file():
            for _line in _env_path.read_text(encoding="utf-8").splitlines():
                _line = _line.strip()
                if not _line or _line.startswith("#") or "=" not in _line:
                    continue
                _key, _, _val = _line.partition("=")
                _key = _key.strip()
                _val = _val.strip().strip(chr(34)).strip(chr(39))
                if _key and (_override or _key not in os.environ):
                    os.environ[_key] = _val
# [SCP-DNA-FIX 4-b-012] Re-export the audit log schema so callers of
# runner.run_once() (which feeds bugs into AutoFixEngine.process_bug())
# can construct validated audit entries. DNA #8 (KB accumulation): every
# Tier 1 / Tier 2 / Tier 3 / Tier 4 fix decision MUST carry before_hash
# + after_hash + rollback_token + reality_test_result. Pre-fix, only
# Tier 3 logged all 4 fields — Tier 1/2/4 wrote only a message (no
# forensic data, no rollback path). The AuditLogEntry Pydantic schema
# below enforces the 4 required fields at write time for ALL tiers —
# an entry missing before_hash / after_hash / rollback_token /
# reality_test_result is REJECTED (Pydantic ValidationError), not
# silently logged.
from scp.autofix.audit_log import (  # noqa: F401
    AuditLogEntry,
    compute_hashes,
    make_rollback_token_backup,
    make_rollback_token_git,
    write_audit_entry,
)
from scp.autofix.classifier import BugReport, BugTier
from scp.autofix.engine import get_autofix_engine

# [Task 10-B] Re-export extracted phase modules — backward compat.
# Tất cả code moved vào runner_phases/, runner.py chỉ giữ run_once + run_deep_audit + _main + _load_bugs_from_jsonl.
from scp.autofix.runner_phases.ast_scan import (  # noqa: F401
    _MAX_BUGS_PER_SCAN,
    _MAX_SCAN_FILES,
    _SCP_ROOT,
    DEEP_AUDIT_RESULTS_FILE,
    _BareExceptPassFinder,
    _build_bug_report,
    _iter_python_files,
    _ModuleNameCollector,
    _scan_file,
    _UndefinedNameFinder,
    _write_deep_audit_result,
    ast_scan_scp,
)
from scp.autofix.runner_phases.permission_check import _prioritize_bugs  # noqa: F401
from scp.autofix.runner_phases.pre_startup import (
    pre_startup_audit as _pre_startup_audit_impl,
)
from scp.autofix.runner_phases.pre_startup import (
    run_scheduled as _run_scheduled_impl,
)
from scp.autofix.runner_phases.report import (  # noqa: F401
    run_full_scan,
    run_full_scan_and_fix,
    run_single_scanner,
)

DEFAULT_AUDIT_FILE = "data/audit_bugs.jsonl"

from scp.autofix.runner_helpers import (
    _load_bugs_from_jsonl,
    _main,
)



def run_once(
    bugs: list[BugReport] | None = None,
    audit_file: str | Path | None = None,
    max_bugs: int = 0,
    ast_scan: bool = False,
    deep_audit_log: str | Path | None = None,
    parallel_workers: int = 0,
    parallel_min_files: int = 2,
    deterministic_only: bool = False,
) -> dict:
    """Run one auto-fix pass over the given bugs.

    Args:
        bugs: Explicit list of BugReports to process. If None, loads from
              audit_file (or DEFAULT_AUDIT_FILE).
        audit_file: Path to JSONL audit trail. Ignored if `bugs` is given.
        max_bugs: Cap number of bugs processed this run (0 = no limit).
                  Useful to bound runtime when the audit trail is large.
        ast_scan: [EXEC-1 A4] If True AND no bugs were loaded from
              `bugs`/`audit_file`, run the AST scanner over scp/ to detect
              bugs automatically. Default False (preserves EXEC-2 behavior).
        deep_audit_log: [EXEC-1 A4] Optional path to write per-bug outcomes
              from AST scan runs (default: data/deep_audit_results.jsonl).
              Only written when ast_scan=True.
        parallel_workers: [IMP-11 R7-Full] If >0, use N parallel worker
              threads for Tier-1/Tier-2 fixes (default 0 = sequential).
              Tier-3/Tier-4 always sequential.
        parallel_min_files: [IMP-11] Only use parallel mode if ≥N distinct
              files have bugs (default 2 — avoids thread overhead for
              single-file runs).
        deterministic_only: Apply only predefined/pattern/deterministic fixes;
              unresolved findings are recorded as skipped without LLM/provider I/O.

    Returns:
        Summary dict:
          {"processed": N, "fixed": N, "permission_requested": N,
           "skipped": N, "denied": N, "details": [...], "engine_stats": {...},
           "source": "explicit" | "audit_file" | "ast_scan" | "empty"}
    """
    ensure_runner_env()
    # [HC-40 · 2026-10-23] Kill-switch boundary — ĐIỂM CHẶN CHUNG của cả 2
    # đường kích hoạt audit/fix:
    #   (1) backend scheduled job 'deep_audit_scheduler' (lifespan) →
    #       run_deep_audit() → run_once();
    #   (2) loop-scheduler mini-service → POST /v105/autofix/run-audit →
    #       run_deep_audit() → run_once().
    # Kill switch engaged (file flag data/pc_controller/KILL_SWITCH | env
    # SCP_KILL_SWITCH=1 | kernel global_kill — authority duy nhất
    # scp.security.kill_switch) → KHÔNG scan, KHÔNG fix: fail-closed, trả
    # summary rỗng với source='kill_switch_blocked' — contract dict của caller
    # (.get('processed')/.get('fixed')/...) vẫn đọc được, không raise.
    from scp.security.kill_switch import is_engaged
    if is_engaged():
        logger.warning(
            '[HC-40] run_once blocked: kill switch engaged — audit/fix cycle REFUSED (fail-closed)'
        )
        return {
            "processed": 0, "fixed": 0, "permission_requested": 0,
            "skipped": 0, "denied": 0, "details": [], "engine_stats": {},
            "source": "kill_switch_blocked",
            "pending_permissions": 0,
        }
    source = "explicit"
    if bugs is None:
        path = Path(audit_file) if audit_file else Path(DEFAULT_AUDIT_FILE)
        bugs = _load_bugs_from_jsonl(path)
        if bugs:
            source = "audit_file"
            logger.info(f"[runner] loaded {len(bugs)} bug(s) from {path}")
        else:
            # [CLOSED-LOOP PHASE 3] Ingest runtime test failures (Highest priority — Reality > Model)
            try:
                from scp.autofix.ingestors.test_failure_ingestor import TestFailureIngestor
                test_bugs = TestFailureIngestor().collect_bugs(max_bugs=max_bugs or 10)
                if test_bugs:
                    bugs = test_bugs
                    source = "test_failure_ingestor"
                    logger.info(f"[runner] Ingested {len(bugs)} test failure(s) from pytest output")
            except Exception as _t_err:
                logger.debug(f"[runner] TestFailureIngestor error: {_t_err}")

            # [CLOSED-LOOP PHASE 3] Ingest active failure patterns from failure_ledger
            if not bugs:
                try:
                    from scp.epistemic.failure_ledger import get_failure_ledger
                    ledger_bugs = get_failure_ledger().get_unresolved_bugs(limit=max_bugs or 5)
                    if ledger_bugs:
                        bugs = ledger_bugs
                        source = "failure_ledger"
                        logger.info(f"[runner] Ingested {len(bugs)} unresolved failure(s) from failure_ledger")
                except Exception as _fl_err:
                    logger.debug(f"[runner] FailureLedger ingestion error: {_fl_err}")

            if not bugs and ast_scan:
                # [EXEC-1 A4] No bugs from explicit list, audit file, test failures or ledger —
                # fall back to AST scan over the scp/ package.
                max_scan_files = int(os.environ.get("SCP_AUTOFIX_MAX_SCAN_FILES", "1000") or "1000")
                include_ent = os.environ.get("SCP_AUTOFIX_INCLUDE_ENTERPRISE", "0") == "1"
                bugs = ast_scan_scp(
                    max_files=max_scan_files,
                    max_bugs=max_bugs or 200,
                    include_enterprise=include_ent,
                )
                source = "ast_scan"
                logger.info(f"[runner] AST scan found {len(bugs)} bug(s) in scp/")
            elif not bugs:
                source = "empty"
                logger.info(f"[runner] no bugs in {path}; pass ast_scan=True to auto-scan")
    else:
        logger.info(f"[runner] processing {len(bugs)} explicit bug(s)")

    if max_bugs > 0 and len(bugs) > max_bugs:
        logger.info(f"[runner] capping at {max_bugs} bugs (had {len(bugs)})")
        bugs = bugs[:max_bugs]

    # [V9.0-WHY-GATE] WHY prioritizes/skips bugs — PRIMARY CONTROL GATE
    # TẠI SAO: WHY = chốt (block-capable), không phải cố vấn. WHY Gate filters
    # bugs BEFORE they reach AutoFix — skips bugs whose action_desc matches a
    # falsification reject pattern (relaxation, security bypass, error suppression).
    # Non-blocking on WHY error (default allow) so scanner never breaks because
    # WHY itself crashed. Constitution HARD LOCK preserved inside gate().
    # [COOLDOWN-FILTER] Filter out bugs currently in cooldown to avoid redundant scanner/WHY cycles
    try:
        _eng_cd = get_autofix_engine()
        _uncooled_bugs = []
        for bug in bugs:
            in_cd, cd_reason = _eng_cd.is_in_cooldown(bug)
            if in_cd:
                logger.debug(f"[runner] Skipping bug in cooldown: {bug.file}:{bug.line} ({bug.bug_type}) — {cd_reason}")
                continue
            _uncooled_bugs.append(bug)
        bugs = _uncooled_bugs
    except Exception as _cd_err:
        logger.debug(f"[runner] Pre-WHY cooldown filter error (fail-open): {_cd_err}", exc_info=True)

    try:
        from scp.meta.why_gate import get_why_gate
        _why_gate = get_why_gate()
        _filtered_bugs = []
        for bug in bugs:
            _why = _why_gate.gate(
                action_type="scanner",
                action_desc=f"Bug {bug.bug_type} at {bug.file}:{bug.line}",
                context=(bug.description or "")[:200],
            )
            if _why.allowed:
                _filtered_bugs.append(bug)
            else:
                logger.info(
                    f"[V9.0-WHY-GATE] Bug skipped by WHY: {bug.file}:{bug.line} "
                    f"({bug.bug_type}) — {_why.falsification_reason[:80]}"
                )
                try:
                    get_autofix_engine().record_cooldown(
                        bug, reason=f"WHY gate skipped: {_why.falsification_reason[:60]}"
                    )
                except Exception as _cd_rec_err:
                    logger.debug(f"[runner] WHY skip cooldown record error: {_cd_rec_err}")
        if len(_filtered_bugs) != len(bugs):
            logger.info(
                f"[V9.0-WHY-GATE] Scanner filtered {len(bugs) - len(_filtered_bugs)} "
                f"of {len(bugs)} bugs via WHY"
            )
        bugs = _filtered_bugs
    except Exception as _why_err:
        logger.debug(f"[V9.0-WHY-GATE] WHY Gate error (non-blocking, default allow): {_why_err}", exc_info=True)

    #  ImpactPrioritization — prioritize bugs by impact (CRITICAL first).
    # TẠI SAO: WHY gate filters "should we fix this?" (action). Prioritization
    # decides "fix in what ORDER?" (verify/impact layer). WHY + prioritize = 2 layer.
    # CRITICAL (security/race) fixed before LOW (dead code/perf) — even with
    # rate limit, the most dangerous bugs get fixed first. Non-blocking fail-open.
    try:
        _bugs_before = len(bugs)
        bugs = _prioritize_bugs(bugs)
        if len(bugs) != _bugs_before:
            logger.warning(
                f" _prioritize_bugs changed bug count "
                f"({_bugs_before} → {len(bugs)}) — unexpected, investigate"
            )
    except Exception as _prio_call_err:
        logger.debug(f" _prioritize_bugs call error (fail-open): {_prio_call_err}", exc_info=True)

    engine = get_autofix_engine()

    # [P1-2 FIX R16] Wire check_pending_permissions before processing bugs.
    # BEFORE: check_pending_permissions (engine.py:2294) was implemented but had
    #         0 callers (G2-3 in dead-code audit). The permission gate was a no-op.
    # AFTER:  before processing any bug, call check_pending_permissions(). If there
    #         are pending (un-approved) permission requests for CRITICAL/HIGH bugs,
    #         log a warning so operator knows to review. Non-blocking (fail-open) —
    #         we don't want to brick the runner if the permission system has a bug,
    #         but operator sees the warning in logs.
    #
    #  ROOT FIX: use-before-def bug (DNA #22 — PASS ≠ TRUE).
    # BEFORE: summary["pending_permissions"] = ... was 9 lines BEFORE summary = {...}
    #         defined. Broad except at L287 caught NameError → logged "fail-open"
    #         → permission count NEVER recorded in audit summary.
    # AFTER:  Define summary FIRST, then check_pending_permissions, then set field.
    #         No more use-before-def. NameError impossible.
    summary = {
        "processed": 0, "fixed": 0, "permission_requested": 0,
        "skipped": 0, "denied": 0, "details": [], "engine_stats": {},
        "source": source,
        "pending_permissions": 0,  #  initialize here, populated below
    }

    try:
        if hasattr(engine, "check_pending_permissions"):
            _pending = engine.check_pending_permissions()
            if isinstance(_pending, dict) and _pending.get("pending_count", 0) > 0:
                logger.warning(
                    f"[P1-2 R16] { _pending.get('pending_count', 0)} pending permission "
                    f"request(s) awaiting human review — CRITICAL/HIGH fixes may be "
                    f"blocked until approved. Pending: {_pending.get('pending', [])[:3]}"
                )
                summary["pending_permissions"] = _pending.get("pending_count", 0)
            else:
                logger.debug(f"[P1-2 R16] check_pending_permissions: {_pending}")
    except Exception as _pp_err:
        logger.error(
            f"[P1-2 R16] check_pending_permissions crashed (fail-open, DNA #7): {_pp_err} — "
            f"proceeding, but operator must investigate permission system health", exc_info=True
        )

    log_path = str(deep_audit_log) if deep_audit_log else DEEP_AUDIT_RESULTS_FILE
    write_log = (source == "ast_scan")

    # [FIX-3] TẠI SAO: AST scanner produces text descriptions, not search-replace
    # blocks. process_bug() feeds text directly to _apply_fix → Strategy 1 fails
    # → Strategy 3 queues for review → Bug B counts as "fixed". Use LLM bridge.
    from scp.autofix.llm_fix import process_bug_with_llm

    # [IMP-11 R7-Full] Parallel worker pool dispatch.
    # If parallel_workers > 0 AND ≥ parallel_min_files distinct files have bugs,
    # dispatch to concurrent_runner.run_once_parallel(). Else sequential.
    _use_parallel = False
    if parallel_workers > 0 and len(bugs) > 0:
        distinct_files = {getattr(b, "file", "") for b in bugs}
        if len(distinct_files) >= parallel_min_files:
            _use_parallel = True

    if _use_parallel:
        try:
            from scp.autofix.concurrent_runner import run_once_parallel
            logger.info(
                f"[IMP-11] dispatching to parallel runner "
                f"(workers={parallel_workers}, "
                f"files={len({getattr(b, 'file', '') for b in bugs})})"
            )
            return run_once_parallel(
                bugs=bugs,
                engine=engine,
                process_bug_fn=(lambda bug, eng: process_bug_with_llm(bug, eng, allow_llm=not deterministic_only)),
                max_workers=parallel_workers,
                write_log=write_log,
                log_path=log_path,
                tier3_tier4_sequential=True,
            )
        except ImportError as _par_imp_err:
            logger.warning(
                f"[IMP-11] concurrent_runner unavailable, falling back to sequential: "
                f"{_par_imp_err}"
            )
        except Exception as _par_err:  # noqa: BLE001
            logger.warning(
                f"[IMP-11] parallel runner failed, falling back to sequential: {_par_err}"
            )

    for bug in bugs:
        try:
            result = process_bug_with_llm(bug, engine, allow_llm=not deterministic_only)
        except Exception as e:
            logger.error(f"[runner] process_bug_with_llm failed for {bug.file}:{bug.line}: {e}", exc_info=True)
            result = {"action": "skipped", "tier": 0, "reason": f"runner error: {e}"}

        summary["processed"] += 1
        action = result.get("action", "skipped")
        if action == "fixed":
            summary["fixed"] += 1
        elif action == "permission_requested":
            summary["permission_requested"] += 1
        elif action == "denied":
            summary["denied"] += 1
        else:
            summary["skipped"] += 1

        detail = {
            "file": bug.file,
            "line": bug.line,
            "bug_type": bug.bug_type,
            "result": result,
        }
        summary["details"].append(detail)

        # [EXEC-1 A4] persist AST-scan outcomes for forensic review
        if write_log:
            _write_deep_audit_result({
                "timestamp": time.time(),
                "source": "ast_scan",
                **detail,
            }, log_path)

    # [R10 v4 WIRE — IMP-22] Incremental call-graph delta after fix batch.
    # TẠI SAO: when bugs are fixed, the call-graph (used by IMP-16 blast_radius
    # + IMP-20 type_flow_verifier) becomes stale. Re-building the full graph
    # takes ~3-5s for 378 .py files. IMP-22 apply_delta(changed_files) only
    # recomputes the affected entries — ~50ms per file.
    # Fail-open per DNA #7: if graph corrupt → rebuild from scratch (the
    # CallGraph class handles this internally). If apply_delta crashes →
    # log + continue (next run will use stale graph, no worse than pre-R10).
    try:
        from scp.autofix.callgraph_delta import get_call_graph as _v4_get_cg
        # Collect files that were actually patched in this run.
        _v4_changed_files: list[str] = []
        for _d in summary["details"]:
            _r = _d.get("result", {})
            if _r.get("action") == "fixed" and _r.get("patched"):
                _fp = _d.get("file", "")
                if _fp and _fp not in _v4_changed_files:
                    _v4_changed_files.append(_fp)
        if _v4_changed_files:
            _v4_cg = _v4_get_cg()
            # Build full graph if cache is empty/missing (first run).
            # apply_delta handles this internally — if graph has no files,
            # it calls build_full() under the hood. Fail-open.
            _v4_delta = _v4_cg.apply_delta(_v4_changed_files)
            logger.info(
                f"[R10 v4 IMP-22] callgraph apply_delta: "
                f"{len(_v4_changed_files)} changed file(s), "
                f"added_edges={len(_v4_delta.added_edges)}, "
                f"removed_edges={len(_v4_delta.removed_edges)}, "
                f"affected_callers={len(_v4_delta.affected_callers)}"
            )
            summary["callgraph_delta"] = {
                "changed_files": len(_v4_changed_files),
                "added_edges": len(_v4_delta.added_edges),
                "removed_edges": len(_v4_delta.removed_edges),
                "affected_callers": len(_v4_delta.affected_callers),
            }
        else:
            logger.debug("[R10 v4 IMP-22] no fixed files — skip apply_delta")
    except ImportError as _v4_cg_imp:
        logger.debug(
            f"[R10 v4 IMP-22] callgraph_delta unavailable (fail-open): {_v4_cg_imp}"
        )
    except Exception as _v4_cg_err:
        logger.debug(
            f"[R10 v4 IMP-22] callgraph_delta apply_delta crash (fail-open): {_v4_cg_err}", exc_info=True
        )

    # [AUTONOMOUS MAKER-CHECKER GOVERNANCE RESOLVER - PHASE 4]
    # Autonomously review pending fixes & governance proposals without human bottleneck (Rule #7)
    try:
        from scp.autofix.subsystem_reviewer import get_subsystem_reviewer
        _reviewer = get_subsystem_reviewer()
        _rev_summary = _reviewer.review_pending_fixes(auto_prune=True)
        if _rev_summary.total_inspected > 0:
            logger.info(
                f"[runner] SubsystemIndependentReviewer: inspected={_rev_summary.total_inspected}, "
                f"approved={_rev_summary.approved_count}, rejected={_rev_summary.rejected_count}, "
                f"quarantined={_rev_summary.quarantined_count}"
            )
            summary["reviewer_summary"] = _rev_summary.to_dict()
            summary["fixed"] += _rev_summary.approved_count
    except Exception as _rev_err:
        logger.debug(f"[runner] SubsystemIndependentReviewer error (fail-closed/skip): {_rev_err}", exc_info=True)

    summary["engine_stats"] = engine.stats()
    logger.info(
        f"[runner] done: source={source}, {summary['processed']} processed, "
        f"{summary['fixed']} fixed, {summary['permission_requested']} perm-req, "
        f"{summary['skipped']} skipped"
    )
    return summary


def run_deep_audit(max_bugs: int = 0, deterministic_only: bool = False) -> dict:
    """[EXEC-1 A4] Convenience wrapper: AST-scan scp/ and feed findings
    into AutoFixEngine.process_bug().

    Equivalent to run_once(ast_scan=True). Used by the
    /v105/autofix/run-audit API endpoint.
    """
    return run_once(ast_scan=True, max_bugs=max_bugs, deterministic_only=deterministic_only)


def pre_startup_audit(max_bugs: int | None = None) -> dict:
    """[STARTUP-GATE] Pre-startup audit (backward-compat wrapper).

    [ROOT-FIX 46] max_bugs default: env SCP_MAX_STARTUP_BUGS or 200 (was 50).
    """
    if max_bugs is None:
        max_bugs = int(os.environ.get("SCP_MAX_STARTUP_BUGS", "200"))
    return _pre_startup_audit_impl(run_deep_audit, max_bugs=max_bugs)


def run_scheduled(interval_seconds: int = 7 * 24 * 3600,
                  max_bugs: int = 0) -> None:
    """[EXEC-1 A4] Block + run deep audit on a schedule (backward-compat wrapper)."""
    return _run_scheduled_impl(run_deep_audit, interval_seconds=interval_seconds,
                               max_bugs=max_bugs)





if __name__ == "__main__":
    sys.exit(_main())
