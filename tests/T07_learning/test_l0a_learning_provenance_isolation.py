"""[L0a 2026-10-31] Cách ly dữ liệu test khỏi kho học SỐNG + provenance có cấu trúc.

Reality gốc (VERIFIED bởi checker khác lineage): ``data/failure_ledger.sqlite3`` 235/235 bản
ghi từ pytest/fixture, ``data/kb_evolve.sqlite`` 30/30 lesson từ pytest, ``ErrorStore`` ghi
``data/error_store.jsonl`` theo cwd và bỏ qua ``SCP_DATA_DIR``. Các test dưới đây dựng một
"kho runtime GIẢ" trong tmp (``SCP_DATA_DIR`` + cwd trỏ vào đó) để KHÔNG BAO GIỜ chạm
``data/`` thật — kể cả khi chạy trên commit cũ (đỏ-trước): đường mặc định cũ của
failure_ledger (``DEFAULT_DB_PATH``) cũng được trỏ vào kho giả.

Nhánh runtime được kiểm bằng TIẾN TRÌNH CON thật không có tín hiệu harness (không mock
provenance): đó chính là ngữ cảnh của server production.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _snapshot(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


@pytest.fixture
def fake_runtime(tmp_path, monkeypatch):
    """Kho runtime giả: cwd/data == SCP_DATA_DIR; đường mặc định cũ của ledger cũng trỏ vào đây."""
    root = tmp_path / "fake_runtime_root"
    data = root / "data"
    data.mkdir(parents=True)
    (data / "sentinel.txt").write_text("runtime store sentinel\n", encoding="utf-8")
    monkeypatch.chdir(root)
    monkeypatch.setenv("SCP_DATA_DIR", str(data))
    monkeypatch.setenv("SCP_ISOLATED_DATA_DIR", str(tmp_path / "isolated"))
    monkeypatch.delenv("SCP_PROVENANCE", raising=False)
    import scp.epistemic.failure_ledger as fl
    import scp.meta.kb_evolve as kb
    import scp.brain.error_store as es

    monkeypatch.setattr(fl, "DEFAULT_DB_PATH", data / "failure_ledger.sqlite3", raising=False)
    monkeypatch.setattr(fl, "_global_ledger", None)
    monkeypatch.setattr(kb, "_kb_store", None)
    monkeypatch.setattr(kb, "_scanner_evolver", None)
    monkeypatch.setattr(es, "_ERROR_STORE", None)
    return data


def _lesson(tag: str, bug_file: str = "scp/core/x.py"):
    from scp.meta.kb_evolve import Lesson

    return Lesson(lesson_id=f"les-{tag}", timestamp=1.0, bug_type="NullDereference",
                  bug_file=bug_file, bug_line=3, root_cause=f"root-{tag}", lesson=f"lesson-{tag}",
                  fix_pattern="", fix_verified=True, success_rate=0.9)


# ---------------------------------------------------------------------------
# 1. Writer mặc định trong tiến trình test KHÔNG chạm kho runtime
# ---------------------------------------------------------------------------

def test_default_error_store_in_test_process_leaves_runtime_store_unchanged(fake_runtime):
    before = _snapshot(fake_runtime)
    from scp.brain.error_store import ErrorStore

    store = ErrorStore()
    rec = store.add(question="What is the boiling point of water at sea level?", answer="x",
                    verdict="REFUTED", domain="falsification", error_type="v4_refutation")
    assert rec.get("id")
    store.add(question="   ", answer="", verdict="REFUTED")  # nhánh spam ⇒ rejected log
    assert _snapshot(fake_runtime) == before, "test process wrote into the runtime learning store"


def test_default_failure_ledger_in_test_process_leaves_runtime_store_unchanged(fake_runtime):
    before = _snapshot(fake_runtime)
    from scp.epistemic.failure_ledger import get_failure_ledger

    led = get_failure_ledger()
    led.record_failure("WHY_REJECTION", "scp/core/engine.py", verbatim_critique="patch removes guard")
    assert _snapshot(fake_runtime) == before, "test process wrote into the runtime failure ledger"


def test_default_kb_store_in_test_process_leaves_runtime_store_unchanged(fake_runtime):
    before = _snapshot(fake_runtime)
    from scp.meta.kb_evolve import get_kb_store

    assert get_kb_store().save_lesson(_lesson("t1")) is True
    assert _snapshot(fake_runtime) == before, "test process wrote into the runtime kb_evolve store"


def test_math_routing_relevance_lessons_share_isolated_store(fake_runtime):
    """math/routing/relevance lessons + refutations đi qua FalsificationEngine._get_error_store()."""
    before = _snapshot(fake_runtime)
    from scp.meta.falsification_engine import FalsificationEngine

    eng = FalsificationEngine()
    resp = eng.record_refutation(question="Was the scheduler deadline honored?", v4_verdict="PASS",
                                 reality="overran the deadline")
    assert resp["errorstore_grew"] is True
    assert _snapshot(fake_runtime) == before


# ---------------------------------------------------------------------------
# 2. Fail-closed: tiến trình KHÔNG phải runtime khởi động với SCP_DATA_DIR = kho runtime
#    (ngữ cảnh boot thật, tiến trình con) ⇒ ghi mặc định đi kho cách ly, ghi tường minh
#    vào kho runtime bị TỪ CHỐI. SCP_DATA_DIR do test tự monkeypatch về tmp_path KHÔNG
#    phải kho runtime (gốc runtime = <repo>/data + SCP_DATA_DIR lúc tiến trình khởi động).
# ---------------------------------------------------------------------------

_NONRUNTIME_SCRIPT = textwrap.dedent(
    r"""
    import json, os, sqlite3, sys
    from pathlib import Path
    data = Path(os.environ["SCP_DATA_DIR"])
    import scp as _scp_pkg
    import scp.epistemic.failure_ledger as fl
    if hasattr(fl, "DEFAULT_DB_PATH"):
        fl.DEFAULT_DB_PATH = data / "failure_ledger.sqlite3"   # commit cũ: không bao giờ chạm data/ thật
    from scp.brain.error_store import ErrorStore
    from scp.meta.kb_evolve import KBAccumulationStore, Lesson, get_kb_store
    from scp.meta.falsification_engine import FalsificationEngine
    out = {"scp_file": str(Path(_scp_pkg.__file__).resolve()), "pytest_loaded": "_pytest" in sys.modules,
           "refused": {}, "prov": {}}

    def lesson(tag):
        return Lesson(lesson_id="les-" + tag, timestamp=1.0, bug_type="NullDereference", bug_file="scp/a.py",
                      bug_line=1, root_cause="root-" + tag, lesson="l-" + tag, fix_pattern="",
                      fix_verified=True, success_rate=0.9)

    def attempt(name, fn):
        try:
            fn()
            out["refused"][name] = None
        except Exception as exc:
            out["refused"][name] = type(exc).__name__

    def prov_of(db, sql):
        try:
            with sqlite3.connect(db) as c:
                row = c.execute(sql).fetchone()
            return row[0] if row else None
        except Exception as exc:
            return "ERR:" + type(exc).__name__

    if sys.argv[1] == "misconfigured":
        attempt("default_es", lambda: ErrorStore().add(question="What is the capital city of Japan?",
                                                       answer="x", verdict="REFUTED"))
    else:
        rec = ErrorStore().add(question="What is the boiling point of water at sea level?", answer="x",
                               verdict="REFUTED")
        out["prov"]["es"] = rec.get("provenance")
        led = fl.get_failure_ledger()
        fid = led.record_failure("WHY_REJECTION", "scp/core/engine.py", verbatim_critique="probe crit")
        out["prov"]["fl"] = prov_of(led.db_path, "SELECT provenance FROM failure_records WHERE failure_id='%s'" % fid)
        kbs = get_kb_store()
        kbs.save_lesson(lesson("p"))
        out["prov"]["kb"] = prov_of(kbs.db_path, "SELECT provenance FROM lessons WHERE lesson_id='les-p'")
        FalsificationEngine().record_refutation(question="Was the scheduler deadline honored?",
                                                v4_verdict="PASS", reality="overran the deadline")
        attempt("explicit_es", lambda: ErrorStore(path=str(data / "error_store.jsonl")).add(
            question="What is the capital city of Japan?", answer="x", verdict="REFUTED"))
        attempt("explicit_fl", lambda: fl.FailureLedger(db_path=data / "failure_ledger.sqlite3").record_failure(
            "WHY_REJECTION", "scp/a.py", verbatim_critique="c"))
        attempt("explicit_kb", lambda: KBAccumulationStore(data_dir=str(data)).save_lesson(lesson("e")))
    print("RESULT=" + json.dumps(out))
    """
)


def _run_child(tmp_path: Path, data: Path, script_text: str, mode: str, extra_env: dict | None = None) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in {"PYTEST_CURRENT_TEST", "SCP_PROVENANCE"}}
    env["SCP_DATA_DIR"] = str(data)
    env["SCP_ISOLATED_DATA_DIR"] = str(tmp_path / "isolated")
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["SCP_DEV_MAIN_ROOT"] = str(REPO_ROOT)  # dev venv sitecustomize: import THIS checkout
    env.update(extra_env or {})
    script = tmp_path / f"child_{mode}.py"
    script.write_text(script_text, encoding="utf-8")
    proc = subprocess.run([sys.executable, "-X", "utf8", str(script), mode], cwd=str(data.parent),
                          env=env, capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr[-3000:]
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT=")][-1]
    res = json.loads(line[len("RESULT="):])
    assert Path(res["scp_file"]).is_relative_to(REPO_ROOT.resolve()), f"child imported foreign scp: {res['scp_file']}"
    assert res["pytest_loaded"] is False
    return res


def _seeded_runtime(tmp_path: Path) -> Path:
    data = tmp_path / "boot_root" / "data"
    data.mkdir(parents=True)
    (data / "sentinel.txt").write_text("runtime store sentinel\n", encoding="utf-8")
    return data


def test_nonruntime_process_booted_on_runtime_store_never_writes_it(tmp_path):
    data = _seeded_runtime(tmp_path)
    before = _snapshot(data)
    out = _run_child(tmp_path, data, _NONRUNTIME_SCRIPT, "write", {"SCP_PROVENANCE": "probe"})
    assert _snapshot(data) == before, "probe process wrote into the runtime learning store"
    assert out["prov"] == {"es": "probe", "fl": "probe", "kb": "probe"}
    assert out["refused"] == {"explicit_es": "LearningIsolationError", "explicit_fl": "LearningIsolationError",
                              "explicit_kb": "LearningIsolationError"}


def test_isolated_dir_configured_inside_runtime_root_is_refused(tmp_path):
    data = _seeded_runtime(tmp_path)
    before = _snapshot(data)
    out = _run_child(tmp_path, data, _NONRUNTIME_SCRIPT, "misconfigured",
                     {"SCP_PROVENANCE": "probe", "SCP_ISOLATED_DATA_DIR": str(data / "sub")})
    assert out["refused"] == {"default_es": "LearningIsolationError"}
    assert _snapshot(data) == before


def test_repo_data_dir_is_always_a_runtime_root(fake_runtime):
    """``<repo>/data`` là đích bind-mount production: luôn được bảo vệ, kể cả khi SCP_DATA_DIR khác."""
    from scp.core.learning_provenance import guard_write, is_runtime_location, LearningIsolationError

    assert is_runtime_location(REPO_ROOT / "data" / "failure_ledger.sqlite3")
    with pytest.raises(LearningIsolationError):
        guard_write(REPO_ROOT / "data" / "error_store.jsonl")
    # SCP_DATA_DIR do test tự đặt (monkeypatch) không phải gốc runtime của tiến trình
    assert not is_runtime_location(fake_runtime / "kb_evolve.sqlite")


# ---------------------------------------------------------------------------
# 3. Provenance có cấu trúc từ ngữ cảnh tiến trình (không từ nội dung)
# ---------------------------------------------------------------------------

def test_records_carry_structured_test_provenance(fake_runtime):
    from scp.brain.error_store import ErrorStore
    from scp.epistemic.failure_ledger import get_failure_ledger
    from scp.meta.kb_evolve import get_kb_store

    rec = ErrorStore().add(question="Which planet is closest to the sun?", answer="x", verdict="REFUTED")
    assert rec["provenance"] == "test"
    led = get_failure_ledger()
    fid = led.record_failure("TEST_FAILURE", "scp/core/a.py", verbatim_critique="boom")
    with sqlite3.connect(led.db_path) as c:
        assert c.execute("SELECT provenance FROM failure_records WHERE failure_id=?", (fid,)).fetchone() == ("test",)
    kbs = get_kb_store()
    kbs.save_lesson(_lesson("p1"))
    with sqlite3.connect(kbs.db_path) as c:
        assert c.execute("SELECT provenance FROM lessons WHERE lesson_id='les-p1'").fetchone() == ("test",)


@pytest.mark.parametrize("declared,expected", [
    ("probe", "probe"), ("fixture", "fixture"), ("test", "test"),
    ("runtime", "test"),          # harness thắng: tiến trình pytest không thể tự xưng runtime
    ("garbage", "unknown"),       # khai báo lạ ⇒ không phải runtime (fail-closed)
])
def test_declared_provenance_cannot_escalate_to_runtime(fake_runtime, monkeypatch, declared, expected):
    from scp.core.learning_provenance import current_provenance
    from scp.brain.error_store import ErrorStore

    monkeypatch.setenv("SCP_PROVENANCE", declared)
    assert current_provenance() == expected
    before = _snapshot(fake_runtime)
    rec = ErrorStore().add(question="Which planet is closest to the sun?", answer="x", verdict="REFUTED")
    assert rec["provenance"] == expected
    assert _snapshot(fake_runtime) == before


# ---------------------------------------------------------------------------
# 4. Nhánh runtime: tiến trình con THẬT không có tín hiệu harness
# ---------------------------------------------------------------------------

_RUNTIME_SCRIPT = textwrap.dedent(
    r"""
    import json, os, sqlite3, sys
    from pathlib import Path
    data = Path(os.environ["SCP_DATA_DIR"])
    import scp.epistemic.failure_ledger as fl
    if hasattr(fl, "DEFAULT_DB_PATH"):
        fl.DEFAULT_DB_PATH = data / "failure_ledger.sqlite3"   # commit cũ: không bao giờ chạm data/ thật
    from scp.brain.error_store import ErrorStore
    from scp.meta.kb_evolve import get_kb_store, Lesson
    mode = sys.argv[1]
    import scp as _scp_pkg
    out = {"pytest_loaded": "_pytest" in sys.modules, "scp_file": str(Path(_scp_pkg.__file__).resolve())}
    led = fl.get_failure_ledger(); kbs = get_kb_store(); es = ErrorStore()
    out["paths"] = [str(Path(led.db_path).resolve()), str(Path(kbs.db_path).resolve()), str(Path(es.path).resolve())]
    if mode == "write":
        # nội dung GIỐNG test (đường pytest) — provenance KHÔNG được suy từ nội dung
        fid = led.record_failure("WHY_REJECTION", "/tmp/pytest-of-x/p0/broken_module.py", verbatim_critique="rt crit")
        kbs.save_lesson(Lesson(lesson_id="les-rt", timestamp=1.0, bug_type="RaceCondition",
                               bug_file="/tmp/pytest-of-x/p0/m.py", bug_line=1, root_cause="rt-root",
                               lesson="rt-lesson", fix_pattern="", fix_verified=True, success_rate=0.9))
        rec = es.add(question="What is the boiling point of water at sea level?", answer="x", verdict="REFUTED")
        with sqlite3.connect(led.db_path) as c:
            out["fl_prov"] = c.execute("SELECT provenance FROM failure_records WHERE failure_id=?", (fid,)).fetchone()
        with sqlite3.connect(kbs.db_path) as c:
            out["kb_prov"] = c.execute("SELECT provenance FROM lessons WHERE lesson_id='les-rt'").fetchone()
        out["es_prov"] = rec.get("provenance")
    else:
        les = kbs.lookup_lesson("NullDereference")
        out["legacy_lesson"] = les.lesson_id if les else None
        out["legacy_anti"] = [r["failure_id"] for r in led.get_anti_patterns_for_module("scp/core/legacy.py")]
        out["es_visible"] = [r.get("id") for r in es.get_recent(50)]
        rt = es.add(question="Which planet is closest to the sun?", answer="x", verdict="REFUTED")
        out["es_visible_after"] = [r.get("id") for r in es.get_recent(50)]
        out["rt_id"] = rt.get("id")
        fid = led.record_failure("WHY_REJECTION", "scp/core/legacy.py", verbatim_critique="legacy crit")
        out["anti_after"] = [r["failure_id"] for r in led.get_anti_patterns_for_module("scp/core/legacy.py")]
        out["rt_fid"] = fid
    print("RESULT=" + json.dumps(out))
    """
)


def test_runtime_process_writes_runtime_store_with_runtime_provenance(tmp_path):
    data = tmp_path / "rt_root" / "data"
    data.mkdir(parents=True)
    out = _run_child(tmp_path, data, _RUNTIME_SCRIPT, "write")
    for p in out["paths"]:
        assert Path(p).parent == data.resolve(), f"runtime store must honour SCP_DATA_DIR: {p}"
    assert out["fl_prov"] == ["runtime"]
    assert out["kb_prov"] == ["runtime"]
    assert out["es_prov"] == "runtime"


# id của bản ghi cũ = đúng fingerprint trước L0a (type:module:critique) ⇒ nếu danh tính không gồm
# provenance, một quan sát runtime giống hệt sẽ CỘNG DỒN vào (và chìm trong) dòng legacy.
LEGACY_FID = hashlib.sha256(b"WHY_REJECTION:scp/core/legacy.py:legacy crit").hexdigest()[:16]


def _plant_legacy_and_test_rows(data: Path) -> None:
    """Kho runtime giả mang dữ liệu CŨ (schema trước L0a, không provenance) + bản ghi test lọt vào."""
    with sqlite3.connect(data / "failure_ledger.sqlite3") as c:
        c.execute("""CREATE TABLE failure_records (failure_id TEXT PRIMARY KEY, failure_type TEXT NOT NULL,
            target_module TEXT NOT NULL, target_symbol TEXT, verbatim_critique TEXT NOT NULL,
            falsified_patch_diff TEXT, anti_pattern_rule TEXT, occurrence_count INTEGER DEFAULT 1,
            first_observed TEXT NOT NULL, last_observed TEXT NOT NULL, status TEXT DEFAULT 'ACTIVE_IMMUNITY')""")
        c.execute("INSERT INTO failure_records VALUES (?,'WHY_REJECTION','scp/core/legacy.py','',"
                  "'legacy crit','','r',3,'t','t','ACTIVE_IMMUNITY')", (LEGACY_FID,))
    with sqlite3.connect(data / "kb_evolve.sqlite") as c:
        c.execute("""CREATE TABLE lessons (lesson_id TEXT PRIMARY KEY, timestamp REAL NOT NULL, bug_type TEXT NOT NULL,
            bug_file TEXT, bug_line INTEGER, root_cause TEXT, lesson TEXT NOT NULL, fix_pattern TEXT,
            fix_verified INTEGER DEFAULT 0, occurrence_count INTEGER DEFAULT 1, success_rate REAL DEFAULT 0.0)""")
        c.execute("INSERT INTO lessons VALUES ('les-legacy',1.0,'NullDereference','f',1,'legacy-root','l','',1,5,0.9)")
    (data / "error_store.jsonl").write_text(
        json.dumps({"id": "err-legacy", "ts": 1.0, "question": "q", "answer": "a", "verdict": "REFUTED",
                    "domain": "general", "error_type": "x", "details": {}}) + "\n"
        + json.dumps({"id": "err-planted-test", "ts": 2.0, "question": "q2", "answer": "a", "verdict": "REFUTED",
                      "domain": "general", "error_type": "x", "details": {}, "provenance": "test"}) + "\n",
        encoding="utf-8")


def test_runtime_reader_ignores_legacy_and_non_runtime_rows_without_deleting_them(tmp_path):
    data = tmp_path / "rt_root" / "data"
    data.mkdir(parents=True)
    _plant_legacy_and_test_rows(data)
    out = _run_child(tmp_path, data, _RUNTIME_SCRIPT, "read")
    assert out["legacy_lesson"] is None, "runtime reader consumed a legacy_untrusted lesson"
    assert out["legacy_anti"] == [], "runtime reader consumed a legacy_untrusted anti-pattern"
    assert "err-legacy" not in out["es_visible"] and "err-planted-test" not in out["es_visible"]
    assert out["rt_id"] in out["es_visible_after"]
    assert out["anti_after"] == [out["rt_fid"]] and out["rt_fid"] != LEGACY_FID
    # dữ liệu cũ KHÔNG bị xóa — chỉ bị đánh dấu không tin cậy
    with sqlite3.connect(data / "failure_ledger.sqlite3") as c:
        assert c.execute("SELECT provenance, occurrence_count FROM failure_records WHERE failure_id=?",
                         (LEGACY_FID,)).fetchone() == ("legacy_untrusted", 3)
    with sqlite3.connect(data / "kb_evolve.sqlite") as c:
        assert c.execute("SELECT provenance, occurrence_count FROM lessons WHERE lesson_id='les-legacy'").fetchone() == (
            "legacy_untrusted", 5)
    ids = [json.loads(ln)["id"] for ln in (data / "error_store.jsonl").read_text(encoding="utf-8").splitlines() if ln]
    assert ids[:2] == ["err-legacy", "err-planted-test"]


def test_test_process_reader_never_sees_legacy_rows(tmp_path):
    """Kho cách ly của tiến trình test có thể chứa schema cũ (copy) — legacy vẫn bị loại."""
    data = tmp_path / "copy"
    data.mkdir()
    _plant_legacy_and_test_rows(data)
    from scp.epistemic.failure_ledger import FailureLedger
    from scp.meta.kb_evolve import KBAccumulationStore

    assert FailureLedger(db_path=data / "failure_ledger.sqlite3").get_anti_patterns_for_module("scp/core/legacy.py") == []
    assert KBAccumulationStore(data_dir=str(data)).lookup_lesson("NullDereference") is None


# ---------------------------------------------------------------------------
# 5. Công cụ báo cáo dữ liệu cũ: CHỈ ĐỌC (sha256 nguồn không đổi) + đếm đúng
# ---------------------------------------------------------------------------

def test_legacy_report_tool_is_read_only_and_counts_missing_provenance(tmp_path):
    data = tmp_path / "legacy"
    data.mkdir()
    _plant_legacy_and_test_rows(data)
    before = _snapshot(data)
    proc = subprocess.run([sys.executable, "-X", "utf8", str(REPO_ROOT / "tools" / "learning_provenance_report.py"),
                           "--data-dir", str(data)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    rep = json.loads(proc.stdout)[0]["stores"]
    assert _snapshot(data) == before, "report tool modified the store it reports on"
    fl_t = rep["failure_ledger.sqlite3"]["tables"]["failure_records"]
    assert (fl_t["total"], fl_t["has_provenance_column"], fl_t["missing_provenance"]) == (1, False, 1)
    kb_t = rep["kb_evolve.sqlite"]["tables"]["lessons"]
    assert (kb_t["total"], kb_t["missing_provenance"]) == (1, 1)
    es = rep["error_store.jsonl"]
    assert es["by_provenance"] == {"legacy_untrusted": 1, "test": 1}
    assert all(s.get("source_unchanged_by_report", True) for s in rep.values())


# ---------------------------------------------------------------------------
# 6. Trạng thái SỐNG ngoài kho học (bổ sung phạm vi 31/10): capability_state.json của Hands
#    bị KHÓA trên production bởi test_flow_04::test_hands_capabilities_revoke_requires_token
#    (route singleton hands_routes._hands = HandsExecutor() bám <repo>/data/security).
#    Mọi test dưới đây gọi learning_provenance TRƯỚC khi có bất kỳ thao tác ghi nào ⇒ trên
#    commit cũ chúng đỏ vì ImportError/AttributeError, KHÔNG BAO GIỜ chạm file sống.
# ---------------------------------------------------------------------------

def _sha_or_none(p: Path) -> str | None:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


LIVE_CAPABILITY_STATE = REPO_ROOT / "data" / "security" / "capability_state.json"


def test_hands_default_security_dir_is_isolated_in_test_process():
    from scp.core.learning_provenance import is_runtime_location
    from scp.hands.hands_executor import HandsExecutor

    assert not is_runtime_location(HandsExecutor.default_security_dir())


def test_hands_route_revoke_in_test_process_never_touches_live_capability_state(monkeypatch):
    from scp.core.learning_provenance import is_runtime_location
    from scp.api.routes import hands_routes

    state_path = hands_routes._hands.capability_authority.state_path
    assert not is_runtime_location(state_path), f"route singleton bound to live state: {state_path}"
    before = _sha_or_none(LIVE_CAPABILITY_STATE)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setenv("SCP_PC_CONTROLLER_TOKEN", "l0a-test-token")
    app = FastAPI()
    app.include_router(hands_routes.router)
    resp = TestClient(app).post("/v3/hands/capabilities/revoke", json={"reason": "test"},
                                headers={"X-SCP-PC-Token": "l0a-test-token"})
    assert resp.status_code == 200
    assert resp.json()["capability"]["revoked"] is True
    assert _sha_or_none(LIVE_CAPABILITY_STATE) == before, "test revoked the LIVE Hands capability state"
    # dọn trạng thái cách ly để test sau trong cùng tiến trình không thừa hưởng revoked
    hands_routes._hands.restore_capabilities("l0a-test-cleanup", "test")


def test_hands_executor_default_revoke_lands_in_isolated_dir(tmp_path, monkeypatch):
    from scp.core.learning_provenance import is_runtime_location
    from scp.hands.hands_executor import HandsExecutor

    monkeypatch.delenv("SCP_SECURITY_DATA_DIR", raising=False)
    monkeypatch.delenv("SCP_HANDS_DATA_DIR", raising=False)
    monkeypatch.setenv("SCP_ISOLATED_DATA_DIR", str(tmp_path / "iso"))
    assert not is_runtime_location(HandsExecutor.default_security_dir())
    before = _sha_or_none(LIVE_CAPABILITY_STATE)
    ex = HandsExecutor()
    assert ex.revoke_capabilities("test", "operator")["revoked"] is True
    assert ex.capability_authority.state_path.resolve().is_relative_to((tmp_path / "iso").resolve())
    assert ex.audit_path.resolve().is_relative_to((tmp_path / "iso").resolve())
    assert _sha_or_none(LIVE_CAPABILITY_STATE) == before


_CAPABILITY_SCRIPT = textwrap.dedent(
    r"""
    import json, os, sys
    from pathlib import Path
    data = Path(os.environ["SCP_DATA_DIR"])
    import scp as _scp_pkg
    from scp.security.capability_epoch import CapabilityAuthority
    out = {"scp_file": str(Path(_scp_pkg.__file__).resolve()), "pytest_loaded": "_pytest" in sys.modules}
    try:
        CapabilityAuthority(data / "security" / "capability_state.json").revoke(reason="test", actor="operator")
        out["refused"] = None
    except Exception as exc:
        out["refused"] = type(exc).__name__
    print("RESULT=" + json.dumps(out))
    """
)


def test_capability_authority_refuses_nonruntime_write_to_runtime_state(tmp_path):
    data = _seeded_runtime(tmp_path)
    before = _snapshot(data)
    out = _run_child(tmp_path, data, _CAPABILITY_SCRIPT, "capability", {"SCP_PROVENANCE": "probe"})
    assert out["refused"] == "LearningIsolationError"
    assert _snapshot(data) == before, "probe process persisted capability state into the runtime store"


def test_attack_predictor_audit_in_test_process_leaves_runtime_store_unchanged(fake_runtime):
    before = _snapshot(fake_runtime)
    from scp.security.predictor import AttackPredictor

    AttackPredictor()._audit_v91("l0a_probe", {"threat_type": "ddos"})
    assert _snapshot(fake_runtime) == before, "test process appended to the runtime v91 audit"


def test_autofix_default_audit_writers_in_test_process_leave_runtime_store_unchanged(fake_runtime):
    """regression_watch.jsonl + tier3/v91/autofix audit: mặc định cwd ``data`` cũ ⇒ cách ly."""
    before = _snapshot(fake_runtime)
    from scp.autofix.runner_phases.auto_rollback import RegressionWatcher
    from scp.autofix.engine import AutoFixEngine

    watcher = RegressionWatcher()
    watcher._log_event({"event": "l0a_probe"})
    engine = AutoFixEngine()
    with open(engine.tier3_auto_audit_log, "a", encoding="utf-8") as f:
        f.write("{}\n")
    assert _snapshot(fake_runtime) == before, "autofix default writers targeted the runtime store"
