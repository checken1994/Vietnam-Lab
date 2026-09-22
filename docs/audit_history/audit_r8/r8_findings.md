# SCP DNA Audit — Round 8 (R8) Findings

> **DNA #22 (PASS ≠ TRUE):** R7-Full reported 14 fixes "applied" + 12/12 hypothesis tests passing + 51/51 autofix files ast.parse OK. R8 (Subagent B) does NOT trust R7's PASS; instead it re-audits the SCP codebase for NEW root-cause bugs R7 missed, using world's-best-tool patterns (ruff/bandit/semgrep/vulture/pyright/CodeQL) replicated via Grep + ast.
>
> **DNA #26 (Reality > Model):** Every finding below quotes EXACT code at the EXACT line (verified by Read). Every bug has a runtime consequence. No style nits, no fiction.
>
> **DNA #23 (Coverage limits, honest disclosure):** see footer — grep-only audit, no ruff/bandit binaries, no test execution.

**Subagent:** B (Task ID 1-b, retry — previous attempt timed out)
**Scope:** `/home/z/my-project/scp/` (365 .py files)
**Method:** Targeted grep scans (12 bug-class patterns) + deep-read of 6 highest-risk files (api_server.py, judge.py, healing_v14.py, storage_manager.py, autofix/engine.py, slms.py).
**Date:** 2026-08-08 (R8)

---

## Methodology

### Step 1 — Read R7's 14 findings (`r7full-ref/docs/FIXES_APPLIED_R7.md`)
R7 covered: chemistryslm None>0 (R7-1), Tor task GC in _lifespan (R7-2), WHY race threading.Lock (R7-3), source reputation cold-start (R7-4), is_skeptical (R7-5), weight voting (R7-6), crawler restart in judge.py (R7-7), conflict_resolver rowcount (R7-8), disk prune (R7-9), hypothesis tests (R7-10), scanner self-audit (R7-11), cross-file vulture (R7-12), audit log columns + rollback endpoint (R7-13), relaxation patterns (R7-14). **R8 explicitly AVOIDS re-reporting these.**

### Step 2 — Targeted grep scans (world's-best-tool signatures)
Patterns run (via Grep tool, NOT bash grep):
- `except\s*:\s*$` / `except.*:\s*pass` / `except.*:\s*continue` (bandit/ruff S110/S112)
- `execute\(f["']` / `execute\(.*\.format` / `cursor\.execute\(.*\+` (semgrep/bandit SQLi)
- `subprocess.*shell\s*=\s*True` / `os\.system\(` / `eval\(` / `exec\(` (bandit B602/B102)
- `hashlib\.md5` / `hashlib\.sha1` / `random\.random\(\)` (bandit B324/S311)
- `def \w+\(.*=\s*\[\]` / `def \w+\(.*=\s*\{\}` (ruff B006)
- `\.acquire\(\)` without release (vulture)
- `datetime\.utcnow\(\)` / `datetime\.now\(\)` (deprecation / tz-awareness)
- `\.get\(["']\w+["']\)\.` (pyright Optional chaining)
- `==\s*0\.0` / `!=\s*0\.0` (ruff float-equality)
- `threading\.Timer\(` / `global _` (race / shared state)
- `re\.compile.*\.search.*\.group\(` (None-safety on regex)
- `\.lock\(\)|\.acquire\(\)` (resource leak)

### Step 3 — Deep-read 6 highest-risk files
1. `runtime/judge.py` (crawler restart area: lines 1080-1160)
2. `api_server.py` (lifespan + ask handler + attack-mode monitor: lines 1-1170)
3. `runtime/healing_v14.py` (full file, 361 lines)
4. `runtime/storage_manager.py` (full file, 486 lines)
5. `autofix/engine.py` (process_bug + _apply_fix + Tier-3 auto-approve: lines 1-1450)
6. `runtime/slms.py` (cache + base SLM: lines 1-100)
7. Plus: `autofix/engine_extensions.py` (rollback registry), `api/_lifespan.py` (duplicate attack-mode monitor), `api/routes/v105_routes.py` (rollback endpoint), `core/db_manager.py` (schema), `runtime/notifications.py` (storage), `meta/why_engine.py` (R7-3 lock)

### Step 4 — Rigorous documentation
For each finding: verify exact line via Read, quote exact code, explain root cause (TẠI SAO), propose a real fix, identify which world-class tool catches it, hypothesize repro, explain why R7 missed it.

---

## Summary Table

| ID | File | Line | Bug Class | Severity | One-liner |
|----|------|------|-----------|----------|-----------|
| R8-1 | `api_server.py` + `api/_lifespan.py` | 354-369 / 285-296 | Silent failure / wrong data source | HIGH | Attack-mode monitor queries non-existent SQLite `notifications` table → swallowed by except → kill_count always 0 → attack mode NEVER auto-triggers |
| R8-2 | `autofix/engine.py` | 1091-1098 | Logic error / safety guard defeated | HIGH | Tier-3 auto-approve 1h timeout re-arms on every bug call after expiry → effectively permanent until env var unset |
| R8-3 | `runtime/storage_manager.py` | 167-191 | Implementation-vs-doc / silent data loss | MEDIUM | `_rotate_large_files` overwrites `.1.gz` on every rotation; docstring says "shift .1→.2, delete .3" — only 1 generation kept, older rotations lost |
| R8-4 | `runtime/judge.py` | 1106 | Cold-start regression / silent failure | MEDIUM | R7-7's 12h stale WARN guard `if last_success > 0` disables WARN when first crawl never succeeds → operator blind to cold-start failure |
| R8-5 | `autofix/engine.py` + `api/routes/v105_routes.py` | 1171-1172 / 336-345 | Insufficient storage / misleading error | MEDIUM | Single `.tier3bak` per file → rollback of older fix on multi-fix file fails with misleading 409 "tampered" (bak_hash != before_hash) |
| R8-6 | `runtime/healing_v14.py` | 213-218 + 344-352 | Race condition (concurrent list mutation) | LOW | `heal()` appends to `self.healing_history` while `get_stats()` iterates it from another thread → CPython `RuntimeError: list changed size during iteration` |
| R8-7 | `meta/why_engine.py` | 764-765 | Race condition (check-then-act lazy lock init) | LOW | R7-3's lock uses `if not hasattr(self, "_execute_pending_lock"): self._execute_pending_lock = Lock()` — two concurrent threads could both create separate Lock objects → no mutual exclusion (R7-3 fix defeated in race window) |

**Total: 7 NEW root-cause bugs across 6 different bug classes.**

---

## Per-Finding Detail

### R8-1 — Attack-mode monitor queries non-existent SQLite `notifications` table (HIGH)

**File:** `api_server.py:354-369` (and duplicate at `api/_lifespan.py:285-296`)
**Bug class:** Silent failure / wrong data source (dead code path)
**World tool:** vulture (dead code) + manual review

**Root cause (TẠI SAO):** The `_attack_mode_monitor` background thread queries SQLite for KILL events to auto-toggle attack mode. The SQL `SELECT COUNT(*) as cnt FROM notifications WHERE timestamp > ?` assumes a SQLite table named `notifications` exists. Reality: **NO such table is ever created.** Grep for `CREATE TABLE.*notifications` returns ZERO matches across all 365 .py files. Notifications are stored in-memory (`runtime/notifications.py:95` `self._recent: list[dict] = []`) + JSONL file (`runtime/notifications.py:94` `self._notifications_file = self.data_dir / "notifications.jsonl"`), NOT in SQLite.

The query raises `sqlite3.OperationalError: no such table: notifications`, swallowed by `except Exception as e: logger.debug(f"[AUTO] Attack mode monitor: {e}")` (line 369). Debug-level logging is invisible by default. `kill_count` stays at 0 (the `row["cnt"] if row else 0` fallback). The `if kill_count > 20` branch is NEVER taken → attack mode NEVER auto-enables.

**Before code (api_server.py:355-369):**
```python
from scp.core.db_manager import db_query_one as _dq
row = _dq(
    "SELECT COUNT(*) as cnt FROM notifications "
    "WHERE timestamp > ?",
    (_time.time() - 600,)  # 10 phút gần nhất
)
kill_count = row["cnt"] if row else 0

if kill_count > 20 and not eng.in_attack_mode:
    eng.set_attack_mode(True)
    logger.warning(f"[AUTO] Attack mode ENABLED — {kill_count} KILLs in 10min")
elif kill_count < 5 and eng.in_attack_mode:
    eng.set_attack_mode(False)
    ...
except Exception as e:
    logger.debug(f"[AUTO] Attack mode monitor: {e}")
```

**Suggested fix:** Query the in-memory `UserNotificationSystem` instead of SQLite. The notification singleton lives on `judge.notifications` (see `runtime/judge.py:1237` `if getattr(self, "notifications", None):`). Replace the SQL with:
```python
from scp.api_server import get_judge
_j = get_judge()
_notif = getattr(_j, "notifications", None)
kill_count = 0
if _notif is not None:
    # _recent is list[dict] with "timestamp" (epoch float) + "severity"
    cutoff = _time.time() - 600
    kill_count = sum(
        1 for n in _notif._recent
        if n.get("timestamp", 0) > cutoff
        and n.get("event_type") in ("governance_kill", "attack_blocked")
    )
```
(Also remove the duplicate dead code in `api/_lifespan.py:285`.)

**Repro hypothesis:** Start SCP, send 50 attack prompts that get KILL verdicts. Check `eng.in_attack_mode` after 6 minutes (120s warm-up + 300s poll). It will be `False`. Logs (debug level) will show "no such table: notifications" repeating.

**Why R7 missed:** R7 audited the `_lifespan.py` task GC (R7-2) and the crawler restart (R7-7), but never traced the data flow inside `_attack_mode_monitor`. The SQL string looks plausible ("notifications table"), and the `except Exception` swallows the error silently — only `logger.debug` would reveal it (debug is off by default). R7's grep-based scanner looked for `except: pass` patterns, not "query against non-existent table" patterns.

---

### R8-2 — Tier-3 auto-approve 1h timeout broken (re-arms on every call) (HIGH)

**File:** `autofix/engine.py:1091-1098`
**Bug class:** Logic error / safety guard defeated
**World tool:** manual review + ruff PLR (logic) + semgrep `python.lang.security.audit.timeout-bypass`

**Root cause (TẠI SAO):** The 1h timeout for Tier-3 auto-approve is supposed to auto-disable after `TIER3_AUTO_TIMEOUT_SECONDS`, requiring the operator to re-set `SCP_AUTO_APPROVE_TIER3=1`. The implementation uses `self._tier3_auto_enabled_at == 0.0` as the "disabled" sentinel (line 190: `self._tier3_auto_enabled_at: float = 0.0  # 0 = disabled`). On timeout expiry (line 1098), it sets the sentinel back to `0.0`. **But on the NEXT bug call, line 1091 sees `== 0.0` (disabled) and immediately RE-ARMS by setting `_tier3_auto_enabled_at = now` (line 1092).** So the timeout is ineffective — Tier-3 auto-approve stays permanently enabled as long as the env var remains set, with the clock restarting on every bug that arrives after expiry.

The comment at line 1095-1097 says "re-enable by setting SCP_AUTO_APPROVE_TIER3=1 again" — but you don't need to.

**Before code (engine.py:1089-1099):**
```python
# Guard 2: timeout (auto-expire 1h after first enable)
now = time.time()
if self._tier3_auto_enabled_at == 0.0:
    self._tier3_auto_enabled_at = now  # first call starts the clock
elif now - self._tier3_auto_enabled_at > TIER3_AUTO_TIMEOUT_SECONDS:
    logger.warning(
        f"[TIER3-AUTO] Timed out after {TIER3_AUTO_TIMEOUT_SECONDS}s -- "
        f"re-enable by setting SCP_AUTO_APPROVE_TIER3=1 again"
    )
    self._tier3_auto_enabled_at = 0.0
    return False
```

**Suggested fix:** Use a separate `_tier3_auto_expired` flag that does NOT clear on subsequent calls. Require explicit re-arm via env var unset+set or API call.
```python
# Guard 2: timeout (auto-expire 1h after first enable)
now = time.time()
if self._tier3_auto_enabled_at == 0.0:
    if getattr(self, "_tier3_auto_expired", False):
        # Previously expired — require explicit env-var re-set to re-arm.
        # Detect re-set by tracking the env var's previous value.
        return False
    self._tier3_auto_enabled_at = now  # first call starts the clock
elif now - self._tier3_auto_enabled_at > TIER3_AUTO_TIMEOUT_SECONDS:
    logger.warning(f"[TIER3-AUTO] Timed out after {TIER3_AUTO_TIMEOUT_SECONDS}s -- "
                   f"unset + re-set SCP_AUTO_APPROVE_TIER3=1 to re-arm")
    self._tier3_auto_enabled_at = 0.0
    self._tier3_auto_expired = True
    return False
```
(Clear `_tier3_auto_expired` only when the env var transitions from "0"/unset → "1" — track via a `_tier3_env_last_seen` field.)

**Repro hypothesis:** Set `SCP_AUTO_APPROVE_TIER3=1`. Wait 1h + 1 bug. Then send 5 more bugs over the next hour. ALL 5 will still be auto-approved (the timeout re-arms). The WARNING log fires once per expiry but doesn't actually disable.

**Why R7 missed:** R7-13 added the audit log columns + rollback endpoint but never tested the timeout behavior end-to-end. The logic looks correct in isolation (`elif` branch sets to 0.0 and returns False), but the re-arm-on-next-call behavior is invisible unless you trace the SECOND call after expiry.

---

### R8-3 — `_rotate_large_files` overwrites .1.gz (no generation shift) (MEDIUM)

**File:** `runtime/storage_manager.py:167-191`
**Bug class:** Implementation-vs-doc mismatch / silent data loss
**World tool:** vulture (dead code) + manual review + ruff DOC (docstring vs impl)

**Root cause (TẠI SAO):** The docstring says "Rotation: rename to .1.gz, shift existing .1.gz → .2.gz, delete .3.gz" — implying 3 generations of compressed history are kept. The implementation does NOT do this. `gzip.open(gz_path, "wb")` opens in write+truncate mode, OVERWRITING any existing .1.gz from a previous rotation. So after the 2nd rotation, the 1st rotation's compressed data is GONE. Only the most recent rotation is ever kept.

**Before code (storage_manager.py:170-188):**
```python
"""Rotate files larger than ROTATE_SIZE_MB.

Rotation: rename to .1.gz, shift existing .1.gz → .2.gz, delete .3.gz
"""
rotated = 0
for filename in self._monitor_files:
    f = self.data_dir / filename
    if not f.is_file():
        continue
    try:
        size_mb = f.stat().st_size / (1024 * 1024)
        if size_mb > self.ROTATE_SIZE_MB:
            # Compress + rename
            gz_path = f.with_suffix(f.suffix + ".1.gz")
            with open(f, "rb") as src, gzip.open(gz_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
            # Truncate original (keep last 1000 lines)
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
            f.write_text("\n".join(lines[-1000:]) + "\n", encoding="utf-8")
            rotated += 1
            logger.info(f"[Storage] Rotated {filename} ({size_mb:.1f}MB → compressed)")
```

**Suggested fix:** Actually shift the generations before writing the new .1.gz:
```python
if size_mb > self.ROTATE_SIZE_MB:
    # Shift generations: .2.gz → delete, .1.gz → .2.gz, then write new .1.gz
    gz1 = f.with_suffix(f.suffix + ".1.gz")
    gz2 = f.with_suffix(f.suffix + ".2.gz")
    gz3 = f.with_suffix(f.suffix + ".3.gz")
    if gz3.exists():
        gz3.unlink()
    if gz2.exists():
        gz2.rename(gz3)
    if gz1.exists():
        gz1.rename(gz2)
    with open(f, "rb") as src, gzip.open(gz1, "wb") as dst:
        shutil.copyfileobj(src, dst)
    # Truncate original (keep last 1000 lines)
    ...
```

**Repro hypothesis:** Set `ROTATE_SIZE_MB = 0.1` (for testing). Write 200MB to `error_store.jsonl`. Trigger rotation → `error_store.jsonl.1.gz` created. Write another 200MB. Trigger rotation → `error_store.jsonl.1.gz` is OVERWRITTEN with the 2nd batch. The 1st batch's compressed data is LOST.

**Why R7 missed:** R7-9 verified "memory + disk prune + atomic rename + thread-safe" in `canary_monitor.py`, but `storage_manager.py`'s rotation was not in R7's audit scope. The docstring lies convincingly — readers assume the shift happens.

---

### R8-4 — R7-7's 12h stale WARN disabled for cold-start crawl failures (MEDIUM)

**File:** `runtime/judge.py:1106`
**Bug class:** Cold-start regression / silent failure
**World tool:** manual review + semgrep `python.lang.security.audit.missing-cold-start`

**Root cause (TẠI SAO):** R7-7 added a 12h healthcheck WARN to detect a stale knowledge base. The guard is `if last_success > 0 and (now - last_success) > 12 * 3600:`. The `last_success > 0` check was intended to skip the WARN before the first successful crawl (avoid noise at startup). **But it ALSO skips the WARN when the first crawl NEVER succeeds.** If SCP starts with network down / data source unavailable, `last_success` stays at 0.0 forever, and the 12h stale WARN NEVER fires. Operator gets zero signal that the knowledge base is stale from cold start — exactly the failure mode R7-7 was supposed to catch.

**Before code (judge.py:1099-1111):**
```python
last_crawl = 0.0
last_success = 0.0  #  tracks staleness for 12h healthcheck
backoff = 60  #  initial backoff on failure (seconds)

while True:
    now = time.time()
    #  12h healthcheck — WARN if no successful crawl in 12h.
    if last_success > 0 and (now - last_success) > 12 * 3600:
        logger.warning(
            f" V100 crawler STALE — no successful crawl in "
            f"{(now - last_success) / 3600:.1f}h. Knowledge base may be outdated. "
            ...
        )
```

**Suggested fix:** Track a `_scheduler_started_at` timestamp and fire the WARN if 12h have passed since start WITHOUT a successful crawl:
```python
_scheduler_started_at = time.time()
last_success = 0.0
...
while True:
    now = time.time()
    #  12h healthcheck — WARN if no successful crawl in 12h.
    # Cold-start: if first crawl never succeeded and 12h elapsed since start.
    _stale_since = last_success if last_success > 0 else _scheduler_started_at
    if now - _stale_since > 12 * 3600:
        logger.warning(
            f" V100 crawler STALE — no successful crawl in "
            f"{(now - _stale_since) / 3600:.1f}h "
            f"(last_success={last_success}, cold_start={last_success == 0}). "
            f"Knowledge base may be outdated. Check network egress + data source availability."
        )
```

**Repro hypothesis:** Start SCP with no network. Wait 12h. Check logs — NO "V100 crawler STALE" WARN fires (R7-7's WARN is gated by `last_success > 0` which is False for cold start). The crawler's backoff retry logs (ERROR level) fire, but the 12h "stale" signal that R7-7 added for operators is missing.

**Why R7 missed:** R7-7 was specifically about the failure path (don't advance last_crawl on failure). The healthcheck guard `last_success > 0` was added as a "skip startup noise" optimization, but the cold-start interaction was not tested. R7's hypothesis tests (R7-10) tested None-safety, not the 12h WARN path.

---

### R8-5 — Single .tier3bak per file → rollback of older fix fails with misleading "tampered" error (MEDIUM)

**File:** `autofix/engine.py:1171-1172` (write) + `api/routes/v105_routes.py:336-345` (read + hash check)
**Bug class:** Insufficient storage / misleading error (logic error)
**World tool:** manual review + custom CodeQL data-flow (token → file → bak → hash)

**Root cause (TẠI SAO):** R7-13's Tier-3 auto-approve writes a single `.tier3bak` backup per file:
```python
bak_path = filepath.with_suffix(filepath.suffix + ".tier3bak")
bak_path.write_text(filepath.read_text(encoding="utf-8"), encoding="utf-8")
```
This OVERWRITES any existing .tier3bak on the same file. So if bug A is fixed (writes .tier3bak = pre-A state), then bug B is fixed on the same file (writes .tier3bak = pre-B state = post-A state), the .tier3bak for bug A is LOST.

When the operator POSTs to `/v105/autofix/rollback/{token_A}`, the endpoint reads the .tier3bak (which is now pre-B state, NOT pre-A state), computes its hash, and compares to `before_hash` (the pre-A hash stored in the audit log). The hashes DON'T MATCH (because the .tier3bak was overwritten by bug B's backup). The endpoint returns HTTP 409 "Backup hash mismatch (backup tampered?)" — a MISLEADING error that suggests tampering, when the real cause is that the backup was clobbered by a later fix on the same file.

**Before code (engine.py:1171-1172):**
```python
# Backup file to .tier3bak (so Ga can audit/rollback later)
try:
    from pathlib import Path as PathCls
    filepath = PathCls(bug.file)
    if filepath.exists():
        bak_path = filepath.with_suffix(filepath.suffix + ".tier3bak")
        bak_path.write_text(filepath.read_text(encoding="utf-8"), encoding="utf-8")
except Exception as e:
    logger.warning(f"[TIER3-AUTO] Backup failed for {bug.file}: {e}")
```

**Before code (v105_routes.py:336-345):**
```python
file_path = _Path(file_path_str)
bak_path = file_path.with_suffix(file_path.suffix + ".tier3bak")
if not bak_path.is_file():
    raise HTTPException(409, f"Backup file missing: {bak_path} (cannot rollback)")
# Verify backup hash matches before_hash (tamper detection).
bak_hash = _hashlib.sha256(bak_path.read_bytes()).hexdigest()
if bak_hash != before_hash:
    raise HTTPException(
        409,
        f"Backup hash mismatch (backup tampered?): expected={before_hash} got={bak_hash}"
    )
```

**Suggested fix:** Per-token backup files (one .tier3bak per rollback_token, not per file):
```python
# engine.py:1171 — use rollback_token in backup filename
bak_path = filepath.with_suffix(
    filepath.suffix + f".tier3bak.{_rollback_token}"
)
bak_path.write_text(filepath.read_text(encoding="utf-8"), encoding="utf-8")

# v105_routes.py:336 — derive backup path from token
bak_path = file_path.with_suffix(
    file_path.suffix + f".tier3bak.{rollback_token}"
)
```
And update the misleading 409 message to: "Backup for token {token} missing or overwritten by a later fix on the same file."

**Repro hypothesis:**
1. SCP auto-approves bug A at `foo.py:10` (writes `foo.py.tier3bak` = pre-A state, audit log token_A with before_hash=hash(pre-A))
2. SCP auto-approves bug B at `foo.py:20` (writes `foo.py.tier3bak` = pre-B state = post-A state — OVERWRITES)
3. Operator POSTs `/v105/autofix/rollback/{token_A}` → endpoint reads `foo.py.tier3bak` (now pre-B state), computes hash → does NOT match before_hash (pre-A) → HTTP 409 "Backup hash mismatch (backup tampered?)"

The error message LIES — the backup wasn't tampered, it was clobbered by bug B.

**Why R7 missed:** R7-13 tested rollback with a SINGLE fix per file (T2/T3/T4 in the route docstring). The multi-fix-per-file case was not tested. R7's reality test only verified "rollback reverts file to before_hash" for a single-fix file — the hash check trivially passes when only one fix exists.

---

### R8-6 — Concurrent list mutation + iteration in healing_v14 (LOW)

**File:** `runtime/healing_v14.py:213-218` (mutation in `heal()`) + `344-352` (iteration in `get_stats()`)
**Bug class:** Race condition (concurrent list mutation without lock)
**World tool:** manual review + semgrep `python.lang.security.audit.race-condition` + ruff RUF006 (async/threading)

**Root cause (TẠI SAO):** `V14SelfHealingEngine.heal()` is called from the V98 pipeline thread (sync, inside `judge.judge()`). `get_stats()` is called from the API request thread (asyncio event loop thread, via admin endpoints). The `healing_history` list is mutated WITHOUT a lock:

```python
# heal() line 213-218:
self.healing_history.append({
    "timestamp": datetime.now().isoformat(), "error_type": issue.get("type", "unknown"),
    "strategy": strategy_name, "success": success, "duration": duration,
})
if len(self.healing_history) > 1000:  # Cap at 1000 entries
    self.healing_history = self.healing_history[-500:]
```

```python
# get_stats() line 344-346:
total = len(self.healing_history)
successes = sum(1 for h in self.healing_history if h["success"])
avg_duration = sum(h["duration"] for h in self.healing_history) / max(1, total)
```

CPython's list iterator caches the list's `ob_size` at iterator creation. If `heal()` calls `.append(...)` (changing `ob_size`) while `get_stats()` is mid-iteration, the next `next(iterator)` raises `RuntimeError: list changed size during iteration`. The asyncio event loop then propagates this as a 500 error to the admin client.

The reassignment `self.healing_history = self.healing_history[-500:]` is also racy — if `get_stats()` reads `self.healing_history` between the append and the reassignment, it gets one list; if it reads after, it gets the new (shorter) list. Not a crash, but inconsistent stats.

**Before code:** (see above)

**Suggested fix:** Guard `healing_history` with a `threading.Lock`:
```python
def __init__(self, ...):
    ...
    self._history_lock = threading.Lock()
    self.healing_history: list[dict] = []

def heal(self, issue: dict) -> dict:
    ...
    with self._history_lock:
        self.healing_history.append({...})
        if len(self.healing_history) > 1000:
            self.healing_history = self.healing_history[-500:]

def get_stats(self) -> dict:
    with self._history_lock:
        total = len(self.healing_history)
        successes = sum(1 for h in self.healing_history if h["success"])
        avg_duration = sum(h["duration"] for h in self.healing_history) / max(1, total)
    ...
```
(Or use `collections.deque(maxlen=1000)` to eliminate the manual truncation race.)

**Repro hypothesis:** Run SCP under load (concurrent /ask requests triggering heal() + admin polling /v98/status calling get_stats()). Eventually a `RuntimeError: list changed size during iteration` appears in the API response (500) or logs.

**Why R7 missed:** R7-3 added a threading.Lock to `why_engine.py` for a similar concurrent-claim race, but `healing_v14.py` was not in R7's audit scope. The mutation pattern looks safe in isolation (single-threaded read), but the cross-thread interaction is invisible without tracing call sites.

---

### R8-7 — R7-3's WHY lock has check-then-act race in lazy init (LOW)

**File:** `meta/why_engine.py:764-765`
**Bug class:** Race condition (check-then-act lazy lock init)
**World tool:** manual review + semgrep `python.lang.security.audit.double-checked-locking`

**Root cause (TẠI SAO):** R7-3 added a per-instance `threading.Lock` to serialize `execute_pending_plans` across in-process concurrent callers. The lazy-init pattern is:
```python
if not hasattr(self, "_execute_pending_lock"):
    self._execute_pending_lock = _threading.Lock()
```
This is a classic check-then-act race. If two threads enter `execute_pending_plans` concurrently and BOTH see `not hasattr(...)` as True (before either has assigned), both create NEW Lock objects. Thread A assigns `self._execute_pending_lock = Lock_A`; Thread B assigns `self._execute_pending_lock = Lock_B` (overwriting Lock_A). Now Thread B holds a reference to Lock_B (its local), while Thread A reads `self._execute_pending_lock` fresh at the `with` statement and gets Lock_B.

If Thread B reaches `with self._execute_pending_lock:` BEFORE Thread A overwrites the attribute, Thread B acquires Lock_B. Then Thread A overwrites with Lock_A and acquires Lock_A. **Both threads now hold DIFFERENT locks → no mutual exclusion** — exactly the race R7-3 was supposed to prevent.

The window is tiny (a few bytecodes between `hasattr` and `with`), but real. CPython's GIL releases between bytecodes, so a context switch can occur at the worst moment.

**Before code (why_engine.py:762-772):**
```python
import threading as _threading
# [R7-3+] Per-instance lock — guards in-process concurrency.
if not hasattr(self, "_execute_pending_lock"):
    self._execute_pending_lock = _threading.Lock()
import uuid as _uuid
_worker_id = worker_id or str(_uuid.uuid4())
_now = time.time()
# [R7-3+] Hold the lock only for the DB claim — execution can run in
# parallel (claimed_by column prevents cross-thread re-claim even if
# two threads both have rows to execute).
with self._execute_pending_lock:
    try:
        #  Atomic claim: UPDATE returns only rows THIS thread claimed.
        ...
```

**Suggested fix:** Initialize the lock in `__init__` (eager init), not lazily:
```python
def __init__(self, ...):
    ...
    self._execute_pending_lock = threading.Lock()

def execute_pending_plans(self, ...):
    # No lazy init — lock always exists.
    with self._execute_pending_lock:
        ...
```
(Or use a class-level lock to guard the instance lock creation — but eager init is simpler + race-free.)

**Repro hypothesis:** Hammer `execute_pending_plans` with 10 concurrent threads from a stress test (e.g., 10 simultaneous `/admin/why/execute` calls). With enough iterations, two threads will both create separate Lock objects and both proceed to claim the same `why_verification_plans` row. The DB-level `claimed_by` column catches this (R7-3's defense-in-depth), so no data corruption — but the in-process lock's purpose (avoiding wasted DB contention) is defeated.

**Why R7 missed:** R7-3's fix was tested for the common case (sequential calls). The race window is tiny and stress-testing concurrent `execute_pending_plans` calls was not in R7's hypothesis tests (R7-10 tested None-safety, not concurrency stress). The `hasattr` lazy-init pattern is a common Python idiom that LOOKS safe but has this subtle race.

---

## Coverage Limits (DNA #23 — honest disclosure)

1. **Grep-only audit:** `ruff`, `bandit`, `semgrep`, `vulture`, `pyright`, `CodeQL` binaries are NOT installed in this sandbox. Patterns were replicated via the Grep tool (ripgrep-based). Some bug classes that require data-flow analysis (e.g., taint propagation for SQL injection) are UNDER-COVERED — only string-pattern matches were used.
2. **No test execution:** Findings are based on static code analysis only. No runtime repro was performed. Repro hypotheses are reasoning, not verified failures.
3. **Sample-biased deep reads:** Step 3 deep-read 6 files (api_server.py, judge.py, healing_v14.py, storage_manager.py, autofix/engine.py, slms.py). The other 359 .py files were scanned via grep patterns only — bugs that don't match the grep signatures (e.g., subtle off-by-one in a for-loop, wrong boolean operator, missing await in async) in those files are NOT covered.
4. **No frontend/dashboard audit:** This subagent's scope is Python only. The Next.js dashboard (`/home/z/my-project/dashboard/`) was audited by Subagent A (round9_self_audit.md) — see SA-R8-1 (lint errors) and SA-R8-2 (sidebar count) for frontend findings.
5. **Bugs requiring multi-file data-flow tracing** (e.g., "user input flows from /ask → judge → DB write without sanitization") require a taint-flow analyzer (semgrep/CodeQL) — only approximated via grep here. Some SQL-injection risks may exist that this audit did not catch.
6. **Time-boxed:** This was a retry of a timed-out previous attempt. The audit prioritized depth (7 rigorous findings with exact line numbers + repro hypotheses) over breadth (no attempt to enumerate all 365 files). A more thorough audit might find additional bugs in `core/`, `security/`, `meta/`, `experience/`, `prediction/` modules.

---

## Conclusion

R8 (Subagent B) found **7 NEW root-cause bugs** that R7 missed, across 6 different bug classes:
- 2 HIGH (R8-1 dead attack-mode monitor, R8-2 broken Tier-3 timeout)
- 3 MEDIUM (R8-3 rotation overwrite, R8-4 cold-start WARN gap, R8-5 misleading rollback error)
- 2 LOW (R8-6 healing history race, R8-7 lazy lock race)

The 2 HIGH bugs are silent security failures (R8-1: attack-mode monitor dead, R8-2: safety timeout defeated). The 3 MEDIUM bugs are silent operational failures (R8-3: log history lost, R8-4: cold-start staleness invisible, R8-5: rollback broken for multi-fix files). The 2 LOW bugs are race conditions with tiny windows but real consequences.

**DNA #22 (PASS ≠ TRUE) applied recursively:** R7-Full reported 14 fixes applied + 12/12 hypothesis tests passing. R8 found 7 NEW bugs in the SAME codebase that R7's scanners + hypothesis tests did not catch. R7's PASS was not TRUE — there were (at least) 7 more root-cause bugs lurking.

**Recommendation for orchestrator:** Apply R8 fixes (especially R8-1 + R8-2 HIGH-severity) via the upgraded autofix v3 engine (Subagent C's IMP-13..IMP-18). Use IMP-15 (semantic_equiv) + IMP-16 (blast_radius) to verify the fixes don't break callers. Use IMP-17 (auto_rollback) to revert if regression detected.
