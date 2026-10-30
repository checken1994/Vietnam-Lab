"""[L1-MODEL 2026-10-31] L1 (LineageEmbed) chạy THẬT bằng onnxruntime + tokenizers.

Khác test_gd2_lineage_embed.py (session GIẢ), file này nạp model ONNX THẬT (fixture
tí hon trong tests/fixtures/l1_tiny, sinh bằng onnx.helper) qua đúng đường runtime:
lock → sha256 model + MỌI artifact → onnxruntime.InferenceSession → tokenizer → pool.

Đỏ-trước (trên main c9aa96e7):
  - model 3 input (BERT: input_ids/attention_mask/token_type_ids) ⇒ main chỉ cấp
    input_ids ⇒ onnxruntime "missing input" ⇒ L1 luôn DEGRADED (gốc "L1 chưa từng chạy");
  - tokenizer/centroid bị tráo ⇒ main vẫn nạp (chỉ model.onnx được hash) ⇒ fail-open;
  - lock thiếu artifacts_sha256 ⇒ main vẫn nạp;
  - calibration (temperature/vote_floor/min_cosine/disabled_votes) bị main bỏ qua;
  - /ready không có trạng thái L1; lock trong repo là TEMPLATE.

Fixture autouse gỡ onnxruntime/tokenizers khỏi sys.modules sau mỗi test để các
khẳng định import-lười của test_gd2_lineage_embed.py đúng với MỌI thứ tự chạy.
"""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

import scp.runtime.semantic_router.lineage_embed as le
from scp.runtime.router_rules import LANE_CHATBOT
from scp.runtime.semantic_router import consensus
from scp.runtime.semantic_router.arbitration import map_label_to_lane

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "l1_tiny"
MODEL_ID = "l1-tiny-fixture"
ORDER4 = ["CHAT", "FACT", "REAS", "SEC"]  # row i ↔ embedding e_i của fixture
CAL = {"temperature": 0.06, "vote_floor": {"CHAT": 0.88}, "min_cosine": {}}
# Hằng hợp đồng viết TƯỜNG MINH (không lấy từ module) để test thu thập được và đỏ
# đúng chỗ trên main — nơi chưa có LOCK_ARTIFACTS/ABSTAIN_LABEL.
ARTIFACTS = ("tokenizer.json", "centroids.npy", "centroids.labels.json")
ABSTAIN = "ABSTAIN"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _npy(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    np.save(buf, arr)
    return buf.getvalue()


def _provision(
    tmp: Path,
    *,
    model: str = "model_3in.onnx",
    centroids: np.ndarray | None = None,
    labels: list[str] | None = None,
    calibration: dict | None = CAL,
    with_artifact_hashes: bool = True,
) -> tuple[Path, Path]:
    """Cache <tmp>/cache/<id>/ + lock khớp hash — như tools/gd2_build_l1_model.py làm."""
    d = tmp / "cache" / MODEL_ID
    d.mkdir(parents=True, exist_ok=True)
    model_bytes = (FIXTURES / model).read_bytes()
    (d / le.MODEL_FILENAME).write_bytes(model_bytes)
    shutil.copyfile(FIXTURES / "tokenizer.json", d / le.TOKENIZER_FILENAME)
    (d / le.CENTROIDS_FILENAME).write_bytes(_npy(np.eye(4) if centroids is None else centroids))
    (d / le.CENTROID_LABELS_FILENAME).write_text(json.dumps(labels or ORDER4), encoding="utf-8")
    entry: dict = {
        "model_id": MODEL_ID,
        "sha256": _sha(model_bytes),
        "size_bytes": len(model_bytes),
        "input_prefix": "",
    }
    if with_artifact_hashes:
        entry["artifacts_sha256"] = {n: _sha((d / n).read_bytes()) for n in ARTIFACTS}
    if calibration is not None:
        entry["calibration"] = calibration
    lock = tmp / "lock.json"
    lock.write_text(json.dumps({"schema_version": 1, "active": MODEL_ID, "models": {MODEL_ID: entry}}),
                    encoding="utf-8")
    return d.parent, lock


def _emb(cache: Path, lock: Path) -> le.LineageEmbed:
    return le.LineageEmbed(cache_dir=str(cache), lock_path=str(lock), model_id=MODEL_ID)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(le.MODEL_CACHE_ENV, raising=False)
    monkeypatch.delenv(le.MODEL_ID_ENV, raising=False)
    le.reset_default()
    before = set(sys.modules)
    yield
    le.reset_default()
    # Không để onnxruntime/tokenizers rò sang test khác: test_gd2_lineage_embed.py
    # khẳng định import lười (onnxruntime KHÔNG nằm trong sys.modules) — thứ tự
    # chạy (xdist/random/chọn file) không được làm hỏng khẳng định đó.
    for name in [m for m in sys.modules if m not in before]:
        if name.split(".")[0] in {"onnxruntime", "tokenizers"}:
            del sys.modules[name]


# ---------------------------------------------------------------------------
# 1. L1 nạp được bằng onnxruntime thật + hash khớp
# ---------------------------------------------------------------------------
def test_real_onnxruntime_three_input_model_loads_and_classifies(tmp_path: Path) -> None:
    cache, lock = _provision(tmp_path)
    emb = _emb(cache, lock)
    v = emb.classify("alpha")
    assert v.degraded is False, v.reason
    assert v.reason == "ok"
    assert v.label == "CHAT"
    assert v.confidence > 0.99  # temperature 0.06 từ lock.calibration
    assert emb.classify("delta").label == "SEC"
    assert emb.status() == {"state": "ready", "reason": "ok", "model_id": MODEL_ID}
    assert "onnxruntime" in sys.modules  # thật sự là onnxruntime, không phải session giả
    emb.close()


def test_multi_prototype_rows_score_by_class_max(tmp_path: Path) -> None:
    rows = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, 1, 0, 0], [0, 0, 0, 1], [0.0, 0.1, 0.1, 0.0]])
    cache, lock = _provision(tmp_path, centroids=rows, labels=["CHAT", "CHAT", "FACT", "SEC", "REAS"])
    v = _emb(cache, lock).classify("gamma")  # e2 khớp prototype CHAT thứ hai
    assert v.degraded is False, v.reason
    assert v.label == "CHAT"


# ---------------------------------------------------------------------------
# 2. Hash lệch / thiếu ⇒ DEGRADED, không bao giờ bỏ phiếu
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("artifact", ARTIFACTS)
def test_tampered_artifact_fails_closed(tmp_path: Path, artifact: str) -> None:
    # model 1 input: loại trừ lỗi input — chỉ còn cổng hash quyết định.
    cache, lock = _provision(tmp_path, model="model_1in.onnx")
    target = cache / MODEL_ID / artifact
    if artifact == "centroids.npy":
        # tráo centroid sao cho MỌI câu "delta" thành CHAT (kẻ tấn công mở cửa hội thoại)
        target.write_bytes(_npy(np.array([[0, 0, 0, 1], [0, 1, 0, 0], [0, 0, 1, 0], [1, 0, 0, 0]], dtype=float)))
    elif artifact == "centroids.labels.json":
        target.write_text(json.dumps(["SEC", "FACT", "REAS", "CHAT"]), encoding="utf-8")
    else:
        tok = json.loads(target.read_text(encoding="utf-8"))
        tok["model"]["vocab"]["delta"], tok["model"]["vocab"]["alpha"] = 1, 4
        target.write_text(json.dumps(tok), encoding="utf-8")
    emb = _emb(cache, lock)
    v = emb.classify("delta")
    assert v.degraded is True
    assert v.reason == "artifact_sha256_mismatch"
    assert v.label == "DEGRADED" and map_label_to_lane(v.label) is None
    assert emb.status()["state"] == "unavailable"


def test_lock_without_artifact_hashes_fails_closed(tmp_path: Path) -> None:
    cache, lock = _provision(tmp_path, model="model_1in.onnx", with_artifact_hashes=False)
    v = _emb(cache, lock).classify("alpha")
    assert v.degraded is True
    assert v.reason == "lock_missing_artifact_hashes"


def test_model_sha256_mismatch_never_creates_session(tmp_path: Path) -> None:
    cache, lock = _provision(tmp_path)
    (cache / MODEL_ID / le.MODEL_FILENAME).write_bytes((FIXTURES / "model_1in.onnx").read_bytes()[:-1] + b"\x00")
    emb = _emb(cache, lock)
    calls: list[int] = []
    orig = emb._load_runtime
    emb._load_runtime = lambda b: (calls.append(1), orig(b))[1]  # type: ignore[method-assign]
    v = emb.classify("alpha")
    assert v.degraded is True
    assert v.reason in {"size_mismatch", "sha256_mismatch"}
    assert calls == []


def test_missing_onnxruntime_is_reported_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache, lock = _provision(tmp_path)
    monkeypatch.setitem(sys.modules, "onnxruntime", None)  # import ⇒ ImportError
    emb = _emb(cache, lock)
    assert emb.status() == {"state": "unavailable", "reason": "onnxruntime_not_installed", "model_id": MODEL_ID}
    assert emb.classify("alpha").degraded is True


@pytest.mark.parametrize("bad", [{"temperature": 0}, {"temperature": 0.06, "vote_floor": {"CHAT": 2}},
                                 {"temperature": 0.06, "disabled_votes": ["NOPE"]}, "x"])
def test_invalid_calibration_fails_closed(tmp_path: Path, bad) -> None:
    cache, lock = _provision(tmp_path, calibration=bad)
    v = _emb(cache, lock).classify("alpha")
    assert v.degraded is True
    assert v.reason == "calibration_invalid"


# ---------------------------------------------------------------------------
# 3. Cổng phiếu đã calibrate (lock) — L1 không chắc ⇒ ABSTAIN (= đường như L1 chết)
# ---------------------------------------------------------------------------
def test_calibrated_gates_abstain(tmp_path: Path) -> None:
    cal = {"temperature": 0.06, "vote_floor": {"CHAT": 0.88}, "min_cosine": {"CHAT": 0.95},
           "disabled_votes": ["SEC"]}
    cache, lock = _provision(tmp_path, calibration=cal)
    emb = _emb(cache, lock)
    strong = emb.classify("alpha")  # cos(CHAT)=1.0 ≥ 0.95
    assert strong.label == "CHAT" and not strong.abstained
    weak = emb.classify("alpha alpha beta")  # cos(CHAT)≈0.894 < 0.95 dù xác suất > 0.88
    assert weak.confidence >= 0.88
    assert weak.abstained is True and weak.label == ABSTAIN
    assert map_label_to_lane(weak.label) is None
    sec = emb.classify("delta")  # SEC thắng nhưng phiếu SEC bị tắt
    assert sec.abstained is True and sec.label == ABSTAIN
    assert sec.degraded is False


# ---------------------------------------------------------------------------
# 4. End-to-end cổng ngữ nghĩa: CHATBOT CHỈ khi L1 thật + L2 cùng đồng ý
# ---------------------------------------------------------------------------
def _stub_l2(monkeypatch: pytest.MonkeyPatch, label: str, conf: float) -> None:
    from scp.runtime.semantic_router import lineage_slm

    async def fake_classify_l2(question, gateway=None, **kw):  # noqa: ANN001
        return lineage_slm.L2Outcome(
            verdict=lineage_slm.L2SLMVerdict(label=label, confidence=conf),
            degraded=False,
            reason="ok",
            elapsed_ms=1.0,
            provider="stub",
            input_sha256="0" * 64,
            request_id="t",
        )

    monkeypatch.setattr(lineage_slm, "classify_l2", fake_classify_l2)


def test_semantic_gate_dual_consensus_with_real_l1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache, lock = _provision(tmp_path)
    monkeypatch.setattr(le, "_default", _emb(cache, lock))
    _stub_l2(monkeypatch, "CHATBOT", 0.95)
    d = consensus.semantic_gate_sync("alpha")
    assert d is not None and d.lane == LANE_CHATBOT and d.via == consensus.CONSENSUS_VIA
    # L1 thật báo FACT ⇒ không hỏi L2, không CHATBOT dù L2 (stub) sẽ nói CHATBOT
    assert consensus.semantic_gate_sync("beta") is None


def test_semantic_gate_tampered_l1_never_chatbot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache, lock = _provision(tmp_path, model="model_1in.onnx")
    # kẻ tấn công tráo centroid để "delta" (vốn SEC) thành CHAT
    (cache / MODEL_ID / le.CENTROIDS_FILENAME).write_bytes(
        _npy(np.array([[0, 0, 0, 1], [0, 1, 0, 0], [0, 0, 1, 0], [1, 0, 0, 0]], dtype=float)))
    monkeypatch.setattr(le, "_default", _emb(cache, lock))
    _stub_l2(monkeypatch, "CHATBOT", 0.99)
    assert consensus.l1_verdict("delta") is None
    assert consensus.semantic_gate_sync("delta") is None


# ---------------------------------------------------------------------------
# 5. /ready báo trạng thái L1 thật
# ---------------------------------------------------------------------------
def test_ready_reports_semantic_l1_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scp.api_server_parts.server_helpers import llm_readiness_checks

    missing = tmp_path / "missing"
    missing.mkdir()
    _, lock = _provision(tmp_path)
    monkeypatch.setattr(le, "_default", le.LineageEmbed(cache_dir=str(missing), lock_path=str(lock),
                                                        model_id=MODEL_ID))
    out = llm_readiness_checks()
    assert out["semantic_l1"] == "unavailable"
    assert out["semantic_l1_reason"] == "model_file_missing"

    cache, lock2 = _provision(tmp_path / "ok")
    monkeypatch.setattr(le, "_default", _emb(cache, lock2))
    out2 = llm_readiness_checks()
    assert out2["semantic_l1"] == "ok"
    assert "semantic_l1_reason" not in out2
    assert MODEL_ID not in json.dumps(out2)  # không lộ model id qua /ready


# ---------------------------------------------------------------------------
# 6. Lock thật trong repo: model ≤150MB, hash đủ, calibration fail-closed
# ---------------------------------------------------------------------------
def test_shipped_lock_is_complete_and_conservative() -> None:
    lock = json.loads((le.REPO_ROOT / "config" / "gd2_models.lock.json").read_text(encoding="utf-8"))
    mid = lock["active"]
    e = lock["models"][mid]
    assert le._is_sha256_hex(e["sha256"])
    assert 0 < e["size_bytes"] <= le.MAX_MODEL_BYTES <= 150 * 1024 * 1024
    assert set(e["artifacts_sha256"]) == set(ARTIFACTS) == set(le.LOCK_ARTIFACTS)
    assert all(le._is_sha256_hex(v) for v in e["artifacts_sha256"].values())
    assert le._is_sha256_hex(e["source"]["model_sha256"]) and le._is_sha256_hex(e["source"]["tokenizer_sha256"])
    cal = e["calibration"]
    assert 0 < cal["temperature"] <= 1
    assert cal["vote_floor"]["CHAT"] >= 0.88  # không thấp hơn ngưỡng đồng thuận mặc định
    assert "SEC" in cal["disabled_votes"]  # độ chính xác phiếu SEC của L1 chưa được chứng minh
    assert e["multilingual"] is True and e["int8_quantized"] is True
    assert "Apache-2.0" in e["license"]


def test_seed_prototypes_are_hash_locked_multilingual_data() -> None:
    from tools.gd2_build_l1_model import SEEDS_RELPATH, canonical_sha256, load_seeds

    record = json.loads((le.REPO_ROOT / SEEDS_RELPATH).read_text(encoding="utf-8"))
    integrity = json.loads((le.REPO_ROOT / "config" / "data_integrity.lock.json").read_text(encoding="utf-8"))
    assert canonical_sha256(record) == integrity["records"][SEEDS_RELPATH]["canonical_sha256"]
    langs = {lang for cls in record["classes"].values() for lang in cls}
    assert len(langs) >= 12
    for cls in le.CLASSES:  # mọi lớp phủ cùng một tập ngôn ngữ, cùng số câu (không lệch ngôn ngữ)
        assert set(record["classes"][cls]) == langs
        assert len({len(v) for v in record["classes"][cls].values()}) == 1
    texts, labels = load_seeds()
    assert set(labels) == set(le.CLASSES) and len(texts) == len(labels)
