"""Test Failure Ingestor for SCP AutoFix.

Extracts failing assertions and stack traces from pytest output files
(e.g., reports/pytest_output.txt or pytest JSON logs) into BugReport objects.

Invariants:
  - FA-01: Disallows loosening assertions; aims to fix the product or harness properly.
  - FA-04 Zero-Mock: Pure empirical test output parsing.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from scp.autofix.classifier import BugReport, BugTier

logger = logging.getLogger("scp.autofix.test_failure_ingestor")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TEST_REPORT = PROJECT_ROOT / "reports" / "pytest_output.txt"

# Pytest failure block header: ________________________ test_foo ________________________
_FAIL_HEADER = re.compile(r"^_{5,}\s*(?P<test_name>[^_\n].*?[^_\n])\s*_{5,}$")
# Location: filepath:line: in test_foo
_LOC_PATTERN = re.compile(r"^(?P<file>[^:]+\.py):(?P<line>\d+):\s*(?:in\s+(?P<func>\w+))?")
# Assertion: E   assert ...
_ASSERT_PATTERN = re.compile(r"^E\s+(?P<msg>.*)$")


class TestFailureIngestor:
    """Parses pytest test failures into structured BugReports."""

    __test__ = False

    def __init__(self, report_path: Path | str | None = None) -> None:
        self.report_path = Path(report_path) if report_path else DEFAULT_TEST_REPORT

    def parse_report_content(self, text: str) -> list[BugReport]:
        """Parse raw pytest stdout text into a list of BugReports."""
        bugs: list[BugReport] = []
        lines = text.splitlines()
        i = 0
        n = len(lines)

        while i < n:
            m_head = _FAIL_HEADER.match(lines[i].strip())
            if m_head:
                test_name = m_head.group("test_name")
                fpath = ""
                fline = 1
                assert_msgs = []
                i += 1
                while i < n:
                    if _FAIL_HEADER.match(lines[i].strip()) or lines[i].startswith("=== FAILURES ==="):
                        break
                    m_loc = _LOC_PATTERN.match(lines[i].strip())
                    if m_loc:
                        fpath = m_loc.group("file")
                        fline = int(m_loc.group("line"))
                    m_assert = _ASSERT_PATTERN.match(lines[i].strip())
                    if m_assert:
                        assert_msgs.append(m_assert.group("msg"))
                    i += 1

                if fpath and assert_msgs:
                    msg = " | ".join(assert_msgs[:2])
                    # Normalize path
                    try:
                        p = Path(fpath)
                        if p.is_absolute() and p.is_relative_to(PROJECT_ROOT):
                            fpath = p.relative_to(PROJECT_ROOT).as_posix()
                    except (ValueError, OSError):
                        logger.debug("Path normalization fallback for %s", fpath)

                    bugs.append(
                        BugReport(
                            file=fpath,
                            line=fline,
                            bug_type="Test_Failure",
                            description=f"Test failure in {test_name}: {msg[:200]}",
                            suggested_fix=f"# Address failing assertion: {msg[:100]}",
                            tier=BugTier.TIER_1_AUTO_FIX,
                            is_restraint=False,
                            is_reversible=True,
                            affects_logic=True,
                        )
                    )
                continue
            i += 1

        return bugs

    def collect_bugs(self, max_bugs: int = 20) -> list[BugReport]:
        """Read and parse pytest failures from configured report path."""
        if not self.report_path.exists():
            return []
        try:
            content = self.report_path.read_text(encoding="utf-8", errors="replace")
            bugs = self.parse_report_content(content)
            return bugs[:max_bugs]
        except Exception as e:
            logger.error("[TestFailureIngestor] Error reading report %s: %s", self.report_path, e, exc_info=True)
            return []
