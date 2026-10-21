"""Reachability of the normative service endpoints.
This module is part of :mod:`scp.runtime_audit.probes`.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Sequence

from scp.runtime_audit.models import ProbeResult, Severity, utc_now_iso


class ServiceLivenessProbe:
    """Observe whether the normative services actually answer.

    Status semantics
    ----------------
    * 2xx/3xx  -> the endpoint answered normally.
    * 401/403  -> the endpoint answered and refused the request without
      credentials. That PROVES the process is alive and the guard is
      enforcing authentication, so it is not a liveness failure. It is
      recorded separately as ``protected``.
    * 404      -> the route is absent (spec drift) -> WARN.
    * 5xx      -> the endpoint is broken -> FAIL.
    * connection error -> FAIL.
    """

    probe_id = "services.liveness"

    def __init__(
        self,
        targets: Sequence[dict[str, Any]],
        *,
        timeout_sec: float = 5.0,
        bearer_token: str | None = None,
    ) -> None:
        self.targets = list(targets)
        self.timeout_sec = timeout_sec
        self.bearer_token = bearer_token or None

    def probe_one(self, target: dict[str, Any]) -> dict[str, Any]:
        name = str(target.get("name") or target.get("url") or "unknown")
        url = str(target.get("url") or "")
        record: dict[str, Any] = {"name": name, "url": url}
        if not url:
            record.update({"reachable": False, "error": "missing_url", "status": None, "latency_ms": None})
            return record
        headers = {"User-Agent": "scp-runtime-audit/1.0"}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        started = time.monotonic()
        try:
            request = urllib.request.Request(url, method="GET", headers=headers)
            with urllib.request.urlopen(request, timeout=self.timeout_sec) as response:
                body = response.read(65536).decode("utf-8", errors="replace")
                record.update({
                    "reachable": True,
                    "status": int(response.status),
                    "latency_ms": round((time.monotonic() - started) * 1000, 2),
                    "body_bytes": len(body),
                })
                parsed = self._parse_json(body)
                if parsed is not None:
                    record["json"] = parsed
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            record.update({
                "reachable": True,
                "status": status,
                "latency_ms": round((time.monotonic() - started) * 1000, 2),
                "error": f"HTTPError {status}",
            })
            if status in (401, 403):
                record["protected"] = True
        except (urllib.error.URLError, OSError, ValueError) as exc:
            record.update({
                "reachable": False,
                "status": None,
                "latency_ms": round((time.monotonic() - started) * 1000, 2),
                "error": f"{type(exc).__name__}: {exc}",
            })
        return record

    @staticmethod
    def _parse_json(body: str) -> Any:
        try:
            return json.loads(body)
        except (TypeError, ValueError):
            return None

    def run(self) -> list[ProbeResult]:
        results: list[ProbeResult] = []
        if not self.targets:
            results.append(ProbeResult(
                probe_id=self.probe_id,
                severity=Severity.UNKNOWN,
                title="No service liveness targets configured",
                summary="The audit was not given any endpoint to probe.",
                details={"targets": []},
            ))
            return results

        observations = [self.probe_one(t) for t in self.targets]
        unreachable = [o["name"] for o in observations if not o.get("reachable")]
        server_error = [
            o["name"] for o in observations
            if isinstance(o.get("status"), int) and o["status"] >= 500
        ]
        missing = [
            o["name"] for o in observations
            if isinstance(o.get("status"), int) and o["status"] == 404
        ]
        protected = [o["name"] for o in observations if o.get("protected")]
        healthy = [
            o["name"] for o in observations
            if o.get("reachable") and isinstance(o.get("status"), int)
            and o["status"] < 400
        ]

        if unreachable or server_error:
            severity = Severity.FAIL
            title = "Service endpoint is unreachable or broken"
            parts = []
            if unreachable:
                parts.append(f"unreachable: {', '.join(unreachable)}")
            if server_error:
                parts.append(f"server error: {', '.join(server_error)}")
            summary = "; ".join(parts)
        elif missing:
            severity = Severity.WARN
            title = "Service route is absent"
            summary = f"Route not found: {', '.join(missing)}"
        else:
            severity = Severity.PASS
            title = "All probed service endpoints answer"
            summary = f"{len(healthy)} endpoint(s) reachable"
            if protected:
                summary += f"; {len(protected)} refused the request without credentials (auth enforced)"

        results.append(ProbeResult(
            probe_id=self.probe_id,
            severity=severity,
            title=title,
            summary=summary,
            details={
                "unreachable": unreachable,
                "server_error": server_error,
                "missing": missing,
                "protected": protected,
                "healthy": healthy,
                "authenticated": bool(self.bearer_token),
            },
            evidence={"observations": observations},
            observed_at=utc_now_iso(),
        ))
        return results
