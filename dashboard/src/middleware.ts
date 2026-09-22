import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const LOCAL_IPS = new Set(["127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"]);

export function middleware(request: NextRequest) {
  // Fail-closed IP restriction across all dashboard API routes
  if (request.nextUrl.pathname.startsWith("/api/scp/")) {
    const forwardedHeader = request.headers.get("x-forwarded-for");
    const realIpHeader = request.headers.get("x-real-ip");

    const rawIp = forwardedHeader
      ? forwardedHeader.split(",")[0].trim()
      : realIpHeader
      ? realIpHeader.trim()
      : null;

    if (!rawIp) {
      return NextResponse.json(
        { error: "Access denied. Missing IP headers; dashboard API is restricted to localhost." },
        { status: 403 }
      );
    }

    if (!LOCAL_IPS.has(rawIp)) {
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
