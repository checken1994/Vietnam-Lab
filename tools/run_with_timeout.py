"""Deadline wrapper for SCP checker runs (fail-closed).

Usage:
    python tools/run_with_timeout.py <deadline_seconds> <command> [args...]

Runs the child command with a hard wall-clock deadline. On expiry the whole
child process tree is terminated and the wrapper exits 124 (GNU timeout
convention). Otherwise the wrapper exits with the child's exit code.

Exit codes:
    124  deadline exceeded (child killed)
    2    wrapper usage error (bad deadline, empty command)
    127  child could not be spawned (fail-closed: never exit 0 without a run)
    N    child exit code, propagated unchanged, otherwise

No output of the child is swallowed: stdout/stderr stream straight through.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys


def _kill_tree(proc: subprocess.Popen) -> None:
    """Terminate the child and, best effort, its whole process tree."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
            )
        else:
            # Child was started in its own process group (see Popen kwargs).
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            return
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: run_with_timeout.py <deadline_seconds> <command> [args...]", file=sys.stderr)
        return 2
    try:
        deadline = float(argv[0])
    except ValueError:
        print(f"run_with_timeout: invalid deadline {argv[0]!r}", file=sys.stderr)
        return 2
    if deadline <= 0:
        print("run_with_timeout: deadline must be > 0", file=sys.stderr)
        return 2
    cmd = argv[1:]
    popen_kwargs: dict = {}
    if os.name != "nt":
        popen_kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(cmd, **popen_kwargs)
    except OSError as exc:
        print(f"run_with_timeout: failed to spawn {cmd!r}: {exc}", file=sys.stderr)
        return 127
    try:
        returncode = proc.wait(timeout=deadline)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass
        print(
            f"run_with_timeout: TIMEOUT after {deadline:g}s — killed {' '.join(cmd)!r}",
            file=sys.stderr,
        )
        return 124
    return returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
