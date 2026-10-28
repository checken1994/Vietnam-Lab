"""Khóa C 1.A — lane L3: verifier daemon là tiến trình OS RIÊNG giữ private key C (zero-mock).

Mọi test dùng subprocess daemon THẬT (``python -m scp.verifier_service``), DB SQLite tạm và
TaskKernel THẬT; receipt được kiểm bằng codec L1 (``verify_vr2``) với CHỈ public keyring và
bằng đúng cổng ``verify_completion_receipt`` (L1) trên lease row THẬT của kernel.
Không mock, không patch kernel, không stub trả VERIFIED.

Ngoài phạm vi L3 (CHƯA KIỂM ở đây): commit_approval gọi cổng đó để vào COMPLETED (lane L2a);
khi đó receipt từ test A0 phải được commit thật.
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
import secrets  # [AG §5.2 điều kiện 6] fixture hermetic cần secrets (token/tên duy nhất)
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scp.core.verifier_receipt import VerifierReceipt, sign_verifier_receipt
from scp.hands.task_kernel_bridge import TaskKernelHandsBridge
from scp.security.receipt_authority import (
    PROVENANCE_KEY_C,
    PROVENANCE_SELF_ATTESTED,
    ErrorCode,
    ReceiptAuthorityError,
    compute_kid,
    parse_keyring,
    self_signed_receipt_count,
    verify_completion_receipt,
    verify_vr2,
)
from scp.task_kernel import TaskKernel
from scp.verifier_service.client import (
    VerifierConfigError,
    VerifierDaemonClient,
    VerifierRefusedError,
    VerifierUnavailableError,
)
from scp.verifier_service.daemon import StdioChannel

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
WORKER = "keyc-l3-worker"


# --------------------------------------------------------------------------- helpers
def _derived_public_keys(values) -> set[str]:
    """Mọi chuỗi decode được thành seed Ed25519 32 byte -> public key base64 tương ứng."""
    found: set[str] = set()
    for value in values:
        for chunk in str(value).replace('"', " ").replace(",", " ").split():
            try:
                raw = base64.b64decode(chunk.strip(), validate=True)
            except ValueError:
                continue
            if len(raw) != 32:
                continue
            pub = Ed25519PrivateKey.from_private_bytes(raw).public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            found.add(base64.b64encode(pub).decode("ascii"))
    return found


def _new_task_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(6)}"


def _to_running(k: TaskKernel, task_id: str, ttl: float = 60.0):
    k.create_task(task_id, owner="keyc-owner", goal="keyc l3", risk_tier="R0", deadline_ms=120_000)
    for state, reason in (("PLANNING", "soak_plan"), ("READY", "soak_ready"), ("QUEUED", "soak_enqueue")):
        k.transition(task_id, state, actor=WORKER, reason=reason)
    lease = k.claim(task_id, WORKER, ttl_seconds=ttl)
    k.start(task_id, lease.lease_id)
    return lease


def _to_verifying(k: TaskKernel, task_id: str, ttl: float = 60.0):
    lease = _to_running(k, task_id, ttl=ttl)
    k.transition(task_id, "VERIFYING", actor=WORKER, reason="soak_postcondition")
    return lease


def _retry_to_new_attempt(k: TaskKernel, task_id: str, lease):
    k.commit_failed(task_id, lease.lease_id, actor=WORKER, failure_classification="RETRYABLE",
                    indictment_ref=f"evidence://keyc-l3/{task_id}/retry")
    k.transition(task_id, "QUEUED", actor=WORKER, reason="soak_enqueue")
    lease2 = k.claim(task_id, WORKER, ttl_seconds=60.0)
    k.start(task_id, lease2.lease_id)
    k.transition(task_id, "VERIFYING", actor=WORKER, reason="soak_postcondition")
    return lease2


def _to_verifying_with_file(k: TaskKernel, task_id: str, workspace: Path, content: str):
    """Như hands bridge: checkpoint planned action TRƯỚC dispatch, ghi file thật, rồi VERIFYING."""
    lease = _to_running(k, task_id)
    target = (workspace / f"{task_id}.txt").resolve()
    params = {"path": str(target), "content": content, "confirmation_id": f"conf-{task_id}"}
    planned = TaskKernelHandsBridge._planned_action("pc.write_file", params)
    cp_id = k.checkpoint(task_id, lease.lease_id, "pc.write_file", "WAITING_TOOL", planned, 1,
                         f"idem-{task_id}", pre_observation_ref=f"hands://{task_id}/pre")
    with open(target, "w", encoding="utf-8", newline="") as handle:  # cùng cách PCController ghi
        handle.write(content)
    k.transition(task_id, "VERIFYING", actor=WORKER, reason="soak_postcondition")
    return lease, cp_id, params


def _journal_receipt(client: VerifierDaemonClient, task_id: str, lease_id: str):
    return client.request_receipt(task_id=task_id, lease_id=lease_id, verifier_scope="journal_integrity",
                                  evidence_ref=f"evidence://worker/{task_id}/journal")


def _refusal(fn) -> VerifierRefusedError:
    with pytest.raises(VerifierRefusedError) as info:
        fn()
    return info.value


def _audit_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _run_keygen(out_dir: Path, keyring: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("SCP_VERIFIER")}
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.run([PY, str(ROOT / "tools" / "verifier_keygen.py"), "--out-dir", str(out_dir),
                           "--keyring-out", str(keyring)], capture_output=True, text=True, env=env,
                          cwd=str(ROOT), timeout=120, check=False)


# --------------------------------------------------------------------------- hermetic fixture
@pytest.fixture(scope="module")
def keyc_keys(tmp_path_factory):
    """Cặp khóa C ephemeral sinh bằng tools/verifier_keygen.py THẬT (subprocess).

    Tiến trình test chỉ nhận stdout công khai (kid/public key/đường dẫn); private key nằm trong
    file ``daemon_only/`` mà chỉ daemon mở.
    """
    base = tmp_path_factory.mktemp("keyc_keys")
    proc = _run_keygen(base / "daemon_only", base / "keyring_public.json")
    assert proc.returncode == 0, proc.stderr
    info = json.loads(proc.stdout)
    info["stdout"] = proc.stdout
    info["keyring_text"] = (base / "keyring_public.json").read_text(encoding="utf-8")
    return info


@contextlib.contextmanager
def hermetic_verifier_daemon(db_path: Path, tmp_path: Path, keys: dict):
    """AG §5.2 (6 bước, bản stdio): khóa ephemeral -> file daemon-only -> spawn daemon thật ->
    public keyring vào env tiến trình test -> yield -> teardown + khôi phục env."""
    audit = tmp_path / f"verifier_audit_{secrets.token_hex(4)}.jsonl"
    old = os.environ.get("SCP_VERIFIER_PUBLIC_KEYS")
    client = VerifierDaemonClient.spawn_stdio(db_path=db_path, private_key_file=keys["private_key_file"],
                                              audit_log=audit)
    os.environ["SCP_VERIFIER_PUBLIC_KEYS"] = keys["keyring_text"]  # kernel/worker chỉ giữ PUBLIC
    try:
        # pid THẬT của tiến trình Python daemon (Popen pid có thể là launcher của venv trên Windows)
        yield {"client": client, "audit": audit, "pid": client.call("health")["pid"]}
    finally:
        client.close()
        if old is None:
            os.environ.pop("SCP_VERIFIER_PUBLIC_KEYS", None)
        else:
            os.environ["SCP_VERIFIER_PUBLIC_KEYS"] = old


@pytest.fixture(scope="module")
def shared(tmp_path_factory, keyc_keys):
    """Một kernel + một daemon cho các test chỉ-đọc (task id duy nhất mỗi test)."""
    base = tmp_path_factory.mktemp("keyc_shared")
    kernel = TaskKernel(base / "kernel.sqlite3")
    with hermetic_verifier_daemon(base / "kernel.sqlite3", base, keyc_keys) as daemon:
        yield {"kernel": kernel, "base": base, **daemon}
    kernel.close()


# --------------------------------------------------------------------------- tests
def test_keygen_writes_daemon_only_private_file_and_never_prints_it(keyc_keys, tmp_path):
    stdout = keyc_keys["stdout"]
    assert keyc_keys["public_key"] not in _derived_public_keys([stdout]), "private seed leaked to stdout"
    assert Path(keyc_keys["private_key_file"]).is_file()
    pub_raw = base64.b64decode(keyc_keys["public_key"])
    assert keyc_keys["kid"] == compute_kid(pub_raw)
    ring = parse_keyring(keyc_keys["keyring_text"])
    assert [(e.kid, e.status) for e in ring.entries] == [(keyc_keys["kid"], "active")]
    # không ghi đè khóa đã có
    again = _run_keygen(Path(keyc_keys["private_key_file"]).parent, tmp_path / "other_ring.json")
    assert again.returncode != 0 and "refusing to overwrite" in again.stderr


def test_a0_daemon_signs_valid_vr2_bound_to_verifying_lease(shared, keyc_keys):
    k, client = shared["kernel"], shared["client"]
    task_id = _new_task_id("a0")
    lease = _to_verifying(k, task_id)
    before = self_signed_receipt_count()
    receipt = _journal_receipt(client, task_id, lease.lease_id)

    payload = verify_vr2(receipt.signature, keyc_keys["keyring_text"])  # chỉ public keyring
    assert payload["kid"] == keyc_keys["kid"]
    assert (payload["task_id"], payload["lease_id"], payload["attempt_id"], payload["fencing_token"]) == (
        task_id, lease.lease_id, lease.attempt_id, lease.fencing_token)
    assert payload["profile"] == "journal_integrity" and payload["verdict"] == "VERIFIED"
    assert 0 < payload["exp"] - payload["iat"] <= 300
    head = k.get_events(task_id)[-1]["event_hash"]
    assert payload["evidence_ref"] == f"evidence://scp-verifier/journal_integrity/{head}"
    assert (receipt.task_id, receipt.attempt_id, receipt.evidence_ref) == (
        task_id, lease.attempt_id, payload["evidence_ref"])
    assert client.public_key() == {"kid": keyc_keys["kid"], "public_key": keyc_keys["public_key"],
                                   "domain": "scp.verifier_receipt.v2"}
    issued = [e for e in _audit_events(shared["audit"]) if e["event"] == "RECEIPT_ISSUED" and e["task_id"] == task_id]
    assert len(issued) == 1 and issued[0]["jti"] == payload["jti"]
    assert k.get_task(task_id)["state"] == "VERIFYING"  # daemon không ghi DB kernel
    # đúng cổng mà L2a sẽ gọi trong txn commit, trên lease row THẬT của kernel, ở enforce
    gate = verify_completion_receipt(receipt, task_id=task_id, lease_row=k._lease(lease.lease_id),
                                     mode_floor="enforce")
    assert (gate.provenance, gate.mode, gate.jti, gate.kid) == (PROVENANCE_KEY_C, "enforce", payload["jti"],
                                                                keyc_keys["kid"])
    assert self_signed_receipt_count() == before


def test_a10_refuses_task_not_in_verifying(shared):
    k, client = shared["kernel"], shared["client"]
    task_id = _new_task_id("a10-run")
    lease = _to_running(k, task_id)
    err = _refusal(lambda: _journal_receipt(client, task_id, lease.lease_id))
    assert (err.error_code, err.reason) == (ErrorCode.TASK_NOT_VERIFYING, "task_not_verifying")
    err = _refusal(lambda: _journal_receipt(client, _new_task_id("ghost"), lease.lease_id))
    assert (err.error_code, err.reason) == (ErrorCode.TASK_NOT_VERIFYING, "task_not_found")


def test_a10_refuses_stale_attempt_and_cross_task_lease(shared):
    k, client = shared["kernel"], shared["client"]
    task_id = _new_task_id("a10-stale")
    old = _to_verifying(k, task_id)
    new = _retry_to_new_attempt(k, task_id, old)
    err = _refusal(lambda: _journal_receipt(client, task_id, old.lease_id))
    assert (err.error_code, err.reason) == (ErrorCode.LEASE_INVALID, "lease_not_active")
    # cùng id + khoảng trắng là task KHÁC: lease của task này không ký được cho task kia (exact)
    twin = task_id + " "
    _to_verifying(k, twin)
    err = _refusal(lambda: _journal_receipt(client, twin, new.lease_id))
    assert (err.error_code, err.reason) == (ErrorCode.LEASE_INVALID, "lease_not_active")
    err = _refusal(lambda: _journal_receipt(client, task_id, "lease-does-not-exist"))
    assert (err.error_code, err.reason) == (ErrorCode.LEASE_INVALID, "lease_not_found")
    assert verify_vr2(_journal_receipt(client, task_id, new.lease_id).signature,
                      os.environ["SCP_VERIFIER_PUBLIC_KEYS"])["attempt_id"] == new.attempt_id


def test_a10_refuses_expired_lease(shared):
    k, client = shared["kernel"], shared["client"]
    task_id = _new_task_id("a10-exp")
    lease = _to_verifying(k, task_id, ttl=1.0)
    time.sleep(1.3)
    err = _refusal(lambda: _journal_receipt(client, task_id, lease.lease_id))
    assert (err.error_code, err.reason) == (ErrorCode.LEASE_INVALID, "lease_expired")


def test_a10_refuses_tampered_journal(shared):
    k, client = shared["kernel"], shared["client"]
    task_id = _new_task_id("a10-tamper")
    lease = _to_verifying(k, task_id)
    with sqlite3.connect(shared["base"] / "kernel.sqlite3") as raw:
        raw.execute("UPDATE events SET reason='rewritten' WHERE task_id=? AND seq=2", (task_id,))
    err = _refusal(lambda: _journal_receipt(client, task_id, lease.lease_id))
    assert (err.error_code, err.reason) == (ErrorCode.POSTCONDITION_FAILED, "journal_invalid")


def test_a10_refuses_under_global_kill_and_old_kill_epoch(tmp_path, keyc_keys):
    db = tmp_path / "kill.sqlite3"
    k = TaskKernel(db)
    try:
        with hermetic_verifier_daemon(db, tmp_path, keyc_keys) as daemon:
            client = daemon["client"]
            task_id = _new_task_id("a10-kill")
            lease = _to_verifying(k, task_id)
            k.set_global_kill(True, actor="keyc-operator")
            err = _refusal(lambda: _journal_receipt(client, task_id, lease.lease_id))
            assert (err.error_code, err.reason) == (ErrorCode.KILL_SWITCH_ACTIVE, "global_kill_active")
            k.set_global_kill(False, actor="keyc-operator")
            err = _refusal(lambda: _journal_receipt(client, task_id, lease.lease_id))
            assert (err.error_code, err.reason) == (ErrorCode.KILL_SWITCH_ACTIVE, "kill_epoch_mismatch")
            refused = [e for e in _audit_events(daemon["audit"]) if e["event"] == "RECEIPT_REFUSED"]
            assert [e["reason"] for e in refused] == ["global_kill_active", "kill_epoch_mismatch"]
            assert not [e for e in _audit_events(daemon["audit"]) if e["event"] == "RECEIPT_ISSUED"]
    finally:
        k.close()


def test_file_profile_signs_only_when_physical_file_matches_kernel_checkpoint(shared, keyc_keys, tmp_path):
    k, client = shared["kernel"], shared["client"]
    task_id = _new_task_id("file-ok")
    lease, cp_id, params = _to_verifying_with_file(k, task_id, tmp_path, "keyc postcondition\n")
    receipt = client.request_receipt(task_id=task_id, lease_id=lease.lease_id,
                                     verifier_scope="hands_file_postcondition",
                                     evidence_ref=f"hands://{task_id}/result/x", checkpoint_id=cp_id,
                                     evidence={"action": "pc.write_file", "params": params})
    payload = verify_vr2(receipt.signature, keyc_keys["keyring_text"])
    content_hash = "sha256:" + __import__("hashlib").sha256(params["content"].encode("utf-8")).hexdigest()
    assert payload["profile"] == "hands_file_postcondition"
    assert payload["evidence_ref"] == f"evidence://scp-verifier/hands_file_postcondition/{cp_id}/{content_hash}"
    assert payload["lease_id"] == lease.lease_id


def test_file_profile_refuses_tampered_missing_or_unplanned_file(shared, tmp_path):
    k, client = shared["kernel"], shared["client"]

    def ask(task_id, lease, cp_id, params, execution_hash=None):
        return lambda: client.request_receipt(
            task_id=task_id, lease_id=lease.lease_id, verifier_scope="hands_file_postcondition",
            evidence_ref=f"hands://{task_id}/result/x", checkpoint_id=cp_id, execution_hash=execution_hash,
            evidence={"action": "pc.write_file", "params": params})

    task_id = _new_task_id("file-tamper")
    lease, cp_id, params = _to_verifying_with_file(k, task_id, tmp_path, "planned bytes")
    Path(params["path"]).write_text("other bytes", encoding="utf-8")
    err = _refusal(ask(task_id, lease, cp_id, params))
    assert (err.error_code, err.reason) == (ErrorCode.POSTCONDITION_FAILED, "postcondition_failed")
    Path(params["path"]).unlink()
    err = _refusal(ask(task_id, lease, cp_id, params))
    assert (err.error_code, err.reason) == (ErrorCode.POSTCONDITION_FAILED, "postcondition_failed")

    task_id = _new_task_id("file-unplanned")
    lease, cp_id, params = _to_verifying_with_file(k, task_id, tmp_path, "planned bytes")
    forged = {**params, "content": "worker claims different bytes"}
    Path(params["path"]).write_text(forged["content"], encoding="utf-8")
    err = _refusal(ask(task_id, lease, cp_id, forged))
    assert (err.error_code, err.reason) == (ErrorCode.POSTCONDITION_FAILED, "evidence_invalid")
    Path(params["path"]).write_text(params["content"], encoding="utf-8")
    err = _refusal(ask(task_id, lease, cp_id, params, execution_hash="sha256:" + "0" * 64))
    assert (err.error_code, err.reason) == (ErrorCode.POSTCONDITION_FAILED, "postcondition_failed")

    other_task = _new_task_id("file-other")  # checkpoint của task khác không bind được cho task này
    _, other_cp, _ = _to_verifying_with_file(k, other_task, tmp_path, "planned bytes")
    err = _refusal(ask(task_id, lease, other_cp, params))
    assert (err.error_code, err.reason) == (ErrorCode.POSTCONDITION_FAILED, "evidence_invalid")

    rel_task = _new_task_id("file-rel")  # path tương đối: daemon không quan sát lại được -> từ chối
    rel_lease = _to_running(k, rel_task)
    rel_params = {"path": f"{rel_task}.txt", "content": "c", "confirmation_id": f"conf-{rel_task}"}
    rel_cp = k.checkpoint(rel_task, rel_lease.lease_id, "pc.write_file", "WAITING_TOOL",
                          TaskKernelHandsBridge._planned_action("pc.write_file", rel_params), 1, f"idem-{rel_task}")
    k.transition(rel_task, "VERIFYING", actor=WORKER, reason="soak_postcondition")
    err = _refusal(ask(rel_task, rel_lease, rel_cp, rel_params))
    assert (err.error_code, err.reason) == (ErrorCode.POSTCONDITION_FAILED, "evidence_invalid")


def test_request_schema_and_auth_violations_return_error_codes(shared):
    k, client = shared["kernel"], shared["client"]
    task_id = _new_task_id("schema")
    lease = _to_verifying(k, task_id)
    token = client._token
    good_request = {"task_id": task_id, "lease_id": lease.lease_id, "evidence_ref": "evidence://w/x",
                    "verifier_scope": "journal_integrity"}
    hands_request = {**good_request, "verifier_scope": "hands_file_postcondition", "checkpoint_id": "cp_x"}

    def frame(**over):
        base = {"v": 1, "id": "f1", "op": "issue", "auth": token, "request": dict(good_request)}
        base.update(over)
        return {k_: v for k_, v in base.items() if v is not None}

    cases = [
        ("{not json", ErrorCode.MALFORMED_REQUEST, "bad_json"),
        (frame(auth=None), ErrorCode.UNAUTHORIZED, "unauthorized"),
        (frame(auth="x" * 48), ErrorCode.UNAUTHORIZED, "unauthorized"),
        (frame(db=str(shared["base"] / "attacker.sqlite3")), ErrorCode.MALFORMED_REQUEST, "schema_invalid"),
        (frame(request={**good_request, "db": "attacker.sqlite3"}), ErrorCode.MALFORMED_REQUEST, "schema_invalid"),
        (frame(request={k_: v for k_, v in good_request.items() if k_ != "lease_id"}),
         ErrorCode.MALFORMED_REQUEST, "schema_invalid"),
        (frame(request={**good_request, "lease_id": 7}), ErrorCode.MALFORMED_REQUEST, "schema_invalid"),
        (frame(v=2), ErrorCode.MALFORMED_REQUEST, "schema_invalid"),
        (frame(v=True), ErrorCode.MALFORMED_REQUEST, "schema_invalid"),
        (frame(op="sign_anything"), ErrorCode.MALFORMED_REQUEST, "unknown_op"),
        (frame(request={**good_request, "verifier_scope": "gui_click"}), ErrorCode.PROFILE_UNSUPPORTED,
         "profile_unsupported"),
        (frame(evidence={"action": "pc.write_file", "params": {}}), ErrorCode.MALFORMED_REQUEST, "schema_invalid"),
        ({k_: v for k_, v in frame().items() if k_ != "op"}, ErrorCode.MALFORMED_REQUEST, "schema_invalid"),
        (frame(id=""), ErrorCode.MALFORMED_REQUEST, "schema_invalid"),
        (frame(request={**good_request, "task_id": "t" * 300}), ErrorCode.MALFORMED_REQUEST, "schema_invalid"),
        (frame(request={**good_request, "verifier_scope": "hands_file_postcondition"},
               evidence={"action": "pc.write_file", "params": {"path": "C:/x", "content": "c"}}),
         ErrorCode.MALFORMED_REQUEST, "schema_invalid"),  # thiếu checkpoint_id
        (frame(request=dict(hands_request), evidence={"action": "pc.click", "params": {"path": "C:/x", "content": "c"}}),
         ErrorCode.PROFILE_UNSUPPORTED, "profile_unsupported"),
        (frame(request=dict(hands_request), evidence={"action": "pc.write_file", "params": {"path": "C:/x"}}),
         ErrorCode.MALFORMED_REQUEST, "schema_invalid"),
        ("x" * (300 * 1024), ErrorCode.MALFORMED_REQUEST, "frame_too_large"),
    ]
    for raw, code, reason in cases:
        response = client.send_frame(raw)
        assert response["success"] is False, (reason, response)
        assert (response["error_code"], response["reason"]) == (code, reason)
        assert "receipt" not in response
    # daemon vẫn sống và đếm đúng số lần từ chối
    metrics = client.metrics()
    assert metrics["security.key_c.receipts_refused_count.unauthorized"] >= 2
    assert metrics["security.key_c.receipts_refused_count.schema_invalid"] >= 7
    assert not [e for e in _audit_events(shared["audit"]) if e.get("task_id") == task_id
                and e["event"] == "RECEIPT_ISSUED"]
    assert client.send_frame(frame())["success"] is True  # cùng frame, đúng token + schema -> ký


def test_private_key_c_never_in_parent_or_worker_env(shared, keyc_keys, monkeypatch, tmp_path):
    assert shared["client"].public_key()["public_key"] == keyc_keys["public_key"]
    assert keyc_keys["public_key"] not in _derived_public_keys(os.environ.values())
    assert not [n for n in os.environ if n.upper().startswith("SCP_VERIFIER_PRIVATE_KEY")]
    monkeypatch.setenv("SCP_VERIFIER_PRIVATE_KEY_FILE", keyc_keys["private_key_file"])
    with pytest.raises(VerifierConfigError) as info:
        VerifierDaemonClient.spawn_stdio(db_path=shared["base"] / "kernel.sqlite3",
                                         private_key_file=keyc_keys["private_key_file"],
                                         audit_log=tmp_path / "a.jsonl")
    assert info.value.error_code == "private_key_in_worker_env"
    with pytest.raises(VerifierConfigError):
        VerifierDaemonClient.connect_pipe("\\\\.\\pipe\\unused", "t" * 48)


def _local_test_seed() -> tuple[str, str]:
    """Seed Ed25519 TEST sinh tại chỗ (không phải key C của daemon) -> (seed_b64, pub_b64)."""
    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(seed).decode("ascii"), base64.b64encode(pub).decode("ascii")


def test_daemon_load_private_key_scrubs_both_env_channels(tmp_path, monkeypatch):
    """[CHECKER L3 đk1] Postcondition phía DAEMON: nạp xong thì cả SCP_VERIFIER_PRIVATE_KEY lẫn
    SCP_VERIFIER_PRIVATE_KEY_FILE biến khỏi os.environ (không lọt sang tiến trình con), và
    seed trả về đúng là key đã cấu hình (_FILE thắng env theo HC-08)."""
    from scp.verifier_service.daemon import load_private_key

    def pub_of(raw: bytes) -> str:
        return base64.b64encode(Ed25519PrivateKey.from_private_bytes(raw).public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode("ascii")

    names = ("SCP_VERIFIER_PRIVATE_KEY", "SCP_VERIFIER_PRIVATE_KEY_FILE")
    file_seed, file_pub = _local_test_seed()
    env_seed, env_pub = _local_test_seed()
    key_file = tmp_path / "daemon_only" / "verifier_c_private.key"
    key_file.parent.mkdir()
    key_file.write_text(file_seed + "\n", encoding="ascii")

    monkeypatch.setenv("SCP_VERIFIER_PRIVATE_KEY", env_seed)  # cả hai kênh cùng có mặt
    monkeypatch.setenv("SCP_VERIFIER_PRIVATE_KEY_FILE", str(key_file))
    assert pub_of(load_private_key()) == file_pub
    assert [n for n in names if n in os.environ] == []

    monkeypatch.setenv("SCP_VERIFIER_PRIVATE_KEY", env_seed)  # chỉ kênh env
    assert pub_of(load_private_key()) == env_pub
    assert [n for n in names if n in os.environ] == []


def test_engine_readonly_connection_cannot_write_kernel_db(tmp_path):
    """[CHECKER L3 đk2] Kết nối của engine là CHỈ-ĐỌC ở cả hai lớp: query_only và URI mode=ro.
    Mọi UPDATE/INSERT/DELETE bị SQLite từ chối; tắt query_only vẫn không ghi được (mode=ro)."""
    from scp.verifier_service.verifier_engine import VerifierEngine

    db = tmp_path / "ro_kernel.sqlite3"
    k = TaskKernel(db)
    try:
        task_id = _new_task_id("ro")
        _to_verifying(k, task_id)
        engine = VerifierEngine(db_path=db, signer=None, ipc_token="t" * 48, audit_log=tmp_path / "a.jsonl")
        conn = engine._open_readonly()
        try:
            assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
            writes = (("UPDATE tasks SET state='COMPLETED' WHERE task_id=?", (task_id,)),
                      ("INSERT INTO control(id) VALUES(2)", ()),
                      ("DELETE FROM leases WHERE task_id=?", (task_id,)))
            for sql, args in writes:
                with pytest.raises(sqlite3.OperationalError, match="readonly"):
                    conn.execute(sql, args)
            conn.execute("PRAGMA query_only=OFF")  # lớp 2: URI mode=ro vẫn chặn
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                conn.execute("UPDATE tasks SET state='COMPLETED' WHERE task_id=?", (task_id,))
        finally:
            conn.close()
        assert k.get_task(task_id)["state"] == "VERIFYING"
        assert len([row for row in k.conn.execute("SELECT lease_id FROM leases WHERE task_id=?", (task_id,))]) == 1
    finally:
        k.close()


def test_daemon_death_fails_closed_without_self_signing(tmp_path, keyc_keys):
    db = tmp_path / "death.sqlite3"
    k = TaskKernel(db)
    try:
        with hermetic_verifier_daemon(db, tmp_path, keyc_keys) as daemon:
            client = daemon["client"]
            task_id = _new_task_id("death")
            lease = _to_verifying(k, task_id)
            before = self_signed_receipt_count()
            assert daemon["pid"] != os.getpid()  # tiến trình OS riêng
            os.kill(daemon["pid"], signal.SIGTERM)  # daemon thật chết (crash), không phải đóng êm
            for _ in range(2):  # lần sau trên kênh đã chết cũng phải raise, không fallback
                with pytest.raises(VerifierUnavailableError) as info:
                    _journal_receipt(client, task_id, lease.lease_id)
                assert info.value.error_code == "daemon_unavailable"
            assert k.get_task(task_id)["state"] == "VERIFYING"
            assert self_signed_receipt_count() == before
    finally:
        k.close()


def test_daemon_timeout_fails_closed_and_kills_hung_process(tmp_path):
    env = {k: v for k, v in os.environ.items() if k.upper() in {"SYSTEMROOT", "PATH", "TEMP", "TMP"}}
    channel = StdioChannel([PY, "-c", "import time; time.sleep(120)"], env, str(tmp_path))
    client = VerifierDaemonClient(channel, "t" * 48)
    started = time.monotonic()
    with pytest.raises(VerifierUnavailableError) as info:
        client.request_receipt(task_id="t-timeout", lease_id="l", verifier_scope="journal_integrity",
                               evidence_ref="evidence://w/x", timeout=1.0)
    assert info.value.error_code == "daemon_timeout"
    assert time.monotonic() - started < 30
    assert channel._proc.poll() is not None  # tiến trình treo đã bị dừng, kênh đóng hẳn
    with pytest.raises(VerifierUnavailableError):
        client.call("health")


_RELAY = ("import json, sys\n"
          "canned = json.loads(sys.argv[1])\n"
          "for line in sys.stdin:\n"
          "    print(json.dumps({**canned, 'id': json.loads(line)['id']}), flush=True)\n")


def test_client_rejects_receipt_relayed_for_another_task_or_lease(shared, tmp_path):
    """Kênh bị chiếm (tiến trình relay thật) trả receipt HỢP LỆ nhưng của task/lease khác -> từ chối."""
    k, client = shared["kernel"], shared["client"]
    task_a, task_b = _new_task_id("relay-a"), _new_task_id("relay-b")
    lease_a, lease_b = _to_verifying(k, task_a), _to_verifying(k, task_b)
    canned = client.send_frame({"v": 1, "id": "x", "op": "issue", "auth": client._token,
                                "request": {"task_id": task_a, "lease_id": lease_a.lease_id,
                                            "evidence_ref": "evidence://w/a", "verifier_scope": "journal_integrity"}})
    assert canned["success"] is True
    env = {k_: v for k_, v in os.environ.items() if k_.upper() in {"SYSTEMROOT", "PATH", "TEMP", "TMP"}}
    relay = VerifierDaemonClient(StdioChannel([PY, "-c", _RELAY, json.dumps(canned)], env, str(tmp_path)), "t" * 48)
    try:
        for lease_id in (lease_b.lease_id, lease_a.lease_id + "x"):
            target = task_b if lease_id == lease_b.lease_id else task_a
            with pytest.raises(VerifierUnavailableError) as info:
                _journal_receipt(relay, target, lease_id)
            assert info.value.error_code == "protocol_error"
        assert _journal_receipt(relay, task_a, lease_a.lease_id).task_id == task_a  # positive control
    finally:
        relay.close()


def test_unwritable_audit_ledger_withholds_receipt(tmp_path, keyc_keys):
    db = tmp_path / "audit.sqlite3"
    k = TaskKernel(db)
    ledger_dir = tmp_path / "ledger_is_a_directory"
    ledger_dir.mkdir()
    client = VerifierDaemonClient.spawn_stdio(db_path=db, private_key_file=keyc_keys["private_key_file"],
                                              audit_log=ledger_dir)
    try:
        task_id = _new_task_id("audit")
        lease = _to_verifying(k, task_id)
        err = _refusal(lambda: _journal_receipt(client, task_id, lease.lease_id))
        assert (err.error_code, err.reason) == (ErrorCode.INTERNAL_ERROR, "audit_unavailable")
        assert client.metrics().get("security.key_c.receipts_issued_count", 0) == 0
    finally:
        client.close()
        k.close()


def test_daemon_refuses_to_start_without_token_bad_key_or_issuer_key(tmp_path, keyc_keys):
    db = tmp_path / "boot.sqlite3"
    TaskKernel(db).close()
    base_env = {k: v for k, v in os.environ.items()
                if k.upper() in {"SYSTEMROOT", "PATH", "TEMP", "TMP", "SCP_JOURNAL_SECRET",
                                 "SCP_APPROVAL_ISSUER_PUBLIC_KEY"}}
    base_env["PYTHONPATH"] = str(ROOT)

    def boot(env, key_file):
        return subprocess.run([PY, "-m", "scp.verifier_service", "--db", str(db), "--audit-log",
                               str(tmp_path / "boot.jsonl")], input="", capture_output=True, text=True,
                              env={**env, "SCP_VERIFIER_PRIVATE_KEY_FILE": str(key_file)}, cwd=str(ROOT),
                              timeout=120, check=False)

    no_token = boot(base_env, keyc_keys["private_key_file"])
    assert no_token.returncode == 2 and "SCP_VERIFIER_IPC_TOKEN" in no_token.stderr
    env = {**base_env, "SCP_VERIFIER_IPC_TOKEN": secrets.token_hex(24)}
    bad_key = tmp_path / "bad.key"
    bad_key.write_text("not-a-key", encoding="ascii")
    assert boot(env, bad_key).returncode == 2
    issuer_key = tmp_path / "issuer_as_c.key"  # key B (test-only, từ conftest) không được dùng làm key C
    issuer_key.write_text(os.environ["SCP_APPROVAL_ISSUER_PRIVATE_KEY"], encoding="ascii")
    role = boot(env, issuer_key)
    assert role.returncode == 2 and "role separation" in role.stderr
    assert boot(env, keyc_keys["private_key_file"]).returncode == 0  # cấu hình đúng: chạy, EOF -> thoát 0


def test_pipe_transport_requires_ipc_token_handshake(tmp_path, keyc_keys):
    db = tmp_path / "pipe.sqlite3"
    k = TaskKernel(db)
    token = secrets.token_hex(24)
    token_file = tmp_path / "ipc.token"
    token_file.write_text(token, encoding="ascii")
    address = (f"\\\\.\\pipe\\scp-verifier-test-{secrets.token_hex(6)}" if os.name == "nt"
               else str(tmp_path / "verifier.sock"))
    ready = tmp_path / "ready"
    env = {k_: v for k_, v in os.environ.items()
           if k_.upper() in {"SYSTEMROOT", "PATH", "TEMP", "TMP", "SCP_JOURNAL_SECRET"}}
    env.update({"PYTHONPATH": str(ROOT), "SCP_VERIFIER_PRIVATE_KEY_FILE": keyc_keys["private_key_file"]})
    proc = subprocess.Popen([PY, "-m", "scp.verifier_service", "--transport", "pipe", "--db", str(db),
                             "--audit-log", str(tmp_path / "pipe.jsonl"), "--ipc-token-file", str(token_file),
                             "--pipe-address", address, "--ready-file", str(ready)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env, cwd=str(ROOT))
    try:
        deadline = time.monotonic() + 60
        while not ready.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        assert ready.exists(), proc.stderr.read().decode("utf-8", "replace") if proc.poll() is not None else ""
        task_id = _new_task_id("pipe")
        lease = _to_verifying(k, task_id)
        receipt = _journal_receipt(VerifierDaemonClient.connect_pipe(address, token), task_id, lease.lease_id)
        assert verify_vr2(receipt.signature, keyc_keys["keyring_text"])["lease_id"] == lease.lease_id
        with pytest.raises(VerifierUnavailableError) as info:
            _journal_receipt(VerifierDaemonClient.connect_pipe(address, secrets.token_hex(24)),
                             task_id, lease.lease_id)
        assert info.value.error_code == "unauthorized"
        assert proc.poll() is None  # bắt tay sai không làm sập daemon
    finally:
        proc.kill()
        proc.wait(timeout=10)
        k.close()


def test_self_signed_metric_counts_executor_hmac_but_never_daemon_receipts(shared, monkeypatch):
    """Metric ``security.key_c.self_signed_receipt_count`` (L1) tách được receipt daemon và tự ký."""
    k, client = shared["kernel"], shared["client"]
    monkeypatch.setenv("SCP_RECEIPT_AUTHORITY_MODE", "audit")
    t_daemon, t_self = _new_task_id("metric-daemon"), _new_task_id("metric-self")
    l_daemon, l_self = _to_verifying(k, t_daemon), _to_verifying(k, t_self)
    before = self_signed_receipt_count()
    via_daemon = verify_completion_receipt(_journal_receipt(client, t_daemon, l_daemon.lease_id), task_id=t_daemon,
                                           lease_row=k._lease(l_daemon.lease_id))
    assert via_daemon.provenance == PROVENANCE_KEY_C
    assert self_signed_receipt_count() == before
    self_signed = sign_verifier_receipt(VerifierReceipt(
        task_id=t_self, verifier_id="hands-executor-self-verifier-v1", verdict="VERIFIED",
        evidence_ref=f"hands://{t_self}/result/x", issued_at=time.time()))
    accepted = verify_completion_receipt(self_signed, task_id=t_self, lease_row=k._lease(l_self.lease_id))
    assert accepted.provenance == PROVENANCE_SELF_ATTESTED
    assert self_signed_receipt_count() == before + 1
    monkeypatch.setenv("SCP_RECEIPT_AUTHORITY_MODE", "enforce")
    with pytest.raises(ReceiptAuthorityError) as info:
        verify_completion_receipt(self_signed, task_id=t_self, lease_row=k._lease(l_self.lease_id))
    assert info.value.code == ErrorCode.HMAC_REJECTED
    assert self_signed_receipt_count() == before + 1
