"""Direct Q17/Q19 contracts: replay receipts and raw SSE event boundaries."""
from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from scp.api.routes import stream_routes
from scp.api_server import app
from scp.autofix.evidence_replay import EvidenceReplay, EvidenceRole, source_content_sha
from scp.core.verifier_receipt import VerifierReceipt, sign_verifier_receipt


_REPLAY_SECRET = "q17-replay-test-secret-with-sufficient-length"
_REPLAY_SHA = "a" * 40


def _replay_inputs():
    buggy = "def answer():\n    return 0\n"
    fixed = "def answer():\n    return 1\n"
    test_files = {
        "tests/test_answer.py": (
            "from module import answer\n"
            "def test_answer():\n"
            "    assert answer() == 1\n"
        )
    }
    provenance = {
        "task_id": "task-q17-positive",
        "attempt_id": "attempt-q17-positive",
        "source_sha": _REPLAY_SHA,
        "step_id": "evidence-replay",
    }
    return buggy, fixed, test_files, provenance


def _make_signed_receipt(result, secret: str = _REPLAY_SECRET):
    evidence_ref = EvidenceReplay.make_evidence_ref(
        task_id=result.task_id,
        attempt_id=result.attempt_id,
        source_sha=result.source_sha,
        provenance=result.provenance,
        observation_digest=result.observation_digest,
    )
    return sign_verifier_receipt(
        VerifierReceipt(
            task_id=result.task_id,
            verifier_id="independent-replay-test-verifier",
            verdict="VERIFIED",
            evidence_ref=evidence_ref,
            issued_at=__import__("time").time(),
            attempt_id=result.attempt_id,
        ),
        secret=secret,
    )


def _run_replay(*, receipt=None, provenance=None):
    buggy, fixed, test_files, default_provenance = _replay_inputs()
    return EvidenceReplay().classify_evidence(
        [sys.executable, "-m", "pytest", "tests/test_answer.py"],
        buggy,
        fixed,
        fixed,
        "module.py",
        task_id="task-q17-positive",
        attempt_id="attempt-q17-positive",
        source_sha=_REPLAY_SHA,
        provenance=provenance or default_provenance,
        receipt=receipt,
        test_files=test_files,
    )


def test_q17_positive_replay_requires_real_observation_and_signed_receipt():
    observed = _run_replay()
    assert observed.role is EvidenceRole.GOLD_ALIGNED
    assert observed.b_result[0] is False
    assert observed.s_result[0] is True
    assert observed.g_result[0] is True
    assert observed.receipt_status == "UNVERIFIED"

    signed = _make_signed_receipt(observed)
    verified = EvidenceReplay().verify(
        task_id=observed.task_id,
        attempt_id=observed.attempt_id,
        source_sha=observed.source_sha,
        provenance=observed.provenance,
        replay_result=observed,
        receipt=signed,
        secret=_REPLAY_SECRET,
    )
    assert verified["status"] == "VERIFIED"
    assert verified["ok"] is True
    assert observed.observation_digest.startswith("sha256:")
    assert all(item.executed for item in observed.observations.values())
    assert all(item.evaluator == "scp.sandbox_evaluator.evaluate" for item in observed.observations.values())


def test_q17_missing_receipt_is_not_verified():
    result = _run_replay()
    assert result.receipt_status == "UNVERIFIED"
    assert result.verified is False
    assert "missing verifier receipt" in result.verification_reason


def test_q17_mismatched_attempt_and_tampered_observation_fail_closed():
    observed = _run_replay()
    signed = _make_signed_receipt(observed)
    tampered = replace(observed, attempt_id="attempt-other")
    checked = EvidenceReplay().verify(
        task_id=tampered.task_id,
        attempt_id=tampered.attempt_id,
        source_sha=tampered.source_sha,
        provenance=tampered.provenance,
        replay_result=tampered,
        receipt=signed,
        secret=_REPLAY_SECRET,
    )
    assert checked["ok"] is False
    assert checked["status"] == "UNVERIFIED"

    digest_tampered = replace(observed, observation_digest="sha256:" + "b" * 64)
    checked_digest = EvidenceReplay().verify(
        task_id=digest_tampered.task_id,
        attempt_id=digest_tampered.attempt_id,
        source_sha=digest_tampered.source_sha,
        provenance=digest_tampered.provenance,
        replay_result=digest_tampered,
        receipt=signed,
        secret=_REPLAY_SECRET,
    )
    assert checked_digest["ok"] is False
    assert "observation digest mismatch" in checked_digest["reason"]


def _parse_raw_sse(raw: str) -> list[dict]:
    """Parse full SSE frames; reject split-token or non-JSON lines."""
    frames = []
    for frame in raw.split("\n\n"):
        if not frame.strip():
            continue
        lines = frame.splitlines()
        assert len(lines) == 1, frame
        assert lines[0].startswith("data: "), frame
        payload = lines[0][len("data: ") :]
        value = json.loads(payload)
        assert isinstance(value, dict)
        frames.append(value)
    return frames


@pytest.fixture(autouse=True)
def _stream_auth(monkeypatch):
    monkeypatch.setenv("SCP_AUTH_TOKEN_SECRET", "q19-stream-test-token-with-sufficient-length")
    monkeypatch.setenv("SCP_EGRESS_MODE", "deny")
    from scp.security import auth

    auth._auth_failures.clear()
    yield
    auth._auth_failures.clear()


def test_q19_raw_sse_withheld_never_contains_candidate_text():
    token = "q19-stream-test-token-with-sufficient-length"
    with TestClient(app) as client:
        response = client.post(
            "/v105/ask/stream",
            json={"question": "Q19 direct parse", "ai_answer": "CANDIDATE-MUST-NOT-LEAK"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    frames = _parse_raw_sse(response.text)
    assert frames[-1]["step"] == "final"
    assert frames[-1]["status"] in {"complete", "withheld"}
    if frames[-1]["status"] == "withheld":
        assert frames[-1]["candidate"] is None
        assert frames[-1]["final_answer"] is None
        assert "CANDIDATE-MUST-NOT-LEAK" not in response.text


@pytest.mark.parametrize("verdict", sorted(stream_routes._WITHHELD_VERDICTS))
def test_q19_policy_verdicts_are_withheld_without_split_token_loophole(verdict):
    data = {
        "verdict": verdict,
        "governance_decision": "UPHOLD",
        "run_status": verdict,
    }
    assert stream_routes._withheld(data) is True
    wire = stream_routes._sse({
        "step": "final",
        "status": "withheld",
        "verdict": verdict,
        "final_answer": None,
        "candidate": None,
    })
    frames = _parse_raw_sse(wire)
    assert len(frames) == 1
    assert frames[0]["candidate"] is None
    assert frames[0]["final_answer"] is None
