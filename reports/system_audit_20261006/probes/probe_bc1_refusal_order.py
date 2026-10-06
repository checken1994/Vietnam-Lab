"""[AUDIT-20261006 PROBE-1] BC-1 assertion-verb guard — asymmetric scan order.

Hypothesis (static inference, scp/runtime/judge.py::is_refusal_abstain_answer):
digits/URL are scanned on the WHOLE answer text, but the BC-1 assertion-verb
scan (".*_ABSTAIN_ASSERTION_RE.search(lowered[match.end():])") only inspects
text AFTER each refusal marker. A copula claim placed BEFORE the refusal
marker therefore escapes the guard and classifies as honest-abstain.

Expected if implementation is correct (BC-1 contract as documented):
claim-before-marker -> False (goes to FAIL/verify path).
Expected if hypothesis is correct:
claim-before-marker -> True (rides the '[unverified -- abstain]' lane).

Deterministic, pure functions, no network, no mutation of repo state.
Evidence collected: verdict per case + which tier classified it.
"""
import sys

sys.path.insert(0, r"D:\scp")

from scp.runtime.judge import (  # noqa: E402
    is_honest_abstain_answer,
    is_refusal_abstain_answer,
)

CASES = [
    # (label, answer, expected_if_correct)
    ("BC-1 hardened: claim AFTER marker",
     "Tôi không thể xác minh — Donald Trump là tổng thống Mỹ.",
     False),
    ("claim BEFORE marker (audit hypothesis)",
     "Donald Trump là tổng thống Mỹ. Tôi không thể xác minh thêm.",
     False),
    ("claim BEFORE marker, no trailing words",
     "Donald Trump là tổng thống Mỹ. Tôi không có dữ liệu.",
     False),
    ("plain honest refusal (must stay abstain)",
     "Tôi không có dữ liệu để trả lời câu hỏi này.",
     True),
    ("digits anywhere -> claim (whole-text scan)",
     "Donald Trump 2020 là tổng thống. Tôi không có dữ liệu.",
     False),
]

results = []
all_as_expected_correct = True
for label, answer, expected_correct in CASES:
    refusal = is_refusal_abstain_answer(answer)
    honest = is_honest_abstain_answer(answer)
    observed = honest
    verdict = "GUARD-OK" if observed == expected_correct else "GUARD-GAP"
    if observed != expected_correct:
        all_as_expected_correct = False
    results.append((label, refusal, honest, expected_correct, verdict))

print("PROBE-1 BC-1 refusal-order asymmetry")
print("=" * 72)
for label, refusal, honest, expected, verdict in results:
    print(f"{verdict:9s} refusal={refusal!s:5s} honest_abstain={honest!s:5s} "
          f"(expected_if_correct={expected!s:5s}) | {label}")
print("=" * 72)
if all_as_expected_correct:
    print("RESULT: NO GAP — guard behaves as documented on all cases.")
else:
    gaps = [r for r in results if r[4] == "GUARD-GAP"]
    print(f"RESULT: {len(gaps)} GUARD-GAP case(s) — claim-before-marker escapes "
          "BC-1 scan (detector layer CONFIRMED). Full-chain impact (delivery "
          "with label) additionally requires judge escalation + benign lane "
          "(static-inference level).")
