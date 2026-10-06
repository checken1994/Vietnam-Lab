"""[AUDIT-20261006 PROBE-2] Weather egress scope + SSRF PoC re-verification.

Independent re-run of the W13 checker claim "SSRF sweep 9/9 PoC dead" plus
additional invariant probes, executed against live code at HEAD 44e5bf02:

  P1  DENY mode beats scoped extra_allowed_hosts      (Invariant 3 > 4)
  P2  metadata IPv4 literal 169.254.169.254            (Invariant 1)
  P3  metadata numeric spellings (dec/oct/hex)         (Invariant 1 + normalizer)
  P4  metadata IPv4-mapped IPv6 ::ffff:169.254.169.254 (Invariant 1)
  P5  userinfo spoof: https://api.open-meteo.com@evil/ (hostname after '@')
  P6  suffix spoof: evil-api.open-meteo.com            (exact match required)
  P7  subdomain: geocoding-api.open-meteo.com          (not in grant)
  P8  wildcard-shaped host api.open-meteo.com.evil.com (not in grant)
  P9  exact approved host in ALLOWLIST mode w/ grant   (allowed — the 1 gate)
  P10 build_open_meteo_url: nan/inf/str/None coords    (ValueError pre-fetch)
  P11 _weather_host_allowed dry-check rejects geocoding host

Deterministic, NO network I/O (policy engines are pure; validate/pin paths
not exercised beyond policy layer).
"""
import os
import sys

sys.path.insert(0, r"D:\scp")

from scp.policy.egress import EgressDeniedError, EgressPolicy  # noqa: E402

GRANT = frozenset({"api.open-meteo.com"})


def run(policy_mode, url, extra=GRANT):
    """Return 'ALLOWED' or 'DENIED(<reason>)' for one enforce() call."""
    policy = EgressPolicy(mode=policy_mode, allowlist=["api.open-meteo.com"])
    try:
        policy.enforce(url, token_allowed_hosts=extra)
        return "ALLOWED"
    except EgressDeniedError as exc:
        return f"DENIED({exc.args[0].split(':')[-1].strip()[:60]})"


results = []


def check(pid, desc, observed, expect):
    ok = observed == expect or (expect == "DENIED" and observed.startswith("DENIED"))
    results.append((pid, desc, observed, expect, "PASS" if ok else "FAIL"))


# P1 DENY beats scoped grant
check("P1", "DENY mode + scoped grant -> blocked",
      run("deny", "https://api.open-meteo.com/v1/forecast"), "DENIED")
# P2 metadata literal
check("P2", "metadata literal 169.254.169.254 (allowlist+grant)",
      run("allowlist", "https://169.254.169.254/latest/meta-data"), "DENIED")
# P3 numeric spellings
for spelling in ("2852039166", "025177524776", "0xA9FEA9FE", "0251.0376.0251.0376"):
    check("P3", f"metadata numeric spelling {spelling}",
          run("allowlist", f"https://{spelling}/latest/meta-data"), "DENIED")
# P4 IPv4-mapped IPv6 metadata
check("P4", "metadata IPv4-mapped ::ffff:169.254.169.254",
      run("allowlist", "https://[::ffff:169.254.169.254]/latest/meta-data"), "DENIED")
# P5 userinfo spoof — hostname is parsed AFTER the last '@'
check("P5", "userinfo spoof api.open-meteo.com@evil.com",
      run("allowlist", "https://api.open-meteo.com@evil.com/x"), "DENIED")
# P6 suffix spoof
check("P6", "suffix spoof evil-api.open-meteo.com",
      run("allowlist", "https://evil-api.open-meteo.com/x"), "DENIED")
# P7 disapproved subdomain
check("P7", "subdomain geocoding-api.open-meteo.com",
      run("allowlist", "https://geocoding-api.open-meteo.com/v1/search"), "DENIED")
# P8 trailing-host takeover shape
check("P8", "host api.open-meteo.com.evil.com",
      run("allowlist", "https://api.open-meteo.com.evil.com/x"), "DENIED")
# P9 exact approved host in allowlist mode with scoped grant
check("P9", "exact approved host + grant (allowlist)",
      run("allowlist", "https://api.open-meteo.com/v1/forecast"), "ALLOWED")
# P10 URL builder coordinate validation
from scp.data_sources.weather import build_open_meteo_url  # noqa: E402

p10 = []
for bad in (float("nan"), float("inf"), None, "21;drop", float("-inf")):
    try:
        build_open_meteo_url(bad if bad != float("-inf") else 21.0,
                             float("-inf") if bad == 21.0 else bad)
        p10.append((repr(bad), "NO-ERROR"))
    except ValueError as exc:
        p10.append((repr(bad), f"ValueError:{str(exc)[:28]}"))
p10_ok = all(v.startswith("ValueError") for _, v in p10)
results.append(("P10", "build_open_meteo_url rejects bad coords",
                p10, "all ValueError", "PASS" if p10_ok else "FAIL"))

# P11 dry-check scoped host check
os.environ["SCP_EGRESS_MODE"] = "allowlist"
from scp.runtime.question_router import _weather_host_allowed  # noqa: E402

p11_geo = _weather_host_allowed("https://geocoding-api.open-meteo.com/v1/search")
p11_ok = _weather_host_allowed("https://api.open-meteo.com/v1/forecast") is True and p11_geo is False
results.append(("P11", "_weather_host_allowed exact-host dry-check",
                {"approved": True, "geocoding": p11_geo},
                {"approved": True, "geocoding": False},
                "PASS" if p11_ok else "FAIL"))

print("PROBE-2 weather egress scope + SSRF PoC re-verification")
print("=" * 72)
fails = 0
for pid, desc, observed, expect, verdict in results:
    if verdict == "FAIL":
        fails += 1
    print(f"{verdict} {pid:4s} {desc}\n      observed={observed!s:.90s}")
print("=" * 72)
print(f"RESULT: {len(results) - fails}/{len(results)} probe groups PASS, {fails} FAIL")
