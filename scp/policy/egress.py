"""Unified Egress Policy Engine for SCP Agent OS Autonomous Operations."""
from __future__ import annotations

import enum
import ipaddress
import os
import urllib.parse
from dataclasses import dataclass
from typing import Any, Iterable


class EgressMode(str, enum.Enum):
    DENY = "deny"
    ALLOWLIST = "allowlist"
    OPEN = "open"


class EgressDeniedError(PermissionError, ValueError):
    """Raised when an outbound network request is forbidden by egress policy."""

    def __init__(self, target: str, reason: str, url: str | None = None) -> None:
        self.target = target
        self.reason = reason
        self.url = url or target
        super().__init__(f"egress denied for {self.url!r}: {reason}")


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
        try:
            ip = ipaddress.ip_address(norm_host)
            if getattr(ip, "ipv4_mapped", None) is not None and ip.ipv4_mapped:
                return ip.ipv4_mapped in ipaddress.ip_network("169.254.0.0/16")
            if isinstance(ip, ipaddress.IPv6Address):
                return ip == ipaddress.ip_address("fd00:ec2::254")
            return ip in ipaddress.ip_network("169.254.0.0/16")
        except ValueError:
            return False

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
