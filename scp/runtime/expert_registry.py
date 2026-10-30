"""
Expert registry — explicit binding: registry domain id -> domain expert class.

TẠI SAO (lane knowledge-experts wire-or-kill, G-1 trong verify-domains-coverage):
  Trước đây ``RealityJudge.domain_experts`` quét ``os.listdir(experts/)`` và ghi
  ``dict[instance.domain] = instance`` — "cái nạp sau cùng thắng". Hậu quả đã
  quan sát bằng probe uvicorn thật (main 9e2837a2):
    * câu ``history`` triệu hồi ``lifestyle.Holiday`` (API ngày lễ), ``geography``
      triệu hồi ``lifestyle.City``, ``biology`` triệu hồi ``lifestyle.AnimalFacts``
      (catfact ngẫu nhiên) — vì nhiều class khai cùng khóa, thứ tự phụ thuộc
      ``os.listdir`` (NTFS: sắp xếp; ext4/overlayfs trong container: không xác định);
    * 7 expert khai khóa không có trong ``domain_registry.DOMAINS`` (``law`` ≠
      ``legal``, ``economics``, ``engineering``, ``literature``, ``management``,
      ``news``, ``philosophy``) nên ``classify_top1`` không bao giờ trả khóa đó;
    * 16 id registry không có expert nào khai trùng tên.
  Nguyên nhân gốc: khóa định tuyến = chuỗi tự khai trong từng class, không có
  một nơi khai báo đối chiếu với registry ⇒ không ai thấy trùng/lệch.

CƠ CHẾ (tổng quát, không có mẫu câu hỏi):
  * ``DOMAIN_EXPERT_BINDINGS`` phủ ĐỦ mọi id của ``domain_registry.DOMAINS``:
    giá trị là ``"module:Class"`` hoặc ``None`` = "no-expert" trung thực.
  * ``UNBOUND_EXPERTS`` liệt kê mọi class expert KHÔNG được bind, kèm lý do —
    test tĩnh buộc mỗi class expert phải nằm ở đúng một trong hai bảng, nên
    expert mồ côi không thể xuất hiện lại trong im lặng.
  * Fail-closed khi dựng: target không import được / không phải BaseSLM /
    khởi tạo lỗi / mang bảng tra cứu nhúng (class hoặc instance) ⇒ id đó KHÔNG được bind
    và lý do được ghi (log WARNING + ``refusals``), không bao giờ "đè".

Bảng tra cứu nhúng (Mapping không rỗng, khóa chuỗi -> văn bản/bản ghi, ở cấp
class hoặc instance ngay sau khởi tạo — vd ``_LOCAL_KNOWLEDGE``/``_FRAMEWORKS``/
``History._local``) là đáp án viết cứng khớp chuỗi con câu hỏi (H12). Expert
mang bảng đó bị từ chối bind cho tới khi bảng được gỡ (DEHARDCODE / LN-11) — chặn
tái phát: thêm lại bảng vào expert đang bind ⇒ id đó tự rơi về no-expert + WARNING.
"""
from __future__ import annotations

import importlib
import logging
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger("scp.expert_registry")

_EXPERTS_PKG = "scp.runtime.experts"

# Registry domain id (scp.data_sources.domain_registry.DOMAINS) -> "module:Class" | None.
# None = no-expert (trung thực): không có class expert nào trả lời miền này từ dữ liệu.
DOMAIN_EXPERT_BINDINGS: dict[str, str | None] = {
    # --- science -----------------------------------------------------------
    "math": None,  # không còn class Math (sciences_slm gỡ ở e40af003); engine tính toán nằm ngoài expert
    "physics": None,  # không có class expert
    "chemistry": None,  # không có class expert
    "astronomy": None,  # không có class expert
    "biology": None,  # AnimalFacts (catfact ngẫu nhiên) KHÔNG phải chuyên gia sinh học — gỡ khóa đè
    "geography": "humanities:Geography",  # trước: lifestyle.City đè (last-wins)
    # trước: lifestyle.Holiday đè (last-wins). History hiện mang bảng năm→sự kiện viết cứng
    # (self._local, 143 mục tiếng Việt) ⇒ guard từ chối bind tới khi bảng được gỡ (LN-11).
    "history": "humanities:History",
    "logic": "logic:Logic",
    "statistics": None,  # không có class expert
    "weather": "weather:Weather",
    "conversion": "misc:Conversion",
    "reality": None,  # không có class expert
    "agriculture": "agriculture:Agriculture",
    "environment": "environment:Environment",
    "foodtech": "foodtech:FoodTech",
    "oceanography": "oceanography:Oceanography",
    "cartography": "cartography:Cartography",
    "geology": "geology:Geology",
    # --- business ----------------------------------------------------------
    "finance": "finance:Finance",
    "tourism": "tourism:Tourism",
    "transport": "transport:Transport",
    "digitalmarketing": "digitalmarketing:DigitalMarketing",
    "ecommerce": "ecommerce:Ecommerce",
    # --- health ------------------------------------------------------------
    "medical": None,  # không có class expert (spacemedicine là miền khác — không giả vờ)
    "spacemedicine": "spacemedicine:SpaceMedicine",
    # --- tech --------------------------------------------------------------
    "technology": "engineering:Engineering",  # registry en_name "Technology & Engineering"; trước: mồ côi (khai "engineering")
    "architecture": "architecture:Architecture",
    "energy": "energy:Energy",
    "aerospace": "aerospace:Aerospace",
    "blockchain": "blockchain:Blockchain",
    "genai": "genai:GenAI",
    "uxui": "uxui:UXUI",
    "audiovideo": "audiovideo:AudioVideo",
    "cybersecurity": "cybersecurity:Cybersecurity",
    # --- society -----------------------------------------------------------
    "legal": "law:Law",  # registry "Law & Administration"; trước: Law khai "law" ⇒ mồ côi
    "education": "education:Education",
    "psychology": "psychology:Psychology",
    "diplomacy": "diplomacy:Diplomacy",
    "military": "military:Military",
    "social": "social:Social",
    "entertainment": "misc:Entertainment",  # lifestyle.ChuckNorris từng khai cùng khóa
    "food": "lifestyle:Food",
    "general": "lifestyle:General",  # lifestyle.Advice từng khai cùng khóa
    # --- culture -----------------------------------------------------------
    "sports": None,  # không có class expert
    "arts": "art:Art",
    "heritage": None,  # không có class expert
    "religion": "religion:Religion",  # lifestyle.Religion từng khai cùng khóa
    "crafts": "crafts:Crafts",
    # --- special -----------------------------------------------------------
    "holiday": "lifestyle:Holiday",  # tra cứu date.nager.at theo mã nước trong câu
    "city": "lifestyle:City",  # tra cứu geocoding theo tên thành phố trong câu
    "animal_facts": None,  # endpoint ngẫu nhiên, bỏ qua nội dung câu ⇒ không phải bằng chứng (G-4)
    "advice": None,  # endpoint ngẫu nhiên (adviceslip) ⇒ không phải bằng chứng (G-4)
    "chuck_norris": None,  # endpoint ngẫu nhiên (joke) ⇒ không phải bằng chứng (G-4)
    "universal": "misc:Universal",
}

# Class expert KHÔNG được bind vào id registry nào, kèm lý do (quyết định wire-or-kill).
UNBOUND_EXPERTS: dict[str, str] = {
    "economics:Economics": (
        "KILL-registry: không có id 'economics' trong registry; thêm id mới cần keyword "
        "định tuyến (mẫu cứng — cấm). Code giữ: test DEHARDCODE (static guard, routing "
        "behavior) và provenance staging/legacy_unverified/economics.jsonl trỏ tới file này."
    ),
    "news:News": (
        "KILL-registry: không có id 'news' trong registry; NewsAPI cần key. Code giữ "
        "(tests/T02_contract/test_m3_architectural_deprecation.py import trực tiếp)."
    ),
    "literature:Literature": (
        "HOLD-AG: không có id registry; dữ liệu thật ở catalog humanities.literature "
        "(12 bản ghi verified) ⇒ cần reader tier đọc catalog (lane AG), không thêm keyword."
    ),
    "philosophy:Philosophy": (
        "HOLD-AG: không có id registry; dữ liệu thật ở catalog humanities.philosophy "
        "(18 bản ghi verified) ⇒ cần reader tier đọc catalog (lane AG), không thêm keyword."
    ),
    "management:Management": (
        "HOLD-AG: không có id registry; dữ liệu thật ở catalog reference "
        "management.frameworks (16 bản ghi) ⇒ cần reader tier (lane AG), không thêm keyword."
    ),
    "lifestyle:AnimalFacts": "Endpoint ngẫu nhiên (catfact/dog fact) bỏ qua câu hỏi ⇒ không bind (G-4/LN-12).",
    "lifestyle:Advice": "Endpoint ngẫu nhiên (adviceslip) bỏ qua câu hỏi ⇒ không bind (G-4/LN-12).",
    "lifestyle:ChuckNorris": "Endpoint ngẫu nhiên (joke) bỏ qua câu hỏi ⇒ không bind (G-4/LN-12).",
    "lifestyle:Religion": "Trùng khóa 'religion' với religion.Religion (đang bind) ⇒ không bind bản trùng.",
}


def resolve_target(target: str) -> type:
    """'module:Class' -> class. Module không có dấu chấm được hiểu trong scp.runtime.experts."""
    module_name, sep, class_name = target.partition(":")
    if not sep or not module_name or not class_name:
        raise ValueError(f"binding target phải có dạng 'module:Class', nhận {target!r}")
    if "." not in module_name:
        short = f"{_EXPERTS_PKG}.{module_name}"
        try:
            return getattr(importlib.import_module(short), class_name)
        except ModuleNotFoundError as exc:
            if exc.name != short:
                raise
        # không có trong experts/ ⇒ module tuyệt đối (test tĩnh buộc mọi target
        # của bảng thật phải là class trong experts/)
    module = importlib.import_module(module_name)
    return getattr(module, class_name)


def _is_answer_table(value: Any) -> bool:
    """Mapping không rỗng: khóa chuỗi -> giá trị là văn bản/bản ghi (không phải bộ đếm số)."""
    return (
        isinstance(value, Mapping)
        and bool(value)
        and any(isinstance(k, str) for k in value)
        and any(isinstance(v, (str, Mapping)) for v in value.values())
    )


def embedded_lookup_tables(cls: type, instance: Any = None) -> list[str]:
    """Tên các bảng tra cứu nhúng trong expert: thuộc tính cấp class (theo MRO) và,
    nếu có ``instance``, thuộc tính instance ngay sau khởi tạo (vd ``History._local``)."""
    found: list[str] = []
    for klass in cls.__mro__:
        if klass is object:
            continue
        for name, value in vars(klass).items():
            if not name.startswith("__") and _is_answer_table(value):
                found.append(f"{klass.__name__}.{name}")
    if instance is not None:
        for name, value in vars(instance).items():
            if not name.startswith("__") and _is_answer_table(value):
                found.append(f"{cls.__name__}().{name}")
    return found


def build_domain_experts(
    bindings: Mapping[str, str | None] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Dựng bảng id registry -> instance expert. Trả (experts, refusals).

    Fail-closed: mọi lỗi ở một id ⇒ id đó không có expert + lý do trong refusals.
    Một class được bind ở nhiều id dùng chung một instance.
    """
    from scp.runtime.slm_base import BaseSLM

    table = DOMAIN_EXPERT_BINDINGS if bindings is None else bindings
    experts: dict[str, Any] = {}
    refusals: dict[str, str] = {}
    instances: dict[type, Any] = {}
    for domain_id, target in table.items():
        if target is None:
            continue
        try:
            cls = resolve_target(target)
        except Exception as exc:  # noqa: BLE001 — fail-closed: mọi lỗi resolve ⇒ id không bind + lý do
            refusals[domain_id] = f"unresolvable:{target}:{type(exc).__name__}: {exc}"
            continue
        if not (isinstance(cls, type) and issubclass(cls, BaseSLM) and cls is not BaseSLM):
            refusals[domain_id] = f"not_a_domain_expert:{target}"
            continue
        tables = embedded_lookup_tables(cls)
        if tables:
            refusals[domain_id] = f"embedded_answer_table:{target}:{','.join(sorted(tables))}"
            continue
        if cls not in instances:
            try:
                instance = cls()
            except Exception as exc:  # noqa: BLE001 — fail-closed: khởi tạo lỗi ⇒ id không bind + lý do
                refusals[domain_id] = f"init_failed:{target}:{type(exc).__name__}: {exc}"
                continue
            tables = embedded_lookup_tables(cls, instance)
            if tables:
                refusals[domain_id] = f"embedded_answer_table:{target}:{','.join(sorted(tables))}"
                continue
            instances[cls] = instance
        experts[domain_id] = instances[cls]
    for domain_id, reason in sorted(refusals.items()):
        logger.warning("expert binding refused for domain %r: %s", domain_id, reason)
    return experts, refusals
