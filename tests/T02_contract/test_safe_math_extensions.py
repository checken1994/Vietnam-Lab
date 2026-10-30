"""Contract tests for safe_math extensions (sqrt, can, Vietnamese phrasing, factorial).

Part of Task Việc 2 in agtask6.md:
- sqrt(...), can(...), căn bậc hai của N
- factorial N!
- Preserve legacy operators (+, -, *, /, ^ XOR)
- Anti-placebo and fail-closed safety bounds
"""
from __future__ import annotations

import math
import pytest

from scp.runtime.safe_math import safe_eval_arithmetic


class TestSafeMathLegacyPreservation:
    """Anti-regression: Ensure all legacy operations remain 100% intact."""

    def test_legacy_basic_arithmetic(self) -> None:
        assert safe_eval_arithmetic("1+1") == 2.0
        assert safe_eval_arithmetic("17*23") == 391.0
        assert safe_eval_arithmetic("10 / 2") == 5.0
        assert safe_eval_arithmetic("100 - 45") == 55.0
        assert safe_eval_arithmetic("-5 + 12") == 7.0

    def test_legacy_xor_preserved(self) -> None:
        # Operator ^ must keep legacy bitwise XOR semantics (2^10 = 8, NOT 1024)
        assert safe_eval_arithmetic("2^10") == 8.0
        assert safe_eval_arithmetic("5 ^ 3") == 6.0

    def test_legacy_overflow_safety(self) -> None:
        with pytest.raises(ValueError, match="arithmetic result outside safety bound"):
            safe_eval_arithmetic("1e308 * 1e308")

    def test_legacy_zero_division(self) -> None:
        with pytest.raises(ZeroDivisionError):
            safe_eval_arithmetic("10 / 0")


class TestSafeMathSquareRoot:
    """Tests for square root extensions: sqrt, can, and Vietnamese phrasing."""

    def test_sqrt_basic(self) -> None:
        assert safe_eval_arithmetic("sqrt(144)") == 12.0
        assert safe_eval_arithmetic("sqrt(0)") == 0.0
        assert safe_eval_arithmetic("sqrt(1)") == 1.0
        assert safe_eval_arithmetic("sqrt(100 + 44)") == 12.0
        assert safe_eval_arithmetic("10 + sqrt(144)") == 22.0
        assert safe_eval_arithmetic("sqrt(144) * 2") == 24.0

    def test_sqrt_non_perfect_square(self) -> None:
        assert math.isclose(safe_eval_arithmetic("sqrt(2)"), math.sqrt(2), rel_tol=1e-9)

    def test_can_synonym(self) -> None:
        assert safe_eval_arithmetic("can(144)") == 12.0
        assert safe_eval_arithmetic("căn(144)") == 12.0
        assert safe_eval_arithmetic("isqrt(144)") == 12.0

    def test_vietnamese_phrasing_buyer_b1(self) -> None:
        # Buyer B1 requirement: "căn bậc hai của 144" -> 12
        assert safe_eval_arithmetic("căn bậc hai của 144") == 12.0
        assert safe_eval_arithmetic("căn bậc 2 của 144") == 12.0
        assert safe_eval_arithmetic("can bac hai cua 144") == 12.0
        assert safe_eval_arithmetic("can bac 2 cua 144") == 12.0
        assert safe_eval_arithmetic("căn của 144") == 12.0
        assert safe_eval_arithmetic("can cua 144") == 12.0
        assert safe_eval_arithmetic("căn bậc hai 144") == 12.0
        assert safe_eval_arithmetic("căn 144") == 12.0

    def test_vietnamese_phrasing_with_expressions(self) -> None:
        assert safe_eval_arithmetic("căn bậc hai của (100 + 44)") == 12.0
        assert safe_eval_arithmetic("10 + căn bậc hai của 144") == 22.0
        assert safe_eval_arithmetic("căn bậc hai của 1e4") == 100.0
        assert safe_eval_arithmetic("căn bậc hai của (((1 + 2) * 2) * 24)") == 12.0
        assert safe_eval_arithmetic("căn bậc hai của căn bậc hai của 16") == 2.0
        assert safe_eval_arithmetic("căn bậc hai của 144 + căn bậc hai của 25") == 17.0

    def test_vietnamese_conversational_wrappers(self) -> None:
        assert safe_eval_arithmetic("căn bậc hai của 144?") == 12.0
        assert safe_eval_arithmetic("tính căn bậc hai của 144") == 12.0
        assert safe_eval_arithmetic("hãy tính căn bậc hai của 144") == 12.0
        assert safe_eval_arithmetic("cho biết căn bậc hai của 144") == 12.0
        assert safe_eval_arithmetic("cho tôi biết căn bậc hai của 144") == 12.0
        assert safe_eval_arithmetic("hãy cho biết căn bậc hai của 144") == 12.0
        assert safe_eval_arithmetic("tính giúp tôi căn bậc hai của 144") == 12.0
        assert safe_eval_arithmetic("căn bậc hai của 144 = ?") == 12.0
        assert safe_eval_arithmetic("căn bậc hai của 144 bằng bao nhiêu?") == 12.0
        assert safe_eval_arithmetic("căn bậc hai của 144 là bao nhiêu?") == 12.0
        assert safe_eval_arithmetic("căn bậc hai của 144 là mấy?") == 12.0
        assert safe_eval_arithmetic("căn bậc hai của 144 là gì?") == 12.0

    def test_sqrt_negative_number_fail_closed(self) -> None:
        with pytest.raises(ValueError, match="square root of negative number"):
            safe_eval_arithmetic("sqrt(-1)")
        with pytest.raises(ValueError, match="square root of negative number"):
            safe_eval_arithmetic("can(-144)")
        with pytest.raises(ValueError, match="square root of negative number"):
            safe_eval_arithmetic("căn bậc hai của -9")

    def test_unsupported_root_degree_fail_closed(self) -> None:
        with pytest.raises(ValueError, match="unsupported root degree"):
            safe_eval_arithmetic("căn bậc 3 của 8")
        with pytest.raises(ValueError, match="unsupported root degree"):
            safe_eval_arithmetic("can bac 3 cua 8")
        with pytest.raises(ValueError, match="unsupported root degree"):
            safe_eval_arithmetic("căn bậc ba của 27")


class TestSafeMathFactorial:
    """Tests for factorial extensions: N! and factorial()."""

    def test_factorial_basic(self) -> None:
        assert safe_eval_arithmetic("5!") == 120.0
        assert safe_eval_arithmetic("0!") == 1.0
        assert safe_eval_arithmetic("1!") == 1.0
        assert safe_eval_arithmetic("3!") == 6.0
        assert safe_eval_arithmetic("6!") == 720.0

    def test_factorial_in_compound_expressions(self) -> None:
        assert safe_eval_arithmetic("3! + 4!") == 30.0
        assert safe_eval_arithmetic("2 * 3!") == 12.0
        assert safe_eval_arithmetic("3! * 2") == 12.0
        assert safe_eval_arithmetic("(2 + 3)!") == 120.0
        assert safe_eval_arithmetic("sqrt(144)!") == 479001600.0
        assert safe_eval_arithmetic("can(144)!") == 479001600.0
        assert math.isclose(safe_eval_arithmetic("căn bậc hai của 4!"), math.sqrt(24))
        assert math.isclose(safe_eval_arithmetic("căn bậc hai của 5!"), math.sqrt(120))
        assert safe_eval_arithmetic("(căn bậc hai của 4)!") == 2.0
        assert safe_eval_arithmetic("3!!") == 720.0

    def test_factorial_named_function(self) -> None:
        assert safe_eval_arithmetic("factorial(5)") == 120.0
        assert safe_eval_arithmetic("fact(5)") == 120.0

    def test_factorial_bounds_and_safety(self) -> None:
        # Negative operand
        with pytest.raises(ValueError, match="factorial of negative number"):
            safe_eval_arithmetic("(-5)!")

        # Non-integer operand
        with pytest.raises(ValueError, match="factorial requires integer operand"):
            safe_eval_arithmetic("5.5!")

        # Operand too large (CPU exhaustion guard: n <= 100)
        with pytest.raises(ValueError, match="factorial operand too large"):
            safe_eval_arithmetic("101!")

        # 100! should succeed within safety bound
        res_100 = safe_eval_arithmetic("100!")
        assert res_100 > 0.0
        assert math.isclose(res_100, float(math.factorial(100)), rel_tol=1e-9)


class TestSafeMathSecurityAndASTBoundaries:
    """Anti-placebo tests verifying fail-closed AST security constraints."""

    def test_code_execution_blocked(self) -> None:
        dangerous_inputs = [
            "__import__('os').system('echo pwned')",
            "eval('1+1')",
            "exec('1+1')",
            "open('/etc/passwd')",
            "globals()",
            "locals()",
            "__builtins__",
        ]
        for payload in dangerous_inputs:
            with pytest.raises((ValueError, SyntaxError)):
                safe_eval_arithmetic(payload)

    def test_unsupported_function_blocked(self) -> None:
        with pytest.raises(ValueError, match="unsupported arithmetic"):
            safe_eval_arithmetic("sin(0)")

    def test_ast_node_limit_blocked(self) -> None:
        # Expression exceeding _MAX_NODES (64)
        large_expr = " + ".join(["1"] * 70)
        with pytest.raises(ValueError, match="arithmetic expression too large"):
            safe_eval_arithmetic(large_expr)
