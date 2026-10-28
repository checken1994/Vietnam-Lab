#!/usr/bin/env python
"""GĐ2 Lane B — CLI tải model L1 embedding MỘT LẦN lúc setup (không phải runtime).

Chạy THỦ CÔNG (file này KHÔNG tự chạy khi import — guard ``__main__``):

    # từ repo root D:\\scp, dùng venv của repo:
    .venv/Scripts/python.exe tools/gd2_provision_model.py --model multilingual-e5-small-int8

    # model ngoài registry — chỉ URL trực tiếp tới file .onnx int8:
    .venv/Scripts/python.exe tools/gd2_provision_model.py --model my-model \\
        --url https://example.com/model_quantized.onnx \\
        --tokenizer-url https://example.com/tokenizer.json

Tuỳ chọn:
    --cache-dir <dir>   mặc định: env SCP_GD2_MODEL_CACHE hoặc ~/.cache/scp-gd2-models
                        (LUÔN ngoài repo — owner: "không ảnh hưởng tới PC")
    --lock-path <file>  mặc định: config/gd2_models.lock.json
    --force             tải lại kể cả khi file đã tồn tại đúng sha256

Tool làm gì:
    1. Tải .onnx (+ tokenizer.json nếu có URL) về <cache>/<model_id>/, stream,
       chặn khi vượt CAP 150MB (ràng buộc owner NV1 C1-i).
    2. Hash lại file vừa tải (verify sau ghi), ghi lock: model id, sha256,
       size_bytes, source_url, license, provisioned_at; đặt ``active``.
       Ghi lock ATOMIC (tmp + os.replace) để không bao giờ để lại lock nửa vời.
    3. KHÔNG cài dependency, KHÔNG sửa scp/requirements.txt, KHÔNG sinh
       centroids (centroids.npy do Lane D/E huấn luyện từ seed có provenance —
       review §2e chống phrase-listing trong không gian vector).

Sau khi chạy xong, bật L1 bằng cách cài dependency riêng (KHÔNG vào
requirements.txt chính):

    .venv/Scripts/python.exe -m pip install onnxruntime numpy tokenizers
    # hoặc pip install -r scp/requirements-optional-ml.txt (nếu đã có file đó)

URL trong REGISTRY bên dưới là ỨNG VIÊN, ĐÃ ĐÁNH DẤU URL_UNVERIFIED: lane này
không được phép chạy mạng nên chưa verify URL/hash/license — người chạy tự
kiểm tra trang model trước khi tin. Nếu URL sai, dùng --url trực tiếp.

Thoát code: 0 = provision xong; 2 = lỗi đối số; 3 = lỗi mạng/tải; 4 = vượt cap
150MB; 5 = lỗi ghi lock/cache.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scp.runtime.semantic_router.lineage_embed import (  # noqa: E402
    CENTROIDS_FILENAME,
    CENTROID_LABELS_FILENAME,
    MAX_MODEL_BYTES,
    MODEL_FILENAME,
    TOKENIZER_FILENAME,
    resolve_cache_dir,
)

#: Owner ràng buộc (NV1 C1-i): model vendored int8 đa ngôn ngữ ≤ 150MB.
OWNER_SIZE_CAP_BYTES: int = MAX_MODEL_BYTES
#: Lock mặc định — cùng quy ước với adapter (config/gd2_models.lock.json).
DEFAULT_LOCK_PATH: Path = (
    Path(__file__).resolve().parents[1] / "config" / "gd2_models.lock.json"
)

#: Registry model ứng viên đa ngôn ngữ (review §3.1: bge-small-en KHÔNG đa
#: ngôn ngữ — không đưa vào). URL_UNVERIFIED: chưa verify trong lane này.
MODEL_REGISTRY: dict[str, dict[str, str]] = {
    "multilingual-e5-small-int8": {
        "url": (
            "URL_UNVERIFIED: https://huggingface.co/intfloat/"
            "multilingual-e5-small/resolve/main/onnx/model_quantized.onnx"
        ),
        "tokenizer_url": (
            "URL_UNVERIFIED: https://huggingface.co/intfloat/"
            "multilingual-e5-small/resolve/main/tokenizer.json"
        ),
        "license": "MIT (theo repo HF — tự kiểm lại trước khi tin)",
        "input_prefix": "query: ",
        "notes": "int8 quantized ONNX, đa ngôn ngữ (VI/ES/RU/ZH...), ~118MB.",
    },
    "paraphrase-multilingual-minilm-l12-int8": {
        "url": (
            "URL_UNVERIFIED: https://huggingface.co/sentence-transformers/"
            "paraphrase-multilingual-MiniLM-L12-v2/resolve/main/onnx/"
            "model_quantized.onnx"
        ),
        "tokenizer_url": (
            "URL_UNVERIFIED: https://huggingface.co/sentence-transformers/"
            "paraphrase-multilingual-MiniLM-L12-v2/resolve/main/tokenizer.json"
        ),
        "license": "Apache-2.0 (theo repo HF — tự kiểm lại trước khi tin)",
        "input_prefix": "",
        "notes": "int8 quantized ONNX, đa ngôn ngữ, ~46MB.",
    },
}

_EXIT_USAGE, _EXIT_NET, _EXIT_CAP, _EXIT_LOCK = 2, 3, 4, 5


def _fail(code: int, message: str) -> int:
    print(f"[gd2-provision] LỖI: {message}", file=sys.stderr)
    return code


def _download_to(url: str, dest: Path, expected_max: int = OWNER_SIZE_CAP_BYTES) -> int:
    """Stream url → dest; trả số byte; tự huỷ file khi vượt cap."""
    if url.startswith("URL_UNVERIFIED:"):
        url = url.split("URL_UNVERIFIED:", 1)[1].strip()
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    sha = hashlib.sha256()
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 - URL do người chạy đưa
            declared = resp.headers.get("Content-Length")
            if declared is not None and int(declared) > expected_max:
                raise SystemExit(_EXIT_CAP)
            with open(tmp, "wb") as fh:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > expected_max:
                        raise SystemExit(_EXIT_CAP)
                    sha.update(chunk)
                    fh.write(chunk)
    except SystemExit:
        tmp.unlink(missing_ok=True)
        raise
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise OSError(f"tải {url} thất bại: {exc}") from exc
    os.replace(tmp, dest)
    # Verify sau ghi: hash lại từ đĩa, đúng byte người dùng sẽ nạp lúc chạy.
    rehash = hashlib.sha256()
    with open(dest, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            rehash.update(chunk)
    if rehash.hexdigest() != sha.hexdigest():
        dest.unlink(missing_ok=True)
        raise OSError("sha256 sau ghi không khớp lúc stream — bỏ file")
    return total


def _upsert_lock(
    lock_path: Path,
    model_id: str,
    sha256: str,
    size_bytes: int,
    source_url: str,
    license_info: str,
    input_prefix: str,
    notes: str,
    tokenizer_sha256: str | None,
) -> dict[str, Any]:
    """Ghi/cập nhật entry model trong lock — ATOMIC (tmp + replace)."""
    lock: dict[str, Any] = {"schema_version": 1, "active": None, "models": {}}
    if lock_path.is_file():
        try:
            loaded = json.loads(lock_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("models"), dict):
                lock = loaded
        except (OSError, ValueError):
            print(
                f"[gd2-provision] CẢNH BÁO: lock {lock_path} hỏng — ghi lại từ đầu.",
                file=sys.stderr,
            )
    entry: dict[str, Any] = {
        "model_id": model_id,
        "sha256": sha256,
        "size_bytes": size_bytes,
        "source_url": source_url,
        "license": license_info,
        "int8_quantized": True,
        "multilingual": True,
        "input_prefix": input_prefix,
        "provisioned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "notes": notes,
    }
    if tokenizer_sha256:
        entry["tokenizer_sha256"] = tokenizer_sha256
    lock.setdefault("schema_version", 1)
    lock["models"][model_id] = entry
    lock["active"] = model_id
    fd, tmp_name = tempfile.mkstemp(
        dir=str(lock_path.parent), prefix=lock_path.name, suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(lock, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp_name, lock_path)
    except OSError:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return lock


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Tải model L1 embedding GĐ2 MỘT LẦN lúc setup + ghi lock."
    )
    parser.add_argument("--model", required=True, help="model id (vd multilingual-e5-small-int8)")
    parser.add_argument("--url", help="URL trực tiếp tới .onnx int8 (bắt buộc nếu id ngoài registry)")
    parser.add_argument("--tokenizer-url", help="URL tokenizer.json đi kèm model")
    parser.add_argument("--cache-dir", help="mặc định: $SCP_GD2_MODEL_CACHE hoặc ~/.cache/scp-gd2-models")
    parser.add_argument("--lock-path", default=str(DEFAULT_LOCK_PATH))
    parser.add_argument("--force", action="store_true", help="tải lại kể cả khi đã có file")
    args = parser.parse_args(argv)

    model_id = args.model.strip()
    if not model_id:
        return _fail(_EXIT_USAGE, "--model trống")
    entry = MODEL_REGISTRY.get(model_id)
    url = args.url or (entry or {}).get("url", "")
    if not url:
        return _fail(
            _EXIT_USAGE,
            f"model {model_id!r} không có trong registry — phải truyền --url",
        )

    cache_dir = Path(args.cache_dir).expanduser() if args.cache_dir else resolve_cache_dir()
    model_dir = cache_dir / model_id
    model_path = model_dir / MODEL_FILENAME
    lock_path = Path(args.lock_path).expanduser()

    # Idempotent-friendly: đã đúng file thì bỏ qua trừ --force.
    if model_path.is_file() and not args.force:
        print(f"[gd2-provision] Đã có {model_path} — bỏ qua tải (dùng --force để tải lại).")
    else:
        print(f"[gd2-provision] Tải {url} → {model_path} (cap {OWNER_SIZE_CAP_BYTES} bytes)...")
        try:
            size = _download_to(url, model_path)
        except SystemExit as cap:
            return _fail(
                _EXIT_CAP,
                f"model vượt cap owner {OWNER_SIZE_CAP_BYTES} bytes — từ chối.",
            ) if cap.code == _EXIT_CAP else _fail(_EXIT_NET, "lỗi tải")
        except OSError as exc:
            return _fail(_EXIT_NET, str(exc))
        print(f"[gd2-provision] OK {size} bytes → {model_path}")

    sha = hashlib.sha256()
    with open(model_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            sha.update(chunk)
    size_actual = model_path.stat().st_size

    tok_sha: str | None = None
    tok_url = args.tokenizer_url or (entry or {}).get("tokenizer_url", "")
    if tok_url:
        tok_path = model_dir / TOKENIZER_FILENAME
        if tok_path.is_file() and not args.force:
            print(f"[gd2-provision] Đã có {tok_path} — bỏ qua tải.")
        else:
            try:
                _download_to(tok_url, tok_path)
            except SystemExit:
                return _fail(_EXIT_NET, f"tải tokenizer thất bại: {tok_url}")
            except OSError as exc:
                return _fail(_EXIT_NET, str(exc))
        tok_sha = hashlib.sha256(tok_path.read_bytes()).hexdigest()

    try:
        _upsert_lock(
            lock_path=lock_path,
            model_id=model_id,
            sha256=sha.hexdigest(),
            size_bytes=size_actual,
            source_url=url,
            license_info=(entry or {}).get("license", "KHÔNG RÕ — tự kiểm license"),
            input_prefix=(entry or {}).get("input_prefix", ""),
            notes=(entry or {}).get("notes", "provision qua --url trực tiếp"),
            tokenizer_sha256=tok_sha,
        )
    except OSError as exc:
        return _fail(_EXIT_LOCK, f"không ghi được lock {lock_path}: {exc}")

    print(
        f"[gd2-provision] Lock đã ghi: {lock_path} (active={model_id}, "
        f"sha256={sha.hexdigest()[:16]}..., size={size_actual}).\n"
        f"[gd2-provision] Next steps:\n"
        f"  1. Cài dependency riêng (KHÔNG vào requirements.txt chính):\n"
        f"     .venv/Scripts/python.exe -m pip install onnxruntime numpy tokenizers\n"
        f"  2. Centroid: đặt {model_dir / CENTROIDS_FILENAME} + "
        f"{model_dir / CENTROID_LABELS_FILENAME} (do Lane D/E huấn luyện từ seed\n"
        f"     có provenance — thiếu centroid thì L1 vẫn DEGRADED, đó là hành vi đúng).\n"
        f"  3. Chạy test hermetic: .venv/Scripts/python.exe -m pytest "
        f"tests/T02_security/test_gd2_lineage_embed.py -v"
    )
    return 0


if __name__ == "__main__":  # KHÔNG chạy gì lúc import — chỉ khi gọi CLI trực tiếp.
    sys.exit(main())
