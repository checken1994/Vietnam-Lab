"""[WAVE25 2026-10-26] Quality gate chống docs/landing-page HTML cho FreeAPIAdapterBridge.

[ARC-01] free_api_bridge.py đã ở 792/800 LOC — heuristic này tách ra file riêng
(theo pattern bridge_lookup.py) thay vì nhồi vào bridge. FreeAPICatalog có nhiều
entry trỏ trang docs/landing (CHECKER bắt live: entry "DataCube AI" trả HTML
landing "Free AI News API | Data Cube AI Skip to content..." thay vì dữ liệu
JSON) — các trang này là web-chrome, KHÔNG phải dữ liệu API, nhưng vẫn qua được
term gate khi landing chứa đúng từ khóa câu hỏi (vd "latest ... news").

Hợp đồng fail-safe:
  - JSON hợp lệ LUÔN pass gate này: content-type chứa "json" HOẶC body bắt đầu
    '{'/'[' (sau strip) → False ngay, không đọc marker — nội dung JSON chứa chữ
    "cookie"/"sign in" là dữ liệu thật, không bị ám sát.
  - Chỉ text phi-JSON có ≥2 marker web-chrome trong 500 ký tự đầu (sau khi bỏ
    tag) mới bị coi là landing page — 1 marker đơn lẻ không đủ kết tội.
  - Body rỗng → False (để caller tự fail-closed theo đường _extract_readable cũ).

Tabula Rasa: marker là dấu hiệu CẤU TRÚC web-chrome, không phải dữ liệu sự kiện.
"""
from __future__ import annotations

import re

# Dấu hiệu web-chrome của trang docs/landing (đều là chrome, không phải dữ liệu
# JSON/CSV/API). Danh sách ghim theo mandate [WAVE25 2026-10-26] — thêm marker
# là quyết định thiết kế tường minh, không thêm ngẫu nhiên.
_DOCS_LANDING_MARKERS: tuple[str, ...] = (
    "skip to content",
    "sign in",
    "cookie",
    "subscribe",
    "navigation menu",
    "© 20",
    "all rights reserved",
    "privacy policy",
)

# Ngưỡng kết tội: ≥2 marker trong 500 ký tự đầu của text đã bỏ tag.
_DOCS_LANDING_MIN_MARKERS = 2
_DOCS_LANDING_PROBE_CHARS = 500

# Bỏ <script>/<style> rồi tag HTML — cùng xử lý với _extract_readable của bridge
# nhưng tự trị (module này không import ngược bridge — tránh cycle).
_SCRIPT_STYLE_RE = re.compile(r"<script.*?</script>|<style.*?</style>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def is_docs_landing_page(raw_text: str, content_type: str = "") -> bool:
    """True nếu raw body trông như trang docs/landing HTML (bỏ entry này).

    Args:
        raw_text: body THÔ đã decode utf-8 (chưa strip tag).
        content_type: Content-Type của response (đã/không lower đều được).

    Returns:
        True → caller bỏ candidate, thử entry kế (fail-closed None nếu hết).
        JSON (content-type json hoặc body '{'/'[') luôn False — không ám sát.
    """
    raw = (raw_text or "").strip()
    if not raw:
        return False
    if "json" in (content_type or "").lower() or raw[0] in "[{":
        # JSON hợp lệ LUÔN pass — kể cả khi value chứa chữ trùng marker.
        return False
    # [WAVE25-FIX2 2026-10-26] Live probe CHECKER (spaceflightnewsapi.net —
    # Docusaurus): landing hiện đại có 0/8 marker trong 500 ký tự đầu → gate
    # marker-based lọt. Quy tắc cấu trúc bổ sung: body non-JSON bắt đầu
    # bằng HTML document (`<!doctype html` / `<html`) là WEB PAGE, không bao
    # giờ là payload data-API → landing. An toàn vì JSON-always-pass phía
    # trên đã bảo vệ mọi JSON (kể cả JSON chèn snippet HTML ở giữa value —
    # raw[0] là '{'/'[' nên không vào nhánh này).
    lowered_head = raw[:256].lower()
    if lowered_head.startswith("<!doctype html") or lowered_head.startswith("<html"):
        return True
    stripped = _WS_RE.sub(" ", _TAG_RE.sub(" ", _SCRIPT_STYLE_RE.sub(" ", raw)))
    probe = stripped.lower()[:_DOCS_LANDING_PROBE_CHARS]
    hits = sum(1 for marker in _DOCS_LANDING_MARKERS if marker in probe)
    return hits >= _DOCS_LANDING_MIN_MARKERS


__all__ = ["is_docs_landing_page"]
