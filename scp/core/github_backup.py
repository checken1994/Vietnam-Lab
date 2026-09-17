"""
SCP V88 — Auto-backup SQLite to Hugging Face Hub
Pushes v13.db to HF dataset every N cycles to prevent data loss on container restart.
"""
import logging
import os
import re
import shutil
import tempfile
import urllib.parse
from datetime import datetime
from pathlib import Path

from scp.security.url_safety import EgressDeniedError, enforce_egress_policy

logger = logging.getLogger("scp.github_backup")

BASE_DIR = Path(__file__).parent.parent.parent.resolve()
DB_PATH = BASE_DIR / "data" / "v13.db"

# Hugging Face is an external-write destination (R2).  This module has no
# general capability broker for third-party drivers, so keep a deliberately
# narrow, local adapter: one HTTPS origin, one dataset repo, and two remote
# paths only.  The adapter is not a replacement for the SCP capability system.
_HF_ORIGIN = "https://huggingface.co"
_HF_HOST = "huggingface.co"
_APPROVED_BACKUP_REPO = "checken9x/scp-backup"
_APPROVED_REPO_TYPE = "dataset"
_APPROVED_MAIN_PATH = "v13.db"
_APPROVED_SNAPSHOT_RE = re.compile(r"backups/v13_[0-9]{8}_[0-9]{6}\.db\Z")
_KNOWN_EGRESS_MODES = frozenset({"allowlist", "deny", "offline", "disabled"})
_HF_CREATE_API_URL = f"{_HF_ORIGIN}/api/repos/create"
_HF_COMMIT_API_URL = (
    f"{_HF_ORIGIN}/api/datasets/{_APPROVED_BACKUP_REPO}/commit/main"
)

# Compatibility export remains stable; destination and token resolution below
# are authoritative and happen at each attempt.
BACKUP_REPO = _APPROVED_BACKUP_REPO
BACKUP_INTERVAL_CYCLES = 10
# [FIX 2026-07-09] Cycle-count gating alone is unsafe now that cycles run as
# fast as ~14s each (post throughput optimization): every restart re-attempts
# immediately once cycle count crosses 10, and legit periodic attempts can
# still add up to 2 commits each. Confirmed LIVE: HF returned 429 "exceeded
# rate limit for repository commits (128 per hour)" with 34 consecutive
# failures within minutes of this container starting. Adding a hard wall-clock
# floor makes this robust regardless of cycle speed or how many times the
# container restarts within an hour.
BACKUP_MIN_SECONDS_BETWEEN = 240  # at most 15/hour attempts -> <=30 commits/hour, safely under the 128/hour cap
# [FIX] Skip the extra timestamped snapshot most of the time — it's a nice-to-have
# point-in-time history, not required for restore (v13.db alone is the restore
# source on boot). Only take a snapshot every Nth successful backup to cut commit
# volume roughly in half without losing restore capability.
SNAPSHOT_EVERY_N_BACKUPS = 5

_last_backup_cycle = 0
_last_backup_time = 0.0
_backup_success_count = 0


def _configured_backup_repo() -> str:
    """Resolve the operator target without allowing an arbitrary destination.

    The environment is read at attempt time so a changed target cannot bypass
    the guard merely because this module was imported earlier.  The fixed
    repository is not operator-overridable.
    """
    configured = os.environ.get("HF_BACKUP_REPO", _APPROVED_BACKUP_REPO)
    return str(configured).strip()


def _safe_exception_label(exc: BaseException) -> str:
    """Return a non-sensitive failure label; never retain driver text/tokens."""
    return type(exc).__name__ or "Exception"


def _is_rate_limit_exception(exc: BaseException) -> bool:
    """Recognize rate limiting without copying driver text into logs/status."""
    if getattr(exc, "status_code", None) == 429:
        return True
    return "429" in str(exc) or "rate limit" in str(exc).lower()


def _configured_hf_token() -> str:
    """Resolve the token from the current environment for this attempt only."""
    return os.environ.get("HF_TOKEN", "").strip()


def _target_denial(url: str, reason: str) -> EgressDeniedError:
    """Build a policy error without exposing credentials or driver details."""
    return EgressDeniedError(url, reason)


def _egress_block_status() -> str:
    """Return a stable, non-sensitive status for a blocked R2 attempt."""
    mode = os.environ.get("SCP_EGRESS_MODE", "").strip().lower()
    if not mode or mode not in _KNOWN_EGRESS_MODES:
        return "fail_closed_unknown"
    return "unconfigured_external_write"


def _guard_hf_write(
    *,
    repo_id: str,
    repo_type: str,
    path_in_repo: str | None,
) -> str:
    """Authorize one exact HF write immediately before its driver call.

    This is a narrow adapter for this third-party driver, not a full SCP
    capability system.  It enforces the fixed HTTPS origin, the one approved
    dataset repo, the allowed remote paths, and then delegates mode/allowlist
    enforcement to the repository egress PEP.
    """
    if repo_id != _APPROVED_BACKUP_REPO or repo_type != _APPROVED_REPO_TYPE:
        raise _target_denial(_HF_ORIGIN, "backup destination is not approved")

    if path_in_repo is None:
        url = _HF_CREATE_API_URL
        expected_path = "/api/repos/create"
    else:
        if path_in_repo != _APPROVED_MAIN_PATH and not _APPROVED_SNAPSHOT_RE.fullmatch(
            path_in_repo
        ):
            raise _target_denial(_HF_ORIGIN, "backup path is not approved")
        url = _HF_COMMIT_API_URL
        expected_path = f"/api/datasets/{_APPROVED_BACKUP_REPO}/commit/main"

    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != _HF_HOST
        or parsed.port is not None
        or parsed.path != expected_path
        or parsed.query
        or parsed.fragment
    ):
        raise _target_denial(_HF_ORIGIN, "HF write endpoint is not exact-approved")

    # This R2 adapter must never inherit the generic dev fail-open default:
    # external writes require an explicit known-safe mode and exact allowlist.
    mode = os.environ.get("SCP_EGRESS_MODE", "").strip().lower()
    if mode not in _KNOWN_EGRESS_MODES or mode != "allowlist":
        raise _target_denial(url, _egress_block_status())
    allowlist = {
        item.strip().lower().rstrip(".")
        for item in os.environ.get("SCP_EGRESS_ALLOWLIST", "").split(",")
        if item.strip()
    }
    if _HF_HOST not in allowlist:
        raise _target_denial(url, "unconfigured_external_write")

    # PEP immediately before the third-party driver.  No raw token enters it.
    enforce_egress_policy(url)
    return url


# [FIX 2026-07-09] Observability + resilience state, exposed to health.json so
# backup staleness is visible on /health instead of silently going undetected.
# Root cause found live: backups went stale for ~8.7h (01:23->10:05 UTC) with
# zero visibility on the dashboard — the only way to notice was manually
# inspecting HF dataset commit history. Also found the two uploads (v13.db +
# timestamped snapshot) were NOT independently resilient: if the 2nd upload
# raised, the whole function's except-block swallowed it AND skipped updating
# _last_backup_cycle, but there was no record of *which* upload failed or when.
_last_backup_result = {
    "last_attempt_at": None,
    "last_success_at": None,
    "last_error": None,
    "consecutive_failures": 0,
}


def get_backup_status() -> dict:
    """Expose backup health for inclusion in write_health()."""
    return dict(_last_backup_result)


def backup_to_hf(cycle_num: int) -> bool:
    """
    Backup v13.db to Hugging Face dataset repo.
    Called every cycle, but only uploads every BACKUP_INTERVAL_CYCLES AND at
    most once per BACKUP_MIN_SECONDS_BETWEEN (whichever gate is stricter).
    """
    # [FALSE-POS-FIX] F824: removed `_last_backup_result` from global —
    # only dict key assignments (_last_backup_result["key"] = val), NOT name rebinding.
    # The other 3 names ARE assigned: _last_backup_cycle (L162), _last_backup_time (L163),
    # _backup_success_count (L164 +=).
    global _last_backup_cycle, _last_backup_time, _backup_success_count

    import time as _time
    if cycle_num - _last_backup_cycle < BACKUP_INTERVAL_CYCLES:
        return False
    if _time.time() - _last_backup_time < BACKUP_MIN_SECONDS_BETWEEN:
        return False

    _last_backup_result["last_attempt_at"] = datetime.now().isoformat()

    if not DB_PATH.exists():
        logger.warning(f"[BACKUP] DB not found: {DB_PATH}")
        _last_backup_result["last_error"] = "db_not_found"
        return False

    hf_token = _configured_hf_token()
    if not hf_token:
        logger.warning("[BACKUP] No HF_TOKEN set, skipping backup")
        _last_backup_result["last_error"] = "no_hf_token"
        return False

    repo_id = _configured_backup_repo()
    if repo_id != _APPROVED_BACKUP_REPO:
        _last_backup_result["consecutive_failures"] += 1
        _last_backup_result["last_error"] = "backup_destination_not_approved"
        logger.warning("[BACKUP] Backup destination is not approved; skipping")
        return False

    tmp_path = None
    try:
        # Create repo only after exact-target and egress authorization.  A
        # create failure is terminal for this attempt: upload must not follow.
        _guard_hf_write(
            repo_id=repo_id,
            repo_type=_APPROVED_REPO_TYPE,
            path_in_repo=None,
        )
        from huggingface_hub import HfApi
        api = HfApi(endpoint=_HF_ORIGIN, token=hf_token)
        try:
            api.create_repo(repo_id=repo_id, repo_type=_APPROVED_REPO_TYPE, exist_ok=True)
        except Exception as exc:
            _last_backup_result["consecutive_failures"] += 1
            _last_backup_result["last_error"] = "create_repo_failed"
            logger.warning("[BACKUP] Repository preparation failed (%s); upload skipped", _safe_exception_label(exc))
            return False

        # Copy DB to temp (avoid locking issues)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_path = tmp.name

        # [V104.35 #72] TẠI SAO: shutil.copy2 only copies v13.db, NOT v13.db-wal.
        # SCP runs in WAL mode → recent writes live in -wal file until checkpoint.
        # Without checkpoint, backup misses last N minutes of data → silent data loss
        # on container restart. Fix: checkpoint WAL before copy (mirror auto_backup.py).
        try:
            import sqlite3 as _sqlite3
            _conn = _sqlite3.connect(str(DB_PATH), timeout=10.0)
            _conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            _conn.close()
        except Exception as _exc:
            logger.warning(
                "[V104.35 #72] WAL checkpoint failed (proceeding anyway): %s",
                _safe_exception_label(_exc),
            )

        shutil.copy2(str(DB_PATH), tmp_path)

        # [FIX] Each upload now independently try/except'd so a transient
        # failure on ONE of the two files doesn't silently hide the other's
        # outcome or leave zero trace of what happened.
        # [FIX] Snapshot copy only every SNAPSHOT_EVERY_N_BACKUPS successful
        # backups — cuts commit volume roughly in half to stay under HF's
        # 128 commits/hour cap. v13.db (updated every time) is what's used to
        # restore on boot, so this doesn't reduce actual crash-safety.
        main_ok = False
        snap_ok = None  # None = skipped this round (not attempted, not a failure)
        errors = []
        upload_exceptions = []

        try:
            _guard_hf_write(
                repo_id=repo_id,
                repo_type=_APPROVED_REPO_TYPE,
                path_in_repo=_APPROVED_MAIN_PATH,
            )
            api.upload_file(
                path_or_fileobj=tmp_path,
                path_in_repo=_APPROVED_MAIN_PATH,
                repo_id=repo_id,
                repo_type=_APPROVED_REPO_TYPE,
                token=hf_token,
            )
            main_ok = True
        except Exception as exc:
            upload_exceptions.append(exc)
            errors.append(f"main_upload:{_safe_exception_label(exc)}")

        if main_ok and (_backup_success_count % SNAPSHOT_EVERY_N_BACKUPS == 0):
            snapshot_path = f"backups/v13_{timestamp}.db"
            try:
                _guard_hf_write(
                    repo_id=repo_id,
                    repo_type=_APPROVED_REPO_TYPE,
                    path_in_repo=snapshot_path,
                )
                api.upload_file(
                    path_or_fileobj=tmp_path,
                    path_in_repo=snapshot_path,
                    repo_id=repo_id,
                    repo_type=_APPROVED_REPO_TYPE,
                    token=hf_token,
                )
                snap_ok = True
            except Exception as exc:
                upload_exceptions.append(exc)
                snap_ok = False
                errors.append(f"snapshot_upload:{_safe_exception_label(exc)}")

        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except Exception as exc:
                logger.debug("[BACKUP] Temporary backup cleanup failed (%s)", _safe_exception_label(exc))

        db_size = DB_PATH.stat().st_size / 1024

        if main_ok:
            # [FIX] Only advance cycle/time gates when the primary v13.db copy
            # succeeded — that's the actual restore source on boot. Snapshot
            # (if attempted) is a bonus, not required for crash-safety.
            _last_backup_cycle = cycle_num
            _last_backup_time = _time.time()
            _backup_success_count += 1
            _last_backup_result["last_success_at"] = datetime.now().isoformat()
            _last_backup_result["consecutive_failures"] = 0
            if errors:
                _last_backup_result["last_error"] = "; ".join(errors)
                logger.warning(f"[BACKUP] Partial success (main=True, snapshot={snap_ok}): {errors}")
            else:
                _last_backup_result["last_error"] = None
            logger.info(f"[BACKUP] v13.db ({db_size:.0f}KB) uploaded to approved HF dataset "
                        f"(main=True, snapshot={snap_ok})")
            return True
        else:
            _last_backup_result["consecutive_failures"] += 1
            _last_backup_result["last_error"] = "; ".join(errors)
            # [FIX] Back off harder on rate-limit (429) errors specifically —
            # retrying every cycle against an hour-long rate-limit window just
            # burns more attempts once the window resets. Push the time-gate
            # forward so we don't hammer it again for a while.
            if any(_is_rate_limit_exception(exc) for exc in upload_exceptions):
                _last_backup_time = _time.time() - BACKUP_MIN_SECONDS_BETWEEN + 900  # wait ~15 extra min
                logger.warning(f"[BACKUP] Rate-limited (429) — backing off ~15min extra before next attempt (failure #{_last_backup_result['consecutive_failures']})")
            else:
                logger.warning(f"[BACKUP] Failed entirely (attempt #{_last_backup_result['consecutive_failures']}): {errors}")
            return False

    except EgressDeniedError as exc:
        _last_backup_result["consecutive_failures"] += 1
        reason = getattr(exc, "reason", "")
        if reason in {"fail_closed_unknown", "unconfigured_external_write"}:
            _last_backup_result["last_error"] = reason
        else:
            _last_backup_result["last_error"] = "egress_denied"
        logger.warning("[BACKUP] Egress policy denied backup (%s)", _safe_exception_label(exc))
        return False
    except Exception as exc:
        _last_backup_result["consecutive_failures"] += 1
        _last_backup_result["last_error"] = "backup_failed"
        logger.warning("[BACKUP] Failed (%s)", _safe_exception_label(exc))
        return False
    finally:
        if tmp_path is not None and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception as exc:
                logger.debug("[BACKUP] Temporary backup cleanup failed (%s)", _safe_exception_label(exc))
