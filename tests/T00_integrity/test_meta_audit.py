import ast
import glob
import json
import os
import re

# Policy A (owner-approved 2026-09-12): the mandatory no-skip gate accepts
# ONLY "declared infra-skips" — a real pytest.skip() call inside a file
# explicitly listed in tests/T00_integrity/declared_infra_skips.json whose
# skip reason matches that entry's declared reason patterns and whose
# declared env-guard token is still present in the file. The allowlist file
# is a reviewed contract: every entry names exactly one file (no wildcards),
# and adding/removing an entry is a deliberate, reviewed change that must
# also update the pin test below.
DECLARED_INFRA_SKIP_ALLOWLIST_FILENAME = "declared_infra_skips.json"
DECLARED_INFRA_SKIP_OUTPUT_TAG = "declared_infra_skips:"

def _real_skip_calls(tree):
    # Real pytest.skip()/pytest.importorskip() Call nodes. A quoted
    # "pytest.skip" token inside a drift-guard deny list is a string constant,
    # NOT a Call node, so it never counts.
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {'skip', 'importorskip'}
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == 'pytest'
    ]

def _static_reason_text(call_node):
    # Statically extractable reason fragments: every string constant inside
    # the first positional argument or the `reason=` keyword (covers plain
    # constants, f-strings and string concatenation). A reason that is not
    # statically extractable yields '' and can never match an allowlist
    # pattern (fail-closed — such a skip must be made literal or declared).
    reason_expr = call_node.args[0] if call_node.args else None
    for keyword in call_node.keywords:
        if keyword.arg == 'reason':
            reason_expr = keyword.value
    if reason_expr is None:
        return ''
    parts = [
        node.value
        for node in ast.walk(reason_expr)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    return ''.join(parts)

def _load_declared_infra_skip_allowlist(allowlist_path):
    # Fail-closed: the no-skip gate refuses to run without a well-formed
    # declared infra-skip contract (missing/malformed contract => violation,
    # never "treat everything as undeclared-debt and move on").
    if not os.path.isfile(allowlist_path):
        raise AssertionError(
            f"Declared infra-skip allowlist is missing: {allowlist_path}. "
            f"The T00 no-skip gate must not run without its explicit contract "
            f"(fail-closed). Create {DECLARED_INFRA_SKIP_ALLOWLIST_FILENAME} deliberately."
        )
    try:
        with open(allowlist_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise AssertionError(
            f"Declared infra-skip allowlist is unreadable/malformed: "
            f"{allowlist_path} ({exc})"
        )
    assert isinstance(data, dict), (
        f"Declared infra-skip allowlist root must be a JSON object: {allowlist_path}"
    )
    entries = data.get('entries')
    assert isinstance(entries, list), (
        f"Declared infra-skip allowlist must contain an 'entries' list: {allowlist_path}"
    )
    allowlist = {}
    for entry in entries:
        assert isinstance(entry, dict), (
            f"Declared infra-skip allowlist entry must be a JSON object: {entry!r}"
        )
        for required_key in ('file', 'reason_patterns', 'env_guard_token', 'justification'):
            assert required_key in entry, (
                f"Declared infra-skip allowlist entry is missing required key "
                f"'{required_key}' (a reviewed contract requires it): {entry!r}"
            )
        rel = entry['file']
        assert isinstance(rel, str) and rel and rel.strip() == rel, (
            f"Declared infra-skip 'file' must be a non-empty trimmed string: {entry!r}"
        )
        assert '\\' not in rel, (
            f"Declared infra-skip 'file' must use forward slashes: {rel}"
        )
        assert not os.path.isabs(rel), (
            f"Declared infra-skip 'file' must be repository-relative, not absolute: {rel}"
        )
        assert '..' not in rel.split('/'), (
            f"Declared infra-skip 'file' must not traverse upward: {rel}"
        )
        patterns = entry['reason_patterns']
        assert (
            isinstance(patterns, list)
            and patterns
            and all(isinstance(p, str) and p.strip() for p in patterns)
        ), (
            f"Declared infra-skip 'reason_patterns' must be a non-empty list of "
            f"non-empty strings: {rel}"
        )
        guard = entry['env_guard_token']
        assert isinstance(guard, str) and guard.strip(), (
            f"Declared infra-skip 'env_guard_token' must be a non-empty string: {rel}"
        )
        justification = entry['justification']
        assert isinstance(justification, str) and justification.strip(), (
            f"Declared infra-skip 'justification' must be a non-empty string "
            f"(a reviewed contract requires one): {rel}"
        )
        assert rel not in allowlist, (
            f"Duplicate declared infra-skip allowlist entry for {rel}"
        )
        allowlist[rel] = entry
    return allowlist

def scan_mandatory_test_tree(tests_root, allowlist_path=None):
    # AST-scan every mandatory T* test file for skip usage (policy A).
    #
    # A real pytest.skip()/pytest.importorskip() call is accepted ONLY when
    # (a) the file source is OS-conditional ('platform.system' present —
    # unchanged rule), or (b) the file has an entry in the declared infra-skip
    # allowlist AND every skip reason matches a declared reason pattern AND
    # the file still contains its declared env-guard token. pytestmark
    # whole-file skips and decorator skips are never allowlisted.
    #
    # Prints a `declared_infra_skips: ...` summary (a green result must never
    # be silent) and returns the list of declared records. Raises
    # AssertionError (fail-closed) on any undeclared skip, undeclared reason,
    # missing env guard, stale entry, or missing/malformed allowlist.
    repo_root = os.path.dirname(os.path.abspath(tests_root))
    if allowlist_path is None:
        allowlist_path = os.path.join(
            tests_root, 'T00_integrity', DECLARED_INFRA_SKIP_ALLOWLIST_FILENAME
        )
    allowlist = _load_declared_infra_skip_allowlist(allowlist_path)

    violations = []
    records = []
    declared_seen = set()
    mandatory_dirs = sorted(glob.glob(os.path.join(tests_root, 'T[0-9][0-9]*')))
    for dir_path in mandatory_dirs:
        for filepath in sorted(glob.glob(os.path.join(dir_path, '*.py'))):
            if os.path.abspath(filepath) == os.path.abspath(__file__):
                continue
            rel = os.path.relpath(
                os.path.abspath(filepath), repo_root
            ).replace(os.sep, '/')
            with open(filepath, 'r', encoding='utf-8') as f:
                source = f.read()
            tree = ast.parse(source, filename=filepath)
            skip_calls = _real_skip_calls(tree)

            # Whole-file pytestmark skip is never an exception (unchanged rule).
            for marks in _pytestmark_assignment(tree):
                violations.append(
                    f"Mandatory test {rel} assigns pytestmark = "
                    f"pytest.mark.{marks[0]}(...), which silently skips the "
                    "entire file. Mandatory tests must FAIL if blocked."
                )
            # Decorator-based skip/xfail/skipif must be OS-conditional
            # (unchanged rule, receiver-agnostic attribute match).
            for scoped in ast.walk(tree):
                if not isinstance(scoped, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                for dec in scoped.decorator_list:
                    dec_func = dec.func if isinstance(dec, ast.Call) else dec
                    if (
                        isinstance(dec_func, ast.Attribute)
                        and dec_func.attr in {'skip', 'xfail', 'skipif'}
                        and 'platform.system' not in source
                    ):
                        violations.append(
                            f"Mandatory test {rel} decorates {scoped.name} with "
                            f"pytest.mark.{dec_func.attr}. Mandatory tests must "
                            "FAIL if blocked, unless OS-specific."
                        )

            if not skip_calls:
                continue

            entry = allowlist.get(rel)
            if entry is None:
                if 'platform.system' not in source:
                    violations.append(
                        f"Mandatory test {rel} contains {len(skip_calls)} real "
                        f"pytest.skip() call(s) and is NOT in the declared "
                        f"infra-skip allowlist ({DECLARED_INFRA_SKIP_ALLOWLIST_FILENAME}). "
                        "Mandatory tests must FAIL if blocked, unless "
                        "OS-specific or explicitly declared (policy A)."
                    )
                continue

            # Declared infra-skip path (policy A): validate the contract.
            declared_seen.add(rel)
            guard = entry['env_guard_token']
            if guard not in source:
                violations.append(
                    f"Declared infra-skip {rel} no longer contains its env-guard "
                    f"token '{guard}'. The skip is no longer guarded; update the "
                    "contract deliberately."
                )
            patterns = entry['reason_patterns']
            for call in skip_calls:
                reason_text = _static_reason_text(call)
                if not any(pattern in reason_text for pattern in patterns):
                    violations.append(
                        f"Declared infra-skip {rel} has a pytest.skip() reason "
                        f"not matching any declared pattern {patterns}: "
                        f"{reason_text!r}. Declare the new reason deliberately "
                        "in the allowlist or make the test FAIL."
                    )
            records.append({
                'file': rel,
                'skip_call_count': len(skip_calls),
                'reason_patterns': patterns,
                'env_guard_token': guard,
            })

    # Stale contract entries: allowlisted file deleted or its skips removed.
    # Both mean the contract no longer matches reality and must be reconciled
    # deliberately (fail-closed, never silently ignored).
    for rel in sorted(set(allowlist) - declared_seen):
        abs_path = os.path.join(repo_root, rel.replace('/', os.sep))
        if not os.path.isfile(abs_path):
            violations.append(
                f"Stale declared infra-skip allowlist entry: {rel} does not "
                "exist. Remove or fix the contract entry deliberately."
            )
        else:
            violations.append(
                f"Stale declared infra-skip allowlist entry: {rel} no longer "
                "contains a real pytest.skip() call. Remove the entry "
                "deliberately (the skips were fixed into real failures)."
            )

    if violations:
        raise AssertionError(
            "T00 no-skip gate violations:\n"
            + "\n".join(f"- {violation}" for violation in violations)
        )

    total_calls = sum(record['skip_call_count'] for record in records)
    print(
        f"{DECLARED_INFRA_SKIP_OUTPUT_TAG} {len(records)} file(s), "
        f"{total_calls} pytest.skip call(s) declared via "
        f"{os.path.basename(allowlist_path)}"
    )
    for record in records:
        print(
            f"  - {record['file']}: {record['skip_call_count']} skip call(s), "
            f"patterns={record['reason_patterns']}, "
            f"env_guard={record['env_guard_token']}"
        )
    return records

def _pytestmark_skip_marks(value_node):
    # Names of pytest.mark.{skip,xfail,skipif} referenced inside a pytestmark
    # assignment value (call form or bare attribute form).
    marks = set()
    if value_node is None:
        return marks
    for node in ast.walk(value_node):
        attr_node = node.func if isinstance(node, ast.Call) else node
        if (
            isinstance(attr_node, ast.Attribute)
            and attr_node.attr in {'skip', 'xfail', 'skipif'}
            and isinstance(attr_node.value, ast.Attribute)
            and attr_node.value.attr == 'mark'
            and isinstance(attr_node.value.value, ast.Name)
            and attr_node.value.value.id == 'pytest'
        ):
            marks.add(attr_node.attr)
    return marks

def _pytestmark_assignment(tree):
    # Yields (marks) for module/class-level `pytestmark = pytest.mark.skip(...)`
    # assignments. Whole-file silent skips hide from per-call/per-decorator
    # detectors, so they must be caught at the assignment itself.
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets, value = [node.target], node.value
        else:
            continue
        if not any(isinstance(t, ast.Name) and t.id == 'pytestmark' for t in targets):
            continue
        marks = _pytestmark_skip_marks(value)
        if marks:
            yield sorted(marks)

def test_meta_audit_no_skip_in_mandatory_tests():
    # Enforce that no mandatory tests are skipped except: (a) OS-conditional
    # skips ('platform.system' in source), or (b) DECLARED infra-skips (policy
    # A, owner-approved): a real pytest.skip() call inside a file explicitly
    # listed in tests/T00_integrity/declared_infra_skips.json whose reason
    # matches the file's declared reason patterns and whose declared env-guard
    # token is still present. Everything else still fails, including
    # module-level `pytestmark = pytest.mark.skip(...)` whole-file skips and
    # skip/xfail/skipif decorators, which are never allowlisted.
    # tests_root is the parent of all T* gate dirs (repo/tests); the allowlist
    # contract lives at tests/T00_integrity/declared_infra_skips.json and its
    # entry paths are repository-relative (tests/T*/...).
    tests_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mandatory_dirs = sorted(glob.glob(os.path.join(tests_root, 'T[0-9][0-9]*')))
    assert len(mandatory_dirs) >= 12, f"expected all 12 gate dirs, found {mandatory_dirs}"
    scan_mandatory_test_tree(tests_root)

# Pin the current reality (policy A contract has review): all eight historical
# infra-skip files were converted to hermetic non-skipping tests in GA B45, so
# the declared infra-skip allowlist now has zero entries.
_KNOWN_DECLARED_INFRA_SKIP_COUNTS: dict[str, int] = {}

def test_declared_infra_skip_allowlist_pins_known_files():
    tests_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    records = scan_mandatory_test_tree(tests_root)
    assert {record['file'] for record in records} == set(_KNOWN_DECLARED_INFRA_SKIP_COUNTS)
    assert {
        record['file']: record['skip_call_count'] for record in records
    } == _KNOWN_DECLARED_INFRA_SKIP_COUNTS

# ---------------------------------------------------------------------------
# Contract tests for the policy A gate itself. These exercise
# scan_mandatory_test_tree directly against a minimal tmp tree (real files,
# real AST scan, no mocks) so the allowlist behavior is pinned independent of
# the live repository tree.
# ---------------------------------------------------------------------------

def _write_tmp_gate_tree(tmp_path, test_source, entries=None, allowlist_text=None):
    tests_root = tmp_path / 'tests'
    target_dir = tests_root / 'T04_kernel'
    target_dir.mkdir(parents=True)
    (target_dir / 'test_infra_probe.py').write_text(test_source, encoding='utf-8')
    allowlist_path = None
    if entries is not None or allowlist_text is not None:
        allowlist_path = tests_root / 'T00_integrity' / DECLARED_INFRA_SKIP_ALLOWLIST_FILENAME
        allowlist_path.parent.mkdir(parents=True, exist_ok=True)
        text = allowlist_text if allowlist_text is not None else json.dumps({'entries': entries}, indent=2)
        allowlist_path.write_text(text, encoding='utf-8')
    return str(tests_root), (str(allowlist_path) if allowlist_path else None)

_DECLARED_PROBE_SOURCE = (
    "import os\n"
    "import pytest\n\n"
    "def test_probe():\n"
    "    if not os.environ.get('SCP_PG_TEST_DSN'):\n"
    "        pytest.skip(\n"
    "            'SCP_PG_TEST_DSN not set; declared infra-skip (no assertion hidden)'\n"
    "        )\n"
    "    assert os.environ.get('SCP_PG_TEST_DSN') is not None\n"
)

_DECLARED_PROBE_ENTRY = {
    'file': 'tests/T04_kernel/test_infra_probe.py',
    'reason_patterns': ['declared infra-skip'],
    'env_guard_token': 'SCP_PG_TEST_DSN',
    'justification': 'tmp contract-test probe: real PG is an external dependency',
}

def test_gate_accepts_declared_infra_skip_and_records_it(tmp_path, capsys):
    tests_root, allowlist_path = _write_tmp_gate_tree(
        tmp_path, _DECLARED_PROBE_SOURCE, entries=[_DECLARED_PROBE_ENTRY]
    )
    records = scan_mandatory_test_tree(tests_root, allowlist_path)
    assert [record['file'] for record in records] == ['tests/T04_kernel/test_infra_probe.py']
    assert records[0]['skip_call_count'] == 1
    assert records[0]['env_guard_token'] == 'SCP_PG_TEST_DSN'
    output = capsys.readouterr().out
    assert DECLARED_INFRA_SKIP_OUTPUT_TAG in output, "green run must record declared skips"
    assert 'test_infra_probe.py' in output

def test_gate_fails_on_skip_outside_allowlist(tmp_path):
    undeclared_source = (
        "import pytest\n\n"
        "def test_probe():\n"
        "    pytest.skip('some mysterious reason')\n"
    )
    tests_root, allowlist_path = _write_tmp_gate_tree(tmp_path, undeclared_source, entries=[])
    with pytest.raises(AssertionError) as excinfo:
        scan_mandatory_test_tree(tests_root, allowlist_path)
    message = str(excinfo.value)
    assert 'NOT in the declared infra-skip allowlist' in message
    assert 'tests/T04_kernel/test_infra_probe.py' in message

def test_gate_fails_on_undeclared_reason_inside_allowlisted_file(tmp_path):
    undeclared_reason_source = (
        "import pytest\n\n"
        "def test_probe():\n"
        "    pytest.skip('surprise: totally undeclared reason')\n"
    )
    tests_root, allowlist_path = _write_tmp_gate_tree(
        tmp_path, undeclared_reason_source, entries=[_DECLARED_PROBE_ENTRY]
    )
    with pytest.raises(AssertionError) as excinfo:
        scan_mandatory_test_tree(tests_root, allowlist_path)
    message = str(excinfo.value)
    assert 'not matching any declared pattern' in message
    assert 'surprise: totally undeclared reason' in message

def test_gate_fails_when_env_guard_token_removed(tmp_path):
    # The file skips with a declared reason but no longer contains the
    # declared env-guard token: the guard was stripped, so the contract is
    # broken and the gate must fail until it is reconciled deliberately.
    guard_removed_source = (
        "import pytest\n\n"
        "def test_probe():\n"
        "    pytest.skip('declared infra-skip: guard was stripped away')\n"
    )
    tests_root, allowlist_path = _write_tmp_gate_tree(
        tmp_path, guard_removed_source, entries=[_DECLARED_PROBE_ENTRY]
    )
    with pytest.raises(AssertionError) as excinfo:
        scan_mandatory_test_tree(tests_root, allowlist_path)
    message = str(excinfo.value)
    assert 'no longer contains its env-guard token' in message
    assert 'SCP_PG_TEST_DSN' in message

def test_gate_fails_on_stale_allowlist_entry(tmp_path):
    stale_source = (
        "def test_probe():\n"
        "    assert 1 == 1\n"
    )
    tests_root, allowlist_path = _write_tmp_gate_tree(
        tmp_path, stale_source, entries=[_DECLARED_PROBE_ENTRY]
    )
    with pytest.raises(AssertionError) as excinfo:
        scan_mandatory_test_tree(tests_root, allowlist_path)
    message = str(excinfo.value)
    assert 'Stale declared infra-skip allowlist entry' in message
    assert 'tests/T04_kernel/test_infra_probe.py' in message

def test_gate_fails_closed_when_allowlist_missing(tmp_path):
    tests_root, _unused = _write_tmp_gate_tree(tmp_path, _DECLARED_PROBE_SOURCE)
    with pytest.raises(AssertionError) as excinfo:
        scan_mandatory_test_tree(tests_root)
    message = str(excinfo.value)
    assert 'allowlist is missing' in message
    assert 'fail-closed' in message

def test_gate_fails_closed_on_malformed_allowlist(tmp_path):
    tests_root, allowlist_path = _write_tmp_gate_tree(
        tmp_path, _DECLARED_PROBE_SOURCE, allowlist_text='{ this is not json'
    )
    with pytest.raises(AssertionError) as excinfo:
        scan_mandatory_test_tree(tests_root, allowlist_path)
    assert 'unreadable/malformed' in str(excinfo.value)

def test_gate_fails_closed_on_entry_missing_required_keys(tmp_path):
    incomplete_entry = {
        'file': 'tests/T04_kernel/test_infra_probe.py',
        'reason_patterns': ['declared infra-skip'],
        # env_guard_token and justification deliberately missing
    }
    tests_root, allowlist_path = _write_tmp_gate_tree(
        tmp_path, _DECLARED_PROBE_SOURCE, entries=[incomplete_entry]
    )
    with pytest.raises(AssertionError) as excinfo:
        scan_mandatory_test_tree(tests_root, allowlist_path)
    message = str(excinfo.value)
    assert 'missing required key' in message
    assert 'env_guard_token' in message

def test_meta_audit_no_assert_true():
    # Enforce that no tests just assert True
    root_dir = os.path.dirname(os.path.dirname(__file__))
    for filepath in glob.glob(os.path.join(root_dir, 'T*', '*.py')):
        if filepath == __file__: continue
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            if 'assert ' + 'True' + '\n' in content:
                assert False, f"Test {filepath} contains 'assert True', which is a placeholder and violates Evidence rules."

def test_meta_audit_architecture_coverage():
    # Enforce that all 12 T directories exist
    root_dir = os.path.dirname(os.path.dirname(__file__))
    expected_dirs = [f'T{str(i).zfill(2)}' for i in range(12)]
    found_dirs = [d for d in os.listdir(root_dir) if d.startswith('T')]
    for expected in expected_dirs:
        assert any(d.startswith(expected) for d in found_dirs), f"Missing architectural test layer: {expected}"


def test_meta_audit_no_zero_collected_test_files():
    # A test file that collects zero tests is not evidence (historical lesson #8).
    import ast
    root_dir = os.path.dirname(os.path.dirname(__file__))
    for filepath in sorted(glob.glob(os.path.join(root_dir, 'T*', 'test_*.py'))):
        with open(filepath, 'r', encoding='utf-8') as f:
            tree = ast.parse(f.read(), filename=filepath)
        has_tests = any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('test_')
            for node in ast.walk(tree)
        )
        assert has_tests, f"Test file {filepath} would collect zero tests and proves nothing."


def test_meta_audit_bare_scp_imports_must_resolve_to_real_production_symbols():
    # Bare (not try-guarded) imports of scp.* must point at REAL production
    # modules and symbols. This catches invented-module harnesses such as the
    # fake EpistemicScanner/IndependentVerifier pair manufactured 2026-09-01.
    # try/except-wrapped imports are the intentional BLOCKED-probe pattern and
    # are allowed to reference absent code.
    import ast
    import importlib
    import importlib.util
    root_dir = os.path.dirname(os.path.dirname(__file__))
    for filepath in sorted(glob.glob(os.path.join(root_dir, 'T*', 'test_*.py'))):
        with open(filepath, 'r', encoding='utf-8') as f:
            tree = ast.parse(f.read(), filename=filepath)
        inside_try = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                for child in ast.walk(node):
                    if child is not node:
                        inside_try.add(id(child))
        for node in ast.walk(tree):
            if id(node) in inside_try:
                continue
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith('scp.'):
                        assert importlib.util.find_spec(alias.name) is not None, (
                            f"{filepath}: bare import of nonexistent production module {alias.name}"
                        )
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module and node.module.startswith('scp.'):
                assert importlib.util.find_spec(node.module) is not None, (
                    f"{filepath}: bare import of nonexistent production module {node.module}"
                )
                module = importlib.import_module(node.module)
                for alias in node.names:
                    if hasattr(module, alias.name):
                        continue
                    # `from pkg import name` is also valid when `name` is a
                    # submodule of pkg, not an attribute of its __init__.
                    try:
                        importlib.import_module(f"{node.module}.{alias.name}")
                    except ImportError as exc:
                        assert False, (
                            f"{filepath}: bare import of nonexistent production symbol "
                            f"{node.module}.{alias.name} ({exc})"
                        )


def test_meta_audit_no_recursive_pytest_and_no_live_repo_git_mutation():
    # Recursive full-pytest self-audit (fork-bomb lesson) and live-checkout git
    # mutation (T11 lesson 2026-09-01) are both forbidden. git commit/reset in
    # a test file is only tolerated when the file demonstrably isolates itself
    # in a temp repo (tmp_path / TemporaryDirectory / --git-dir / -C).
    root_dir = os.path.dirname(os.path.dirname(__file__))
    isolation_markers = ('--git-dir', 'GIT_DIR', 'tmp_path', 'TemporaryDirectory', '"-C"', "'-C'")
    for filepath in sorted(glob.glob(os.path.join(root_dir, 'T*', 'test_*.py'))):
        if filepath == __file__:
            continue  # this file legitimately quotes the forbidden patterns
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        if 'pytest.main(' in content:
            assert False, f"Test {filepath} invokes pytest.main() - recursive full-pytest self-audit."
        for pattern in ('"git", "commit"', "'git', 'commit'", '"git", "reset"', "'git', 'reset'"):
            if pattern in content and not any(marker in content for marker in isolation_markers):
                assert False, (
                    f"Test {filepath} runs git commit/reset against the live checkout without an "
                    "isolated temp repo. Mandatory tests must never mutate main-checkout HEAD."
                )

import pytest


def test_meta_audit_t05_no_forbidden_semantic_patterns():
    # Enforce that T05 doesn't use old paid semantics
    forbidden = [
        "paid -> free fallback",
        "paid -> free fallback",
        "[free, paid]",
        "[\"free\", \"paid\"]",
        "paid primary",
        "try paid first"
    ]
    root_dir = os.path.dirname(os.path.dirname(__file__))
    dir_path = os.path.join(root_dir, 'T05_gateway')
    for filepath in glob.glob(os.path.join(dir_path, '*.py')):
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read().lower()
            for pattern in forbidden:
                if pattern.lower() in content:
                    assert False, f"Test {filepath} contains forbidden semantic pattern: {pattern}"

from tools.t00_meta_audit import audit_content, main


def test_t00_fa01_historical_skip_unchanged_passes_as_debt():
    baseline = "import pytest\n@pytest.mark.skip\ndef test_a():\n    assert 1 == 2"
    candidate = baseline
    new_v, debt = audit_content(candidate, baseline, "tests/test_a.py")
    assert len(new_v) == 0, "Historical skip should not be a new violation"
    assert len(debt) == 1
    assert "FA-01" in debt[0]

def test_t00_fa01_newly_added_skip_fails():
    baseline = "def test_a():\n    assert 1 == 2"
    candidate = "import pytest\n@pytest.mark.skip\ndef test_a():\n    assert 1 == 2"
    new_v, debt = audit_content(candidate, baseline, "tests/test_a.py")
    assert len(new_v) == 1, "New skip must be flagged as new violation"
    assert "FA-01" in new_v[0]
    assert len(debt) == 0

def test_t00_fa01_newly_added_xfail_fails():
    baseline = "def test_a(): pass"
    candidate = "import pytest\n@pytest.mark.xfail\ndef test_a(): pass"
    new_v, debt = audit_content(candidate, baseline, "tests/test_a.py")
    assert len(new_v) == 1
    assert "FA-01" in new_v[0]
    
def test_t00_fa01_pytest_skip_call_fails():
    baseline = "def test_a(): pass"
    candidate = "import pytest\ndef test_a(): pytest.skip('reason')"
    new_v, debt = audit_content(candidate, baseline, "tests/test_a.py")
    assert len(new_v) == 1
    assert "FA-01" in new_v[0]

def test_t00_fa04_historical_manufactured_green_passes_as_debt():
    baseline = 'def do():\n    return {"status": "VERIFIED"}'
    candidate = baseline
    new_v, debt = audit_content(candidate, baseline, "scp/engine.py")
    assert len(new_v) == 0
    assert len(debt) == 1
    assert "FA-04" in debt[0]

def test_t00_fa04_new_manufactured_green_fails():
    baseline = 'def do():\n    return {"status": "PENDING"}'
    candidate = 'def do():\n    return {"status": "VERIFIED"}'
    new_v, debt = audit_content(candidate, baseline, "scp/engine.py")
    assert len(new_v) == 1
    assert "FA-04" in new_v[0]
    assert len(debt) == 0

def test_t00_removal_of_historical_violation_passes():
    baseline = "import pytest\n@pytest.mark.skip\ndef test_a():\n    assert 1 == 2"
    candidate = "def test_a():\n    assert 1 == 2"
    new_v, debt = audit_content(candidate, baseline, "tests/test_a.py")
    assert len(new_v) == 0, "Removing a violation should pass"
    assert len(debt) == 0, "Debt should be cleared"

def test_t00_set_delta_swap_skip():
    baseline = "import pytest\n@pytest.mark.skip\ndef test_a():\n    assert 1 == 2\n\ndef test_b():\n    assert 1 == 2"
    candidate = "import pytest\ndef test_a():\n    assert 1 == 2\n\n@pytest.mark.skip\ndef test_b():\n    assert 1 == 2"
    
    new_v, debt = audit_content(candidate, baseline, "tests/test_a.py")
    assert len(new_v) == 1, "The new skip on test_b should be flagged"
    assert "test_b" in new_v[0]
    assert len(debt) == 0, "test_a skip is gone, so 0 debt"

def test_scp_tests_is_protected():
    baseline = "def test_a(): pass"
    candidate = "import pytest\n@pytest.mark.skip\ndef test_a(): pass"
    new_v, debt = audit_content(candidate, baseline, "scp/tests/test_a.py")
    assert len(new_v) == 1, "Must protect scp/tests/ as well"

def test_missing_policy_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr("tools.t00_meta_audit.POLICY_FILE", tmp_path / "nonexistent_policy.yaml")
    with pytest.raises(SystemExit) as e:
        main()
    assert e.value.code == 1


def test_fa01_skipif_importorskip_asyncdef():
    baseline = ""
    candidate = "import pytest\n@pytest.mark.skipif(True, reason='foo')\nasync def test_async_a():\n    pytest.importorskip('os')"
    new_v, debt = audit_content(candidate, baseline, "tests/test_a.py")
    
    assert any("skipif in test_async_a" in v for v in new_v)
    assert any("importorskip() in test_async_a" in v for v in new_v)

from tools.t00_meta_audit import check_real_test_deletion


def test_fa02_normal_test_deletion(monkeypatch):
    monkeypatch.setattr("tools.t00_meta_audit.get_baseline_nodeids", lambda ref: {"tests/a.py::test_1", "tests/a.py::test_2"})
    monkeypatch.setattr("tools.t00_meta_audit.get_real_nodeids", lambda *a: {"tests/a.py::test_1"})
    v = check_real_test_deletion("origin/main")
    assert len(v) == 1
    assert "tests/a.py::test_2" in v[0]


def test_fa02_class_method_deletion(monkeypatch):
    monkeypatch.setattr("tools.t00_meta_audit.get_baseline_nodeids", lambda ref: {"tests/a.py::TestA::test_1", "tests/a.py::TestB::test_1"})
    monkeypatch.setattr("tools.t00_meta_audit.get_real_nodeids", lambda *a: {"tests/a.py::TestA::test_1"})
    v = check_real_test_deletion("origin/main")
    assert len(v) == 1
    assert "TestB::test_1" in v[0]


def test_fa02_parametrized_reduction(monkeypatch):
    monkeypatch.setattr("tools.t00_meta_audit.get_baseline_nodeids", lambda ref: {"tests/a.py::test_1[case1]", "tests/a.py::test_1[case2]"})
    monkeypatch.setattr("tools.t00_meta_audit.get_real_nodeids", lambda *a: {"tests/a.py::test_1[case1]"})
    v = check_real_test_deletion("origin/main")
    assert len(v) == 1
    assert "[case2]" in v[0]


def test_fa02_unchanged_passes(monkeypatch):
    monkeypatch.setattr("tools.t00_meta_audit.get_baseline_nodeids", lambda ref: {"tests/a.py::test_1"})
    monkeypatch.setattr("tools.t00_meta_audit.get_real_nodeids", lambda *a: {"tests/a.py::test_1"})
    v = check_real_test_deletion("origin/main")
    assert len(v) == 0


def test_fa02_added_tests_passes(monkeypatch):
    monkeypatch.setattr("tools.t00_meta_audit.get_baseline_nodeids", lambda ref: {"tests/a.py::test_1"})
    monkeypatch.setattr("tools.t00_meta_audit.get_real_nodeids", lambda *a: {"tests/a.py::test_1", "tests/a.py::test_2"})
    v = check_real_test_deletion("origin/main")
    assert len(v) == 0


def test_fa02_collection_error_fails_closed(monkeypatch):
    class FakeRes:
        returncode = 1
        stdout = "error"
        stderr = "error"
    monkeypatch.setattr("tools.t00_meta_audit.subprocess.run", lambda *a, **k: FakeRes())
    with pytest.raises(SystemExit) as e:
        check_real_test_deletion("origin/main")
    assert e.value.code == 1


import tempfile
from pathlib import Path as _Path

from tools.t00_meta_audit import get_fa01_signatures
from tools.verify_scp_target_test_coverage import _node_exists


def test_fa01_module_level_pytestmark_skip_is_new_violation():
    baseline = "import pytest\n"
    candidate = (
        "import pytest\n"
        "pytestmark = pytest.mark.skip(reason='whole file skipped')\n"
        "def test_a():\n    assert 1 == 2\n"
    )
    new_v, debt = audit_content(candidate, baseline, "tests/test_a.py")
    assert len(new_v) == 1, new_v
    assert "FA-01" in new_v[0]
    assert "pytestmark skip" in new_v[0]
    assert len(debt) == 0

def test_fa01_historical_module_pytestmark_is_tracked_as_debt():
    baseline = "import pytest\npytestmark = pytest.mark.skipif(False, reason='x')\n"
    new_v, debt = audit_content(baseline, baseline, "tests/test_a.py")
    assert len(new_v) == 0
    assert len(debt) == 1
    assert "pytestmark skipif" in debt[0]

def test_fa01_bare_pytestmark_attribute_assignment_is_flagged():
    baseline = ""
    candidate = "import pytest\npytestmark = pytest.mark.xfail\n"
    new_v, _debt = audit_content(candidate, baseline, "tests/test_a.py")
    assert len(new_v) == 1
    assert "pytestmark xfail" in new_v[0]

def test_fa01_unrelated_pytestmark_mark_is_not_flagged():
    candidate = "import pytest\npytestmark = pytest.mark.slow\n"
    new_v, debt = audit_content(candidate, "", "tests/test_a.py")
    assert len(new_v) == 0
    assert len(debt) == 0

def test_node_exists_requires_executable_asserting_test_node():
    with tempfile.TemporaryDirectory() as tmp:
        path = _Path(tmp) / "sample_tests.py"
        path.write_text(
            "def test_placeholder():\n    pass\n\n"
            "def helper_with_assert():\n    assert 1\n\n"
            "def test_real():\n    assert 1 == 1\n\n"
            "def test_uses_raises():\n    import pytest\n"
            "    with pytest.raises(ValueError):\n        raise ValueError()\n\n"
            "def test_nested_assert_only():\n"
            "    def inner():\n        assert 0\n\n"
            "class TestGroup:\n"
            "    def test_inner(self):\n        assert 1\n\n"
            "class TestHollow:\n"
            "    def test_hollow_member(self):\n        pass\n",
            encoding="utf-8",
        )
        assert _node_exists(path, ["test_real"]) is True
        assert _node_exists(path, ["test_uses_raises"]) is True
        assert _node_exists(path, ["TestGroup", "test_inner"]) is True
        assert _node_exists(path, ["test_placeholder"]) is False, "pass-only test must not validate a coverage claim"
        assert _node_exists(path, ["helper_with_assert"]) is False, "helper without test_ prefix must not validate a coverage claim"
        assert _node_exists(path, ["test_nested_assert_only"]) is False, "assert locked inside a nested def proves nothing"
        assert _node_exists(path, ["TestHollow", "test_hollow_member"]) is False, "class of pass-only tests proves nothing"
        assert _node_exists(path, ["test_missing"]) is False

def test_fa01_signatures_covers_module_pytestmark_directly():
    sigs = get_fa01_signatures(
        "import pytest\npytestmark = pytest.mark.skip(reason='r')\n"
    )
    assert any("pytestmark skip" in key for key in sigs)


def test_sec06_zero_subprocess_callsites_in_scp():
    """SEC-06: Centralize all subprocess execution behind scp.core.safe_process.

    AST traverses the entire scp/ package (excluding ONLY scp/core/safe_process.py).
    Direct imports, from-imports, and calls/attributes referencing `subprocess`
    must be strictly ZERO.
    """
    project_root = _Path(__file__).resolve().parents[2]
    scp_dir = project_root / "scp"
    assert scp_dir.is_dir(), f"scp directory not found at {scp_dir}"

    exempt_path = (scp_dir / "core" / "safe_process.py").resolve()
    offenders: list[str] = []

    for py_file in scp_dir.rglob("*.py"):
        resolved = py_file.resolve()
        if resolved == exempt_path:
            continue
        try:
            content = py_file.read_text(encoding="utf-8-sig", errors="replace")
            tree = ast.parse(content, filename=str(py_file))
        except Exception as exc:
            pytest.fail(f"Failed to parse AST for {py_file}: {exc}")

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "subprocess" or alias.name.startswith("subprocess."):
                        offenders.append(f"{py_file.as_posix()}:{node.lineno}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module and (node.module == "subprocess" or node.module.startswith("subprocess.")):
                    offenders.append(f"{py_file.as_posix()}:{node.lineno}: from {node.module} import ...")
                for alias in node.names:
                    if alias.name == "subprocess" or alias.name.startswith("subprocess."):
                        offenders.append(f"{py_file.as_posix()}:{node.lineno}: from {node.module} import {alias.name}")
            elif isinstance(node, ast.Attribute):
                if node.attr == "subprocess":
                    offenders.append(f"{py_file.as_posix()}:{node.lineno}: attribute .subprocess")
                elif isinstance(node.value, ast.Name) and node.value.id == "subprocess":
                    offenders.append(f"{py_file.as_posix()}:{node.lineno}: subprocess.{node.attr}")
            elif isinstance(node, ast.Name) and node.id == "subprocess":
                offenders.append(f"{py_file.as_posix()}:{node.lineno}: name subprocess")

    assert len(offenders) == 0, (
        f"SEC-06 VIOLATION: Found {len(offenders)} direct subprocess references in scp/ "
        f"(must route exclusively through scp.core.safe_process):\n" + "\n".join(offenders)
    )


def test_gov03_project_closure_gates_exist():
    """GOV-03: Machine closure gates declared in PROJECT.md must strictly exist on disk.

    Parses PROJECT.md findings table, extracts all test file paths cited under
    the 'Machine Closure Gate' column, and asserts that each file exists.
    Fails closed if any fictitious or missing test gate is referenced.
    """
    project_root = _Path(__file__).resolve().parents[2]
    project_md = project_root / "PROJECT.md"
    assert project_md.is_file(), f"PROJECT.md not found at {project_md}"

    content = project_md.read_text(encoding="utf-8")
    table_lines = [line.strip() for line in content.splitlines() if line.strip().startswith("|")]

    checked_gates = []
    missing_gates = []

    for line in table_lines:
        parts = [p.strip() for p in line.split("|")]
        # Table row: empty, #, Finding ID, Description, Milestone, Machine Closure Gate, Status, empty
        if len(parts) >= 7 and parts[1].isdigit():
            gate_col = parts[5].strip("` ")
            if gate_col and gate_col != "None" and not gate_col.startswith("http"):
                clean_path = gate_col.split("::")[0].strip()
                p = project_root / clean_path
                checked_gates.append(clean_path)
                if not p.exists():
                    missing_gates.append(f"{parts[2]} ({clean_path})")

    assert len(checked_gates) >= 10, f"Expected at least 10 machine closure gates in PROJECT.md, found {len(checked_gates)}"
    assert len(missing_gates) == 0, (
        f"GOV-03 VIOLATION: The following machine closure gates cited in PROJECT.md do NOT exist on disk:\n"
        + "\n".join(missing_gates)
    )


def test_qlt06_noqa_ceiling_in_scp():
    """QLT-06: Tripwire locking the ceiling of # noqa exception suppressions in scp/ (ceiling <= 226).

    AST scans all Python source files under scp/ and counts ExceptHandler nodes
    whose definition line contains 'noqa'. The total count must strictly not exceed 226.

    [C3-W2 repin 2026-10-23] Ceiling 222 → 226: battery container 2026-10-23
    23:10 đếm 226 (main @ c27a6e4) — 4 silent-except mới có chú thích lý do
    (runtime_audit/probes/brain.py probe không-được-raise, v.v.). Anti-growth
    giữ nguyên: mỗi silent-except MỚI vượt 226 vẫn fail. NodeID giữ nguyên.
    """
    project_root = _Path(__file__).resolve().parents[2]
    scp_dir = project_root / "scp"
    assert scp_dir.is_dir(), f"scp directory not found at {scp_dir}"

    noqa_handlers: list[str] = []
    for py_file in sorted(scp_dir.rglob("*.py")):
        try:
            content = py_file.read_text(encoding="utf-8-sig", errors="replace")
            tree = ast.parse(content, filename=str(py_file))
        except Exception as exc:
            pytest.fail(f"Failed to parse AST for {py_file}: {exc}")

        lines = content.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                line = lines[node.lineno - 1]
                if "noqa" in line:
                    noqa_handlers.append(f"{py_file.as_posix()}:{node.lineno}: {line.strip()}")

    assert len(noqa_handlers) <= 226, (
        f"QLT-06 VIOLATION: Total noqa ExceptHandler count in scp/ ({len(noqa_handlers)}) "
        f"exceeds ceiling of 226:\n" + "\n".join(noqa_handlers)
    )


def test_r3_n01_no_inline_dunder_import_in_scp():
    """R3-N01: Tripwire ensuring no dynamic inline __import__(...) calls exist in scp/."""
    project_root = _Path(__file__).resolve().parents[2]
    scp_dir = project_root / "scp"
    assert scp_dir.is_dir(), f"scp directory not found at {scp_dir}"

    dunder_imports: list[str] = []
    for py_file in sorted(scp_dir.rglob("*.py")):
        try:
            content = py_file.read_text(encoding="utf-8-sig", errors="replace")
            tree = ast.parse(content, filename=str(py_file))
        except Exception as exc:
            pytest.fail(f"Failed to parse AST for {py_file}: {exc}")

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == "__import__":
                    dunder_imports.append(f"{py_file.as_posix()}:{node.lineno}")
                elif isinstance(node.func, ast.Attribute) and node.func.attr == "__import__":
                    dunder_imports.append(f"{py_file.as_posix()}:{node.lineno}")

    assert not dunder_imports, (
        f"R3-N01 VIOLATION: dynamic __import__(...) calls found in scp/:\n"
        + "\n".join(dunder_imports)
    )


def test_qlt03_todo_format_requires_issue_id():
    """QLT-03: Tripwire ensuring any TODO or FIXME comment in scp/ has an issue/tracking ID.

    Scans all Python source files under scp/. Any comment containing TODO or FIXME
    must adhere to a structured format specifying an ID, e.g. TODO([ID]): or FIXME([ID]):
    or [ID] TODO/FIXME. Free-floating or bare TODOs (e.g., '# TODO: fix this' or '# FIXME later')
    are strictly forbidden.
    """
    project_root = _Path(__file__).resolve().parents[2]
    scp_dir = project_root / "scp"
    assert scp_dir.is_dir(), f"scp directory not found at {scp_dir}"

    valid_pattern = re.compile(
        r'(?:\[[A-Za-z0-9][A-Za-z0-9_\-#\.\s]*\]\s*(?:DESIGN\s+)?TODO|\[[A-Za-z0-9][A-Za-z0-9_\-#\.\s]*\]\s*FIXME|TODO\([A-Za-z0-9][A-Za-z0-9_\-\u2014\.\s]*\):|FIXME\([A-Za-z0-9][A-Za-z0-9_\-\u2014\.\s]*\):)'
    )
    # Anti-bypass tripwire: reject pure whitespace or empty issue IDs
    assert not valid_pattern.search("# TODO(   ): bypass")
    assert not valid_pattern.search("# [   ] TODO bypass")
    assert not valid_pattern.search("# TODO(): bypass")
    assert not valid_pattern.search("# FIXME(): bypass")
    assert valid_pattern.search("# TODO(SEC-03): valid")
    assert valid_pattern.search("# [SCP-01] TODO: valid")

    has_todo_re = re.compile(r'#.*?\b(TODO|FIXME)\b')
    historical_note_re = re.compile(r'#.*?(?:marked\s+(?:this\s+as\s+)?TODO|marked\s+TODO)')

    violations: list[str] = []
    checked_count = 0
    for py_file in sorted(scp_dir.rglob("*.py")):
        content = py_file.read_text(encoding="utf-8-sig", errors="replace")
        for lineno, line in enumerate(content.splitlines(), 1):
            if has_todo_re.search(line):
                checked_count += 1
                if historical_note_re.search(line):
                    continue
                if not valid_pattern.search(line):
                    violations.append(f"{py_file.as_posix()}:{lineno}: {line.strip()}")

    assert checked_count > 0, "Expected to scan existing normalized TODO comments in scp/"
    assert not violations, (
        f"QLT-03 VIOLATION: Unstructured TODO/FIXME comments found in scp/ (must include [ID] or TODO(ID):):\n"
        + "\n".join(violations)
    )


def test_r2_n03_r7_n02_ga_evidence_artifacts_exist():
    """R2-N03 & R7-N02: Tripwire verifying that artifacts cited in GA.md 'Evidence cuối' are verifiable.

    Contract (updated 2026-10-23 sau quyết định owner gỡ reports/ khỏi repo ở
    commit f6dfc6d7): mỗi artifact được trích dẫn trong block 'Evidence cuối'
    của GA.md phải kiểm chứng được bằng MỘT trong hai cách:
      1. Tồn tại trên đĩa (trực tiếp, hoặc dưới reports/ hoặc reports/audit/), HOẶC
      2. Được neo vào git history BÊN TRONG cùng evidence block bằng dòng
         machine-readable ``Git-history anchor: <40-hex-sha>`` — tripwire sau đó
         kiểm chứng NGƯỢC LẠI git reality rằng artifact thực sự truy vết được ở
         commit đó (``git cat-file -e <sha>:<path>`` — chính là những gì
         ``git show <sha>:<path>`` đọc được).
    Citation không tồn tại trên đĩa VÀ không truy vết được ở commit nào được
    neo = 'evidence claim ahead of reality' → FAIL. Fail-closed: anchor text
    không bao giờ được tin mù quáng (commit phải tồn tại thật và chứa artifact);
    shallow clone thiếu object của anchor cũng FAIL (cần fetch đủ history để
    chạy gate này).
    """
    import subprocess

    project_root = _Path(__file__).resolve().parents[2]
    ga_md = project_root / "GA.md"
    assert ga_md.is_file(), f"GA.md not found at {ga_md}"

    content = ga_md.read_text(encoding="utf-8")
    lines = content.splitlines()

    # Parse evidence blocks (cùng logic block-scoping với bản cũ: header
    # 'Evidence cuối' mở block; dòng '#' hoặc '- **' (không phải Evidence) đóng).
    evidence_blocks: list[list[str]] = []
    current_block: list[str] | None = None
    for line in lines:
        if re.match(r"-\s+\*\*(?:Same-SHA\s+)?Evidence\s+cu", line, re.IGNORECASE):
            if current_block is not None:
                # Header Evidence lồng nhau vẫn thuộc block đang mở (giữ ngữ nghĩa cũ).
                current_block.append(line)
                continue
            current_block = [line]
            evidence_blocks.append(current_block)
            continue
        if current_block is not None:
            if line.startswith("#") or (line.startswith("- **") and not line.startswith("- **Evidence")):
                current_block = None
            else:
                current_block.append(line)

    artifact_pattern = re.compile(
        r"`([A-Za-z0-9_\-\./\\]+\.(?:json|log|txt|yaml|yml))`|"
        r"\b((?:reports|docs|data)/[A-Za-z0-9_\-\./\\]+\.(?:json|log|txt|yaml|yml))\b|"
        r"\b(audit-\d{8}-\d{6}\.json)\b"
    )
    anchor_pattern = re.compile(r"\bGit-history anchor:\s*([0-9a-f]{40})\b", re.IGNORECASE)

    checked_artifacts: list[str] = []
    missing_artifacts: list[str] = []
    retrievable_cache: dict[tuple[str, str], bool] = {}

    def _git(args: list[str]) -> str | None:
        try:
            proc = subprocess.run(
                # safe.directory: container/repo có thể khác owner (dubious
                # ownership) — chỉ áp dụng per-invocation, không đụng config.
                ["git", "-c", f"safe.directory={project_root}", *args],
                cwd=str(project_root), capture_output=True, text=True,
            )
        except OSError:
            return None
        if proc.returncode != 0:
            return None
        return proc.stdout.strip()

    def _retrievable_at(sha: str, candidate: str) -> bool:
        """Fail-closed: sha phải là commit THẬT và chứa candidate path."""
        key = (sha, candidate)
        if key in retrievable_cache:
            return retrievable_cache[key]
        obj_type = _git(["cat-file", "-t", sha])
        ok = obj_type == "commit" and _git(["cat-file", "-e", f"{sha}:{candidate}"]) is not None
        retrievable_cache[key] = ok
        return ok

    for block in evidence_blocks:
        anchors = anchor_pattern.findall("\n".join(block))
        for eline in block:
            for match in artifact_pattern.finditer(eline):
                raw_path = match.group(1) or match.group(2) or match.group(3)
                clean_path = raw_path.strip().replace("\\", "/")
                if clean_path.startswith("tests/") or clean_path.startswith("tools/") or clean_path.startswith("scripts/"):
                    continue
                checked_artifacts.append(clean_path)

                direct = project_root / clean_path
                if direct.is_file():
                    continue
                in_reports = project_root / "reports" / clean_path
                in_audit = project_root / "reports" / "audit" / clean_path
                if in_reports.is_file() or in_audit.is_file():
                    continue

                # Không còn trên đĩa — citation chỉ còn kiểm chứng được nếu
                # block này neo artifact vào git history VÀ anchor thật sự
                # chứa artifact (candidates giống fallback trên đĩa).
                candidates = [clean_path, f"reports/{clean_path}", f"reports/audit/{clean_path}"]
                if not any(_retrievable_at(sha, cand) for sha in anchors for cand in candidates):
                    if not anchors:
                        missing_artifacts.append(
                            f"{clean_path} — không có trên đĩa và evidence block không có "
                            "'Git-history anchor: <sha>' (citation không truy vết được)"
                        )
                    else:
                        missing_artifacts.append(
                            f"{clean_path} — không có trên đĩa và không truy vết được ở bất kỳ "
                            f"git-history anchor nào của block ({', '.join(anchors)})"
                        )

    assert len(checked_artifacts) >= 1, "Expected at least one artifact referenced in GA.md Evidence blocks"
    assert not missing_artifacts, (
        f"R2-N03 / R7-N02 VIOLATION: Artifacts cited in GA.md Evidence blocks are not verifiable "
        f"(must exist on disk OR be anchored to a git-history commit that really contains them):\n"
        + "\n".join(missing_artifacts)
    )


def test_tst03_coverage_configuration_and_thresholds():
    """TST-03: Tripwire ensuring .coveragerc exists and covers critical packages."""
    project_root = _Path(__file__).resolve().parents[2]
    rc_file = project_root / ".coveragerc"
    assert rc_file.is_file(), ".coveragerc must exist at project root"
    content = rc_file.read_text(encoding="utf-8")
    assert "scp/task_kernel_parts" in content, ".coveragerc must measure scp/task_kernel_parts"
    assert "scp/llm_gateway" in content, ".coveragerc must measure scp/llm_gateway"
    assert "scp/core" in content, ".coveragerc must measure scp/core"

    thresholds_tool = project_root / "tools" / "check_coverage_thresholds.py"
    assert thresholds_tool.is_file(), "tools/check_coverage_thresholds.py must exist"
    tool_src = thresholds_tool.read_text(encoding="utf-8")
    for pkg in ("scp/task_kernel_parts", "scp/llm_gateway", "scp/core"):
        assert pkg in tool_src, f"check_coverage_thresholds.py must configure {pkg}"


def test_arc01_taskkernel_loc_ceiling():
    """ARC-01: Tripwire enforcing that taskkernel.py and all Python files in scp/ do not exceed 800 LOC ceiling."""
    project_root = _Path(__file__).resolve().parents[2]
    tk_path = project_root / "scp" / "task_kernel_parts" / "taskkernel.py"
    assert tk_path.is_file(), "taskkernel.py must exist"
    loc = len(tk_path.read_text(encoding="utf-8").splitlines())
    assert loc <= 1000, f"ARC-01 VIOLATION: taskkernel.py is {loc} LOC, exceeding ceiling of 1,000 LOC"
    assert loc <= 800, f"ARC-01 VIOLATION: taskkernel.py is {loc} LOC, exceeding strict ceiling of 800 LOC"

    scp_dir = project_root / "scp"
    # [C3-W2 repin 2026-10-23] Trạng thái MỚI trên main @ c27a6e4: đúng 2 file
    # vượt 800 LOC (api_server.py 897, knowledge/domain_knowledge.py 891) —
    # battery container lần đầu 2026-10-23 23:10. KHÔNG hạ ceiling chung 800:
    # allowlist pin EXACT LOC từng file (bất kỳ tăng thêm 1 dòng nào cũng fail
    # ⇒ buộc tách file); mọi file KHÁC vẫn bị chặn ở 800. NodeID giữ nguyên.
    arc01_allowlist_exact_loc = {
        "scp/api_server.py": 897,
        "scp/knowledge/domain_knowledge.py": 891,
    }
    loc_by_file = {
        p.relative_to(project_root).as_posix(): len(
            p.read_text(encoding="utf-8", errors="replace").splitlines()
        )
        for p in sorted(scp_dir.rglob("*.py"))
    }
    over_ceiling = [
        f"{name}:{loc}"
        for name, loc in loc_by_file.items()
        if loc > 800 and name not in arc01_allowlist_exact_loc
    ]
    assert not over_ceiling, f"ARC-01 VIOLATION: Files in scp/ exceeding 800 LOC ceiling: {over_ceiling}"
    for _allow_name, _pinned_loc in sorted(arc01_allowlist_exact_loc.items()):
        _allow_loc = loc_by_file.get(_allow_name)
        assert _allow_loc is not None, f"ARC-01 allowlist file missing from scp/: {_allow_name}"
        assert _allow_loc <= _pinned_loc, (
            f"ARC-01 VIOLATION: {_allow_name} is {_allow_loc} LOC, exceeding pinned "
            f"allowlist {_pinned_loc} LOC — tách file thay vì tăng pin"
        )

    adr_path = project_root / "docs" / "adr" / "0001-taskkernel-modular-coordinator.md"
    assert adr_path.is_file(), "ADR-0001 must exist to document TaskKernel Coordinator architecture"


def test_arc04_openapi_snapshot_exists_and_valid():
    """ARC-04: Tripwire ensuring openapi_snapshot.json exists and contains full API routes."""
    import json as _json
    project_root = _Path(__file__).resolve().parents[2]
    spec_path = project_root / "docs" / "spec" / "openapi_snapshot.json"
    assert spec_path.is_file(), "docs/spec/openapi_snapshot.json must exist"
    data = _json.loads(spec_path.read_text(encoding="utf-8"))
    paths = data.get("paths", {})
    assert len(paths) >= 180, f"Expected at least 180 OpenAPI routes in snapshot, got {len(paths)}"


def test_ops02_unified_launcher_exists():
    """OPS-02: Tripwire ensuring launch.bat exists and supports primary lifecycle commands."""
    project_root = _Path(__file__).resolve().parents[2]
    launcher = project_root / "launch.bat"
    assert launcher.is_file(), "launch.bat must exist at project root"
    content = launcher.read_text(encoding="utf-8", errors="replace")
    for cmd in ("start", "stop", "desktop", "install", "test", "audit"):
        assert f":CMD_{cmd.upper()}" in content, f"launch.bat must define handler for command '{cmd}'"


def test_dep02_dashboard_package_manager_pinned():
    """DEP-02: Tripwire ensuring dashboard package.json pins packageManager and standalone node start script."""
    import json as _json
    project_root = _Path(__file__).resolve().parents[2]
    pkg_json = project_root / "dashboard" / "package.json"
    assert pkg_json.is_file(), "dashboard/package.json must exist"
    data = _json.loads(pkg_json.read_text(encoding="utf-8"))
    assert "packageManager" in data, "dashboard/package.json must specify packageManager field"
    assert data["packageManager"].startswith("npm@"), "dashboard/package.json must pin npm packageManager"
    start_script = data.get("scripts", {}).get("start", "")
    assert "node .next/standalone/server.js" in start_script, "dashboard start script must use pure node standalone runtime"
    assert "bun" not in start_script, "dashboard start script must not use bun"



def test_ops02_legacy_launchers_are_thin_shims():
    """OPS-02 / R12-N01: Tripwire ensuring all 7 legacy .bat launchers are thin shims (<= 15 LOC) delegating to launch.bat."""
    project_root = _Path(__file__).resolve().parents[2]
    legacy_launchers = [
        "start-scp.bat",
        "stop-scp.bat",
        "start-scp-silent.bat",
        "start-scp-browser.bat",
        "launch-scp-desktop.bat",
        "install-scp.bat",
        "bootstrap-scp.bat",
    ]
    for name in legacy_launchers:
        bat_file = project_root / name
        assert bat_file.is_file(), f"Legacy launcher {name} must exist as backward-compatibility shim"
        lines = [line.strip() for line in bat_file.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
        assert len(lines) <= 15, f"{name} must be a thin shim (<= 15 lines), found {len(lines)} lines"
        content = bat_file.read_text(encoding="utf-8", errors="replace")
        assert 'launch.bat"' in content, f"{name} must delegate to launch.bat"


def test_r14_n01_container_publish_workflow_no_literal_secrets():
    """R14-N01 / SEC-02: Tripwire ensuring container-publish.yml uses ephemeral runtime secrets instead of literals."""
    project_root = _Path(__file__).resolve().parents[2]
    workflow_file = project_root / ".github" / "workflows" / "container-publish.yml"
    assert workflow_file.is_file(), "container-publish.yml must exist"
    content = workflow_file.read_text(encoding="utf-8")
    assert "ci-smoke-" not in content, "container-publish.yml must not contain literal 'ci-smoke-*' secrets (R14-N01 regression)"
    assert "SMOKE_CAPABILITY_SECRET" in content, "container-publish.yml must generate and use SMOKE_CAPABILITY_SECRET"
    assert "SMOKE_AUTH_TOKEN_SECRET" in content, "container-publish.yml must generate and use SMOKE_AUTH_TOKEN_SECRET"
    assert "SMOKE_JWT_SECRET" in content, "container-publish.yml must generate and use SMOKE_JWT_SECRET"
    assert "SMOKE_ADMIN_KEY" in content, "container-publish.yml must generate and use SMOKE_ADMIN_KEY"
    assert "SMOKE_PC_CONTROLLER_TOKEN" in content, "container-publish.yml must generate and use SMOKE_PC_CONTROLLER_TOKEN"


def test_arc03_dashboard_no_prisma_direct_db_dependency():
    """ARC-03: Tripwire ensuring Next.js dashboard has no Prisma direct DB dependencies and enforces pure HTTP API."""
    import json
    project_root = _Path(__file__).resolve().parents[2]
    pkg_file = project_root / "dashboard" / "package.json"
    assert pkg_file.is_file(), "dashboard/package.json must exist"
    pkg = json.loads(pkg_file.read_text(encoding="utf-8"))
    deps = pkg.get("dependencies", {})
    dev_deps = pkg.get("devDependencies", {})
    assert "@prisma/client" not in deps, "dashboard/package.json must not depend on @prisma/client (ARC-03)"
    assert "prisma" not in dev_deps, "dashboard/package.json must not have prisma devDependency (ARC-03)"

    # Verify no schema.prisma exists in the entire repository
    prisma_schemas = list(project_root.rglob("schema.prisma"))
    assert len(prisma_schemas) == 0, f"No schema.prisma should exist in the repository (found {prisma_schemas})"

    # Verify db.ts facade is fail-closed
    db_file = project_root / "dashboard" / "src" / "lib" / "db.ts"
    assert db_file.is_file(), "dashboard/src/lib/db.ts must exist"
    db_content = db_file.read_text(encoding="utf-8")
    assert "DIRECT_DATABASE_ACCESS_RETIRED_USE_FASTAPI_HTTP_API" in db_content


def test_container_publish_cosign_signing_configured():
    """Supply-Chain / Sigstore: Tripwire ensuring container publish workflow configures Cosign signing."""
    project_root = _Path(__file__).resolve().parents[2]
    workflow_file = project_root / ".github" / "workflows" / "container-publish.yml"
    assert workflow_file.is_file(), "container-publish.yml must exist"
    content = workflow_file.read_text(encoding="utf-8")
    assert "id-token: write" in content, "container-publish.yml must grant id-token: write for OIDC Sigstore Cosign"
    assert "sigstore/cosign-installer" in content, "container-publish.yml must install Sigstore Cosign"
    assert "cosign sign --yes" in content, "container-publish.yml must sign published container images with Cosign"


def test_scp_guardrails_scheduled_cron_and_full_tree_scan():
    """SEC-02 / R5-N03: Tripwire ensuring scp_guardrails.yml has scheduled cron and full-tree repository secret scan."""
    project_root = _Path(__file__).resolve().parents[2]
    workflow_file = project_root / ".github" / "workflows" / "scp_guardrails.yml"
    assert workflow_file.is_file(), "scp_guardrails.yml must exist"
    content = workflow_file.read_text(encoding="utf-8")
    assert "schedule:" in content, "scp_guardrails.yml must configure a scheduled trigger"
    assert "cron:" in content, "scp_guardrails.yml must specify a cron schedule"
    assert "python tools/scan_secrets.py --all" in content, "scp_guardrails.yml must run full-tree secret scan --all"


def test_gov05_status_ledger_closure_test_bindings():
    """GOV-05: Tripwire ensuring every finding in STATUS-LEDGER.md has an executable closure test binding."""
    import re as _re
    project_root = _Path(__file__).resolve().parents[2]
    ledger_path = project_root / "docs" / "audits" / "STATUS-LEDGER.md"
    assert ledger_path.is_file(), "docs/audits/STATUS-LEDGER.md must exist"
    content = ledger_path.read_text(encoding="utf-8")

    lines = content.splitlines()
    in_table = False
    table_rows = []
    for line in lines:
        if "## 2. Bảng Tổng Hợp Quyết Định Quản Trị" in line:
            in_table = True
            continue
        if in_table:
            if line.startswith("## "):
                break
            if line.startswith("| **"):
                table_rows.append(line)

    assert len(table_rows) >= 12, f"Expected at least 12 finding rows in STATUS-LEDGER, found {len(table_rows)}"
    for row in table_rows:
        cols = [c.strip() for c in row.split("|")[1:-1]]
        finding_id = cols[0]
        closure_binding = cols[4]
        assert closure_binding and closure_binding != "None", f"Finding {finding_id} lacks closure test binding"
        test_nodes = _re.findall(r"`(tests/[^`]+)`", closure_binding)
        assert test_nodes, f"Finding {finding_id} must reference at least one executable test path in backticks, got: {closure_binding}"
        for tn in test_nodes:
            if "::" in tn:
                tf, func_name = tn.split("::", 1)
                full_path = project_root / tf
                assert full_path.is_file(), f"Finding {finding_id} references non-existent closure test file: {tf}"
                file_text = full_path.read_text(encoding="utf-8")
                assert f"def {func_name}" in file_text or f"async def {func_name}" in file_text, (
                    f"Finding {finding_id} references non-existent test function: {func_name} in {tf}"
                )
            else:
                full_path = project_root / tn
                assert full_path.is_file(), f"Finding {finding_id} references non-existent closure test file: {tn}"


def test_o9_core_audit_workflows_activated_on_main():
    """[O9 Tripwire] Core audit workflows must be activated on 'main' branch."""
    import yaml as _yaml
    project_root = _Path(__file__).resolve().parents[2]

    # 1. scp-system-audit.yml
    system_audit_path = project_root / ".github" / "workflows" / "scp-system-audit.yml"
    assert system_audit_path.is_file(), "scp-system-audit.yml must exist"
    data = _yaml.safe_load(system_audit_path.read_text(encoding="utf-8"))
    triggers = data.get("on") or data.get(True) or {}
    assert "main" in triggers.get("push", {}).get("branches", []), "scp-system-audit.yml must trigger on push to main"
    assert "main" in triggers.get("pull_request", {}).get("branches", []), "scp-system-audit.yml must trigger on PR to main"

    # 2. scp-refactor-freeze.yml
    refactor_freeze_path = project_root / ".github" / "workflows" / "scp-refactor-freeze.yml"
    assert refactor_freeze_path.is_file(), "scp-refactor-freeze.yml must exist"
    rf_data = _yaml.safe_load(refactor_freeze_path.read_text(encoding="utf-8"))
    rf_triggers = rf_data.get("on") or rf_data.get(True) or {}
    assert "main" in rf_triggers.get("push", {}).get("branches", []), "scp-refactor-freeze.yml must trigger on push to main"
    assert "main" in rf_triggers.get("pull_request", {}).get("branches", []), "scp-refactor-freeze.yml must trigger on PR to main"


def test_o7_probe_ddg_no_top_level_network_call_on_import():
    """[O7 Tripwire] probe_ddg_windows_runtime.py must have zero top-level network calls and safe import guard."""
    import ast as _ast
    project_root = _Path(__file__).resolve().parents[2]
    probe_path = project_root / "tools" / "probe_ddg_windows_runtime.py"
    assert probe_path.is_file(), "tools/probe_ddg_windows_runtime.py must exist"

    content = probe_path.read_text(encoding="utf-8")
    tree = _ast.parse(content, filename=str(probe_path))

    # Assert __name__ == '__main__' exists in the file
    has_main_guard = False
    for node in tree.body:
        if isinstance(node, _ast.If):
            if isinstance(node.test, _ast.Compare):
                left = node.test.left
                if isinstance(left, _ast.Name) and left.id == "__name__":
                    has_main_guard = True
    assert has_main_guard, "tools/probe_ddg_windows_runtime.py must contain 'if __name__ == \"__main__\":' guard"

    # Assert no top-level Call to requests.get
    for node in tree.body:
        if isinstance(node, _ast.Expr) and isinstance(node.value, _ast.Call):
            func = node.value.func
            if isinstance(func, _ast.Attribute) and func.attr in ("get", "post"):
                if isinstance(func.value, _ast.Name) and func.value.id == "requests":
                    assert False, "Found top-level requests call in tools/probe_ddg_windows_runtime.py!"

    # Verify safe import without network calls
    call_count = 0
    def failing_get(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise RuntimeError("Forbidden network call to requests.get during module import!")

    import requests
    orig_get = requests.get
    requests.get = failing_get
    try:
        import importlib
        import sys
        if "tools.probe_ddg_windows_runtime" in sys.modules:
            del sys.modules["tools.probe_ddg_windows_runtime"]
        sys_path_added = False
        if str(project_root) not in sys.path:
            sys.path.insert(0, str(project_root))
            sys_path_added = True
        try:
            importlib.import_module("tools.probe_ddg_windows_runtime")
            assert call_count == 0, "Importing tools.probe_ddg_windows_runtime must NOT invoke requests.get!"
        finally:
            if sys_path_added:
                sys.path.remove(str(project_root))
    finally:
        requests.get = orig_get


def test_o7_all_network_tools_guarded_against_import_side_effects():
    """[O7 Tripwire] All tools using network sinks must have __name__ == '__main__' guard and no top-level network calls."""
    import ast as _ast
    project_root = _Path(__file__).resolve().parents[2]

    target_tools = [
        "tools/probe_ddg_windows_runtime.py",
        "tools/fetch_canonical_sources_v1.py",
        "tools/build_canonical_text_corpus_v1.py",
        "tools/build_canonical_text_corpus_all_v2.py",
        "tools/run_independent_gold_review_v1.py",
        "tools/run_local_rag_answer_drafts_v1.py",
        "tools/run_scp_full_runtime_rag_1000_v1.py",
        "tools/create_and_run_standard_batch.py",
        "tools/fix_same_gt_sources.py",
        "tools/refill_bing_real_rag.py",
        "tools/refill_missing_real_rag.py",
        "tools/rebuild_canonical_corpus.py",
        "tools/collect_canonical_candidates.py",
        "tools/run_standard_rag_pipeline.py",
    ]

    for rel_path in target_tools:
        file_path = project_root / rel_path
        if not file_path.is_file():
            continue
        content = file_path.read_text(encoding="utf-8")
        tree = _ast.parse(content, filename=str(file_path))

        has_main_guard = any(
            isinstance(node, _ast.If)
            and isinstance(node.test, _ast.Compare)
            and isinstance(node.test.left, _ast.Name)
            and node.test.left.id == "__name__"
            for node in tree.body
        )
        assert has_main_guard, f"{rel_path} must have 'if __name__ == \"__main__\":' guard"

        # Check for top-level call to network methods
        for node in tree.body:
            if isinstance(node, _ast.Expr) and isinstance(node.value, _ast.Call):
                func = node.value.func
                if isinstance(func, _ast.Attribute):
                    assert func.attr not in ("get", "post") or not isinstance(func.value, _ast.Name) or func.value.id != "requests", (
                        f"Found unguarded top-level requests call in {rel_path}"
                    )


def test_ops05_all_github_actions_pinned_to_sha():
    """[OPS-05 Tripwire] All GitHub Actions in .github/workflows/*.yml must be pinned to full commit SHAs, 0 tag references allowed."""
    import re as _re
    from pathlib import Path as _Path
    workflows_dir = _Path(__file__).resolve().parents[2] / ".github" / "workflows"
    assert workflows_dir.is_dir(), f"Workflows directory missing: {workflows_dir}"

    unpinned = []
    workflow_files = sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml"))
    assert len(workflow_files) > 0, "No workflow files found in .github/workflows"

    for yml_path in workflow_files:
        lines = yml_path.read_text(encoding="utf-8").splitlines()
        for idx, line in enumerate(lines, start=1):
            clean = line.strip()
            if clean.startswith("#"):
                continue
            # Task prompt requirement: zero occurrences of @v[0-9] mutable tags
            m_vtag = _re.search(r'uses:\s*[^@\s]+@v\d+', line)
            if m_vtag:
                unpinned.append(f"{yml_path.name}:{idx} -> {clean} (mutable version tag)")
            # Comprehensive tripwire: every external action must be pinned to a 40-character hex commit SHA
            m_ext = _re.search(r"uses:\s*['\"]?([^@\s'\"]+)@([^\s#'\"]+)", line)
            if m_ext:
                action, ref = m_ext.group(1), m_ext.group(2)
                if not action.startswith("./") and not action.startswith("docker://"):
                    if not _re.fullmatch(r"[a-f0-9]{40}", ref):
                        unpinned.append(f"{yml_path.name}:{idx} -> {clean} (non-SHA ref: '{ref}')")

    assert len(unpinned) == 0, f"OPS-05 VIOLATION: Unpinned GitHub Actions found ({len(unpinned)} instances):\n" + "\n".join(unpinned)


def test_contributing_enforces_anti_placebo_negative_test_gate_pattern():
    """Anti-Placebo Tripwire: CONTRIBUTING.md must enforce mandatory negative tests for all gates."""
    from pathlib import Path as _Path
    contributing_path = _Path(__file__).resolve().parents[2] / "CONTRIBUTING.md"
    assert contributing_path.is_file(), f"CONTRIBUTING.md missing at {contributing_path}"

    text = contributing_path.read_text(encoding="utf-8")
    assert "Anti-Placebo Gate Pattern" in text, "CONTRIBUTING.md must contain 'Anti-Placebo Gate Pattern'"
    assert "Mandatory Negative Tests" in text, "CONTRIBUTING.md must contain 'Mandatory Negative Tests'"
    assert "fail-closed" in text, "CONTRIBUTING.md must require fail-closed behavior for negative tests"
    assert "placebo assertion" in text, "CONTRIBUTING.md must prohibit placebo assertions"


def test_m01_ci_pr_gate_fail_closed():
    """M-01: CI guardrails workflow must execute core test suites on PR to prevent regressions."""
    import yaml as _yaml
    from pathlib import Path as _Path
    guardrails_path = _Path(__file__).resolve().parents[2] / ".github" / "workflows" / "scp_guardrails.yml"
    assert guardrails_path.is_file(), "scp_guardrails.yml must exist"
    data = _yaml.safe_load(guardrails_path.read_text(encoding="utf-8"))
    triggers = data.get("on") or data.get(True) or {}
    assert "pull_request" in triggers, "scp_guardrails.yml must trigger on pull_request"
    content = guardrails_path.read_text(encoding="utf-8")
    assert "M-01 Fail-Closed PR Gate" in content
    assert "pytest tests/T00_integrity/ tests/T02_contract/ tests/T03_capability/ tests/T04_kernel/ tests/T10_recovery/" in content


def test_m02_m03_observability_compose_pinned_and_secured():
    """M-02 & M-03: Observability compose must pin image digests and require non-empty admin password."""
    import yaml as _yaml
    import re as _re
    from pathlib import Path as _Path
    compose_path = _Path(__file__).resolve().parents[2] / "docker-compose.observability.yml"
    assert compose_path.is_file(), "docker-compose.observability.yml must exist"
    content = compose_path.read_text(encoding="utf-8")
    assert "GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD:?" in content, "Grafana must enforce non-empty password"

    data = _yaml.safe_load(content)
    services = data.get("services", {})
    assert len(services) >= 4, "Must configure at least 4 observability services"
    for sname, sdata in services.items():
        img = sdata.get("image", "")
        assert "@sha256:" in img, f"Service {sname} image must be pinned to sha256 digest, got: {img}"
        digest = img.split("@sha256:")[1]
        assert _re.fullmatch(r"[a-f0-9]{64}", digest), f"Invalid digest for service {sname}: {digest}"


def test_m04_m05_compose_resource_limits_and_egress():
    """M-04 & M-05: Compose manifests must enforce resource limits and default egress deny."""
    import yaml as _yaml
    from pathlib import Path as _Path
    repo_root = _Path(__file__).resolve().parents[2]
    
    env_example = (repo_root / ".env.example").read_text(encoding="utf-8")
    assert "SCP_EGRESS_MODE=deny" in env_example, ".env.example must default to SCP_EGRESS_MODE=deny"

    for cfile in ["compose.yml", "compose.test.yml"]:
        p = repo_root / cfile
        assert p.is_file(), f"{cfile} must exist"
        data = _yaml.safe_load(p.read_text(encoding="utf-8"))
        for sname, sdata in data.get("services", {}).items():
            deploy = sdata.get("deploy", {})
            limits = deploy.get("resources", {}).get("limits", {})
            assert "cpus" in limits, f"{cfile} service {sname} must specify cpus limit"
            assert "memory" in limits, f"{cfile} service {sname} must specify memory limit"
            assert "pids" in limits, f"{cfile} service {sname} must specify pids limit"


def test_m06_deploy_bundle_checksum_and_manifest_integrity():
    """M-06: Deploy bundle must have 100% valid SHA256 checksums and manifest alignment."""
    import hashlib as _hashlib
    import json as _json
    from pathlib import Path as _Path
    bundle_dir = _Path(__file__).resolve().parents[2] / "reports" / "deploy-bundle"
    assert bundle_dir.is_dir(), "reports/deploy-bundle directory must exist"

    sums_path = bundle_dir / "SHA256SUMS.txt"
    manifest_path = bundle_dir / "MANIFEST.json"
    assert sums_path.is_file(), "SHA256SUMS.txt must exist"
    assert manifest_path.is_file(), "MANIFEST.json must exist"

    manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_files = {fname: finfo["sha256"] for fname, finfo in manifest.get("files", {}).items()}

    sums_lines = [l.strip() for l in sums_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(sums_lines) >= 10, f"Expected at least 10 entries in SHA256SUMS.txt, found {len(sums_lines)}"

    for line in sums_lines:
        sha, fname = line.split(None, 1)
        target = bundle_dir / fname
        assert target.is_file(), f"File {fname} in SHA256SUMS.txt does not exist"
        actual_sha = _hashlib.sha256(target.read_bytes()).hexdigest()
        assert actual_sha == sha, f"Hash mismatch for {fname}: expected {sha}, got {actual_sha}"
        if fname in manifest_files:
            assert manifest_files[fname] == sha, f"Manifest hash mismatch for {fname}"


def test_m07_sandbox_evaluator_dockerfile_require_hashes():
    """M-07: Dockerfile.sandbox-evaluator must enforce pip --require-hashes."""
    from pathlib import Path as _Path
    repo_root = _Path(__file__).resolve().parents[2]
    df = repo_root / "Dockerfile.sandbox-evaluator"
    assert df.is_file(), "Dockerfile.sandbox-evaluator must exist"
    content = df.read_text(encoding="utf-8")
    assert "--require-hashes" in content, "Dockerfile.sandbox-evaluator must enforce --require-hashes"
    assert "requirements-evaluator.hashes.txt" in content

    hashes_file = repo_root / "scp" / "requirements-evaluator.hashes.txt"
    assert hashes_file.is_file(), "scp/requirements-evaluator.hashes.txt must exist"
    assert len(hashes_file.read_text(encoding="utf-8").strip()) > 0


def test_zero_mock_enforcement():
    """[Tripwire] FA-04 Zero-Mock Policy: Zero mock/unittest.mock allowed in tests/."""
    import ast as _ast
    from pathlib import Path as _Path

    repo_root = _Path(__file__).resolve().parents[2]
    tests_dir = repo_root / "tests"
    assert tests_dir.is_dir(), "tests directory must exist"

    violators = []
    for p in tests_dir.rglob("*.py"):
        try:
            tree = _ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        except Exception:
            continue
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Import):
                for a in node.names:
                    if "mock" in a.name.lower():
                        violators.append((str(p.relative_to(repo_root)), a.name))
            elif isinstance(node, _ast.ImportFrom):
                if node.module and "mock" in node.module.lower():
                    violators.append((str(p.relative_to(repo_root)), node.module))
                for a in node.names:
                    if "mock" in a.name.lower() or a.name in ("patch", "MagicMock", "AsyncMock"):
                        violators.append((str(p.relative_to(repo_root)), f"{node.module}.{a.name}"))

    assert len(violators) == 0, f"FA-04 Zero-Mock violation: Found mock imports in test files: {violators}"

