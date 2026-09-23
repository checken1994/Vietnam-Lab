import { NextResponse } from "next/server";

/**
 * Extracts and validates caller authentication from request Authorization header or cookies.
 * Does not mint or inject server-side tokens — caller must supply valid credentials.
 */
export function extractCallerAuth(request: Request): {
  authenticated: boolean;
  authHeader: string;
  pcToken?: string;
  errorResponse?: NextResponse;
} {
  let authToken = "";
  const authHeader = request.headers.get("authorization") || request.headers.get("Authorization");
  if (authHeader && authHeader.trim()) {
    authToken = authHeader.trim();
  } else {
    const cookieHeader = request.headers.get("cookie");
    if (cookieHeader) {
      const match = cookieHeader.match(/(?:^|;\s*)(?:scp_token|session_token|token)=([^;]+)/);
      if (match && match[1]) {
        authToken = `Bearer ${decodeURIComponent(match[1].trim())}`;
      }
    }
  }

  if (!authToken) {
    return {
      authenticated: false,
      authHeader: "",
      errorResponse: NextResponse.json(
        { error: "Unauthorized: Missing authentication credentials" },
        { status: 401 }
      ),
    };
  }

  const authHeaderToSend = authToken.toLowerCase().startsWith("bearer ")
    ? authToken
    : `Bearer ${authToken}`;

  const pcToken = request.headers.get("x-scp-pc-token") || request.headers.get("X-SCP-PC-Token") || undefined;

  return {
    authenticated: true,
    authHeader: authHeaderToSend,
    pcToken,
  };
}
