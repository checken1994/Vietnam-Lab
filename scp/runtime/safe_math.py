"""Small, non-evaluating arithmetic parser for deterministic checks."""
from __future__ import annotations

import ast
import math
import operator
import re

_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.BitXor: operator.xor,
}
_UNARYOPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_MAX_NODES = 64
_MAX_ABS_RESULT = 1e300
_MAX_FACTORIAL_N = 100

_SQRT_NAMES = {"sqrt", "can", "căn", "isqrt"}
_FACT_NAMES = {"factorial", "fact"}

_CLEAN_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"hãy\s+(?:cho\s+biết|tính)|"
    r"cho\s+(?:tôi\s+)?biết|"
    r"tính\s+(?:giúp|giùm)(?:\s+tôi)?|"
    r"tính|"
    r"please\s+(?:calculate|compute)|"
    r"calculate|compute|what\s+is"
    r")\s+",
    re.IGNORECASE,
)
_CLEAN_SUFFIX_RE = re.compile(
    r"\s*(?:=\s*\??|\?|(?:bằng|là)\s+(?:bao\s+nhiêu|mấy|gì)\??)\s*$",
    re.IGNORECASE,
)

_UNSUPPORTED_ROOT_RE = re.compile(
    r"(?i)\b(?:căn|can)\s+(?:bậc|bac)\s*(?:[3-9]|\d{2,}|ba|bốn|bon|năm|nam|sáu|sau|bảy|bay|tám|tam|chín|chin)\b"
)
_SQRT_PREFIX_RE = re.compile(
    r"(?i)\b(?:căn\s+bậc\s+(?:hai|2)|can\s+bac\s+(?:hai|2)|(?:căn|can)\s+(?:của|cua)|căn|can)\s*(?:của|cua)?\s*"
)
_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")
_CALL_RE = re.compile(r"^[a-zA-Z_]\w*\s*\(")


def _extract_sqrt_arg(s: str, start: int) -> tuple[str | None, int]:
    rest = s[start:].lstrip()
    offset = len(s[start:]) - len(rest)
    pos = start + offset
    if not rest:
        return None, 0
    if rest.startswith("("):
        depth = 0
        for idx in range(pos, len(s)):
            if s[idx] == "(":
                depth += 1
            elif s[idx] == ")":
                depth -= 1
                if depth == 0:
                    return s[pos : idx + 1], idx + 1
        return None, 0
    call_m = _CALL_RE.match(rest)
    if call_m:
        paren_pos = pos + call_m.end() - 1
        depth = 0
        for idx in range(paren_pos, len(s)):
            if s[idx] == "(":
                depth += 1
            elif s[idx] == ")":
                depth -= 1
                if depth == 0:
                    return s[pos : idx + 1], idx + 1
        return None, 0
    num_m = _NUM_RE.match(rest)
    if num_m:
        return num_m.group(0), pos + num_m.end()
    return None, 0


def _normalize_vietnamese_sqrt(expr: str) -> str:
    """Normalize phrases like 'căn bậc hai của 144' or 'can(144)' into 'sqrt(...)'.
    Processes right-to-left so nested root expressions are handled cleanly.
    """
    if _UNSUPPORTED_ROOT_RE.search(expr):
        raise ValueError("unsupported root degree (only square root is supported)")
    curr = expr
    while True:
        matches = list(_SQRT_PREFIX_RE.finditer(curr))
        if not matches:
            break
        changed = False
        for m in reversed(matches):
            arg, end_pos = _extract_sqrt_arg(curr, m.end())
            if arg is not None:
                if arg.startswith("(") and arg.endswith(")"):
                    replacement = f"sqrt{arg}"
                else:
                    replacement = f"sqrt({arg})"
                curr = curr[: m.start()] + replacement + curr[end_pos:]
                changed = True
                break
        if not changed:
            break
    return curr


def _rewrite_factorial(expr: str) -> str:
    """Rewrite postfix factorial expressions 'N!' or '(expr)!' to 'factorial(...)'.
    Safely differentiates from '!=' comparison operator.
    """
    s = expr
    i = 0
    while i < len(s):
        if s[i] == "!" and (i + 1 == len(s) or s[i + 1] != "="):
            end_op = i
            j = i - 1
            while j >= 0 and s[j].isspace():
                j -= 1
            if j < 0:
                raise ValueError("unexpected '!' without preceding operand")
            if s[j] in "+-*/^(":
                raise ValueError("unexpected '!' after operator")
            if s[j] == ")":
                depth = 1
                k = j - 1
                while k >= 0 and depth > 0:
                    if s[k] == ")":
                        depth += 1
                    elif s[k] == "(":
                        depth -= 1
                    k -= 1
                if depth != 0:
                    raise ValueError("unmatched parentheses before '!'")
                func_end = k
                while func_end >= 0 and s[func_end].isspace():
                    func_end -= 1
                func_start = func_end
                while func_start >= 0 and (s[func_start].isalnum() or s[func_start] == "_"):
                    func_start -= 1
                ident = s[func_start + 1 : func_end + 1]
                if ident.lower() in (_SQRT_NAMES | _FACT_NAMES):
                    start_op = func_start + 1
                else:
                    start_op = k + 1
            else:
                k = j
                while k >= 0 and (s[k].isalnum() or s[k] == "." or s[k] == "_"):
                    k -= 1
                start_op = k + 1
            operand = s[start_op:end_op].strip()
            s = s[:start_op] + f"factorial({operand})" + s[i + 1:]
            i = start_op + len(f"factorial({operand})")
        else:
            i += 1
    return s


def _preprocess_arithmetic_expression(expression: str) -> str:
    """Clean conversational wrappers and normalize sqrt/factorial expressions safely."""
    expr = str(expression).strip()
    expr = _CLEAN_PREFIX_RE.sub("", expr)
    expr = _CLEAN_SUFFIX_RE.sub("", expr)
    expr = _rewrite_factorial(expr)
    expr = _normalize_vietnamese_sqrt(expr)
    return expr


def safe_eval_arithmetic(expression: str) -> float:
    """Evaluate finite arithmetic without Python eval; ``^`` keeps legacy XOR semantics.
    Supports basic ops (+, -, *, /), XOR (^), square root (sqrt, can, căn bậc hai của N),
    and factorial (N!, factorial()).
    """
    preprocessed = _preprocess_arithmetic_expression(expression)
    tree = ast.parse(preprocessed, mode="eval")
    if sum(1 for _ in ast.walk(tree)) > _MAX_NODES:
        raise ValueError("arithmetic expression too large")

    def visit(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            value = float(node.value)
            if not math.isfinite(value):
                raise ValueError("non-finite number")
            return value
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARYOPS:
            return float(_UNARYOPS[type(node.op)](visit(node.operand)))
        if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
            left = visit(node.left)
            right = visit(node.right)
            if isinstance(node.op, ast.BitXor):
                if not left.is_integer() or not right.is_integer():
                    raise ValueError("bitwise XOR requires integer operands")
                value = float(_BINOPS[type(node.op)](int(left), int(right)))
            else:
                try:
                    value = float(_BINOPS[type(node.op)](left, right))
                except OverflowError as exc:
                    raise ValueError("arithmetic result outside safety bound") from exc
            if not math.isfinite(value) or abs(value) > _MAX_ABS_RESULT:
                raise ValueError("arithmetic result outside safety bound")
            return value
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ValueError(f"unsupported arithmetic node: {type(node.func).__name__}")
            func_name = node.func.id.lower()
            if len(node.args) != 1 or node.keywords:
                raise ValueError(f"function {node.func.id} requires exactly one argument")
            arg_val = visit(node.args[0])
            if func_name in _SQRT_NAMES:
                if arg_val < 0:
                    raise ValueError("square root of negative number")
                if arg_val.is_integer():
                    i_arg = int(arg_val)
                    root = math.isqrt(i_arg)
                    if root * root == i_arg:
                        value = float(root)
                    else:
                        value = float(math.sqrt(arg_val))
                else:
                    value = float(math.sqrt(arg_val))
            elif func_name in _FACT_NAMES:
                if not arg_val.is_integer():
                    raise ValueError("factorial requires integer operand")
                n = int(arg_val)
                if n < 0:
                    raise ValueError("factorial of negative number")
                if n > _MAX_FACTORIAL_N:
                    raise ValueError(f"factorial operand too large (max {_MAX_FACTORIAL_N})")
                try:
                    value = float(math.factorial(n))
                except OverflowError as exc:
                    raise ValueError("arithmetic result outside safety bound") from exc
            else:
                raise ValueError(f"unsupported arithmetic function: {node.func.id}")

            if not math.isfinite(value) or abs(value) > _MAX_ABS_RESULT:
                raise ValueError("arithmetic result outside safety bound")
            return value

        raise ValueError(f"unsupported arithmetic node: {type(node).__name__}")

    try:
        return visit(tree)
    except OverflowError as exc:
        raise ValueError("arithmetic result outside safety bound") from exc
