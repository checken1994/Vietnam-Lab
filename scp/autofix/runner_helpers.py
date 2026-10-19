from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import logging
import os
from pathlib import Path
import sys
from typing import Any

from scp.autofix.classifier import BugReport, BugTier

logger = logging.getLogger("scp.autofix.runner_helpers")


def _load_bugs_from_jsonl(path: Path) -> list[BugReport]:
    """Load BugReports from a JSONL audit trail.

    Each line must have: file, line, bug_type, description, suggested_fix.
    Unknown fields are ignored; missing optional fields get defaults.
    Malformed lines are skipped with a warning (best-effort).
    """
    bugs: list[BugReport] = []
    if not path.exists():
        return bugs
    try:
        with open(path, encoding="utf-8") as f:
            for lineno, raw in enumerate(f, 1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError as e:
                    logger.warning(f"[runner] {path}:{lineno} bad JSON: {e}")
                    continue
                # Required fields
                if not all(rec.get(k) for k in ("file", "bug_type", "description")):
                    logger.warning(f"[runner] {path}:{lineno} missing required fields, skip")
                    continue
                try:
                    bugs.append(
                        BugReport(
                            file=rec["file"],
                            line=int(rec.get("line", 0) or 0),
                            bug_type=rec["bug_type"],
                            description=rec["description"],
                            suggested_fix=rec.get("suggested_fix", ""),
                            tier=BugTier.TIER_1_AUTO_FIX,  # classifier will re-tier
                            is_restraint=bool(rec.get("is_restraint", False)),
                            is_reversible=bool(rec.get("is_reversible", True)),
                            affects_logic=bool(rec.get("affects_logic", False)),
                        )
                    )
                except (KeyError, ValueError, TypeError) as e:
                    logger.warning(f"[runner] {path}:{lineno} bad record: {e}")
    except Exception as e:
        logger.error(f"[runner] failed to read {path}: {e}", exc_info=True)
    return bugs


def _main() -> int:
    from scp.autofix.runner import DEFAULT_AUDIT_FILE, ensure_runner_env, run_once
    from scp.autofix.runner_phases.ast_scan import ast_scan_scp
    from scp.autofix.runner_phases.report import (
        run_full_scan,
        run_full_scan_and_fix,
        run_single_scanner,
    )

    ensure_runner_env()
    parser = argparse.ArgumentParser(
        description="Run SCP Auto-Fix over a bug audit trail (default: data/audit_bugs.jsonl)"
    )
    parser.add_argument(
        "--audit-file",
        default=DEFAULT_AUDIT_FILE,
        help=f"JSONL audit file (default: {DEFAULT_AUDIT_FILE})",
    )
    parser.add_argument(
        "--max-bugs",
        type=int,
        default=0,
        help="Cap bugs processed this run (0 = no limit)",
    )
    parser.add_argument(
        "--ast-scan",
        action="store_true",
        help="AST-scan scp/ for bugs (same as server STARTUP-GATE)",
    )
    parser.add_argument(
        "--scan-only",
        action="store_true",
        help="Scan scp/ and return findings without WHY/fix/reflect",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    # [IMP-11 R7-Full] Parallel fix worker pool (default: off — backward compat).
    parser.add_argument(
        "--parallel",
        type=int,
        default=0,
        metavar="N",
        help="Use N parallel worker threads for Tier-1/Tier-2 fixes "
        "(0 = sequential, default). Tier-3/Tier-4 always sequential.",
    )
    parser.add_argument(
        "--parallel-min-files",
        type=int,
        default=2,
        metavar="N",
        help="Only use parallel mode if ≥N distinct files have bugs "
        "(default 2 — avoids thread overhead for single-file runs).",
    )

    # [V5.9-SCANNER] System-wide scanners — logic + wiring + routing + schema
    parser.add_argument(
        "--full-scan",
        action="store_true",
        help="Run ALL 14 scanners (5 V5.9 + 8 V8.0 + 1 OPT-18) + AST scan",
    )
    parser.add_argument(
        "--full-scan-and-fix",
        action="store_true",
        help="Run --full-scan AND feed bugs into AutoFixEngine",
    )
    parser.add_argument(
        "--scan-dead-slm", action="store_true", help="Run DeadSLMScanner only"
    )
    parser.add_argument(
        "--scan-routing", action="store_true", help="Run RoutingGapScanner only"
    )
    parser.add_argument(
        "--scan-api-wiring",
        action="store_true",
        help="Run APIWiringScanner only",
    )
    parser.add_argument(
        "--scan-logic", action="store_true", help="Run LogicFlowScanner only"
    )
    parser.add_argument(
        "--scan-schema",
        action="store_true",
        help="Run SchemaMismatchScanner only",
    )
    # [V8.0-SCANNER] 8 NEW scanner flags
    parser.add_argument(
        "--scan-type",
        action="store_true",
        help="Run TypeContractScanner only (type mismatches)",
    )
    parser.add_argument(
        "--scan-null",
        action="store_true",
        help="Run NullSafetyScanner only (None dereference risks)",
    )
    parser.add_argument(
        "--scan-race",
        action="store_true",
        help="Run RaceConditionScanner only (missing locks)",
    )
    parser.add_argument(
        "--scan-sql",
        action="store_true",
        help="Run SQLInjectionScanner only (unsafe SQL)",
    )
    parser.add_argument(
        "--scan-resource",
        action="store_true",
        help="Run ResourceLeakScanner only (unclosed resources)",
    )
    parser.add_argument(
        "--scan-perf",
        action="store_true",
        help="Run PerformanceScanner only (O(n²) patterns)",
    )
    parser.add_argument(
        "--scan-security",
        action="store_true",
        help="Run SecurityScanner only (CWE Top 25)",
    )
    parser.add_argument(
        "--scan-dead-code",
        action="store_true",
        help="Run DeadCodeScanner only (never-referenced symbols)",
    )
    # [OPT-18-SCANNER] 1 NEW scanner flag for CWE-79 (XSS)
    parser.add_argument(
        "--scan-xss",
        action="store_true",
        help="Run XSSScanner only (CWE-79 reflected/stored XSS)",
    )
    #  2 previously-dead scanners — now wireable via CLI
    parser.add_argument(
        "--scan-hypothesis",
        action="store_true",
        help="Run HypothesisScanner only (property-based bug discovery)",
    )
    parser.add_argument(
        "--scan-self-audit",
        action="store_true",
        help="Run ScannerSelfAudit only (audits the scanners themselves)",
    )

    # [EVOLUTION] Tier 4 — WHY-controlled evolution modes
    parser.add_argument(
        "--build-module",
        metavar="SPEC.json",
        help="Build new module from SPEC JSON file (Tier 4 evolution)",
    )
    parser.add_argument(
        "--reflect",
        action="store_true",
        help="Reflect on last fix — learn WHY bug occurred (Tier 4)",
    )
    parser.add_argument(
        "--evolve",
        action="store_true",
        help="Run evolution cycle: WHY -> Audit -> Fix -> Reflect (Tier 4)",
    )
    parser.add_argument(
        "--evolution-timeout-seconds",
        type=float,
        default=300.0,
        help="Hard deadline for evolution child process (default 300s)",
    )
    parser.add_argument(
        "--evolution-stats",
        action="store_true",
        help="Show evolution engine stats",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    if args.scan_only:
        bugs = ast_scan_scp(max_bugs=args.max_bugs)
        print(
            json.dumps(
                {
                    "source": "scan_only",
                    "total_bugs": len(bugs),
                    "bugs": [
                        {
                            "file": b.file,
                            "line": b.line,
                            "bug_type": b.bug_type,
                            "tier": int(b.tier),
                        }
                        for b in bugs
                    ],
                },
                indent=2,
                default=str,
            )
        )
        return 0

    # [EVOLUTION] Tier 4 modes
    if args.build_module or args.evolve or args.reflect or args.evolution_stats:
        from scp.autofix.evolution import ModuleSpec, get_evolution_engine

        eng = get_evolution_engine()

        if args.evolution_stats:
            stats = eng.stats()
            print(
                json.dumps(
                    asdict(stats)
                    if hasattr(stats, "__dataclass_fields__")
                    else stats.__dict__,
                    indent=2,
                    default=str,
                )
            )
            return 0

        if args.build_module:
            # Load SPEC JSON
            spec_path = Path(args.build_module)
            if not spec_path.exists():
                print(f"ERROR: SPEC file not found: {spec_path}")
                return 1
            spec_data = json.loads(spec_path.read_text(encoding="utf-8"))
            spec = ModuleSpec(**spec_data)
            result = eng.build_module(spec)
            print(json.dumps(result, indent=2, default=str))
            return 0

        if args.evolve:
            from scp.core.bounded_evolution import run_bounded_evolution

            result = run_bounded_evolution(
                max_bugs=args.max_bugs or 20,
                timeout_seconds=args.evolution_timeout_seconds,
            )
            print(json.dumps(result, indent=2, default=str))
            return 0

        if args.reflect:
            # Reflect on last fix from audit log
            print("NOTE: --reflect requires a bug context. Use --evolve for full cycle.")
            return 0

    # [V5.9-SCANNER] System-wide scanners — handle --scan-* / --full-scan
    # BEFORE the default audit_file flow so they take precedence.
    if args.full_scan or args.full_scan_and_fix:
        if args.full_scan_and_fix:
            summary = run_full_scan_and_fix(max_bugs=args.max_bugs)
        else:
            scan_summary = run_full_scan(include_ast=True, max_bugs=args.max_bugs)
            # For --full-scan without --fix, just print the bug list (no engine run)
            summary = {
                "source": "full_scan",
                "scanners": scan_summary["scanners"],
                "total_bugs": scan_summary["total_bugs"],
                "bugs": [
                    {
                        "file": b.file,
                        "line": b.line,
                        "bug_type": b.bug_type,
                        "description": b.description,
                        "suggested_fix": b.suggested_fix,
                        "tier": int(b.tier),
                    }
                    for b in scan_summary["bugs"]
                ],
            }
        print(json.dumps(summary, indent=2, default=str))
        return 0

    # Individual scanner flags
    individual_scans = [
        (args.scan_dead_slm, "dead_slm"),
        (args.scan_routing, "routing"),
        (args.scan_api_wiring, "api_wiring"),
        (args.scan_logic, "logic"),
        (args.scan_schema, "schema"),
        # [V8.0-SCANNER] 8 new scanner flags
        (args.scan_type, "type"),
        (args.scan_null, "null"),
        (args.scan_race, "race"),
        (args.scan_sql, "sql"),
        (args.scan_resource, "resource"),
        (args.scan_perf, "perf"),
        (args.scan_security, "security"),
        (args.scan_dead_code, "dead_code"),
        # [OPT-18-SCANNER] 1 new scanner flag
        (args.scan_xss, "xss"),
        #  2 previously-dead scanners — now wireable via CLI
        (args.scan_hypothesis, "hypothesis"),
        (args.scan_self_audit, "self_audit"),
    ]
    if any(flag for flag, _ in individual_scans):
        combined_bugs: list[BugReport] = []
        per_scanner: dict[str, int] = {}
        for flag, scanner_name in individual_scans:
            if not flag:
                continue
            try:
                found = run_single_scanner(scanner_name)
                per_scanner[scanner_name] = len(found)
                combined_bugs.extend(found)
            except Exception as e:
                logger.error(f"[V5.9-SCANNER] {scanner_name} failed: {e}", exc_info=True)
                per_scanner[scanner_name] = -1
        summary = {
            "source": "individual_scans",
            "scanners": per_scanner,
            "total_bugs": len(combined_bugs),
            "bugs": [
                {
                    "file": b.file,
                    "line": b.line,
                    "bug_type": b.bug_type,
                    "description": b.description,
                    "suggested_fix": b.suggested_fix,
                    "tier": int(b.tier),
                }
                for b in combined_bugs
            ],
        }
        print(json.dumps(summary, indent=2, default=str))
        return 0

    # [FIX-4] TẠI SAO: CLI default read audit_file (usually empty) → 0 bugs →
    # misleading "all good" while server STARTUP-GATE finds 50. --ast-scan
    # makes CLI behavior match server behavior.
    summary = run_once(
        audit_file=args.audit_file,
        max_bugs=args.max_bugs,
        ast_scan=args.ast_scan,
        parallel_workers=args.parallel,
        parallel_min_files=args.parallel_min_files,
    )
    print(json.dumps(summary, indent=2, default=str))
    return 0


__all__ = [
    "_load_bugs_from_jsonl",
    "_main",
]
