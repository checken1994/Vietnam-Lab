import logging
import re
from datetime import datetime

from scp.security.env_loader import load_selected_env

load_selected_env()

logger = logging.getLogger("scp.runtime.judge_llm")

_JUDGE_SYSTEM = "You are a factual judge. You MUST output exactly the word PASS or FAIL and nothing else."


def _current_date() -> str:
    """[W8-e1 2026-10-05] Seam NGÀY HIỆN TẠI cho judge/cascade system prompt.

    Root-cause q08 (W7-battery run C, GA.md B1b): judge uphold answer stale
    ("Joe Biden là tổng thống Mỹ hiện tại") vì (i) system prompt không có ngày
    hiện tại — LLM judge chỉ còn general knowledge với training cutoff cũ,
    và (ii) hai opinion crosscheck (2 family khác lineage theo _family_key)
    vẫn có thể cùng stale cutoff = cùng blind spot trên trục thời gian (DNA #5
    residual: độc lập provider ≠ độc lập kiến thức).

    Seam: module-level function để test hermetic monkeypatch NGÀY GIẢ
    (không dùng os.environ trong test). Chỉ datetime.now tại chỗ này.
    """
    return datetime.now().strftime("%Y-%m-%d")


def _judge_system_prompt() -> str:
    """[W8-e1] System prompt cho judge/cascade — gắn ngày hiện tại.

    Contract stale-fact: claim time-sensitive chỉ đúng ở thời điểm quá khứ
    phải bị chấm FAIL (không PASS-thuần); ngày hiện tại là mốc tham chiếu
    bắt buộc để model không chấm theo cutoff huấn luyện của nó.
    """
    return (
        f"Current date: {_current_date()}. Treat any time-sensitive claim "
        "(who currently holds an office, latest facts, 'today') against THIS "
        "date; an answer that was true only at an earlier date is FAIL. "
        + _JUDGE_SYSTEM
    )

# [MẢNH GHÉP 3 — XML/Structured verdict] Reasoning models (DeepSeek-R1, QwQ...)
# bày <think> block TRƯỚC đáp án. `"PASS" in content` cũ bị đánh lừa: think
# block chứa "PASS" trong khi đáp án cuối là FAIL (hoặc ngược lại). Luật mới:
#   1. Cắt toàn bộ <think>...</think> — suy luận nội bộ KHÔNG phải phán quyết.
#   2. Lấy token phán quyết CUỐI CÙNG trong phần còn lại (đáp án wins).
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_VERDICT_RE = re.compile(r"\b(PASS|FAIL)\b")


def _parse_verdict(content: str | None) -> str | None:
    """PASS | FAIL | None (ambiguous). Máy đọc, không cảm tính."""
    if not content:
        return None
    cleaned = _THINK_RE.sub(" ", content).upper()
    matches = _VERDICT_RE.findall(cleaned)
    if not matches:
        return None
    final = matches[-1]
    return final if final in {"PASS", "FAIL"} else None


def _llm_judge(question: str, ai_answer: str, context: str = "") -> bool | None:
    """Semantic judge — TRI-STATE cascade (Cổng D, 2 tiers).

    Trả về:
      True  — PASS (model phán đúng dựa trên context/knowledge)
      False — FAIL (cả primary lẫn second-opinion model đều chối)
      None  — CHƯA QUYẾT ĐỊNH ĐƯỢC: LLM lỗi, trả lời mơ hồ, hoặc
              HAI MODEL BẤT ĐỒNG → caller phải ESCALATE cho người
              (fail-closed đúng nghĩa: không ai bịa quyết định).

    Cascade: primary = task="judge" provider; nếu FAIL → second opinion qua
    task="autofix" provider (model mạnh hơn trong warehouse). Chỉ khi CẢ HAI
    cùng FAIL mới là FAIL thật.
    """
    try:
        from scp.llm_gateway import get_gateway

        prompt = f"Question: {question}\nContext: {context}\nAI Answer: {ai_answer}\nEvaluate if the AI Answer correctly answers the Question based ONLY on the Context (if provided) or general knowledge. Output only PASS or FAIL."
        gateway = get_gateway()
        first_content, _primary = gateway.chat_sync(
            # [W8-e1] system prompt gắn ngày hiện tại (stale-fact contract q08).
            prompt, system_prompt=_judge_system_prompt(), task="judge"
        )
        first = _parse_verdict(first_content)
        if first == "PASS":
            return True
        if first is None:
            logger.warning("LLM judge ambiguous/empty (provider=%s) — escalate", _primary)
            return None
        # Primary nói FAIL → mượn não model mạnh hơn trước khi kết luận.
        second_content, _second = gateway.chat_sync(
            prompt, system_prompt=_judge_system_prompt(), task="autofix"
        )
        second = _parse_verdict(second_content)
        if second == "PASS":
            logger.warning("Judge cascade disagreement (%s=FAIL vs %s=PASS) — escalate", _primary, _second)
            return None
        if second == "FAIL":
            return False
        return None
    except Exception as e:
        # silent-by-design: error is printed and None returned — caller treats None as 'no verdict'
        logger.error(f"LLM Judge error: {e}", exc_info=True)
        return None

async def _llm_judge_async(question: str, ai_answer: str, context: str = "") -> bool | None:
    from scp.llm_gateway import get_gateway
    try:
        prompt = f"Question: {question}\nContext: {context}\nAI Answer: {ai_answer}\nEvaluate if the AI Answer correctly answers the Question based ONLY on the Context (if provided) or general knowledge. Output only PASS or FAIL."
        gateway = get_gateway()
        first_content, _primary = await gateway.chat(
            # [W8-e1] system prompt gắn ngày hiện tại (stale-fact contract q08).
            prompt, system_prompt=_judge_system_prompt(), task="judge"
        )
        first = _parse_verdict(first_content)
        if first == "PASS":
            return True
        elif first not in {"PASS", "FAIL"}:
            logger.warning("LLM judge ambiguous/empty (provider=%s) — escalate", _primary)
            return None

        second_content, _second = await gateway.chat(
            prompt, system_prompt=_judge_system_prompt(), task="autofix"
        )
        second = _parse_verdict(second_content)
        if second == "PASS":
            logger.warning("Judge cascade disagreement (%s=FAIL vs %s=PASS) — escalate", _primary, _second)
            return None

        return False
    except Exception as e:
        logger.error("LLM judge cascade failed: %s", e, exc_info=True)
        return None
