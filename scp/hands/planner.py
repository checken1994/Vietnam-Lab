# SCP CIRCUIT: M04 — STATUS: CLOSED_WITH_KNOWN_GAP (closure: docs/evidence-summary/M04-closure.json)
"""SCP Hands v3.6.1 bounded planner and plan runner.

The planner is deterministic and explicit. It accepts only registered Hands
actions, evaluates a small allowlisted condition language, retries bounded
failures, records evidence, and requires approval before risky steps.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import threading
import time
import uuid
from typing import Any

from scp.core.capability_token import verify_token
from scp.security.capability_epoch import parse_capability_token

from .hands_executor import HandsExecutor
from .planner_helpers import (
    PLAN_STATES,
    PLAN_VERSION,
    POSTCONDITION_TYPES,
    PRECONDITION_TYPES,
    RECOVERY_DECISIONS,
    RETRY_TYPES,
    STEP_STATES,
    HandsPlannerHelpersMixin,
)

logger = logging.getLogger(__name__)


class HandsPlanner(HandsPlannerHelpersMixin):
    """Persisted, sequential Planner for the existing Hands Executor."""

    def __init__(self, executor: HandsExecutor | None = None, autonomous_governor: Any = None) -> None:
        self.executor = executor or HandsExecutor()
        self.data_dir = self.executor.data_dir
        self.plan_path = self.data_dir / "plans.jsonl"
        self.lease_dir = self.data_dir / "leases"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.lease_dir.mkdir(parents=True, exist_ok=True)
        self._journal_lock = threading.Lock()
        self._active_runs: set[str] = set()
        self._run_tokens: dict[str, str] = {}
        self._event_seq = 0
        self._previous_event_hash = ""
        self._restore_journal_state()
        if autonomous_governor is None:
            from scp.security.autonomous_governor import AutonomousCapabilityGovernor
            self.autonomous_governor = AutonomousCapabilityGovernor(getattr(self.executor, "capability_authority", None))
        else:
            self.autonomous_governor = autonomous_governor

    def create_plan(self, goal: str, steps: list[dict[str, Any]], metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        goal = self._bounded_text(goal, 1000).strip()
        if not goal:
            raise ValueError("Plan goal is required")
        if not isinstance(steps, list) or not steps or len(steps) > 20:
            raise ValueError("Plan must contain between 1 and 20 steps")
        normalized: list[dict[str, Any]] = []
        known_ids: set[str] = set()
        for index, raw in enumerate(steps):
            if not isinstance(raw, dict):
                raise ValueError(f"Step {index + 1} must be an object")
            step = self._validate_step(raw, index, known_ids)
            normalized.append(step)
            known_ids.add(step["stepId"])
        plan = {
            "planId": uuid.uuid4().hex,
            "version": PLAN_VERSION,
            "goal": goal,
            "state": "PLANNED",
            "createdAt": self._now(),
            "updatedAt": self._now(),
            "currentStepId": None,
            "completedStepCount": 0,
            "metadata": metadata if isinstance(metadata, dict) else {},
            "steps": normalized,
        }
        self._save(plan, "PLAN_CREATED")
        return self._public_plan(plan)

    def list_plans(self, limit: int = 20) -> list[dict[str, Any]]:
        plans = sorted(self._load_latest().values(), key=lambda item: float(item.get("updatedAt", 0)), reverse=True)
        return [self._public_plan(item) for item in plans[: max(1, min(limit, 100))]]

    def get_plan(self, plan_id: str) -> dict[str, Any] | None:
        plan = self._get(plan_id)
        return self._public_plan(plan) if plan else None

    async def run_plan(self, plan_id: str, capability_level: int = 0, approved: bool = False, dry_run: bool = False, stop_on_failure: bool = True, capability_token: Any = "") -> dict[str, Any]:
        with self._journal_lock:
            if plan_id in self._active_runs:
                return {"success": False, "planId": plan_id, "error": "Plan run already active", "safeToRetry": False}
            lease_token = self._claim_lease(plan_id)
            if not lease_token:
                return {"success": False, "planId": plan_id, "error": "Plan lease is held by another worker", "safeToRetry": False, "errorCode": "LEASE_HELD"}
            self._active_runs.add(plan_id)
            self._run_tokens[plan_id] = lease_token
        heartbeat = asyncio.create_task(self._lease_heartbeat(plan_id, lease_token))
        try:
            return await self._run_plan_locked(plan_id, capability_level, approved, dry_run, stop_on_failure, capability_token)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            with self._journal_lock:
                self._active_runs.discard(plan_id)
                self._run_tokens.pop(plan_id, None)
                self._release_lease(plan_id, lease_token)

    async def _run_plan_locked(self, plan_id: str, capability_level: int = 0, approved: bool = False, dry_run: bool = False, stop_on_failure: bool = True, capability_token: Any = "") -> dict[str, Any]:
        plan = self._get(plan_id)
        if not plan:
            return {"success": False, "error": "Plan not found", "planId": plan_id}
        if plan.get("state") in {"COMPLETED", "ROLLED_BACK"}:
            return {"success": False, "error": f"Plan is already {plan.get('state')}", "plan": self._public_plan(plan)}
        if plan.get("state") in {"UNKNOWN", "HUMAN_REVIEW"}:
            return {"success": False, "error": "Plan requires recovery decision before another side effect", "requiresRecovery": True, "safeToRetry": False, "plan": self._public_plan(plan)}
        if plan.get("state") == "RUNNING":
            plan["state"] = "HUMAN_REVIEW"
            self._save(plan, "PLAN_STALE_RUN_DETECTED", {"safeToRetry": False})
            return {"success": False, "error": "A previous run may have stopped mid-side-effect; reconciliation is required", "requiresRecovery": True, "safeToRetry": False, "plan": self._public_plan(plan)}
        plan["state"] = "RUNNING"
        self._save(plan, "PLAN_STARTED")
        last_result: dict[str, Any] = {}
        had_failure = False
        for step in plan.get("steps", []):
            if step.get("state") == "VERIFIED":
                continue
            plan["currentStepId"] = step["stepId"]
            precondition_ok, precondition_message = self._evaluate_condition(step.get("precondition", {"type": "always"}), plan)
            if not precondition_ok:
                step["state"] = "FAILED"
                step["error"] = f"Precondition failed: {precondition_message}"
                step["evidence"] = {"preconditionPassed": False, "preconditionMessage": precondition_message}
                plan["state"] = "FAILED"
                self._save(plan, "PLAN_STEP_PRECONDITION_FAILED", {"stepId": step["stepId"], "action": step["action"], "error": step["error"]})
                if stop_on_failure:
                    return {"success": False, "plan": self._public_plan(plan), "stepId": step["stepId"], "error": step["error"]}
                had_failure = True
                continue
            definition = self.executor.registry.require(step["action"])

            autonomous_mode = str(os.environ.get("SCP_AUTONOMOUS_MODE", "")).lower() in ("1", "true", "yes")
            if autonomous_mode and getattr(self, "autonomous_governor", None):
                granted, auth_token, reason = self.autonomous_governor.evaluate_and_grant_step(
                    step, plan, str(self.executor.data_dir.resolve())
                )
                if granted and auth_token:
                    step["capabilityToken"] = auth_token.to_dict()
                    step["_governor_granted"] = True
                    self._attach_governor_grant(step, plan)
                    self._save(plan, "PLAN_STEP_AUTONOMOUS_GRANT", {"stepId": step["stepId"], "reason": reason})
                else:
                    self._save(plan, "PLAN_STEP_AUTONOMOUS_DENY", {"stepId": step["stepId"], "reason": reason})

            step_token = (
                step.get("capabilityToken")
                or step.get("capability_token")
                or (capability_token.get(step["stepId"]) if isinstance(capability_token, dict) else None)
                or (capability_token.get(step["action"]) if isinstance(capability_token, dict) else None)
                or capability_token
            )
            parsed_step_token = parse_capability_token(step_token)

            token_is_valid = (
                parsed_step_token is not None
                or (isinstance(step_token, str) and "." in step_token and verify_token(step_token).get("valid", False))
            )
            requested_capability = max(int(capability_level), int(step.get("capabilityLevel", 0))) if token_is_valid else min(int(capability_level), int(step.get("capabilityLevel", 0)))

            from scp.security.confirmation_store import get_confirmation_store
            conf_store = get_confirmation_store()
            has_human_confirmation = conf_store.is_confirmed(
                action=step.get("action", ""),
                target=json.dumps(step.get("params", {}), sort_keys=True, default=str),
                confirmation_id=step.get("confirmationId") or step.get("confirmation_id"),
                consume=True,
            )
            governor_granted = bool(autonomous_mode and step.get("_governor_granted"))
            request_approved = bool(approved or has_human_confirmation or governor_granted)
            if requested_capability < definition.capability_level or (definition.requires_approval and not request_approved):
                if autonomous_mode:
                    step["state"] = "FAILED"
                    step["error"] = "Autonomous governor denied step execution: Capability or approval lacking"
                    plan["state"] = "FAILED"
                    self._save(plan, "PLAN_STEP_FAILED", {"stepId": step["stepId"], "action": step["action"], "error": step["error"]})
                    if stop_on_failure:
                        return {"success": False, "plan": self._public_plan(plan), "stepId": step["stepId"], "error": step["error"]}
                    had_failure = True
                    continue
                else:
                    step["state"] = "WAITING_APPROVAL"
                    step["error"] = "Explicit approval or higher capability is required"
                    plan["state"] = "WAITING_APPROVAL"
                    self._save(plan, "PLAN_STEP_WAITING_APPROVAL", {"stepId": step["stepId"], "action": step["action"]})
                    return {"success": False, "waitingApproval": True, "plan": self._public_plan(plan), "stepId": step["stepId"], "error": step["error"]}
            retry_policy = step.get("retryPolicy", {"maxAttempts": 1, "backoffSeconds": 0.0, "on": "verification_failed"})
            max_attempts = max(1, min(int(retry_policy.get("maxAttempts", 1)), 3))
            retry_on = str(retry_policy.get("on", "verification_failed"))
            while int(step.get("attempts", 0)) < max_attempts:
                if not self._run_lease_valid(plan_id):
                    step["state"] = "UNKNOWN"
                    step["error"] = "Lease lost before tool execution"
                    plan["state"] = "UNKNOWN"
                    self._save(plan, "PLAN_LEASE_LOST", {"stepId": step["stepId"], "safeToRetry": False})
                    return {"success": False, "requiresRecovery": True, "safeToRetry": False, "errorCode": "STALE_LEASE", "plan": self._public_plan(plan), "stepId": step["stepId"]}
                step["state"] = "RUNNING"
                step["attempts"] = int(step.get("attempts", 0)) + 1
                self._save(plan, "PLAN_STEP_STARTED", {"stepId": step["stepId"], "action": step["action"], "attempt": step["attempts"], "maxAttempts": max_attempts})
                try:
                    last_result = await self.executor.execute(
                        step["action"],
                        step.get("params", {}),
                        requested_capability,
                        request_approved,
                        dry_run or bool(step.get("dryRun", False)),
                        capability_token=parsed_step_token,
                    )
                except Exception as exc:
                    logger.warning("Planner step execution failed (plan %s): %s", plan_id, exc, exc_info=True)
                    last_result = {"success": False, "error": f"Planner executor error: {exc}", "verification": {"passed": False}}
                if not self._run_lease_valid(plan_id):
                    step["state"] = "UNKNOWN"
                    step["error"] = "Lease lost after tool execution; result requires reconciliation"
                    plan["state"] = "UNKNOWN"
                    self._save(plan, "PLAN_LEASE_LOST_AFTER_TOOL", {"stepId": step["stepId"], "safeToRetry": False})
                    return {"success": False, "requiresRecovery": True, "safeToRetry": False, "errorCode": "STALE_LEASE", "plan": self._public_plan(plan), "stepId": step["stepId"], "result": last_result}
                verification = last_result.get("verification") if isinstance(last_result.get("verification"), dict) else {}
                verification_passed = bool(verification.get("passed"))
                postcondition_ok, postcondition_message = self._evaluate_condition(step.get("postcondition", {"type": "verification_passed"}), plan, last_result)
                passed = bool(last_result.get("success")) and verification_passed and postcondition_ok
                step["evidence"] = {
                    "success": bool(last_result.get("success")),
                    "verificationPassed": verification_passed,
                    "postconditionPassed": postcondition_ok,
                    "postconditionMessage": postcondition_message,
                    "verificationRule": self._bounded_text(verification.get("rule"), 200),
                    "evidence": last_result.get("evidence", {}),
                    "url": self._bounded_text(last_result.get("url"), 500),
                    "path": self._bounded_text(last_result.get("path"), 500),
                    "durationMs": last_result.get("durationMs"),
                }
                step["checkpointId"] = last_result.get("checkpointId")
                step["error"] = self._bounded_text(last_result.get("error"), 1000)
                if passed:
                    step["state"] = "VERIFIED"
                    step["error"] = ""
                    self._save(plan, "PLAN_STEP_VERIFIED", {"stepId": step["stepId"], "action": step["action"], "attempt": step["attempts"]})
                    break
                failure_kind = "execution_error" if step["error"] else "verification_failed"
                definition_mutates = bool(definition.mutates_state)
                if definition_mutates:
                    step["state"] = "HUMAN_REVIEW"
                    step["recovery"] = {
                        "decision": "RECONCILE",
                        "reason": "MUTATING_ACTION_RESULT_NOT_VERIFIED",
                        "safeToRetry": False,
                        "requiredEvidence": ["postcondition", "provider_or_driver_state"],
                        "failureKind": failure_kind,
                    }
                    step["error"] = "Mutating action was not verified; automatic retry is blocked"
                    plan["state"] = "HUMAN_REVIEW"
                    self._save(plan, "PLAN_STEP_RECONCILE_REQUIRED", {"stepId": step["stepId"], "action": step["action"], "safeToRetry": False, "failureKind": failure_kind})
                    return {"success": False, "requiresRecovery": True, "safeToRetry": False, "plan": self._public_plan(plan), "stepId": step["stepId"], "result": last_result}
                can_retry = int(step.get("attempts", 0)) < max_attempts and retry_on in {failure_kind, "always"}
                if can_retry:
                    step["state"] = "RUNNING"
                    self._save(plan, "PLAN_STEP_RETRY", {"stepId": step["stepId"], "action": step["action"], "attempt": step["attempts"], "nextAttempt": int(step["attempts"]) + 1, "reason": failure_kind})
                    backoff = max(0.0, min(float(retry_policy.get("backoffSeconds", 0)), 5.0))
                    if backoff:
                        await asyncio.sleep(backoff)
                    continue
                step["state"] = "FAILED"
                plan["state"] = "FAILED"
                had_failure = True
                self._save(plan, "PLAN_STEP_FAILED", {"stepId": step["stepId"], "action": step["action"], "error": step["error"], "attempts": step["attempts"]})
                break
            if step.get("state") == "FAILED" and stop_on_failure:
                return {"success": False, "plan": self._public_plan(plan), "stepId": step["stepId"], "result": last_result}
        if had_failure:
            plan["state"] = "FAILED"
            self._save(plan, "PLAN_FAILED")
            return {"success": False, "plan": self._public_plan(plan), "result": last_result}
        plan["state"] = "COMPLETED"
        plan["currentStepId"] = None
        plan["completedStepCount"] = sum(1 for step in plan.get("steps", []) if step.get("state") == "VERIFIED")
        self._save(plan, "PLAN_COMPLETED")
        return {"success": True, "plan": self._public_plan(plan), "result": last_result}

    async def _run_dag_step(self, plan: dict[str, Any], step: dict[str, Any], capability_level: int, approved: bool, dry_run: bool, capability_token: Any = "") -> dict[str, Any]:
        """Execute one ready DAG node with the same evidence/approval contract."""
        definition = self.executor.registry.require(step["action"])

        autonomous_mode = str(os.environ.get("SCP_AUTONOMOUS_MODE", "")).lower() in ("1", "true", "yes")
        if autonomous_mode and getattr(self, "autonomous_governor", None):
            granted, auth_token, reason = self.autonomous_governor.evaluate_and_grant_step(
                step, plan, str(self.executor.data_dir.resolve())
            )
            if granted and auth_token:
                step["capabilityToken"] = auth_token.to_dict()
                step["_governor_granted"] = True
                self._attach_governor_grant(step, plan)
                self._save(plan, "PLAN_STEP_AUTONOMOUS_GRANT", {"stepId": step["stepId"], "reason": reason, "scheduler": "dag"})
            else:
                self._save(plan, "PLAN_STEP_AUTONOMOUS_DENY", {"stepId": step["stepId"], "reason": reason, "scheduler": "dag"})

        step_token = (
            step.get("capabilityToken")
            or step.get("capability_token")
            or (capability_token.get(step["stepId"]) if isinstance(capability_token, dict) else None)
            or (capability_token.get(step["action"]) if isinstance(capability_token, dict) else None)
            or capability_token
        )
        parsed_step_token = parse_capability_token(step_token)

        token_is_valid = (
            parsed_step_token is not None
            or (isinstance(step_token, str) and "." in step_token and verify_token(step_token).get("valid", False))
        )
        requested_capability = max(int(capability_level), int(step.get("capabilityLevel", 0))) if token_is_valid else min(int(capability_level), int(step.get("capabilityLevel", 0)))
        from scp.security.confirmation_store import get_confirmation_store
        conf_store = get_confirmation_store()
        has_human_confirmation = conf_store.is_confirmed(
            action=step.get("action", ""),
            target=json.dumps(step.get("params", {}), sort_keys=True, default=str),
            confirmation_id=step.get("confirmationId") or step.get("confirmation_id"),
            consume=True,
        )
        governor_granted = bool(autonomous_mode and step.get("_governor_granted"))
        request_approved = bool(approved or has_human_confirmation or governor_granted)
        if requested_capability < definition.capability_level or (definition.requires_approval and not request_approved):
            if autonomous_mode:
                step["state"] = "FAILED"
                step["error"] = "Autonomous governor denied step execution: Capability or approval lacking"
                self._save(plan, "PLAN_STEP_FAILED", {"stepId": step["stepId"], "action": step["action"], "error": step["error"], "scheduler": "dag"})
                return {"success": False, "stepId": step["stepId"], "error": step["error"]}
            else:
                step["state"] = "WAITING_APPROVAL"
                step["error"] = "Explicit approval or higher capability is required"
                self._save(plan, "PLAN_STEP_WAITING_APPROVAL", {"stepId": step["stepId"], "action": step["action"], "scheduler": "dag"})
                return {"success": False, "waitingApproval": True, "stepId": step["stepId"], "error": step["error"]}
        retry_policy = step.get("retryPolicy", {"maxAttempts": 1, "backoffSeconds": 0.0, "on": "verification_failed"})
        max_attempts = max(1, min(int(retry_policy.get("maxAttempts", 1)), 3))
        retry_on = str(retry_policy.get("on", "verification_failed"))
        last_result: dict[str, Any] = {}
        while int(step.get("attempts", 0)) < max_attempts:
            if not self._run_lease_valid(str(plan.get("planId"))):
                step["state"] = "UNKNOWN"
                step["error"] = "Lease lost before tool execution"
                return {"success": False, "requiresRecovery": True, "safeToRetry": False, "errorCode": "STALE_LEASE", "stepId": step["stepId"]}
            step["state"] = "RUNNING"
            step["attempts"] = int(step.get("attempts", 0)) + 1
            self._save(plan, "PLAN_STEP_STARTED", {"stepId": step["stepId"], "action": step["action"], "attempt": step["attempts"], "maxAttempts": max_attempts, "scheduler": "dag"})
            try:
                last_result = await self.executor.execute(
                    step["action"],
                    step.get("params", {}),
                    requested_capability,
                    request_approved,
                    dry_run or bool(step.get("dryRun", False)),
                    capability_token=parsed_step_token,
                )
            except Exception as exc:
                logger.warning("Planner step execution failed (plan %s): %s", plan.get("planId"), exc, exc_info=True)
                last_result = {"success": False, "error": f"Planner executor error: {exc}", "verification": {"passed": False}}
            if not self._run_lease_valid(str(plan.get("planId"))):
                step["state"] = "UNKNOWN"
                step["error"] = "Lease lost after tool execution; result requires reconciliation"
                return {"success": False, "requiresRecovery": True, "safeToRetry": False, "errorCode": "STALE_LEASE", "stepId": step["stepId"], "result": last_result}
            verification = last_result.get("verification") if isinstance(last_result.get("verification"), dict) else {}
            postcondition_ok, postcondition_message = self._evaluate_condition(step.get("postcondition", {"type": "verification_passed"}), plan, last_result)
            passed = bool(last_result.get("success")) and bool(verification.get("passed")) and postcondition_ok
            step["evidence"] = {
                "success": bool(last_result.get("success")),
                "verificationPassed": bool(verification.get("passed")),
                "postconditionPassed": postcondition_ok,
                "postconditionMessage": postcondition_message,
                "verificationRule": self._bounded_text(verification.get("rule"), 200),
                "evidence": last_result.get("evidence", {}),
                "url": self._bounded_text(last_result.get("url"), 500),
                "path": self._bounded_text(last_result.get("path"), 500),
                "durationMs": last_result.get("durationMs"),
            }
            step["checkpointId"] = last_result.get("checkpointId")
            step["error"] = self._bounded_text(last_result.get("error"), 1000)
            if passed:
                step["state"] = "VERIFIED"
                step["error"] = ""
                self._save(plan, "PLAN_STEP_VERIFIED", {"stepId": step["stepId"], "action": step["action"], "attempt": step["attempts"], "scheduler": "dag"})
                return {"success": True, "stepId": step["stepId"], "result": last_result}
            failure_kind = "execution_error" if step["error"] else "verification_failed"
            if definition.mutates_state:
                step["state"] = "HUMAN_REVIEW"
                step["recovery"] = {
                    "decision": "RECONCILE",
                    "reason": "MUTATING_ACTION_RESULT_NOT_VERIFIED",
                    "safeToRetry": False,
                    "requiredEvidence": ["postcondition", "provider_or_driver_state"],
                    "failureKind": failure_kind,
                }
                step["error"] = "Mutating action was not verified; automatic retry is blocked"
                self._save(plan, "PLAN_STEP_RECONCILE_REQUIRED", {"stepId": step["stepId"], "action": step["action"], "scheduler": "dag", "safeToRetry": False, "failureKind": failure_kind})
                return {"success": False, "requiresRecovery": True, "safeToRetry": False, "stepId": step["stepId"], "result": last_result}
            can_retry = int(step.get("attempts", 0)) < max_attempts and retry_on in {failure_kind, "always"}
            if can_retry:
                self._save(plan, "PLAN_STEP_RETRY", {"stepId": step["stepId"], "action": step["action"], "attempt": step["attempts"], "nextAttempt": int(step["attempts"]) + 1, "reason": failure_kind, "scheduler": "dag"})
                backoff = max(0.0, min(float(retry_policy.get("backoffSeconds", 0)), 5.0))
                if backoff:
                    await asyncio.sleep(backoff)
                continue
            step["state"] = "FAILED"
            self._save(plan, "PLAN_STEP_FAILED", {"stepId": step["stepId"], "action": step["action"], "error": step["error"], "attempts": step["attempts"], "scheduler": "dag"})
            return {"success": False, "stepId": step["stepId"], "result": last_result, "error": step["error"]}
        return {"success": False, "stepId": step["stepId"], "error": "Retry budget exhausted"}

    async def run_dag(self, plan_id: str, capability_level: int = 0, approved: bool = False, dry_run: bool = False, max_parallel: int = 2, stop_on_failure: bool = True, capability_token: Any = "") -> dict[str, Any]:
        with self._journal_lock:
            if plan_id in self._active_runs:
                return {"success": False, "planId": plan_id, "error": "Plan run already active", "safeToRetry": False}
            lease_token = self._claim_lease(plan_id)
            if not lease_token:
                return {"success": False, "planId": plan_id, "error": "Plan lease is held by another worker", "safeToRetry": False, "errorCode": "LEASE_HELD"}
            self._active_runs.add(plan_id)
            self._run_tokens[plan_id] = lease_token
        heartbeat = asyncio.create_task(self._lease_heartbeat(plan_id, lease_token))
        try:
            return await self._run_dag_locked(plan_id, capability_level, approved, dry_run, max_parallel, stop_on_failure, capability_token)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            with self._journal_lock:
                self._active_runs.discard(plan_id)
                self._run_tokens.pop(plan_id, None)
                self._release_lease(plan_id, lease_token)

    async def _run_dag_locked(self, plan_id: str, capability_level: int = 0, approved: bool = False, dry_run: bool = False, max_parallel: int = 2, stop_on_failure: bool = True, capability_token: Any = "") -> dict[str, Any]:
        """Run topologically ready steps concurrently with bounded parallelism."""
        plan = self._get(plan_id)
        if not plan:
            return {"success": False, "error": "Plan not found", "planId": plan_id}
        if plan.get("state") in {"COMPLETED", "ROLLED_BACK"}:
            return {"success": False, "error": f"Plan is already {plan.get('state')}", "plan": self._public_plan(plan)}
        if plan.get("state") in {"UNKNOWN", "HUMAN_REVIEW"}:
            return {"success": False, "error": "Plan requires recovery decision before another side effect", "requiresRecovery": True, "safeToRetry": False, "plan": self._public_plan(plan)}
        if plan.get("state") == "RUNNING":
            plan["state"] = "HUMAN_REVIEW"
            self._save(plan, "PLAN_STALE_RUN_DETECTED", {"scheduler": "dag", "safeToRetry": False})
            return {"success": False, "error": "A previous DAG run may have stopped mid-side-effect; reconciliation is required", "requiresRecovery": True, "safeToRetry": False, "plan": self._public_plan(plan)}
        max_parallel = max(1, min(int(max_parallel), 4))
        step_map = {str(step.get("stepId")): step for step in plan.get("steps", [])}
        pending = {step_id for step_id, step in step_map.items() if step.get("state") != "VERIFIED"}
        completed = {step_id for step_id, step in step_map.items() if step.get("state") == "VERIFIED"}
        plan["state"] = "RUNNING"
        plan["scheduler"] = "dag"
        plan["maxParallel"] = max_parallel
        self._save(plan, "PLAN_DAG_STARTED", {"maxParallel": max_parallel})
        running: dict[str, asyncio.Task] = {}
        last_results: dict[str, Any] = {}
        while pending or running:
            ready: list[str] = []
            for step_id in sorted(pending):
                step = step_map[step_id]
                dependencies = {str(item) for item in step.get("dependsOn", [])}
                if dependencies.issubset(completed):
                    precondition_ok, precondition_message = self._evaluate_condition(step.get("precondition", {"type": "always"}), plan)
                    if not precondition_ok:
                        step["state"] = "FAILED"
                        step["error"] = f"Precondition failed: {precondition_message}"
                        step["evidence"] = {"preconditionPassed": False, "preconditionMessage": precondition_message}
                        self._save(plan, "PLAN_STEP_PRECONDITION_FAILED", {"stepId": step_id, "action": step["action"], "scheduler": "dag"})
                        if stop_on_failure:
                            plan["state"] = "FAILED"
                            self._save(plan, "PLAN_FAILED", {"scheduler": "dag", "reason": "precondition"})
                            return {"success": False, "plan": self._public_plan(plan), "stepId": step_id, "error": step["error"]}
                        pending.remove(step_id)
                        continue
                    definition = self.executor.registry.require(step["action"])
                    step_token = (
                        step.get("capabilityToken")
                        or step.get("capability_token")
                        or (capability_token.get(step_id) if isinstance(capability_token, dict) else None)
                        or (capability_token.get(step["action"]) if isinstance(capability_token, dict) else None)
                        or capability_token
                    )
                    parsed_step_token = parse_capability_token(step_token)
                    token_is_valid = (
                        parsed_step_token is not None
                        or (isinstance(step_token, str) and "." in step_token and verify_token(step_token).get("valid", False))
                    )
                    requested_capability = max(int(capability_level), int(step.get("capabilityLevel", 0))) if token_is_valid else min(int(capability_level), int(step.get("capabilityLevel", 0)))
                    autonomous_mode = str(os.environ.get("SCP_AUTONOMOUS_MODE", "")).lower() in ("1", "true", "yes")
                    from scp.security.confirmation_store import get_confirmation_store
                    conf_store = get_confirmation_store()
                    has_human_confirmation = conf_store.is_confirmed(
                        action=step.get("action", ""),
                        target=json.dumps(step.get("params", {}), sort_keys=True, default=str),
                        confirmation_id=step.get("confirmationId") or step.get("confirmation_id"),
                        consume=False,
                    )
                    governor_granted = bool(autonomous_mode and step.get("_governor_granted"))
                    request_approved = bool(approved or has_human_confirmation or governor_granted)
                    governor_pending = bool(
                        autonomous_mode
                        and getattr(self, "autonomous_governor", None)
                        and definition.requires_approval
                        and not request_approved
                    )
                    if requested_capability < definition.capability_level or (definition.requires_approval and not request_approved and not governor_pending):
                        if autonomous_mode:
                            step["state"] = "FAILED"
                            step["error"] = "Autonomous governor denied step execution: Capability or approval lacking"
                            plan["state"] = "FAILED"
                            self._save(plan, "PLAN_STEP_FAILED", {"stepId": step_id, "action": step["action"], "error": step["error"], "scheduler": "dag"})
                            if stop_on_failure:
                                return {"success": False, "plan": self._public_plan(plan), "stepId": step_id, "error": step["error"]}
                            pending.remove(step_id)
                            continue
                        else:
                            step["state"] = "WAITING_APPROVAL"
                            step["error"] = "Explicit approval or higher capability is required"
                            plan["state"] = "WAITING_APPROVAL"
                            self._save(plan, "PLAN_STEP_WAITING_APPROVAL", {"stepId": step_id, "action": step["action"], "scheduler": "dag"})
                            return {"success": False, "waitingApproval": True, "plan": self._public_plan(plan), "stepId": step_id, "error": step["error"]}
                    ready.append(step_id)
            slots = max_parallel - len(running)
            for step_id in ready[: max(0, slots)]:
                pending.remove(step_id)
                step = step_map[step_id]
                running[step_id] = asyncio.create_task(self._run_dag_step(plan, step, capability_level, approved, dry_run, capability_token=capability_token))
            if not running:
                unresolved = sorted(pending)
                plan["state"] = "FAILED"
                self._save(plan, "PLAN_FAILED", {"scheduler": "dag", "reason": "unresolved_dependencies", "steps": unresolved})
                return {"success": False, "plan": self._public_plan(plan), "error": f"Unresolved DAG dependencies: {', '.join(unresolved)}"}
            done, _ = await asyncio.wait(list(running.values()), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                result = task.result()
                step_id = str(result.get("stepId"))
                running.pop(step_id, None)
                last_results[step_id] = result
                if result.get("success") is True:
                    completed.add(step_id)
                elif stop_on_failure:
                    for other in running.values():
                        other.cancel()
                    if running:
                        await asyncio.gather(*running.values(), return_exceptions=True)
                    plan["state"] = "HUMAN_REVIEW" if result.get("requiresRecovery") else "FAILED"
                    self._save(plan, "PLAN_RECONCILE_REQUIRED" if result.get("requiresRecovery") else "PLAN_FAILED", {"scheduler": "dag", "reason": "step_failure", "stepId": step_id, "safeToRetry": bool(result.get("safeToRetry", False))})
                    return {"success": False, "plan": self._public_plan(plan), "stepId": step_id, "result": result, "requiresRecovery": bool(result.get("requiresRecovery")), "safeToRetry": bool(result.get("safeToRetry", False))}
        plan["state"] = "COMPLETED"
        plan["currentStepId"] = None
        plan["completedStepCount"] = sum(1 for step in plan.get("steps", []) if step.get("state") == "VERIFIED")
        self._save(plan, "PLAN_DAG_COMPLETED", {"maxParallel": max_parallel, "parallelSteps": max_parallel > 1})
        return {"success": True, "plan": self._public_plan(plan), "results": last_results}

    def recover_plan(self, plan_id: str, decision: str, evidence_ref: str, approved: bool = False) -> dict[str, Any]:
        """Reconcile an interrupted/uncertain plan before any new side effect."""
        plan = self._get(plan_id)
        if not plan:
            return {"success": False, "error": "Plan not found", "planId": plan_id}
        decision = str(decision or "").strip().upper()
        evidence_ref = str(evidence_ref or "").strip()[:512]
        if decision not in RECOVERY_DECISIONS:
            return {"success": False, "error": f"Unsupported recovery decision: {decision}", "allowedDecisions": sorted(RECOVERY_DECISIONS)}
        if not approved:
            return {"success": False, "error": "Recovery requires explicit approval", "safeToRetry": False}
        if plan.get("state") not in {"UNKNOWN", "HUMAN_REVIEW"}:
            return {"success": False, "error": "Plan is not waiting for recovery", "state": plan.get("state"), "safeToRetry": False}
        if not evidence_ref:
            return {"success": False, "error": "Evidence reference is required", "safeToRetry": False}
        evidence_hash = hashlib.sha256(evidence_ref.encode("utf-8", "replace")).hexdigest()
        affected = [step for step in plan.get("steps", []) if step.get("state") in {"UNKNOWN", "HUMAN_REVIEW"}]
        if not affected:
            return {"success": False, "error": "No step is waiting for recovery", "safeToRetry": False}
        recovery = {
            "decision": decision,
            "evidenceRefSha256": evidence_hash,
            "evidenceProvided": True,
            "safeToRetry": decision == "NOT_APPLIED",
            "recordedAt": self._now(),
        }
        plan["recovery"] = recovery
        for step in affected:
            step["recovery"] = recovery
            if decision == "NOT_APPLIED":
                step["state"] = "PLANNED"
                step["attempts"] = 0
                step["error"] = ""
            else:
                step["state"] = "HUMAN_REVIEW"
        if decision == "NOT_APPLIED":
            plan["state"] = "PLANNED"
            plan["currentStepId"] = None
            self._save(plan, "PLAN_RECOVERY_RESUMED", {"decision": decision, "evidenceRefSha256": evidence_hash, "safeToRetry": True})
            return {"success": True, "plan": self._public_plan(plan), "decision": decision, "safeToRetry": True}
        plan["state"] = "HUMAN_REVIEW"
        self._save(plan, "PLAN_RECOVERY_RECORDED", {"decision": decision, "evidenceRefSha256": evidence_hash, "safeToRetry": False})
        return {"success": True, "plan": self._public_plan(plan), "decision": decision, "safeToRetry": False, "requiresHumanReview": True}

    async def rollback_plan(self, plan_id: str, capability_level: int = 3, approved: bool = False, capability_token: Any = None) -> dict[str, Any]:
        plan = self._get(plan_id)
        if not plan:
            return {"success": False, "error": "Plan not found", "planId": plan_id}
        checkpoints = [step.get("checkpointId") for step in reversed(plan.get("steps", [])) if step.get("checkpointId")]
        if not checkpoints:
            return {"success": False, "error": "Plan has no reversible checkpoints", "plan": self._public_plan(plan)}
        rollback_results: list[dict[str, Any]] = []
        parsed_token = parse_capability_token(capability_token)
        for checkpoint_id in checkpoints:
            result = await self.executor.rollback(str(checkpoint_id), capability_level, approved, capability_token=parsed_token)
            rollback_results.append(result)
            if not result.get("success"):
                self._save(plan, "PLAN_ROLLBACK_FAILED", {"checkpointId": checkpoint_id, "error": result.get("error", "")})
                return {"success": False, "plan": self._public_plan(plan), "rollbackResults": rollback_results}
        for step in plan.get("steps", []):
            if step.get("checkpointId"):
                step["state"] = "ROLLED_BACK"
        plan["state"] = "ROLLED_BACK"
        plan["currentStepId"] = None
        self._save(plan, "PLAN_ROLLED_BACK", {"checkpointCount": len(rollback_results)})
        return {"success": True, "plan": self._public_plan(plan), "rollbackResults": rollback_results}

    def status(self) -> dict[str, Any]:
        plans = self.list_plans(20)
        active = next((item for item in plans if item.get("state") in {"PLANNED", "RUNNING", "WAITING_APPROVAL", "UNKNOWN", "HUMAN_REVIEW"}), None)
        return {
            "version": PLAN_VERSION,
            "planner": "online",
            "planCount": len(plans),
            "journal": self.journal_integrity(),
            "activePlan": active,
            "states": {state: sum(1 for plan in plans if plan.get("state") == state) for state in sorted(PLAN_STATES)},
        }


__all__ = [
    "PLAN_VERSION",
    "PLAN_STATES",
    "STEP_STATES",
    "RECOVERY_DECISIONS",
    "PRECONDITION_TYPES",
    "POSTCONDITION_TYPES",
    "RETRY_TYPES",
    "HandsExecutor",
    "HandsPlannerHelpersMixin",
    "HandsPlanner",
    "verify_token",
    "parse_capability_token",
    "time",
]
