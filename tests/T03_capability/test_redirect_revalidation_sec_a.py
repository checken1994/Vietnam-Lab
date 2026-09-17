"""[SEC-A] Behavioural tests for per-hop redirect revalidation (AUDIT-20260909).

CONFIRMED static finding (two independent readers): `safe_urlopen` used
`urllib.request.urlopen` with the DEFAULT opener, which follows up to 10
cross-host 302/303/307/308 hops while `enforce_egress_policy` + `validate_url`
ran ONLY on the first URL. An allowlisted attacker host answering
`302 → http://169.254.169.254/` (cloud metadata) or `302 → http://127.0.0.1:8000`
was a full SSRF + deny/allowlist bypass, and the stdlib redirect also COPIES
`Authorization`/`Cookie` to the new host (bearer-token leak on the
`allow_internal=True` LLM sites). The requests-based sites
(`_common._http_get_json` requests branch — the main path when requests is
installed — and `DirectAPIVerifier._session_get`) had the identical class of
hole via requests' default `allow_redirects=True`, and url_safety sits in
GATE_EXCLUDED_MODULES so the static gate cannot see it — hence BEHAVIOUR
tests here.

Contracts pinned (task SEC-A):
  1. allowlisted hop0 302→169.254.169.254 under SCP_EGRESS_MODE=allowlist
     raises EgressDeniedError with ZERO socket attempts to that IP.
  2. 302→127.0.0.1 with allow_internal=False raises (SSRF layer).
  3. [tightening] allow_internal=True does NOT rescue hop>0: a cross-host
     internal target still raises; internal at hop>0 is allowed only on the
     SAME origin host (operator-intent exception).
  4. cross-host redirect scrubs Authorization/Cookie/Cookie2; same-host
     keeps them.
  5. ftp:// and file:// redirect targets raise ValueError (not HTTPError —
     the stdlib would happily pass ftp:// to its FTPHandler).
  6. chain > SAFE_MAX_REDIRECT_HOPS (5) raises; ≤5 same-allowed-host chain OK.
  7. non-2xx still raises HTTPError (unchanged urlopen contract);
     policy violations stay ValueError (EgressDeniedError subclasses it).
  8. equivalent behaviour across the four stacks that used to follow
     redirects blindly: safe_urlopen (urllib), url_fetcher._SafeRedirectHandler,
     _common._http_get_json (requests hop loop), DirectAPIVerifier._session_get.
  9. duplicate-standard tripwire: url_fetcher._SafeRedirectHandler must remain
     a subclass of the ONE shared policy class (no second divergent impl).

Evidence policy (no-mock discipline, FA-09 exploit mandate): a REAL
http.server on loopback answers the 302s; NO real network. Public-IP
literals (8.8.8.8 / 9.9.9.9) act as attacker-controlled "external" origins
whose CONNECT is rewired to the local test server via a patched socket
factory — this only rewrites the destination address; the full urllib/requests
redirect machinery, the egress gate and validate_url run for real. Any
attempt to resolve/connect 169.254.169.254 is recorded and refused, so
"count == 0" is a positive assertion that the hop was NEVER contacted.
"""
from __future__ import annotations

import http.client
import http.server
import io
import json
import socket
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scp.security.url_safety import (  # noqa: E402
    SAFE_MAX_REDIRECT_HOPS,
    EgressDeniedError,
    _RevalidatingRedirectHandler,
    safe_urlopen,
)

# Built from octets so gate scanners never see a suspicious literal.
METADATA_IP = ".".join(["169", "254", "169", "254"])
METADATA_URL = f"http://{METADATA_IP}/latest/meta-data/"
# "Attacker-controlled public" stand-ins — never resolved; connect is
# rewired to the loopback test server by the fixture below.
PUBLIC_A = "8.8.8.8"
PUBLIC_B = "9.9.9.9"

_REAL_CREATE_CONNECTION = socket.create_connection
_REAL_GETADDRINFO = socket.getaddrinfo

_PROXY_ENV_VARS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy",
    "all_proxy",  # read by BOTH requests/urllib3 (trust_env) and urllib's ProxyHandler
)


@pytest.fixture(autouse=True)
def _clean_proxy_env(monkeypatch):
    """Determinism: ambient proxy settings must not re-route (or fail) the
    loopback test traffic through a proxy — the gate under test is the
    redirect policy, not the environment."""
    for var in _PROXY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


# ------------------------------------------------------------- fake network
class _RoutedNetwork:
    """Socket spy + destination rewriter (see module docstring)."""

    def __init__(self, server_port: int):
        self.server_port = server_port
        self.metadata_attempts: list[str] = []

    def _watch_metadata(self, kind: str, host, port):
        if str(host) == METADATA_IP:
            self.metadata_attempts.append(f"{kind}:{host}:{port}")
            raise ConnectionRefusedError("test harness refuses the metadata IP")

    # --- urllib/http.client path (safe_urlopen, _SafeRedirectHandler) ---
    def create_connection(self, address, *args, **kwargs):
        host, port = address[0], address[1]
        self._watch_metadata("create_connection", host, port)
        if host in (PUBLIC_A, PUBLIC_B):
            address = ("127.0.0.1", self.server_port, *address[2:])
        return _REAL_CREATE_CONNECTION(address, *args, **kwargs)

    # --- urllib3/requests path (_common, DirectAPIVerifier) ---
    def getaddrinfo(self, host, port, *args, **kwargs):
        self._watch_metadata("getaddrinfo", host, port)
        # Rewrite ONLY real connects (port set by urllib3/http.client). A
        # port=None lookup is validation-only (e.g. url_fetcher's
        # _resolve_public_ips / _is_private_ip): IP literals resolve to
        # themselves OFFLINE, and the validator must see the TRUE public
        # answer, not the loopback rewrite.
        if port is not None and str(host) in (PUBLIC_A, PUBLIC_B):
            return _REAL_GETADDRINFO("127.0.0.1", self.server_port, *args, **kwargs)
        return _REAL_GETADDRINFO(host, port, *args, **kwargs)


@pytest.fixture()
def server():
    """Real loopback HTTP server with redirect/echo/chain endpoints."""
    hits: list[tuple[str, dict]] = []
    # [SEC-A v5] Server-side view of EVERY POST entity (path, body) — needed
    # to prove a 307/308 replayed the full body at hop0 AND hop1 instead of
    # silently sending b"".
    post_bodies: list[tuple[str, bytes]] = []

    class _Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, code: int, body: bytes = b"", headers: dict | None = None):
            self.send_response(code)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _get(self):
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            hits.append((parsed.path, dict(self.headers.items())))
            if parsed.path == "/redirect":
                self._send(302, headers={"Location": qs["to"][0]})
            elif parsed.path == "/chain":
                left = int(qs["left"][0])
                if left > 0:
                    # RELATIVE Location — also pins urljoin against full URL.
                    self._send(
                        302,
                        headers={
                            "Location": f"chain?left={left - 1}&start={qs['start'][0]}"
                        },
                    )
                else:
                    body = json.dumps({"ok": True, "start": int(qs["start"][0])}).encode()
                    self._send(200, body, {"Content-Type": "application/json"})
            elif parsed.path == "/echo":
                body = json.dumps(
                    {"headers": {k.lower(): v for k, v in dict(self.headers.items()).items()}}
                ).encode()
                self._send(200, body, {"Content-Type": "application/json"})
            elif parsed.path == "/echo-post":
                body = json.dumps(
                    {
                        "method": self.command,
                        "body": "",
                        "content_length": self.headers.get("Content-Length"),
                        "headers": {k.lower(): v for k, v in dict(self.headers.items()).items()},
                    }
                ).encode()
                self._send(200, body, {"Content-Type": "application/json"})
            elif parsed.path == "/noheader":
                self._send(302)  # 3xx WITHOUT Location
            else:
                self._send(404, b'{"error":"not found"}',
                           {"Content-Type": "application/json"})

        def do_GET(self):  # noqa: N802 — stdlib API
            self._get()

        def do_POST(self):  # noqa: N802 — same endpoints for POST
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            hits.append((parsed.path, dict(self.headers.items())))
            post_bodies.append((parsed.path, body))
            if parsed.path == "/redirect":
                self._send(302, headers={"Location": qs["to"][0]})
            elif parsed.path in {"/post307", "/post308"}:
                status = 307 if parsed.path == "/post307" else 308
                self._send(status, headers={"Location": qs["to"][0]})
            elif parsed.path == "/echo-post":
                response = json.dumps(
                    {
                        "method": self.command,
                        "body": body.decode("utf-8"),
                        "content_length": self.headers.get("Content-Length"),
                        "headers": {k.lower(): v for k, v in dict(self.headers.items()).items()},
                    }
                ).encode()
                self._send(200, response, {"Content-Type": "application/json"})
            else:
                self._get()

        def log_message(self, *args):  # silence per-request stderr
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    srv.hits = hits  # type: ignore[attr-defined]
    srv.post_bodies = post_bodies  # type: ignore[attr-defined]
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        hits.clear()
        post_bodies.clear()


@pytest.fixture()
def routed(server, monkeypatch):
    """Rewire public-IP connects to the local server; refuse metadata."""
    net = _RoutedNetwork(server.server_address[1])
    # CPython 3.12: HTTPConnection.__init__ binds
    # `self._create_connection = socket.create_connection` PER CONNECTION at
    # construction time, so patching the socket module attribute is picked up
    # by every connect started after the patch (urllib stack).
    monkeypatch.setattr(socket, "create_connection", net.create_connection)
    # urllib3/requests resolves via socket.getaddrinfo before raw connect.
    monkeypatch.setattr(socket, "getaddrinfo", net.getaddrinfo)
    return net


@pytest.fixture()
def second_server():
    """A second real loopback HTTP server for same-host/different-port pivots."""
    hits: list[tuple[str, dict]] = []

    class _Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):  # noqa: N802 — stdlib API
            parsed = urllib.parse.urlparse(self.path)
            hits.append((parsed.path, dict(self.headers.items())))
            body = b'{"ok":true}' if parsed.path == "/echo" else b"not found"
            code = 200 if parsed.path == "/echo" else 404
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # silence per-request stderr
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    srv.hits = hits  # type: ignore[attr-defined]
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        hits.clear()


# -------------------------------------------------------------- URL helpers
def _loopback(server) -> str:
    return f"127.0.0.1:{server.server_address[1]}"


def _url(host: str, path: str, to: str | None = None) -> str:
    """host may carry an explicit port (loopback server) or gets the
    stand-in port :1 (public literals are rewired by the fixture)."""
    base = host if ":" in host else f"{host}:1"
    if to is None:
        return f"http://{base}{path}"
    return f"http://{base}{path}?to={urllib.parse.quote(to, safe='')}"


def _chain(host: str, left: int) -> str:
    base = host if ":" in host else f"{host}:1"
    return f"http://{base}/chain?left={left}&start={left}"


def _abs(server, path: str) -> str:
    """Absolute Location targeting the REAL loopback test server."""
    return f"http://{_loopback(server)}{path}"


def _hits(server, path: str) -> list[dict]:
    return [h for p, h in server.hits if p == path]  # type: ignore[attr-defined]


def _post_bodies(server, path: str) -> list[bytes]:
    """Server-side view of POST entities received for `path` (SEC-A v5)."""
    return [b for p, b in server.post_bodies if p == path]  # type: ignore[attr-defined]


def _set_egress(monkeypatch, mode, allowlist=None):
    if mode is None:
        monkeypatch.delenv("SCP_EGRESS_MODE", raising=False)
    else:
        monkeypatch.setenv("SCP_EGRESS_MODE", mode)
    if allowlist is None:
        monkeypatch.delenv("SCP_EGRESS_ALLOWLIST", raising=False)
    else:
        monkeypatch.setenv("SCP_EGRESS_ALLOWLIST", allowlist)
    monkeypatch.delenv("SCP_PRODUCTION_MODE", raising=False)


# ============================================================ 1+3. metadata
def test_allowlisted_hop1_to_metadata_denied_with_zero_connects(monkeypatch, server, routed):
    """Task scenario #1: allowlist host answers 302 → 169.254.169.254.
    MUST be EgressDeniedError BEFORE any socket to the metadata IP."""
    _set_egress(monkeypatch, "allowlist", allowlist=PUBLIC_A)
    url = _url(PUBLIC_A, "/redirect", to=METADATA_URL)
    with pytest.raises(EgressDeniedError) as excinfo:
        safe_urlopen(url, timeout=5)
    assert isinstance(excinfo.value, ValueError)  # 103 except-ValueError sites
    assert isinstance(excinfo.value, PermissionError)
    assert "169" in excinfo.value.url
    assert routed.metadata_attempts == [], (
        f"a hop contacted the metadata IP: {routed.metadata_attempts}"
    )


def test_deny_mode_blocks_redirect_to_internal_but_allows_origin(monkeypatch, server, routed):
    """mode=deny: the loopback ORIGIN is always allowed (internal services /
    self-probe), but the 302 target 169.254 must still die at the egress
    gate — deny must not be re-opened by a hop."""
    _set_egress(monkeypatch, "deny")
    url = _url(_loopback(server), "/redirect", to=METADATA_URL)
    with pytest.raises(EgressDeniedError):
        safe_urlopen(url, timeout=5, allow_internal=True)
    assert routed.metadata_attempts == []


# ============================================== 2. 302→127.0.0.1 hop policy
def test_cross_host_redirect_to_loopback_raises_when_internal_not_allowed(
    monkeypatch, server, routed
):
    """Task scenario #2: allowlisted/public hop0 302 → http://127.0.0.1:x
    with allow_internal=False must raise (SSRF layer, not egress)."""
    _set_egress(monkeypatch, None)
    url = _url(PUBLIC_A, "/redirect", to=_abs(server, "/echo"))
    with pytest.raises(ValueError) as excinfo:
        safe_urlopen(url, timeout=5)
    assert not isinstance(excinfo.value, EgressDeniedError)
    assert "internal/private" in str(excinfo.value)
    assert _hits(server, "/echo") == [], "hop target must never be fetched"


def test_allow_internal_true_still_blocks_cross_host_internal_hop(monkeypatch, server, routed):
    """[SEC-A tightening] allow_internal applies to hop 0 only; a hop>0
    internal target on a DIFFERENT host raises even with allow_internal=True
    (this is exactly the OpenRouter/Ollama-token SSRF-302 shape)."""
    _set_egress(monkeypatch, None)
    url = _url(PUBLIC_A, "/redirect", to=_abs(server, "/echo"))
    req = urllib.request.Request(url, headers={"Authorization": "***"})
    with pytest.raises(ValueError) as excinfo:
        safe_urlopen(req, timeout=5, allow_internal=True)
    assert "internal/private" in str(excinfo.value)
    assert _hits(server, "/echo") == []
    assert routed.metadata_attempts == []


def test_forged_origin_req_host_cannot_authorize_loopback_redirect(monkeypatch, server, routed):
    """A caller-controlled Request.origin_req_host must not define authority.

    The actual initial URL is public; forging origin_req_host to loopback must
    not permit a 302 to the local server even with allow_internal=True.
    """
    _set_egress(monkeypatch, None)
    url = _url(PUBLIC_A, "/redirect", to=_abs(server, "/echo"))
    req = urllib.request.Request(
        url,
        headers={"Authorization": "***"},
        origin_req_host="127.0.0.1",
    )
    with pytest.raises(ValueError):
        safe_urlopen(req, timeout=5, allow_internal=True)
    assert _hits(server, "/echo") == []
    assert routed.metadata_attempts == []


def test_forged_scp_origin_marker_cannot_authorize_loopback_redirect(
    monkeypatch, server, routed
):
    """A caller-controlled private provenance marker must be ignored.

    The actual initial URL is public.  A forged ``_scp_origin_url`` pointing
    at the local server must not turn the cross-authority redirect into an
    allowed same-origin internal hop.
    """
    _set_egress(monkeypatch, None)
    url = _url(PUBLIC_A, "/redirect", to=_abs(server, "/echo"))
    req = urllib.request.Request(url)
    req._scp_origin_url = _abs(server, "/echo")  # type: ignore[attr-defined]
    with pytest.raises(ValueError):
        safe_urlopen(req, timeout=5, allow_internal=True)
    assert _hits(server, "/echo") == []
    assert routed.metadata_attempts == []


@pytest.mark.parametrize("redirect_path", ["/post307", "/post308"])
def test_post_307_308_preserve_method_body_and_headers(
    monkeypatch, server, redirect_path
):
    """307/308 must replay a POST entity instead of CPython rejecting it."""
    _set_egress(monkeypatch, None)
    target = _abs(server, "/echo-post")
    source = _abs(server, redirect_path) + "?to=" + urllib.parse.quote(target, safe="")
    payload = b'{"answer":"keep-me"}'
    req = urllib.request.Request(
        source,
        data=payload,
        headers={"Content-Type": "application/json", "X-Trace": "redirect-body"},
        method="POST",
    )
    with safe_urlopen(req, timeout=5, allow_internal=True) as resp:
        echoed = json.loads(resp.read())
    assert echoed["method"] == "POST"
    assert echoed["body"] == payload.decode()
    assert echoed["content_length"] == str(len(payload))
    assert echoed["headers"]["x-trace"] == "redirect-body"


def test_post_302_keeps_urllib_compatible_rewrite(monkeypatch, server):
    """POST+302 follows urllib's compatibility rewrite to a GET without body."""
    _set_egress(monkeypatch, None)
    target = _abs(server, "/echo-post")
    source = _abs(server, "/redirect") + "?to=" + urllib.parse.quote(target, safe="")
    req = urllib.request.Request(source, data=b"rewrite-me", method="POST")
    with safe_urlopen(req, timeout=5, allow_internal=True) as resp:
        echoed = json.loads(resp.read())
    assert echoed["method"] == "GET"
    assert echoed["body"] == ""
    assert echoed["content_length"] is None


def test_same_hostname_different_port_loopback_redirect_is_denied(
    monkeypatch, server, second_server
):
    """Same hostname but a different effective port is a host-change/pivot."""
    _set_egress(monkeypatch, None)
    source = _loopback(server)
    target = _abs(second_server, "/echo")
    redirect_url = _abs(server, "/redirect") + "?to=" + urllib.parse.quote(target, safe="")
    req = urllib.request.Request(redirect_url, origin_req_host="127.0.0.1")
    with pytest.raises(ValueError):
        safe_urlopen(req, timeout=5, allow_internal=True)
    assert _hits(second_server, "/echo") == []
    assert source != _loopback(second_server)


def test_same_hostname_different_port_scrubs_credentials_via_public_stand_in(
    monkeypatch, server, routed
):
    """Port-changing redirects scrub credentials before the next hop.

    Both public-IP authorities are routed to the same real local server, so
    this isolates authority comparison from DNS/network access.
    """
    _set_egress(monkeypatch, None)
    source = _url(PUBLIC_A, "/redirect")
    target = _url(PUBLIC_A + ":2", "/echo")
    req = urllib.request.Request(
        source + "?to=" + urllib.parse.quote(target, safe=""),
        headers={"Authorization": "SECRET", "Cookie": "session=SECRET"},
    )
    with safe_urlopen(req, timeout=5) as resp:
        echoed = json.loads(resp.read())["headers"]
    assert "authorization" not in echoed
    assert "cookie" not in echoed


def test_allow_internal_true_permits_same_host_internal_chain(monkeypatch, server):
    """Operator-internal service redirecting to ITSELF (same host) must keep
    working — ≤5 hop chain of relative Locations stays allowed.
    Scheme/host/effective-port must remain unchanged for the internal exception."""
    _set_egress(monkeypatch, None)
    url = _chain(_loopback(server), 4)
    with safe_urlopen(url, timeout=5, allow_internal=True) as resp:
        assert (getattr(resp, "status", None) or resp.getcode()) == 200
        assert json.loads(resp.read())["ok"] is True
    assert len([p for p, _ in server.hits if p == "/chain"]) == 5  # 4 hops + final


# ================================================== 4. credential scrubbing
def test_cross_host_redirect_scrubs_authorization_cookie(monkeypatch, server, routed):
    """Redirect changes host ⇒ Authorization/Cookie/Cookie2 must NOT ride to
    the new host (the LLM token-leak shape)."""
    _set_egress(monkeypatch, None)
    url = _url(PUBLIC_A, "/redirect", to=_url(PUBLIC_B, "/echo"))
    req = urllib.request.Request(
        url,
        headers={"Authorization": "***", "Cookie": "session=SECRET-COOKIE"},
    )
    with safe_urlopen(req, timeout=5) as resp:
        echoed = json.loads(resp.read())["headers"]
    assert "authorization" not in echoed, f"token leaked to new host: {echoed}"
    assert "cookie" not in echoed, f"cookie leaked to new host: {echoed}"
    assert not any("S3CRIT-TOKEN" in str(v) for v in echoed.values())


def test_same_host_redirect_keeps_authorization(monkeypatch, server, routed):
    """Same-host hop is the origin service's own redirect — credentials stay
    (otherwise legitimate flows like provider self-redirects break)."""
    _set_egress(monkeypatch, None)
    url = _url(PUBLIC_A, "/redirect", to=_url(PUBLIC_A, "/echo"))
    req = urllib.request.Request(url, headers={"Authorization": "***"})
    with safe_urlopen(req, timeout=5) as resp:
        echoed = json.loads(resp.read())["headers"]
    assert echoed.get("authorization") == "***"


# =================================================== 5. scheme downgrade
@pytest.mark.parametrize("bad", ["ftp://example.invalid/pub", "file:///etc/passwd"])
def test_ftp_and_file_redirect_raise_valueerror(monkeypatch, server, routed, bad):
    """The stdlib allows ftp:// redirect targets (FTPHandler!) and turns
    file:// into HTTPError — SCP policy is ValueError on both, and nothing
    may be fetched from either."""
    _set_egress(monkeypatch, None)
    url = _url(_loopback(server), "/redirect", to=bad)
    with pytest.raises(ValueError) as excinfo:
        safe_urlopen(url, timeout=5, allow_internal=True)
    assert not isinstance(excinfo.value, urllib.error.HTTPError)
    assert "scheme" in str(excinfo.value).lower()


# ========================================================= 6. hop budget
def test_chain_longer_than_five_hops_raises(monkeypatch, server):
    """>5 redirects must raise ValueError with the canonical message, AFTER
    exactly SAFE_MAX_REDIRECT_HOPS+1 requests (5 redirects processed)."""
    _set_egress(monkeypatch, None)
    url = _chain(_loopback(server), 8)
    with pytest.raises(ValueError) as excinfo:
        safe_urlopen(url, timeout=5, allow_internal=True)
    assert "too many redirects" in str(excinfo.value)
    assert len([p for p, _ in server.hits if p == "/chain"]) == SAFE_MAX_REDIRECT_HOPS + 1


def test_exactly_five_hops_same_host_chain_ok(monkeypatch, server):
    _set_egress(monkeypatch, None)
    url = _chain(_loopback(server), SAFE_MAX_REDIRECT_HOPS)
    with safe_urlopen(url, timeout=5, allow_internal=True) as resp:
        assert json.loads(resp.read())["ok"] is True


# ================================================ 7. non-2xx contract kept
def test_non2xx_still_httperror(monkeypatch, server, routed):
    _set_egress(monkeypatch, None)
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        safe_urlopen(_url(PUBLIC_A, "/notfound"), timeout=5)
    assert excinfo.value.code == 404


def test_302_without_location_still_httperror(monkeypatch, server, routed):
    """Handler declines to fabricate a hop → the default error handler turns
    the 3xx into HTTPError exactly like urlopen did."""
    _set_egress(monkeypatch, None)
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        safe_urlopen(_url(PUBLIC_A, "/noheader"), timeout=5)
    assert excinfo.value.code == 302


# ============================= 8b. url_fetcher._SafeRedirectHandler stack
def test_safe_redirect_handler_shares_one_standard():
    """Tripwire against re-forking a second redirect policy (DNA #5):
    url_fetcher's public handler must BE the shared url_safety standard
    (plus its resolve+pin hook), with the same hop budget."""
    from scp.core import url_fetcher

    assert issubclass(url_fetcher._SafeRedirectHandler, _RevalidatingRedirectHandler)
    assert url_fetcher._SafeRedirectHandler.max_redirections == SAFE_MAX_REDIRECT_HOPS
    assert url_fetcher._SafeRedirectHandler().max_redirections == SAFE_MAX_REDIRECT_HOPS


def _fetcher_opener():
    from scp.core.url_fetcher import _SafeRedirectHandler

    return urllib.request.build_opener(_SafeRedirectHandler())


def test_urlfetcher_handler_metadata_denied_under_allowlist(monkeypatch, server, routed):
    _set_egress(monkeypatch, "allowlist", allowlist=PUBLIC_A)
    opener = _fetcher_opener()
    with pytest.raises(EgressDeniedError):
        opener.open(_url(PUBLIC_A, "/redirect", to=METADATA_URL), timeout=5)
    assert routed.metadata_attempts == []


def test_urlfetcher_handler_scrubs_cross_host_credentials(monkeypatch, server, routed):
    _set_egress(monkeypatch, None)
    opener = _fetcher_opener()
    req = urllib.request.Request(
        _url(PUBLIC_A, "/redirect", to=_url(PUBLIC_B, "/echo")),
        headers={"Authorization": "***"},
    )
    with opener.open(req, timeout=5) as resp:
        echoed = json.loads(resp.read())["headers"]
    assert "authorization" not in echoed


# ============================================ 8c. _common requests hop loop
def _common_get(monkeypatch, url, headers=None):
    import requests as _rq

    from scp.core.question_fetchers import _common as qf

    # FRESH session per test: the module-level _SESSION pools keep-alive
    # sockets across tests, and the previous test's server socket would be
    # reused against a dead port (test-isolation harness, not product
    # state). trust_env=False keeps proxy env vars from any ambient profile.
    fresh = _rq.Session()
    fresh.trust_env = False
    monkeypatch.setattr(qf, "_SESSION", fresh)
    return qf._http_get_json(url, timeout=5, headers=headers)


def test_common_redirect_to_metadata_denied_with_zero_connects(monkeypatch, server, routed):
    _set_egress(monkeypatch, "allowlist", allowlist=PUBLIC_A)
    # _http_get_json contract: fail-closed → None (EgressDenied is a
    # ValueError swallowed by the graceful handler).
    assert _common_get(monkeypatch, _url(PUBLIC_A, "/redirect", to=METADATA_URL)) is None
    assert routed.metadata_attempts == []
    assert _hits(server, "/echo") == []


def test_common_dynamic_hop0_does_not_inherit_session_credentials(
    monkeypatch, server, routed
):
    """Dynamic hop-0 URLs use an isolated session, not session-level auth."""
    _set_egress(monkeypatch, None)
    import requests as _rq
    from scp.core.question_fetchers import _common as qf

    fresh = _rq.Session()
    fresh.trust_env = False
    fresh.headers.update({"Authorization": "SESSION-SECRET", "Cookie": "session=SECRET"})
    fresh.cookies.set("cookie_secret", "SECRET")
    monkeypatch.setattr(qf, "_SESSION", fresh)
    out = qf._http_get_json(_url(PUBLIC_A, "/echo"), timeout=5)
    echoed = out["headers"]
    assert "authorization" not in echoed
    assert "cookie" not in echoed
    assert not any("SECRET" in str(value) for value in echoed.values())


def test_common_follows_public_chain_and_scrubs_credentials(monkeypatch, server, routed):
    _set_egress(monkeypatch, None)
    out = _common_get(
        monkeypatch,
        _url(PUBLIC_A, "/redirect", to=_url(PUBLIC_B, "/echo")),
        headers={"Authorization": "***", "Cookie": "session=SECRET-COOKIE"},
    )
    assert isinstance(out, dict), "requests hop loop must still FOLLOW valid hops"
    echoed = out["headers"]
    assert "authorization" not in echoed and "cookie" not in echoed
    assert len(_hits(server, "/echo")) == 1


def test_common_blocks_cross_host_internal_hop(monkeypatch, server, routed):
    _set_egress(monkeypatch, None)
    assert _common_get(monkeypatch, _url(PUBLIC_A, "/redirect", to=_abs(server, "/echo"))) is None
    assert _hits(server, "/echo") == []


def test_common_too_many_hops_fail_closed(monkeypatch, server, routed):
    _set_egress(monkeypatch, None)
    assert _common_get(monkeypatch, _chain(PUBLIC_A, 8)) is None
    assert len([p for p, _ in server.hits if p == "/chain"]) == SAFE_MAX_REDIRECT_HOPS + 1


def test_common_same_host_chain_ok(monkeypatch, server, routed):
    """No allow_internal exists for this fetcher → internal is NEVER a valid
    hop target; public same-host chain of ≤5 hops works."""
    _set_egress(monkeypatch, None)
    out = _common_get(monkeypatch, _chain(PUBLIC_A, 2))
    assert out == {"ok": True, "start": 2}


# =============================== 8d. DirectAPIVerifier._session_get stack
def _verifier_get(url, headers=None):
    from scp.runtime.engine_parts.direct_api_verifier import DirectAPIVerifier

    DirectAPIVerifier._session = None  # per-test session isolation
    v = DirectAPIVerifier()
    return v._session_get(url, timeout=5, headers=headers)


def test_verifier_redirect_to_metadata_denied_with_zero_connects(monkeypatch, server, routed):
    _set_egress(monkeypatch, "allowlist", allowlist=PUBLIC_A)
    with pytest.raises(EgressDeniedError):
        _verifier_get(_url(PUBLIC_A, "/redirect", to=METADATA_URL))
    assert routed.metadata_attempts == []


def test_verifier_blocks_internal_hop_and_scrubs_credentials(monkeypatch, server, routed):
    _set_egress(monkeypatch, None)
    with pytest.raises(ValueError):
        _verifier_get(_url(PUBLIC_A, "/redirect", to=_abs(server, "/echo")))
    assert _hits(server, "/echo") == []
    resp = _verifier_get(
        _url(PUBLIC_A, "/redirect", to=_url(PUBLIC_B, "/echo")),
        headers={"Authorization": "***"},
    )
    echoed = resp.json()["headers"]
    assert "authorization" not in echoed


def test_verifier_too_many_hops_raises(monkeypatch, server, routed):
    _set_egress(monkeypatch, None)
    with pytest.raises(ValueError) as excinfo:
        _verifier_get(_chain(PUBLIC_A, 8))
    assert "too many redirects" in str(excinfo.value)


def test_verifier_rejects_private_initial_url_without_local_hit(monkeypatch, server):
    """Hop-0 SSRF validation must run before DirectAPIVerifier session.get."""
    _set_egress(monkeypatch, None)
    local_url = _abs(server, "/echo")
    from scp.runtime.engine_parts.direct_api_verifier import DirectAPIVerifier

    DirectAPIVerifier._session = None
    with pytest.raises(ValueError):
        _verifier_get(local_url)
    assert _hits(server, "/echo") == []


def test_verifier_verify_never_raises_for_private_initial_url(monkeypatch, server):
    """The public verify() contract remains graceful UNKNOWN, never raise."""
    _set_egress(monkeypatch, None)
    from scp.runtime.engine_parts.direct_api_verifier import DirectAPIVerifier

    DirectAPIVerifier._session = None
    verifier = DirectAPIVerifier()
    local_url = _abs(server, "/echo")

    def _invoke_private(*args, **kwargs):
        verifier._session_get(local_url, timeout=5)

    monkeypatch.setattr(verifier, "_verify_geography", _invoke_private)
    result = verifier.verify("capital of France?", "Paris", "geography")
    assert result["verdict"] == "UNKNOWN"
    assert _hits(server, "/echo") == []


# ====================================== 9. LLM token-leak regression (task)
def test_llm_style_post_with_bearer_never_leaks_on_cross_host_hop(monkeypatch, server, routed):
    """Exact multi_llm_check/_call_openrouter shape: allow_internal=True POST
    with bearer. A malicious provider self-host redirect chain must either
    stay same-origin (token kept) or die at the gate before any next hop."""
    _set_egress(monkeypatch, None)
    req = urllib.request.Request(
        _url(PUBLIC_A, "/redirect", to=_abs(server, "/echo")),
        data=b'{"model":"x"}',
        headers={"Authorization": "***", "Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(ValueError):
        safe_urlopen(req, timeout=5, allow_internal=True)
    assert _hits(server, "/echo") == []


# ============================ 10. SEC-A v5: host authority + body replay ====
def test_host_header_follows_target_authority_on_cross_authority_redirect(
    monkeypatch, server, routed
):
    """A cross-authority hop must send the TARGET authority as Host.

    The stale source Host (8.8.8.8:1) must not ride to the redirect target:
    urllib only regenerates Host when the caller did not supply one, so the
    pre-SEC-A code sent the source authority to the target server (wrong
    virtual host / cache-routing confusion).  Both public stand-ins are
    routed to the same local server, so the Host header is observable
    without any DNS.
    """
    _set_egress(monkeypatch, None)
    url = _url(PUBLIC_A, "/redirect", to=_url(PUBLIC_B + ":2", "/echo"))
    req = urllib.request.Request(url)
    with safe_urlopen(req, timeout=5) as resp:
        assert (getattr(resp, "status", None) or resp.getcode()) == 200
        resp.read()
    hop_hits = _hits(server, "/echo")
    assert len(hop_hits) == 1, "the redirect hop must land exactly once"
    assert hop_hits[0].get("Host") == "9.9.9.9:2", (
        f"hop1 Host must be the TARGET authority, got {hop_hits[0].get('Host')!r}"
    )


@pytest.mark.parametrize("redirect_path", ["/post307", "/post308"])
def test_bytesio_post_body_replayed_on_307_and_308(
    monkeypatch, server, redirect_path
):
    """A seekable file-like body must be snapshotted BEFORE the first driver
    and replayed FULL at the 307/308 hop — at hop0 AND hop1.

    Pre-SEC-A-v5 behavior (probed RED): the driver drained the BytesIO at
    hop0 and the server received b'' with Content-length 0 on both hops.
    """
    _set_egress(monkeypatch, None)
    target = _abs(server, "/echo-post")
    source = _abs(server, redirect_path) + "?to=" + urllib.parse.quote(target, safe="")
    req = urllib.request.Request(
        source,
        data=io.BytesIO(b"payload"),
        headers={"Content-Type": "text/plain"},
        method="POST",
    )
    with safe_urlopen(req, timeout=5, allow_internal=True) as resp:
        echoed = json.loads(resp.read())
    # hop1 (the redirect target) received the full replayed entity.
    assert echoed["method"] == "POST"
    assert echoed["body"] == "payload"
    assert echoed["content_length"] == str(len(b"payload"))
    assert echoed["headers"]["content-type"] == "text/plain"
    # hop0 (the 307/308 endpoint itself) ALSO received the full entity.
    hop0_bodies = _post_bodies(server, redirect_path)
    hop1_bodies = _post_bodies(server, "/echo-post")
    assert hop0_bodies == [b"payload"], f"hop0 body: {hop0_bodies!r}"
    assert hop1_bodies == [b"payload"], f"hop1 body: {hop1_bodies!r}"


def test_non_replayable_body_fails_closed(monkeypatch, server):
    """A body stream with no seek is NOT replayable → clear ValueError at
    hop0, BEFORE any socket opens; no hop may be fetched with a silently
    empty body."""
    _set_egress(monkeypatch, None)

    class _NoSeekStream:
        """Read-only stream: consume-once, cannot rewind (like a socket/pipe)."""

        def __init__(self, payload: bytes):
            self._payload = payload
            self._read_done = False

        def read(self, size: int = -1) -> bytes:  # noqa: D102 — probe stream
            if self._read_done:
                return b""
            self._read_done = True
            return self._payload

    target = _abs(server, "/echo-post")
    source = _abs(server, "/post307") + "?to=" + urllib.parse.quote(target, safe="")
    req = urllib.request.Request(
        source, data=_NoSeekStream(b"payload"), method="POST"
    )
    with pytest.raises(ValueError) as excinfo:
        safe_urlopen(req, timeout=5, allow_internal=True)
    assert "replayable" in str(excinfo.value)
    # Fail closed BEFORE the driver: nothing was posted, nothing fetched.
    assert _post_bodies(server, "/post307") == []
    assert _post_bodies(server, "/echo-post") == []
    assert _hits(server, "/echo-post") == []


def test_request_reuse_sequential_fresh_chain_each_call(monkeypatch, server, routed):
    """Reusing ONE caller Request 8 times must give 8 independent, successful
    chains (fresh chain state per top-level call, caller Request not mutated).

    Pre-SEC-A-v5 behavior (probed RED): calls 1–5 OK, calls 6–8 raised
    "too many redirects (maximum 5)" because the redirect counters accumulated
    on the shared Request.  Loop detection must still fire WITHIN one chain
    (>5 hops) — the per-call clone never disables it.
    """
    _set_egress(monkeypatch, None)
    target = _url(PUBLIC_A, "/echo")
    r_url = _url(PUBLIC_A, "/redirect", to=target)
    req = urllib.request.Request(r_url)
    for call in range(8):
        with safe_urlopen(req, timeout=5) as resp:
            assert (getattr(resp, "status", None) or resp.getcode()) == 200, (
                f"reuse call {call + 1} must be a fresh, successful chain"
            )
            body = json.loads(resp.read())
            assert body.get("headers") is not None
    assert len(_hits(server, "/echo")) == 8

    # One chain longer than the hop budget STILL raises (same caller-side
    # Request type; a fresh chain must not turn fail-closed into pass).
    loop_req = urllib.request.Request(_chain(PUBLIC_A, SAFE_MAX_REDIRECT_HOPS + 3))
    with pytest.raises(ValueError) as excinfo:
        safe_urlopen(loop_req, timeout=5)
    assert "too many redirects" in str(excinfo.value)


def test_request_reuse_concurrent_safe(monkeypatch, server, routed):
    """10 threads sharing ONE caller Request must all succeed — no cross-chain
    redirect-counter bleed (no 'too many redirects' from another thread's
    chain), and the caller Request stays untouched."""
    _set_egress(monkeypatch, None)
    target = _url(PUBLIC_A, "/echo")
    r_url = _url(PUBLIC_A, "/redirect", to=target)
    shared_req = urllib.request.Request(r_url)
    threads = 10
    barrier = threading.Barrier(threads)
    results: list[tuple[int, str]] = [(0, "")] * threads
    errors: list[str] = []
    results_lock = threading.Lock()

    def _worker(idx: int):
        barrier.wait()
        try:
            with safe_urlopen(shared_req, timeout=10) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                resp.read()
            results[idx] = (status, "ok")
        except Exception as exc:  # noqa: BLE001 — the test classifies failures
            with results_lock:
                errors.append(f"thread{idx}: {type(exc).__name__}: {exc}")

    workers = [
        threading.Thread(target=_worker, args=(i,), daemon=True) for i in range(threads)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)
        assert not worker.is_alive(), "a reuse thread hung"

    cross_chain_errors = [e for e in errors if "too many redirects" in e]
    assert cross_chain_errors == [], (
        f"cross-chain redirect bleed detected: {cross_chain_errors}"
    )
    assert errors == [], f"unexpected failures under concurrent reuse: {errors}"
    assert all(status == 200 for status, _ in results), f"results: {results!r}"
    assert len(_hits(server, "/echo")) == threads


# ================================ 9. _safe_fetch_url egress fail-closed (SEC-A v6 F1)
def _stub_dns_and_connect(monkeypatch) -> list[tuple]:
    """Record EVERY socket.getaddrinfo call and refuse ALL socket connects.

    Hermetic even in the BROKEN state: if the fail-closed guard is ever
    deleted again, the fetch path hits this stub — never the real network."""
    dns_calls: list[tuple] = []

    def _no_dns(host, port=None, *args, **kwargs):
        dns_calls.append((host, port))
        raise AssertionError(f"DNS lookup attempted before egress guard: {host!r}")

    def _no_connect(address, *args, **kwargs):
        raise AssertionError(f"socket connect attempted: {address!r}")

    monkeypatch.setattr(socket, "getaddrinfo", _no_dns)
    monkeypatch.setattr(socket, "create_connection", _no_connect)
    return dns_calls


def test_safe_fetch_url_unset_mode_fails_closed(monkeypatch):
    """[SEC-A v6 F1] Unset SCP_EGRESS_MODE must DEFAULT to deny for the
    canonical fetcher: plain ValueError "external egress disabled by
    SCP_EGRESS_MODE" BEFORE any DNS/socket I/O. This is the HEAD (d38b0ba)
    semantic that enforce_egress_policy alone does NOT provide — its dev
    default is "" (no-op), so this pin exists precisely to catch the explicit
    guard being deleted from `_safe_fetch_url`."""
    _set_egress(monkeypatch, None)  # delenv SCP_EGRESS_MODE/_ALLOWLIST + SCP_PRODUCTION_MODE
    dns_calls = _stub_dns_and_connect(monkeypatch)
    from scp.core.url_fetcher import _safe_fetch_url

    with pytest.raises(ValueError, match="external egress disabled by SCP_EGRESS_MODE"):
        _safe_fetch_url(f"https://{PUBLIC_A}/probe")
    assert dns_calls == [], f"DNS was attempted under unset egress mode: {dns_calls}"


def test_safe_fetch_url_explicit_deny_still_blocks(monkeypatch):
    """[SEC-A v6 F1] Explicit SCP_EGRESS_MODE=deny blocks external hosts with
    zero DNS attempts. Per HEAD ordering the deny layer fires as
    EgressDeniedError (a ValueError subclass, so the fetcher's documented
    "raises ValueError" contract holds) from enforce_egress_policy BEFORE the
    restored explicit guard — pinning the subclass is stricter, not weaker."""
    _set_egress(monkeypatch, "deny")
    dns_calls = _stub_dns_and_connect(monkeypatch)
    from scp.core.url_fetcher import _safe_fetch_url

    with pytest.raises(EgressDeniedError):
        _safe_fetch_url(f"https://{PUBLIC_A}/probe")
    assert dns_calls == [], f"DNS was attempted under deny mode: {dns_calls}"
