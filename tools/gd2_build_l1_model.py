#!/usr/bin/env python
"""[L1-MODEL 2026-10-31] Dựng artifact L1 (LineageEmbed) từ model LOCAL đã có — KHÔNG tải mạng.

Tại sao có tool này (Chuỗi Tại Sao, rút gọn):
- L1 chưa từng chạy vì (1) model local duy nhất là bản ONNX **fp16** 235,052,644 byte
  (> trần owner 150MB), (2) adapter chỉ cấp ``input_ids`` trong khi model BERT cần 3
  input, (3) không có prototype/centroid nào được dựng, (4) onnxruntime/tokenizers
  chưa được cài.
- Tool này biến model local đó thành artifact ≤150MB có hash, lặp lại được:
  fp16 → fp32 → lượng tử hoá động int8 (per-tensor, MatMul/Attention/Gather), copy
  tokenizer, nhúng các prototype đa ngôn ngữ (DATA có hash) thành ``centroids.npy``.

Chạy (máy dựng — cần thêm gói ``onnx``, KHÔNG cần trong image runtime):

    python tools/gd2_build_l1_model.py --source-dir <thư mục chứa model_optimized.onnx
        + tokenizer.json> --cache-dir <đích>            # mặc định: kiểm khớp lock
    python tools/gd2_build_l1_model.py ... --write-lock  # cập nhật hash vào lock (review!)

Cổng fail-closed (exit != 0, không ghi lock):
- sha256 nguồn (model fp16 + tokenizer) phải khớp ``source`` trong lock;
- file prototype phải khớp canonical sha256 trong ``config/data_integrity.lock.json``;
- model int8 phải ≤ ``MAX_MODEL_BYTES``;
- (mặc định) sha256 của 4 artifact dựng ra phải khớp lock — khác máy/khác phiên bản
  onnx/onnxruntime có thể cho byte khác ⇒ tool báo lệch, người duyệt quyết định;
- sau khi dựng, CHÍNH adapter runtime nạp artifact qua lock và mọi prototype phải
  được phân loại về đúng lớp của nó (tự nhất quán) — nếu không ⇒ exit 6.

Thoát: 0 ok · 2 đối số · 3 nguồn sai hash/thiếu · 4 vượt cap · 5 lock/prototype ·
6 kiểm tự nhất quán hỏng · 7 artifact lệch lock (chế độ kiểm).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scp.runtime.semantic_router.lineage_embed import (  # noqa: E402
    CENTROID_LABELS_FILENAME,
    CENTROIDS_FILENAME,
    CLASSES,
    LOCK_ARTIFACTS,
    MAX_MODEL_BYTES,
    MODEL_FILENAME,
    TOKENIZER_FILENAME,
    LineageEmbed,
)

LOCK_PATH = REPO / "config" / "gd2_models.lock.json"
INTEGRITY_LOCK = REPO / "config" / "data_integrity.lock.json"
SEEDS_RELPATH = "data/semantic_router/l1_seed_utterances.json"
SOURCE_MODEL_FILE = "model_optimized.onnx"
DEFAULT_MAX_TOKENS = 128  # khớp tokenizer_config.max_length của model nguồn


def _sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_sha256(record: Any) -> str:
    """Cùng phương pháp với config/data_integrity.lock.json (hash_method)."""
    return hashlib.sha256(
        json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def load_seeds(repo: Path = REPO) -> tuple[list[str], list[str]]:
    """Prototype (texts, labels) — CHỈ khi canonical sha256 khớp integrity lock."""
    path = repo / SEEDS_RELPATH
    record = json.loads(path.read_text(encoding="utf-8"))
    lock = json.loads((repo / "config" / "data_integrity.lock.json").read_text(encoding="utf-8"))
    want = lock["records"][SEEDS_RELPATH]["canonical_sha256"]
    got = canonical_sha256(record)
    if got != want:
        raise SystemExit(f"[exit 5] prototype hash lệch: lock={want} actual={got}")
    texts: list[str] = []
    labels: list[str] = []
    for cls in CLASSES:
        for lang in sorted(record["classes"][cls]):
            for t in record["classes"][cls][lang]:
                texts.append(t)
                labels.append(cls)
    return texts, labels


def _norm(t: str) -> str:
    return " ".join("".join(ch for ch in unicodedata.normalize("NFKC", t).lower()
                            if ch.isalnum() or ch.isspace()).split())


def leak_check(seeds: list[str], files: list[Path], extra: list[str] = ()) -> list[str]:
    """Prototype KHÔNG được trùng (sau chuẩn hoá) bất kỳ câu eval/probe nào."""
    seen = {_norm(s): s for s in seeds}
    hits: list[str] = []
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                txt = json.loads(line).get("text", "")
                if _norm(txt) in seen:
                    hits.append(f"{f.name}:{json.loads(line).get('qid')}")
    for e in extra:
        if _norm(e) in seen:
            hits.append(f"probe:{e}")
    return hits


def quantize(source_model: Path, out_model: Path) -> None:
    """fp16 → fp32 → int8 động (per-tensor). Import ``onnx`` LAZY (chỉ máy dựng)."""
    import numpy as np
    import onnx
    from onnx import TensorProto, numpy_helper
    from onnxruntime.quantization import QuantType, quantize_dynamic

    m = onnx.load(str(source_model))
    for i, t in enumerate(m.graph.initializer):
        if t.data_type == TensorProto.FLOAT16:
            arr = numpy_helper.to_array(t).astype(np.float32)
            m.graph.initializer[i].CopyFrom(numpy_helper.from_array(arr, t.name))
    for n in m.graph.node:
        for at in n.attribute:
            if n.op_type == "Cast" and at.name == "to" and at.i == TensorProto.FLOAT16:
                at.i = TensorProto.FLOAT
            if n.op_type == "Constant" and at.name == "value" and at.t.data_type == TensorProto.FLOAT16:
                at.t.CopyFrom(numpy_helper.from_array(numpy_helper.to_array(at.t).astype(np.float32)))
    for vi in list(m.graph.value_info) + list(m.graph.output) + list(m.graph.input):
        if vi.type.tensor_type.elem_type == TensorProto.FLOAT16:
            vi.type.tensor_type.elem_type = TensorProto.FLOAT
    with tempfile.TemporaryDirectory() as td:
        fp32 = Path(td) / "fp32.onnx"
        onnx.save(m, str(fp32))
        del m
        quantize_dynamic(
            str(fp32), str(out_model),
            weight_type=QuantType.QInt8,
            op_types_to_quantize=["MatMul", "Attention", "Gather"],
            per_channel=False, reduce_range=False,
        )


def embed(model: Path, tokenizer: Path, texts: list[str], max_tokens: int = DEFAULT_MAX_TOKENS) -> Any:
    """Cùng phép nhúng với adapter: cấp đủ input, mean-pool theo mask, L2-normalize."""
    import numpy as np
    import onnxruntime as ort
    import tokenizers

    so = ort.SessionOptions()
    so.intra_op_num_threads = 1
    so.inter_op_num_threads = 1
    sess = ort.InferenceSession(str(model), sess_options=so, providers=["CPUExecutionProvider"])
    tok = tokenizers.Tokenizer.from_file(str(tokenizer))
    tok.enable_truncation(max_length=max_tokens)
    names = [i.name for i in sess.get_inputs()]
    rows = []
    for t in texts:
        enc = tok.encode(t)
        by = {"input_ids": enc.ids, "attention_mask": enc.attention_mask, "token_type_ids": enc.type_ids}
        out = sess.run(None, {n: np.asarray([by[n]], dtype=np.int64) for n in names})[0][0]
        m = np.asarray(enc.attention_mask, dtype=np.float64)[:, None]
        v = (out.astype(np.float64) * m).sum(0) / m.sum()
        rows.append(v / np.linalg.norm(v))
    return np.asarray(rows, dtype=np.float32)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source-dir", required=True)
    ap.add_argument("--cache-dir", required=True)
    ap.add_argument("--lock-path", default=str(LOCK_PATH))
    ap.add_argument("--write-lock", action="store_true", help="ghi hash artifact vào lock (cần review)")
    ap.add_argument("--leak-check", nargs="*", default=[], help="jsonl eval có trường text")
    ap.add_argument("--probe", nargs="*", default=[], help="câu probe không được nằm trong prototype")
    a = ap.parse_args(argv)

    lock_path = Path(a.lock_path)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    model_id = lock.get("active")
    entry = (lock.get("models") or {}).get(model_id)
    if not model_id or not isinstance(entry, dict):
        print("[exit 5] lock không có active model", file=sys.stderr)
        return 5
    src = entry.get("source") or {}
    sdir = Path(a.source_dir)
    for fname, key in ((SOURCE_MODEL_FILE, "model_sha256"), (TOKENIZER_FILENAME, "tokenizer_sha256")):
        p = sdir / fname
        if not p.is_file():
            print(f"[exit 3] thiếu nguồn {p}", file=sys.stderr)
            return 3
        got = _sha_file(p)
        if got != src.get(key):
            print(f"[exit 3] sha256 nguồn lệch {fname}: lock={src.get(key)} actual={got}", file=sys.stderr)
            return 3

    texts, labels = load_seeds()
    hits = leak_check(texts, [Path(f) for f in a.leak_check], a.probe)
    if hits:
        print(f"[exit 5] prototype trùng câu eval/probe: {hits[:10]}", file=sys.stderr)
        return 5

    out_dir = Path(a.cache_dir) / model_id
    out_dir.mkdir(parents=True, exist_ok=True)
    quantize(sdir / SOURCE_MODEL_FILE, out_dir / MODEL_FILENAME)
    size = (out_dir / MODEL_FILENAME).stat().st_size
    if size > MAX_MODEL_BYTES:
        print(f"[exit 4] int8 {size} > cap {MAX_MODEL_BYTES}", file=sys.stderr)
        return 4
    shutil.copyfile(sdir / TOKENIZER_FILENAME, out_dir / TOKENIZER_FILENAME)
    import numpy as np

    np.save(out_dir / CENTROIDS_FILENAME, embed(out_dir / MODEL_FILENAME, out_dir / TOKENIZER_FILENAME, texts,
                   int(entry.get("max_input_tokens", DEFAULT_MAX_TOKENS))))
    (out_dir / CENTROID_LABELS_FILENAME).write_text(json.dumps(labels), encoding="utf-8")

    built = {
        "sha256": _sha_file(out_dir / MODEL_FILENAME),
        "size_bytes": size,
        "artifacts_sha256": {n: _sha_file(out_dir / n) for n in LOCK_ARTIFACTS},
    }
    print(json.dumps({"model_id": model_id, **built}, indent=1))

    if a.write_lock:
        entry.update(built)
        tmp = lock_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, lock_path)
    else:
        mismatch = [k for k in ("sha256", "size_bytes") if entry.get(k) != built[k]]
        mismatch += [n for n in LOCK_ARTIFACTS
                     if (entry.get("artifacts_sha256") or {}).get(n) != built["artifacts_sha256"][n]]
        if mismatch:
            print(f"[exit 7] artifact lệch lock: {mismatch}", file=sys.stderr)
            return 7

    # Tự nhất quán: chính adapter runtime (qua lock) phải nạp được và xếp mọi
    # prototype về đúng lớp của nó (không có cổng phiếu ở bước này: so argmax).
    emb = LineageEmbed(cache_dir=a.cache_dir, lock_path=str(lock_path), model_id=model_id)
    st = emb.status()
    if st["state"] != "ready":
        print(f"[exit 6] adapter không nạp được artifact: {st}", file=sys.stderr)
        return 6
    wrong = [(t, lab, emb.classify(t).labels) for t, lab in zip(texts, labels)
             if max(emb.classify(t).labels.items(), key=lambda kv: kv[1])[0] != lab]
    emb.close()
    if wrong:
        print(f"[exit 6] {len(wrong)} prototype bị xếp sai lớp: {[w[:2] for w in wrong[:5]]}", file=sys.stderr)
        return 6
    print(f"[OK] {len(texts)} prototype tự nhất quán; artifact tại {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
