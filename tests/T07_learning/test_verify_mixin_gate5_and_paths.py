"""Regression tests for verify_mixin gates 5 (pytest suite check) and path equality.

[VERIFY-GATE-5-FIX] The pytest "suite must not break" check used to be
indented INSIDE the ``else:`` (file-outside-repo) branch of _verify_fix, so
for in-repo files (the normal case) _test_targets was computed and never
used — the gate never spawned pytest.

[PATH-EQ-FIX] Re-scan / new-bug filters compared ``str(bug.file) ==
str(filepath)`` as RAW strings — a backslash-vs-slash mismatch (Windows
absolute path vs POSIX form) silently emptied the candidate set, making
both "original bug still present" and "no new bugs" checks vacuous.
Matching now normalizes both sides with Path(...).resolve(), mirroring
completeness_check._bug_matches.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from scp.autofix.classifier import BugReport
from scp.autofix.engine import AutoFixEngine
from scp.autofix.engine_parts.verify_mixin import _same_bug_file

# tests/T07_learning/ -> tests/ -> repo root (D:\scp). The previous two-level
# parent chain resolved REPO_ROOT to tests/ and the fixture tried to write
# tests/tests/_gate5_in_repo_target_mod.py → FileNotFoundError at setup.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
assert (REPO_ROOT / "scp" / "autofix").is_dir(), (
    f"REPO_ROOT resolved wrongly: {REPO_ROOT}"
)
IN_REPO_MODULE_NAME = "_gate5_in_repo_target_mod"


@pytest.fixture
def in_repo_target():
    """A real, parsing file INSIDE the repo checkout (created + cleaned up)."""
    target = REPO_ROOT / "tests" / f"{IN_REPO_MODULE_NAME}.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    yield target
    target.unlink(missing_ok=True)


def _concrete_proc(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class TestPytestGateRunsForInRepoFiles:
    def test_in_repo_file_triggers_pytest_gate_subprocess(self, tmp_path, in_repo_target, monkeypatch):
        engine = AutoFixEngine(data_dir=str(tmp_path / "data"))
        bug = BugReport(
            file=str(in_repo_target),
            line=1,
            bug_type="PossiblyUndefinedName",
            description="gate5 probe",
            suggested_fix="VALUE = 1",
            tier=2,
        )
        calls = []

        def fake_run(argv, **kwargs):
            calls.append([str(a) for a in argv])
            return _concrete_proc(returncode=0, stdout="1 passed in 0.01s")

        monkeypatch.setattr("subprocess.run", fake_run)
        is_ok, reason = engine._verify_fix(in_repo_target, [bug])

        pytest_calls = [argv for argv in calls if "pytest" in argv]
        assert pytest_calls, (
            "pytest gate never ran for an IN-REPO file — Check 5 is vacuous "
            f"(all subprocess calls: {calls})"
        )
        argv = pytest_calls[0]
        assert "-m" in argv and "pytest" in argv
        assert is_ok is True, reason

    def test_out_of_repo_file_still_triggers_pytest_gate_subprocess(self, tmp_path, monkeypatch):
        # S15 contract preserved: the fail-closed out-of-repo fallback keeps running.
        engine = AutoFixEngine(data_dir=str(tmp_path / "data"))
        target = tmp_path / "outside_repo_mod.py"
        target.write_text("VALUE = 1\n", encoding="utf-8")
        bug = BugReport(
            file=str(target),
            line=1,
            bug_type="PossiblyUndefinedName",
            description="gate5 probe",
            suggested_fix="VALUE = 1",
            tier=2,
        )
        calls = []

        def fake_run(argv, **kwargs):
            calls.append([str(a) for a in argv])
            return _concrete_proc(returncode=0, stdout="1 passed in 0.01s")

        monkeypatch.setattr("subprocess.run", fake_run)
        is_ok, reason = engine._verify_fix(target, [bug])

        assert [argv for argv in calls if "pytest" in argv], (
            "out-of-repo pytest fallback regressed"
        )
        assert is_ok is True, reason


class TestBugFilePathNormalization:
    def test_same_bug_file_normalizes_separator_variants(self, in_repo_target):
        native = str(in_repo_target.resolve())
        posix = in_repo_target.resolve().as_posix()
        assert _same_bug_file(posix, native) is True
        assert _same_bug_file(native, posix) is True
        assert _same_bug_file(native, native) is True
        other = str(in_repo_target.parent / "completely_other.py")
        assert _same_bug_file(other, native) is False

    def test_re_scan_detects_original_bug_across_separator_mismatch(
        self, tmp_path, in_repo_target, monkeypatch,
    ):
        """The re-scan gate must NOT be vacuous: an original bug still present
        in the patched file must REJECT the fix.

        [Re-pin 2026-10-25] Check 2 của VerifyMixin._verify_fix giờ re-scan
        TRỰC TIẾP target bằng _scan_file + _build_bug_report (từ
        scp.autofix.runner_phases.ast_scan) — không đi qua ast_scan_scp
        aggregate nữa, nên patch point cũ (scp.autofix.runner.ast_scan_scp)
        không còn effective ⇒ gate chết giả vờ ok=True. Patch điểm NGUỒN mà
        engine dùng thật; bug báo cáo vẫn giữ native-form path để pin rằng
        matching theo line+type không bị đá văng vì separator."""
        import scp.autofix.runner_phases.ast_scan as ast_scan_mod

        engine = AutoFixEngine(data_dir=str(tmp_path / "data"))
        bug = BugReport(
            file=in_repo_target.resolve().as_posix(),
            line=1,
            bug_type="BareExceptPass",
            description="stale bug probe",
            suggested_fix="pass",
            tier=2,
        )

        def fake_scan_file(filepath):
            # Scanner trả đúng 1 finding cho target — như thể bug gốc vẫn còn.
            return [{
                "bug_type": "BareExceptPass",
                "line": 1,
                "description": "stale bug probe",
                "suggested_fix": "pass",
            }]

        monkeypatch.setattr(ast_scan_mod, "_scan_file", fake_scan_file)
        is_ok, reason = engine._verify_fix(in_repo_target, [bug])

        assert is_ok is False, (
            "re-scan gate missed the original bug — Check 2 is vacuous"
        )
        assert "original bug still present" in reason
