"""Runtime Log Ingestor for SCP AutoFix.

Monitors service error logs (backend-error.log, container stderr) in real time,
parses Python tracebacks into BugReport objects, and feeds them into the AutoFix
pipeline.

Invariants:
  - FA-04 Zero-Mock: Pure empirical log parsing from real files.
  - Auto-cleanup: Temporary/simulated log files are automatically wiped on completion.
  - Fail-Closed: Corrupted log files or partial writes never crash the system.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from scp.autofix.classifier import BugReport, BugTier

logger = logging.getLogger("scp.autofix.runtime_log_ingestor")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LOG_PATH = PROJECT_ROOT / "data" / "service-logs" / "backend-error.log"

# Traceback frame pattern: File "...", line 123, in foo
_FRAME_PATTERN = re.compile(
    r'File\s+["\'](?P<file>[^"\']+)["\'],\s+line\s+(?P<line>\d+)(?:,\s+in\s+(?P<func>\w+))?'
)
# Exception line pattern: TypeError: message
_EXC_PATTERN = re.compile(
    r"^(?P<exc_type>[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Warning|Fault)):\s*(?P<msg>.*)$"
)


class RuntimeLogIngestor:
    """Ingests runtime Python crashes and tracebacks from physical logs."""

    def __init__(self, log_path: Path | str | None = None) -> None:
        self.log_path = Path(log_path) if log_path else DEFAULT_LOG_PATH
        self._last_offset = 0
        self._temp_files_to_clean: list[Path] = []

    def register_temp_file(self, file_path: Path | str) -> None:
        """Register a temporary test/simulation log for guaranteed auto-cleanup."""
        p = Path(file_path)
        if p not in self._temp_files_to_clean:
            self._temp_files_to_clean.append(p)

    def cleanup_temporary_logs(self) -> int:
        """Wipe all registered temporary simulation logs (Rule #4 & User Directive #3)."""
        cleaned = 0
        for p in self._temp_files_to_clean:
            try:
                if p.exists():
                    p.unlink()
                    cleaned += 1
            except OSError as e:
                logger.debug("[RuntimeLogIngestor] Failed to remove temp log %s: %s", p, e)
        self._temp_files_to_clean.clear()
        return cleaned

    def parse_log_content(self, text: str) -> list[BugReport]:
        """Parse raw log text containing one or more Python tracebacks into BugReports."""
        bugs: list[BugReport] = []
        lines = text.splitlines()
        i = 0
        n = len(lines)

        while i < n:
            line = lines[i].strip()
            if "Traceback (most recent call last):" in line:
                frames: list[tuple[str, int, str]] = []
                i += 1
                while i < n:
                    frame_line = lines[i].strip()
                    m_frame = _FRAME_PATTERN.search(frame_line)
                    if m_frame:
                        fpath = m_frame.group("file")
                        fline = int(m_frame.group("line"))
                        ffunc = m_frame.group("func") or ""
                        frames.append((fpath, fline, ffunc))
                        i += 1
                        continue

                    m_exc = _EXC_PATTERN.match(frame_line)
                    if m_exc:
                        exc_type = m_exc.group("exc_type")
                        exc_msg = m_exc.group("msg").strip()
                        # Resolve target file: prefer the deepest frame inside project
                        target_frame = None
                        for frame_file, frame_line, frame_func in reversed(frames):
                            # Ignore virtualenv or standard library frames
                            if "site-packages" not in frame_file and "python3" not in frame_file.lower() and "lib" not in frame_file.lower():
                                target_frame = (frame_file, frame_line, frame_func)
                                break
                        if not target_frame and frames:
                            target_frame = frames[-1]

                        if target_frame:
                            tf_file, tf_line, tf_func = target_frame
                            try:
                                # Normalize path relative to project if possible
                                p = Path(tf_file)
                                if p.is_absolute() and p.is_relative_to(PROJECT_ROOT):
                                    tf_file = p.relative_to(PROJECT_ROOT).as_posix()
                            except (ValueError, OSError):
                                logger.debug("Path normalization fallback for %s", tf_file)

                            bugs.append(
                                BugReport(
                                    file=tf_file,
                                    line=tf_line,
                                    bug_type=f"Runtime_{exc_type}",
                                    description=f"Unhandled {exc_type} in {tf_func or 'module'}: {exc_msg}",
                                    suggested_fix=f"# Guard against {exc_type}: verify inputs/nullability",
                                    tier=BugTier.TIER_1_AUTO_FIX,
                                    is_restraint=False,
                                    is_reversible=True,
                                    affects_logic=True,
                                )
                            )
                        break
                    i += 1
            i += 1

        return bugs

    def collect_bugs(self, max_bugs: int = 20, since_last: bool = False) -> list[BugReport]:
        """Read and parse tracebacks from the configured log file."""
        if not self.log_path.exists():
            return []

        try:
            with open(self.log_path, encoding="utf-8", errors="replace") as f:
                if since_last and self._last_offset > 0:
                    f.seek(self._last_offset)
                content = f.read()
                self._last_offset = f.tell()

            bugs = self.parse_log_content(content)
            return bugs[:max_bugs]
        except Exception as e:
            logger.error("[RuntimeLogIngestor] Error reading log file %s: %s", self.log_path, e, exc_info=True)
            return []

    def __enter__(self) -> RuntimeLogIngestor:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.cleanup_temporary_logs()
