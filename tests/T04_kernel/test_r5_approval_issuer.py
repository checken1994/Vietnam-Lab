"""R5-B · approvals are issuer-signed (Ed25519); nobody holding SCP_CAPABILITY_SECRET can self-approve.

Before R5-B an approval:grant token was an HMAC under the capability key: a worker (or the
autonomous checker gate itself) holding that key minted its own approval and moved its task
WAITING_APPROVAL -> READY. The kernel now verifies with the issuer PUBLIC key only.

Zero-mock: real TaskKernel on SQLite under tmp_path; tokens are real HMAC/Ed25519 signatures.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest

from scp.core.capability_token import InvalidTokenSignatureError, compute_token_signature, mint_token
from scp.security.capability_epoch import CapabilityToken
from scp.task_kernel import InvalidTransition, TaskKernel

_CAPABILITY = "r5b-capability-secret-held-by-a-worker-32b"


@pytest.fixture
def kernel(tmp_path, monkeypatch):
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", _CAPABILITY)
    monkeypatch.setenv("SCP_VERIFIER_SECRET", "r5b-verifier-secret-for-checker-gate-32b")
    k = TaskKernel(tmp_path / "r5b.sqlite3")
    try:
        yield k
    finally:
        k.close()


def _waiting(k: TaskKernel, task_id: str) -> None:
    k.create_task(task_id, "r5b-owner", "needs approval", "R2", input_hash="sha256:" + "0" * 64)
    k.transition(task_id, "PLANNING")
    k.transition(task_id, "WAITING_APPROVAL")


def _assert_not_approved(k: TaskKernel, task_id: str) -> None:
    assert k.get_task(task_id)["state"] == "WAITING_APPROVAL"
    assert [e for e in k.get_events(task_id) if e["type"] == "TASK_APPROVED"] == []


def _sign_raw(payload: dict) -> str:
    """Build an apv1 token over an arbitrary payload with the configured TEST issuer key."""
    import os

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(os.environ["SCP_APPROVAL_ISSUER_PRIVATE_KEY"]))
    sig = key.sign(b"scp.approval.v1\x00" + body)

    def enc(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"apv1.{enc(body)}.{enc(sig)}"


def test_capability_key_holder_cannot_mint_its_own_approval(kernel):
    _waiting(kernel, "r5b-self-mint")
    token = mint_token(issuer="rogue-autofix-worker", scope="approval:grant:r5b-self-mint", capability_level=3)
    with pytest.raises(InvalidTokenSignatureError):
        kernel.commit_approval("r5b-self-mint", token, actor="rogue-autofix-worker")
    _assert_not_approved(kernel, "r5b-self-mint")


def test_hmac_capability_token_and_operator_hmac_signature_are_rejected(kernel):
    _waiting(kernel, "r5b-hmac")
    now = time.time()
    sig = compute_token_signature(_CAPABILITY.encode(), "approval:grant:r5b-hmac", 0, "tok-r5b", now)
    with pytest.raises(InvalidTokenSignatureError):
        kernel.commit_approval("r5b-hmac", CapabilityToken("approval:grant:r5b-hmac", 0, "tok-r5b", now, sig))
    canonical = f"operator_approval:r5b-hmac:operator:{now:.6f}".encode()
    operator_sig = {
        "actor": "operator",
        "task_id": "r5b-hmac",
        "timestamp": now,
        "signature": hmac.new(_CAPABILITY.encode(), canonical, hashlib.sha256).hexdigest(),
    }
    with pytest.raises(InvalidTokenSignatureError):
        kernel.commit_approval("r5b-hmac", operator_sig)
    _assert_not_approved(kernel, "r5b-hmac")


def test_issuer_signed_token_approves_and_journals_the_issuer(kernel):
    from scp.security.approval_issuer import issue_approval_token

    _waiting(kernel, "r5b-issued")
    token = issue_approval_token("r5b-issued", issuer="alice-operator")
    task = kernel.commit_approval("r5b-issued", token, actor="alice-operator")
    assert task["state"] == "READY"
    (event,) = [e for e in kernel.get_events("r5b-issued") if e["type"] == "TASK_APPROVED"]
    payload = json.loads(event["payload_json"])
    assert payload["token_type"] == "approval_ed25519"
    assert payload["scope"] == "approval:grant:r5b-issued"


def test_token_signed_by_a_different_key_is_rejected(kernel, monkeypatch):
    from scp.security.approval_issuer import generate_issuer_keypair, issue_approval_token

    _waiting(kernel, "r5b-other-key")
    other_private, _other_public = generate_issuer_keypair()
    monkeypatch.setenv("SCP_APPROVAL_ISSUER_PRIVATE_KEY", other_private)
    forged = issue_approval_token("r5b-other-key", issuer="mallory")
    monkeypatch.undo()
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", _CAPABILITY)
    with pytest.raises(InvalidTokenSignatureError):
        kernel.commit_approval("r5b-other-key", forged)
    _assert_not_approved(kernel, "r5b-other-key")


def test_token_is_bound_to_one_task(kernel):
    from scp.security.approval_issuer import issue_approval_token

    _waiting(kernel, "r5b-task-a")
    _waiting(kernel, "r5b-task-b")
    token_for_a = issue_approval_token("r5b-task-a", issuer="alice-operator")
    with pytest.raises(InvalidTransition):
        kernel.commit_approval("r5b-task-b", token_for_a)
    _assert_not_approved(kernel, "r5b-task-b")


def test_expired_and_non_finite_tokens_are_rejected(kernel):
    from scp.security.approval_issuer import issue_approval_token

    _waiting(kernel, "r5b-expired")
    expired = issue_approval_token("r5b-expired", issuer="alice-operator", ttl_seconds=60, now=time.time() - 4000)
    with pytest.raises(InvalidTransition):
        kernel.commit_approval("r5b-expired", expired)
    never_expires = _sign_raw({
        "v": 1, "typ": "scp.approval", "task_id": "r5b-expired", "scope": "approval:grant:r5b-expired",
        "iss": "alice-operator", "iat": float("nan"), "exp": float("nan"), "jti": "nan",
    })
    with pytest.raises(InvalidTokenSignatureError):
        kernel.commit_approval("r5b-expired", never_expires)
    _assert_not_approved(kernel, "r5b-expired")


def test_kernel_without_issuer_public_key_fails_closed(kernel, monkeypatch):
    from scp.security.approval_issuer import issue_approval_token

    _waiting(kernel, "r5b-no-pubkey")
    token = issue_approval_token("r5b-no-pubkey", issuer="alice-operator")
    monkeypatch.delenv("SCP_APPROVAL_ISSUER_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("SCP_APPROVAL_ISSUER_PUBLIC_KEY_FILE", raising=False)
    with pytest.raises(InvalidTokenSignatureError):
        kernel.commit_approval("r5b-no-pubkey", token)
    _assert_not_approved(kernel, "r5b-no-pubkey")


def test_checker_gate_recommends_but_never_approves(kernel, tmp_path):
    target = tmp_path / "service.py"
    target.write_text("def compute_status():\n    code = 100\n    return code\n", encoding="utf-8")
    _waiting(kernel, "r5b-gate")
    patch = (
        "<<<<<<< SEARCH\n    code = 100\n    return code\n=======\n"
        "    code = 200\n    return code\n>>>>>>> REPLACE\n"
    )
    result = kernel.autonomous_review_and_approve(
        task_id="r5b-gate", candidate_patch=patch, target_file=str(target),
        actor="autonomous_checker", repo_root=tmp_path,
    )
    assert result.approved is False
    assert result.verdict == "RECOMMEND_APPROVE"
    assert result.token is None
    assert result.checks["approval_recommended"] is True
    _assert_not_approved(kernel, "r5b-gate")
