"""Verifier daemon khóa C (1.A) — tiến trình OS RIÊNG giữ private key C; transport + vòng lặp.

Private key C chỉ được nạp TRONG tiến trình này, qua secret_registry
``SCP_VERIFIER_PRIVATE_KEY[_FILE]`` trong env CỦA DAEMON; worker/kernel chỉ giữ public keyring.
Daemon KHÔNG ghi DB kernel, KHÔNG commit — chỉ thẩm định độc lập (verifier_engine) rồi chứng
thực bằng receipt vr2 (codec của lane L1, scp.security.receipt_authority).

Transport (cùng một giao thức frame JSON, mọi frame mang ``auth`` = IPC token):
  * ``stdio``: JSONL qua stdin/stdout của tiến trình con — dùng cho test/soak/prober (cha giữ pipe).
  * ``pipe``: named pipe Windows (``\\\\.\\pipe\\...``) hoặc UDS (POSIX) qua
    multiprocessing.connection với ``authkey`` = IPC token (bắt tay HMAC, sai -> đóng kết nối) —
    hướng production. Server chỉ dùng recv_bytes/send_bytes (KHÔNG unpickle dữ liệu client).
IPC token: env ``SCP_VERIFIER_IPC_TOKEN`` hoặc ``--ipc-token-file``; thiếu -> không khởi động.
"""
from __future__ import annotations

import argparse
import base64
import collections
import json
import logging
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, BinaryIO

from scp.verifier_service.verifier_engine import (
    DEFAULT_TTL_SECONDS,
    DEFAULT_VERIFIER_ID,
    MAX_FRAME_BYTES,
    VerifierEngine,
)

logger = logging.getLogger("scp.verifier_service.daemon")

PRIVATE_KEY_SECRET = "SCP_VERIFIER_PRIVATE_KEY"
IPC_TOKEN_ENV = "SCP_VERIFIER_IPC_TOKEN"
EXIT_CONFIG_ERROR = 2


class DaemonConfigError(RuntimeError):
    """Cấu hình daemon không an toàn/không đủ — daemon từ chối khởi động (fail-closed)."""


def _decode_raw_key(text: str, what: str) -> bytes:
    try:
        raw = base64.b64decode(text.strip(), validate=True)
    except (ValueError, TypeError) as exc:
        raise DaemonConfigError(f"{what} is not valid base64") from exc
    if len(raw) != 32:
        raise DaemonConfigError(f"{what} must be a raw 32-byte Ed25519 key")
    return raw


def load_private_key() -> bytes:
    """Nạp seed Ed25519 của key C qua secret_registry (``SCP_VERIFIER_PRIVATE_KEY[_FILE]``, HC-08).

    Chỉ gọi trong tiến trình daemon. Sau khi nạp, xóa cả hai biến khỏi env của daemon để không
    lọt sang tiến trình con của nó.
    """
    from scp.security.secret_registry import resolve

    text = resolve(PRIVATE_KEY_SECRET) or ""
    for name in (PRIVATE_KEY_SECRET, f"{PRIVATE_KEY_SECRET}_FILE"):
        os.environ.pop(name, None)
    if not text:
        raise DaemonConfigError(f"{PRIVATE_KEY_SECRET}[_FILE] is required (missing or unreadable)")
    return _decode_raw_key(text, "verifier private key")


class ReceiptSigner:
    """Bao private key C. Ký qua codec vr2 DUY NHẤT của lane L1 (không tự viết codec)."""

    def __init__(self, private_raw: bytes) -> None:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        from scp.security import receipt_authority as ra

        self._ra = ra
        self._key = Ed25519PrivateKey.from_private_bytes(private_raw)
        public_raw = self._key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.public_key_b64 = base64.b64encode(public_raw).decode("ascii")
        self.kid = ra.compute_kid(public_raw)
        self.domain = ra.RECEIPT_DOMAIN_NAME
        self.max_ttl_seconds = ra.MAX_TTL_SECONDS

    def assert_role_separation(self) -> None:
        """Key C không được trùng public key của approval issuer (key B)."""
        from scp.security.secret_registry import resolve

        issuer_pub = (resolve("SCP_APPROVAL_ISSUER_PUBLIC_KEY") or "").strip()
        if issuer_pub and issuer_pub == self.public_key_b64:
            raise DaemonConfigError("key C must not reuse the approval issuer key (role separation)")

    def mint(self, **binding: Any) -> tuple[str, dict[str, Any]]:
        token = self._ra.mint_vr2(self._key, kid=self.kid, **binding)
        claims = self._ra.parse_vr2(token)
        return token, claims


def serve_stdio(engine: VerifierEngine, stdin: BinaryIO, stdout: BinaryIO) -> int:
    """JSONL: một frame/dòng vào, một frame/dòng ra. EOF stdin -> thoát 0."""
    while True:
        line = stdin.readline(MAX_FRAME_BYTES + 1)
        if not line:
            return 0
        if len(line) > MAX_FRAME_BYTES and not line.endswith(b"\n"):
            while line and not line.endswith(b"\n"):  # bỏ phần còn lại của frame quá cỡ
                line = stdin.readline(MAX_FRAME_BYTES + 1)
            response = engine.refuse_frame("frame_too_large", f"frame > {MAX_FRAME_BYTES} bytes")
        else:
            line = line.strip()
            if not line:
                continue
            response = engine.handle_frame(line)
        stdout.write(json.dumps(response, sort_keys=True).encode("utf-8") + b"\n")
        stdout.flush()


def serve_pipe(engine: VerifierEngine, address: str, authkey: bytes, ready_file: str | None = None) -> int:
    """Named pipe (Windows) / UDS (POSIX); mỗi kết nối = một request/response."""
    from multiprocessing import AuthenticationError
    from multiprocessing.connection import Listener

    family = "AF_PIPE" if address.startswith("\\\\.\\pipe\\") else "AF_UNIX"
    with Listener(address, family=family, authkey=authkey) as listener:
        if ready_file:
            Path(ready_file).write_text(address, encoding="utf-8")
        logger.info("verifier daemon listening on %s (%s)", address, family)
        while True:
            try:
                conn = listener.accept()
            except AuthenticationError:
                engine.refuse_frame("unauthorized", "pipe client failed IPC authentication")
                logger.warning("verifier pipe: client failed IPC authentication")
                continue
            except OSError as exc:
                logger.warning("verifier pipe accept failed: %s", exc)
                continue
            with conn:
                try:
                    raw = conn.recv_bytes(MAX_FRAME_BYTES)
                except (OSError, EOFError) as exc:
                    logger.warning("verifier pipe: unreadable frame: %s", type(exc).__name__)
                    continue
                conn.send_bytes(json.dumps(engine.handle_frame(raw), sort_keys=True).encode("utf-8"))


# ----------------------------------------------- phía client của transport (client.py dùng)
class ChannelError(RuntimeError):
    """Transport hỏng (daemon chết/timeout/bắt tay sai) — client đổi thành VerifierUnavailableError."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class StdioChannel:
    """Tiến trình daemon con; một luồng bơm stdout thành hàng đợi để đọc có timeout."""

    def __init__(self, argv: list[str], env: dict[str, str], cwd: str) -> None:
        self._proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, env=env, cwd=cwd)
        self._lines: queue.Queue[bytes | None] = queue.Queue()
        self.stderr_tail: collections.deque[str] = collections.deque(maxlen=40)
        self._lock = threading.Lock()
        self._broken: str | None = None
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()

    @property
    def pid(self) -> int:
        return self._proc.pid

    def _pump_stdout(self) -> None:
        for line in iter(self._proc.stdout.readline, b""):
            self._lines.put(line)
        self._lines.put(None)

    def _pump_stderr(self) -> None:
        for line in iter(self._proc.stderr.readline, b""):
            self.stderr_tail.append(line.decode("utf-8", "replace").rstrip())

    def _fail(self, code: str, why: str) -> ChannelError:
        self._broken = why
        self.kill()
        tail = " | ".join(list(self.stderr_tail)[-3:])
        return ChannelError(code, f"{why} (rc={self._proc.poll()}) {tail}".strip())

    def exchange(self, data: bytes, timeout: float) -> bytes:
        with self._lock:
            if self._broken:
                raise ChannelError("daemon_unavailable", f"channel closed: {self._broken}")
            try:
                self._proc.stdin.write(data + b"\n")
                self._proc.stdin.flush()
            except (OSError, ValueError) as exc:
                raise self._fail("daemon_unavailable", f"daemon stdin closed: {type(exc).__name__}") from exc
            try:
                line = self._lines.get(timeout=timeout)
            except queue.Empty as exc:  # trả lời muộn sẽ lệch khung -> đóng hẳn kênh
                raise self._fail("daemon_timeout", f"no response within {timeout:.1f}s") from exc
            if line is None:
                raise self._fail("daemon_unavailable", "daemon exited")
            return line

    def kill(self) -> None:
        if self._proc.poll() is None:
            self._proc.kill()
        self._proc.wait(timeout=10)

    def close(self) -> None:
        if self._proc.poll() is None:
            try:
                self._proc.stdin.close()  # EOF -> daemon thoát 0
                self._proc.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                self.kill()
        self._broken = self._broken or "closed"


class PipeChannel:
    """Named pipe/UDS; bắt tay HMAC multiprocessing bằng IPC token, một kết nối mỗi request."""

    def __init__(self, address: str, authkey: bytes) -> None:
        self.address = address
        self._family = "AF_PIPE" if address.startswith("\\\\.\\pipe\\") else "AF_UNIX"
        self._authkey = authkey

    def exchange(self, data: bytes, timeout: float) -> bytes:
        from multiprocessing import AuthenticationError
        from multiprocessing.connection import Client

        try:
            with Client(self.address, family=self._family, authkey=self._authkey) as conn:
                conn.send_bytes(data)
                if not conn.poll(timeout):
                    raise ChannelError("daemon_timeout", f"no response within {timeout:.1f}s")
                return conn.recv_bytes(MAX_FRAME_BYTES)
        except AuthenticationError as exc:
            raise ChannelError("unauthorized", "pipe IPC authentication failed") from exc
        except (OSError, EOFError) as exc:
            raise ChannelError("daemon_unavailable", f"pipe transport: {type(exc).__name__}") from exc

    def close(self) -> None:
        return None


def _read_ipc_token(token_file: str | None) -> str:
    if token_file:
        try:
            return Path(token_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise DaemonConfigError(f"IPC token file unreadable: {type(exc).__name__}") from exc
    token = os.environ.pop(IPC_TOKEN_ENV, "").strip()
    if not token:
        raise DaemonConfigError(f"{IPC_TOKEN_ENV} or --ipc-token-file is required")
    return token


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m scp.verifier_service", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--transport", choices=("stdio", "pipe"), default="stdio")
    parser.add_argument("--db", required=True, help="kernel SQLite DB (opened read-only, fixed for life)")
    parser.add_argument("--audit-log", required=True, help="append-only JSONL audit ledger of this daemon")
    parser.add_argument("--ipc-token-file", help="file holding the IPC token (else env SCP_VERIFIER_IPC_TOKEN)")
    parser.add_argument("--pipe-address", help="pipe transport: \\\\.\\pipe\\NAME (Windows) or UDS path")
    parser.add_argument("--ready-file", help="pipe transport: written with the address once listening")
    parser.add_argument("--verifier-id", default=DEFAULT_VERIFIER_ID)
    parser.add_argument("--ttl", type=float, default=DEFAULT_TTL_SECONDS, help="receipt TTL seconds (<= 300)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="%(asctime)s %(levelname)s verifier-daemon: %(message)s")
    protocol_out = sys.stdout.buffer
    sys.stdout = sys.stderr  # stdout chỉ dành cho frame giao thức
    try:
        token = _read_ipc_token(args.ipc_token_file)
        signer = ReceiptSigner(load_private_key())
        signer.assert_role_separation()
        engine = VerifierEngine(db_path=args.db, signer=signer, ipc_token=token, audit_log=args.audit_log,
                                verifier_id=args.verifier_id, ttl_seconds=args.ttl)
        if args.transport == "pipe" and not args.pipe_address:
            raise DaemonConfigError("--pipe-address is required for the pipe transport")
        if not 0 < args.ttl <= signer.max_ttl_seconds:
            raise DaemonConfigError(f"--ttl must be in (0, {signer.max_ttl_seconds:.0f}] seconds")
    except (DaemonConfigError, OSError, ValueError) as exc:
        logger.error("verifier daemon refused to start: %s", exc)
        return EXIT_CONFIG_ERROR
    logger.info("verifier daemon ready pid=%d kid=%s transport=%s", os.getpid(), signer.kid, args.transport)
    if args.transport == "pipe":
        return serve_pipe(engine, args.pipe_address, token.encode("utf-8"), args.ready_file)
    return serve_stdio(engine, sys.stdin.buffer, protocol_out)
