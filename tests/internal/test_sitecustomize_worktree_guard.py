"""Guard: the dev ``sitecustomize`` must import ``scp``/``tests`` of the checkout being run.

Template under test: ``tools/dev/sitecustomize_scp.py`` (the venv file
``<venv>/Lib/site-packages/sitecustomize.py`` is a byte copy of it).

Bug pinned here: the legacy venv file always put the main tree first and imported
``tests.pg_hermetic_adapter`` from it, so a script run BY PATH inside a git
worktree (``<main>/.claude/worktrees/<x>/tools/foo.py``) imported the MAIN tree's
``scp`` and adapter. These tests build a fake main tree with a fake worktree
nested inside it (same layout as the real repo; ``tests/`` is a namespace
package like the real one), install the template as ``sitecustomize`` through
``PYTHONPATH`` and run real interpreters. No mocks: every assertion reads what a
real child interpreter imported (``__file__``/marker) and what the fake adapter's
``install_if_needed`` physically wrote to disk.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "tools" / "dev" / "sitecustomize_scp.py"

# Byte-exact copy of the legacy venv sitecustomize.py (sha256 pinned below).
LEGACY_SITECUSTOMIZE = (
    "import sys\n"
    'if "d:\\\\scp" not in sys.path:\n'
    '    sys.path.insert(0, "d:\\\\scp")\n'
    "try:\n"
    "    from tests.pg_hermetic_adapter import install_if_needed\n"
    "    install_if_needed()\n"
    "except Exception:\n"
    "    pass\n"
)
LEGACY_SHA256 = "08272f92501e8f026f1e9c3d850eaddaed9b7e49f8d50b42ab4a7f030f3c8bf0"
LEGACY_MAIN_LITERAL = '"d:\\\\scp"'

ADAPTER_OK = (
    "import os\n"
    "MARKER = {marker!r}\n"
    "def install_if_needed():\n"
    "    with open(os.environ['SCP_GUARD_ADAPTER_LOG'], 'a', encoding='utf-8') as fh:\n"
    "        fh.write(MARKER + '\\n')\n"
)
ADAPTER_BOOM = (
    "def install_if_needed():\n"
    "    raise RuntimeError('guard-boom-from-{marker}')\n"
)
PROBE = (
    "import json, sys\n"
    "import scp\n"
    "adapter = sys.modules.get('tests.pg_hermetic_adapter')\n"
    "print(json.dumps({'scp_marker': scp.MARKER, 'scp_file': scp.__file__,\n"
    "                  'adapter_file': getattr(adapter, '__file__', None),\n"
    "                  'sys_path': [p for p in sys.path if isinstance(p, str)]}))\n"
)
SCRUBBED_ENV = ("PYTHONPATH", "PYTHONSAFEPATH", "PYTHONSTARTUP", "PYTHONINSPECT", "SCP_DEV_MAIN_ROOT")


def _load_template():
    spec = importlib.util.spec_from_file_location("scp_dev_sitecustomize_under_test", TEMPLATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _norm(path) -> str:
    return os.path.normcase(os.path.realpath(str(path)))


def _make_checkout(root: Path, marker: str, adapter: str | None = "ok") -> Path:
    (root / "scp").mkdir(parents=True)
    (root / "scp" / "__init__.py").write_text(f"MARKER = {marker!r}\n", encoding="utf-8")
    (root / "tests").mkdir()  # namespace package, exactly like the real repo
    if adapter == "ok":
        (root / "tests" / "pg_hermetic_adapter.py").write_text(ADAPTER_OK.format(marker=marker), encoding="utf-8")
    elif adapter == "boom":
        (root / "tests" / "pg_hermetic_adapter.py").write_text(ADAPTER_BOOM.format(marker=marker), encoding="utf-8")
    (root / "tools").mkdir()
    (root / "tools" / "probe.py").write_text(PROBE, encoding="utf-8")
    return root


@pytest.fixture
def layout(tmp_path):
    main = _make_checkout(tmp_path / "main", "main")
    wt = _make_checkout(main / ".claude" / "worktrees" / "wt", "wt")
    outside = tmp_path / "outside"
    (outside / "tools").mkdir(parents=True)
    (outside / "tools" / "probe.py").write_text(PROBE, encoding="utf-8")
    site_new = tmp_path / "site_new"
    site_new.mkdir()
    (site_new / "sitecustomize.py").write_bytes(TEMPLATE.read_bytes())
    site_legacy = tmp_path / "site_legacy"
    site_legacy.mkdir()
    legacy = LEGACY_SITECUSTOMIZE
    assert legacy.count(LEGACY_MAIN_LITERAL) == 2, "negative control lost its main-root literal"
    (site_legacy / "sitecustomize.py").write_text(
        legacy.replace(LEGACY_MAIN_LITERAL, repr(str(main))), encoding="utf-8"
    )
    tpl = _load_template()
    assert tpl._find_checkout(str(outside)) is None, (
        f"harness precondition: {outside} must not live inside an SCP checkout (basetemp inside a repo?)"
    )
    return {
        "main": main,
        "wt": wt,
        "outside": outside,
        "site_new": site_new,
        "site_legacy": site_legacy,
        "log": tmp_path / "adapter.log",
    }


def _run(lay, site_dir: Path, args: list[str], cwd: Path):
    env = {k: v for k, v in os.environ.items() if k not in SCRUBBED_ENV}
    env["PYTHONPATH"] = str(site_dir)
    env["SCP_DEV_MAIN_ROOT"] = str(lay["main"])
    env["SCP_GUARD_ADAPTER_LOG"] = str(lay["log"])
    if lay["log"].exists():
        lay["log"].unlink()
    proc = subprocess.run(
        [sys.executable, *args], cwd=str(cwd), env=env, capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, f"child failed rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert lines, f"probe printed nothing; stderr={proc.stderr}"
    result = json.loads(lines[-1])
    adapter_log = lay["log"].read_text(encoding="utf-8").split() if lay["log"].exists() else []
    return result, adapter_log, proc.stderr


def _assert_from(result, adapter_log, root: Path, marker: str):
    assert result["scp_marker"] == marker, result
    assert _norm(result["scp_file"]) == _norm(root / "scp" / "__init__.py"), result
    assert result["adapter_file"] is not None, "adapter was never imported at startup"
    assert _norm(result["adapter_file"]) == _norm(root / "tests" / "pg_hermetic_adapter.py"), result
    assert adapter_log == [marker], f"install_if_needed side effect: {adapter_log!r}"


# ---------------------------------------------------------------- end-to-end (real child interpreters)


def test_a_worktree_script_with_worktree_cwd_imports_worktree(layout):
    wt = layout["wt"]
    result, log, _ = _run(layout, layout["site_new"], [str(wt / "tools" / "probe.py")], cwd=wt)
    _assert_from(result, log, wt, "wt")
    hits = [p for p in result["sys_path"] if p and _norm(p) == _norm(wt)]
    assert len(hits) == 1, f"worktree root must be on sys.path exactly once: {result['sys_path']}"
    assert all(_norm(p) != _norm(layout["main"]) for p in result["sys_path"] if p), result["sys_path"]


def test_b_worktree_script_with_outside_cwd_still_imports_worktree(layout):
    wt = layout["wt"]
    result, log, _ = _run(layout, layout["site_new"], [str(wt / "tools" / "probe.py")], cwd=layout["outside"])
    _assert_from(result, log, wt, "wt")


def test_b2_relative_script_path_resolves_against_cwd(layout):
    wt = layout["wt"]
    result, log, _ = _run(layout, layout["site_new"], [os.path.join("tools", "probe.py")], cwd=wt)
    _assert_from(result, log, wt, "wt")


def test_c_main_script_imports_main(layout):
    main = layout["main"]
    result, log, _ = _run(layout, layout["site_new"], [str(main / "tools" / "probe.py")], cwd=layout["outside"])
    _assert_from(result, log, main, "main")


def test_c2_main_script_wins_over_worktree_cwd(layout):
    main = layout["main"]
    result, log, _ = _run(layout, layout["site_new"], [str(main / "tools" / "probe.py")], cwd=layout["wt"])
    _assert_from(result, log, main, "main")


def test_d_script_outside_any_checkout_falls_back_to_main(layout):
    result, log, _ = _run(
        layout, layout["site_new"], [str(layout["outside"] / "tools" / "probe.py")], cwd=layout["outside"]
    )
    _assert_from(result, log, layout["main"], "main")


def test_d2_dash_c_uses_cwd_checkout_for_startup_adapter(layout):
    wt = layout["wt"]
    result, log, _ = _run(layout, layout["site_new"], ["-c", PROBE], cwd=wt)
    _assert_from(result, log, wt, "wt")


def test_e_negative_control_legacy_file_leaks_main_tree_into_worktree_run(layout):
    """Anti-placebo: the same harness on the legacy file must observe the bug."""
    assert hashlib.sha256(LEGACY_SITECUSTOMIZE.encode("utf-8")).hexdigest() == LEGACY_SHA256
    wt = layout["wt"]
    result, log, _ = _run(layout, layout["site_legacy"], [str(wt / "tools" / "probe.py")], cwd=wt)
    _assert_from(result, log, layout["main"], "main")
    assert result["scp_marker"] != "wt"


def test_adapter_failure_is_reported_on_stderr_and_not_fatal(layout, tmp_path):
    broken = _make_checkout(tmp_path / "broken", "broken", adapter="boom")
    result, log, stderr = _run(layout, layout["site_new"], [str(broken / "tools" / "probe.py")], cwd=broken)
    assert result["scp_marker"] == "broken"
    assert _norm(result["scp_file"]) == _norm(broken / "scp" / "__init__.py")
    assert log == []
    assert "[scp-sitecustomize]" in stderr and "guard-boom-from-broken" in stderr, stderr


def test_checkout_without_adapter_installs_nothing_and_stays_quiet(layout, tmp_path):
    bare = _make_checkout(tmp_path / "bare", "bare", adapter=None)
    result, log, stderr = _run(layout, layout["site_new"], [str(bare / "tools" / "probe.py")], cwd=bare)
    assert result["scp_marker"] == "bare"
    assert _norm(result["scp_file"]) == _norm(bare / "scp" / "__init__.py")
    assert result["adapter_file"] is None, "main tree adapter must not leak into another checkout"
    assert log == []
    assert "[scp-sitecustomize]" not in stderr, stderr


# ---------------------------------------------------------------- unit: _choose_root / _main_root


def test_choose_root_prefers_nearest_checkout_for_nested_worktree(layout):
    tpl = _load_template()
    wt, main = layout["wt"], layout["main"]
    chosen = tpl._choose_root(str(wt / "tools" / "probe.py"), str(layout["outside"]), str(main))
    assert _norm(chosen) == _norm(wt)


@pytest.mark.parametrize("argv0", ["", "-c", "-m", "-"])
def test_choose_root_non_path_argv0_uses_cwd_checkout(layout, argv0):
    tpl = _load_template()
    chosen = tpl._choose_root(argv0, str(layout["wt"] / "tools"), str(layout["main"]))
    assert _norm(chosen) == _norm(layout["wt"])


def test_choose_root_ignores_launcher_inside_interpreter_prefix(layout):
    tpl = _load_template()
    main, wt = layout["main"], layout["wt"]
    venv = main / ".venv"
    (venv / "Scripts").mkdir(parents=True)
    launcher = venv / "Scripts" / "pytest.exe"
    launcher.write_bytes(b"MZ")
    with_exclusion = tpl._choose_root(str(launcher), str(wt), str(main), (str(venv),))
    without_exclusion = tpl._choose_root(str(launcher), str(wt), str(main), ())
    assert _norm(with_exclusion) == _norm(wt)
    assert _norm(without_exclusion) == _norm(main)


def test_choose_root_falls_back_to_main_root_or_none(layout):
    tpl = _load_template()
    outside = layout["outside"]
    probe = str(outside / "tools" / "probe.py")
    assert _norm(tpl._choose_root(probe, str(outside), str(layout["main"]))) == _norm(layout["main"])
    assert tpl._choose_root(probe, str(outside), None) is None
    assert tpl._choose_root("-c", None, None) is None


def test_checkout_marker_requires_both_scp_package_and_tests_dir(tmp_path):
    tpl = _load_template()
    only_scp = tmp_path / "only_scp"
    (only_scp / "scp").mkdir(parents=True)
    (only_scp / "scp" / "__init__.py").write_text("", encoding="utf-8")
    only_tests = tmp_path / "only_tests"
    (only_tests / "tests").mkdir(parents=True)
    (only_tests / "scp").mkdir()  # scp dir without __init__.py is not the package
    both = _make_checkout(tmp_path / "both", "both")
    assert tpl._find_checkout(str(only_scp)) is None
    assert tpl._find_checkout(str(only_tests)) is None
    assert _norm(tpl._find_checkout(str(both / "tools"))) == _norm(both)


def test_main_root_env_override_then_prefix_checkout(layout):
    tpl = _load_template()
    main = layout["main"]
    venv = main / ".venv"
    venv.mkdir()
    other = layout["outside"]
    assert _norm(tpl._main_root({"SCP_DEV_MAIN_ROOT": str(other)}, str(venv))) == _norm(other)
    assert _norm(tpl._main_root({}, str(venv))) == _norm(main)
    assert tpl._main_root({}, str(other)) is None
    assert tpl._main_root({}, None) is None


def test_template_has_no_side_effects_when_not_imported_as_sitecustomize(layout):
    before = list(sys.path)
    modules_before = "tests.pg_hermetic_adapter" in sys.modules
    _load_template()
    assert sys.path == before
    assert ("tests.pg_hermetic_adapter" in sys.modules) == modules_before
