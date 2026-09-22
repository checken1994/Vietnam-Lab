# V4 Wire Log — Subagent F (Task 6)

**Date:** 2026-08-08 (R9 continuation)
**Agent:** Subagent F (general-purpose)
**Task:** Wire v4 modules into `scp/autofix/engine.py` — activate "implemented but unused" code.

> **🐔 Gà (R9):** "có cái gì đang có mà không dùng không?"
>
> **🤖 SCP (R9 Task 6):** 12 modules (6 v3 + 6 v4, 6,975 LOC) pass `ast.parse` + smoke-test
> but **0** were imported by `engine.py` / `runner.py` / `runner_phases/`. DNA #22
> (PASS ≠ TRUE) at its clearest: "implemented but unused" = dead code.
> This task wires the 3 highest-impact v4 modules into the live fix pipeline.

---

## Summary

- **3 v4 modules wired** into `scp/autofix/engine.py` (surgically, fail-open where safe).
- **engine.py LOC:** 1493 → 1775 (+282 LOC for hooks + shared simulation).
- **378/378** `.py` files in `scp/` pass `ast.parse` after wiring (0 FAIL).
- **Real import test:** `from scp.autofix.engine import AutoFixEngine` → **OK**.
- **End-to-end runtime test:** A `BugReport` was driven through `process_bug()`
  with monkey-patched spies on all 3 v4 entrypoints — **all 3 hooks were
  actually CALLED in the live pipeline** (DNA #22 verified: wiring ≠ true
  until the function runs).
- **Forbidden-pattern test:** A `verify=False` patch was driven through the
  pipeline → `policy_gate.evaluate_fix()` returned `allowed=False` →
  the fix was BLOCKED + logged to `data/policy_blocks.jsonl` + downstream
  hooks (`confidence_ranker`, `shadow_canary`) were short-circuited.

---

## The 3 modules wired

### Hook 1 — IMP-24 `policy_gate.evaluate_fix(fix)` (Constitutional KILL gate)

| Field | Value |
|---|---|
| **File** | `scp/autofix/engine.py` |
| **Location** | `_auto_fix()` method, between WHY gate (line ~648) and outer `try:` block (line ~731) |
| **Hook lines** | 650–729 (80 LOC including comments) |
| **Call site** | Line 674: `_v4_decision = _v4_policy_evaluate(_v4_pf)` |
| **Why this module** | WHY gate (v9.0) asks "should we fix?" (action layer). PolicyGate asks "does this PATCH TEXT contain a forbidden pattern?" (content layer — constitution KILL). Independent axis — a high-confidence fix can still violate constitution (`verify=False`, `os.chmod 0o777`, `eval()`). **HIGHEST SAFETY IMPACT.** |
| **DNA principles** | #4 (Constitution KILL — never auto-approve forbidden patterns), #22 (PASS ≠ TRUE — confidence ≠ safety), #11 (Fail loudly — every block logged + surfaced), #17 (Operator oversight — `appeal_block()` for human override) |
| **Fail-open / Fail-closed** | **DEFAULT-DENY (fail-closed) per DNA #4.** If `policy_gate` module crashes or import fails → BLOCK the fix + log loudly (`policy_gate_down: true` in response). Security > availability. NOT fail-open. |
| **Block behavior** | If `PolicyDecision.allowed == False` → return early with `{"action": "skipped", "policy_blocked": true, "policy_audit_id": ..., "policy_patterns": [...]}`. Downstream hooks (`confidence_ranker`, `shadow_canary`) are NOT called. |
| **Audit trail** | Every decision (ALLOW / REVIEW / BLOCK) is logged to `data/policy_blocks.jsonl` (append-only JSONL with SHA-256 hash chain). |
| **ast.parse** | OK |
| **Import test** | OK |
| **Runtime test** | OK — `verify=False` patch → BLOCKED, audit_id `077e846580518d52` written to `data/policy_blocks.jsonl`. |

**Code added (excerpt):**

```python
# [R9 v4 WIRE — IMP-24] Constitutional Policy Gate (DEFAULT-DENY).
try:
    from scp.autofix.policy_gate import (
        PolicyFix as _V4_PolicyFix,
        evaluate_fix as _v4_policy_evaluate,
    )
    _v4_pf = _V4_PolicyFix(
        fix_id=f"{bug.file}:{bug.line}:{bug.bug_type}",
        patch=bug.suggested_fix or "",
        patched_source="",  # not known yet — gate scans patch text only
        bug_file=bug.file or "",
        bug_line=int(bug.line or 0),
        scanner_name="autofix_engine",
        extra={"bug_type": bug.bug_type, "tier": int(bug.tier)},
    )
    _v4_decision = _v4_policy_evaluate(_v4_pf)
    if not _v4_decision.allowed:
        logger.warning(...)
        return {"action": "skipped", "tier": int(bug.tier),
                "reason": f"policy_gate BLOCK (DNA #4): ...",
                "policy_blocked": True,
                "policy_audit_id": _v4_decision.audit_id,
                "policy_patterns": list(_v4_decision.blocked_patterns)}
except ImportError as _v4_p_imp:
    logger.error(f"[R9 v4 IMP-24] policy_gate unavailable — DEFAULT-DENY (DNA #4): {_v4_p_imp}")
    return {"action": "blocked", "tier": int(bug.tier),
            "reason": f"policy_gate ImportError — DEFAULT-DENY (DNA #4): {_v4_p_imp}",
            "policy_gate_down": True}
except Exception as _v4_p_err:
    logger.error(f"[R9 v4 IMP-24] policy_gate CRASH — DEFAULT-DENY (DNA #4): {_v4_p_err}", exc_info=True)
    return {"action": "blocked", "tier": int(bug.tier),
            "reason": f"policy_gate crash — DEFAULT-DENY (DNA #4): {_v4_p_err}",
            "policy_gate_down": True}
```

---

### Hook 2 — IMP-14 `confidence_ranker.best_fix(fixes)` (accuracy filter)

| Field | Value |
|---|---|
| **File** | `scp/autofix/engine.py` |
| **Location** | `_auto_fix()` method, after validation try/except (line ~933) and before `agent._apply_fix()` (line ~1136) |
| **Hook lines** | 935–1049 (115 LOC including shared simulation + comments) |
| **Call site** | Line 993: `_v4_ranked = _v4_rank_best([_v4_candidate], bug_type=bug.bug_type)` |
| **Why this module** | Existing flow has exactly 1 candidate fix per bug. IMP-14 scores it (bug-FP-rate × source-quality × blast-radius × ast-parse × reality-test × relaxation-cap) → confidence ∈ [0,1] → disposition `auto_apply` / `review` / `discard`. If `discard` → SKIP the fix (low confidence + not a relaxation). **HIGHEST ACCURACY IMPACT** — filters low-quality LLM patches. |
| **DNA principles** | #7 (AutoFix safe — fail-open), #6 (Evidence — confidence is evidence-based, not argument-based), #22 (PASS ≠ TRUE — a fix that parses ≠ a fix that's good), #8 (KB accumulation — bug-FP-rate feeds back) |
| **Fail-open / Fail-closed** | **Fail-open per DNA #7.** If `confidence_ranker` module crashes or import fails → log debug + proceed with OLD behavior (apply without scoring). NOT fail-closed — availability > accuracy (an unscored fix is still better than no fix when safety gates below are already in place). |
| **Discard behavior** | If `_v4_ranked is None` or `disposition == "discard"` → return early with `{"action": "skipped", "confidence": <float>, "disposition": "discard"}` + record to monitor (apply_result=`discarded_by_ranker`). |
| **Shared simulation** | Lines 935–960 compute `_v4_sim_patched` by applying the same SEARCH/REPLACE pairs `_apply_fix` will apply. This simulated source is shared by both IMP-14 and IMP-23 (avoids duplicating the patch simulation). |
| **ast.parse** | OK |
| **Import test** | OK |
| **Runtime test** | OK — clean fix ranked `confidence=0.785 disposition=review` (did not discard, proceeded to apply). |

**Code added (excerpt):**

```python
# [R9 v4 WIRE — IMP-14] Confidence Ranker (fail-open).
try:
    from scp.autofix.confidence_ranker import (
        best_fix as _v4_rank_best,
        make_fix as _v4_make_fix,
    )
    if _v4_sim_patched is not None:
        _v4_candidate = _v4_make_fix(
            fix_id=_autofix_bug_id,
            patch=bug.suggested_fix or "",
            patched_source=_v4_sim_patched,
            source="llm",
            bug_type=bug.bug_type,
            bug_file=bug.file,
            bug_line=int(bug.line or 0),
            lines_changed=_v4_lines_changed,
            reality_test_result=None,
        )
        _v4_ranked = _v4_rank_best([_v4_candidate], bug_type=bug.bug_type)
        if _v4_ranked is None or _v4_ranked.disposition == "discard":
            logger.info(f"[R9 v4 IMP-14] confidence ranker DISCARDED ...")
            # record to monitor (apply_result="discarded_by_ranker")
            return {"action": "skipped", "tier": int(bug.tier),
                    "reason": f"confidence_ranker DISCARD (confidence=...)",
                    "patched": False,
                    "confidence": _v4_candidate.confidence,
                    "disposition": "discard"}
        logger.info(f"[R9 v4 IMP-14] fix ranked: confidence=... disposition=...")
except ImportError as _v4_cr_imp:
    logger.debug(f"[R9 v4 IMP-14] confidence_ranker unavailable (fail-open): {_v4_cr_imp}")
except Exception as _v4_cr_err:
    logger.debug(f"[R9 v4 IMP-14] confidence_ranker crash (fail-open): {_v4_cr_err}")
```

---

### Hook 3 — IMP-23 `shadow_canary.shadow_apply_and_compare(...)` (pre-apply canary)

| Field | Value |
|---|---|
| **File** | `scp/autofix/engine.py` |
| **Location** | `_auto_fix()` method, immediately after Hook 2 (IMP-14), before `agent._apply_fix()` (line ~1136) |
| **Hook lines** | 1051–1134 (84 LOC including comments) |
| **Call site** | Line 1072: `_v4_shadow_result = _v4_shadow_compare(target_file=bug.file, fix=_v4_shadow_fix, canary_suite=_v4_default_canary())` |
| **Why this module** | Pre-apply safety gate. Apply patch to a SHADOW COPY (temp file), import as module, run default canary suite (ast_parse + import + smoke_call + reality + property tests — 5 tests total) on BOTH original and shadow, compare outputs. Only promote (write to real file) if shadow passed. **HIGHEST SAFETY IMPACT (pre-apply).** Catches regressions that ast.parse + lint cannot (e.g. shadow raises TypeError where original returned None gracefully). |
| **DNA principles** | #7 (AutoFix safe — fail-open), #11 (Fail loudly — diffs surface in warning), #22 (PASS ≠ TRUE — parses ≠ works), #4 (Constitution — if canary internally detects a forbidden-pattern-style regression, blocks) |
| **Fail-open / Fail-closed** | **Fail-open per DNA #7.** If `shadow_canary` module crashes or import fails → log debug + proceed with OLD behavior (apply without canary). NOT fail-closed. The canary itself is fail-open internally (empty suite → `passed=True` + `flagged_for_review=True`). |
| **Block behavior** | If `CanaryResult.passed == False` → return early with `{"action": "skipped", "shadow_canary_passed": false, "shadow_diffs": [...], "shadow_tests_run": <int>}` + record to monitor (apply_result=`shadow_canary_failed`). |
| **Simulation reuse** | Uses the same `_v4_sim_patched` computed for IMP-14 — zero duplicated work. |
| **ast.parse** | OK |
| **Import test** | OK |
| **Runtime test** | OK — clean fix shadow-canary passed (`tests_run=5 flagged=True`), proceeded to apply. |

**Code added (excerpt):**

```python
# [R9 v4 WIRE — IMP-23] Shadow-Apply + Canary Compare (fail-open).
try:
    from scp.autofix.runner_phases.shadow_canary import (
        ShadowFix as _V4_ShadowFix,
        default_canary_suite as _v4_default_canary,
        shadow_apply_and_compare as _v4_shadow_compare,
    )
    if _v4_sim_patched is not None and _pre_fix_content is not None:
        _v4_shadow_fix = _V4_ShadowFix(
            original_source=_pre_fix_content,
            patched_source=_v4_sim_patched,
            fix_id=_autofix_bug_id,
        )
        _v4_shadow_result = _v4_shadow_compare(
            target_file=bug.file,
            fix=_v4_shadow_fix,
            canary_suite=_v4_default_canary(),
        )
        if not _v4_shadow_result.passed:
            logger.warning(f"[R9 v4 IMP-23] SHADOW CANARY FAILED ...")
            # record to monitor (apply_result="shadow_canary_failed")
            return {"action": "skipped", "tier": int(bug.tier),
                    "reason": f"shadow_canary FAIL: ...",
                    "patched": False,
                    "shadow_canary_passed": False,
                    "shadow_diffs": list(_v4_shadow_result.diffs[:5]),
                    "shadow_tests_run": _v4_shadow_result.tests_run}
        logger.info(f"[R9 v4 IMP-23] shadow canary OK ...")
except ImportError as _v4_sc_imp:
    logger.debug(f"[R9 v4 IMP-23] shadow_canary unavailable (fail-open): {_v4_sc_imp}")
except Exception as _v4_sc_err:
    logger.debug(f"[R9 v4 IMP-23] shadow_canary crash (fail-open): {_v4_sc_err}")
```

---

## Verification (DNA #22 — PASS ≠ TRUE, all run)

| Check | Command | Result |
|---|---|---|
| `ast.parse engine.py` | `python3 -c "import ast; ast.parse(open('scp/autofix/engine.py').read())"` | **OK** |
| Real import | `python3 -c "from scp.autofix.engine import AutoFixEngine"` | **OK** |
| Full-tree `ast.parse` sweep | python walk `scp/` (378 files) | **378 OK, 0 FAIL** |
| Hook 1 grep | `rg -n "R9 v4 WIRE — IMP-24" scp/autofix/engine.py` | **line 650** ✓ |
| Hook 2 grep | `rg -n "R9 v4 WIRE — IMP-14" scp/autofix/engine.py` | **lines 935, 962** ✓ |
| Hook 3 grep | `rg -n "R9 v4 WIRE — IMP-23" scp/autofix/engine.py` | **line 1051** ✓ |
| Hook 1 CALLED | `rg -n "_v4_policy_evaluate\(" scp/autofix/engine.py` | **line 674** ✓ (not just imported) |
| Hook 2 CALLED | `rg -n "_v4_rank_best\(" scp/autofix/engine.py` | **line 993** ✓ (not just imported) |
| Hook 3 CALLED | `rg -n "_v4_shadow_compare\(" scp/autofix/engine.py` | **line 1072** ✓ (not just imported) |
| End-to-end live pipeline test | Monkey-patch spies on all 3 v4 entrypoints + drive `process_bug()` | **All 3 hooks actually CALLED** ✓ |
| Forbidden-pattern BLOCK test | Drive `verify=False` patch through pipeline | **policy_gate BLOCKS, downstream hooks short-circuited** ✓ |
| Audit log write test | Inspect `data/policy_blocks.jsonl` after BLOCK | **Entry written with SHA-256 audit_id** ✓ |

---

## End-to-end runtime test output (DNA #22 — wiring ≠ true until function runs)

**Test 1: clean fix (all 3 hooks should fire)**

```
bug = BugReport(file="sample_module.py", line=2, bug_type="MissingReturn",
                suggested_fix="<<<<<<< SEARCH\n    return x\n=======\n    return x  # noqa\n>>>>>>> REPLACE",
                tier=TIER_2_AUTO_FIX_LOG)
result = engine.process_bug(bug)
```

→ call_log:
```
[('policy_gate.evaluate_fix', 'sample_module.py:2:MissingReturn', '<<<<<<< SEARCH\n    return x\n=======\n    return x  '),
 ('confidence_ranker.best_fix', 1, 'MissingReturn'),
 ('shadow_canary.shadow_apply_and_compare', 'sample_module.py', 'sample_module.py:2')]
```

→ result: `{"action": "fixed", "tier": 2, "patched": true, "attack_mode": false}`

→ stderr:
```
[R9 v4 IMP-14] fix ranked: confidence=0.785 disposition=review for sample_module.py:2
[R9 v4 IMP-23] shadow canary OK for sample_module.py:2 (tests_run=5 flagged=True)
[V104.48] Applied search-replace patch to sample_module.py
```

**TEST 1 PASS** — all 3 v4 hooks CALLED in live pipeline for clean fix.

---

**Test 2: forbidden pattern (`verify=False`) → policy_gate BLOCK**

```
bug = BugReport(file="sample_module.py", line=2, bug_type="TLSBypass",
                suggested_fix="<<<<<<< SEARCH\n    return x\n=======\n    import requests\n    return requests.get(url, verify=False)\n>>>>>>> REPLACE",
                tier=TIER_2_AUTO_FIX_LOG)
result = engine.process_bug(bug)
```

→ call_log:
```
[('policy_gate.evaluate_fix', 'sample_module.py:2:TLSBypass', '<<<<<<< SEARCH\n    return x\n=======\n    import req')]
```

→ result:
```json
{
  "action": "skipped",
  "tier": 2,
  "reason": "policy_gate BLOCK (DNA #4): BLOCKED by constitutional policy: verify_false_tls (DNA #4). Matched: verify_false_tls: Disabling TLS certificate verification (DNA #4)",
  "patched": false,
  "policy_blocked": true,
  "policy_audit_id": "077e846580518d52",
  "policy_patterns": ["verify_false_tls"]
}
```

→ `data/policy_blocks.jsonl` (append-only, hash-chained audit log):
```json
{"allowed": false, "audit_id": "077e846580518d52", "blocked_patterns": ["verify_false_tls"],
 "bug_file": "sample_module.py", "bug_line": 2, "fix_id": "sample_module.py:2:TLSBypass",
 "reason": "BLOCKED by constitutional policy: verify_false_tls (DNA #4). ...",
 "scanner_name": "autofix_engine", "severity": "BLOCK", "timestamp": 1786223171.98, ...}
```

→ **downstream hooks NOT called** (confidence_ranker.best_fix and shadow_canary.shadow_apply_and_compare absent from call_log).

**TEST 2 PASS** — policy_gate BLOCKS forbidden fix; downstream hooks short-circuited.

---

## Honest disclosure — what's STILL unwired (DNA #23)

The task explicitly scoped this pass to **3 highest-impact modules** (out of 12 total v3+v4). The other 9 remain unwired — they continue to pass `ast.parse` + smoke-test but are NOT called by `engine.py`. Listed below with the reason they were deprioritized for this pass:

### v4 modules NOT wired this pass (3 of 6 v4 still unwired)

| ID | Module | LOC | Reason not wired this pass |
|---|---|---|---|
| **IMP-19** | `property_validator.py` | 728 | Overlaps with existing `_verify_fix` (which already runs ast.parse + re-scan + new-bug-check + self-scan + pytest + enterprise re-scan — 6 checks). Wiring IMP-19 would add an 7th check that runs *Hypothesis-style* property tests — useful but requires generating test strategies per bug_type, which is non-trivial integration. Defer to a focused round. |
| **IMP-20** | `type_flow_verifier.py` | 723 | Cross-file type-flow verification needs the call-graph to walk (which IMP-22 builds). Wiring IMP-20 standalone would require either building the callgraph inline (slow) or depending on IMP-22 being wired first. Defer to a paired IMP-20+IMP-22 wiring pass. |
| **IMP-21** | `speculative_prefixer.py` | 798 | Speed optimization (speculative decoding for LLM fixes). SCP autofix currently uses single-shot LLM calls — speculative prefixing requires refactoring the LLM call site in `llm_fix.py` (not `engine.py`). Wrong file. Defer. |
| **IMP-22** | `callgraph_delta.py` | 642 | Speed optimization (incremental callgraph for re-scans). Wiring requires building the initial callgraph at startup (expensive) + maintaining deltas. The `runner.py` re-scan flow is the right integration point, not `engine.py`. Defer. |

### v3 modules NOT wired this pass (6 of 6 v3 still unwired)

| ID | Module | LOC | Reason not wired this pass |
|---|---|---|---|
| **IMP-13** | `ast_diff_cache.py` | 412 | Speed optimization (cache AST diffs for re-parses). Integration point is `runner.py` (the AST scan loop), not `engine.py`. Defer. |
| **IMP-14** | `confidence_ranker.py` | 402 | **WIRED THIS PASS** ✓ |
| **IMP-16** | `runner_phases/blast_radius.py` | 372 | Computes blast-radius for a fix (files affected). Overlaps with IMP-14's `_blast_radius_score` (which uses `lines_changed`). Wiring the full blast-radius module requires the callgraph (IMP-22) — defer to paired pass. |
| **IMP-17** | `runner_phases/auto_rollback.py` | 575 | Post-apply rollback watcher. Engine.py already has rollback inside `_verify_fix` failure path (line ~908-915). Wiring IMP-17 would add a *background watcher* (different lifecycle — needs a thread + queue). Defer to a focused integration. |
| **IMP-18** | `parallel_scanner.py` | 428 | Speed optimization (run scanners in parallel). Integration point is `runner.py` (the scan dispatcher), not `engine.py`. Defer. |

**Total LOC still unwired:** 6,975 − 1,805 (IMP-14 + IMP-23 + IMP-24) = **5,170 LOC** across 9 modules.

These are not "broken" — they continue to pass `ast.parse` + smoke-test, and each has a documented integration point in `V4_MANIFEST.md` (table "Integration approach"). A future round can wire them following the same surgical pattern documented here.

---

## Risk assessment (DNA #7 NON-NEGOTIABLE — fail-open where safe)

| Hook | Risk if module unavailable | Mitigation |
|---|---|---|
| IMP-24 policy_gate | **HIGH** — without policy gate, forbidden patterns (`verify=False`, `os.chmod 0o777`, `eval()`) could be auto-applied | **DEFAULT-DENY (fail-closed) per DNA #4** — if module import fails or crashes, BLOCK the fix + log loudly. Security > availability. |
| IMP-14 confidence_ranker | **LOW** — without ranker, fixes are applied with old behavior (no confidence scoring) | **Fail-open per DNA #7** — log debug + proceed with old behavior. Availability > accuracy (downstream gates still apply). |
| IMP-23 shadow_canary | **MEDIUM** — without canary, regressions that ast.parse can't catch (e.g. shadow raises TypeError where original returned None) could slip through | **Fail-open per DNA #7** — log debug + proceed with old behavior. The existing `_verify_fix` (6 checks) still runs post-apply + rolls back on failure. |

**Net effect on engine availability:**
- If ALL 3 v4 modules are available (the normal case): fixes go through 4-layer safety net (policy → confidence → shadow → verify). Stronger than before.
- If policy_gate is unavailable: **engine BLOCKS all fixes** until policy_gate is restored. This is intentional (DNA #4) — operators must restore the constitution KILL gate before auto-fixing resumes.
- If confidence_ranker / shadow_canary are unavailable: engine falls back to old behavior (apply + verify). No regression vs R8 baseline.

---

## No regressions introduced (self-audit)

- **No surrounding code refactored.** All 3 hooks are pure insertions. The existing flow (protected path check → capability gate → BareExceptPass skip → rate limit → WHY gate → **[NEW: policy gate]** → outer try → setup → backup → XSS pattern fix → provider select → validation → **[NEW: simulate patched source]** → **[NEW: confidence ranker]** → **[NEW: shadow canary]** → `agent._apply_fix()` → `_verify_fix` → audit → reflect → monitor record) is preserved.
- **All existing return paths preserved.** The new hooks ADD early-return paths but do not remove or alter existing ones.
- **No new top-level imports.** All v4 imports are lazy (inside `_auto_fix()`), so engine.py import-time behavior is unchanged. If a v4 module has a broken import, `engine.py` still imports fine — the failure surfaces only when `_auto_fix()` is called (and is handled by the per-hook try/except).
- **Shared simulation avoids duplication.** IMP-14 and IMP-23 both need `patched_source` before write. The simulation block (lines 935–960) computes `_v4_sim_patched` once, used by both hooks. Zero duplicated work.
- **Variable scoping safe.** `_pairs` is built inside a try/except (lines 785–852). The shared simulation uses `locals().get("_pairs", [])` defensively — if the validation try failed before `_pairs` assignment, simulation skips gracefully (both v4 hooks fail-open).
- **Audit chain preserved.** `policy_gate`'s immutable JSONL log (`data/policy_blocks.jsonl`) uses SHA-256 hash chaining from previous entry. The wired hook writes through this log on every decision (ALLOW/REVIEW/BLOCK).
- **378/378** `.py` files in `scp/` pass `ast.parse` after wiring (was 378 before — no regression).

---

## Files touched

- **`scp/autofix/engine.py`** — 3 hook insertions (+282 LOC: 1493 → 1775). NO other files modified.

## Files created

- **`scp/audit_r9/V4_WIRE_LOG.md`** — this file.
- **`docs/AUTOFIX_V4_WIRE_LOG.md`** — copy of this file (mirrors V3/V4_MANIFEST.md + V3/V4_CHANGELOG.md dual-location pattern from R8/R9).

## Worklog appended

- **`worklog.md`** — Task 6 section appended after Task 4+5 (orchestrator).

---

## Conclusion

DNA #22 (PASS ≠ TRUE) applied recursively to the v4 modules themselves: they passed `ast.parse` + smoke-test (PASS) but were not actually called by the engine (not TRUE). This task closes that gap for 3 of the 12 modules — `policy_gate.evaluate_fix()`, `confidence_ranker.best_fix()`, and `shadow_canary.shadow_apply_and_compare()` are now CALLED in the live `process_bug()` → `_auto_fix()` pipeline.

The recursion continues (DNA #23): a Round 11 audit of this work would likely wire the remaining 9 modules, or audit whether the 3 wired hooks have any unintended interactions (e.g. does the policy_gate's pattern matcher over-block legitimate patches that contain the substring `verify=False` in a comment? Does the shadow_canary's import-test fail on modules with side-effecting imports?).
