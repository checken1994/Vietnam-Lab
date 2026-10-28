# SPDX-License-Identifier: AGPL-3.0-only
"""GĐ1 Output Behavior Gate — Runtime Secret Matcher & Canary Scanner.

Semantic Migration Roadmap GĐ1 (docs/security/SEMANTIC_MIGRATION_ROADMAP.md):
chốt chặn rò rỉ đầu ra bằng so khớp GIÁ TRỊ secret runtime (hash exact-match
+ normalized so khớp), thay cho phrase-listing từ khóa (GA.md mandate
bfb40026 cấm phrase-listing; tên biến "SCP_ADMIN_KEY"/"token" trong câu hỏi
phải ALLOW — chỉ GIÁ TRỊ thật mới BLOCK).

Chuẩn hóa so khớp: NFKC → casefold → fold ký tự confusable (Cyrillic/Greek
homoglyph về Latin) → skeleton (chỉ giữ ký tự chữ-số). Skeleton bắt được
các biến thể: chèn khoảng trắng/zero-width/soft-hyphen/tab, gạch nối thay
gạch dưới, fullwidth, chữ toán học, homoglyph, hoa/thường.

Giải mã tại chỗ trước khi so: URL-encode (1-2 lớp), HTML numeric entities,
Base64 (kể cả không padding / bị bọc dòng), Hex (kể cả 0x prefix, hoa) —
lồng tối đa 2 vòng. Owner Q5=5.C: thêm view ROT13 + LEET chuẩn
(4→a, 3→e, 1→i, 7→t, 0→o, 5→s); đánh-vần-âm-position/acrostic NLP đầy đủ
thuộc GĐ2 — đối chiếu ghi chú CASE-082 bên dưới.

Khớp một phần (owner Q4=4.A): token chữ-số liền là tiền tố của skeleton
secret (hoặc của core-skeleton đã bỏ prefix phổ biến sk-/bearer) với cửa
sổ >= 12 ký tự HOẶC >= 50% độ dài skeleton → rò rỉ từng đoạn (rationale
CASE-081; semantics tiền tố-thẳng là ràng buộc của ground-truth CASE-023/
097: near-miss 1 ký tự cuối phải ALLOW).

Acrostic: nếu văn bản >= 8 dòng, ghép chữ cái đầu mỗi dòng thành view riêng
để đối soát (rationale CASE-082 BẮT BUỘC BLOCK — trích xuất tất định 5 dòng
này giữ lại ở GĐ1 dù Q5=5.C xếp acrostic chung sang GĐ2; xem WorkerReport).

Canary: match canary → BLOCK + cờ canary_alert + secondary_action="ALERT"
và ghi log security-incident RIÊNG kênh scp.security.incident (owner Q3=3.A).

Bảo mật giá trị: secret được giữ lại trong RAM để so khớp chuỗi; log/audit
chỉ ghi ref + sha256[:12] + loại match, KHÔNG BAO GIỜ log giá trị.
Owner 2.B: surface memory_write mặc định REDACT (SCP_GD1_MEMORY_REDACT
mặc định 1; =0 để chặn hẳn); REDACT phải redact SẠCH — còn match sau
redact thì hạ về BLOCK. Output chat vẫn BLOCK 100%.
"""
from __future__ import annotations

import base64
import binascii
import codecs
import hashlib
import html
import logging
import os
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from urllib.parse import unquote

logger = logging.getLogger("scp.security.output_behavior_gate")
# Kênh cảnh báo an ninh riêng cho canary (owner Q3=3.A — security-incident).
incident_log = logging.getLogger("scp.security.incident")

VERDICT_ALLOW = "ALLOW"
VERDICT_BLOCK = "BLOCK"
ACTION_REDACT = "REDACT"
MEMORY_REDACT_ENV = "SCP_GD1_MEMORY_REDACT"
MEMORY_REDACT_DEFAULT = "1"  # owner 2.B: memory_write mặc định REDACT
REDACT_PLACEHOLDER = "[REDACTED_SECRET]"
SECONDARY_ALERT = "ALERT"

# Confusable homoglyph (UTS #39-style, bản thu gọn) — Cyrillic/Greek trùng hình Latin.
_CONFUSABLES = str.maketrans({
    "\u0430": "a", "\u0435": "e", "\u043e": "o", "\u0440": "p", "\u0441": "c",
    "\u0445": "x", "\u0443": "y", "\u0456": "i", "\u0458": "j", "\u0455": "s",
    "\u0501": "d", "\u04bb": "h", "\u045b": "h", "\u0454": "e", "\u045c": "k",
    "\u03b1": "a", "\u03b2": "b", "\u03b3": "g", "\u03b4": "d", "\u03b5": "e",
    "\u03b6": "z", "\u03b7": "h", "\u03b9": "i", "\u03ba": "k", "\u03bc": "m",
    "\u03bd": "n", "\u03bf": "o", "\u03c1": "p", "\u03c4": "t", "\u03c5": "u",
    "\u03c7": "x", "\u03c9": "w", "\u03c2": "s", "\u03b8": "0",
})

_WS_RE = re.compile(r"\s+")
_B64_RE = re.compile(r"[A-Za-z0-9+/]{16,}={0,2}")
_HEX_RE = re.compile(r"(?:0x)?[0-9a-fA-F]{16,}")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]+")
# Owner Q5=5.C: bảng LEET chuẩn; ROT13 dùng codecs.decode(s, "rot13").
_LEET = str.maketrans({"4": "a", "3": "e", "1": "i", "7": "t", "0": "o", "5": "s"})
# Owner Q4=4.A: prefix phổ biến bị loại trước khi tính core-skeleton ngưỡng.
_PREFIX_RE = re.compile(r"^(?:sk-|bearer[\s_-]*)", re.IGNORECASE)
_PARTIAL_MIN_CHARS = 12
_PARTIAL_MIN_RATIO = 0.5
_MIN_SECRET_LEN = 4
_ACROSTIC_MIN_LINES = 8


def normalize_text(s: str) -> str:
    """NFKC + casefold + fold homoglyph confusable về Latin."""
    return unicodedata.normalize("NFKC", s).casefold().translate(_CONFUSABLES)


def skeleton(s: str) -> str:
    """Bỏ mọi ký tự không phải chữ-số (kể cả zero-width, soft-hyphen, dấu câu)."""
    return "".join(ch for ch in normalize_text(s) if ch.isalnum())


def _acrostic(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < _ACROSTIC_MIN_LINES:
        return ""
    return "".join(ln[0] for ln in lines)


def _decoded_views(view: str) -> set[str]:
    """Giải mã các token Base64/Hex đủ dài trong view; trả chuỗi utf-8 đọc được."""
    out: set[str] = set()
    compact = _WS_RE.sub("", view)
    for tok in _B64_RE.findall(compact):
        try:
            raw = base64.b64decode(tok + "=" * (-len(tok) % 4), validate=False)
            out.add(raw.decode("utf-8"))
        except (binascii.Error, ValueError, UnicodeDecodeError):
            continue
    for tok in _HEX_RE.findall(compact):
        t = tok[2:] if tok.startswith(("0x", "0X")) else tok
        if len(t) % 2:
            continue
        try:
            raw = bytes.fromhex(t)
            out.add(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
    return out


def build_views(text: str) -> set[str]:
    """Mọi 'view' của văn bản cần đối soát: nguyên văn, compact, giải mã lồng."""
    views = {text, _WS_RE.sub("", text)}
    u1 = unquote(text)
    views.update({u1, _WS_RE.sub("", u1), unquote(u1)})
    views.add(html.unescape(text))
    acr = _acrostic(text)
    if acr:
        views.add(acr)
    for v in tuple(views):  # Q5=5.C: view ROT13 + view LEET của mọi view gốc
        views.add(codecs.decode(v, "rot13"))
        views.add(v.translate(_LEET))
    round1: set[str] = set()
    for v in tuple(views):
        round1 |= _decoded_views(v)
    round2: set[str] = set()
    for v in tuple(round1):
        round2 |= _decoded_views(v)
    views |= round1 | round2
    views.update(_WS_RE.sub("", v) for v in round1 | round2)
    return views


@dataclass
class _Record:
    ref: str
    value: str
    sha256: str
    norm: str
    skel: str
    core: str  # skeleton sau khi bỏ prefix sk-/bearer (owner Q4=4.A)
    is_canary: bool


@dataclass
class GateResult:
    verdict: str
    matched_refs: tuple[str, ...] = ()
    matched_kinds: dict[str, str] = field(default_factory=dict)
    canary_alert: bool = False
    secondary_action: str = "NONE"


def _as_pairs(x: object, prefix: str) -> list[tuple[str, str]]:
    if not x:
        return []
    if isinstance(x, Mapping):
        return [(str(k), str(v)) for k, v in x.items() if v]
    return [(f"{prefix}{i}", str(v)) for i, v in enumerate(x) if v]  # type: ignore[arg-type]


def _prepare(pairs: Iterable[tuple[str, str]], is_canary: bool) -> list[_Record]:
    recs: list[_Record] = []
    for ref, value in pairs:
        value = value.strip()
        if len(value) < _MIN_SECRET_LEN:
            continue
        recs.append(_Record(
            ref=ref, value=value,
            sha256=hashlib.sha256(value.encode("utf-8")).hexdigest(),
            norm=normalize_text(value), skel=skeleton(value),
            core=skeleton(_PREFIX_RE.sub("", normalize_text(value))), is_canary=is_canary,
        ))
    return recs


def scan_output(text: object, secrets: object, canaries: object = ()) -> GateResult:
    """Quét text đối soát với giá trị secret/canary runtime.

    secrets/canaries: Mapping ref→giá trị, hoặc iterable giá trị (ref tự sinh).
    Trả GateResult; KHÔNG log giá trị secret — chỉ ref + sha256[:12] + loại match.
    """
    text = str(text or "")
    records = _prepare(_as_pairs(secrets, "SECRET:"), False)
    records += _prepare(_as_pairs(canaries, "CANARY:"), True)
    if not records:
        return GateResult(verdict=VERDICT_ALLOW)

    views = build_views(text)
    view_norms = [normalize_text(v) for v in views]
    view_skels = [skeleton(v) for v in views]
    tokens: set[str] = set()
    for vn in view_norms:
        tokens.update(_TOKEN_RE.findall(vn))

    matched_kinds: dict[str, str] = {}
    canary_alert = False
    for rec in records:
        kind = ""
        if rec.norm and any(rec.norm in vn for vn in view_norms):
            kind = "exact"
        elif rec.skel and len(rec.skel) >= _PARTIAL_MIN_CHARS and any(
            rec.skel in vs for vs in view_skels
        ):
            kind = "skeleton"
        else:
            # Owner Q4=4.A: cửa sổ tiền tố >= 12 ký tự HOẶC >= 50% skeleton,
            # đối cả core-skeleton (đã bỏ prefix sk-/bearer).
            for tok in tokens:
                wins = (skeleton(tok), skeleton(_PREFIX_RE.sub("", tok)))
                if any(
                    cand.startswith(win)
                    and (len(win) >= _PARTIAL_MIN_CHARS or len(win) >= _PARTIAL_MIN_RATIO * len(cand))
                    for cand in (rec.skel, rec.core)
                    for win in wins
                ):
                    kind = "partial"
                    break
        if kind:
            matched_kinds[rec.ref] = kind
            if rec.is_canary:
                canary_alert = True
                incident_log.error(  # Q3=3.A: kênh security-incident riêng
                    "[GD1-INCIDENT][%s] canary match ref=%s sha256=%s kind=%s",
                    SECONDARY_ALERT, rec.ref, rec.sha256[:12], kind,
                )
            logger.warning(
                "[GD1] match ref=%s sha256=%s kind=%s canary=%s",
                rec.ref, rec.sha256[:12], kind, rec.is_canary,
            )
    verdict = VERDICT_BLOCK if matched_kinds else VERDICT_ALLOW
    return GateResult(
        verdict=verdict,
        matched_refs=tuple(sorted(matched_kinds)),
        matched_kinds=matched_kinds,
        canary_alert=canary_alert,
        secondary_action=SECONDARY_ALERT if canary_alert else "NONE",
    )


def apply_surface_policy(result: GateResult, surface: str, env: Mapping[str, str] | None = None) -> str:
    """ALLOW → ALLOW; match trên surface='memory_write' → REDACT theo owner 2.B
    (SCP_GD1_MEMORY_REDACT mặc định 1; =0 để BLOCK hẳn); mọi match còn lại → BLOCK."""
    if result.verdict == VERDICT_ALLOW:
        return VERDICT_ALLOW
    env = os.environ if env is None else env
    flag = str(env.get(MEMORY_REDACT_ENV, MEMORY_REDACT_DEFAULT)).strip().lower() in ("1", "true", "yes", "on")
    if surface == "memory_write" and flag:
        return ACTION_REDACT
    return VERDICT_BLOCK


def redact_text(
    text: str, secrets: object, canaries: object = (), placeholder: str = REDACT_PLACEHOLDER
) -> str | None:
    """Thay mọi cửa sổ con khớp normalized-exact secret bằng placeholder.

    Trả None nếu sau một vòng redact vẫn còn match (không redact sạch được —
    caller phải giữ BLOCK, fail-closed, không được trả text bán redact).
    """
    text = str(text or "")
    records = _prepare(_as_pairs(secrets, "SECRET:"), False)
    records += _prepare(_as_pairs(canaries, "CANARY:"), True)
    if not records:
        return text
    spans: list[tuple[int, int]] = []
    n = len(text)
    for rec in records:
        w = len(rec.norm)
        for i in range(n):
            for delta in (0, 1, 2, 3, 4, -1, -2):
                j = i + w + delta
                if j <= i or j > n:
                    continue
                if normalize_text(text[i:j]) == rec.norm:
                    spans.append((i, j))
                    break
    if not spans:
        return None
    spans.sort()
    merged: list[list[int]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    out: list[str] = []
    pos = 0
    for s, e in merged:
        out.append(text[pos:s], )
        out.append(placeholder)
        pos = e
    out.append(text[pos:])
    redacted = "".join(out)
    # Fail-closed: redact xong phải rescan sạch, nếu không trả None để caller BLOCK.
    res = scan_output(redacted, secrets, canaries)
    if res.verdict != VERDICT_ALLOW:
        return None
    return redacted


def collect_runtime_secrets() -> dict[str, str]:
    """Thu thập giá trị secret runtime từ secret_registry (env HOẶC *_FILE).

    Trả dict ref(tên secret)→giá trị. Giá trị rỗng/quá ngắn bị bỏ. Không log giá trị.
    """
    try:
        from scp.security import secret_registry
    except Exception:  # pragma: no cover - registry luôn có mặt trong repo
        logger.error("[GD1] secret_registry import failed — runtime secret set rỗng")
        return {}
    out: dict[str, str] = {}
    for name in getattr(secret_registry, "SECRET_PURPOSES", {}):
        try:
            value = secret_registry.resolve(name)
        except Exception as exc:  # fail-closed per-secret: log tên+lỗi, bỏ qua giá trị
            logger.error("[GD1] resolve %s failed: %s", name, type(exc).__name__)
            continue
        if value and len(value) >= _MIN_SECRET_LEN:
            out[name] = value
    return out


def collect_runtime_canaries(env: Mapping[str, str] | None = None) -> list[str]:
    """Canary token từ env SCP_CANARY_TOKENS (phân tách ; , hoặc newline)."""
    env = os.environ if env is None else env
    raw = str(env.get("SCP_CANARY_TOKENS", "") or "")
    return [t.strip() for t in re.split(r"[;,\n]", raw) if len(t.strip()) >= _MIN_SECRET_LEN]
