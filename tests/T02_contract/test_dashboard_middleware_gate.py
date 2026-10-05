"""[AUDIT-FIX low-9] Contract test — dashboard middleware IP/secret gate.

Root cause (external audit low-9): matcher chỉ phủ /api/scp/*; /api/audit,
/api/autofix, /api/scanners nằm NGOÀI gate — /api/autofix trả về internal
backendUrl. Fix: GATED_API_ROOTS + matcher mở rộng.

Regression:
  * Static: matcher config phải chứa cả 4 prefix.
  * Runtime (bun + next/server thật): request KHÔNG headers → 403 cho mọi
    gated path (kể cả /api/autofix/scanners); secret đúng + XFF local → pass;
    path ngoài gate → pass.
"""
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MIDDLEWARE = ROOT / "dashboard" / "src" / "middleware.ts"
DASHBOARD_DIR = ROOT / "dashboard"

RUNTIME_SCRIPT = r"""
const { middleware } = await import("./src/middleware.ts");
const { NextRequest } = await import("next/server");

async function statusFor(path, headers = {}, secret = "") {
  // [P2-06 fix 2026-09-28] When no proxy secret is configured the
  // production branch FAILS CLOSED (503). The XFF-only 403 contract this
  // test pins requires the explicit dev-mode fallback, so the harness
  // opts in per request.
  process.env.SCP_DEV_MODE = secret ? "" : "1";
  process.env.SCP_DASHBOARD_PROXY_SECRET = secret;
  const req = new NextRequest("http://127.0.0.1" + path, { headers });
  const res = middleware(req);
  return res.status;
}

// 1. Mọi gated path KHÔNG headers → 403 fail-closed (bao gồm case audit).
const gated = [
  "/api/autofix/scanners",
  "/api/autofix",
  "/api/autofix/",
  "/api/audit",
  "/api/audit/x",
  "/api/scanners",
  "/api/scanners/x",
  "/api/scp/ask",
  "/api/scp/activity",
];
for (const p of gated) {
  const s = await statusFor(p);
  if (s !== 403) {
    console.error(`FAIL gated ${p} -> ${s} (expected 403)`);
    process.exit(1);
  }
}

// 2. Secret đúng + XFF local → middleware cho đi tiếp (NextResponse.next()).
const ok = await statusFor(
  "/api/autofix/scanners",
  { "x-scp-proxy-secret": "s3cret", "x-forwarded-for": "127.0.0.1" },
  "s3cret",
);
if (ok !== 200) {
  console.error(`FAIL authorized passthrough -> ${ok} (expected 200)`);
  process.exit(1);
}

// 3. Secret SAI → 403 dù có IP headers.
const bad = await statusFor(
  "/api/autofix/scanners",
  { "x-scp-proxy-secret": "wrong", "x-forwarded-for": "127.0.0.1" },
  "s3cret",
);
if (bad !== 403) {
  console.error(`FAIL wrong secret -> ${bad} (expected 403)`);
  process.exit(1);
}

// 4. Path ngoài gate → không bị chặn bởi middleware này.
const other = await statusFor("/api/public-route");
if (other !== 200) {
  console.error(`FAIL non-gated ${"/api/public-route"} -> ${other}`);
  process.exit(1);
}

console.log("MIDDLEWARE_GATE_OK");
"""


def test_middleware_matcher_covers_all_gated_roots():
    source = MIDDLEWARE.read_text(encoding="utf-8")
    for pattern in (
        '"/api/scp/:path*"',
        '"/api/audit/:path*"',
        '"/api/autofix/:path*"',
        '"/api/scanners/:path*"',
    ):
        assert pattern in source, f"matcher thiếu {pattern}"
    # Gate logic phải check cả exact path lẫn prefix.
    for root in ('"/api/scp"', '"/api/audit"', '"/api/autofix"', '"/api/scanners"'):
        assert root in source, f"GATED_API_ROOTS thiếu {root}"


def _require_runtime_deps() -> None:
    """[W9-FLAKE-FIX] Precondition fail-closed (KHÔNG phải skip): khi
    dashboard/node_modules thiếu (runner mới, deps chưa install), bun rơi
    vào auto-install global cache — evidence wave4 (kc_a_pytest_base.log):
    `Cannot find package 'react' from '...\\.bun\\install\\cache\\next@16.3.8@@@1\\...'`
    (next 16.3.8 != pin 16.3.6). Fail NGAY với thông báo actionable thay vì
    lỗi cache mù mờ 1/2 run."""
    missing = [
        name
        for name in ("next", "react")
        if not (DASHBOARD_DIR / "node_modules" / name).is_dir()
    ]
    if missing:
        pytest.fail(
            "dashboard runtime deps missing: "
            + ", ".join(missing)
            + " — cài dependencies của dashboard (npm ci / bun install trong "
            "dashboard/) trước khi chạy runtime gate; bun sẽ rơi vào global "
            "install cache nếu giải quyết tiếp (không deterministic)"
        )


@pytest.mark.parametrize("marker", ["MIDDLEWARE_GATE_OK"])
def test_middleware_runtime_gate_behavior(marker):
    """Runtime thật qua bun (node_modules của dashboard): NextRequest +
    middleware(request) — không phải mock.

    [W9-FLAKE-FIX] Script được ghi ra FILE THẬT trong dashboard/ thay vì
    `bun -e`: entrypoint ảo của `-e` không có đường dẫn thật, nên khi
    dashboard/node_modules thiếu/không resolve được, bun fallback vào global
    install cache (next@16.3.8 không thấy react) — flake 1/2 run trên main
    (evidence: wave4 kc_a_pytest_base.log). Với file thật nằm trong
    dashboard/, module resolution luôn đi qua dashboard/node_modules ->
    deterministic. Contract script KHÔNG đổi; precondition fail-closed.
    """
    _require_runtime_deps()
    probe = DASHBOARD_DIR / f".scp-middleware-gate-probe-{os.getpid()}.ts"
    probe.write_text(RUNTIME_SCRIPT, encoding="utf-8")
    try:
        result = subprocess.run(
            ["bun", "run", probe.name],
            cwd=str(DASHBOARD_DIR),
            capture_output=True,
            text=True,
            timeout=120,
        )
    finally:
        probe.unlink(missing_ok=True)
    assert result.returncode == 0, (
        f"middleware gate runtime FAIL:\n{result.stdout}\n{result.stderr}"
    )
    assert marker in result.stdout
