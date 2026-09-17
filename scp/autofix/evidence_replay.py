"""Evidence replay with sandboxed observation and signed provenance.

This module is deliberately conservative.  A replay is *not* evidence merely
because a Python function returned ``True``: the three code states must be run
through the existing :mod:`scp.sandbox_evaluator`, their observable results
must be bound to an exact task/attempt/source identity, and an independent
signed verifier receipt must match that binding before the result can be
``VERIFIED``.

The old implementation returned a hard-coded ``VERIFIED``/``mock_signature``.
No source supplied to this module is executed directly; replay execution is
always delegated to ``sandbox_evaluator.evaluate``.  Missing, malformed or
mismatching identity/receipt data is returned as ``UNVERIFIED`` (never as a
successful fallback).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shlex
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping, Sequence

from scp.core.verifier_receipt import (
    InvalidReceiptSignatureError,
    VerifierReceipt,
    sign_verifier_receipt,
    verify_verifier_receipt,
)

logger = logging.getLogger("scp.autofix.evidence_replay")

_SCHEMA = "scp-evidence-replay-v1"
_OBSERVATION_SCHEMA = "scp-evidence-replay-observation-v1"
_MAX_TEXT = 1024
_MAX_COMMAND = 2048
_MAX_TEST_TIME_S = 120
_SHA40_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_SHA64_RE = re.compile(r"^(?:sha256:)?[0-9a-fA-F]{64}$")
_SHELL_META = frozenset(";|&$`><\n\r\"'!*?[]{}~")


class EvidenceRole(str, Enum):
    """Classification of B/S/G replay observations.

    ``VERIFIER`` is retained as a compatibility value only.  It is never
    emitted by a default path and is never sufficient for a verified receipt.
    """

    GOLD_ALIGNED = "gold_aligned"
    REGRESSION_ONLY = "regression_only"
    MISLEADING = "misleading"
    CANDIDATE_SPECIFIC = "candidate_specific"
    DIAGNOSTIC_NEGATIVE = "diagnostic_negative"
    VERIFIER = "VERIFIER"


_DISCRIMINATING_ROLES = frozenset(
    {
        EvidenceRole.GOLD_ALIGNED,
        EvidenceRole.CANDIDATE_SPECIFIC,
        EvidenceRole.DIAGNOSTIC_NEGATIVE,
    }
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return f"sha256:{_sha256_bytes(_canonical_json(value).encode('utf-8'))}"


def _observation_digest(
    *,
    task_id: str,
    attempt_id: str,
    source_sha: str,
    provenance: Mapping[str, Any],
    test_command: str,
    role: EvidenceRole,
    observations: Mapping[str, "ReplayStateObservation"],
) -> str:
    payload = {
        "schema": _OBSERVATION_SCHEMA,
        "task_id": task_id,
        "attempt_id": attempt_id,
        "source_sha": source_sha,
        "provenance": provenance,
        "test_command_sha256": f"sha256:{_sha256_bytes(test_command.encode('utf-8'))}",
        "role": role.value,
        "states": {
            key: {
                "state": value.state,
                "executed": value.executed,
                "passed": value.passed,
                "verdict": value.verdict,
                "returncode": value.returncode,
                "reason": value.reason,
                "evaluator": value.evaluator,
                "output_sha256": value.output_sha256,
            }
            for key, value in sorted(observations.items())
        },
    }
    return _sha256_json(payload)


def _bounded_text(value: Any, limit: int = _MAX_TEXT) -> str:
    text = str(value or "").replace("\x00", "")
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...[truncated]"


def _valid_sha(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(_SHA40_RE.fullmatch(text) or _SHA64_RE.fullmatch(text))


def _normalise_provenance(value: Any) -> dict[str, Any]:
    """Validate provenance as an identity-bearing canonical object.

    Provenance is untrusted data.  It must carry the same task/attempt/source
    identity as the replay; a free-form string or unrelated object cannot
    authorize a receipt.
    """

    if not isinstance(value, Mapping) or not value:
        raise ValueError("provenance must be a non-empty object")
    try:
        normalised = json.loads(_canonical_json(dict(value)))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("provenance is not canonical JSON") from exc
    if not isinstance(normalised, dict) or not normalised:
        raise ValueError("provenance is empty")
    return normalised


def _validate_provenance_identity(
    provenance: Mapping[str, Any], *, task_id: str, attempt_id: str, source_sha: str
) -> None:
    required = {
        "task_id": task_id,
        "attempt_id": attempt_id,
        "source_sha": source_sha,
    }
    for key, expected in required.items():
        if str(provenance.get(key, "")).strip() != expected:
            raise ValueError(f"provenance {key} mismatch")


def _safe_relpath(value: str) -> str | None:
    """Return a workspace-relative path, rejecting traversal/absolute paths."""

    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        return None
    win = PureWindowsPath(value)
    if win.is_absolute() or win.drive or win.root:
        return None
    posix = PurePosixPath(value.replace("\\", "/"))
    if posix.is_absolute() or any(part in {"..", ""} for part in posix.parts):
        return None
    return "/".join(part for part in posix.parts if part != ".") or None


def _command_tokens(command: str | Sequence[str]) -> list[str] | None:
    """Parse an allowed test command without executing it.

    The command is either a pytest invocation or ``python -c CODE``.  The
    latter is converted to a pytest test file before it reaches the existing
    sandbox evaluator; it is never passed to a shell or executed in this
    process.
    """

    if isinstance(command, (list, tuple)):
        tokens = [str(item) for item in command]
    elif isinstance(command, str):
        try:
            if sys.platform.startswith("win"):
                tokens = shlex.split(command, posix=False)
                tokens = [
                    token[1:-1]
                    if len(token) >= 2 and token[0] == token[-1] and token[0] in {"'", '"'}
                    else token
                    for token in tokens
                ]
            else:
                tokens = shlex.split(command)
        except ValueError:
            return None
    else:
        return None
    if not tokens or len(" ".join(tokens)) > _MAX_COMMAND:
        return None
    executable = Path(tokens[0]).name.lower()
    is_python = executable in {"python", "python.exe", "python3", "python3.exe"}
    try:
        is_current_python = Path(tokens[0]).resolve() == Path(sys.executable).resolve()
    except OSError:
        is_current_python = False
    if executable == "pytest" or executable == "pytest.exe":
        return tokens
    if (is_python or is_current_python) and len(tokens) >= 3:
        if tokens[1] == "-c" and len(tokens) == 3 and tokens[2].strip():
            return tokens
        if tokens[1] == "-m" and tokens[2] == "pytest":
            return tokens
    return None


def _command_test_file(tokens: list[str] | None) -> dict[str, str]:
    """Convert an allowed ``python -c`` command into a pytest test file."""

    if not tokens or len(tokens) != 3 or tokens[1] != "-c":
        return {}
    code = tokens[2]
    # The code remains untrusted input.  It is only embedded into a file that
    # the existing sandbox evaluator copies into its temporary workspace.
    indented = "\n".join(f"    {line}" for line in code.splitlines())
    return {"tests/test_replay_command.py": f"def test_replay_command():\n{indented}\n"}


def compute_bug_signature(bug_type: str, description: str) -> str:
    """Compute the stable SHA-256 lookup key used by ``GoldDataset``."""

    if not isinstance(bug_type, str) or not isinstance(description, str):
        raise TypeError("bug_type and description must be strings")
    return _sha256_bytes(f"{bug_type}:{description}".encode("utf-8"))


def source_content_sha(source: str) -> str:
    """Return a content identity suitable for explicit replay provenance."""

    if not isinstance(source, str) or not source:
        raise ValueError("source must be non-empty text")
    return f"sha256:{_sha256_bytes(source.encode('utf-8'))}"


class GoldDataset:
    """Read-only JSONL gold dataset.

    No environment variable can seed a synthetic entry.  A missing or malformed
    entry is simply unavailable and therefore cannot produce a verified replay.
    """

    def __init__(self, data_dir: str | os.PathLike[str] | None = None, *, dataset_path: str | os.PathLike[str] | None = None):
        raw = dataset_path if dataset_path is not None else data_dir
        path = Path(raw) if raw is not None else Path("data") / "gold_dataset.jsonl"
        self.dataset_path = path / "gold_dataset.jsonl" if path.is_dir() else path
        self._cache: dict[str, dict[str, Any]] | None = None

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._cache is not None:
            return self._cache
        rows: dict[str, dict[str, Any]] = {}
        if self.dataset_path.is_file():
            try:
                for line_no, raw in enumerate(self.dataset_path.read_text(encoding="utf-8").splitlines(), 1):
                    if not raw.strip():
                        continue
                    try:
                        entry = json.loads(raw)
                    except json.JSONDecodeError:
                        logger.warning("gold dataset line %s is malformed", line_no)
                        continue
                    if not isinstance(entry, dict) or not isinstance(entry.get("bug_signature"), str):
                        logger.warning("gold dataset line %s has no valid bug_signature", line_no)
                        continue
                    rows[entry["bug_signature"]] = entry
            except OSError as exc:
                logger.warning("gold dataset read failed: %s", type(exc).__name__)
        self._cache = rows
        return rows

    def get_entry(self, signature: str) -> dict[str, Any] | None:
        entry = self._load().get(str(signature))
        return dict(entry) if entry is not None else None

    def get_gold_fix(self, signature: str) -> str | None:
        entry = self.get_entry(signature)
        return str(entry["gold_source"]) if entry and isinstance(entry.get("gold_source"), str) else None

    def list_entries(self) -> list[dict[str, Any]]:
        return [dict(entry) for entry in self._load().values()]


@dataclass(frozen=True)
class ReplayStateObservation:
    """Bounded observable result for one B/S/G evaluator run."""

    state: str
    executed: bool
    passed: bool
    verdict: str
    returncode: int | None
    reason: str
    evaluator: str
    output_sha256: str
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "executed": self.executed,
            "passed": self.passed,
            "verdict": self.verdict,
            "returncode": self.returncode,
            "reason": self.reason,
            "evaluator": self.evaluator,
            "output_sha256": self.output_sha256,
            "stdout": _bounded_text(self.stdout),
            "stderr": _bounded_text(self.stderr),
            "duration_seconds": round(float(self.duration_seconds), 3),
        }


@dataclass
class ReplayResult:
    """B/S/G result plus identity and receipt status."""

    role: EvidenceRole
    b_result: tuple[bool, str]
    s_result: tuple[bool, str]
    g_result: tuple[bool, str]
    test_command: str
    task_id: str = ""
    attempt_id: str = ""
    source_sha: str = ""
    provenance: Any = None
    observations: dict[str, ReplayStateObservation] = field(default_factory=dict)
    observation_digest: str = ""
    receipt_status: str = "UNVERIFIED"
    verification_reason: str = "receipt not verified"
    discriminating: bool = field(init=False)

    def __post_init__(self) -> None:
        self.discriminating = self.role in _DISCRIMINATING_ROLES

    @property
    def verified(self) -> bool:
        return self.receipt_status == "VERIFIED"

    def observation_payload(self) -> dict[str, Any]:
        states = {
            key: value.to_dict()
            for key, value in sorted(self.observations.items())
        }
        return {
            "schema": _OBSERVATION_SCHEMA,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "source_sha": self.source_sha,
            "provenance": self.provenance,
            "test_command_sha256": f"sha256:{_sha256_bytes(self.test_command.encode('utf-8'))}",
            "role": self.role.value,
            "states": states,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": _SCHEMA,
            "role": self.role.value,
            "raw_role": self.role.value,
            "b_pass": self.b_result[0],
            "s_pass": self.s_result[0],
            "g_pass": self.g_result[0],
            "b_snippet": _bounded_text(self.b_result[1]),
            "s_snippet": _bounded_text(self.s_result[1]),
            "g_snippet": _bounded_text(self.g_result[1]),
            "test_command": _bounded_text(self.test_command, _MAX_COMMAND),
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "source_sha": self.source_sha,
            "provenance": self.provenance,
            "observations": {
                key: value.to_dict() for key, value in sorted(self.observations.items())
            },
            "observation_digest": self.observation_digest,
            "receipt_status": self.receipt_status,
            "verification_reason": self.verification_reason,
            "discriminating": self.discriminating,
        }

    def __str__(self) -> str:
        return (
            f"ReplayResult(role={self.role.value}, "
            f"b={'PASS' if self.b_result[0] else 'FAIL'}, "
            f"s={'PASS' if self.s_result[0] else 'FAIL'}, "
            f"g={'PASS' if self.g_result[0] else 'FAIL'}, "
            f"receipt={self.receipt_status})"
        )


class EvidenceReplay:
    """Run B/S/G through the existing sandbox evaluator and verify its receipt."""

    def __init__(self, working_dir: str | os.PathLike[str] | None = None, dataset: Any = None):
        self.working_dir = Path(working_dir or ".").resolve()
        self.dataset = dataset

    @staticmethod
    def _classify(b_pass: bool, s_pass: bool, g_pass: bool) -> EvidenceRole:
        if not b_pass and s_pass and g_pass:
            return EvidenceRole.GOLD_ALIGNED
        if b_pass and s_pass and g_pass:
            return EvidenceRole.REGRESSION_ONLY
        if b_pass and s_pass and not g_pass:
            return EvidenceRole.MISLEADING
        if not b_pass and s_pass and not g_pass:
            return EvidenceRole.CANDIDATE_SPECIFIC
        return EvidenceRole.DIAGNOSTIC_NEGATIVE

    @staticmethod
    def make_evidence_ref(
        *, task_id: str, attempt_id: str, source_sha: str, provenance: Any, observation_digest: str
    ) -> str:
        payload = {
            "schema": _SCHEMA,
            "task_id": task_id,
            "attempt_id": attempt_id,
            "source_sha": source_sha,
            "provenance": provenance,
            "observation_digest": observation_digest,
        }
        return _canonical_json(payload)

    def _evaluate_state(
        self,
        *,
        state: str,
        source: str,
        target_relpath: str,
        test_files: Mapping[str, str],
        extra_files: Mapping[str, str],
        test_paths: list[str],
        timeout_seconds: int,
    ) -> ReplayStateObservation:
        """Evaluate exactly one state through ``sandbox_evaluator``."""

        try:
            from scp.sandbox_evaluator.evaluator import evaluate

            patch_target: dict[str, Any] = {
                "files": {target_relpath: source},
                "extra_files": dict(extra_files),
                "test_files": dict(test_files),
                "test_paths": list(test_paths),
                "timeout_seconds": max(5, min(900, int(timeout_seconds))),
            }
            result = evaluate(patch_target)
            stdout = str(result.stdout or "")
            stderr = str(result.stderr or "")
            # Pytest appends wall-clock duration to its summary. Normalize
            # that nondeterministic presentation while retaining stdout/stderr
            # itself in the bounded observation artifact.
            stable_output = re.sub(
                r"in\s+[0-9]+(?:\.[0-9]+)?s",
                "in <duration>s",
                stdout + "\n" + stderr,
            )
            output_sha = f"sha256:{_sha256_bytes(stable_output.encode('utf-8', 'replace'))}"
            return ReplayStateObservation(
                state=state,
                executed=result.returncode is not None,
                passed=bool(result.verdict == "PASS"),
                verdict=str(result.verdict),
                returncode=result.returncode,
                reason=str(result.reason or ""),
                evaluator="scp.sandbox_evaluator.evaluate",
                output_sha256=output_sha,
                stdout=stdout,
                stderr=stderr,
                duration_seconds=float(result.duration_seconds),
            )
        except Exception as exc:  # fail closed; no direct fallback execution
            return ReplayStateObservation(
                state=state,
                executed=False,
                passed=False,
                verdict="FAIL",
                returncode=None,
                reason=f"sandbox evaluator unavailable: {type(exc).__name__}",
                evaluator="scp.sandbox_evaluator.evaluate",
                output_sha256="",
            )

    def classify_evidence(
        self,
        test_command: str | Sequence[str],
        buggy_source: str,
        candidate_source: str,
        gold_source: str,
        file_path: str,
        *,
        task_id: str | None = None,
        attempt_id: str | None = None,
        source_sha: str | None = None,
        provenance: Any = None,
        receipt: VerifierReceipt | Mapping[str, Any] | None = None,
        test_files: Mapping[str, str] | None = None,
        extra_files: Mapping[str, str] | None = None,
        test_paths: Sequence[str] | None = None,
        timeout_seconds: int = _MAX_TEST_TIME_S,
        secret: str | bytes | None = None,
    ) -> ReplayResult:
        """Replay the three states in the existing isolated evaluator.

        ``receipt`` is optional only so callers can inspect an unverified
        observation and then obtain an independent receipt.  A result without a
        matching receipt is explicitly ``UNVERIFIED`` and is not promoted.
        """

        command_tokens = _command_tokens(test_command)
        command_text = " ".join(command_tokens or ([str(test_command)] if test_command is not None else []))
        identity_error: str | None = None
        try:
            task = str(task_id or "").strip()
            attempt = str(attempt_id or "").strip()
            sha = str(source_sha or "").strip()
            prov = _normalise_provenance(provenance)
            _validate_provenance_identity(
                prov, task_id=task, attempt_id=attempt, source_sha=sha
            )
            if not task:
                raise ValueError("task_id is missing")
            if not attempt:
                raise ValueError("attempt_id is missing")
            if not _valid_sha(sha):
                raise ValueError("source_sha must be an exact SHA-1 or sha256 digest")
        except ValueError as exc:
            task = str(task_id or "").strip()
            attempt = str(attempt_id or "").strip()
            sha = str(source_sha or "").strip()
            prov = provenance
            identity_error = str(exc)

        target_relpath = _safe_relpath(str(file_path or ""))
        if target_relpath is None:
            identity_error = identity_error or "file_path must be a safe workspace-relative path"
        if command_tokens is None:
            identity_error = identity_error or "test_command must be an allowed pytest/python test command"
        if not isinstance(buggy_source, str) or not buggy_source:
            identity_error = identity_error or "buggy_source is missing"
        if not isinstance(candidate_source, str) or not candidate_source:
            identity_error = identity_error or "candidate_source is missing"
        if not isinstance(gold_source, str) or not gold_source:
            identity_error = identity_error or "gold_source is missing"

        safe_test_files: dict[str, str] = {}
        generated_test_files = _command_test_file(command_tokens)
        for relpath, content in generated_test_files.items():
            safe_test_files[relpath] = content
        for relpath, content in dict(test_files or {}).items():
            safe = _safe_relpath(str(relpath))
            if safe is None or not safe.endswith(".py") or not isinstance(content, str):
                identity_error = identity_error or f"invalid test file: {relpath!r}"
                continue
            safe_test_files[safe] = content
        safe_extra_files: dict[str, str] = {}
        for relpath, content in dict(extra_files or {}).items():
            safe = _safe_relpath(str(relpath))
            if safe is None or not isinstance(content, str):
                identity_error = identity_error or f"invalid extra file: {relpath!r}"
                continue
            safe_extra_files[safe] = content
        safe_test_paths = [str(path) for path in (test_paths or [])]
        # Disk test paths are accepted only beneath the configured working dir;
        # the evaluator itself performs the final copy/path checks.
        for raw_path in safe_test_paths:
            path = Path(raw_path)
            resolved = (path if path.is_absolute() else self.working_dir / path).resolve()
            if not resolved.is_relative_to(self.working_dir) or resolved.suffix != ".py":
                identity_error = identity_error or f"test path escapes working_dir: {raw_path!r}"

        if identity_error:
            empty = (False, f"UNVERIFIED: {identity_error}")
            return ReplayResult(
                role=EvidenceRole.DIAGNOSTIC_NEGATIVE,
                b_result=empty,
                s_result=empty,
                g_result=empty,
                test_command=command_text,
                task_id=task,
                attempt_id=attempt,
                source_sha=sha,
                provenance=prov,
                receipt_status="UNVERIFIED",
                verification_reason=identity_error,
            )
        if not safe_test_files and not safe_test_paths:
            empty = (False, "UNVERIFIED: no test_files or test_paths supplied")
            return ReplayResult(
                role=EvidenceRole.DIAGNOSTIC_NEGATIVE,
                b_result=empty,
                s_result=empty,
                g_result=empty,
                test_command=command_text,
                task_id=task,
                attempt_id=attempt,
                source_sha=sha,
                provenance=prov,
                receipt_status="UNVERIFIED",
                verification_reason="no test_files or test_paths supplied",
            )

        observations: dict[str, ReplayStateObservation] = {}
        for state, source in (
            ("B", buggy_source),
            ("S", candidate_source),
            ("G", gold_source),
        ):
            observations[state] = self._evaluate_state(
                state=state,
                source=source,
                target_relpath=target_relpath or "module.py",
                test_files=safe_test_files,
                extra_files=safe_extra_files,
                test_paths=safe_test_paths,
                timeout_seconds=timeout_seconds,
            )

        raw_role = self._classify(
            observations["B"].passed,
            observations["S"].passed,
            observations["G"].passed,
        )
        # Keep the digest stable across repeat calls: duration and raw output
        # snippets are observable but not identity material; output_sha256,
        # return code and evaluator result are identity material.
        observation_digest = _observation_digest(
            task_id=task,
            attempt_id=attempt,
            source_sha=sha,
            provenance=prov,
            test_command=command_text,
            role=raw_role,
            observations=observations,
        )
        result = ReplayResult(
            role=raw_role,
            b_result=(observations["B"].passed, _bounded_text(observations["B"].stdout or observations["B"].stderr)),
            s_result=(observations["S"].passed, _bounded_text(observations["S"].stdout or observations["S"].stderr)),
            g_result=(observations["G"].passed, _bounded_text(observations["G"].stdout or observations["G"].stderr)),
            test_command=command_text,
            task_id=task,
            attempt_id=attempt,
            source_sha=sha,
            provenance=prov,
            observations=observations,
            observation_digest=observation_digest,
            receipt_status="UNVERIFIED",
            verification_reason="receipt not verified",
        )
        # Verify the independent receipt against this exact observation.  No
        # receipt means the role remains visible for diagnosis but cannot be
        # promoted to VERIFIED.
        verified = self.verify(
            task_id=task,
            attempt_id=attempt,
            source_sha=sha,
            provenance=prov,
            replay_result=result,
            receipt=receipt,
            secret=secret,
        )
        result.receipt_status = str(verified.get("status", "UNVERIFIED"))
        result.verification_reason = str(verified.get("reason", "receipt verification failed"))
        # Keep the measured B/S/G role visible for diagnosis, but expose the
        # independent receipt state separately.  Only ``receipt_status`` can
        # authorize promotion; a role alone is never a VERIFIED claim.
        return result

    def verify(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Verify exact replay identity, observation and HMAC receipt.

        The method intentionally returns a structured fail-closed result for
        compatibility with existing phase runners.  It never manufactures a
        success object when a required field or independent observation is
        missing.
        """

        task_id = str(kwargs.get("task_id", args[0] if args else "") or "").strip()
        attempt_id = str(kwargs.get("attempt_id", "") or "").strip()
        source_sha = str(
            kwargs.get("source_sha", kwargs.get("sha", kwargs.get("expected_source_sha", ""))) or ""
        ).strip()
        provenance = kwargs.get("provenance")
        receipt = kwargs.get("receipt")
        replay = kwargs.get("replay_result") or kwargs.get("observed_result") or kwargs.get("replay_observation")
        errors: list[str] = []
        try:
            if not task_id:
                errors.append("missing task_id")
            if not attempt_id:
                errors.append("missing attempt_id")
            if not _valid_sha(source_sha):
                errors.append("missing or invalid source_sha")
            expected_prov = _normalise_provenance(provenance)
        except ValueError as exc:
            expected_prov = provenance
            errors.append(str(exc))

        if replay is None:
            errors.append("missing replay observation")
            replay_dict: dict[str, Any] = {}
        elif isinstance(replay, ReplayResult):
            replay_dict = replay.to_dict()
        elif isinstance(replay, Mapping):
            replay_dict = dict(replay)
        else:
            errors.append("invalid replay observation type")
            replay_dict = {}

        if replay_dict:
            if replay_dict.get("schema") not in {_SCHEMA, _OBSERVATION_SCHEMA}:
                errors.append("replay observation schema mismatch")
            if str(replay_dict.get("task_id", "")).strip() != task_id:
                errors.append("task_id mismatch")
            if str(replay_dict.get("attempt_id", "")).strip() != attempt_id:
                errors.append("attempt_id mismatch")
            if str(replay_dict.get("source_sha", "")).strip() != source_sha:
                errors.append("source_sha mismatch")
            try:
                if _canonical_json(replay_dict.get("provenance")) != _canonical_json(expected_prov):
                    errors.append("provenance mismatch")
            except (TypeError, ValueError):
                errors.append("provenance is not canonical")
            observation_digest = str(
                replay_dict.get("observation_digest", replay_dict.get("result_digest", "")) or ""
            )
            if not observation_digest or not _SHA64_RE.fullmatch(observation_digest):
                errors.append("missing observation digest")
            elif isinstance(replay, ReplayResult):
                recomputed = _observation_digest(
                    task_id=task_id,
                    attempt_id=attempt_id,
                    source_sha=source_sha,
                    provenance=expected_prov,
                    test_command=replay.test_command,
                    role=replay.role,
                    observations=replay.observations,
                )
                if observation_digest != recomputed:
                    errors.append("observation digest mismatch")
        else:
            observation_digest = ""

        if receipt is None:
            errors.append("missing verifier receipt")
        else:
            try:
                verify_verifier_receipt(receipt, secret=kwargs.get("secret"), task_id=task_id)
                receipt_dict = receipt.to_dict() if isinstance(receipt, VerifierReceipt) else dict(receipt)
                if str(receipt_dict.get("attempt_id", "")).strip() != attempt_id:
                    errors.append("receipt attempt_id mismatch")
                expected_ref = self.make_evidence_ref(
                    task_id=task_id,
                    attempt_id=attempt_id,
                    source_sha=source_sha,
                    provenance=expected_prov,
                    observation_digest=observation_digest,
                )
                if str(receipt_dict.get("evidence_ref", "")) != expected_ref:
                    errors.append("receipt evidence_ref mismatch")
                if str(receipt_dict.get("verdict", "")) != "VERIFIED":
                    errors.append("receipt verdict mismatch")
            except (InvalidReceiptSignatureError, TypeError, ValueError) as exc:
                errors.append(f"receipt verification failed: {type(exc).__name__}")

        if errors:
            return {
                "ok": False,
                "status": "UNVERIFIED",
                "reason": "; ".join(errors),
                "errors": errors,
                "task_id": task_id,
                "attempt_id": attempt_id,
                "source_sha": source_sha,
            }
        return {
            "ok": True,
            "status": "VERIFIED",
            "reason": "exact replay observation and signed receipt verified",
            "task_id": task_id,
            "attempt_id": attempt_id,
            "source_sha": source_sha,
            "observation_digest": observation_digest,
            "receipt": receipt.to_dict() if isinstance(receipt, VerifierReceipt) else dict(receipt),
        }


__all__ = [
    "EvidenceRole",
    "ReplayStateObservation",
    "ReplayResult",
    "GoldDataset",
    "EvidenceReplay",
    "compute_bug_signature",
    "source_content_sha",
]
