"""[REPR-INJECTION-FIX + ENV-LEAK-FIX] Regression tests for evidence_replay.

(a) build_characterization_test used to embed the probe-observed repr TEXT
    verbatim into generated pytest source. A hostile candidate controls that
    text via a custom __repr__ (arbitrary quotes/newlines/expression text),
    so the generated test file could break out of the assert and execute
    injected code (e.g. ``assert f(1) == 0 or __import__('os').system(...) or 0``).
    The repr is now pinned as an ESCAPED STRING LITERAL via repr(result_repr)
    with a string-match assert; args_expr must parse as a pure literal list
    or the record is not pinned (fail-closed).

(b) run_test() used to pass the FULL host environment to the B/S/G subprocess
    while the probe legs already used _minimal_probe_env() — host env leaked
    into replay subprocesses. run_test now uses the same minimal env.
"""
import ast as _ast
import sys
from unittest.mock import MagicMock, patch

from scp.autofix.evidence_replay import EvidenceReplay, build_characterization_test

_FORBIDDEN_CALL_NAMES = {"open", "eval", "exec", "compile", "__import__", "input", "breakpoint"}


def _record(result_repr, args_expr="1", name="f") -> dict:
    return {
        "callable": name,
        "exercised": True,
        "pinnable": True,
        "result_repr": result_repr,
        "error": None,
        "async": False,
        "args_expr": args_expr,
    }


def _assert_no_injection(generated_source: str, module_stem: str = "cand_mod") -> None:
    tree = _ast.parse(generated_source)
    allowed_imports = {"asyncio", "os", "sys", module_stem}
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Import):
            # The harness only imports asyncio/os/sys + the module under test;
            # anything else is injection.
            for alias in node.names:
                assert alias.name.split(".")[0] in allowed_imports, (
                    f"injected import: {alias.name}"
                )
        elif isinstance(node, _ast.ImportFrom):
            module = getattr(node, "module", None)
            assert module in allowed_imports, f"injected import-from: {module}"
        if isinstance(node, _ast.Call) and isinstance(node.func, _ast.Name):
            assert node.func.id not in _FORBIDDEN_CALL_NAMES, (
                f"injected call to {node.func.id} in generated test"
            )


class TestCharacterizationTestInjectionHardening:
    def test_expression_breakout_cannot_inject_calls(self):
        # Parses as valid Python when embedded verbatim (the worst case):
        # `assert cand_mod.f(1) == 0 or __import__('os').system('PWNED') or 0`
        hostile = "0 or __import__('os').system('PWNED') or 0"
        src = build_characterization_test([_record(hostile)], "cand_mod")
        assert src is not None
        _assert_no_injection(src)
        assert "assert repr(cand_mod.f(1)) == " in src

    def test_quote_and_newline_breakout_cannot_break_the_file(self):
        hostile = "' or open('secret.txt') and ('\nimport subprocess\nsubprocess.run('PWNED')\n#"
        src = build_characterization_test([_record(hostile)], "cand_mod")
        assert src is not None
        # BEFORE the fix this payload both broke syntax AND (payload variants)
        # injected executable statements; now it must parse as pure literals.
        _assert_no_injection(src)

    def test_multi_line_repr_is_pinned_as_single_escaped_literal(self):
        hostile = "line1\nline2\nline3"
        src = build_characterization_test([_record(hostile)], "cand_mod")
        assert src is not None
        body_lines = [ln for ln in src.splitlines() if "assert repr(" in ln]
        assert len(body_lines) == 1, "pinned repr must be one escaped literal line"
        _assert_no_injection(src)

    def test_honest_repr_still_pins_expected_value(self):
        src = build_characterization_test([_record("42")], "cand_mod")
        assert src is not None
        assert "assert repr(cand_mod.f(1)) == '42'" in src
        _assert_no_injection(src)

    def test_hostile_args_expr_record_is_not_pinned(self):
        bad = _record("42", args_expr="0) if __import__('os').system('PWNED') else (0")
        assert build_characterization_test([bad], "cand_mod") is None

    def test_nothing_pinnable_returns_none_fail_closed(self):
        assert build_characterization_test([], "cand_mod") is None


class TestRunTestMinimalEnv:
    def test_run_test_uses_minimal_probe_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SCP_X_REGRESSION_SENTINEL_9QK", "leak-me")
        replay = EvidenceReplay(working_dir=tmp_path)
        captured = {}
        proc = MagicMock(returncode=0, stdout="", stderr="")

        def fake_run(argv, **kwargs):
            captured.update(kwargs)
            return proc

        with patch("subprocess.run", side_effect=fake_run):
            ok, _snippet = replay.run_test(["python", "-c", "pass"])

        assert ok is True
        assert "env" in captured, "run_test must pass an explicit (minimal) env"
        assert "SCP_X_REGRESSION_SENTINEL_9QK" not in captured["env"], (
            "run_test leaked the full host env into the replay subprocess"
        )
        assert "PATH" in captured["env"], "minimal env must keep process basics"

    def test_run_test_real_execution_still_works_with_minimal_env(self, tmp_path):
        replay = EvidenceReplay(working_dir=tmp_path)
        ok, _ = replay.run_test([sys.executable, "-c", "import sys; sys.exit(0)"])
        assert ok is True
        nok, _ = replay.run_test([sys.executable, "-c", "import sys; sys.exit(3)"])
        assert nok is False
