"""[DEHARDCODE 2026-10-30] Static guard — luật owner cấp cao nhất:
"đừng có mã hoá cứng các mẫu câu hỏi vào SCP ... SCP chỉ lấy dữ liệu trong data,
internet đã được kiểm chứng để trả lời."

Định nghĩa CHÍNH XÁC những gì guard cấm (đỏ trên main aceb137b, xanh sau fix):

G1  Mẫu câu hỏi hội thoại trong code định tuyến/giao (``ROUTING_DELIVERY_FILES``):
    mọi hằng chuỗi (AST ``Constant`` str, kể cả trong regex) — sau khi "nới" cú
    pháp regex (``\\s+``→space, bỏ ``(?:`` ``)`` ``?`` ``\\b`` ``^`` ``$``, tách
    theo ``|``) — KHÔNG được chứa cụm hội thoại trong ``CONVERSATIONAL_LEXICON``
    (chào hỏi / danh tính / smalltalk / cảm ơn / trí nhớ / giải trí, đa ngôn ngữ).
    Docstring và comment không phải code quyết định ⇒ bỏ qua.
G2  Câu trả lời đóng hộp tự giới thiệu (``CANNED_SELF_INTRO``) không được là
    hằng chuỗi trong các file đó — lời tự mô tả phải đến từ
    ``data/identity/scp_self_description.json``.
G3  Đáp án viết cứng trong 10 domain expert (LN-11): không được có ``dict``
    literal mà ≥3 value là chuỗi dài ≥40 ký tự (dạng {khoá-câu-hỏi: đáp-án}),
    và không còn nhánh ``"source": "local_knowledge"``.

NGOẠI LỆ TƯỜNG MINH (không phải định tuyến câu thường, owner cho phép có lý do):
- (a) block-list an ninh phòng thủ nhiều lớp: ``_SECURITY_RULES``,
  ``_SECRET_INDICATORS_PATTERN``, ``_EXTRACTION_*``, ``_CONCEPTUAL_*``,
  ``_BENIGN_CONCEPTUAL_PHRASES`` (router_rules.py), ``_W3_VENDOR_*`` (post-guard
  câu TRẢ LỜI tự nhận vendor — không phải mẫu câu hỏi).
- (b) cú pháp thuần / nhận diện ngôn ngữ: ``_VIETNAMESE_WORDS``,
  ``_VIETNAMESE_DIACRITICS``, ``_NON_LATIN_SCRIPT`` (từng từ đơn — không chứa cụm
  hội thoại), các rule số học. Không file nào được thêm vào danh sách ngoại lệ
  mà không ghi lý do ở đây.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

ROUTING_DELIVERY_FILES = (
    "scp/runtime/router_rules.py",
    "scp/runtime/question_router.py",
    "scp/runtime/semantic_router/consensus.py",
    "scp/runtime/semantic_router/arbitration.py",
    "scp/runtime/semantic_router/structural.py",
    "scp/runtime/semantic_router/lineage_embed.py",
    "scp/runtime/semantic_router/lineage_slm.py",
    "scp/api_server_parts/pipeline/delivery_rules.py",
    "scp/api_server_parts/pipeline/generation_stage.py",
    "scp/api_server_parts/pipeline/verification_stage.py",
    "scp/api_server_parts/_ask_impl.py",
    "scp/api/system_prompt.py",
)

EXPERT_FILES = tuple(
    f"scp/runtime/experts/{n}.py"
    for n in (
        "agriculture", "art", "economics", "education", "engineering",
        "law", "literature", "management", "military", "philosophy",
    )
)

#: Ngoại lệ (a)/(b) — tên biến module-level được bỏ qua, xem docstring.
EXEMPT_ASSIGNMENTS = frozenset({
    "_SECURITY_RULES",
    "_SECRET_INDICATORS_PATTERN",
    "_EXTRACTION_TERMS_PATTERN",
    "_EXTRACTION_TERMS_UNACCENTED",
    "_EXTRACTION_IN_PATTERN",
    "_CONCEPTUAL_PATTERN_STR",
    "_BENIGN_CONCEPTUAL_PHRASES",
    "_W3_VENDOR_NAMES",
    "_W3_VENDOR_CLAIM_RE",
    "_VIETNAMESE_WORDS",
    "_VIETNAMESE_DIACRITICS",
    "_NON_LATIN_SCRIPT",
})

#: Cụm hội thoại nhiều từ (khớp theo ranh giới từ trên chuỗi đã nới cú pháp).
CONVERSATIONAL_LEXICON = (
    # vi
    "xin chào", "chào bạn", "bạn là ai", "bạn tên", "tên bạn", "tên của bạn",
    "tên là gì", "tên gì", "bạn khỏe", "khỏe không", "cảm ơn", "tạm biệt",
    "bạn có nhớ", "nhớ tôi", "tên tôi", "tôi tên", "tôi là ai", "kể chuyện",
    "kể một câu chuyện", "bạn có thể làm gì", "giúp tôi", "bạn nghĩ sao",
    "tôi vừa nói", "rất vui được gặp", "mày là ai", "scp là gì",
    # en
    "who are you", "what are you", "what is your name", "your name",
    "how are you", "how is it going", "thank you", "good morning",
    "good evening", "nice to meet you", "goodbye", "do you remember",
    "remember me", "tell me a story", "tell a joke", "what can you do",
    "who am i", "what is my name", "can you help", "help me",
    "what did i say", "what is scp", "who is scp",
)
#: Lời chào một từ — chỉ cấm khi là NGUYÊN một nhánh regex/chuỗi (không khớp chuỗi con).
SINGLE_WORD_GREETINGS = frozenset({"hello", "hi", "hey", "bye", "thanks", "chào"})

CANNED_SELF_INTRO = (
    "tôi là scp", "mình là scp", "i am scp", "i'm scp",
)


def _loosen(text: str) -> list[str]:
    """Nới cú pháp regex → các nhánh văn bản thường (lowercase)."""
    t = text.lower()
    t = re.sub(r"\\[a-zA-Z][+*?]?", " ", t)  # regex escapes: \s+ \b \w \d ...
    t = re.sub(r"\(\?[:!=<]*", " ", t)
    t = re.sub(r"[()?^$\[\]+*{}]", " ", t)
    parts = [re.sub(r"\s+", " ", p).strip(" .,!;:-") for p in t.split("|")]
    whole = re.sub(r"\s+", " ", t.replace("|", " ")).strip()
    return [p for p in parts if p] + [whole]


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _exempt_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        if any(isinstance(t, ast.Name) and t.id in EXEMPT_ASSIGNMENTS for t in targets):
            for sub in ast.walk(node):
                ids.add(id(sub))
    return ids


def _string_constants(relpath: str, root: Path = REPO) -> list[tuple[int, str]]:
    tree = ast.parse((root / relpath).read_text(encoding="utf-8"))
    skip = _docstring_ids(tree) | _exempt_ids(tree)
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            out.append((node.lineno, node.value))
    return out


def conversational_violations(relpath: str, root: Path = REPO) -> list[str]:
    hits: list[str] = []
    phrase_res = [(p, re.compile(r"(?<!\w)" + re.escape(p) + r"(?!\w)")) for p in CONVERSATIONAL_LEXICON]
    for lineno, value in _string_constants(relpath, root):
        branches = _loosen(value)
        for phrase, rx in phrase_res:
            if any(rx.search(b) for b in branches):
                hits.append(f"{relpath}:{lineno}: '{phrase}'")
        for b in branches:
            if b in SINGLE_WORD_GREETINGS:
                hits.append(f"{relpath}:{lineno}: greeting '{b}'")
    return hits


def canned_intro_violations(relpath: str, root: Path = REPO) -> list[str]:
    hits = []
    for lineno, value in _string_constants(relpath, root):
        low = value.lower()
        if any(c in low for c in CANNED_SELF_INTRO):
            hits.append(f"{relpath}:{lineno}")
    return hits


def expert_canned_violations(relpath: str, root: Path = REPO) -> list[str]:
    src = (root / relpath).read_text(encoding="utf-8")
    tree = ast.parse(src)
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            long_vals = [
                v for v in node.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str) and len(v.value) >= 40
            ]
            if len(long_vals) >= 3:
                hits.append(f"{relpath}:{node.lineno}: canned dict ({len(node.values)} entries)")
    if '"local_knowledge"' in src.replace('"local_knowledge" in answer', ""):
        hits.append(f"{relpath}: local_knowledge source label")
    return hits


@pytest.mark.parametrize("relpath", ROUTING_DELIVERY_FILES)
def test_g1_no_conversational_question_templates_in_routing_delivery(relpath: str) -> None:
    hits = conversational_violations(relpath)
    assert not hits, "Mẫu câu hỏi hội thoại viết cứng (luật owner):\n" + "\n".join(hits)


@pytest.mark.parametrize("relpath", ROUTING_DELIVERY_FILES)
def test_g2_no_canned_self_introduction_strings(relpath: str) -> None:
    hits = canned_intro_violations(relpath)
    assert not hits, "Lời tự giới thiệu đóng hộp trong code (phải từ data/identity):\n" + "\n".join(hits)


@pytest.mark.parametrize("relpath", EXPERT_FILES)
def test_g3_no_canned_expert_answers(relpath: str) -> None:
    hits = expert_canned_violations(relpath)
    assert not hits, "Đáp án viết cứng trong expert (LN-11):\n" + "\n".join(hits)


def test_guard_is_not_a_placebo_on_known_bad_snippets(tmp_path) -> None:
    """Anti-placebo: guard PHẢI bắt đúng các dạng đã từng có trên main."""
    bad_lines = [
        r'R = ((r"^(xin chào|chào bạn|hello|hi)\b", "g"),)',
        r'P = r"bạn\s+tên\s+(?:là\s+)?gì"',
        'A = "Mình là SCP — trợ lý"',
        'L = {"q1": "' + "a" * 45 + '", "q2": "' + "b" * 45 + '", "q3": "' + "c" * 45 + '"}',
    ]
    (tmp_path / "bad.py").write_text("\n".join(bad_lines) + "\n", encoding="utf-8")
    conv = conversational_violations("bad.py", tmp_path)
    assert any("xin chào" in h for h in conv), conv
    assert any("bạn tên" in h for h in conv), conv
    assert any("greeting 'hello'" in h for h in conv), conv
    assert canned_intro_violations("bad.py", tmp_path)
    assert expert_canned_violations("bad.py", tmp_path)


def test_exemptions_are_security_or_syntax_only() -> None:
    """Danh sách ngoại lệ chỉ được chứa biến tồn tại thật và đúng loại (a)/(b)."""
    present: set[str] = set()
    for rel in ROUTING_DELIVERY_FILES:
        tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                tgts = node.targets if isinstance(node, ast.Assign) else [node.target]
                present |= {t.id for t in tgts if isinstance(t, ast.Name)}
    assert EXEMPT_ASSIGNMENTS <= present, sorted(EXEMPT_ASSIGNMENTS - present)
