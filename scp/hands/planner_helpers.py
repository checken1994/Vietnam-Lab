"""Lease, journal, validation, and condition helpers for HandsPlanner."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from typing import Any

from scp.security.capability_epoch import parse_capability_token

logger = logging.getLogger(__name__)

PLAN_VERSION = "3.7"
PLAN_STATES = {
    "PLANNED",
    "RUNNING",
    "WAITING_APPROVAL",
    "VERIFIED",
    "FAILED",
    "ROLLED_BACK",
    "COMPLETED",
    "UNKNOWN",
    "HUMAN_REVIEW",
}
STEP_STATES = {
    "PLANNED",
    "RUNNING",
    "WAITING_APPROVAL",
    "VERIFIED",
    "FAILED",
    "ROLLED_BACK",
    "UNKNOWN",
    "HUMAN_REVIEW",
}
RECOVERY_DECISIONS = {"NOT_APPLIED", "APPLIED", "PARTIAL", "CONFLICT", "UNKNOWN"}
PRECONDITION_TYPES = {"always", "previous_steps_verified", "plan_state", "step_state"}
POSTCONDITION_TYPES = {
    "verification_passed",
    "success",
    "text_contains",
    "url_prefix",
    "evidence_field_equals",
    "field_equals",
}
RETRY_TYPES = {"verification_failed", "execution_error", "always"}


class HandsPlannerHelpersMixin:
    """Lease, journal, step validation, and condition evaluation helpers."""

    @staticmethod
    def _now() -> float:
        return time.time()

    @staticmethod
    def _bounded_text(value: Any, limit: int = 5000) -> str:
        return str(value or "")[:limit]

    def _attach_governor_grant(self, step: dict[str, Any], plan: dict[str, Any]) -> None:
        """Bind a governor decision to the exact driver target for this step."""
        if step.get("confirmationId") or step.get("confirmation_id"):
            return
        from scp.security.confirmation_store import get_confirmation_store

        params = step.setdefault("params", {})
        action = str(step.get("action", ""))
        if action == "pc.write_file":
            target = str(self.executor.controller._resolve_path(str(params.get("path", ""))))
        elif action == "pc.execute":
            target = str(params.get("command", ""))
        elif action == "pc.rollback":
            target = str(params.get("checkpoint_id") or params.get("checkpointId") or "")
        else:
            target = json.dumps(params, sort_keys=True, default=str)
        grant_id = f"{plan.get('planId', 'plan')}:{step.get('stepId', 'step')}"
        confirmation_id = get_confirmation_store().record_governor_grant(
            action=action,
            target=target,
            grant_id=grant_id,
            metadata={"plan_id": plan.get("planId"), "step_id": step.get("stepId")},
        )
        step["confirmationId"] = confirmation_id
        params["confirmation_id"] = confirmation_id

    @staticmethod
    def _constant_time_equal(left: Any, right: Any) -> bool:
        left_text = str(left or "")
        right_text = str(right or "")
        if len(left_text) != len(right_text):
            return False
        difference = 0
        for left_char, right_char in zip(left_text, right_text, strict=True):
            difference |= ord(left_char) ^ ord(right_char)
        return difference == 0

    def _lease_path(self, plan_id: str) -> Any:
        safe_id = "".join(char for char in str(plan_id) if char.isalnum() or char in {"-", "_"})[:128]
        return self.lease_dir / f"{safe_id}.json"

    def _claim_lease(self, plan_id: str, ttl_seconds: int = 120) -> str | None:
        lease_path = self._lease_path(plan_id)
        now = self._now()
        try:
            if lease_path.exists():
                try:
                    current = json.loads(lease_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    logger.debug('HandsPlanner._claim_lease: OSError, json.JSONDecodeError ignored', exc_info=True)
                    current = {}
                if float(current.get("expiresAt", 0) or 0) > now:
                    return None
                stale_path = lease_path.with_name(f"{lease_path.stem}.expired-{uuid.uuid4().hex}.json")
                try:
                    os.replace(lease_path, stale_path)
                except FileNotFoundError:
                    logger.debug('HandsPlanner._claim_lease: FileNotFoundError ignored', exc_info=True)
                    return None
            token = uuid.uuid4().hex
            payload = {"planId": plan_id, "token": token, "ownerPid": os.getpid(), "startedAt": now, "heartbeatAt": now, "expiresAt": now + ttl_seconds}
            fd = os.open(str(lease_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=True)
                    handle.flush()
                    os.fsync(handle.fileno())
            except Exception:
                try:
                    lease_path.unlink(missing_ok=True)
                except OSError:
                    logger.debug('HandsPlanner._claim_lease: OSError ignored', exc_info=True)
                raise
            return token
        except FileExistsError:
            logger.debug('HandsPlanner._claim_lease: FileExistsError ignored', exc_info=True)
            return None
        except OSError:
            logger.debug('HandsPlanner._claim_lease: OSError ignored', exc_info=True)
            return None

    def _lease_is_valid(self, plan_id: str, token: str) -> bool:
        try:
            lease = json.loads(self._lease_path(plan_id).read_text(encoding="utf-8"))
            return self._constant_time_equal(lease.get("token"), token) and float(lease.get("expiresAt", 0) or 0) > self._now()
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            logger.debug('HandsPlanner._lease_is_valid: OSError, json.JSONDecodeError, TypeError, ValueError ignored', exc_info=True)
            return False

    def _renew_lease(self, plan_id: str, token: str, ttl_seconds: int = 120) -> bool:
        lease_path = self._lease_path(plan_id)
        if not self._lease_is_valid(plan_id, token):
            return False
        try:
            lease = json.loads(lease_path.read_text(encoding="utf-8"))
            now = self._now()
            lease.update({"heartbeatAt": now, "expiresAt": now + ttl_seconds})
            temporary = lease_path.with_suffix(f".{uuid.uuid4().hex}.tmp")
            temporary.write_text(json.dumps(lease, ensure_ascii=True), encoding="utf-8")
            os.replace(temporary, lease_path)
            return True
        except (OSError, json.JSONDecodeError):
            logger.debug('HandsPlanner._renew_lease: OSError, json.JSONDecodeError ignored', exc_info=True)
            return False

    def _release_lease(self, plan_id: str, token: str) -> None:
        if not self._lease_is_valid(plan_id, token):
            return
        try:
            self._lease_path(plan_id).unlink(missing_ok=True)
        except OSError:
            logger.debug('HandsPlanner._release_lease: OSError ignored', exc_info=True)

    async def _lease_heartbeat(self, plan_id: str, token: str) -> None:
        while True:
            await asyncio.sleep(10)
            if not self._renew_lease(plan_id, token):
                return

    def _run_lease_valid(self, plan_id: str) -> bool:
        token = self._run_tokens.get(plan_id)
        return bool(token and self._lease_is_valid(plan_id, token))

    def _restore_journal_state(self) -> None:
        """Recover the last journal sequence/hash without trusting projection state."""
        if not self.plan_path.exists():
            return
        try:
            last_line = ""
            with self.plan_path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if line.strip():
                        last_line = line
            if last_line:
                record = json.loads(last_line)
                self._event_seq = int(record.get("eventSeq", 0) or 0)
                self._previous_event_hash = str(record.get("eventHash", "") or "")
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            logger.debug('HandsPlanner._restore_journal_state: OSError, TypeError, ValueError, json.JSONDecodeError ignored', exc_info=True)
            self._event_seq = 0
            self._previous_event_hash = ""

    def _record(self, event: str, plan: dict[str, Any], extra: dict[str, Any] | None = None) -> None:
        record = {
            "timestamp": self._now(),
            "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "event": event,
            "eventId": uuid.uuid4().hex,
            "planId": plan.get("planId"),
            "state": plan.get("state"),
            "goal": self._bounded_text(plan.get("goal"), 500),
            "stepCount": len(plan.get("steps", [])),
        }
        if extra:
            record.update(extra)
        with self._journal_lock:
            self._event_seq += 1
            record["eventSeq"] = self._event_seq
            record["previousEventHash"] = self._previous_event_hash
            canonical = json.dumps({"plan": plan, **record}, ensure_ascii=False, sort_keys=True, default=str)
            record["eventHash"] = hashlib.sha256(canonical.encode("utf-8", "replace")).hexdigest()
            journal_record = {"plan": plan, **record}
            with self.plan_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(journal_record, ensure_ascii=False, default=str) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._previous_event_hash = record["eventHash"]
        self.executor._audit(event, {key: value for key, value in record.items() if key != "event"})

    def _load_latest(self) -> dict[str, dict[str, Any]]:
        plans: dict[str, dict[str, Any]] = {}
        if not self.plan_path.exists():
            return plans
        for line in self.plan_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                logger.debug('HandsPlanner._load_latest: json.JSONDecodeError ignored', exc_info=True)
                continue
            plan = record.get("plan")
            if isinstance(plan, dict) and plan.get("planId"):
                plans[str(plan["planId"])] = plan
        return plans

    def _save(self, plan: dict[str, Any], event: str, extra: dict[str, Any] | None = None) -> None:
        plan["updatedAt"] = self._now()
        self._record(event, plan, extra)

    def _get(self, plan_id: str) -> dict[str, Any] | None:
        return self._load_latest().get(plan_id)

    def _public_plan(self, plan: dict[str, Any]) -> dict[str, Any]:
        return json.loads(json.dumps(plan, ensure_ascii=False, default=str))

    def _summary(self, plan: dict[str, Any]) -> dict[str, Any]:
        steps = plan.get("steps", [])
        return {
            "planId": plan.get("planId"),
            "goal": plan.get("goal", ""),
            "state": plan.get("state", "PLANNED"),
            "currentStepId": plan.get("currentStepId"),
            "stepCount": len(steps),
            "completedStepCount": sum(1 for step in steps if step.get("state") == "VERIFIED"),
            "failedStepCount": sum(1 for step in steps if step.get("state") == "FAILED"),
            "waitingApprovalCount": sum(1 for step in steps if step.get("state") == "WAITING_APPROVAL"),
            "updatedAt": plan.get("updatedAt"),
        }

    @staticmethod
    def _validate_condition(raw: Any, label: str, allowed_types: set[str]) -> dict[str, Any]:
        if raw in (None, {}):
            return {"type": "always"}
        if not isinstance(raw, dict):
            raise ValueError(f"{label} must be an object")
        condition = dict(raw)
        condition_type = str(condition.get("type", "always")).strip()
        if condition_type not in allowed_types:
            raise ValueError(f"{label} has unsupported type: {condition_type}")
        if condition_type == "previous_steps_verified":
            step_ids = condition.get("stepIds", [])
            if not isinstance(step_ids, list) or not step_ids or any(not str(item).strip() for item in step_ids):
                raise ValueError(f"{label} requires a non-empty stepIds list")
            condition["stepIds"] = [str(item).strip() for item in step_ids]
        elif condition_type in {"plan_state", "step_state"}:
            if not str(condition.get("equals", "")).strip():
                raise ValueError(f"{label} requires equals")
            if condition_type == "step_state" and not str(condition.get("stepId", "")).strip():
                raise ValueError(f"{label} requires stepId")
        elif condition_type in {"text_contains", "url_prefix", "field_equals", "evidence_field_equals"}:
            if not str(condition.get("value", "")).strip() and condition_type != "field_equals":
                raise ValueError(f"{label} requires value")
            if condition_type in {"field_equals", "evidence_field_equals"} and not str(condition.get("path", "")).strip():
                raise ValueError(f"{label} requires path")
        return condition

    @staticmethod
    def _validate_retry(raw: Any, label: str) -> dict[str, Any]:
        if raw in (None, {}):
            return {"maxAttempts": 1, "backoffSeconds": 0.0, "on": "verification_failed"}
        if not isinstance(raw, dict):
            raise ValueError(f"{label} must be an object")
        try:
            max_attempts = max(1, min(int(raw.get("maxAttempts", 1)), 3))
            backoff = max(0.0, min(float(raw.get("backoffSeconds", 0)), 5.0))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} has invalid numeric values") from exc
        retry_on = str(raw.get("on", raw.get("retryOn", "verification_failed"))).strip()
        if retry_on not in RETRY_TYPES:
            raise ValueError(f"{label} has unsupported on value: {retry_on}")
        return {"maxAttempts": max_attempts, "backoffSeconds": backoff, "on": retry_on}

    def _validate_step(self, raw: dict[str, Any], index: int, known_ids: set[str]) -> dict[str, Any]:
        action = str(raw.get("action", "")).strip()
        if len(action) < 3:
            raise ValueError(f"Step {index + 1} requires an action")
        definition = self.executor.registry.get(action)
        if definition is None:
            raise ValueError(f"Unknown Hands action: {action}")
        step_id = str(raw.get("stepId") or f"step-{index + 1:02d}").strip()
        if step_id in known_ids:
            raise ValueError(f"Duplicate stepId: {step_id}")
        depends_on = [str(item) for item in raw.get("dependsOn", [])]
        if any(item not in known_ids for item in depends_on):
            raise ValueError(f"Step {step_id} has a forward or unknown dependency")
        if step_id in depends_on:
            raise ValueError(f"Step {step_id} cannot depend on itself")
        params = raw.get("params", {})
        if not isinstance(params, dict):
            raise ValueError(f"Step {step_id} params must be an object")
        precondition = self._validate_condition(raw.get("precondition"), f"Step {step_id} precondition", PRECONDITION_TYPES)
        postcondition = self._validate_condition(raw.get("postcondition"), f"Step {step_id} postcondition", POSTCONDITION_TYPES)
        for dependency_id in precondition.get("stepIds", []):
            if dependency_id not in known_ids:
                raise ValueError(f"Step {step_id} precondition references unknown step: {dependency_id}")
        if precondition.get("type") == "step_state" and precondition.get("stepId") not in known_ids:
            raise ValueError(f"Step {step_id} precondition references unknown step: {precondition.get('stepId')}")
        retry_policy = self._validate_retry(raw.get("retryPolicy"), f"Step {step_id} retryPolicy")
        raw_capability = max(0, min(int(raw.get("capabilityLevel", definition.capability_level)), 5))
        raw_token = raw.get("capabilityToken") if raw.get("capabilityToken") is not None else raw.get("capability_token")
        if raw_token is not None:
            parsed_token = parse_capability_token(raw_token)
            step_token: Any = parsed_token.to_dict() if parsed_token is not None else raw_token
        else:
            step_token = None
        return {
            "stepId": step_id,
            "action": action,
            "params": params,
            "capabilityLevel": raw_capability,
            "capabilityToken": step_token,
            "approved": bool(raw.get("approved", False)),
            "dryRun": bool(raw.get("dryRun", False)),
            "dependsOn": depends_on,
            "precondition": precondition,
            "postcondition": postcondition,
            "retryPolicy": retry_policy,
            "state": "PLANNED",
            "attempts": 0,
            "evidence": {},
            "error": "",
        }

    @staticmethod
    def _read_path(source: Any, path: str) -> Any:
        current = source
        for part in [item for item in path.split(".") if item][:5]:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        return current

    def _evaluate_condition(self, condition: dict[str, Any], plan: dict[str, Any], result: dict[str, Any] | None = None) -> tuple[bool, str]:
        condition_type = str(condition.get("type", "always"))
        if condition_type == "always":
            return True, "always"
        if condition_type == "previous_steps_verified":
            states = {str(step.get("stepId")): step.get("state") for step in plan.get("steps", [])}
            missing = [step_id for step_id in condition.get("stepIds", []) if states.get(step_id) != "VERIFIED"]
            return (not missing, "all previous steps verified" if not missing else f"steps not verified: {', '.join(missing)}")
        if condition_type == "plan_state":
            expected = str(condition.get("equals"))
            return (plan.get("state") == expected, f"plan state is {plan.get('state')}, expected {expected}")
        if condition_type == "step_state":
            step_id = str(condition.get("stepId"))
            step = next((item for item in plan.get("steps", []) if str(item.get("stepId")) == step_id), None)
            expected = str(condition.get("equals"))
            actual = step.get("state") if step else None
            return (actual == expected, f"step {step_id} state is {actual}, expected {expected}")
        if result is None:
            return False, "postcondition needs an execution result"
        if condition_type == "verification_passed":
            actual = bool((result.get("verification") or {}).get("passed"))
            return actual, f"verification.passed={actual}"
        if condition_type == "success":
            actual = bool(result.get("success"))
            return actual, f"success={actual}"
        if condition_type == "text_contains":
            expected = str(condition.get("value", ""))
            text = str(result.get("text", ""))
            return expected in text, f"text contains expected={expected!r}"
        if condition_type == "url_prefix":
            expected = str(condition.get("value", ""))
            actual = str(result.get("url", ""))
            return actual.startswith(expected), f"url={actual!r}, prefix={expected!r}"
        if condition_type in {"field_equals", "evidence_field_equals"}:
            source = result.get("evidence", {}) if condition_type == "evidence_field_equals" else result
            actual = self._read_path(source, str(condition.get("path", "")))
            expected = condition.get("value")
            return actual == expected, f"{condition_type} {condition.get('path')}={actual!r}, expected={expected!r}"
        return False, f"unsupported condition type: {condition_type}"

    def journal_integrity(self) -> dict[str, Any]:
        checked = 0
        legacy = 0
        errors: list[str] = []
        previous_hash = ""
        expected_seq = 1
        if not self.plan_path.exists():
            return {"valid": True, "checked": 0, "legacy": 0, "errors": []}
        try:
            lines = self.plan_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            return {"valid": False, "checked": 0, "legacy": 0, "errors": [str(exc)]}
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                errors.append(f"line {line_number}: invalid JSON")
                continue
            if "eventHash" not in record or "eventSeq" not in record:
                legacy += 1
                continue
            checked += 1
            if int(record.get("eventSeq", -1)) != expected_seq:
                errors.append(f"line {line_number}: event sequence mismatch")
            if str(record.get("previousEventHash", "")) != previous_hash:
                errors.append(f"line {line_number}: previous hash mismatch")
            supplied_hash = str(record.get("eventHash", ""))
            canonical = {key: value for key, value in record.items() if key != "eventHash"}
            calculated_hash = hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8", "replace")).hexdigest()
            if supplied_hash != calculated_hash:
                errors.append(f"line {line_number}: event hash mismatch")
            previous_hash = supplied_hash
            expected_seq += 1
        return {"valid": not errors and legacy == 0, "checked": checked, "legacy": legacy, "errors": errors[:20]}
