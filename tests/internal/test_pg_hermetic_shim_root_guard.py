"""Guard: PG hermetic shims must resolve to the checkout (and interpreter) that installed them.

Code under test: ``tests/pg_hermetic_adapter.py`` (``SHIM_BIN_DIR`` and
``setup_docker_and_dump_shims``).

Bug pinned here: the adapter wrote ``docker_shim.py``, ``sitecustomize.py``,
``docker.cmd``, ``pg_dump.cmd`` and the POSIX shims ONCE (``if not exists``) into
ONE shared ``<temp>/scp_hermetic_bin``. Whichever checkout wrote first (in
practice the main tree) stayed baked in, and because that dir is put first on
``PYTHONPATH`` its ``sitecustomize.py`` shadows the venv one: PG/chaos
subprocesses of a worktree imported the MAIN tree's ``scp``/adapter.

Isolation: every test redirects the temp dir (``tempfile.tempdir`` in-process,
TMP/TEMP/TMPDIR for children) into ``tmp_path``. The real shared dir is only
read, by an autouse tripwire that asserts it is untouched. Fake checkouts get a
byte copy of the real adapter to GENERATE the shims, then a probe stub becomes
the IMPORT TARGET, so real child interpreters (``docker.exe``/``.cmd``/POSIX
launcher/``python``) report which checkout a shim reached, without touching any
PG cluster dir.
"""
from __future__ import annotations

import hashlib
import importlib.util
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ADAPTER = REPO / "tests" / "pg_hermetic_adapter.py"
LEGACY_SHARED_DIR_NAME = "scp_hermetic_bin"
# Read-only tripwire target, captured before any test redirects the temp dir.
REAL_LEGACY_DIR = Path(tempfile.gettempdir()) / LEGACY_SHARED_DIR_NAME
TEXT_SHIMS = ("docker_shim.py", "sitecustomize.py", "docker.cmd", "pg_dump.cmd", "docker", "pg_dump")
ROOT_BOUND_SHIMS = ("docker_shim.py", "sitecustomize.py")
SCRUBBED_ENV = ("PYTHONPATH", "PYTHONSAFEPATH", "PYTHONSTARTUP", "PYTHONINSPECT", "PYTHONHOME", "SCP_DEV_MAIN_ROOT")
_SEQ = itertools.count()

# Import target the shims reach in child interpreters. It records which checkout
# it lives in; ``CLUSTER_DIR`` points at an empty per-test dir (pg_dump path).
PROBE_ADAPTER = (
    "import os\n"
    "from pathlib import Path\n"
    "MARKER = {marker!r}\n"
    "CLUSTER_DIR = Path({cluster!r})\n"
    "\n"
    "def _log(event):\n"
    "    with open(os.environ['SCP_SHIM_GUARD_LOG'], 'a', encoding='utf-8') as fh:\n"
    "        fh.write(MARKER + ':' + event + '\\n')\n"
    "\n"
    "class _ProbeCluster:\n"
    "    def record_restart(self):\n"
    "        _log('restart')\n"
    "\n"
    "_CLUSTER = _ProbeCluster()\n"
    "\n"
    "def install_if_needed():\n"
    "    _log('install_if_needed')\n"
    "\n"
    "_log('import')\n"
)
PROBE_SCRIPT = (
    "import json, sys\n"
    "mod = sys.modules.get('tests.pg_hermetic_adapter')\n"
    "print(json.dumps({'adapter_file': getattr(mod, '__file__', None), 'marker': getattr(mod, 'MARKER', None)}))\n"
)


def _norm(path) -> str:
    return os.path.normcase(os.path.realpath(str(path)))


def _stat_snapshot(directory: Path):
    if not directory.is_dir():
        return None
    return {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in directory.iterdir() if p.is_file()}


def _fingerprint(path: Path):
    st = path.stat()
    return (st.st_ino, st.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())


@pytest.fixture(autouse=True)
def _real_shared_shim_dir_untouched():
    before = _stat_snapshot(REAL_LEGACY_DIR)
    yield
    if before is not None:
        assert _stat_snapshot(REAL_LEGACY_DIR) == before, f"a guard test wrote the real shared dir {REAL_LEGACY_DIR}"


@pytest.fixture
def temp_dir(tmp_path, monkeypatch):
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    for var in ("TMP", "TEMP", "TMPDIR"):
        monkeypatch.setenv(var, str(temp))
    assert Path(tempfile.gettempdir()) == temp, "temp redirection failed: refusing to touch the real temp dir"
    return temp


def _make_root(base: Path, marker: str) -> Path:
    root = base / f"checkout_{marker}"
    (root / "scp").mkdir(parents=True)
    (root / "scp" / "__init__.py").write_text(f"MARKER = {marker!r}\n", encoding="utf-8")
    (root / "tests").mkdir()  # namespace package, exactly like the real repo
    shutil.copyfile(ADAPTER, root / "tests" / "pg_hermetic_adapter.py")
    return root.resolve()


def _load_adapter(path: Path):
    spec = importlib.util.spec_from_file_location(f"_shim_guard_adapter_{next(_SEQ)}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _install(root: Path):
    """Generate shims with the REAL adapter code located in ``root``."""
    module = _load_adapter(root / "tests" / "pg_hermetic_adapter.py")
    shim_dir = module.setup_docker_and_dump_shims()
    assert shim_dir == module.SHIM_BIN_DIR
    return module, shim_dir


def _arm_probe(root: Path, marker: str, cluster: Path) -> None:
    """Swap the generator copy for the probe stub (the import target children reach)."""
    cluster.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(root / "tests" / "__pycache__", ignore_errors=True)
    (root / "tests" / "pg_hermetic_adapter.py").write_text(
        PROBE_ADAPTER.format(marker=marker, cluster=str(cluster)), encoding="utf-8"
    )


def _assert_bound_to(shim_dir: Path, root: Path, foreign: Path) -> None:
    for name in ROOT_BOUND_SHIMS:
        text = (shim_dir / name).read_text(encoding="utf-8")
        assert repr(str(root)) in text, f"{shim_dir / name} does not pin {root}:\n{text[:300]}"
        assert repr(str(foreign)) not in text, f"{shim_dir / name} still pins foreign checkout {foreign}"
    for name in ("docker.cmd", "pg_dump.cmd"):
        text = (shim_dir / name).read_text(encoding="utf-8")
        assert f'"{sys.executable}"' in text and "%~dp0docker_shim.py" in text, (name, text)
    for name in ("docker", "pg_dump"):
        text = (shim_dir / name).read_text(encoding="utf-8")
        assert f'"{sys.executable}" "{shim_dir / "docker_shim.py"}"' in text, (name, text)


def _docker_launchers(shim_dir: Path):
    launchers = [("python", [sys.executable, shim_dir / "docker_shim.py"])]
    if os.name == "nt":
        launchers.append(("docker.cmd", [shim_dir / "docker.cmd"]))
        if (shim_dir / "docker.exe").exists():
            launchers.append(("docker.exe", [shim_dir / "docker.exe"]))
    else:
        launchers.append(("posix docker", [shim_dir / "docker"]))
    return launchers


def _pg_dump_launchers(shim_dir: Path):
    launchers = [("python", [sys.executable, shim_dir / "docker_shim.py", "exec", "scp-pg-test", "pg_dump"])]
    if os.name == "nt":
        launchers.append(("pg_dump.cmd", [shim_dir / "pg_dump.cmd"]))
        if (shim_dir / "pg_dump.exe").exists():
            launchers.append(("pg_dump.exe", [shim_dir / "pg_dump.exe"]))
    else:
        launchers.append(("posix pg_dump", [shim_dir / "pg_dump"]))
    return launchers


def _run(lay, argv, pythonpath: str):
    log = lay["log"]
    if log.exists():
        log.unlink()
    env = {k: v for k, v in os.environ.items() if k not in SCRUBBED_ENV}
    temp = str(lay["temp"])
    env.update(TMP=temp, TEMP=temp, TMPDIR=temp, PYTHONPATH=pythonpath, SCP_SHIM_GUARD_LOG=str(log))
    proc = subprocess.run(
        [str(a) for a in argv], cwd=str(lay["outside"]), env=env, capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, f"{argv} rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    events = log.read_text(encoding="utf-8").split() if log.exists() else []
    return proc, events


@pytest.fixture
def layout(temp_dir, tmp_path):
    lay = {"temp": temp_dir, "log": tmp_path / "shim_guard.log", "outside": tmp_path / "outside"}
    lay["outside"].mkdir()
    neutral = tmp_path / "neutral_site"  # empty sitecustomize: shadows the venv one in children
    neutral.mkdir()
    (neutral / "sitecustomize.py").write_text("", encoding="utf-8")
    lay["neutral"] = str(neutral)
    roots = {m: _make_root(tmp_path, m) for m in ("A", "B")}
    for marker, root in roots.items():
        _, shim_dir = _install(root)
        lay[marker] = {"root": root, "dir": shim_dir}
    for marker, root in roots.items():
        _arm_probe(root, marker, tmp_path / f"probe_cluster_{marker}")
    return lay


# ---------------------------------------------------------------- shim dir identity


def test_each_checkout_gets_its_own_shim_dir_never_the_legacy_shared_one(layout):
    a, b = layout["A"], layout["B"]
    assert a["dir"] != b["dir"], f"two checkouts share one shim dir (first writer wins): {a['dir']}"
    for side in (a, b):
        assert side["dir"].parent == layout["temp"], side["dir"]
        assert side["dir"].name != LEGACY_SHARED_DIR_NAME, "must never write the legacy shared shim dir"


def test_shim_dir_is_deterministic_per_checkout_and_keyed_by_interpreter(temp_dir, tmp_path, monkeypatch):
    adapter = _make_root(tmp_path, "A") / "tests" / "pg_hermetic_adapter.py"
    first = _load_adapter(adapter).SHIM_BIN_DIR
    assert _load_adapter(adapter).SHIM_BIN_DIR == first, "same checkout + interpreter must reuse its dir"
    monkeypatch.setattr(sys, "executable", str(tmp_path / "other-venv" / "Scripts" / "python.exe"))
    other = _load_adapter(adapter).SHIM_BIN_DIR
    assert other != first, "shims embed sys.executable: another interpreter needs its own dir"
    assert other.parent == temp_dir


# ---------------------------------------------------------------- generated content


def test_two_checkouts_shims_embed_their_own_root_and_interpreter(layout):
    _assert_bound_to(layout["A"]["dir"], layout["A"]["root"], layout["B"]["root"])
    _assert_bound_to(layout["B"]["dir"], layout["B"]["root"], layout["A"]["root"])


def test_this_checkout_installs_shims_bound_to_itself(temp_dir, tmp_path):
    module = _load_adapter(ADAPTER)  # the real file at its real location (main tree or worktree)
    shim_dir = module.setup_docker_and_dump_shims()
    assert shim_dir.parent == temp_dir and shim_dir.name != LEGACY_SHARED_DIR_NAME, shim_dir
    _assert_bound_to(shim_dir, REPO, foreign=tmp_path / "elsewhere")


# ---------------------------------------------------------------- real child interpreters


def test_docker_launchers_import_their_own_checkout(layout):
    """Each launcher alone (venv sitecustomize shadowed by an empty one) reaches its own checkout."""
    for me in ("A", "B"):
        for label, argv in _docker_launchers(layout[me]["dir"]):
            _, events = _run(layout, [*argv, "restart"], layout["neutral"])
            assert events == [f"{me}:import", f"{me}:restart"], f"{label} of checkout {me}: {events}"


def test_pg_dump_launchers_import_their_own_checkout(layout):
    for me in ("A", "B"):
        for label, argv in _pg_dump_launchers(layout[me]["dir"]):
            out = layout["outside"] / f"dump_{me}_{label.replace(' ', '_')}.sql"
            _, events = _run(layout, [*argv, "--file", out], layout["neutral"])
            assert events == [f"{me}:import"], f"{label} of checkout {me}: {events}"
            assert "PostgreSQL database dump" in out.read_text(encoding="utf-8"), label


def test_shim_sitecustomize_pins_child_interpreters_to_their_own_checkout(layout):
    """Real chain: install_hermetic_adapter puts ``<shim_dir>;<root>`` first on PYTHONPATH."""
    for me in ("A", "B"):
        root, shim_dir = layout[me]["root"], layout[me]["dir"]
        pythonpath = os.pathsep.join([str(shim_dir), str(root)])
        proc, events = _run(layout, [sys.executable, "-c", PROBE_SCRIPT], pythonpath)
        seen = json.loads(proc.stdout.strip().splitlines()[-1])
        assert seen["marker"] == me, f"child of checkout {me} imported adapter {seen}"
        assert _norm(seen["adapter_file"]) == _norm(root / "tests" / "pg_hermetic_adapter.py"), seen
        assert events == [f"{me}:import", f"{me}:install_if_needed"], events
        for label, argv in _docker_launchers(shim_dir):
            _, events = _run(layout, [*argv, "restart"], pythonpath)
            assert events == [f"{me}:import", f"{me}:install_if_needed", f"{me}:restart"], (label, me, events)


# ---------------------------------------------------------------- stale / legacy / idempotence


def test_stale_root_a_shims_are_rewritten_by_install_from_root_b(temp_dir, tmp_path):
    root_a, root_b = _make_root(tmp_path, "A"), _make_root(tmp_path, "B")
    _, dir_a = _install(root_a)
    mod_b = _load_adapter(root_b / "tests" / "pg_hermetic_adapter.py")
    dir_b = mod_b.SHIM_BIN_DIR
    if dir_b != dir_a:  # per-checkout layout: plant A's shims (foreign content) in B's own dir
        shutil.copytree(dir_a, dir_b, dirs_exist_ok=True)
    bogus = tmp_path / "deleted-venv" / "python.exe"
    (dir_b / "docker.cmd").write_text(f'@echo off\n"{bogus}" "%~dp0docker_shim.py" %*\n', encoding="utf-8")
    assert repr(str(root_a)) in (dir_b / "docker_shim.py").read_text(encoding="utf-8"), "precondition: stale"

    assert mod_b.setup_docker_and_dump_shims() == dir_b
    _assert_bound_to(dir_b, root_b, root_a)
    assert str(bogus) not in (dir_b / "docker.cmd").read_text(encoding="utf-8"), "stale interpreter kept"
    _assert_bound_to(dir_a, root_a, root_b)  # installing B never rewrote A's dir

    lay = {"temp": temp_dir, "log": tmp_path / "stale.log", "outside": tmp_path / "outside"}
    lay["outside"].mkdir()
    _arm_probe(root_a, "A", tmp_path / "cluster_a")
    _arm_probe(root_b, "B", tmp_path / "cluster_b")
    _, events = _run(lay, [sys.executable, dir_b / "docker_shim.py", "restart"], str(dir_b))
    assert events == ["B:import", "B:install_if_needed", "B:restart"], events


def test_legacy_shared_dir_holding_main_tree_shims_is_neither_used_nor_modified(temp_dir, tmp_path):
    """Model of the real ``<temp>/scp_hermetic_bin``, written once by the main tree."""
    root_main, root_wt = _make_root(tmp_path, "main"), _make_root(tmp_path, "wt")
    legacy = temp_dir / LEGACY_SHARED_DIR_NAME
    legacy.mkdir()
    pin_main = f"import sys\nif {str(root_main)!r} not in sys.path:\n    sys.path.insert(0, {str(root_main)!r})\n"
    for name in ROOT_BOUND_SHIMS:
        (legacy / name).write_text(pin_main, encoding="utf-8")
    before = {p.name: _fingerprint(p) for p in legacy.iterdir()}

    _, dir_wt = _install(root_wt)
    assert dir_wt != legacy, "worktree install reused the legacy shared dir"
    _assert_bound_to(dir_wt, root_wt, root_main)
    assert {p.name: _fingerprint(p) for p in legacy.iterdir()} == before, "legacy shared dir was modified"


def test_reinstall_from_same_checkout_leaves_identical_shims_untouched(temp_dir, tmp_path):
    module, shim_dir = _install(_make_root(tmp_path, "A"))
    before = {name: _fingerprint(shim_dir / name) for name in TEXT_SHIMS}
    assert module.setup_docker_and_dump_shims() == shim_dir
    after = {name: _fingerprint(shim_dir / name) for name in TEXT_SHIMS}
    assert after == before, "identical shims were rewritten (churn races concurrent readers)"
    assert not list(shim_dir.glob("*.tmp")), "temp files left behind"


def test_refused_replace_is_tolerated_only_when_target_already_holds_new_content(temp_dir, tmp_path, monkeypatch):
    """Windows refuses ``os.replace`` onto a file another process holds open."""
    module, shim_dir = _install(_make_root(tmp_path, "A"))
    target = shim_dir / "sitecustomize.py"
    good = target.read_text(encoding="utf-8")
    stale = "import sys  # stale shim from another checkout\n"
    real_replace = os.replace

    def concurrent_winner(src, dst, *a, **kw):  # same content landed first, then the replace is refused
        if Path(dst) == target:
            real_replace(src, dst)
            raise PermissionError(13, "simulated: target open in another process", str(dst))
        return real_replace(src, dst, *a, **kw)

    def refused(src, dst, *a, **kw):
        if Path(dst) == target:
            raise PermissionError(13, "simulated: target open in another process", str(dst))
        return real_replace(src, dst, *a, **kw)

    target.write_text(stale, encoding="utf-8")
    with monkeypatch.context() as m:
        m.setattr(os, "replace", concurrent_winner)
        module.setup_docker_and_dump_shims()
    assert target.read_text(encoding="utf-8") == good

    target.write_text(stale, encoding="utf-8")
    with monkeypatch.context() as m:
        m.setattr(os, "replace", refused)
        with pytest.raises(PermissionError):
            module.setup_docker_and_dump_shims()
    assert target.read_text(encoding="utf-8") == stale, "a refused replace must fail loud, never fake success"
    assert not list(shim_dir.glob("*.tmp")), "temp files left behind"
