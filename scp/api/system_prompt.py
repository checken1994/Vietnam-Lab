# SPDX-License-Identifier: AGPL-3.0-only
"""HC-54 — Capability-aware system prompt builder.

TẠI SAO: system prompt trước đây là chuỗi tĩnh hứa hẹn "full physical
Computer-Use capabilities" (noVNC Desktop GUI, Chromium CDP, PC Controller,
Task Kernel) BẤKỂ profile hiện tại cho phép gì. Với `SCP_API_PROFILE=core`
(default production), các route group `hands`/`pc_controller`/`web_control`
bị tắt (`scp/api/route_profile.py:GROUP_MINIMUM`) nhưng LLM vẫn tự xưng là
orchestrator của những năng lực đó → prompt hứa hẹn năng lực mà hệ thống
không có (over-promising), kết hợp directive "không đưa ra câu thoái thác"
khiến model bịa khả năng.

Builder này chỉ liệt kê năng lực mà route profile hiện tại thực sự bật
(qua `route_group_enabled`), và nói TƯỜNG MINH các năng lực đang TẮT để
model không hứa hẹn chúng. Task Kernel thuộc profile `core` nên luôn_enabled.
"""
from __future__ import annotations

from collections.abc import Mapping

from scp.api.route_profile import resolve_api_profile, route_group_enabled

# Capability → route group quản nó trong route_profile.GROUP_MINIMUM.
# Nhóm `trace` (TaskKernel/trace) thuộc level `core` nên không cần liệt kê
# vào phần tắt/bật — luôn có mặt.
_CAPABILITIES_VI: dict[str, str] = {
    "pc_controller": "PC Controller (thực thi lệnh hệ điều hành thật)",
    "hands": "Hands — tự động hóa trình duyệt (Chromium CDP)",
    "web_control": "Desktop GUI noVNC và điều khiển web",
}
_CAPABILITIES_EN: dict[str, str] = {
    "pc_controller": "PC Controller (real OS-level command execution)",
    "hands": "Hands — browser automation (Chromium CDP)",
    "web_control": "noVNC Desktop GUI and web control",
}

_BASE_VI = (
    "Bạn là SCP — một trợ lý AI thông minh. Giao tiếp tự nhiên, thân thiện và chính xác bằng tiếng Việt. "
    "Trả lời ngắn gọn, rõ ràng, trung thực và hỗ trợ thảo luận mở. Chỉ trả lời câu hỏi HIỆN TẠI ở cuối yêu cầu. "
    "Không tiếp tục chủ đề cũ nếu câu hỏi mới đổi chủ đề. Nếu thiếu dữ liệu, nói rõ chưa đủ dữ liệu thay vì đoán."
)
_BASE_EN = (
    "You are SCP — an intelligent AI assistant. Respond naturally, fluently, and accurately in English. "
    "Provide clear, honest, and helpful explanations. Answer the CURRENT question at the end of the prompt. "
    "Do not continue previous topics if the topic has changed. State clearly if data is insufficient rather than guessing."
)

# [DEHARDCODE 2026-10-30] Lời tự mô tả của SCP KHÔNG còn là chuỗi trong code:
# đọc từ data/identity/scp_self_description.json sau khi kiểm hash canonical
# (scp.runtime.self_description). Code chỉ giữ QUY TẮC hành vi (không nhận
# danh vendor nền) — đó là chính sách, không phải nội dung trả lời.
_IDENTITY_RULE_VI = (
    " Khi được hỏi ai tạo ra bạn, nguồn gốc, nền tảng hoặc dữ liệu huấn luyện, trả lời trung lập "
    "theo định danh này; không tự nhận được phát triển, huấn luyện hay vận hành bởi bất kỳ nhà cung "
    "cấp mô hình nền nào (NVIDIA, OpenAI, Anthropic, Google, Meta...)."
)
_IDENTITY_RULE_EN = (
    " When asked who created you, your origin, platform, or training data, answer neutrally per this "
    "identity; never claim to be developed, trained, or operated by any underlying model vendor "
    "(NVIDIA, OpenAI, Anthropic, Google, Meta...)."
)
_IDENTITY_MISSING_VI = (
    " ĐỊNH DANH: bản ghi tự mô tả không khả dụng (kiểm toàn vẹn thất bại) — không đưa ra tuyên bố "
    "nào về nguồn gốc hay năng lực của bạn ngoài tên SCP."
)
_IDENTITY_MISSING_EN = (
    " IDENTITY: the self-description record is unavailable (integrity check failed) — make no claim "
    "about your origin or capabilities beyond the name SCP."
)


def _identity_block(vi: bool) -> str:
    """Khối định danh từ DATA (đã kiểm toàn vẹn) + quy tắc chống nhận vendor."""
    from scp.runtime.self_description import load_self_description

    record = load_self_description()
    rule = _IDENTITY_RULE_VI if vi else _IDENTITY_RULE_EN
    if record is None:
        return (_IDENTITY_MISSING_VI if vi else _IDENTITY_MISSING_EN) + rule
    head = "ĐỊNH DANH" if vi else "IDENTITY"
    return f" {head} ({record.ref}): {record.description('vi' if vi else 'en')}{rule}"


def _resolve_enabled_groups(environ: Mapping[str, str] | None) -> tuple[str, list[str]] | None:
    """Trả về (profile, [group enabled]) hoặc None nếu profile cấu hình sai.

    Fail-conservative: profile không hợp lệ → coi như KHÔNG có năng lực nào
    được phép hứa (chỉ core), thay vì hứa tất cả.
    """
    try:
        profile = resolve_api_profile(environ)
        enabled = [g for g in _CAPABILITIES_VI if route_group_enabled(g, profile, environ)]
        return profile, enabled
    except Exception:
        return None


def build_system_prompt(lang: str, environ: Mapping[str, str] | None = None) -> str:
    """Dựng system prompt capability-aware cho lane chat/generation.

    Args:
        lang: "vi" hoặc ngôn ngữ khác (mặc định khối tiếng Anh).
        environ: mapping env để test hermetic; None = os.environ thật.
    """
    vi = str(lang or "").strip().lower() == "vi"
    resolved = _resolve_enabled_groups(environ)

    if vi:
        base, caps, identity = _BASE_VI, _CAPABILITIES_VI, _identity_block(True)
        enabled_word, disabled_word = "đang BẬT", "đang TẮT"
    else:
        base, caps, identity = _BASE_EN, _CAPABILITIES_EN, _identity_block(False)
        enabled_word, disabled_word = "ENABLED", "DISABLED"

    if resolved is None:
        # Profile cấu hình sai → không được hứa năng lực nào ngoài core.
        if vi:
            warn = (
                " LƯU Ý NĂNG LỰC: profile API hiện tại cấu hình không hợp lệ, do đó chỉ các năng lực lõi "
                "(Task Kernel, hỏi-đáp) được đảm bảo. Tuyệt đối không hứa hẹn hoặc tự nhận có các năng lực "
                "Computer-Use (Desktop GUI, trình duyệt, PC Controller) — nếu được hỏi, trả lời rằng trạng thái "
                "năng lực đó không xác định được và cần kiểm tra cấu hình."
            )
        else:
            warn = (
                " CAPABILITY NOTE: the current API profile configuration is invalid, so only core capabilities "
                "(Task Kernel, Q&A) are guaranteed. Never promise or claim Computer-Use capabilities "
                "(Desktop GUI, browser, PC Controller); if asked, state that their status is undetermined "
                "and configuration must be checked."
            )
        return base + warn + identity

    _profile, enabled_groups = resolved
    enabled_names = [caps[g] for g in enabled_groups]
    disabled_names = [caps[g] for g in caps if g not in enabled_groups]

    if vi:
        parts: list[str] = []
        if enabled_names:
            parts.append(
                " NĂNG LỰC " + enabled_word + ": " + ", ".join(enabled_names) + ". "
                "Bạn trả lời với thẩm quyền của bộ điều phối (orchestrator) các năng lực này, "
                "tuyệt đối không đưa ra các câu thoái thác rập khuôn về việc thiếu khả năng vận hành "
                "hoặc triển khai thực tế ĐỐI VỚI CÁC NĂNG LỰC ĐÃ LIỆT KÊ."
            )
        if disabled_names:
            parts.append(
                " NĂNG LỰC " + disabled_word + " trong profile hiện tại: " + ", ".join(disabled_names) + ". "
                "Tuyệt đối không hứa hẹn, không tự nhận có, và không mô phỏng các năng lực này; "
                "nếu người dùng yêu cầu, trả lời rõ rằng chúng đang bị tắt trong profile hiện tại."
            )
    else:
        parts = []
        if enabled_names:
            parts.append(
                " CAPABILITIES " + enabled_word + ": " + ", ".join(enabled_names) + ". "
                "Answer authoritatively as the orchestrator of these capabilities, without issuing generic canned "
                "disclaimers claiming lack of real-world operational or implementation ability — "
                "FOR THE LISTED CAPABILITIES ONLY."
            )
        if disabled_names:
            parts.append(
                " CAPABILITIES " + disabled_word + " in the current profile: " + ", ".join(disabled_names) + ". "
                "Never promise, claim, or simulate these capabilities; if requested, clearly state that "
                "they are disabled in the current profile."
            )

    return base + "".join(parts) + identity


__all__ = ["build_system_prompt"]
