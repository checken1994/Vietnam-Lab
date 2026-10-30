"""QLT-06: behaviour pins for except-handlers narrowed from ``except Exception`` + noqa.

Each handler that lost its ``# noqa: BLE001`` must still absorb every error
class it can *actually* see (bad persisted entry, unreadable file, NUL in
path, bad candidate) and must never raise out of the public API, while the
bookkeeping that the removed try/except used to wrap (visitor list append,
LRU touch/evict) keeps working.
"""
from __future__ import annotations

import json
import time

from scp.autofix import callgraph_delta as cg
from scp.autofix import speculative_prefixer as sp


# ------------------------------------------------------------ callgraph_delta

def test_filenode_from_dict_skips_malformed_edges_not_raises():
    node = cg.FileNode.from_dict({
        "path": "a.py", "sha": "x", "mtime": 1.0,
        "calls": [
            "not-a-dict",                       # AttributeError
            {"target": "ok", "line": "NaN?"},   # ValueError
            {"target": "ok", "line": None},     # TypeError
            {"target": "good", "line": 3, "col": 1},
        ],
    })
    assert [e.target_name for e in node.calls] == ["good"]


def test_analyze_file_never_raises_and_records_parse_error(tmp_path):
    missing = cg._analyze_file(str(tmp_path / "missing.py"))
    assert missing.parse_error and missing.sha == ""
    bad = tmp_path / "bad.py"
    bad.write_text("def f(:\n", encoding="utf-8")
    assert cg._analyze_file(str(bad)).parse_error.startswith("SyntaxError")
    nul = tmp_path / "nul.py"
    nul.write_bytes(b"x = 1\x00\n")
    assert cg._analyze_file(str(nul)).parse_error  # never raises, error recorded


def test_analyze_file_visitor_bounds_functions_without_guard(tmp_path):
    src = "\n".join(f"def f{i}(): pass" for i in range(cg.MAX_FUNCTIONS_PER_FILE + 5))
    src += "\nasync def g(): pass\n"
    f = tmp_path / "many.py"
    f.write_text(src, encoding="utf-8")
    node = cg._analyze_file(str(f))
    assert node.parse_error.endswith("[bounded]")
    assert "f0" in node.functions_defined


def test_callgraph_load_skips_malformed_entries_keeps_good(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cache = tmp_path / "cg.json"
    good = cg.FileNode(path="good.py", sha="s", mtime=1.0).to_dict()
    cache.write_text(json.dumps({"version": 1, "files": {
        "good.py": good, "bad1.py": "oops", "bad2.py": {"mtime": "NaN?"},
    }}), encoding="utf-8")
    g = cg.CallGraph(cache_file=str(cache))
    assert set(g._files) == {"good.py"}


def test_is_file_current_false_on_unreadable_or_nul_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    g = cg.CallGraph(cache_file=str(tmp_path / "cg.json"))
    assert g.is_file_current(str(tmp_path / "missing.py")) is False
    assert g.is_file_current("bad\x00path.py") is False
    assert g.is_file_current(str(tmp_path)) is False  # directory -> OSError


# -------------------------------------------------------- speculative_prefixer

def _cache(tmp_path, monkeypatch, **kw):
    monkeypatch.chdir(tmp_path)
    return sp.SpeculativeCache(cache_file=str(tmp_path / "sp.json"), **kw)


def _cand(i, generated_at=None):
    return sp.CandidateFix(
        pattern_name=f"p{i}", file_sha=f"s{i}", file_path="f.py", line_start=1,
        line_end=1, original_snippet="a", patched_snippet="b",
        full_patched_source="b", confidence_hint=0.5,
        generated_at=time.time() if generated_at is None else generated_at,
    )


def test_prefetch_returns_zero_on_unreadable_or_nul_or_syntax(tmp_path, monkeypatch):
    c = _cache(tmp_path, monkeypatch)
    assert c.prefetch_candidates(str(tmp_path / "missing.py")) == 0
    assert c.prefetch_candidates("bad\x00path.py") == 0
    assert c.prefetch_candidates(str(tmp_path / "x.py"), source_override="def f(:\n") == 0
    assert c.prefetch_candidates(str(tmp_path / "y.py"), source_override="x = 1\x00\n") == 0


def test_store_candidate_absorbs_bad_candidate(tmp_path, monkeypatch):
    c = _cache(tmp_path, monkeypatch)
    c._store_candidate(object())  # AttributeError absorbed
    assert c._stats["candidates_stored"] == 0
    c._store_candidate(_cand(1))
    assert c._stats["candidates_stored"] == 1


def test_lru_touch_and_enforce_max_keep_working(tmp_path, monkeypatch):
    c = _cache(tmp_path, monkeypatch, max_entries=2)
    for i in range(3):
        c._store_candidate(_cand(i))
    c._enforce_max()
    assert len(c._store) == 2 and c._stats["evictions"] == 1
    assert ("s0", "p0") not in c._store  # oldest evicted
    c._touch(("s1", "p1"))
    assert c._lru[-1] == ("s1", "p1") and c._lru.count(("s1", "p1")) == 1


def test_evict_expired_removes_old_and_absorbs_corrupt(tmp_path, monkeypatch):
    c = _cache(tmp_path, monkeypatch, ttl_seconds=10)
    c._store_candidate(_cand(1, generated_at=time.time() - 100))
    c._store_candidate(_cand(2))
    assert c._evict_expired() == 1 and set(c._store) == {("s2", "p2")}
    c._store[("bad", "bad")] = "not-a-candidate"  # AttributeError path
    assert c._evict_expired() == 0


def test_cache_load_skips_malformed_entries_keeps_good(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    good = _cand(1).__dict__.copy() if hasattr(_cand(1), "__dict__") else None
    if good is None:  # slots/dataclass without __dict__
        import dataclasses
        good = dataclasses.asdict(_cand(1))
    path = tmp_path / "sp.json"
    path.write_text(json.dumps({"entries": [
        good, "oops", {"pattern_name": "x"}, {**good, "pattern_name": "q", "line_start": "NaN?"},
    ]}), encoding="utf-8")
    c = sp.SpeculativeCache(cache_file=str(path))
    assert set(c._store) == {("s1", "p1")}
