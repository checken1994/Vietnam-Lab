#!/usr/bin/env python3
"""Sinh cặp khóa Ed25519 cho khóa C (verifier daemon, phương án 1.A).

    python tools/verifier_keygen.py --out-dir DIR [--keyring-out KEYRING.json]

* Ghi ``DIR/verifier_c_private.key`` (base64 seed 32 byte) bằng O_CREAT|O_EXCL, mode 0o600 — không
  ghi đè file có sẵn. File này CHỈ dành cho tiến trình daemon (``--private-key-file``).
* Ghi ``DIR/verifier_c_public.key`` và (tùy chọn) thêm entry ``{"kid","pub","status":"active"}`` vào
  public keyring JSON ``{"keys":[...]}`` cho kernel/worker.
* stdout chỉ in JSON công khai (kid, public_key, đường dẫn) — KHÔNG BAO GIỜ in private key.

Giới hạn: trên Windows, mode 0o600 không giới hạn ACL (chỉ bit read-only) — production phải đặt
ACL/OS user riêng cho file private key (CHƯA tự động hóa trong tool này).
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))  # luôn ưu tiên cây chứa tool (worktree), kể cả khi .pth thêm cây khác

PRIVATE_NAME = "verifier_c_private.key"
PUBLIC_NAME = "verifier_c_public.key"


def _write_private(path: Path, value: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(value + "\n")


def _append_keyring(path: Path, entry: dict[str, str]) -> None:
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"keys": []}
    keys = data.get("keys") if isinstance(data, dict) else None
    if not isinstance(keys, list):
        raise SystemExit(f"keyring {path} is not a {{'keys': [...]}} document")
    if any(isinstance(k, dict) and k.get("kid") == entry["kid"] for k in keys):
        raise SystemExit(f"kid {entry['kid']} already present in {path}")
    keys.append(entry)
    path.write_text(json.dumps({**data, "keys": keys}, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from scp.security.receipt_authority import keyring_entry

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", required=True, help="directory for the daemon-only private key file")
    parser.add_argument("--keyring-out", help="public keyring JSON to create or append to")
    args = parser.parse_args(argv)

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    private_path, public_path = out / PRIVATE_NAME, out / PUBLIC_NAME
    if private_path.exists():
        raise SystemExit(f"refusing to overwrite existing private key {private_path}")
    key = Ed25519PrivateKey.generate()
    raw_private = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                    serialization.NoEncryption())
    raw_public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    entry = keyring_entry(raw_public, status="active")
    public_b64, kid = entry["pub"], entry["kid"]
    _write_private(private_path, base64.b64encode(raw_private).decode("ascii"))
    public_path.write_text(public_b64 + "\n", encoding="ascii")
    if args.keyring_out:
        _append_keyring(Path(args.keyring_out), entry)
    sys.stdout.write(json.dumps({"kid": kid, "public_key": public_b64, "private_key_file": str(private_path),
                                 "public_key_file": str(public_path), "keyring_entry": entry,
                                 "keyring_file": args.keyring_out}, sort_keys=True) + "\n")
    if os.name == "nt":
        sys.stderr.write("WARNING: Windows ignores mode 0o600; restrict the private key file ACL to the "
                         "verifier service account.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
