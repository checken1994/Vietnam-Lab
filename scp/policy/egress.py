"""Unified Egress Policy Engine for SCP Agent OS Autonomous Operations."""
from __future__ import annotations

import enum
import ipaddress
import logging
import os
import urllib.parse
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


class EgressMode(str, enum.Enum):
    DENY = "deny"
    ALLOWLIST = "allowlist"
    OPEN = "open"


def _redact_url_for_message(url: str) -> str:
    """[AUDIT-FIX med-1] Redact secrets trong query string trước khi URL vào
    message của EgressDeniedError (message bị caller log ở mọi tầng).

    Canonical helper: ``scp.core.api_utils.redact_query_secrets`` (import lười
    để file policy nền tảng này không bị gắn thêm dependency). Fail-closed:
    nếu helper không khả dụng, drop TOÀN BỘ query thay vì leak credential.
    """
    try:
        from scp.core.api_utils import redact_query_secrets

        return redact_query_secrets(url)
    except Exception as _redact_err:
        logger.debug("redact_query_secrets unavailable, falling back to urlsplit redaction: %s", _redact_err, exc_info=True)
        try:
            parts = urllib.parse.urlsplit(str(url))
            return urllib.parse.urlunsplit(parts._replace(query="", fragment=""))
        except Exception as _split_err:
            logger.debug("urlsplit redaction fallback failed, returning fully redacted placeholder: %s", _split_err, exc_info=True)
            return "[REDACTED-URL]"


def _numeric_host_to_ip(host: str) -> str | None:
    """[AUDIT-FIX 2026-09-29 Agent1] Numeric IPv4 spellings to canonical form.

    OS resolvers accept decimal ('2852039166' == 169.254.169.254) and hex
    ('0xA9FEA9FE') single-integer IPv4 spellings while ipaddress.ip_address
    rejects them with ValueError. Left unrecognized, those spellings bypassed
    the cloud-metadata block (Invariant 1) in OPEN mode. Convert numeric-looking
    hosts to the canonical dotted-quad so the block cannot be dodged by
    spelling; anything not purely numeric returns None and normal parsing
    applies. Pure string/int math (no socket) so the result is identical on
    every platform. KNOWN LIMIT (named, not hidden): mixed-radix dotted forms
    (hex/octal-style parts) are NOT normalized here; those stay covered by
    SSRF DNS-resolution checks at fetch time.
    """
    if not host or len(host) > 64:
        return None
    value = None
    try:
        if host.isdigit():
            value = int(host, 10)
        elif len(host) > 2 and host[0] == "0" and host[1] in ("x", "X") and all(
            c in "0123456789abcdefABCDEF" for c in host[2:]
        ):
            value = int(host[2:], 16)
    except ValueError:
        return None
    if value is None:
        return None
    if not 0 <= value <= 0xFFFFFFFF:
        return None
    return ".".join(str((value >> shift) & 0xFF) for shift in (24, 16, 8, 0))


class EgressDeniedError(PermissionError, ValueError):
    """Raised when an outbound network request is forbidden by egress policy."""

    def __init__(self, target: str, reason: str, url: str | None = None) -> None:
        self.target = target
        self.reason = reason
        self.url = url or target
        # [AUDIT-FIX med-1] Message có thể được log bởi bất kỳ caller nào —
        # query string chứa credential (api_key/token/...) phải được redact
        # TRƯỚC khi vào message. Host + path giữ nguyên để còn debug.
        super().__init__(f"egress denied for {_redact_url_for_message(self.url)!r}: {reason}")


@dataclass(frozen=True)
class EgressDestination:
    host: str
    port: int | None = None
    scheme: str = "https"

    @classmethod
    def from_url(cls, url: str | Any) -> EgressDestination:
        if hasattr(url, "full_url"):
            url_str = str(url.full_url)
        else:
            url_str = str(url)
        if "://" not in url_str and not url_str.startswith("//"):
            parsed = urllib.parse.urlparse("//" + url_str)
        else:
            parsed = urllib.parse.urlparse(url_str)
        scheme = (parsed.scheme or "https").lower()
        host = (parsed.hostname or "").strip().strip("[]").lower().rstrip(".")
        port = parsed.port
        return cls(host=host, port=port, scheme=scheme)


class EgressPolicy:
    """Strict Zero-Trust Policy Engine governing network egress for tools."""

    BLOCKED_METADATA_HOSTS = frozenset({
        "169.254.169.254",
        "metadata.google.internal",
        "metadata.aws",
        "169.254.170.2",
        "fd00:ec2::254",
    })
    LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

    def __init__(
        self,
        mode: EgressMode | str | None = None,
        allowlist: Iterable[str] | None = None,
        production_mode: bool | None = None,
    ) -> None:
        if mode is None:
            raw_mode = os.environ.get("SCP_EGRESS_MODE", "").lower().strip() or "allowlist"
            if raw_mode in EgressMode._value2member_map_:
                self.mode = EgressMode(raw_mode)
            elif raw_mode in ("offline", "disabled", "deny"):
                self.mode = EgressMode.DENY
            else:
                self.mode = EgressMode.DENY
        elif isinstance(mode, str):
            clean_mode = mode.lower().strip()
            if clean_mode in EgressMode._value2member_map_:
                self.mode = EgressMode(clean_mode)
            elif clean_mode in ("offline", "disabled", "deny"):
                self.mode = EgressMode.DENY
            else:
                self.mode = EgressMode.DENY
        else:
            self.mode = mode

        if allowlist is not None:
            raw_allowlist = allowlist
        else:
            raw_allowlist = os.environ.get("SCP_EGRESS_ALLOWLIST", "").split(",")

        self.allowlist: frozenset[str] = frozenset(
            item.strip().strip("[]").lower().rstrip(".") for item in raw_allowlist if item.strip()
        )
        self.production_mode = (
            production_mode
            if production_mode is not None
            else os.environ.get("SCP_PRODUCTION_MODE", "0").strip().lower() in {"1", "true", "yes", "on"}
        )

    def is_loopback(self, host: str) -> bool:
        norm_host = host.strip().strip("[]").lower().rstrip(".")
        if norm_host in self.LOOPBACK_HOSTS:
            return True
        try:
            return ipaddress.ip_address(norm_host).is_loopback
        except ValueError:
            return False

    def is_cloud_metadata(self, host: str) -> bool:
        norm_host = host.strip().strip("[]").lower().rstrip(".")
        if norm_host in self.BLOCKED_METADATA_HOSTS:
            return True
        ip = None
        try:
            ip = ipaddress.ip_address(norm_host)
            if getattr(ip, "ipv4_mapped", None) is not None and ip.ipv4_mapped:
                return ip.ipv4_mapped in ipaddress.ip_network("169.254.0.0/16")
            if isinstance(ip, ipaddress.IPv6Address):
                return ip == ipaddress.ip_address("fd00:ec2::254")
            return ip in ipaddress.ip_network("169.254.0.0/16")
        except ValueError:
            # [AUDIT-FIX 2026-09-29 Agent1] Numeric spellings ('2852039166',
            # '0xA9FEA9FE') that resolvers accept but ipaddress rejects must
            # not dodge the metadata block - normalize and re-check fail-closed.
            numeric = _numeric_host_to_ip(norm_host)
            if not numeric:
                return False
            try:
                ip = ipaddress.ip_address(numeric)
            except ValueError:
                return False
        if getattr(ip, "ipv4_mapped", None) is not None and ip.ipv4_mapped:
            return ip.ipv4_mapped in ipaddress.ip_network("169.254.0.0/16")
        if isinstance(ip, ipaddress.IPv6Address):
            return ip == ipaddress.ip_address("fd00:ec2::254")
        return ip in ipaddress.ip_network("169.254.0.0/16")

    def enforce(self, destination: str | EgressDestination, token_allowed_hosts: Iterable[str] | None = None) -> None:
        """Enforce egress policy fail-closed. Raises EgressDeniedError on violation."""
        dest = EgressDestination.from_url(destination) if isinstance(destination, str) else destination
        url_context = str(getattr(destination, "full_url", destination)) if not isinstance(destination, EgressDestination) else dest.host

        if not dest.host:
            raise EgressDeniedError(str(destination), "Missing or invalid host in destination", url=url_context)

        # Invariant 1: Cloud metadata is unconditionally blocked in all modes
        if self.is_cloud_metadata(dest.host):
            raise EgressDeniedError(dest.host, "Access to cloud metadata endpoints is unconditionally prohibited", url=url_context)

        # Invariant 2: Loopback is always permitted for internal microservices
        if self.is_loopback(dest.host):
            return

        # Invariant 3: Deny mode blocks all external traffic
        if self.mode == EgressMode.DENY:
            raise EgressDeniedError(dest.host, "SCP_EGRESS_MODE=deny blocks all non-loopback outbound traffic", url=url_context)

        # Invariant 4: Allowlist mode checks global allowlist and capability token-bound allowlist
        if self.mode == EgressMode.ALLOWLIST:
            permitted = set(self.allowlist)
            if token_allowed_hosts:
                permitted.update(h.strip().strip("[]").lower().rstrip(".") for h in token_allowed_hosts if h.strip())

            matched = False
            for item in permitted:
                if item.startswith("*."):
                    suffix = item[2:]
                    if dest.host == suffix or dest.host.endswith("." + suffix):
                        matched = True
                        break
                elif dest.host == item:
                    matched = True
                    break

            if not matched:
                raise EgressDeniedError(dest.host, f"Host not present in egress allowlist: {sorted(permitted)}", url=url_context)
            return

        # Invariant 5: In production mode, open/unknown modes fail-closed
        if self.production_mode and self.mode not in {EgressMode.DENY, EgressMode.ALLOWLIST}:
            raise EgressDeniedError(dest.host, "Production mode requires explicit deny or allowlist egress policy", url=url_context)


__all__ = [
    "EgressMode",
    "EgressDeniedError",
    "EgressDestination",
    "EgressPolicy",
]
