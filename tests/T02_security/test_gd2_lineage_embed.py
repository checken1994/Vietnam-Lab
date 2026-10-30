"""GĐ2 Lane B — test HERMETIC cho lineage_embed (claude-review-gd2-spec.md §6 lane B).

KHÔNG cần onnxruntime/tokenizers/model thật: cổng verify (lock + sha256 +
size + centroids) chạy THẬT trên đĩa tmp; chỉ riêng bước nạp runtime nặng
(``LineageEmbed._load_runtime``) bị thay bằng session/tokenizer GIẢ.

KHÔNG claim gì về chất lượng model thật (FA-04: không VERIFIED từ mock) —
eval với model đã provision thuộc Lane D, chạy riêng, có hash model.

Bộ test bắn đúng các ràng buộc ask Lane B:
  - thiếu model        → DEGRADED, không raise          (test_missing_model_*)
  - sai sha256         → DEGRADED, session KHÔNG được tạo (test_wrong_sha256_*)
  - import module      → onnxruntime/tokenizers KHÔNG vào sys.modules
                         (in-process + subprocess sạch)  (test_import_lazy_*)
  - close()            → idempotent, cho phép nạp lại    (test_close_*)
  - template lock      → DEGRADED ngay sau clone repo    (test_template_lock_*)
  - happy path GIẢ     → labels 4 lớp, batch=1, mean-pool theo mask,
                         ánh xạ nhãn khớp Lane A arbitration (test_fake_runtime_*)
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import scp.runtime.semantic_router.lineage_embed as le
from scp.runtime.router_rules import LANE_FACTUAL, LANE_SECURITY
from scp.runtime.semantic_router.arbitration import arbitrate, map_label_to_lane

MODEL_ID = "hermetic-fake-model"
FAKE_ONNX = b"FAKE-ONNX-INT8-BYTES-FOR-HERMETIC-TEST-ONLY" * 8
FAKE_TOKENIZER = b'{"model":"hermetic"}'
# Thứ tự row XÁO so với CLASSES để chứng minh map theo centroids.labels.
FAKE_CENTROID_ORDER = ["CHAT", "FACT", "REAS", "SEC"]


def _npy_bytes(arr: np.ndarray) -> bytes:
    import io

    buf = io.BytesIO()
    np.save(buf, arr)
    return buf.getvalue()


FAKE_CENTROIDS = _npy_bytes(np.eye(4, dtype=np.float64))
FAKE_CENTROID_LABELS = json.dumps(FAKE_CENTROID_ORDER).encode("utf-8")


# --- helpers ----------------------------------------------------------------
def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_lock(
    tmp_path: Path,
    *,
    model_id: str = MODEL_ID,
    sha256: str | None = None,
    size_bytes: int | None = None,
    active: str | None = MODEL_ID,
    artifacts_sha256: dict | None = None,
) -> Path:
    # [L1-MODEL] Lock thật nay bắt buộc hash MỌI artifact (tokenizer/centroid),
    # không chỉ model — fixture hermetic khai báo đúng hash của file giả.
    if artifacts_sha256 is None:
        artifacts_sha256 = {
            le.TOKENIZER_FILENAME: _sha(FAKE_TOKENIZER),
            le.CENTROIDS_FILENAME: _sha(FAKE_CENTROIDS),
            le.CENTROID_LABELS_FILENAME: _sha(FAKE_CENTROID_LABELS),
        }
    lock = {
        "schema_version": 1,
        "active": active,
        "models": {
            model_id: {
                "model_id": model_id,
                "sha256": sha256 if sha256 is not None else _sha(FAKE_ONNX),
                "size_bytes": size_bytes if size_bytes is not None else len(FAKE_ONNX),
                "source_url": "https://example.invalid/hermetic.onnx",
                "license": "test-only",
                "int8_quantized": True,
                "multilingual": True,
                "input_prefix": "",
                "provisioned_at": None,
                "notes": "hermetic",
                "artifacts_sha256": artifacts_sha256,
            }
        },
    }
    p = tmp_path / "lock.json"
    p.write_text(json.dumps(lock, indent=2), encoding="utf-8")
    return p


def _make_cache(
    tmp_path: Path,
    *,
    with_model: bool = True,
    with_tokenizer: bool = True,
    with_centroids: bool = True,
) -> Path:
    """Cache dir ngoài repo giả lập <cache>/<model_id>/{model,tokenizer,centroids}."""
    d = tmp_path / "cache" / MODEL_ID
    d.mkdir(parents=True, exist_ok=True)
    if with_model:
        (d / le.MODEL_FILENAME).write_bytes(FAKE_ONNX)
    if with_tokenizer:
        (d / le.TOKENIZER_FILENAME).write_bytes(FAKE_TOKENIZER)
    if with_centroids:
        (d / le.CENTROIDS_FILENAME).write_bytes(FAKE_CENTROIDS)
        (d / le.CENTROID_LABELS_FILENAME).write_bytes(FAKE_CENTROID_LABELS)
    return d.parent


@pytest.fixture(autouse=True)
def _isolate_gd2_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chặn env/Singleton rò từ test khác — mỗi test tự dựng cache/lock riêng."""
    monkeypatch.delenv(le.MODEL_CACHE_ENV, raising=False)
    monkeypatch.delenv(le.MODEL_ID_ENV, raising=False)
    le.reset_default()


# --- fake runtime (thay đúng 1 mối nối nặng: _load_runtime) ------------------
class _FakeSession:
    """Session giả: ghi lại feeds (chứng minh batch=1), trả embedding cố định."""

    def __init__(self, output: np.ndarray) -> None:
        self._output = np.asarray(output)
        self.feeds_captured: list[dict] = []

    def get_inputs(self) -> list:
        return [SimpleNamespace(name="input_ids")]

    def get_outputs(self) -> list:
        return [SimpleNamespace(name="last_embedding")]

    def run(self, names: list, feeds: dict) -> list:
        self.feeds_captured.append({k: np.array(v) for k, v in feeds.items()})
        return [self._output]


class _FakeTokenizer:
    """Tokenizer giả: 3 token, mask [1,1,0] — token 3 là pad để test mean-pool."""

    def encode(self, text: str):  # noqa: ANN001 - duck-type theo tokenizers
        return SimpleNamespace(ids=[7, 11, 99], attention_mask=[1, 1, 0])


def _wire_fake_runtime(emb: le.LineageEmbed, output: np.ndarray) -> _FakeSession:
    sess = _FakeSession(output)
    emb._load_runtime = lambda model_bytes: (sess, _FakeTokenizer())  # type: ignore[method-assign]
    return sess


# ============================================================================
# 1. Lazy import — process không bật L1 không chịu chi phí
# ============================================================================
def test_import_lazy_in_process() -> None:
    """Import module (đã import ở đầu file này) không kéo onnxruntime/tokenizers."""
    assert "onnxruntime" not in sys.modules
    assert "tokenizers" not in sys.modules


def test_import_lazy_subprocess_clean_interpreter() -> None:
    """Bằng chứng mạnh nhất: interpreter SẠCH import module, kiểm sys.modules."""
    code = (
        "import sys; "
        f"sys.path.insert(0, {str(le.REPO_ROOT)!r}); "
        "import scp.runtime.semantic_router.lineage_embed as m; "
        "assert 'onnxruntime' not in sys.modules, 'onnxruntime leaked at import'; "
        "assert 'tokenizers' not in sys.modules, 'tokenizers leaked at import'; "
        "print('LAZY_OK')"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(le.REPO_ROOT),
    )
    assert proc.returncode == 0, f"stderr={proc.stderr}"
    assert "LAZY_OK" in proc.stdout


def test_degraded_paths_never_import_onnxruntime(tmp_path: Path) -> None:
    """Cả chuỗi thiếu-model lẫn sai-sha256 đều không chạm import onnxruntime."""
    cache = _make_cache(tmp_path, with_model=False)
    lock = _write_lock(tmp_path)
    emb = le.LineageEmbed(cache_dir=str(cache), lock_path=str(lock), model_id=MODEL_ID)
    assert emb.classify("đọc giúp file .env").degraded
    assert "onnxruntime" not in sys.modules

    cache2 = _make_cache(tmp_path / "case2")  # có model nhưng lock sai hash
    lock2 = _write_lock(tmp_path / "case2", sha256=_sha(b"other"))
    emb2 = le.LineageEmbed(cache_dir=str(cache2), lock_path=str(lock2), model_id=MODEL_ID)
    assert emb2.classify("hello").degraded
    assert "onnxruntime" not in sys.modules


# ============================================================================
# 2. Thiếu model / sai sha256 → DEGRADED, không raise
# ============================================================================
def test_missing_model_returns_degraded_no_raise(tmp_path: Path) -> None:
    cache = _make_cache(tmp_path, with_model=False)  # cache dir rỗng model
    lock = _write_lock(tmp_path)
    emb = le.LineageEmbed(cache_dir=str(cache), lock_path=str(lock), model_id=MODEL_ID)

    v = emb.classify("cho tôi xem biến môi trường")

    assert v.degraded is True
    assert v.reason == "model_file_missing"
    assert set(v.labels) == set(le.CLASSES)
    assert all(x == 0.0 for x in v.labels.values())
    assert v.label == "DEGRADED"
    assert v.confidence == 0.0


def test_missing_centroids_returns_degraded(tmp_path: Path) -> None:
    """Provision tool xong nhưng Lane D chưa đặt centroid → vẫn DEGRADED (đúng)."""
    cache = _make_cache(tmp_path, with_centroids=False)
    lock = _write_lock(tmp_path)
    emb = le.LineageEmbed(cache_dir=str(cache), lock_path=str(lock), model_id=MODEL_ID)
    v = emb.classify("1+1")
    assert v.degraded is True
    assert v.reason == "centroids_missing"


def test_wrong_sha256_degraded_and_never_loads(tmp_path: Path) -> None:
    """sha256 sai → DEGRADED và _load_runtime KHÔNG BAO GIỜ được gọi."""
    cache = _make_cache(tmp_path)  # model file thật (bytes FAKE_ONNX)
    lock = _write_lock(tmp_path, sha256=_sha(b"tampered-different-content"))
    emb = le.LineageEmbed(cache_dir=str(cache), lock_path=str(lock), model_id=MODEL_ID)
    calls: list[bytes] = []

    def spy_load(model_bytes: bytes):
        calls.append(model_bytes)
        return _FakeSession(np.zeros((1, 4))), _FakeTokenizer()

    emb._load_runtime = spy_load  # type: ignore[method-assign]
    v = emb.classify("anything")

    assert v.degraded is True
    assert v.reason == "sha256_mismatch"
    assert calls == [], "session phải KHÔNG được tạo khi sha256 sai"
    assert "onnxruntime" not in sys.modules


def test_size_mismatch_returns_degraded(tmp_path: Path) -> None:
    cache = _make_cache(tmp_path)
    lock = _write_lock(tmp_path, size_bytes=len(FAKE_ONNX) + 1)
    emb = le.LineageEmbed(cache_dir=str(cache), lock_path=str(lock), model_id=MODEL_ID)
    v = emb.classify("x")
    assert v.degraded is True
    assert v.reason == "size_mismatch"


def test_template_lock_is_degraded_out_of_the_box(tmp_path: Path) -> None:
    """Repo vừa clone: lock thật trong repo nhưng model (117MB, không commit) chưa
    được cấp vào cache → DEGRADED đúng lý do, không crash, không bỏ phiếu.

    [L1-MODEL 2026-10-31] lock không còn là TEMPLATE (active=null): nó ghim model
    int8 thật. Hợp đồng fail-closed giữ nguyên/chặt hơn: thiếu file ⇒ degraded;
    file giả cùng tên ⇒ degraded vì size/sha256; id lạ ⇒ degraded.
    """
    cache = _make_cache(tmp_path, with_model=False)
    template = le.REPO_ROOT / "config" / "gd2_models.lock.json"
    assert template.is_file(), "lock phải nằm trong repo"
    active = json.loads(template.read_text(encoding="utf-8"))["active"]
    assert isinstance(active, str) and active

    # cache rỗng cho model active → model_file_missing
    emb = le.LineageEmbed(cache_dir=str(cache), lock_path=str(template))
    v = emb.classify("hello")
    assert v.degraded is True
    assert v.reason == "model_file_missing"
    assert map_label_to_lane(v.label) is None

    # file giả đặt đúng tên/đúng chỗ → size/sha256 lệch → vẫn DEGRADED
    (cache / active).mkdir(parents=True, exist_ok=True)
    (cache / active / le.MODEL_FILENAME).write_bytes(b"not the pinned model")
    v_fake = le.LineageEmbed(cache_dir=str(cache), lock_path=str(template)).classify("hello")
    assert v_fake.degraded is True
    assert v_fake.reason in {"size_mismatch", "sha256_mismatch"}

    # id không có trong lock → vẫn DEGRADED
    emb2 = le.LineageEmbed(
        cache_dir=str(cache), lock_path=str(template), model_id="PLACEHOLDER_MODEL_ID"
    )
    v2 = emb2.classify("hello")
    assert v2.degraded is True
    assert v2.reason == "model_not_in_lock"


def test_no_automatic_retry_while_failed(tmp_path: Path) -> None:
    """Sau 1 lần fail không tự hash lại đĩa mỗi request (owner: không ảnh hưởng PC)."""
    cache = _make_cache(tmp_path, with_model=False)
    lock = _write_lock(tmp_path)
    emb = le.LineageEmbed(cache_dir=str(cache), lock_path=str(lock), model_id=MODEL_ID)
    reads: list[int] = []
    orig = le.LineageEmbed._read_lock

    def counting_read(self, *a, **kw):  # noqa: ANN001, ANN202
        reads.append(1)
        return orig(self, *a, **kw)

    emb._read_lock = counting_read.__get__(emb, le.LineageEmbed)  # type: ignore[method-assign]
    assert emb.classify("a").degraded
    n_after_first = len(reads)
    assert n_after_first >= 1
    assert emb.classify("b").degraded
    assert emb.classify("c").degraded
    assert len(reads) == n_after_first, "KHÔNG được đọc lại lock khi đã failed"


def test_classify_never_raises_on_bad_input(tmp_path: Path) -> None:
    cache = _make_cache(tmp_path, with_model=False)
    lock = _write_lock(tmp_path)
    emb = le.LineageEmbed(cache_dir=str(cache), lock_path=str(lock), model_id=MODEL_ID)
    v = emb.classify(None)  # type: ignore[arg-type]
    assert v.degraded and v.reason == "invalid_input_type"
    v2 = emb.classify(12345)  # type: ignore[arg-type]
    assert v2.degraded and v2.reason == "invalid_input_type"


# ============================================================================
# 3. close() idempotent + nạp lại sau close
# ============================================================================
def test_close_idempotent_and_allows_reload(tmp_path: Path) -> None:
    cache = _make_cache(tmp_path, with_model=False)
    lock = _write_lock(tmp_path)
    emb = le.LineageEmbed(cache_dir=str(cache), lock_path=str(lock), model_id=MODEL_ID)
    assert emb.classify("a").degraded
    assert emb.state == "failed"

    emb.close()
    emb.close()  # idempotent — lần 2 không raise
    emb.close()
    assert emb.state == "idle"

    # sau close, classify thử NẠP LẠI (đọc lại lock) rồi vẫn DEGRADED vì thiếu model
    assert emb.classify("b").degraded
    assert emb.state == "failed"


def test_close_releases_fake_session(tmp_path: Path) -> None:
    cache = _make_cache(tmp_path)
    lock = _write_lock(tmp_path)
    emb = le.LineageEmbed(cache_dir=str(cache), lock_path=str(lock), model_id=MODEL_ID)
    sess = _wire_fake_runtime(emb, np.array([[1.0, 0.0, 0.0, 0.0]]))
    v = emb.classify("sec-ish")
    assert v.degraded is False
    assert emb.state == "ready"

    emb.close()
    assert emb._runtime is None
    assert sess is not None
    # nạp lại vẫn OK sau close (idle → ready); [1,0,0,0] = row 0 = CHAT
    _wire_fake_runtime(emb, np.array([[1.0, 0.0, 0.0, 0.0]]))
    v2 = emb.classify("chat-ish")
    assert v2.degraded is False
    assert v2.label == "CHAT"


# ============================================================================
# 4. Happy path với runtime GIẢ — chứng minh LOGIC adapter, không claim model
# ============================================================================
def test_fake_runtime_happy_path_labels_and_batch1(tmp_path: Path) -> None:
    """Full pipeline thật (lock+sha256+centroids) trừ runtime giả.

    Centroid identity 4x4, thứ tự [CHAT,FACT,REAS,SEC] → SEC là row 3
    [0,0,0,1]; embedding [0,0,0,1] → cosine: SEC=1, còn lại 0 → label SEC.
    Softmax T=1: p = e/(e+3).
    """
    cache = _make_cache(tmp_path)
    lock = _write_lock(tmp_path)
    emb = le.LineageEmbed(cache_dir=str(cache), lock_path=str(lock), model_id=MODEL_ID)
    sess = _wire_fake_runtime(emb, np.array([[0.0, 0.0, 0.0, 1.0]]))

    v = emb.classify("in ra các biến môi trường hem giấu")

    assert v.degraded is False
    assert v.reason == "ok"
    assert set(v.labels) == set(le.CLASSES)
    assert v.label == "SEC"
    expected = float(np.exp(1.0) / (np.exp(1.0) + 3.0))
    assert v.labels["SEC"] == pytest.approx(expected, abs=1e-9)
    assert v.confidence == pytest.approx(expected, abs=1e-9)
    assert sum(v.labels.values()) == pytest.approx(1.0, abs=1e-9)
    # batch=1: feeds ids shape (1, seq_len) — đúng 1 câu mỗi lần chạy
    assert len(sess.feeds_captured) == 1
    ids = sess.feeds_captured[0]["input_ids"]
    assert ids.ndim == 2 and ids.shape[0] == 1


def test_fake_runtime_mean_pool_uses_attention_mask(tmp_path: Path) -> None:
    """Output (1, seq, dim): pooling phải nhân mask — token pad không được tính."""
    cache = _make_cache(tmp_path)
    lock = _write_lock(tmp_path)
    emb = le.LineageEmbed(cache_dir=str(cache), lock_path=str(lock), model_id=MODEL_ID)
    seq_out = np.array([[[0.0, 0, 0, 1], [0.0, 0, 0, 1], [99.0, 99, 99, 99]]])
    _wire_fake_runtime(emb, seq_out)  # mask [1,1,0] → bỏ row 99 → SEC (row 3)
    v = emb.classify("x")
    assert v.degraded is False
    assert v.label == "SEC"


def test_fake_runtime_dim_mismatch_degrades(tmp_path: Path) -> None:
    cache = _make_cache(tmp_path)
    lock = _write_lock(tmp_path)
    emb = le.LineageEmbed(cache_dir=str(cache), lock_path=str(lock), model_id=MODEL_ID)
    _wire_fake_runtime(emb, np.array([[1.0, 0.0, 0.0]]))  # dim 3 ≠ centroid 4
    v = emb.classify("x")
    assert v.degraded is True
    assert v.reason == "embedding_dim_mismatch"
    assert emb.state == "failed"


def test_verdict_wires_into_lane_a_arbitration(tmp_path: Path) -> None:
    """Nhãn của Lane B phải ánh xạ đúng qua map_label_to_lane của Lane A."""
    cache = _make_cache(tmp_path)
    lock = _write_lock(tmp_path)
    emb = le.LineageEmbed(cache_dir=str(cache), lock_path=str(lock), model_id=MODEL_ID)
    _wire_fake_runtime(emb, np.array([[0.0, 0.0, 0.0, 1.0]]))
    sec = emb.classify("attack-ish")
    assert map_label_to_lane(sec.label) == LANE_SECURITY
    # Rule 1 Lane A: một mình L1 báo SECURITY cũng đủ rẽ LANE_SECURITY.
    decision = arbitrate(l1=sec, l2=None)
    assert decision.lane == LANE_SECURITY
    assert not hasattr(decision, "bypass_verdict_pass")

    emb.close()
    cache2 = _make_cache(tmp_path / "again2", with_model=False)
    lock2 = _write_lock(tmp_path / "again2")
    emb2 = le.LineageEmbed(cache_dir=str(cache2), lock_path=str(lock2), model_id=MODEL_ID)
    dv = emb2.classify("anything")
    assert dv.label == "DEGRADED"
    assert map_label_to_lane(dv.label) is None  # lineage chết không bỏ phiếu
    d2 = arbitrate(l1=dv, l2=None, degraded=True)
    assert d2.lane == LANE_FACTUAL  # C2: DEGRADED → SAFE_FACTUAL, không CHATBOT
    assert not hasattr(d2, "bypass_verdict_pass")


# ============================================================================
# 5. API mức module
# ============================================================================
def test_module_classify_uses_env_cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = _make_cache(tmp_path, with_model=False)
    monkeypatch.setenv(le.MODEL_CACHE_ENV, str(cache))
    le.reset_default()
    v = le.classify("qua API mức module")
    assert v.degraded is True
    # default singleton đọc lock của repo (active = model int8 thật) và cache
    # lấy từ env → cache không có model active ⇒ model_file_missing (không crash).
    assert v.reason == "model_file_missing"
    le.reset_default()


def test_default_cache_dir_is_outside_repo(tmp_path: Path) -> None:
    """Owner ràng buộc: cache dir mặc định phải NGOÀI repo (trong home user)."""
    default = le.resolve_cache_dir({})
    assert le.REPO_ROOT not in default.parents
    # env thắng mặc định
    assert le.resolve_cache_dir({le.MODEL_CACHE_ENV: str(tmp_path)}) == tmp_path
