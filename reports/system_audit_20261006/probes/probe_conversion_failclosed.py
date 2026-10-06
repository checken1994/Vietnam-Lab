"""[AUDIT-20261006 PROBE-3] ConversionDataSource fail-closed + injection.

Independent re-verification of the W12 claims:
  - unknown unit  -> None (fail-closed)
  - cross-category -> None (fail-closed)
  - question-injection cannot reach unit lookup (charset + table resolve)

Deterministic, pure functions, no network.
"""
import sys

sys.path.insert(0, r"D:\scp")

from scp.data_sources.conversion import ConversionDataSource  # noqa: E402

src = ConversionDataSource()
results = []


def check(pid, desc, observed, expect):
    ok = observed == expect
    results.append((pid, desc, observed, expect, "PASS" if ok else "FAIL"))


# Fail-closed basics
check("C1", "unknown unit pair", src.convert(1, "furlong", "km"), None)
check("C2", "cross-category km->kg", src.convert(1, "km", "kg"), None)
check("C3", "cross-category s->m", src.convert(60, "s", "m"), None)
check("C4", "empty from_unit", src.convert(1, "", "m"), None)
check("C5", "temperature unknown scale", src.convert_temperature(1, "x", "c"), None)
# Correct conversions still work (guard not over-broad)
c6 = src.convert(1, "km", "m")
check("C6", "1 km -> m == 1000", (c6 or {}).get("value"), 1000.0)
c7 = src.convert_temperature(0, "c", "f")
check("C7", "0C -> 32F", c7, 32.0)

# Question-injection shapes: payload chars must never reach unit lookup.
INJ = [
    "1 km; DROP TABLE users bằng m",
    "1 km bóng bẩy; rm -rf / sang m",
    "1 km${IFS} bằng m",
    "1 km\b bằng m",
    "1 km' OR '1'='1 bằng m",
    "1 <script>alert(1)</script> bằng m",
]
inj_results = []
for q in INJ:
    r = src.answer_from_question(q)
    inj_results.append((q[:40], "None" if r is None else f"TEXT:{r['text'][:50]}"))
# The only injection shapes that could "resolve" are ones whose unit
# substrings still match the fixed tables; then the emitted text may contain
# the matched unit substring only (charset-restricted). Assert: no answer
# text may contain shell/meta characters from the payload beyond the unit
# charset letters.
META = set(";`$><\\|&(){}[]!\"'\n\r\t")
leak = [q for q, v in inj_results if v != "None" and (META & set(v))]
check("C8", "injection payloads never leak meta chars into answer",
      leak, [])
c9 = src.answer_from_question("1 km bằng bao nhiêu mét?")
check("C9", "benign VI conversion still answers", (c9 or {}).get("text"),
      "1 km = 1000 mét")  # to_unit echoed as typed by the user

print("PROBE-3 conversion fail-closed + injection")
print("=" * 72)
fails = 0
for pid, desc, observed, expect, verdict in results:
    if verdict == "FAIL":
        fails += 1
    print(f"{verdict} {pid:4s} {desc}\n      observed={observed!s:.100s} expect={expect!s:.60s}")
print("-" * 72)
for q, v in inj_results:
    print(f"  inj: {q!r:45s} -> {v}")
print("=" * 72)
print(f"RESULT: {len(results) - fails}/{len(results)} probe groups PASS, {fails} FAIL")
