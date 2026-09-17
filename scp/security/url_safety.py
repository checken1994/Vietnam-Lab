"""
URL Safety Helper — B310 fix.

Validates URL scheme + blocks internal/private IPs before calling urllib.urlopen.
Use this instead of urllib.request.urlopen() to satisfy B310 (CWE-22).  # nosec B310 — URL validated by SCP

[EE egress enforcement — closes M13 G1]
This module is the single EGRESS CHOKE POINT for outbound HTTP fetches.
`enforce_egress_policy` reads SCP_EGRESS_MODE and fails closed:
  - deny/offline/disabled  → only loopback hosts (self-probe/internal services)
  - allowlist              → only loopback + hosts in SCP_EGRESS_ALLOWLIST
  - unset                  → no new restriction (historical dev behavior)
  - unknown value          → dev: no new restriction; production
                             (SCP_PRODUCTION_MODE) → deny (fail-closed)
`safe_urlopen` enforces it before the SSRF validation. The two other
canonical fetchers (`scp.core.url_fetcher._safe_fetch_url` and
`scp.core.api_utils.fetch_with_retry`) call the same idempotent gate.
Do NOT add a new raw HTTP call-site — the static regression gate in
tests/T03_capability/test_egress_enforcement.py fails on raw
urllib/requests/httpx calls outside the gated modules.
"""
from __future__ import annotations

import copy
import ipaddress
import logging
import os
import socket
import ssl
import string
import threading
import urllib.error
import urllib.parse
import urllib.request
import warnings
import weakref
from typing import Any

logger = logging.getLogger(__name__)

# Allowed URL schemes for outbound HTTP requests
ALLOWED_SCHEMES = frozenset({"http", "https"})

# [SEC-A] Redirect policy shared by every SCP HTTP stack (safe_urlopen,
# url_fetcher._SafeRedirectHandler, question_fetchers._common requests
# hop loop, DirectAPIVerifier._session_get):
#   * at most SAFE_MAX_REDIRECT_HOPS 3xx hops (stdlib default is 10),
#   * every hop re-runs enforce_egress_policy + validate_url,
#   * hop>0 may land on an internal IP only when it is the SAME AUTHORITY as
#     the actual initial URL (scheme + normalized host + effective port) and
#     the caller passed allow_internal=True,
#   * Authorization/Cookie/Cookie2 are scrubbed whenever the authority changes.
SAFE_MAX_REDIRECT_HOPS = 5
_DEFAULT_PORTS = {"http": 80, "https": 443}
# A redirect replay must never consume an unbounded caller-controlled stream.
# This is deliberately a bounded, local buffer; exceeding it fails closed before
# the first driver so a 307/308 can never silently replay an empty entity.
_MAX_REPLAYABLE_BODY_BYTES = 10 * 1024 * 1024
_BODY_ENTITY_HEADERS = frozenset(
    {
        "content-length",
        "content-type",
        "content-encoding",
        "content-language",
        "content-location",
        "content-md5",
        "content-range",
        "digest",
        "content-digest",
        "trailer",
        "transfer-encoding",
        "expect",
    }
)

# Credential headers that must never ride a cross-host redirect (CWE-522
# class: urllib's redirect_request copies req.headers — including a
# constructor-supplied Authorization — verbatim to the new host, which
# leaked LLM bearer tokens on `allow_internal=True` provider calls).
CREDENTIAL_HEADERS: tuple[str, ...] = ("Authorization", "Cookie", "Cookie2")
_CREDENTIAL_HEADER_NAMES = frozenset(h.lower() for h in CREDENTIAL_HEADERS)

# [EE] Egress modes — mirror scp/llm_gateway/egress_policy.py _DENY_MODES so
# gateway and generic fetchers share one vocabulary. Deviation from the
# gateway: an UNKNOWN mode is only fail-closed here when production mode is
# explicitly declared (task contract: dev behavior must not be tightened).
_EGRESS_DENY_MODES = frozenset({"deny", "offline", "disabled"})
_EGRESS_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})  # mirrors production_guard._TRUE


class EgressDeniedError(PermissionError, ValueError):
    """Raised when SCP_EGRESS_MODE forbids contacting a URL.

    Inherits BOTH base classes on purpose:
      - PermissionError — capability semantics: callers may treat egress
        denial like a denied permission (task contract, EE agent).
      - ValueError — the canonical fetchers (`url_fetcher._safe_fetch_url`,
        `api_utils.fetch_with_retry`) document "policy violation raises
        ValueError"; keeping that contract means every existing
        `except ValueError` fail path (graceful skip, no retry) keeps
        working — no behavior change for denial-unaware callers.
    """

    def __init__(self, url: str, reason: str):
        self.url = url
        self.reason = reason
        super().__init__(f"egress denied for {url!r}: {reason}")


def _normalize_egress_host(hostname: str | None) -> str:
    """Normalize a hostname, accepting bracketed or host:port spellings."""
    value = str(hostname or "").strip()
    if value.startswith("[") and "]" in value:
        value = value[1:value.index("]")]
    elif value.count(":") == 1:
        host, port = value.rsplit(":", 1)
        if port.isdigit():
            value = host
    return value.strip("[]").lower().rstrip(".")


def _is_loopback_host(hostname: str | None) -> bool:
    """True only for literal loopback: name in the loopback set or an IP
    literal whose ipaddress.is_loopback is True (covers 127.1, [::1] and
    integer spellings). Hostnames are intentionally NOT DNS-resolved here —
    resolution-based SSRF blocking stays in validate_url/_is_private_ip."""
    host = _normalize_egress_host(hostname)
    if not host:
        return False
    if host in _EGRESS_LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _egress_allowlist_hosts() -> frozenset[str]:
    raw = os.environ.get("SCP_EGRESS_ALLOWLIST", "")
    return frozenset(
        _normalize_egress_host(item) for item in raw.split(",") if item.strip()
    )


def _production_mode_declared() -> bool:
    return os.environ.get("SCP_PRODUCTION_MODE", "0").strip().lower() in _TRUE_VALUES


def enforce_egress_policy(
    url: str | urllib.request.Request,
    extra_allowed_hosts: "frozenset[str] | set[str] | None" = None,
) -> None:
    """Fail-closed egress gate driven by SCP_EGRESS_MODE. Idempotent: this is
    a pure check, calling it twice (e.g. `fetch_with_retry` →
    `_safe_fetch_url` → `safe_urlopen`) is harmless.

    Raises EgressDeniedError when the configured mode forbids contacting
    `url`. Loopback (127.0.0.1/::1/localhost and IP-literal loopback) is
    always allowed in every mode so internal services and self-probes keep
    working. Non-HTTP(S) schemes are left to validate_url's scheme
    allowlist — this gate only decides network egress.

    ``extra_allowed_hosts`` lets a call site contribute ITS OWN operator
    allowlist (e.g. the LLM gateway's ``SCP_LLM_EGRESS_ALLOWLIST``) to the
    mode=allowlist decision for its own traffic. It can only widen the
    allowlist branch for that one call site: deny modes still deny every
    non-loopback host, the dev default is unchanged, and every other call
    site (no argument) keeps the exact generic ``SCP_EGRESS_ALLOWLIST``
    behavior.
    """
    url_str = url.full_url if isinstance(url, urllib.request.Request) else str(url)
    mode = os.environ.get("SCP_EGRESS_MODE", "").strip().lower()
    if not mode and not _production_mode_declared():
        return  # dev default: no restriction beyond the SSRF checks
    try:
        parsed = urllib.parse.urlparse(url_str)
    except Exception:  # unparseable under an explicit mode → fail closed
        raise EgressDeniedError(url_str, f"unparseable URL (SCP_EGRESS_MODE={mode!r})") from None
    scheme = (parsed.scheme or "").lower()
    hostname = _normalize_egress_host(parsed.hostname)
    if scheme not in ALLOWED_SCHEMES:
        return  # not an HTTP(S) egress decision; validate_url rejects the scheme
    if _is_loopback_host(hostname):
        return
    if mode in _EGRESS_DENY_MODES:
        raise EgressDeniedError(
            url_str, f"SCP_EGRESS_MODE={mode} blocks all non-loopback hosts"
        )
    if mode == "allowlist":
        extra = {h for h in (extra_allowed_hosts or ()) if h}
        if hostname and (hostname in _egress_allowlist_hosts() or hostname in extra):
            return
        raise EgressDeniedError(
            url_str, f"host {hostname!r} not in SCP_EGRESS_ALLOWLIST (mode=allowlist)"
        )
    if _production_mode_declared():
        raise EgressDeniedError(
            url_str,
            f"SCP_EGRESS_MODE={mode!r} is not deny/allowlist — fail closed in production",
        )
    # Unknown mode + dev → keep historical behavior (no new restriction).

# Disallowed IP ranges (RFC1918 + loopback + link-local + multicast + reserved)
def _is_private_ip(host: str) -> bool:
    """Return True if host resolves to / is a private or loopback IP."""
    if not host:
        return True
    # Strip port
    if ":" in host and not host.startswith("["):
        host = host.rsplit(":", 1)[0]
    host = host.strip("[]")
    try:
        # Already an IP literal?
        try:
            ip = ipaddress.ip_address(host)
            return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
        except ValueError as e:
            logger.warning(f"Silent except: {e}")
        # Resolve hostname
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror:
            logger.debug('_is_private_ip: socket.gaierror ignored', exc_info=True)
            return True  # unresolvable = treat as unsafe
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return True
        return False
    except Exception:
        logger.warning('_is_private_ip: Exception not handled', exc_info=True)
        return True


def validate_url(url: str, *, allow_internal: bool = False) -> urllib.parse.ParseResult:
    """Validate URL scheme + (optionally) internal IP. Raises ValueError on disallowed."""
    if not isinstance(url, str) or not url:
        raise ValueError("URL must be a non-empty string")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise ValueError(f"URL scheme '{parsed.scheme}' not in allowlist {sorted(ALLOWED_SCHEMES)}")
    if not parsed.hostname:
        raise ValueError("URL missing hostname")
    if not allow_internal and _is_private_ip(parsed.hostname):
        raise ValueError(f"URL host '{parsed.hostname}' resolves to internal/private IP — blocked")
    return parsed


# ---------------------------------------------------------------------------
# [SEC-A] Per-hop redirect revalidation — closes the 302-to-metadata SSRF
# and the cross-host bearer-token leak of the DEFAULT urllib opener, which
# followed up to 10 cross-host hops (max_redirections=10), never re-checked
# SCP_EGRESS_MODE / the private-IP boundary after hop 0, allowed ftp://
# redirect targets, and copied Authorization/Cookie to the new host.
# ---------------------------------------------------------------------------

def _authority(url: str) -> tuple[str, str, int]:
    """Return the canonical redirect authority for ``url``.

    The authority deliberately includes scheme and effective port.  A port
    change on the same hostname is a different service/security boundary, and
    an omitted port is normalized to the scheme default (80/443).
    """
    try:
        parsed = urllib.parse.urlparse(url)
        scheme = (parsed.scheme or "").lower()
        host = _normalize_egress_host(parsed.hostname)
        if not scheme or not host:
            return "", "", 0
        try:
            port = parsed.port
        except ValueError:
            return "", "", 0
        effective_port = port if port is not None else _DEFAULT_PORTS.get(scheme, 0)
        return scheme, host, effective_port
    except (TypeError, ValueError):
        return "", "", 0


def _hop_hostname(url: str) -> str:
    """Normalized hostname of a URL ('' when absent)."""
    return _authority(url)[1]


def same_egress_host(previous_url: str, new_url: str) -> bool:
    """True when two URLs share the same canonical authority.

    Despite the historical helper name, this is intentionally stricter than a
    hostname-only comparison: scheme and effective port are part of the
    egress/security boundary.
    """
    previous = _authority(previous_url)
    current = _authority(new_url)
    return bool(previous[0]) and previous == current


def validate_redirect_target(
    newurl: str,
    *,
    allow_internal: bool = False,
    origin_url: str | None = None,
    origin_host: str | None = None,
) -> urllib.parse.ParseResult:
    """Re-validate ONE redirect hop target before the client connects to it.

    Single canonical per-hop policy shared by:
      - `_RevalidatingRedirectHandler` (safe_urlopen),
      - `scp.core.url_fetcher._SafeRedirectHandler` (adds IP pinning on top),
      - the requests hop loops in `question_fetchers._common` /
        `DirectAPIVerifier._session_get` (allow_internal stays False there).

    Ordering mirrors `safe_urlopen` hop 0: egress gate first (deterministic,
    before any DNS), then scheme/hostname/private-IP validation.

    [SEC-A tightening] ``allow_internal`` describes the OPERATOR intent for
    the ORIGIN (hop 0) authority only. A hop>0 target may be internal ONLY
    when it is the same authority as ``origin_url`` (scheme + normalized host
    + effective port, e.g. an Ollama self-redirect at 127.0.0.1:11434).
    ``origin_host`` is retained only as a compatibility fallback for callers
    that have no URL; redirect handlers must pass ``origin_url`` derived from
    the actual request URL and must never use caller-supplied
    ``Request.origin_req_host`` as authority.

    Raises:
        EgressDeniedError (ValueError + PermissionError subclass) — the
            redirect target violates SCP_EGRESS_MODE.
        ValueError — disallowed scheme (incl. ftp:// / file:// downgrade),
            missing hostname, or an internal target without the same-authority
            allow_internal exception.
    """
    if not newurl or not isinstance(newurl, str):
        raise ValueError("redirect target missing URL")
    # Scheme gate FIRST (mirrors the stdlib check but WITHOUT its ftp://
    # allowance — the stdlib lets ftp redirects through to its FTPHandler).
    scheme = (urllib.parse.urlparse(newurl).scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise ValueError(f"redirect scheme not allowed: {scheme!r}")
    enforce_egress_policy(newurl)
    if origin_url is not None:
        same_origin = same_egress_host(origin_url, newurl)
    elif origin_host:
        # A hostname without scheme/effective port is not enough to authorize
        # an internal redirect.  Keep the legacy parameter for API shape, but
        # fail closed rather than silently reintroducing a port-pivot bypass.
        raise ValueError("redirect origin authority is required")
    else:
        same_origin = False
    hop_allow_internal = bool(allow_internal) and same_origin
    return validate_url(newurl, allow_internal=hop_allow_internal)


def strip_credentials_on_host_change(
    headers: dict[str, str], previous_url: str, new_url: str
) -> dict[str, str]:
    """Return `headers` without credential names when the redirect crosses
    authorities; same-authority chains keep them (origin service may legitimately need
    them). Dict flavor for the requests-based hop loops."""
    if same_egress_host(previous_url, new_url):
        return dict(headers)
    # Scheme/host/effective-port changes are all authority changes.
    return {k: v for k, v in headers.items() if k.lower() not in _CREDENTIAL_HEADER_NAMES}


def session_without_credentials(session: Any, *, clear_credentials: bool = True) -> Any:
    """Clone a requests-like session with deterministic proxy behavior.

    ``requests.Session`` merges session headers and cookies after callers
    scrub per-request headers.  A shallow clone retains adapters/pools while
    allowing a cross-host hop to clear both stores without mutating the shared
    session used by other requests.  Same-host hops can retain credentials by
    passing ``clear_credentials=False``.
    """
    isolated = copy.copy(session)
    if hasattr(session, "headers"):
        isolated.headers = session.headers.copy()
        if clear_credentials:
            for key in list(isolated.headers):
                if key.lower() in _CREDENTIAL_HEADER_NAMES:
                    del isolated.headers[key]
    if hasattr(session, "cookies"):
        isolated.cookies = session.cookies.copy()
        if clear_credentials:
            isolated.cookies.clear()
    if clear_credentials and hasattr(isolated, "auth"):
        isolated.auth = None
    if hasattr(isolated, "trust_env"):
        isolated.trust_env = False
    if hasattr(isolated, "proxies"):
        isolated.proxies = {}
    return isolated


def _scrub_request_credentials(new_req: urllib.request.Request, previous_url: str, new_url: str) -> None:
    """Request-object flavor of `strip_credentials_on_host_change`: removes
    Authorization/Cookie/Cookie2 from BOTH header dicts (the stdlib
    redirect_request copies constructor headers into Request.headers, so
    `unredirected_hdrs` alone is NOT a safe store)."""
    if same_egress_host(previous_url, new_url):
        return
    for store in (new_req.headers, new_req.unredirected_hdrs):
        for key in [k for k in store if k.lower() in _CREDENTIAL_HEADER_NAMES]:
            del store[key]


def _scrub_request_authority_headers(
    new_req: urllib.request.Request, previous_url: str, new_url: str
) -> None:
    """Remove headers whose value is bound to the old request authority.

    ``urllib`` normally generates ``Host`` from the new URL, but only when the
    caller did not supply an explicit Host header.  Retaining the old value on
    a cross-authority redirect sends a stale authority to the target server and
    can route the request to the wrong virtual host.  Same-authority redirects
    deliberately retain an explicit Host because some provider APIs require a
    contract-specific value.  Both urllib header stores are scrubbed because
    either store is merged into the driver request.
    """
    if same_egress_host(previous_url, new_url):
        return
    for store in (new_req.headers, new_req.unredirected_hdrs):
        for key in [k for k in store if k.lower() == "host"]:
            del store[key]


# Request objects are weak-referenceable and the handler may be reused for
# multiple independent opens. Keep chain provenance outside caller-controlled
# Request attributes so a pre-populated ``_scp_origin_url`` cannot influence a
# redirect decision.  Redirect counts live on an internal cloned Request, not
# on the caller's Request, so reusing one caller object starts a fresh chain.
_REDIRECT_ORIGINS: weakref.WeakKeyDictionary[urllib.request.Request, str] = (
    weakref.WeakKeyDictionary()
)
_REDIRECT_COUNTS: weakref.WeakKeyDictionary[urllib.request.Request, dict[str, int]] = (
    weakref.WeakKeyDictionary()
)
_REDIRECT_ORIGINS_LOCK = threading.Lock()


def _coerce_body_chunk(chunk: Any) -> bytes:
    """Convert one stream/iterable body chunk to bytes or fail closed."""
    if isinstance(chunk, bytes):
        return chunk
    if isinstance(chunk, (bytearray, memoryview)):
        return bytes(chunk)
    if isinstance(chunk, str):
        try:
            return chunk.encode("iso-8859-1")
        except UnicodeEncodeError as exc:
            raise ValueError(
                "redirect body text is not encodable as ISO-8859-1"
            ) from exc
    try:
        return bytes(memoryview(chunk))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"redirect body chunk is not bytes-like: {type(chunk).__name__}"
        ) from exc


def _snapshot_replayable_body(data: Any) -> Any:
    """Return a bounded replayable snapshot of a urllib request body.

    Bytes-like values and strings are already replayable.  File-like and
    iterable bodies are consumed into a bounded bytes snapshot before the
    first network driver runs and the cloned Request owns the snapshot, so a
    307/308 hop replays the FULL entity instead of a silently empty one.
    Replayability requires ``seek``: a stream that cannot be rewound cannot
    be guaranteed to survive being consumed by the first driver, so such a
    stream fails closed here with a clear ValueError (SEC-A v5: NEVER send an
    empty body silently).  Any read/size/type failure raises the same way.
    """
    if data is None or isinstance(data, (bytes, str)):
        if isinstance(data, str):
            try:
                if len(data.encode("iso-8859-1")) > _MAX_REPLAYABLE_BODY_BYTES:
                    raise ValueError(
                        f"redirect body exceeds {_MAX_REPLAYABLE_BODY_BYTES} bytes"
                    )
            except UnicodeEncodeError as exc:
                raise ValueError(
                    "redirect body text is not encodable as ISO-8859-1"
                ) from exc
        return data
    if isinstance(data, (bytearray, memoryview)):
        body = bytes(data)
        if len(body) > _MAX_REPLAYABLE_BODY_BYTES:
            raise ValueError(
                f"redirect body exceeds {_MAX_REPLAYABLE_BODY_BYTES} bytes"
            )
        return body

    reader = getattr(data, "read", None)
    original_position = None
    have_position = False
    if callable(reader):
        # [SEC-A v5] Replayability REQUIRES seek.  A read-only stream could
        # still be drained by the first driver before any replay decision;
        # instead of gambling on an empty replay, fail closed here — BEFORE
        # any socket is opened — with a clear ValueError.
        seek = getattr(data, "seek", None)
        if not callable(seek):
            raise ValueError(
                "redirect body stream is not replayable "
                f"({type(data).__name__} has no seek); refusing to send a "
                "silently empty body"
            )
        try:
            original_position = data.tell()
            have_position = True
        except (AttributeError, OSError, TypeError, ValueError):
            # Position unknown → rewind to 0 for the snapshot restore below.
            original_position = 0
        chunks: list[bytes] = []
        total = 0
        try:
            while True:
                try:
                    chunk = reader(64 * 1024)
                except TypeError as exc:
                    raise ValueError(
                        "redirect body stream does not support bounded reads"
                    ) from exc
                if chunk in (b"", ""):
                    break
                if chunk is None:
                    raise ValueError("redirect body stream returned None")
                encoded = _coerce_body_chunk(chunk)
                total += len(encoded)
                if total > _MAX_REPLAYABLE_BODY_BYTES:
                    raise ValueError(
                        f"redirect body exceeds {_MAX_REPLAYABLE_BODY_BYTES} bytes"
                    )
                chunks.append(encoded)
            return b"".join(chunks)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"redirect body could not be buffered: {exc}") from exc
        finally:
            if have_position:
                try:
                    data.seek(original_position)
                except (AttributeError, OSError, TypeError, ValueError) as exc:
                    raise ValueError(
                        "redirect body stream could not be restored after buffering"
                    ) from exc

    try:
        iterator = iter(data)
    except TypeError as exc:
        raise ValueError(
            f"redirect body is not replayable: {type(data).__name__}"
        ) from exc
    chunks = []
    total = 0
    try:
        for chunk in iterator:
            encoded = _coerce_body_chunk(chunk)
            total += len(encoded)
            if total > _MAX_REPLAYABLE_BODY_BYTES:
                raise ValueError(
                    f"redirect body exceeds {_MAX_REPLAYABLE_BODY_BYTES} bytes"
                )
            chunks.append(encoded)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"redirect body could not be buffered: {exc}") from exc
    return b"".join(chunks)


def _clone_request_for_open(
    req: urllib.request.Request, *, data_override: Any = None, use_override: bool = False
) -> urllib.request.Request:
    """Clone a caller Request and initialize a private replay/redirect chain.

    The body is snapshotted via `_snapshot_replayable_body` (file-like →
    replayable bytes, non-replayable → fail closed).  Stale auto-generated
    entity headers (Content-length / Transfer-encoding) are dropped from BOTH
    urllib header stores so http.client derives them from the ACTUAL chain
    body — copying the caller's old Content-length verbatim would advertise a
    length the replayed snapshot does not have.
    """
    data = data_override if use_override else req.data
    data = _snapshot_replayable_body(data)
    method = getattr(req, "method", None)
    headers = {
        key: value
        for key, value in req.headers.items()
        if key.lower() not in ("content-length", "transfer-encoding")
    }
    clone = urllib.request.Request(
        req.full_url,
        data=data,
        headers=headers,
        origin_req_host=req.origin_req_host,
        unverifiable=req.unverifiable,
        method=method,
    )
    for key, value in req.unredirected_hdrs.items():
        if key.lower() in ("content-length", "transfer-encoding"):
            continue
        clone.add_unredirected_header(key, value)
    # Deliberately do not copy caller-controlled redirect_dict/private markers.
    return clone


def _copy_request_for_redirect(
    req: urllib.request.Request,
    newurl: str,
    code: int,
    msg: str,
    headers,
    fp,
) -> urllib.request.Request:
    """Build a redirect Request with explicit urllib-compatible semantics.

    CPython's handler rejects POST+307/308 and drops the body for POST+301/302/
    303.  SCP follows the established urllib behavior for the latter three
    statuses while preserving method, body and all headers for 307/308.  The
    explicit construction also preserves ``unredirected_hdrs`` and ensures
    that a replayable body snapshot is used for every preserved-entity hop.
    """
    method = req.get_method()
    preserve_entity = code in (307, 308)
    preserve_post = code in (301, 302, 303) and method == "POST"
    if method not in ("GET", "HEAD") and not preserve_entity and not preserve_post:
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)

    newurl = newurl.replace(" ", "%20")
    all_headers = dict(req.headers)
    all_unredirected = dict(req.unredirected_hdrs)
    if not preserve_entity:
        for store in (all_headers, all_unredirected):
            for key in list(store):
                if key.lower() in _BODY_ENTITY_HEADERS:
                    del store[key]
    data = _snapshot_replayable_body(req.data) if preserve_entity else None
    new_method = method if preserve_entity else None
    new = urllib.request.Request(
        newurl,
        data=data,
        headers=all_headers,
        origin_req_host=req.origin_req_host,
        unverifiable=True,
        method=new_method,
    )
    for key, value in all_unredirected.items():
        new.add_unredirected_header(key, value)
    _scrub_request_credentials(new, req.full_url, newurl)
    _scrub_request_authority_headers(new, req.full_url, newurl)
    with _REDIRECT_ORIGINS_LOCK:
        origin = _REDIRECT_ORIGINS.get(req, req.full_url)
        _REDIRECT_ORIGINS[new] = origin
    return new


class _RevalidatingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """SCP redirect policy for `safe_urlopen`: every 3xx hop re-runs
    `enforce_egress_policy` + `validate_url` (via `validate_redirect_target`),
    only http/https targets, at most `max_redirections` hops, and
    Authorization/Cookie/Cookie2 are scrubbed whenever the authority changes.

    WHY this exists: the default `urllib.request.urlopen` opener follows up
    to 10 cross-host 302/303/307/308 hops with NO re-check of the egress
    policy or the private-IP boundary — an allowlisted host answering
    `302 → http://169.254.169.254/` (cloud metadata) or
    `302 → http://127.0.0.1:8000` was a full SSRF + allowlist-deny bypass —
    and it copies req.headers (bearer tokens!) into the cross-host redirect
    request. (CONFIRMED static, AUDIT-20260909, two independent readers.)

    Policy violations raise ValueError (EgressDeniedError is a ValueError
    subclass), so every existing `except ValueError` graceful-skip call-site
    keeps working. Non-2xx responses that are NOT redirected still surface as
    HTTPError, like urlopen.
    """

    # Total 3xx hop budget for the whole chain (stdlib default 10 is too
    # permissive; SCP caps at SAFE_MAX_REDIRECT_HOPS) and the per-URL repeat
    # budget used for loop detection.
    max_redirections = SAFE_MAX_REDIRECT_HOPS
    max_repeats = SAFE_MAX_REDIRECT_HOPS

    def __init__(self, allow_internal: bool = False, *, origin_url: str | None = None):
        self._allow_internal = bool(allow_internal)
        # Kept for API compatibility, but redirect provenance is always
        # initialized from the actual Request.full_url and then held in the
        # module-owned chain map.  Constructor input cannot override a caller
        # Request's actual authority.
        self._origin_url = origin_url

    def validate_redirect_hop(
        self,
        req: urllib.request.Request,
        newurl: str,
        *,
        origin_url: str | None = None,
    ):
        """Per-hop policy hook. Subclasses (url_fetcher._SafeRedirectHandler)
        extend it with resolve+pin without re-implementing the checks."""
        # The first request's authority is always its actual full URL.  Do
        # not read caller-controlled private attributes or origin_req_host.
        # Subsequent Requests are mapped by _copy_request_for_redirect, which
        # gives this reusable handler safe per-chain provenance.
        with _REDIRECT_ORIGINS_LOCK:
            chain_origin = _REDIRECT_ORIGINS.get(req, req.full_url)
        return validate_redirect_target(
            newurl,
            allow_internal=self._allow_internal,
            # Derive authority from trusted Request/handler state, never from
            # caller-supplied Request.origin_req_host or _scp_origin_url.
            origin_url=chain_origin,
        )

    def http_error_302(self, req, fp, code, msg, headers):
        """Adapted from CPython 3.12 HTTPRedirectHandler.http_error_302 with
        three SCP deviations: (1) the ftp:// allowance is removed, (2) the
        hop budget raises ValueError instead of HTTPError, (3) the full gate
        is re-run on the joined target before redispatch."""
        if "location" in headers:
            newurl = headers["location"]
        elif "uri" in headers:
            newurl = headers["uri"]
        else:
            # No Location: not handled here → HTTPDefaultErrorHandler turns
            # the 3xx into HTTPError (non-2xx contract preserved).
            return None

        urlparts = urllib.parse.urlparse(newurl)
        # Relative Locations arrive with scheme == ''; urljoin below resolves
        # them against req.full_url (already validated at this hop or the
        # previous one), and the final scheme re-check in
        # validate_redirect_target rejects anything non-http(s).
        if urlparts.scheme not in ("", "http", "https"):
            raise ValueError(f"redirect scheme not allowed: {urlparts.scheme!r}")
        if not urlparts.path and urlparts.netloc:
            urlparts = list(urlparts)  # type: ignore[assignment]
            urlparts[2] = "/"
        newurl = urllib.parse.urlunparse(urlparts)  # type: ignore[arg-type]
        # Same normalization as the stdlib: recover the ISO-8859-1 bytes and
        # percent-encode non-ASCII / unsafe characters before joining.
        newurl = urllib.parse.quote(newurl, encoding="iso-8859-1", safe=string.punctuation)
        newurl = urllib.parse.urljoin(req.full_url, newurl)

        # Redirect counts live in module-owned state keyed by the private
        # per-open Request.  Never read or write caller-controlled
        # ``req.redirect_dict``: a reused Request must start a fresh chain.
        with _REDIRECT_ORIGINS_LOCK:
            visited = _REDIRECT_COUNTS.get(req)
            if visited is None:
                visited = {}
                _REDIRECT_COUNTS[req] = visited
        if sum(visited.values()) >= self.max_redirections:
            raise ValueError(f"too many redirects (maximum {self.max_redirections})")
        if visited.get(newurl, 0) >= self.max_repeats:
            raise ValueError(f"redirect loop detected for {newurl!r}")

        # [SEC-A] THE point of this class: re-run the whole SCP gate on the
        # hop target BEFORE any socket is opened to it.
        self.validate_redirect_hop(req, newurl)

        new = self.redirect_request(req, fp, code, msg, headers, newurl)
        if new is None:
            return None
        visited[newurl] = visited.get(newurl, 0) + 1
        with _REDIRECT_ORIGINS_LOCK:
            _REDIRECT_COUNTS[new] = visited

        # Don't close fp until we are sure we won't reuse it with HTTPError.
        fp.read()
        fp.close()
        return self.parent.open(new, timeout=req.timeout)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return _copy_request_for_redirect(req, newurl, code, msg, headers, fp)


def safe_urlopen(
    url: str | urllib.request.Request,
    data: Any = None,
    timeout: float = 30,
    *,
    allow_internal: bool = False,
    cafile: str | None = None,
    capath: str | None = None,
    cadefault: bool = False,
    context: ssl.SSLContext | None = None,
    **kwargs: Any,
):
    """Drop-in replacement for urllib.request.urlopen() that enforces B310 safety.  # nosec B310 — URL validated by SCP

    Args:
        url: URL string or Request object.
        timeout: Request timeout.
        allow_internal: Set True to allow internal/private IPs AT HOP 0 ONLY
            (e.g. for Ollama at 127.0.0.1). [SEC-A] With the redirect
            tightening, an allow_internal=True chain may still land on an
            internal IP at hop>0 ONLY when it stays on the same origin
            authority (scheme, normalized host, effective port);
            a cross-host internal/link-local/metadata redirect raises even
            with allow_internal=True.
        **kwargs: Forwarded to the safe opener's .open().  # nosec B310 — URL validated by SCP

    Returns:
        HTTP response object (same as urllib.request.urlopen).  # nosec B310 — URL validated by SCP

    [SEC-A] Redirects are NO LONGER the default stdlib policy: this uses a
    custom opener whose `_RevalidatingRedirectHandler` re-runs
    enforce_egress_policy + validate_url on EVERY hop, caps the chain at
    SAFE_MAX_REDIRECT_HOPS (5), rejects non-http(s) redirect targets
    (including the ftp:// the stdlib still allows and file:// regardless of
    what the stdlib happens to block), and scrubs Authorization/Cookie/
    Cookie2 when the authority changes. Policy violations raise ValueError
    (EgressDeniedError is a ValueError subclass) so the `except ValueError`
    contract of all call-sites holds; non-2xx responses still raise
    HTTPError.
    """
    if isinstance(url, urllib.request.Request):
        url_str = url.full_url
    else:
        url_str = str(url)
    if cafile or capath or cadefault:
        warnings.warn(
            "cafile, capath and cadefault are deprecated, use an SSL context instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        if context is not None:
            raise ValueError("You can't pass both context and cafile/capath/cadefault")
        context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=cafile, capath=capath)
        context.set_alpn_protocols(["http/1.1"])
    # [SEC-A v5] Snapshot any explicitly-passed body BEFORE the first driver:
    # a 307/308 replays the full entity and a non-replayable stream fails
    # closed here instead of silently sending an empty body.
    if data is not None:
        data = _snapshot_replayable_body(data)
    if isinstance(url, urllib.request.Request):
        # [SEC-A v5] Open a PRIVATE clone per top-level safe_urlopen call:
        #   * fresh redirect-chain state per call — reusing one caller Request
        #     used to accumulate per-URL redirect counters ACROSS calls and
        #     raised "too many redirects" from the 6th call of /r→/ok;
        #   * a replayable body snapshot — a file-like body used to be
        #     consumed by the first driver and then replayed EMPTY on a
        #     307/308 (Content-length 0, method POST intact);
        #   * NO mutation of the caller Request (the old code assigned
        #     url.data = data in place) — safe for sequential AND concurrent
        #     reuse from multiple threads.
        # Deliberately do not register the CALLER Request in the chain maps:
        # it is never opened by this function anymore, and its caller-side
        # attributes must stay untouched.
        chain_req = _clone_request_for_open(
            url, data_override=data, use_override=data is not None
        )
    elif data is not None:
        chain_req = urllib.request.Request(url_str, data=data)
    else:
        chain_req = None
    # Initialize provenance from the actual input URL before any redirect
    # callback, keyed by the PRIVATE per-open Request. This ignores any
    # caller-supplied private marker and cannot collide across calls/threads.
    if chain_req is not None:
        with _REDIRECT_ORIGINS_LOCK:
            _REDIRECT_ORIGINS[chain_req] = chain_req.full_url
    # [EE] Egress gate FIRST: deterministic EgressDeniedError (a ValueError)
    # under explicit SCP_EGRESS_MODE, before any DNS resolution/SSRF check.
    enforce_egress_policy(url)
    validate_url(url_str, allow_internal=allow_internal)
    # [SEC-A] Previously this called urllib.request.urlopen — the DEFAULT
    # opener follows up to 10 cross-host 3xx hops with no SCP policy re-check
    # (302 → 169.254.169.254 SSRF + allowlist bypass, plus bearer-token leak).
    # build_opener detects the HTTPRedirectHandler SUBCLASS below and skips
    # the default redirect handler, so every hop goes through SCP policy.
    https_handler = urllib.request.HTTPSHandler(context=context) if context is not None else None
    opener_handlers = [
        urllib.request.ProxyHandler({}),
        _RevalidatingRedirectHandler(
            allow_internal=allow_internal,
            origin_url=url_str,
        ),
    ]
    if https_handler is not None:
        opener_handlers.append(https_handler)
    opener = urllib.request.build_opener(*opener_handlers)
    if chain_req is not None:
        # The private chain Request already carries the snapshotted body; do
        # NOT pass data= again (that would mutate the clone after snapshotting
        # and re-run body handling inside the stdlib).
        return opener.open(chain_req, timeout=timeout, **kwargs)  # hop-0 validated above; every further hop validated by the redirect handler  # nosec B310  # noqa: S310
    return opener.open(url_str, timeout=timeout, **kwargs)  # hop-0 validated above; every further hop validated by the redirect handler  # nosec B310  # noqa: S310
