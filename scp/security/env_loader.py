"""Canonical early environment loading for SCP server entrypoints."""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("scp.security.env_loader")

# [W4-2 2026-10-23] SCP_ENV_FILE itself is the boundary selector: an env file
# may never redefine it (self-reference or boundary hijack). The launcher owns
# this variable; a selected file rewriting it would silently repoint every
# later load and every subprocess to a different file (.env.example ships
# `SCP_ENV_FILE=.env`, so this is a real footgun, not theoretical).
_BOUNDARY_KEYS = ("SCP_ENV_FILE",)

# Resolved env-file paths already applied in this process. Import-time callers
# (api_server.py:13, judge_llm.py:7) invoke load_selected_env() more than once;
# re-applying the same file silently clobbered variables written into
# os.environ AFTER the first load (argv port, launcher overrides) — the
# "âm thầm kéo về cấu hình cũ" defect class of audit finding 1.2.
_applied_files: set[str] = set()
_boundary_warning_emitted = False


def _iter_env_file_entries(env_path: Path):
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip(chr(34)).strip(chr(39))
        if key:
            yield key, value


def load_selected_env() -> None:
    """Load the explicit env boundary before importing modules that read env.

    When SCP_ENV_FILE is set, the file is the explicit isolated boundary and
    its keys override the parent process environment. Every such override is
    logged WARNING by key name (values are never logged), so a file silently
    pulling the process back to an old configuration (audit finding 1.2:
    operator set SCP_API_PROFILE=full, env file re-applied core) is observable.
    When SCP_ENV_FILE is absent, the repository .env is a compatibility
    fallback and does not override already-present parent variables.

    The same resolved file is applied at most once per process: the second
    import-time call (judge_llm) no longer re-clobbers variables written after
    the first load. SCP_ENV_FILE inside an env file is ignored with a one-time
    warning — the boundary is launcher-owned.
    """
    global _boundary_warning_emitted
    override = os.environ.get("SCP_ENV_FILE")
    if override:
        env_path = Path(override).expanduser()
        if not env_path.is_absolute():
            env_path = Path.cwd() / env_path
    else:
        # env_loader.py lives at <repo>/scp/security/ — parents[2] is the repo
        # root where the canonical .env lives. parents[1] pointed at the
        # package dir (scp/.env) which does not exist, silently loading nothing.
        env_path = Path(__file__).resolve().parents[2] / ".env"

    if not env_path.is_file():
        return

    resolved = str(env_path.resolve())
    if resolved in _applied_files:
        # Import-time second call (e.g. judge_llm): re-applying the same file
        # would clobber variables intentionally written after the first load.
        return
    _applied_files.add(resolved)

    for key, value in _iter_env_file_entries(env_path):
        if key in _BOUNDARY_KEYS:
            if not _boundary_warning_emitted:
                _boundary_warning_emitted = True
                logger.warning(
                    "env_loader: ignored key %s in env file %s — SCP_ENV_FILE "
                    "is launcher-owned and may not be redefined by the file "
                    "it selects",
                    key, env_path.name,
                )
            continue
        if override or key not in os.environ:
            if key in os.environ:
                # Fail-loud on the silent-override hazard: name the key, never
                # the value (secrecy invariant).
                logger.warning(
                    "env_loader: env file %s overrides pre-set process env "
                    "key %s (value not logged)",
                    env_path.name, key,
                )
            os.environ[key] = value


__all__ = ["load_selected_env"]
