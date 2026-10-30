"""[MATH-VERIFY · 2026-10-31] Kiểm chứng phép tính số học — cơ chế cấu trúc, độc lập ngôn ngữ.

TẠI SAO: SCP giao "12 × 7 = **85**" như đã kiểm chứng vì bộ kiểm atomic chỉ đọc
toán tử ASCII ``+-*/^`` và ``= <số>`` liền nhau (× Unicode và ``**85**`` markdown
lọt), nên quyền quyết rơi về judge LLM. Owner (31-10): "AI và SCP đều sai thì ai
kiểm?" ⇒ SCP KHÔNG ghi đè đáp án bằng giá trị của mình; chỉ VERIFIED khi khớp
HAI tầng: (a) BIỂU THỨC — cách hiểu câu hỏi độc lập với AI trùng biểu thức AI
dùng; (b) GIÁ TRỊ — tính lại trùng. Lệch ⇒ withheld, nêu cả hai giá trị.

Cơ chế chỉ dựa trên KÝ HIỆU (chữ số mọi hệ Unicode, toán tử/ký hiệu toán học,
LaTeX, ``=``/``≈``) — không danh sách từ khóa ngôn ngữ nào ở module này.

Máy tính: hai bộ tính độc lập phải đồng ý — ``safe_math`` (float, AST whitelist)
và bộ tính chính xác ``fractions.Fraction`` viết riêng ở đây. Bất đồng ⇒ không
tính lại được (UNRECOMPUTABLE), không bao giờ đoán.

Giới hạn (ghi trung thực): căn không chính phương so ở 40 chữ số thập phân;
lũy thừa chỉ nhận số mũ nguyên |e| <= 1024; số rất lớn bị chặn bởi trần
safe_math (1e300); ``^`` trong CÂU HỎI là mơ hồ (XOR legacy vs lũy thừa) nên
không vào đường tất định; ``^`` trong CÂU TRẢ LỜI đọc là lũy thừa (quy ước văn bản).
"""
from __future__ import annotations

import ast
import math
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, localcontext
from fractions import Fraction
from typing import Any

from scp.runtime.safe_math import _CLEAN_PREFIX_RE, _CLEAN_SUFFIX_RE, safe_eval_arithmetic

# --- Trạng thái đánh giá -----------------------------------------------------
NO_ARITHMETIC = "NO_ARITHMETIC"  # câu trả lời không có phương trình ký hiệu nào
VERIFIED = "VERIFIED"  # khớp 2 tầng (biểu thức + giá trị)
SAME_LINEAGE = "SAME_LINEAGE"  # giá trị khớp nhưng không có cách hiểu độc lập
EXPRESSION_UNCONFIRMED = "EXPRESSION_UNCONFIRMED"  # có cách hiểu độc lập, AI không dùng biểu thức đó
MISMATCH_VALUE = "MISMATCH_VALUE"  # AI ghi kết quả khác giá trị SCP tính lại
MISMATCH_EXPRESSION = "MISMATCH_EXPRESSION"  # hai cách hiểu câu hỏi khác nhau
UNRECOMPUTABLE = "UNRECOMPUTABLE"  # có cấu trúc phép tính nhưng hai bộ tính không cho kết quả chung
MISSING_RESULT = "MISSING_RESULT"  # câu hỏi đọc được thành phép tính, đáp án không chứa kết quả

#: Trạng thái BẮT BUỘC withheld (không judge nào được ghi đè).
WITHHOLD_STATUSES = frozenset({MISMATCH_VALUE, MISMATCH_EXPRESSION, UNRECOMPUTABLE, MISSING_RESULT})

# --- Chuẩn hoá ký hiệu (không phụ thuộc ngôn ngữ) -----------------------------
_SYMBOLS = {
    "×": "*", "✕": "*", "✖": "*", "⨯": "*", "⋅": "*", "·": "*", "∙": "*", "∗": "*",
    "÷": "/", "∕": "/", "⁄": "/", "➗": "/",
    "−": "-", "‒": "-", "﹣": "-", "－": "-",
    "٫": ".", "٬": "", "٪": "%", "＝": "=",
}
_SUPERSCRIPT_DIGITS = {c: str(i) for i, c in enumerate("⁰¹²³⁴⁵⁶⁷⁸⁹")}
_LATEX_SUBS = (
    (re.compile(r"\\sqrt\s*\{([^{}]*)\}"), r"√(\1)"),
    (re.compile(r"\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}"), r"((\1)/(\2))"),
    (re.compile(r"\\(?:times|cdot)(?![A-Za-z])"), "×"),
    (re.compile(r"\\div(?![A-Za-z])"), "÷"),
    (re.compile(r"\\(?:left|right)(?=[()])"), ""),
    (re.compile(r"\\[()\[\]]|\$"), " "),
)
_MARKDOWN_RE = re.compile(r"\*\*|__|`")
_SQRT_PAREN_RE = re.compile(r"√\s*\(")
_SQRT_NUM_RE = re.compile(r"√\s*(\d+(?:[.,]\d+)*)")
_LATIN_TIMES_RE = re.compile(r"(?<=\d)\s+[xX]\s+(?=\d)")


def normalize_math_text(text: str) -> str:
    """Chuẩn hoá ký hiệu toán: chữ số mọi hệ → ASCII, × ÷ − → * / -, √ → sqrt,
    chỉ số trên → ``**n``, LaTeX/markdown được gỡ. Không đụng chữ cái."""
    s = str(text or "")
    for pat, rep in _LATEX_SUBS:
        s = pat.sub(rep, s)
    s = _MARKDOWN_RE.sub("", s)
    out: list[str] = []
    in_sup = False
    for ch in s:
        if ch in _SUPERSCRIPT_DIGITS:
            out.append(("" if in_sup else "**") + _SUPERSCRIPT_DIGITS[ch])
            in_sup = True
            continue
        in_sup = False
        if ch in _SYMBOLS:
            out.append(_SYMBOLS[ch])
            continue
        dig = unicodedata.decimal(ch, None)  # chữ số mọi hệ Unicode (Ả Rập-Ấn, fullwidth, Devanagari…)
        out.append(str(dig) if dig is not None else unicodedata.normalize("NFKC", ch))
    s = "".join(out)
    s = _SQRT_PAREN_RE.sub("sqrt(", s)
    s = _SQRT_NUM_RE.sub(r"sqrt(\1)", s)
    return _LATIN_TIMES_RE.sub(" * ", s)


# --- Số & cách đọc dấu phân cách (đa locale) ----------------------------------
_NUM_TOKEN_RE = re.compile(r"\d+(?:[.,]\d+)*")
#: I1 = "." thập phân / "," nghìn; I2 = "," thập phân / "." nghìn.
_INTERPS = ("dot_decimal", "comma_decimal")


def _token_to_decimal_str(tok: str, interp: str) -> str | None:
    dec, grp = (".", ",") if interp == "dot_decimal" else (",", ".")
    if tok.count(dec) > 1:
        return None
    int_part, _, frac = tok.partition(dec)
    if grp in frac:
        return None
    if grp in int_part:
        groups = int_part.split(grp)
        if not (1 <= len(groups[0]) <= 3 and all(len(g) == 3 for g in groups[1:])):
            return None
        int_part = "".join(groups)
    return int_part + ("." + frac if frac else "")


def _decimals_of(tok: str, interp: str) -> int:
    conv = _token_to_decimal_str(tok, interp) or ""
    return len(conv.partition(".")[2])


# --- Biểu thức → AST an toàn ---------------------------------------------------
_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)


def _to_python(expr: str, interp: str, *, caret_is_power: bool) -> str | None:
    if "^" in expr:
        if not caret_is_power:
            return None
        expr = expr.replace("^", "**")
    bad = False

    def _num(m: re.Match[str]) -> str:
        nonlocal bad
        conv = _token_to_decimal_str(m.group(0), interp)
        if conv is None:
            bad = True
            return "0"
        return conv

    py = _NUM_TOKEN_RE.sub(_num, expr)
    if bad:
        return None
    return re.sub(r"(\d+(?:\.\d+)?)\s*%", r"(\1/100)", py)


def _parse(py: str) -> ast.Expression | None:
    try:
        tree = ast.parse(py.strip(), mode="eval")
    except (SyntaxError, ValueError):
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.Expression, ast.Load)) or isinstance(node, _ALLOWED_BINOPS):
            continue
        if isinstance(node, (ast.UAdd, ast.USub)):
            continue
        if isinstance(node, ast.BinOp) and isinstance(node.op, _ALLOWED_BINOPS):
            continue
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            continue
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            continue
        if (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "sqrt"
            and len(node.args) == 1 and not node.keywords
        ):
            continue
        if isinstance(node, ast.Name) and node.id == "sqrt":
            continue
        return None
    return tree


def _has_operation(tree: ast.Expression) -> bool:
    return any(isinstance(n, (ast.BinOp, ast.Call)) for n in ast.walk(tree))


# --- Bộ tính chính xác độc lập (Fraction) -------------------------------------
_MAX_EXACT_DIGITS = 4000


class _Unrecomputable(ValueError):
    pass


def _exact(node: ast.AST) -> Fraction:
    if isinstance(node, ast.Expression):
        return _exact(node.body)
    if isinstance(node, ast.Constant):
        return Fraction(str(node.value))
    if isinstance(node, ast.UnaryOp):
        v = _exact(node.operand)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp):
        a, b = _exact(node.left), _exact(node.right)
        if isinstance(node.op, ast.Add):
            r = a + b
        elif isinstance(node.op, ast.Sub):
            r = a - b
        elif isinstance(node.op, ast.Mult):
            r = a * b
        elif isinstance(node.op, ast.Div):
            if b == 0:
                raise _Unrecomputable("division by zero")
            r = a / b
        else:  # Pow
            if b.denominator != 1 or abs(b) > 1024 or (a == 0 and b < 0):
                raise _Unrecomputable("non-integer or oversized exponent")
            r = a ** int(b)
        if max(len(str(r.numerator)), len(str(r.denominator))) > _MAX_EXACT_DIGITS:
            raise _Unrecomputable("exact value too large")
        return r
    if isinstance(node, ast.Call):
        v = _exact(node.args[0])
        if v < 0:
            raise _Unrecomputable("square root of negative number")
        rn, rd = math.isqrt(v.numerator), math.isqrt(v.denominator)
        if rn * rn == v.numerator and rd * rd == v.denominator:
            return Fraction(rn, rd)
        with localcontext() as dctx:
            dctx.prec = 40
            return Fraction((Decimal(v.numerator) / Decimal(v.denominator)).sqrt())
    raise _Unrecomputable(f"unsupported node {type(node).__name__}")


def evaluate_dual(py: str) -> Fraction:
    """Tính bằng 2 bộ tính độc lập; bất đồng hoặc lỗi ⇒ _Unrecomputable."""
    tree = _parse(py)
    if tree is None:
        raise _Unrecomputable("unparseable expression")
    exact = _exact(tree)
    try:
        approx = safe_eval_arithmetic(py, allow_power=True)
    except (ValueError, SyntaxError, ZeroDivisionError, OverflowError) as exc:
        raise _Unrecomputable(f"float evaluator: {exc}") from exc
    if not math.isclose(float(exact), approx, rel_tol=1e-9, abs_tol=1e-12):
        raise _Unrecomputable("evaluators disagree")
    return exact


# --- Dạng chuẩn của biểu thức (so sánh tầng BIỂU THỨC) --------------------------
def _canon(node: ast.AST) -> str:
    if isinstance(node, ast.Expression):
        return _canon(node.body)
    if isinstance(node, ast.Constant):
        f = Fraction(str(node.value))
        return str(f.numerator) if f.denominator == 1 else f"{f.numerator}/{f.denominator}"
    if isinstance(node, ast.UnaryOp):
        inner = _canon(node.operand)
        return f"neg({inner})" if isinstance(node.op, ast.USub) else inner
    if isinstance(node, ast.Call):
        return f"sqrt({_canon(node.args[0])})"
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, (ast.Add, ast.Mult)):
            sym = "+" if isinstance(node.op, ast.Add) else "*"
            parts: list[str] = []

            def _flat(n: ast.AST) -> None:
                if isinstance(n, ast.BinOp) and type(n.op) is type(node.op):
                    _flat(n.left)
                    _flat(n.right)
                else:
                    parts.append(_canon(n))

            _flat(node)
            return sym + "(" + ",".join(sorted(parts)) + ")"
        sym = {ast.Sub: "-", ast.Div: "/", ast.Pow: "**"}[type(node.op)]
        return f"{sym}({_canon(node.left)},{_canon(node.right)})"
    return "?"


def _constants(tree: ast.AST) -> list[str]:
    return sorted(_canon(n) for n in ast.walk(tree) if isinstance(n, ast.Constant))


@dataclass
class ParsedExpression:
    source: str  # văn bản gốc (đã làm sạch) để hiển thị cho người
    python: str
    canon: str
    constants: list[str]
    value: Fraction
    interp: str

    def value_str(self) -> str:
        return format_value(self.value)


def format_value(v: Fraction) -> str:
    if v.denominator == 1:
        return str(v.numerator)
    return f"{float(v):.10g}"


def _parse_pure_expression(text: str, *, caret_is_power: bool) -> ParsedExpression | None:
    """Biểu thức thuần ký hiệu (không '=', không chữ cái ngoài ``sqrt``); mọi cách
    đọc dấu phân cách hợp lệ phải cho CÙNG giá trị, nếu không ⇒ mơ hồ ⇒ None."""
    norm = normalize_math_text(text).strip()
    if not norm or "=" in norm or "≈" in norm:
        return None
    if re.search(r"[^\W\d_]", norm.replace("sqrt", "")):
        return None
    found: list[ParsedExpression] = []
    for interp in _INTERPS:
        py = _to_python(norm, interp, caret_is_power=caret_is_power)
        tree = _parse(py) if py else None
        if tree is None or not _has_operation(tree):
            continue
        try:
            val = evaluate_dual(py)
        except _Unrecomputable:
            return None
        found.append(ParsedExpression(norm, py, _canon(tree), _constants(tree), val, interp))
    if not found or any(f.value != found[0].value for f in found):
        return None
    return found[0]


def parse_question_expression(question: str) -> ParsedExpression | None:
    """Cách hiểu câu hỏi CỦA SCP: chỉ khi câu hỏi (sau khi gỡ vỏ lịch sự sẵn có của
    safe_math) là một biểu thức ký hiệu thuần. ``^`` mơ hồ ⇒ None."""
    q = _CLEAN_SUFFIX_RE.sub("", _CLEAN_PREFIX_RE.sub("", str(question or "").strip()))
    return _parse_pure_expression(q, caret_is_power=False)


def parse_independent_expression(text: str) -> ParsedExpression | None:
    """Biểu thức do nguồn độc lập trả (chỉ biểu thức, chưa tính). Một con số trần
    hay có '=' ⇒ nguồn đã tự tính/không tuân thủ ⇒ None."""
    line = str(text or "").strip().splitlines()[0] if str(text or "").strip() else ""
    return _parse_pure_expression(line.strip().strip("`").strip(), caret_is_power=False)


# --- Trích phương trình từ câu trả lời ----------------------------------------
_REL_RE = re.compile(r"(?<![<>=!])(=|≈)(?![=>])")
_LHS_RUN_RE = re.compile(r"(?:sqrt\(|[0-9.,+\-*/()%^\s])+$")
_RHS_RE = re.compile(r"^[\s*_]*(-?\s?\d+(?:[.,]\d+)*)\s*(%?)")


@dataclass
class Equation:
    lhs: str
    relation: str
    claimed: str
    status: str  # MATCH | MISMATCH | UNRECOMPUTABLE
    scp_value: str = ""
    canon: str = ""
    constants: list[str] = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "expression": self.lhs, "relation": self.relation, "claimed": self.claimed,
            "status": self.status, "scp_value": self.scp_value, "detail": self.detail,
        }


def _lhs_candidates(run: str) -> list[str]:
    run = run.rstrip()
    starts = [0] + [i + 1 for i, ch in enumerate(run) if ch.isspace() or ch in "(,"]
    out = []
    for st in starts:
        cand = run[st:].strip().lstrip("*/+,.)").strip()
        if cand and cand not in out and re.search(r"\d", cand):
            out.append(cand)
    return out


def _claim_matches(exact: Fraction, claimed_tok: str, pct: bool, interp: str, relation: str) -> bool | None:
    conv = _token_to_decimal_str(claimed_tok.replace(" ", "").lstrip("-"), interp)
    if conv is None:
        return None
    claimed = Fraction(conv) * (-1 if claimed_tok.strip().startswith("-") else 1)
    if pct:
        claimed /= 100
    d = _decimals_of(claimed_tok.replace(" ", "").lstrip("-"), interp) + (2 if pct else 0)
    tol = Fraction(1, 2 * 10**d) if d > 0 else Fraction(0)
    if relation == "≈":
        tol = max(tol, abs(exact) / 100)
    return abs(exact - claimed) <= tol


def extract_equations(answer: str) -> list[Equation]:
    """Mọi ``<biểu thức có phép toán> (=|≈) <số>`` trong câu trả lời (theo ký hiệu)."""
    text = normalize_math_text(answer)
    eqs: list[Equation] = []
    for m in _REL_RE.finditer(text):
        run_m = _LHS_RUN_RE.search(text[: m.start()])
        rhs_m = _RHS_RE.match(text[m.end():])
        if not run_m or not rhs_m:
            continue
        claimed_tok, pct = rhs_m.group(1), bool(rhs_m.group(2))
        best: Equation | None = None
        for cand in _lhs_candidates(run_m.group(0)):
            outcomes: list[Equation] = []
            for interp in _INTERPS:
                py = _to_python(cand, interp, caret_is_power=True)
                tree = _parse(py) if py else None
                if tree is None or not _has_operation(tree) or not any(
                    isinstance(n, ast.BinOp) or isinstance(n, ast.Call) for n in ast.walk(tree)
                ):
                    continue
                try:
                    exact = evaluate_dual(py)
                except _Unrecomputable as exc:
                    outcomes.append(Equation(cand, m.group(1), claimed_tok, UNRECOMPUTABLE, detail=str(exc)))
                    continue
                ok = _claim_matches(exact, claimed_tok, pct, interp, m.group(1))
                if ok is None:
                    continue
                outcomes.append(Equation(
                    cand, m.group(1), claimed_tok + ("%" if pct else ""), "MATCH" if ok else "MISMATCH",
                    scp_value=format_value(exact), canon=_canon(tree), constants=_constants(tree),
                    detail=f"interp={interp}",
                ))
            if not outcomes:
                continue
            match = next((o for o in outcomes if o.status == "MATCH"), None)
            best = match or next((o for o in outcomes if o.status == "MISMATCH"), None) or outcomes[0]
            break  # ứng viên dài nhất parse được là biểu thức của phương trình
        if best is not None:
            eqs.append(best)
    return eqs


def _answer_numbers(answer: str) -> set[Fraction]:
    vals: set[Fraction] = set()
    for tok in _NUM_TOKEN_RE.findall(normalize_math_text(answer)):
        for interp in _INTERPS:
            conv = _token_to_decimal_str(tok, interp)
            if conv is not None:
                vals.add(Fraction(conv))
    return vals


def _only_states_value(answer: str, value: Any) -> bool:
    """Mọi token số trong đáp án đều đọc được thành đúng ``value`` (≥1 token)."""
    toks = _NUM_TOKEN_RE.findall(normalize_math_text(answer))
    if not toks:
        return False
    for tok in toks:
        convs = [_token_to_decimal_str(tok, i) for i in _INTERPS]
        if not any(c is not None and Fraction(c) == value for c in convs):
            return False
    return True


# --- Đánh giá 2 tầng ----------------------------------------------------------
@dataclass
class MathAssessment:
    status: str
    equations: list[Equation] = field(default_factory=list)
    reference: ParsedExpression | None = None
    reference_lineage: str = ""  # "question_notation" | "independent:<lineage>" | ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        ref = self.reference
        return {
            "status": self.status,
            "equations": [e.to_dict() for e in self.equations],
            "reference_expression": ref.source if ref else None,
            "reference_value": ref.value_str() if ref else None,
            "reference_lineage": self.reference_lineage,
            "independent": self.status == VERIFIED and self.reference_lineage.startswith("independent:"),
            "reason": self.reason,
        }


def assess(
    question: str,
    answer: str,
    *,
    reference: ParsedExpression | None = None,
    reference_lineage: str = "",
) -> MathAssessment:
    """Tầng GIÁ TRỊ (mọi phương trình tính lại) rồi tầng BIỂU THỨC (so cách hiểu
    độc lập: câu hỏi dạng ký hiệu của SCP, hoặc ``reference`` từ nguồn khác lineage)."""
    eqs = extract_equations(answer)
    if not eqs:
        own = parse_question_expression(question)
        if own is not None:
            reference, reference_lineage = own, "question_notation"
        if reference is None:
            return MathAssessment(NO_ARITHMETIC, reason="no symbolic equation in answer")
        # Câu hỏi là phép tính (theo cách hiểu độc lập) nhưng đáp án không nêu phép tính.
        if reference.value not in _answer_numbers(answer):
            return MathAssessment(MISSING_RESULT, [], reference, reference_lineage, reason=(
                f"question reads as {reference.source} = {reference.value_str()}; the answer does not contain this result"
            ))
        # Câu hỏi TỰ NÓ là biểu thức ký hiệu (SCP đọc nguyên văn, mọi cách đọc số trùng
        # giá trị) và đáp án CHỈ nêu đúng giá trị đó: tầng biểu thức không có cách hiểu
        # cạnh tranh — biểu thức hiển thị cho người kiểm chính là câu hỏi. Đáp án có số
        # khác (vd "4, không phải 5") vẫn rơi xuống EXPRESSION_UNCONFIRMED ⇒ withheld.
        if reference_lineage == "question_notation" and _only_states_value(answer, reference.value):
            return MathAssessment(VERIFIED, [], reference, reference_lineage, reason=(
                f"question is the expression {reference.source}; answer states only its value {reference.value_str()}"
            ))
        return MathAssessment(EXPRESSION_UNCONFIRMED, [], reference, reference_lineage, reason=(
            f"answer contains {reference.value_str()} but never states the expression {reference.source}"
        ))
    bad = [e for e in eqs if e.status == "MISMATCH"]
    if bad:
        e = bad[0]
        return MathAssessment(MISMATCH_VALUE, eqs, reason=(
            f"answer states {e.lhs} {e.relation} {e.claimed}; SCP recomputes {e.lhs} = {e.scp_value}"
        ))
    unr = [e for e in eqs if e.status == UNRECOMPUTABLE]
    if unr:
        return MathAssessment(UNRECOMPUTABLE, eqs, reason=f"cannot recompute {unr[0].lhs}: {unr[0].detail}")
    own = parse_question_expression(question)
    if own is not None:
        reference, reference_lineage = own, "question_notation"
    if reference is None:
        return MathAssessment(SAME_LINEAGE, eqs, reason=(
            "values recomputed from the answer's own expressions only (same lineage); "
            "no independent reading of the question"
        ))
    if any(e.canon == reference.canon for e in eqs):
        return MathAssessment(VERIFIED, eqs, reference, reference_lineage, reason=(
            f"expression and value agree: {reference.source} = {reference.value_str()}"
        ))
    diff = [e for e in eqs if e.constants == reference.constants and e.canon != reference.canon]
    if diff or reference.value not in _answer_numbers(answer):
        return MathAssessment(MISMATCH_EXPRESSION, eqs, reference, reference_lineage, reason=(
            f"independent reading {reference.source} = {reference.value_str()} differs from the answer's "
            f"expression {(diff or eqs)[0].lhs} = {(diff or eqs)[0].claimed}"
        ))
    return MathAssessment(EXPRESSION_UNCONFIRMED, eqs, reference, reference_lineage, reason=(
        "answer reaches the independent value but never states that expression"
    ))


def has_digit(text: str) -> bool:
    """Câu có chữ số (mọi hệ chữ số Unicode) — tín hiệu cấu trúc, không theo ngôn ngữ."""
    return bool(re.search(r"\d", str(text or "")))


def numbers_missing_from(question: str, answer: str) -> list[str]:
    """Các số trong câu hỏi KHÔNG xuất hiện trong đáp án (so theo giá trị, mọi hệ chữ số).
    Một đoạn nguồn không nhắc tới các số của câu hỏi thì không trả lời câu hỏi về các số đó."""
    have = _answer_numbers(answer)
    missing: list[str] = []
    for tok in _NUM_TOKEN_RE.findall(normalize_math_text(question)):
        vals = {Fraction(c) for c in (_token_to_decimal_str(tok, i) for i in _INTERPS) if c is not None}
        if vals and not (vals & have):
            missing.append(tok)
    return missing
