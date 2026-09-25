import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const LOCAL_IPS = new Set(["127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"]);

export function middleware(request: NextRequest) {
  // Fail-closed IP restriction across all dashboard API routes
  if (request.nextUrl.pathname.startsWith("/api/scp/")) {
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
  matcher: "/api/scp/:path*",
};
