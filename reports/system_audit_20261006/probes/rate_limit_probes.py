"""R2 rate-limit window probes (no LLM usage — all requests fail auth).

Window A: POST /auth/token with 7 wrong admin keys -> slowapi 5/minute
          expected: 5x 401 then 429 (attempt 6+).
Window B: POST /ask with 62 bad JWTs -> slowapi 60/minute
          expected: 60x 401 then 429.

Every printed line is a real HTTP observation from THIS run (FA-08/FA-09).
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scp.security.url_safety import safe_urlopen

ROOT = Path(__file__).resolve().parents[2]
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8092
BASE = f"http://127.0.0.1:{PORT}"


def post(url: str, body: dict, token: str | None = None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 method="POST", headers=headers)
    try:
        with safe_urlopen(req, timeout=10, allow_internal=True) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, exc.read().decode("utf-8", errors="replace")
        except Exception:
            return exc.code, ""
    except Exception as exc:
        return 0, type(exc).__name__


print(f"[window A] /auth/token x7 wrong admin keys (limiter 5/minute)")
statuses = []
for i in range(7):
    c, r = post(f"{BASE}/auth/token", {"admin_key": f"wrong-key-probe-{i}"})
    statuses.append(c)
    print(f"  attempt {i+1}: HTTP {c} {r[:60]!r}")
    time.sleep(0.1)
print(f"[window A] statuses={statuses} -> 429 observed: {429 in statuses}")

print("[sleep 60s to reset slowapi window]")
time.sleep(61)

print(f"[window B] /ask x62 bad JWT (limiter 60/minute)")
statuses_b = []
first429 = None
for i in range(62):
    c, r = post(f"{BASE}/ask", {"question": "rl"}, token=f"bad-jwt-{i}")
    statuses_b.append(c)
    if c == 429 and first429 is None:
        first429 = i + 1
        print(f"  attempt {i+1}: HTTP {c} {r[:60]!r}")
    time.sleep(0.05)
n401 = sum(1 for s in statuses_b if s == 401)
n429 = sum(1 for s in statuses_b if s == 429)
print(f"[window B] 401={n401} 429={n429} first_429_at={first429} "
      f"tail={statuses_b[-5:]}")
