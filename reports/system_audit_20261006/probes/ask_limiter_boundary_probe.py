"""R2 follow-up: /ask 60/minute limiter boundary probe WITH per-request timing.

62 rapid bad-JWT POSTs, no sleep between requests, timestamps printed.
If total duration < 60s and no 429 appears -> the /ask limiter did not
enforce 60/minute under this burst (FA-09 demonstrated observation).
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

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8092
BASE = f"http://127.0.0.1:{PORT}"


def post(url: str, body: dict, token: str):
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 method="POST", headers=headers)
    try:
        with safe_urlopen(req, timeout=10, allow_internal=True) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        try:
            exc.read()
        except Exception:
            pass
        return exc.code
    except Exception:
        return 0


statuses = []
t_start = time.time()
for i in range(62):
    t0 = time.time()
    c = post(f"{BASE}/ask", {"question": "rl-boundary"}, token=f"bad-jwt-boundary-{i}")
    statuses.append(c)
    if c not in (401,):
        print(f"  attempt {i+1}: HTTP {c} (unexpected) at t={time.time()-t_start:.2f}s")
print(f"total_duration={time.time()-t_start:.2f}s for 62 requests")
n401 = sum(1 for s in statuses if s == 401)
n429 = sum(1 for s in statuses if s == 429)
print(f"401={n401} 429={n429} tail={statuses[-6:]}")
print(f"[window B retest] limiter 60/minute enforced under burst: "
      f"{'YES' if n429 > 0 else 'NO'}")
