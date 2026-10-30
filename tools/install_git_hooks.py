#!/usr/bin/env python3
"""SCP Guardrail — Install git hooks.

[M-12 fix 2026-10-01] The installer used to BLINDLY overwrite any existing
pre-commit hook (rename to pre-commit.backup + write the SCP hook). That
destroyed foreign hook content installed by other tooling. Fail-closed
behavior now:

- No existing pre-commit hook      -> install the SCP T00 tripwire hook.
- Existing hook IS the SCP hook    -> idempotent reinstall (same content,
  no backup churn, exit 0).
- Existing foreign hook            -> DO NOT touch it. Fail loudly (exit 1)
  with merge instructions so a human composes the chain.

[WHY-CHAIN 2026-10-30] Two hooks are managed with the same policy:
  pre-commit  -> tools/t00_meta_audit.py (which also runs the WHY-CLAIMS check)
  commit-msg  -> tools/check_why_chain.py --commit-msg "$1" (a commit without a
                 "Chuỗi Tại Sao" is rejected; pre-commit cannot see the message).
The hooks directory is resolved with ``git rev-parse --git-path hooks`` so linked
worktrees (where ``.git`` is a file) and core.hooksPath work.
"""
import os
import stat
import sys
from pathlib import Path

import subprocess

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def resolve_hooks_dir(root: Path = PROJECT_ROOT) -> Path:
    """Hooks dir via git (worktree + core.hooksPath aware); legacy path as fallback."""
    try:
        res = subprocess.run(["git", "rev-parse", "--git-path", "hooks"], cwd=root,
                             capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = res.stdout.strip()
        if res.returncode == 0 and out:
            path = Path(out)
            return path if path.is_absolute() else root / path
    except OSError:
        pass
    return root / ".git" / "hooks"


HOOKS_DIR = resolve_hooks_dir()

SCP_HOOK_MARKER = "SCP T00 Meta-Audit Tripwire (L2)"
SCP_COMMIT_MSG_MARKER = "SCP WHY-Chain commit-msg gate (L2)"

# Interpreter resolution shared by both hooks. SCP_PYTHON overrides; then the main
# checkout's venv (linked worktrees have no .venv of their own); then PATH names.
_PY_RESOLVER = """\
MAIN_ROOT=""
_COMMON="$(git rev-parse --git-common-dir 2>/dev/null)"
if [ -n "$_COMMON" ]; then MAIN_ROOT="$(cd "$_COMMON/.." 2>/dev/null && pwd)"; fi
if [ -n "$SCP_PYTHON" ]; then
    PYTHON_BIN="$SCP_PYTHON"
elif [ -n "$MAIN_ROOT" ] && [ -f "$MAIN_ROOT/.venv/Scripts/python.exe" ]; then
    PYTHON_BIN="$MAIN_ROOT/.venv/Scripts/python.exe"
elif [ -n "$MAIN_ROOT" ] && [ -f "$MAIN_ROOT/.venv/bin/python" ]; then
    PYTHON_BIN="$MAIN_ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v py >/dev/null 2>&1; then
    PYTHON_BIN="py"
else
    PYTHON_BIN="python"
fi
export PYTHONUTF8=1
"""

PRE_COMMIT_HOOK = f"""\
#!/bin/sh
# {SCP_HOOK_MARKER}

# [harness note 2026-10-01] git exports GIT_DIR/GIT_INDEX_FILE/GIT_WORK_TREE to
# hook processes; T00's run_git_cmd() scrubs those before any child git call
# (HARNESS-FIX for pre-commit worktree ENOENT), so this hook stays a plain
# pass-through — no env surgery here that could shadow T00's own scrubbing.

{_PY_RESOLVER}
echo "[SCP] Running T00 Meta-Audit..."
"$PYTHON_BIN" tools/t00_meta_audit.py
if [ $? -ne 0 ]; then
    echo "COMMIT BLOCKED by T00 Test-Integrity Authority."
    exit 1
fi
exit 0
"""

# commit-msg: the checker is taken from the commit's own tree; a linked worktree that
# predates the tool falls back to the main checkout's copy. No checker anywhere = BLOCK
# (fail-closed), never a silent pass.
COMMIT_MSG_HOOK = f"""#!/bin/sh
# {SCP_COMMIT_MSG_MARKER}
# Every commit must carry a "Chuỗi Tại Sao" (see docs/governance/WHY_CHAIN.md).
{_PY_RESOLVER}
CHECKER="tools/check_why_chain.py"
if [ ! -f "$CHECKER" ] && [ -n "$MAIN_ROOT" ] && [ -f "$MAIN_ROOT/tools/check_why_chain.py" ]; then
    CHECKER="$MAIN_ROOT/tools/check_why_chain.py"
fi
if [ ! -f "$CHECKER" ]; then
    echo "COMMIT BLOCKED: tools/check_why_chain.py not found (fail-closed WHY-Chain gate)."
    exit 1
fi
"$PYTHON_BIN" "$CHECKER" --commit-msg "$1"
if [ $? -ne 0 ]; then
    echo "COMMIT BLOCKED by WHY-Chain gate: add a valid 'Chuỗi Tại Sao' to the commit message."
    exit 1
fi
exit 0
"""

HOOKS = (
    ("pre-commit", SCP_HOOK_MARKER, PRE_COMMIT_HOOK, "L2 Tripwire"),
    ("commit-msg", SCP_COMMIT_MSG_MARKER, COMMIT_MSG_HOOK, "WHY-Chain gate"),
)


def install_one(hooks_dir: Path, name: str, marker: str, body: str, label: str) -> int:
    hook_path = hooks_dir / name
    if hook_path.exists():
        try:
            existing = hook_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"Error: cannot read existing {name} hook ({exc}); refusing to overwrite.")
            return 1
        if marker in existing:
            # Idempotent reinstall of our own hook - content identical.
            hook_path.write_text(body, encoding="utf-8", newline="\n")
            print(f"{name} hook already the SCP one; reinstalled idempotently. {label} active.")
            return 0
        print(
            f"Error: an existing NON-SCP {name} hook was found at "
            f"{hook_path}. It was NOT overwritten (fail-closed, audit M-12).\n"
            f"To compose a chain yourself, edit .git/hooks/{name} so it calls both, e.g.:\n"
            "\n"
            "    #!/bin/sh\n"
            f"    <the SCP {name} command> || exit 1\n"
            f"    exec /path/to/your/existing-hook \"$@\"\n"
            "\n"
            "Then re-run this installer; it will recognize only the SCP marker hook."
        )
        return 1

    hook_path.write_text(body, encoding="utf-8", newline="\n")
    if os.name != "nt":
        hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC)

    print(f"{name} hook installed. {label} active.")
    return 0


def main() -> int:
    hooks_dir = resolve_hooks_dir()
    if not hooks_dir.exists():
        print(f"Error: Git hooks dir not found at {hooks_dir}")
        return 1
    rc = 0
    for name, marker, body, label in HOOKS:
        rc |= install_one(hooks_dir, name, marker, body, label)
    return rc


if __name__ == "__main__":
    sys.exit(main())
