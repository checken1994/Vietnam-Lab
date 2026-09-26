import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const LOCAL_IPS = new Set(["127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"]);

// Trusted-proxy shared secret (injected by the reverse proxy, see
// deploy/vps/Caddyfile.dashboard.example). Next 16 no longer exposes the
// socket peer as request.ip, so when :3000 is directly reachable the
// XFF-based restriction below rests on headers a direct client can spoof.
// WHEN SCP_DASHBOARD_PROXY_SECRET is set, every gated dashboard API request
// must carry `x-scp-proxy-secret` matching it (403 on missing/mismatch) —
// only the proxy holding the secret can reach the dashboard API. WHEN the
// env is unset the historical XFF-only behavior is preserved and a single
// warning is logged so operators notice that direct :3000 access is not
// secret-gated.
let proxySecretWarningLogged = false;

// [AUDIT-FIX low-9] Trước đây chỉ /api/scp/* nằm trong gate; /api/audit,
// /api/autofix, /api/scanners lộ ngoài IP/secret gate (vd /api/autofix
// trả về internal backendUrl). Tất cả route groups của dashboard API nay
// cùng một gate fail-closed.
const GATED_API_ROOTS = ["/api/scp", "/api/audit", "/api/autofix", "/api/scanners"];

function isGatedApiPath(pathname: string): boolean {
  return GATED_API_ROOTS.some(
    (root) => pathname === root || pathname.startsWith(`${root}/`)
  );
}

export function middleware(request: NextRequest) {
  // Fail-closed IP restriction across all dashboard API routes
  if (isGatedApiPath(request.nextUrl.pathname)) {
    const proxySecret = process.env.SCP_DASHBOARD_PROXY_SECRET?.trim() ?? "";
    if (proxySecret) {
      const presented = request.headers.get("x-scp-proxy-secret") ?? "";
      if (presented !== proxySecret) {
        return NextResponse.json(
          { error: "Access denied. Missing or invalid proxy secret; dashboard API is restricted to the trusted reverse proxy." },
          { status: 403 }
        );
      }
    } else if (!proxySecretWarningLogged) {
      proxySecretWarningLogged = true;
      console.warn(
        "[scp-dashboard] SCP_DASHBOARD_PROXY_SECRET is not set: dashboard API access control (/api/scp/*, /api/audit, /api/autofix, /api/scanners) relies on X-Forwarded-For/X-Real-IP headers only. Set the secret on the Next.js process and inject it via the reverse proxy (deploy/vps/Caddyfile.dashboard.example) to fail-close direct :3000 access."
      );
    }

    const forwardedHeader = request.headers.get("x-forwarded-for");
    const realIpHeader = request.headers.get("x-real-ip");

    const hops = forwardedHeader
      ? forwardedHeader.split(",").map((s) => s.trim()).filter(Boolean)
      : realIpHeader
      ? [realIpHeader.trim()]
      : [];

    // Next 16 no longer exposes the socket peer as request.ip; access control
    // rests entirely on the trusted reverse proxy contract: the proxy ALWAYS
    // appends the peer IP to x-forwarded-for (see deploy/vps/Caddyfile), so a
    // request without hop headers is rejected fail-closed even from localhost.

    if (hops.length === 0) {
      return NextResponse.json(
        { error: "Access denied. Missing IP headers; dashboard API is restricted to localhost." },
        { status: 403 }
      );
    }

    // Strict multi-hop validation:
    // 1. The last hop appended by the trusted reverse proxy must be in LOCAL_IPS.
    // 2. All hops in the chain must be local to prevent first-hop or intermediary injection.
    const lastHop = hops[hops.length - 1];
    const allLocal = hops.every((ip) => LOCAL_IPS.has(ip));

    if (!LOCAL_IPS.has(lastHop) || !allLocal) {
      return NextResponse.json(
        { error: "Access denied. Dashboard API is restricted to localhost." },
        { status: 403 }
      );
    }
  }

  return NextResponse.next();
}

export const config = {
  // [AUDIT-FIX low-9] matcher mở rộng theo GATED_API_ROOTS (:path* match cả
  // zero segment → root path cũng vào gate).
  matcher: [
    "/api/scp/:path*",
    "/api/audit/:path*",
    "/api/autofix/:path*",
    "/api/scanners/:path*",
  ],
};
