"""Reality test for the post-S26 4-b-002 invariant.

The original 4-b-002 test mirrored ``JudgeCoreMixin`` implementation details:
its structured ``ground_truth`` map and the deleted self-answer assignment.
S26 deliberately removed that dead judge_parts tree.  The current contract is
therefore a dead-code invariant plus a check that the live judge uses the
canonical independent verifier instead of the deleted mixin path.

This remains a reality test rather than a source-only claim:
- it resolves the deleted package through Python's import machinery;
- it imports the live ``RealityJudge`` class from the current runtime path;
- it executes the real empty-answer fail-closed path without a mock/provider.

DNA principles exercised:
  #2  (vòng lặp khép kín — test gọi code hiện hành)
  #19 (calibration — kiểm tra đúng observable canonical path)
  #22 (PASS ≠ TRUE — phạm vi chỉ là invariant này)
  #26 (reality test — runtime import và execution có quyền cuối)
"""
from __future__ import annotations

import importlib
import importlib.util
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = ROOT / "scp" / "runtime"
LEGACY_PACKAGE = RUNTIME_ROOT / "judge_parts"
LEGACY_MODULE_NAME = "scp.runtime.judge_parts.judgecore_mixin"
CANONICAL_MODULE_NAME = "scp.runtime.judge"
CANONICAL_JUDGE_FILE = RUNTIME_ROOT / "judge.py"

# Make direct invocation independent of the caller's current directory.
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# TEST 1 — S26 removed the dead JudgeCoreMixin tree.  Both the filesystem and
# Python import resolution must agree that the old path is absent.
# ---------------------------------------------------------------------------
assert not LEGACY_PACKAGE.exists(), (
    f"FAIL: deleted judge_parts package still exists: {LEGACY_PACKAGE}"
)
assert importlib.util.find_spec("scp.runtime.judge_parts") is None, (
    "FAIL: deleted judge_parts package is still import-resolvable"
)
assert LEGACY_MODULE_NAME not in sys.modules, (
    "FAIL: deleted JudgeCoreMixin module is already loaded"
)
legacy_import_failed = False
try:
    importlib.import_module(LEGACY_MODULE_NAME)
except (ImportError, ModuleNotFoundError):
    legacy_import_failed = True
assert legacy_import_failed, (
    "FAIL: deleted JudgeCoreMixin module can still be imported: "
    f"{LEGACY_MODULE_NAME}"
)
assert LEGACY_MODULE_NAME not in sys.modules, (
    "FAIL: deleted JudgeCoreMixin module entered sys.modules"
)
print("PASS [1/3]: S26 JudgeCoreMixin package and module are absent/unimportable")


# ---------------------------------------------------------------------------
# TEST 2 — The production judge resolves through the current canonical path,
# not through a compatibility alias or the deleted mixin.
# ---------------------------------------------------------------------------
canonical_judge = importlib.import_module(CANONICAL_MODULE_NAME)
assert Path(canonical_judge.__file__).resolve() == CANONICAL_JUDGE_FILE.resolve(), (
    "FAIL: RealityJudge module resolved outside canonical scp/runtime/judge.py: "
    f"{canonical_judge.__file__}"
)
assert canonical_judge.RealityJudge.__module__ == CANONICAL_MODULE_NAME, (
    "FAIL: RealityJudge public identity drifted from canonical runtime module"
)
source_file = inspect.getsourcefile(canonical_judge.RealityJudge)
assert source_file is not None and Path(source_file).resolve() == CANONICAL_JUDGE_FILE.resolve(), (
    "FAIL: RealityJudge source does not resolve to canonical judge.py: "
    f"{source_file}"
)
assert all(base.__name__ != "JudgeCoreMixin" for base in canonical_judge.RealityJudge.__mro__), (
    "FAIL: canonical RealityJudge still inherits deleted JudgeCoreMixin"
)
print("PASS [2/3]: RealityJudge resolves to canonical scp/runtime/judge.py without legacy mixin")


# ---------------------------------------------------------------------------
# TEST 3 — The live judge owns a real IndependentVerifier and fail-closes on an
# empty answer before any semantic/provider path.  This is the replacement for
# the deleted ground_truth self-verification implementation detail.
# ---------------------------------------------------------------------------
from scp.verifier import IndependentVerifier

judge = canonical_judge.RealityJudge()
assert type(judge.verifier) is IndependentVerifier, (
    "FAIL: canonical RealityJudge does not own IndependentVerifier"
)
verdict = judge.judge("What is the answer?", "")
assert verdict["verdict"] == "FAIL", (
    f"FAIL: canonical empty-answer path did not fail closed: {verdict}"
)
assert "REJECT_EMPTY" in verdict["failures"], (
    f"FAIL: canonical Tier-1 empty-answer rejection missing: {verdict}"
)
canonical_source = CANONICAL_JUDGE_FILE.read_text(encoding="utf-8")
assert "JudgeCoreMixin" not in canonical_source, (
    "FAIL: canonical judge.py still names deleted JudgeCoreMixin"
)
assert "ground_truth[_slm_name]" not in canonical_source, (
    "FAIL: deleted SLM self-ground-truth assignment returned to canonical judge.py"
)
print("PASS [3/3]: canonical IndependentVerifier path rejects empty answer before semantic judging")

print("\nReality test 4-b-002 PASSED (3/3 assertions)")
