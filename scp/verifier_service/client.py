"""VerifierDaemonClient — phía worker xin receipt vr2 từ verifier daemon (khóa C, 1.A).

Bất biến FAIL-CLOSED: client KHÔNG BAO GIỜ tự ký và không có đường fallback. Daemon chết, timeout,
sai giao thức hay từ chối -> raise (VerifierUnavailableError / VerifierRefusedError); task vẫn ở
VERIFYING cho kernel/caller xử lý. Tiến trình worker không được giữ private key C: client từ chối
khởi tạo nếu env của nó có ``SCP_VERIFIER_PRIVATE_KEY[_FILE]``; ``spawn_stdio`` dựng env con từ
allowlist (không kế thừa toàn bộ env cha) và chỉ đặt ĐƯỜNG DẪN file key vào env của CON.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any

from scp.verifier_service.daemon import ChannelError, PipeChannel, StdioChannel
from scp.verifier_service.verifier_engine import PROTOCOL_VERSION

PRIVATE_KEY_ENV_VARS = ("SCP_VERIFIER_PRIVATE_KEY", "SCP_VERIFIER_PRIVATE_KEY_FILE")
_DAEMON_ENV_ALLOWLIST = frozenset({
    "SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "COMSPEC", "TEMP", "TMP", "HOME", "USERPROFILE",
    "LOCALAPPDATA", "APPDATA", "LANG", "LC_ALL", "SCP_JOURNAL_SECRET", "SCP_PREVIOUS_JOURNAL_SECRETS",
    "SCP_APPROVAL_ISSUER_PUBLIC_KEY", "SCP_APPROVAL_ISSUER_PUBLIC_KEY_FILE",
})
DEFAULT_TIMEOUT = 15.0
STARTUP_TIMEOUT = 60.0


class VerifierClientError(RuntimeError):
    def __init__(self, error_code: str, message: str, *, reason: str = "") -> None:
        super().__init__(f"[{error_code}{'/' + reason if reason else ''}] {message}")
        self.error_code = error_code
        self.reason = reason


class VerifierUnavailableError(VerifierClientError):
    """Daemon chết / timeout / sai giao thức — KHÔNG có receipt, không fallback tự ký."""


class VerifierRefusedError(VerifierClientError):
    """Daemon thẩm định và từ chối (error_code của daemon)."""


class VerifierConfigError(VerifierClientError):
    """Cấu hình worker vi phạm tách vai trò khóa (vd private key C nằm trong env worker)."""


def assert_worker_holds_no_private_key(environ: Any = None) -> None:
    env = os.environ if environ is None else environ
    leaked = [name for name in PRIVATE_KEY_ENV_VARS if str(env.get(name, "")).strip()]
    if leaked:
        raise VerifierConfigError("private_key_in_worker_env",
                                  f"worker process must not hold key C ({', '.join(leaked)} set)")


class VerifierDaemonClient:
    def __init__(self, channel: Any, ipc_token: str) -> None:
        assert_worker_holds_no_private_key()
        self._channel = channel
        self._token = ipc_token

    @classmethod
    def spawn_stdio(cls, *, db_path: str | Path, private_key_file: str | Path, audit_log: str | Path,
                    extra_args: tuple[str, ...] = (), startup_timeout: float = STARTUP_TIMEOUT) -> VerifierDaemonClient:
        """Khởi tạo daemon con (stdio). Private key: chỉ ĐƯỜNG DẪN, đặt vào env của con (``_FILE``)."""
        assert_worker_holds_no_private_key()
        import scp

        root = str(Path(scp.__file__).resolve().parents[1])
        token = secrets.token_hex(24)
        env = {k: v for k, v in os.environ.items() if k.upper() in _DAEMON_ENV_ALLOWLIST}
        env.update({"SCP_VERIFIER_IPC_TOKEN": token, "SCP_VERIFIER_PRIVATE_KEY_FILE": str(private_key_file),
                    "PYTHONPATH": root, "PYTHONIOENCODING": "utf-8"})  # env CỦA CON, không phải cha
        argv = [sys.executable, "-m", "scp.verifier_service", "--transport", "stdio", "--db", str(db_path),
                "--audit-log", str(audit_log), *extra_args]
        client = cls(StdioChannel(argv, env, root), token)
        try:
            client.call("health", timeout=startup_timeout)
        except VerifierClientError:
            client.close()
            raise
        return client

    @classmethod
    def connect_pipe(cls, address: str, ipc_token: str) -> VerifierDaemonClient:
        return cls(PipeChannel(address, ipc_token.encode("utf-8")), ipc_token)

    @property
    def pid(self) -> int | None:
        return getattr(self._channel, "pid", None)

    def send_frame(self, frame: Any, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
        """Gửi một frame thô (dict/str/bytes) — dùng cho kiểm thử giao thức."""
        if isinstance(frame, dict):
            data = json.dumps(frame).encode("utf-8")
        elif isinstance(frame, str):
            data = frame.encode("utf-8")
        else:
            data = bytes(frame)
        try:
            raw = self._channel.exchange(data, timeout)
        except ChannelError as exc:  # transport hỏng -> không có receipt; KHÔNG fallback
            raise VerifierUnavailableError(exc.code, str(exc)) from exc
        try:
            response = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise VerifierUnavailableError("protocol_error", "daemon sent a non-JSON frame") from exc
        if (not isinstance(response, dict) or response.get("v") != PROTOCOL_VERSION
                or type(response.get("success")) is not bool):
            raise VerifierUnavailableError("protocol_error", "daemon response has no valid envelope")
        return response

    def call(self, op: str, *, timeout: float = DEFAULT_TIMEOUT, **fields: Any) -> dict[str, Any]:
        req_id = secrets.token_hex(8)
        response = self.send_frame({"v": PROTOCOL_VERSION, "id": req_id, "op": op, "auth": self._token, **fields},
                                   timeout=timeout)
        if response.get("id") != req_id:
            raise VerifierUnavailableError("protocol_error", "response id does not match request")
        if not response["success"]:
            raise VerifierRefusedError(str(response.get("error_code") or "MALFORMED_RESPONSE"),
                                       str(response.get("message") or "refused"),
                                       reason=str(response.get("reason") or ""))
        return response

    def public_key(self) -> dict[str, str]:
        response = self.call("pubkey")
        return {"kid": response["kid"], "public_key": response["public_key"], "domain": response["domain"]}

    def metrics(self) -> dict[str, int]:
        return dict(self.call("metrics")["metrics"])

    def request_receipt(self, *, task_id: str, lease_id: str, verifier_scope: str, evidence_ref: str,
                        checkpoint_id: str | None = None, execution_hash: str | None = None,
                        evidence: dict[str, Any] | None = None, timeout: float = DEFAULT_TIMEOUT) -> Any:
        """Xin receipt vr2 (thân request = schema L1). Không có receipt hợp lệ -> raise; KHÔNG tự ký."""
        from scp.security.receipt_authority import (
            ReceiptAuthorityError,
            parse_receipt_response,
            parse_vr2,
            receipt_carrier,
        )

        request = {"task_id": task_id, "lease_id": lease_id, "evidence_ref": evidence_ref,
                   "verifier_scope": verifier_scope}
        if checkpoint_id is not None:
            request["checkpoint_id"] = checkpoint_id
        if execution_hash is not None:
            request["execution_hash"] = execution_hash
        fields: dict[str, Any] = {"request": request}
        if evidence is not None:
            fields["evidence"] = evidence
        response = self.call("issue", timeout=timeout, **fields)
        try:
            token = parse_receipt_response(response)
            claims = parse_vr2(token)
        except ReceiptAuthorityError as exc:
            raise VerifierUnavailableError("protocol_error", f"daemon returned a malformed receipt: {exc}") from exc
        if claims["task_id"] != task_id or claims["lease_id"] != lease_id:
            raise VerifierUnavailableError("protocol_error", "daemon returned a receipt for another task/lease")
        return receipt_carrier(token)

    def close(self) -> None:
        self._channel.close()

    def __enter__(self) -> VerifierDaemonClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
