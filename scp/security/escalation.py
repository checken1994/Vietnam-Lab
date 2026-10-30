import json
import logging
import os
import secrets
import threading
from collections import deque
from pathlib import Path

from scp.security.circuit_breaker import CircuitBreaker as DosCircuitBreaker  # noqa: F401
from scp.security.deadline_scheduler import BoundedDeadlineScheduler
from scp.security.playbooks import FORBIDDEN_ACTIONS, PlaybookRegistry  # noqa: F401

# Security playbooks and the RPS DoS breaker are intentionally imported here so
# all escalation callers reach one visible guardrail namespace.

logger = logging.getLogger("scp.security.escalation")

# Create a lock for thread safety
lock = threading.Lock()

# Define the default defensive playbook
DEFAULT_DEFENSIVE_PLAYBOOK = {
    "block_ip": True,
    "tighten_rate_limit": True,
    "enable_honeypot": True,
    "forensic_logging": True,
    "alert_cert": True
}

class _PendingDeadline:
    """Handle returned by the ``_timers`` compat view — cancel only, no thread."""

    __slots__ = ("_key", "_scheduler")

    def __init__(self, scheduler: BoundedDeadlineScheduler, key: str) -> None:
        self._scheduler = scheduler
        self._key = key

    def cancel(self) -> None:
        self._scheduler.cancel(self._key)


class EscalationManager:
    # [SCP-DNA-FIX R5-3] Thresholds for auto-arming Dead Man's Switch.
    # When on_threat_detected sees severity in this set, start_countdown is
    # automatically called — previously start_countdown had 0 callers so the
    # Dead Man's Switch was inert (it logged threats but never armed the
    # countdown that would trigger execute_defensive_playbook on timeout).
    AUTO_ARM_SEVERITIES = {"high", "critical"}
    DEFAULT_TIMEOUT_MIN = 30
    # [esc-auto slice 1] Trần cứng cho state/scheduler (giết DoS thread/state).
    # Một attacker gửi N threat khác nhau giờ chỉ làm safe outcome của entry cũ
    # nhất chạy SỚM hơn (fire-early eviction) — không thread mới, không state phình.
    MAX_ACTIVE = 256
    HISTORY_CAP = 1000

    def __init__(
        self,
        data_dir: str = "data",
        *,
        max_active: int | None = None,
        history_cap: int | None = None,
        timeout_seconds: float | None = None,
    ):
        self.escalation_log_path = Path(data_dir) / "escalation_log.jsonl"
        self.escalation_state_path = Path(data_dir) / "escalation_state.json"
        self.escalation_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_active = int(max_active or self.MAX_ACTIVE)
        self.history_cap = int(history_cap or self.HISTORY_CAP)
        # Override chỉ dùng cho test/probe; production dùng DEFAULT_TIMEOUT_MIN.
        self._timeout_seconds = float(timeout_seconds) if timeout_seconds else None
        self._active: dict = {}
        self._history: deque = deque(maxlen=self.history_cap)
        self._counters: dict[str, int] = {
            "threats_seen_total": 0,
            "deduped_total": 0,
            "evicted_total": 0,
            "timeouts_total": 0,
        }
        # [esc-auto slice 1] ONE worker thread for every dead-man deadline
        # (was: one threading.Timer OS thread per high/critical threat).
        self._scheduler = BoundedDeadlineScheduler(
            self._on_deadline, max_pending=self.max_active, name="scp-escalation-deadman"
        )
        # Dedicated lock protects JSONL and atomic state writes without holding
        # the escalation state lock during filesystem I/O.
        self._write_lock = threading.Lock()
        self._restore_state()

    @property
    def _timers(self) -> dict:
        """Compat view (pre-slice-1 attribute name): pending dead-man deadlines.

        Values expose ``cancel()`` only; there is NO OS thread behind them.
        """
        return {key: _PendingDeadline(self._scheduler, key) for key in self._scheduler.pending_keys()}

    def shutdown(self) -> None:
        """Stop the scheduler worker; armed deadlines stay persisted and are
        re-armed on the next start (never treated as approved)."""
        self._scheduler.shutdown()

    def _persist_state(self) -> None:
        """Atomically persist authoritative escalation state."""
        with lock:
            payload = {
                "schema_version": 1,
                "active": self._active,
                "history": list(self._history),
                "counters": dict(self._counters),
            }
            serialized = json.dumps(payload, sort_keys=True, default=str)
        temporary = self.escalation_state_path.with_name(
            f"{self.escalation_state_path.name}.{secrets.token_hex(4)}.tmp"
        )
        with self._write_lock:
            try:
                temporary.write_text(serialized, encoding="utf-8")
                os.replace(temporary, self.escalation_state_path)
            except Exception as exc:
                logger.warning("[escalation] state persist warning: %s", exc, exc_info=True)
            finally:
                if temporary.exists():
                    try:
                        temporary.unlink()
                    except OSError:
                        logger.debug('EscalationManager._persist_state: OSError ignored', exc_info=True)

    def _restore_state(self) -> None:
        """Restore state within caps and re-arm non-terminal deadlines on the
        single scheduler worker. Overflow and invalid deadlines take the SAFE
        outcome (timeout playbook) instead of being dropped silently."""
        try:
            raw = self.escalation_state_path.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except FileNotFoundError:
            logger.debug('EscalationManager._restore_state: FileNotFoundError ignored', exc_info=True)
            return
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("[escalation] state restore skipped: %s", exc)
            return
        if not isinstance(payload, dict):
            logger.warning("[escalation] state restore skipped: invalid root")
            return
        active = payload.get("active", {})
        history = payload.get("history", [])
        counters = payload.get("counters", {})
        if not isinstance(active, dict) or not isinstance(history, list):
            logger.warning("[escalation] state restore skipped: invalid collections")
            return
        entries = [(str(key), value) for key, value in active.items() if isinstance(value, dict)]
        overflow = entries[: max(0, len(entries) - self.max_active)]
        kept = entries[len(overflow):]
        with lock:
            self._active = dict(kept)
            self._history.extend(value for value in history if isinstance(value, dict))
            if isinstance(counters, dict):
                for name in self._counters:
                    if isinstance(counters.get(name), int):
                        self._counters[name] = counters[name]
            armed = [(key, value) for key, value in kept if value.get("status") == "armed"]
        for key, value in overflow:
            self._capacity_evict(key, value)
        for key, entry in armed:
            try:
                deadline = float(entry["deadline_at"])
            except (KeyError, TypeError, ValueError):
                logger.warning("[escalation] armed entry without valid deadline ⇒ safe outcome now: %s", key)
                deadline = self._now()
            effective = self._scheduler.schedule(key, deadline)
            with lock:
                if key in self._active:
                    self._active[key]["deadline_at"] = effective
        if armed or overflow:
            self._persist_state()

    def _admit(self, threat_id: str, threat: dict, severity: str) -> tuple[bool, list]:
        """Insert or dedup an entry under the cap. Returns (is_new, victims).

        Must be called with ``lock`` held; victims are processed by the caller
        AFTER releasing the lock (their safe outcome does file I/O).
        """
        now = self._now()
        entry = self._active.get(threat_id)
        if entry is not None:
            entry["seen_count"] = int(entry.get("seen_count", 1)) + 1
            entry["last_seen_at"] = now
            self._counters["deduped_total"] += 1
            if severity in self.AUTO_ARM_SEVERITIES and entry.get("status") == "monitoring":
                entry["severity"] = severity
                entry["status"] = "armed"
            return False, []
        victims = []
        while len(self._active) >= self.max_active:
            victim_id = next(iter(self._active))
            victims.append((victim_id, self._active.pop(victim_id)))
        self._active[threat_id] = {
            "threat_id": threat_id,
            "threat": threat,
            "severity": severity,
            "armed_at": now,
            "seen_count": 1,
            "status": "armed" if severity in self.AUTO_ARM_SEVERITIES else "monitoring",
        }
        return True, victims

    def _capacity_evict(self, threat_id: str, entry: dict) -> None:
        """Evicted to respect the cap ⇒ SAFE outcome, never a silent drop."""
        with lock:
            self._counters["evicted_total"] += 1
        if entry.get("status") == "armed":
            self._scheduler.cancel(threat_id)
            self.on_timeout(entry.get("threat") or {"id": threat_id}, cause="capacity_evicted", threat_id=threat_id, entry=entry)
            return
        with lock:
            self._history.append({
                "threat_id": threat_id,
                "action": "closed_monitoring",
                "cause": "capacity_evicted",
                "timestamp": self._now(),
            })
        self.log_action(f"Monitoring entry {threat_id} closed at capacity", "capacity_evicted")

    def on_threat_detected(self, threat: dict):
        """[SCP-DNA-FIX R5-3] Entry point for detected threats.

        Auto-arms the Dead Man's Switch for high/critical threats. [esc-auto
        slice 1] Bounded: dedup by threat id (repeat never postpones the
        deadline), cap ``max_active`` with fire-early eviction, one worker.
        """
        severity = self.classify_threat(threat)
        threat_id = self._threat_id(threat)
        with lock:
            self._counters["threats_seen_total"] += 1
            is_new, victims = self._admit(threat_id, threat, severity)
        for victim_id, victim in victims:
            self._capacity_evict(victim_id, victim)
        self._persist_state()
        # [SCP-DNA-FIX 4-b-018] log_action (file I/O) OUTSIDE the escalation lock.
        # [esc-auto slice 1] Repeats are aggregated in seen_count/counters, not
        # re-journaled line by line (an attacker repeating one payload cannot
        # grow the journal per request).
        if is_new:
            self.log_action(f"Threat detected: {threat}, severity: {severity}", "threat_detection")
        if severity in self.AUTO_ARM_SEVERITIES:
            try:
                self.start_countdown(threat, timeout_min=self.DEFAULT_TIMEOUT_MIN)
            except Exception as e:
                logger.warning(f"[escalation] start_countdown failed for threat {threat_id}: {e}", exc_info=True)

    @staticmethod
    def _threat_id(threat: dict) -> str:
        """Derive a stable id from a threat dict (for tracking + human approval)."""
        if not isinstance(threat, dict):
            return str(id(threat))
        # Prefer explicit id; fall back to a hash of the threat content.
        if threat.get("id"):
            return str(threat["id"])
        if threat.get("threat_id"):
            return str(threat["threat_id"])
        import hashlib
        payload = json.dumps(threat, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:16]

    @staticmethod
    def _now() -> float:
        import time
        return time.time()

    def classify_threat(self, threat_data: dict) -> str:
        """Classify threat severity: low | medium | high | critical.

        [SCP-DNA-FIX 4-b-005] Was a stub `return "high"` — every threat that
        hit `on_threat_detected` was classified "high" → matched
        `AUTO_ARM_SEVERITIES = {"high", "critical"}` → `start_countdown` was
        called → 30-min Dead Man's Switch armed → on timeout,
        `execute_defensive_playbook` runs (block_ip, tighten_rate_limit,
        enable_honeypot, forensic_logging, alert_cert). 100% false-alarm rate
        for low/medium threats (DNA #22: PASS≠TRUE — classifier claimed to
        classify but always returned "high"; DNA #9: No harm — false alarms
        cause operator alert fatigue + unnecessary defensive playbook; DNA #11:
        HITL — every threat auto-armed 30-min countdown without discrimination).

        Conservative default: "medium" (does NOT auto-arm Dead Man's Switch).
        Only "high"/"critical" when explicit evidence supports it.

        Args:
            threat_data: Dict from caller (judgecore_mixin). Observed fields in
                production callers:
                  - id, type, severity, prediction_confidence, description
                    (predictive escalation path, judgecore_mixin:410-417)
                  - type, severity, falsification_status, question, verdict,
                    confidence, note (falsification-human-review path,
                    judgecore_mixin:2943-2951)

        Returns:
            "low" | "medium" | "high" | "critical"
        """
        if not isinstance(threat_data, dict):
            return "medium"

        # Explicit severity in threat_data takes precedence — callers that
        # already know the severity (e.g. falsification_human_review always
        # sets severity="high") get their value respected.
        severity = (threat_data.get("severity") or "").lower()
        if severity in ("critical", "high", "medium", "low"):
            return severity

        # Confidence — accept either `confidence` or `prediction_confidence`
        # (both are used by production callers; see judgecore_mixin:414, 2949).
        confidence = threat_data.get("confidence")
        if confidence is None:
            confidence = threat_data.get("prediction_confidence", 0.0)
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            logger.debug('EscalationManager.classify_threat: TypeError, ValueError ignored', exc_info=True)
            confidence = 0.0

        # Indicator type — production callers use `type` (e.g.
        # "falsification_human_review" or a forecast.threat_type). Fall back to
        # explicit `indicator_type` / `indicator` if present.
        indicator = (
            threat_data.get("indicator_type")
            or threat_data.get("indicator")
            or threat_data.get("type")
            or ""
        ).lower()

        # Heuristic: only 'high' if confidence is very high AND an explicit
        # severe indicator type is present (not just a generic anomaly).
        if confidence >= 0.9 and indicator in (
            "exploit_attempt", "data_exfiltration", "privilege_escalation",
        ):
            return "high"
        # Critical indicators (known-bad signatures) require even higher
        # confidence to avoid false alarms.
        if confidence >= 0.95 and indicator in (
            "malware_signature", "known_bad_ip",
        ):
            return "critical"
        # Falsification human-review is a known caller-set severity path; if
        # severity wasn't explicit but type indicates it, treat as medium
        # (operator review required, but does NOT auto-arm countdown).
        if indicator == "falsification_human_review" and confidence >= 0.7:
            return "medium"
        if confidence >= 0.5:
            return "medium"
        # Conservative default — "medium" (NOT "high", NOT auto-arm).
        # Empty / ambiguous threat_data lands here. Returning "medium" rather
        # than "low" ensures the threat is operator-visible (low may be
        # filtered out by dashboards), while still NOT triggering the
        # Dead Man's Switch countdown.
        return "medium"

    def start_countdown(self, threat: dict, timeout_min: int = 30):
        """Arm the dead-man deadline on the single scheduler worker.

        [esc-auto slice 1] Effective deadline = min(existing, new): re-arming
        never postpones the safe outcome. Unknown threats are admitted under
        the same cap first.
        """
        threat_id = self._threat_id(threat)
        timeout_s = self._timeout_seconds if self._timeout_seconds is not None else timeout_min * 60
        requested = self._now() + timeout_s
        with lock:
            victims: list = []
            if threat_id not in self._active:
                _, victims = self._admit(threat_id, threat, "high")
        for victim_id, victim in victims:
            self._capacity_evict(victim_id, victim)
        effective = self._scheduler.schedule(threat_id, requested)
        with lock:
            if threat_id in self._active:
                self._active[threat_id]["status"] = "armed"
                self._active[threat_id]["timeout_min"] = timeout_min
                self._active[threat_id]["deadline_at"] = effective
        self._persist_state()
        # [SCP-DNA-FIX 4-b-018] log_action (file I/O) OUTSIDE the escalation lock.
        if effective == requested:
            self.log_action(f"Countdown started for threat: {threat}, timeout: {timeout_min} minutes", "countdown_started")

    def _on_deadline(self, threat_id: str, cause: str) -> None:
        """Scheduler callback (worker thread): deadline reached ⇒ SAFE outcome."""
        with lock:
            entry = self._active.get(threat_id)
            threat = (entry or {}).get("threat") or {"id": threat_id}
        self.on_timeout(threat, cause=cause, threat_id=threat_id, entry=entry)

    def on_timeout(
        self,
        threat: dict,
        *,
        cause: str = "deadline",
        threat_id: str | None = None,
        entry: dict | None = None,
    ) -> None:
        """Deadline reached (or capacity eviction) ⇒ terminalize + defensive
        playbook. This is the SAFE outcome; it never records an approval."""
        threat_id = threat_id or self._threat_id(threat)
        self._scheduler.cancel(threat_id)
        with lock:
            state = self._active.pop(threat_id, None) or entry
            if state is not None:
                self._counters["timeouts_total"] += 1
                self._history.append({
                    "threat_id": threat_id,
                    "action": "timeout",
                    "cause": cause,
                    "timestamp": self._now(),
                })
        self._persist_state()
        # [SCP-DNA-FIX 4-b-018] log_action (file I/O) — state has already been
        # terminalized above, so dashboard cannot retain stale armed/timer data.
        self.log_action(
            f"Timeout occurred for threat: {threat} (cause={cause}), evaluating defensive playbook",
            "timeout_occurred",
        )
        self.execute_defensive_playbook(threat)

    def execute_defensive_playbook(self, threat: dict):
        """[SCP-DNA-FIX R5-3] Execute the default defensive playbook.

        Each action is now validated against PlaybookRegistry.validate_action()
        BEFORE being executed — refuses FORBIDDEN_ACTIONS (hack_back,
        data_destruction, preemptive_strike, counter_attack, reverse_exploit)
        with a WARNING log. Previously the entire playbooks.py module was
        dead-imported so the guardrail was unenforced.
        """
        # Only forensic logging is implemented in this module today. The
        # other names are policy intents, not real OS/network controls. DNA #22:
        # never record a skipped action as if it had been enforced.
        implemented_actions = {"forensic_logging"}
        results = []
        for action, enabled in DEFAULT_DEFENSIVE_PLAYBOOK.items():
            if not enabled:
                continue
            # [SCP-DNA-FIX R5-3] Guardrail: refuse forbidden actions.
            try:
                if not PlaybookRegistry.validate_action(action):
                    logger.warning(
                        f"[escalation] GUARDRAIL refused forbidden action "
                        f"'{action}' for threat {self._threat_id(threat)} "
                        f"skipping (FORBIDDEN_ACTIONS={sorted(FORBIDDEN_ACTIONS)})"
                    )
                    self.log_action(
                        f"GUARDRAIL refused forbidden action: {action}",
                        "guardrail_refused",
                    )
                    results.append({"action": action, "status": "GUARDRAIL_REFUSED"})
                    continue
            except Exception as guard_err:
                # If the guardrail itself fails, fail-safe (skip the action).
                logger.warning(f"[escalation] guardrail check error for '{action}': {guard_err} — skipping", exc_info=True)
                results.append({"action": action, "status": "GUARDRAIL_ERROR"})
                continue
            if action not in implemented_actions:
                message = (
                    f"DEFENSIVE_ACTION_NOT_IMPLEMENTED: {action} for threat "
                    f"{self._threat_id(threat)}; no OS/network enforcement performed"
                )
                logger.warning("[escalation] %s", message)
                self.log_action(message, "action_not_implemented")
                results.append({"action": action, "status": "SKIPPED_NOT_IMPLEMENTED"})
                continue
            self.log_action(f"Forensic logging recorded for threat: {threat}", action)
            results.append({"action": action, "status": "RECORDED"})
        return {
            "threat_id": self._threat_id(threat),
            "enforcement_performed": False,
            "actions": results,
        }

    def log_action(self, message: str, why: str):
        """Write a log entry to disk.

        [SCP-DNA-FIX 4-b-018] This method does FILE I/O. Callers MUST NOT
        hold the escalation `lock` when invoking this method — the file
        write blocks on disk I/O (could be slow under load, could hang
        on disk-full). All callers in this module have been refactored to
        release the escalation lock BEFORE calling log_action (or to not
        acquire it at all when no state mutation is needed).

        Pre-fix: callers like on_threat_detected, start_countdown,
        on_timeout, and execute_defensive_playbook called log_action
        INSIDE `with lock:` blocks → all escalation decisions serialized
        on disk I/O; if disk was full, the lock was held during the
        exception handling → subsequent calls to on_threat_detected /
        get_active_escalations / escalation_status blocked on the held
        lock.

        Post-fix: log_action is called OUTSIDE any caller's `with lock:`
        block. The log_entry dict is built here (fast, microseconds), then
        written to disk. If disk is slow or full, only this method's
        caller is blocked — not all escalation decisions.
        """
        import time
        log_entry = {
            "timestamp": time.time(),
            "message": message,
            "why": why
        }
        with self._write_lock:
            with self.escalation_log_path.open("a") as log_file:
                json.dump(log_entry, log_file)
                log_file.write("\n")

    def get_active_escalations(self) -> list:
        """[SCP-DNA-FIX R5-3] Return all currently-armed escalations.

        Previously this method had 0 callers → Dead Man's Switch armed threats
        were invisible to the dashboard. Now exposed via escalation_status()
        so any caller (admin dashboard, /v105/escalation/status route, metrics
        scraper) can see armed threats.
        """
        with lock:
            return list(self._active.values())

    def get_escalation_history(self) -> list:
        with lock:
            return list(self._history)

    def escalation_status(self) -> dict:
        """[SCP-DNA-FIX R5-3] Public dashboard snapshot.

        Returns:
            {
              "active_count": int,
              "active": [...],          # via get_active_escalations()
              "history_count": int,
              "armed_count": int,       # subset of active with status='armed'
              "timers_count": int,      # pending dead-man deadlines (no OS thread each)
              "counters": {...},        # [esc-auto] aggregate counters
              "scheduler": {...},       # [esc-auto] single-worker scheduler stats
            }
        """
        scheduler_stats = self._scheduler.stats()
        with lock:
            active = list(self._active.values())
            armed = [e for e in active if e.get("status") == "armed"]
            return {
                "active_count": len(active),
                "active": active,
                "history_count": len(self._history),
                "armed_count": len(armed),
                "timers_count": scheduler_stats["pending"],
                "max_active": self.max_active,
                "counters": dict(self._counters),
                "scheduler": scheduler_stats,
            }

    # ---- Human-in-the-loop wiring ----
    # [SCP-DNA-FIX R5-3] DESIGN TODO(SCP-DNA-R5-03): on_human_approval / on_human_rejection
    # are now reachable from any admin code path (e.g. FastAPI route handlers)
    # but are NOT yet wired to HTTP endpoints. Recommended wiring (out of
    # scope for this fix — api_server_parts/lifespan.py + api_server.py are owned by parent):
    #
    #   @app.post("/v105/escalation/{threat_id}/approve")
    #   async def approve_escalation(threat_id: str, action: str = "default"):
    #       judge.escalation_manager.on_human_approval(threat_id, action)
    #       return {"status": "approved"}
    #
    #   @app.post("/v105/escalation/{threat_id}/reject")
    #   async def reject_escalation(threat_id: str):
    #       judge.escalation_manager.on_human_rejection(threat_id)
    #       return {"status": "rejected"}
    #
    # These endpoints would let a human operator cancel the Dead Man's Switch
    # countdown before the 30-min timeout fires execute_defensive_playbook.
    # Until those routes exist, threats that are auto-armed will fire the
    # default defensive playbook on timeout (fail-safe: act, don't wait).
    # [esc-auto slice 3 · owner 2026-10-30] There is NO approval step any more:
    # the dead-man deadline always takes the SAFE outcome. The legacy
    # on_human_approval / on_human_rejection names are kept for callers, but
    # both only record an operator audit annotation (operator_ack /
    # operator_dispute) with the TRUE principal; neither cancels the deadline.
    def _record_operator_annotation(self, threat_id: str, decision: str, actor: str, note: str = "") -> None:
        with lock:
            record = {
                "threat_id": threat_id,
                "action": decision,
                "actor": str(actor)[:80],
                "note": str(note)[:200],
                "effect": "audit_only",
                "timestamp": self._now(),
            }
            self._history.append(record)
            entry = self._active.get(threat_id)
            if entry is not None:
                annotations = entry.setdefault("operator_annotations", [])
                annotations.append(record)
                del annotations[:-10]
        self._persist_state()

    def on_human_approval(self, threat_id: str, action: str = "default", actor: str = "unattributed"):
        self._record_operator_annotation(threat_id, "operator_ack", actor, action)
        self.log_action(f"Operator ack {threat_id} by {actor} (audit only): {action}", "operator_ack")

    def on_human_rejection(self, threat_id: str, actor: str = "unattributed"):
        self._record_operator_annotation(threat_id, "operator_dispute", actor)
        self.log_action(f"Operator dispute {threat_id} by {actor} (audit only)", "operator_dispute")

    # ---- Public admin API (wired: /v105/escalation/{id}/approve|reject) ----
    # [esc-auto slice 3] Operator AUDIT/VETO annotations only. They never
    # cancel the dead-man deadline (no approval step exists; timeout = safe
    # outcome). Routes require an operator credential (machine ⇒ 403).
    def approve(self, threat_id: str, action: str = "default", actor: str = "unattributed") -> None:
        """Record an operator acknowledgement (audit only; safe outcome unchanged)."""
        self.on_human_approval(threat_id, action, actor=actor)

    def reject(self, threat_id: str, actor: str = "unattributed") -> None:
        """Record an operator dispute / false-positive claim (audit only)."""
        self.on_human_rejection(threat_id, actor=actor)

    # ---- [SCP-DNA-FIX R13-3 BUG-005] Dashboard aggregation ----
    # R5-3 added get_active_escalations / get_escalation_history /
    # escalation_status but they had 0 callers → dashboard could not see live
    # Dead Man's Switch state. Below is a single aggregation method that
    # dashboard endpoints should call.
    #
    # [SCP-ESCALATION-02] TODO(parent — api_server.py owner): wire this into a FastAPI route:
    #     @app.get("/v105/escalation/status")
    #     async def escalation_status():
    #         return judge.escalation_manager.get_dashboard_status()
    #
    # Until that route exists, this method is callable programmatically
    # (e.g., from a metrics scraper or the dashboard's Python REPL).
    def get_dashboard_status(self) -> dict:
        """Aggregate dashboard snapshot — one call returns everything.

        Returns:
            {
              "status": <escalation_status()>,
              "active": [<get_active_escalations()>],
              "history": [<get_escalation_history()>],
              "summary": {
                  "active_count": int,
                  "armed_count": int,
                  "history_count": int,
                  "timers_count": int,
              },
            }
        """
        status = self.escalation_status()
        active = self.get_active_escalations()
        history = self.get_escalation_history()
        return {
            "status": status,
            "active": active,
            "history": history,
            "summary": {
                "active_count": status.get("active_count", 0),
                "armed_count": status.get("armed_count", 0),
                "history_count": status.get("history_count", 0),
                "timers_count": status.get("timers_count", 0),
            },
        }
