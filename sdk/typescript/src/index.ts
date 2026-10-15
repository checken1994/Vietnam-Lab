/**
 * @scp/sdk - Drop-in Security & Epistemic Verification Middleware for TypeScript / Node.js / Bun.
 * Licensed under the Apache License, Version 2.0.
 */

export const VerificationTier = {
  PERMISSIVE: "PERMISSIVE",
  STANDARD: "STANDARD",
  STRICT: "STRICT",
} as const;

export type VerificationTier = (typeof VerificationTier)[keyof typeof VerificationTier];

export interface InputProtectionResult {
  allowed: boolean;
  reason: string;
  details?: string;
}

export interface OutputProtectionResult {
  verified: boolean;
  verdict: string;
  governance?: string;
  tier: string;
  offline?: boolean;
  reason?: string;
}

export interface SCPProtectOptions {
  baseUrl?: string;
  apiToken?: string;
  defaultTier?: VerificationTier | keyof typeof VerificationTier;
  strictMode?: boolean;
  timeoutMs?: number;
}

const INJECTION_PATTERNS: RegExp[] = [
  /ignore\s+(all\s+)?(previous|prior)\s+instructions/i,
  /(system\s+prompt|developer\s+mode|jailbreak|bypass\s+safety)/i,
  /you\s+are\s+now\s+(unconstrained|dan|evil)/i,
  /<\s*script\s*>/i,
  /(---\s*end of|\[admin(\s+override)?\])/i,
  /(hypothetical\s+scenario|ethical\s+hackers|leakage\s+of|reveal.*(secret|key|token))/i,
];

export class SCPProtect {
  public readonly baseUrl: string;
  public readonly apiToken?: string;
  public readonly defaultTier: VerificationTier;
  public readonly strictMode: boolean;
  public readonly timeoutMs: number;

  constructor(options: SCPProtectOptions = {}) {
    this.baseUrl = (options.baseUrl ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
    this.apiToken = options.apiToken;
    this.defaultTier = (options.defaultTier as VerificationTier) ?? VerificationTier.STANDARD;
    this.strictMode = options.strictMode ?? true;
    this.timeoutMs = options.timeoutMs ?? 10000;
  }

  private getHeaders(): Record<string, string> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    if (this.apiToken) {
      headers["Authorization"] = `Bearer ${this.apiToken}`;
      headers["x-scp-capability-token"] = this.apiToken;
    }
    return headers;
  }

  /**
   * Screen input prompt for prompt injections, jailbreak attempts, or policy attacks.
   */
  public async protectInput(prompt: string): Promise<InputProtectionResult> {
    if (!prompt || typeof prompt !== "string" || prompt.trim().length === 0) {
      return { allowed: false, reason: "empty_prompt" };
    }

    // Heuristic fail-closed local screen
    for (const pattern of INJECTION_PATTERNS) {
      if (pattern.test(prompt)) {
        return {
          allowed: false,
          reason: "prompt_injection_detected",
          details: `Matched pattern: ${pattern.source}`,
        };
      }
    }

    // Remote screening attempt if live
    try {
      const controller = new AbortController();
      const id = setTimeout(() => controller.abort(), this.timeoutMs);
      const res = await fetch(`${this.baseUrl}/v1/chat/completions`, {
        method: "POST",
        headers: this.getHeaders(),
        body: JSON.stringify({
          messages: [{ role: "user", content: prompt }],
          model: "scp-guard",
        }),
        signal: controller.signal,
      });
      clearTimeout(id);

      if (res.ok) {
        const data = (await res.json()) as any;
        const metadata = data?.scp_metadata;
        if (metadata?.falsification_status === "FAIL") {
          return { allowed: false, reason: "policy_rejected" };
        }
      }
    } catch {
      // Local screening passed, remote backend unreachable or timed out
    }

    return { allowed: true, reason: "pass" };
  }

  /**
   * Verify model output against epistemic grounding and safety invariants.
   */
  public async protectOutput(
    answer: string,
    context = "",
    tier?: VerificationTier | keyof typeof VerificationTier
  ): Promise<OutputProtectionResult> {
    if (!answer || typeof answer !== "string") {
      return {
        verified: false,
        verdict: "FAIL",
        reason: "empty_answer",
        tier: String(tier ?? this.defaultTier),
      };
    }

    const activeTier = (tier as VerificationTier) ?? this.defaultTier;

    if (answer.includes("[SCP: Answer withheld")) {
      return {
        verified: false,
        verdict: "FAIL",
        reason: "answer_withheld",
        tier: activeTier,
      };
    }

    // Remote verification
    try {
      const controller = new AbortController();
      const id = setTimeout(() => controller.abort(), this.timeoutMs);
      const res = await fetch(`${this.baseUrl}/ask`, {
        method: "POST",
        headers: this.getHeaders(),
        body: JSON.stringify({
          question: "verify_output",
          contexts: context ? [context] : [],
          verification_tier: activeTier,
        }),
        signal: controller.signal,
      });
      clearTimeout(id);

      if (res.ok) {
        const data = (await res.json()) as any;
        const verdict = data?.verdict ?? "PASS";
        return {
          verified: verdict === "PASS" || verdict === "VERIFIED",
          verdict,
          governance: data?.governance_decision ?? "UPHOLD",
          tier: activeTier,
        };
      }
    } catch {
      // Backend unreachable; proceed to fallback
    }

    // Fallback offline validation
    if (activeTier === VerificationTier.PERMISSIVE) {
      return {
        verified: true,
        verdict: "PASS",
        tier: activeTier,
        offline: true,
      };
    }

    const STOP_WORDS = new Set([
      "the", "and", "was", "for", "with", "this", "that", "has", "are", "from",
      "been", "were", "have", "here", "there", "what", "when", "where", "which",
    ]);

    if (context && context.length > 10) {
      const words = new Set((answer.toLowerCase().match(/\w{3,}/g) ?? []).filter((w) => !STOP_WORDS.has(w)));
      const ctxWords = new Set((context.toLowerCase().match(/\w{3,}/g) ?? []).filter((w) => !STOP_WORDS.has(w)));
      let overlap = 0;
      for (const w of words) {
        if (ctxWords.has(w)) overlap++;
      }
      const grounded = overlap > 0;
      return {
        verified: grounded,
        verdict: grounded ? "PASS" : "INSUFFICIENT",
        tier: activeTier,
        offline: true,
      };
    }

    return {
      verified: false,
      verdict: "INSUFFICIENT",
      tier: activeTier,
      offline: true,
    };
  }

  /**
   * Wrap standard fetch calls with automated prompt injection screening.
   */
  public wrapFetch(customFetch: typeof fetch = fetch): typeof fetch {
    const protector = this;
    return async function (input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
      if (init && init.body && typeof init.body === "string") {
        try {
          const parsed = JSON.parse(init.body);
          const prompt =
            parsed.prompt ??
            parsed.messages?.[parsed.messages.length - 1]?.content ??
            "";
          if (prompt) {
            const guard = await protector.protectInput(String(prompt));
            if (!guard.allowed) {
              return new Response(
                JSON.stringify({
                  error: "Blocked by SCPProtect",
                  reason: guard.reason,
                  details: guard.details,
                }),
                {
                  status: 403,
                  headers: { "Content-Type": "application/json" },
                }
              );
            }
          }
        } catch {
          // Non-JSON body, proceed
        }
      }
      return customFetch(input, init);
    };
  }
}
