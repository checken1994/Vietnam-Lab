"""
Chain-of-Thought (CoT) + Self-Consistency + Reflection Integration
===================================================================
Tích hợp 3 industry AI patterns vào SCP V105:

1. Chain-of-Thought (CoT) — step-by-step reasoning before answer
   Industry: OpenAI o1, Claude 3.5 Sonnet, Gemini 2.0 Flash Thinking
   SCP: thêm CoT prompt template cho LLM gateway

2. Self-Consistency — sample N times, vote majority
   Industry: Wang et al. 2022, cited 3000+
   SCP: thêm multi-sample voting trong _llm_verify_reality_check

3. Reflection / Reflexion — self-critique + retry
   Industry: Shinn et al. 2023 (Reflexion), cited 1500+
   SCP: thêm self-critique loop khi verdict = FAIL/UNKNOWN

Usage:
    from scp.ai_patterns import ChainOfThought, SelfConsistency, Reflection
"""

import logging
import os
from typing import Optional

logger = logging.getLogger("scp.ai_patterns")


# ============================================================
# 1. CHAIN-OF-THOUGHT (CoT)
# ============================================================
class ChainOfThought:
    """Chain-of-Thought prompting — step-by-step reasoning.

    Industry: OpenAI o1 uses "thinking" tokens, Claude 3.5 Sonnet uses
    extended thinking, Gemini 2.0 Flash Thinking mode.

    SCP integration: augment LLM prompt với CoT template.
    """

    COT_SYSTEM_PROMPT = """Bạn là SCP Reality Verifier với Chain-of-Thought reasoning.

Nhiệm vụ: kiểm tra xem câu trả lời có chính xác không.

Hãy suy nghĩ từng bước:
1. Phân tích câu hỏi — loại câu hỏi gì? (factual, opinion, calculation)
2. Trích xuất claim chính từ câu trả lời
3. Kiểm tra claim có đúng không (dựa trên kiến thức)
4. Nếu có số liệu, kiểm tra plausibility
5. Kết luận: CORRECT, INCORRECT, hoặc UNCERTAIN

Phản hồi theo định dạng:
REASONING: <suy nghĩ từng bước>
VERDICT: CORRECT|INCORRECT|UNCERTAIN
CONFIDENCE: <0.0-1.0>
EXPLANATION: <giải thích ngắn gọn>"""

    @classmethod
    def augment_prompt(cls, question: str, answer: str) -> tuple[str, str]:
        """ DEPRECATED — 0 callers (vulture confirmed).
        Kept for backward compat. Use RealityJudge._llm_verify_reality_check instead.
        """
        user_prompt = (
            f"Câu hỏi: {question[:500]}\n"
            f"Câu trả lời cần kiểm tra: {answer[:500]}\n\n"
            f"Hãy phân tích từng bước và kết luận."
        )
        return cls.COT_SYSTEM_PROMPT, user_prompt


# ============================================================
# 2. SELF-CONSISTENCY
# ============================================================
class SelfConsistency:
    """Self-Consistency — sample N times, vote majority.

    Industry: Wang et al. 2022 "Self-Consistency Improves Chain of Thought
    Reasoning in Language Models" — cited 3000+.
    Pattern: sample N=3-5 times với temperature=0.7, vote majority verdict.

    SCP integration: trong _llm_verify_reality_check, gọi LLM N times + vote.
    """

    DEFAULT_SAMPLES = 3  # Balance: 3 samples = good accuracy + acceptable latency

    @classmethod
    def verify_with_voting(
        cls,
        llm_client,
        question: str,
        answer: str,
        system_prompt: str,
        n_samples: int | None = None,
    ) -> dict:
        """ DEPRECATED — 0 callers (vulture confirmed).
        Superseded by RealityJudge multi-SLM consensus (R8+). Kept for backward compat.
        """
        """Verify answer using self-consistency voting.

        Returns: {
            "verdict": "CORRECT"|"INCORRECT"|"UNCERTAIN",
            "confidence": float,
            "agreement": float,  # % samples agreeing
            "explanations": list[str],
        }
        """
        n = n_samples or int(os.environ.get("SCP_SELF_CONSISTENCY_N", cls.DEFAULT_SAMPLES))
        verdicts = []
        explanations = []
        confidences = []

        for i in range(n):
            try:
                response, provider = llm_client.chat_sync(
                    question, context="", system_prompt=system_prompt
                )
                if not response:
                    continue

                # Parse verdict
                verdict = "UNCERTAIN"
                conf = 0.5
                for line in response.split("\n"):
                    line_upper = line.upper()
                    if "VERDICT:" in line_upper:
                        if "CORRECT" in line_upper and "INCORRECT" not in line_upper:
                            verdict = "CORRECT"
                        elif "INCORRECT" in line_upper:
                            verdict = "INCORRECT"
                        else:
                            verdict = "UNCERTAIN"
                    elif "CONFIDENCE:" in line_upper:
                        try:
                            conf_str = line.split(":")[-1].strip()
                            conf = float(conf_str)
                        except Exception:
                            logger.exception("[ai_patterns.py:134] silenced exception")

                verdicts.append(verdict)
                confidences.append(conf)
                explanations.append(response[:200])
            except Exception as e:
                logger.warning(f"Self-consistency sample {i+1} failed: {e}")
                continue

        if not verdicts:
            return {"verdict": "UNCERTAIN", "confidence": 0.0, "agreement": 0.0, "explanations": []}

        # Vote majority
        from collections import Counter
        vote = Counter(verdicts)
        majority_verdict, majority_count = vote.most_common(1)[0]
        agreement = majority_count / len(verdicts)
        avg_conf = sum(confidences) / len(confidences)

        return {
            "verdict": majority_verdict,
            "confidence": avg_conf,
            "agreement": agreement,
            "explanations": explanations,
        }


# ============================================================
# 3. REFLECTION / REFLEXION
# ============================================================
class Reflection:
    """Reflection — self-critique + retry.

    Industry: Shinn et al. 2023 "Reflexion: Language Agents with Verbal
    Reinforcement Learning" — cited 1500+.
    Pattern: when verdict = FAIL/UNKNOWN, self-critique → retry.

    SCP integration: sau khi LLM verify FAIL/UNKNOWN, ask LLM to self-critique
    + retry 1 lần. Nếu retry PASS → upgrade verdict.
    """

    REFLECTION_PROMPT = """Bạn vừa kiểm tra một câu trả lời và kết luận {verdict}.

Câu hỏi: {question}
Câu trả lời: {answer}
Lý do: {reasoning}

Hãy tự phê bình (self-critique):
1. Có thể bạn đã hiểu sai câu hỏi không?
2. Có thể câu trả lời đúng theo một cách diễn giải khác không?
3. Có thông tin bạn bỏ qua không?

Sau đó đưa ra kết luận cuối:
FINAL_VERDICT: CORRECT|INCORRECT|UNCERTAIN
FINAL_CONFIDENCE: <0.0-1.0>"""

    MAX_REFLECTIONS = 1  # 1 retry to limit latency

    # [Q07 2026-09-15] Controlled self-correction wiring for the /ask path.
    #
    # TẠI SAO (DNA #1/#19): benchmark F_self_correction = 0/8 vì khi canonical
    # verify trả FAIL/UNKNOWN, đường /ask chỉ withhold — class Reflection tồn
    # tại nhưng chưa từng được gọi. reflect_and_retry ở dưới là SELF-APPROVAL
    # (dùng LLM để FLIP verdict FAIL->CORRECT) và bị CẤM trong scope Q07: nó vi
    # phạm invariant "Reflection không tự approve".
    #
    # Thiết kế CÓ KIỂM SOÁT: Reflection chỉ chịu trách nhiệm về PHÍA CRITIQUE
    # (biên dịch thất bại của independent verifier thành gợi ý self-critique),
    # còn ANSWER mới được regenerate bởi chính primary pipeline (handler) và
    # QUYẾT ĐỊNH accept/reject hoàn toàn thuộc canonical verify_response. Vì
    # vậy Reflection là một thành phần có dùng (critique_turn / MAX_REFLECTIONS
    # được scp.ask_kernel_adapter gọi) nhưng không có quyền phê duyệt.

    @classmethod
    def critique_turns(
        cls,
        question: str,
        failed_answer: str,
        verification: dict,
    ) -> list[dict[str, str]]:
        """Biến verdict thất bại của canonical verifier thành self-critique.

        [Q07] Được scp/ask_kernel_adapter.py:finalize() dùng khi canonical
        verify trả FAIL/UNKNOWN (epistemic hold) để shape lại request regenerate.
        KHÔNG chứa bất kỳ claim "đúng/sai" nào do Reflection tự quyết: mọi lý do
        đều lấy từ verifier độc lập (failures) — Reflection chỉ trình bày lại.

        Trả về conversation turns (role user/assistant) để primary pipeline xem
        như CONTEXT (tham khảo), không phải instruction — question hiện tại vẫn
        có thẩm quyền (khớp AskRequest.contract: history là context, question là
        yêu cầu). Content được cắt ngắn và số vòng bị chặn để bảo toàn budget.
        """
        failures = [str(f) for f in (verification.get("failures") or [])][:6]
        grounded = verification.get("grounded_ratio")
        turns: list[dict[str, str]] = []
        answer = str(failed_answer or "").strip()
        if answer and not answer.startswith("[SCP:"):
            turns.append({"role": "assistant", "content": f"[câu trả lời trước, đã bị từ chối] {answer[:400]}"})
        reasons = ", ".join(failures) if failures else "insufficient evidence"
        grounding_note = "" if grounded is None else f" (grounded_ratio={grounded})"
        turns.append({
            "role": "user",
            "content": (
                "[tự phê bình] Câu trả lời trước ĐÃ BỊ independent verifier từ chối"
                f" vì: {reasons}{grounding_note}. Hãy đọc LẠI đúng câu hỏi hiện tại,"
                " tránh claim bị từ chối, và đưa ra ĐÚNG MỘT câu trả lời ngắn gọn,"
                " chính xác nhất có thể. Nếu thực sự thiếu dữ liệu, nói rõ chưa đủ dữ liệu."
            ),
        })
        return turns[-6:]

    @classmethod
    def should_reflect(cls, verification: dict) -> bool:
        """Reflection chỉ chạy cho epistemic hold (FAIL/UNKNOWN), không phải
        cho answer đã VERIFIED. Adapter vẫn là nơi enforce budget/timeout."""
        if not isinstance(verification, dict):
            return False
        return str(verification.get("verdict")) != "VERIFIED"

    @classmethod
    def reflect_and_retry(
        cls,
        llm_client,
        question: str,
        answer: str,
        original_verdict: str,
        original_reasoning: str,
    ) -> dict:
        """Self-critique + retry when verdict is FAIL/UNKNOWN.

        Returns: {
            "verdict": str,
            "confidence": float,
            "reflected": bool,
            "reasoning": str,
        }
        """
        if original_verdict == "CORRECT":
            # No need to reflect on correct answers
            return {
                "verdict": original_verdict,
                "confidence": 1.0,
                "reflected": False,
                "reasoning": original_reasoning,
            }

        prompt = cls.REFLECTION_PROMPT.format(
            verdict=original_verdict,
            question=question[:300],
            answer=answer[:300],
            reasoning=original_reasoning[:200],
        )

        try:
            response, provider = llm_client.chat_sync(
                prompt, context="", system_prompt="Bạn là AI self-critique system."
            )
            if not response:
                return {
                    "verdict": original_verdict,
                    "confidence": 0.3,
                    "reflected": False,
                    "reasoning": "Reflection failed — no LLM response",
                }

            # Parse final verdict
            final_verdict = original_verdict
            final_conf = 0.3
            for line in response.split("\n"):
                line_upper = line.upper()
                if "FINAL_VERDICT:" in line_upper:
                    if "CORRECT" in line_upper and "INCORRECT" not in line_upper:
                        final_verdict = "CORRECT"
                    elif "INCORRECT" in line_upper:
                        final_verdict = "INCORRECT"
                    else:
                        final_verdict = "UNCERTAIN"
                elif "FINAL_CONFIDENCE:" in line_upper:
                    try:
                        final_conf = float(line.split(":")[-1].strip())
                    except Exception:
                        logger.exception("[ai_patterns.py:254] silenced exception")

            return {
                "verdict": final_verdict,
                "confidence": final_conf,
                "reflected": True,
                "reasoning": response[:300],
            }
        except Exception as e:
            logger.warning(f"Reflection failed: {e}")
            return {
                "verdict": original_verdict,
                "confidence": 0.3,
                "reflected": False,
                "reasoning": f"Reflection error: {e}",
            }


# ============================================================
# 4. TREE OF THOUGHTS (ToT)
# ============================================================
class TreeOfThoughts:
    """Tree of Thoughts — explore multiple reasoning paths.

    Industry: Yao et al. 2023 "Tree of Thoughts: Deliberate Problem Solving
    with Large Language Models" — cited 1000+.
    Pattern: when verdict = UNCERTAIN, explore 3 alternative reasoning paths
    (different framings of the question), vote on best verdict.

    SCP integration: in _llm_verify_reality_check, if verdict = UNCERTAIN,
    call TreeOfThoughts.explore_paths(llm_client, question, answer) to get
    a more confident verdict by exploring multiple reasoning paths.

    Difference vs Self-Consistency:
    - Self-Consistency samples the SAME prompt N times (temperature variance).
    - Tree of Thoughts samples DIFFERENT prompts (semantic framings):
      Branch 1: semantic analysis ("what does the question mean?")
      Branch 2: factual verification ("extract facts, check each")
      Branch 3: counterfactual ("if wrong, what's right?")
    This gives wider coverage when the LLM is uncertain about interpretation.
    """

    MAX_BRANCHES = 3

    # [Task 17-A] 3 distinct reasoning framings — NOT just temperature variance.
    # Each branch asks the LLM to reason from a different angle, so even if
    # one branch is biased (e.g., the LLM misreads the question), the other
    # branches can correct via majority vote.
    TOT_PROMPT_TEMPLATES = [
        # Branch 1: semantic analysis — focus on what the question MEANS
        "Phân tích semantic: Câu hỏi '{q}' có ý nghĩa gì?\n"
        "Câu trả lời '{a}' có phản hồi đúng semantic không?\n"
        "Hãy phân tích từng bước rồi kết luận:\n"
        "VERDICT: CORRECT|INCORRECT|UNCERTAIN\n"
        "CONFIDENCE: <0.0-1.0>",

        # Branch 2: factual verification — extract claims, check each
        "Verify factual: Trích xuất các facts từ câu trả lời '{a}'.\n"
        "Mỗi fact có đúng không (dựa trên kiến thức + câu hỏi '{q}')?\n"
        "Hãy liệt kê từng fact rồi kết luận:\n"
        "VERDICT: CORRECT|INCORRECT|UNCERTAIN\n"
        "CONFIDENCE: <0.0-1.0>",

        # Branch 3: counterfactual — what if the answer is wrong?
        "Counterfactual: Nếu câu trả lời '{a}' sai, câu trả lời đúng sẽ là gì?\n"
        "Có thể '{a}' đúng một phần không (một fact đúng, fact khác sai)?\n"
        "Câu hỏi '{q}' yêu cầu trả lời full hay partial?\n"
        "Kết luận:\n"
        "VERDICT: CORRECT|INCORRECT|UNCERTAIN\n"
        "CONFIDENCE: <0.0-1.0>",
    ]

    @classmethod
    def explore_paths(cls, llm_client, question: str, answer: str) -> dict:
        """Explore multiple reasoning paths, vote on best verdict.

        [Task 17-A] TẠI SAO: khi verdict = UNCERTAIN, Self-Consistency re-samples
        the same prompt — if the LLM is confused by the prompt itself, re-sampling
        won't help. Tree of Thoughts samples DIFFERENT prompts (different framings),
        so if one framing is confusing, others may not be. Vote majority.

        Args:
            llm_client: Object with .chat_sync(question, context, system_prompt)
                method (e.g., scp.llm_gateway.get_gateway()).
            question: The user's question.
            answer: The AI answer to verify.

        Returns: {
            "verdict": "CORRECT"|"INCORRECT"|"UNCERTAIN",
            "confidence": float,  # avg across branches
            "agreement": float,   # % branches agreeing with majority
            "paths": list[str],   # truncated reasoning from each branch
            "branches_used": int, # how many branches succeeded (≤ MAX_BRANCHES)
        }
        """
        from collections import Counter

        verdicts = []
        confidences = []
        paths = []

        for i, template in enumerate(cls.TOT_PROMPT_TEMPLATES[:cls.MAX_BRANCHES]):
            try:
                prompt = template.format(q=question[:300], a=answer[:300])
                response, provider = llm_client.chat_sync(
                    prompt,
                    context="",
                    system_prompt="Bạn là SCP Tree-of-Thoughts reasoner. "
                                  "Explore the question from a different angle.",
                )
                if not response:
                    continue

                # Parse verdict from response
                verdict = "UNCERTAIN"
                conf = 0.5
                response.upper()
                # Look for the last VERDICT: line (in case LLM mentions it earlier)
                for line in response.split("\n"):
                    line_upper = line.upper()
                    if "VERDICT:" in line_upper:
                        if "CORRECT" in line_upper and "INCORRECT" not in line_upper:
                            verdict = "CORRECT"
                            conf = 0.75
                        elif "INCORRECT" in line_upper:
                            verdict = "INCORRECT"
                            conf = 0.75
                        else:
                            verdict = "UNCERTAIN"
                            conf = 0.4
                    elif "CONFIDENCE:" in line_upper:
                        try:
                            conf_str = line.split(":")[-1].strip()
                            if "-" in conf_str:
                                conf_str = conf_str.split("-")[0].strip() # Lấy cận dưới nếu LLM sinh ra dải
                            parsed = float(conf_str)
                            if 0.0 <= parsed <= 1.0:
                                conf = parsed
                        except Exception:
                            logger.exception("[ai_patterns.py ToT] silenced exception")

                verdicts.append(verdict)
                confidences.append(conf)
                paths.append(response[:200])
            except Exception as e:
                logger.warning(f"ToT branch {i+1} failed: {e}")
                continue

        if not verdicts:
            return {
                "verdict": "UNCERTAIN",
                "confidence": 0.0,
                "agreement": 0.0,
                "paths": [],
                "branches_used": 0,
            }

        # Vote majority
        vote = Counter(verdicts)
        majority_verdict, majority_count = vote.most_common(1)[0]
        agreement = majority_count / len(verdicts)
        avg_conf = sum(confidences) / len(confidences)

        return {
            "verdict": majority_verdict,
            "confidence": avg_conf,
            "agreement": agreement,
            "paths": paths,
            "branches_used": len(verdicts),
        }


# ============================================================
# FACTORY: get pattern by name
# ============================================================
def get_ai_pattern(pattern_name: str):
    """Get AI pattern instance by name.

    Args:
        pattern_name: "cot", "self_consistency", "reflection", "tot"

    Returns:
        Pattern class
    """
    patterns = {
        "cot": ChainOfThought,
        "self_consistency": SelfConsistency,
        "reflection": Reflection,
        "tot": TreeOfThoughts,
        "tree_of_thoughts": TreeOfThoughts,
    }
    return patterns.get(pattern_name.lower())


__all__ = [
    "ChainOfThought", "SelfConsistency", "Reflection", "TreeOfThoughts",
    "get_ai_pattern",
]
