// [AUDIT-FIX low-10] Conversation-history capping cho /api/scp/ask.
//
// TẠI SAO: route.ts từng forward `conversation_history` lên backend không có
// size cap (chỉ slice(-8) theo số item) — client có thể bơm mỗi item hàng
// chục MB → payload upstream phình to tùy ý. Helper thuần này cap BOTH
// per-item và total (serialized byte), giữ các item MỚI NHẤT, và trả flag
// `truncated` để payload báo rõ về backend.
//
// Pure module (không import Next.js) để bun/node test import trực tiếp.

export const CONVERSATION_HISTORY_MAX_ITEMS = 8;
export const CONVERSATION_HISTORY_MAX_ITEM_BYTES = 4096;
export const CONVERSATION_HISTORY_MAX_TOTAL_BYTES = 32 * 1024; // 32KB

export interface CappedHistory {
  items: Record<string, unknown>[];
  truncated: boolean;
}

function serializedByteLength(value: unknown): number {
  try {
    return new TextEncoder().encode(JSON.stringify(value ?? null)).length;
  } catch {
    // Không serialize được (circular...) → coi như oversized, fail-closed.
    return Number.MAX_SAFE_INTEGER;
  }
}

/** Truncate `text` tới <= maxBytes UTF-8, geometric shrink (O(log n) pass). */
function truncateToBytes(text: string, maxBytes: number): string {
  if (maxBytes <= 0) return "";
  let cut = text;
  while (cut.length > 0) {
    if (new TextEncoder().encode(cut).length <= maxBytes) return cut;
    cut = cut.slice(0, Math.floor(cut.length * 0.8));
  }
  return "";
}

/**
 * Cap conversation history: giữ tối đa `maxItems` item MỚI NHẤT, mỗi item
 * tối đa `maxItemBytes` (serialized), tổng tối đa `maxTotalBytes`. Item quá
 * lớn bị cắt `content` (kèm marker "…"); item cũ nhất bị drop khi vượt tổng.
 * Bất kỳ lần cắt/drop nào cũng đặt truncated=true.
 */
export function capConversationHistory(
  raw: unknown,
  maxItems: number = CONVERSATION_HISTORY_MAX_ITEMS,
  maxItemBytes: number = CONVERSATION_HISTORY_MAX_ITEM_BYTES,
  maxTotalBytes: number = CONVERSATION_HISTORY_MAX_TOTAL_BYTES,
): CappedHistory {
  let truncated = false;

  const valid = Array.isArray(raw)
    ? raw.filter(
        (it): it is Record<string, unknown> =>
          !!it && typeof it === "object" && !Array.isArray(it)
      )
    : [];
  if (valid.length !== (Array.isArray(raw) ? raw.length : 0)) {
    truncated = true; // có item không hợp lệ bị loại
  }
  const recent = valid.slice(-maxItems);
  if (valid.length > recent.length) {
    truncated = true; // slice(-8) đã drop item cũ
  }

  // Per-item cap.
  const perItem = recent.map((item) => {
    if (serializedByteLength(item) <= maxItemBytes) return item;
    truncated = true;
    const clone: Record<string, unknown> = { ...item };
    if (typeof clone.content === "string" && clone.content.length > 0) {
      const budget = maxItemBytes - 16; // chừa marker "…"
      clone.content = `${truncateToBytes(clone.content, budget)}…`;
      return clone;
    }
    // Object phức tạp quá lớn → thay bằng marker tối giản.
    return {
      role: typeof clone.role === "string" ? clone.role : "unknown",
      content: "…[truncated]",
    };
  });

  // Total cap: giữ item MỚI NHẤT trong budget.
  const kept: Record<string, unknown>[] = [];
  let total = 0;
  for (let i = perItem.length - 1; i >= 0; i--) {
    const size = serializedByteLength(perItem[i]);
    if (total + size > maxTotalBytes) {
      truncated = true;
      continue; // drop item này (cũ hơn)
    }
    total += size;
    kept.unshift(perItem[i]);
  }

  return { items: kept, truncated };
}
