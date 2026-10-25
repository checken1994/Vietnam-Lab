# [HC-25 2026-10-23] Typed registry cho app.state của API server.
#
# VẤN ĐỀ CŨ (HC-25): ~17 attr trên ``app.state`` được đặt rải rác trong
# lifespan.py (boot/shutdown stage khác nhau) + api_server.py (limiter, import
# time). api_server.py và server_helpers.py đọc bằng
# ``getattr(app.state, "...", None)`` phòng thủ — hoạt động đúng nhưng KHÔNG có
# nơi nào liệt kê được: attr nào tồn tại, kiểu gì, ai ghi, ai đọc, default khi
# chưa set. Audit runtime phải dò bằng rg thay vì đọc contract.
#
# THIẾT KẾ (tối thiểu can thiệp):
# - Bảng contract ``AppStateContract``: mỗi ``StateField`` ghi tên, kiểu, giá
#   trị default, stage ghi (writer), điểm đọc (reader), ghi chú.
# - ``register(app)``: gọi MỘT LẦN ở đầu startup để set defaults cho attr chưa
#   có. Idempotent — không ghi đè giá trị đã set. Consumer getattr-defensive
#   hiện tại vẫn hoạt động nguyên vẹn (không phá reader).
# - Anti-placebo: attr nằm NGOÀI contract (set bởi ai đó lúc register chạy) →
#   WARNING log + xuất hiện trong ``audit_app_state(app)["undeclared"]``.
#   Contract KHÔNG khóa cứng ghi ngoài contract (không veto runtime) — chỉ
#   bắt buộc phải qua 1 bước khai báo để audit nhìn thấy.
# - ``audit_app_state(app)``: snapshot dict cho runtime audit dùng sau.
#
# KHÔNG migrate mọi writer ngay (deadline wave này): lifespan.py giữ nguyên
# cách gán attr trực tiếp lên app.state; contract chỉ cần đúng BẢNG —
# register() chạy trước lifespan nên defaults có sẵn, writer ghi đè đúng type.
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("scp.api")

# ---------------------------------------------------------------- contract --


@dataclass(frozen=True)
class StateField:
    """Một attr trên ``app.state`` theo contract HC-25."""

    name: str
    kind: str  # tên kiểu để đọc/audit (không ép runtime type — ghi đè writer giữ nguyên ngữ nghĩa cũ)
    default: Any
    writer: str  # stage/module nào ghi
    readers: str  # ai đọc
    note: str = ""


@dataclass(frozen=True)
class AppStateContract:
    """Bảng contract đầy đủ các attr ``app.state`` của API server.

    Nguồn quét: ``rg "app\\.state\\.\\w+ =" scp/`` tại ec4cc1eb (2026-10-23).
    """

    fields: tuple[StateField, ...] = ()

    def names(self) -> frozenset[str]:
        return frozenset(f.name for f in self.fields)


# Bảng canonical — nguồn quét: rg "app\.state\.\w+ =" scp/ tại ec4cc1eb.
CONTRACT_FIELDS: tuple[StateField, ...] = (
    StateField(
        name="judge_ready",
        kind="bool",
        default=False,
        writer="lifespan._lifespan_core (boot: False → True/False khi judge init xong; shutdown: False)",
        readers="api_server.py /health (642,714), readiness endpoints",
        note="Cờ readiness chính — /ask trả 503 khi False.",
    ),
    StateField(
        name="startup_status",
        kind="str ('starting' | 'ready' | 'failed' | 'stopping')",
        default="starting",
        writer="lifespan._lifespan_core (boot/shutdown)",
        readers="api_server.py /health",
        note="Trạng thái vòng đời tổng.",
    ),
    StateField(
        name="readiness_reason",
        kind="str | None",
        default="judge_initialization_pending",
        writer="lifespan._lifespan_core (boot/failed/shutdown)",
        readers="api_server.py /health (647,724)",
        note="None khi ready.",
    ),
    StateField(
        name="limiter",
        kind="Limiter (slowapi)",
        default=None,
        writer="api_server.py:385 (import time, TRƯỚC lifespan)",
        readers="route decorators (@limiter.limit), rate limiting middleware",
        note="Set ở import-time module api_server — register() không ghi đè.",
    ),
    StateField(
        name="background_scheduler_started",
        kind="bool",
        default=False,
        writer="lifespan (init False; True/False sau khi judge background task chạy)",
        readers="server_helpers.py (122,149), api_server.py /health (715)",
        note="",
    ),
    StateField(
        name="evolution_initialized",
        kind="bool",
        default=False,
        writer="lifespan (210 init, 217 True sau init evolution)",
        readers="background evolution task trong lifespan",
        note="",
    ),
    StateField(
        name="deep_audit_started",
        kind="bool",
        default=False,
        writer="lifespan (226 init, 286 True)",
        readers="lifespan shutdown bookkeeping",
        note="",
    ),
    StateField(
        name="deep_audit_stop",
        kind="threading.Event | None",
        default=None,
        writer="lifespan (237 khi deep audit boot được bật)",
        readers="lifespan shutdown (575) — set event để dừng thread",
        note="Stop-handle, không phải service object.",
    ),
    StateField(
        name="attack_monitor_started",
        kind="bool",
        default=False,
        writer="lifespan (227 init, 341 True)",
        readers="lifespan shutdown bookkeeping",
        note="",
    ),
    StateField(
        name="attack_monitor_stop",
        kind="threading.Event | None",
        default=None,
        writer="lifespan (298)",
        readers="lifespan shutdown (576)",
        note="Stop-handle.",
    ),
    StateField(
        name="doubt_cron",
        kind="scheduler handle | None",
        default=None,
        writer="lifespan (380)",
        readers="shutdown bookkeeping trong lifespan",
        note="",
    ),
    StateField(
        name="why_verify_stop",
        kind="threading.Event | None",
        default=None,
        writer="lifespan (396)",
        readers="lifespan shutdown (578)",
        note="Stop-handle.",
    ),
    StateField(
        name="why_verify_thread",
        kind="threading.Thread | None",
        default=None,
        writer="(không có writer — HC-39: lifespan không còn ghi thread why-verify; thay bằng registry job 'why_verify')",
        readers="không còn reader (attr giữ trong contract để audit các state cũ)",
        note="HC-39: vòng why-verify chạy trong BackgroundJobRegistry (job 'why_verify'); stop-handle vẫn là why_verify_stop.",
    ),
    StateField(
        name="retry_policy_stop",
        kind="threading.Event | None",
        default=None,
        writer="lifespan (469)",
        readers="lifespan shutdown (577)",
        note="Stop-handle.",
    ),
    StateField(
        name="retry_policy",
        kind="RetryPolicy | None",
        default=None,
        writer="lifespan (480)",
        readers="kernel polling worker trong lifespan",
        note="",
    ),
    StateField(
        name="discovery_scheduler",
        kind="scheduler handle | None",
        default=None,
        writer="lifespan (511)",
        readers="lifespan shutdown (584) — getattr defensive",
        note="",
    ),
    StateField(
        name="inbox_daemon",
        kind="InboxDaemon | None",
        default=None,
        writer="lifespan (545 True / 548,551,621 None)",
        readers="lifespan shutdown",
        note="None khi daemon không khởi động được (non-fatal).",
    ),
    StateField(
        name="task_kernel",
        kind="TaskKernel | None",
        default=None,
        writer="(không có writer trong repo tại ec4cc1eb — đọc defensive qua hasattr)",
        readers="lifespan shutdown (632) — close() nếu có",
        note="Chỗ gắn kernel adapter ngoài lifespan nếu tương lai cần; default None an toàn.",
    ),
    StateField(
        name="app_container",
        kind="AppContainer | None",
        default=None,
        writer="lifespan._lifespan_core (H3-W1 · build_container idempotent, container thật dựng lần đầu tại api_server import-time)",
        readers="routes/pipeline cần object dùng chung qua composition root",
        note="DI composition root — scp/api_server_parts/composition.py. close() ở lifespan shutdown.",
    ),
)

APP_STATE_CONTRACT = AppStateContract(fields=CONTRACT_FIELDS)


# ---------------------------------------------------------------- register --


def _state_mapping(state: Any) -> dict[str, Any]:
    """Mapping attr thật của state object.

    Starlette ``State`` (app.state) giữ attr trong ``self._state`` dict, không
    phải ``__dict__`` trực tiếp — ``vars(state)`` sẽ chỉ thấy ``_state``.
    Fallback ``vars()`` cho state object thuần (test/instance khác).
    """
    inner = getattr(state, "_state", None)
    if isinstance(inner, dict):
        return inner
    return dict(vars(state))


def register(app: Any) -> dict[str, list[str]]:
    """Gọi MỘT LẦN đầu startup: set defaults cho attr chưa có + cảnh báo attr
    ngoài contract. Idempotent; KHÔNG ghi đè giá trị đã set (limiter đã được
    api_server.py set ở import time phải sống sót).

    Trả về ``{"initialized": [...], "kept": [...], "undeclared": [...]}``
    (cùng dữ liệu mà audit_app_state dùng, để test/audit pin).
    """
    declared = APP_STATE_CONTRACT.names()
    initialized: list[str] = []
    kept: list[str] = []
    undeclared: list[str] = []

    # Attr hiện có nằm ngoài contract → WARNING (anti-placebo HC-25): mọi attr
    # mới bắt buộc được thêm vào CONTRACT_FIELDS, nếu không audit sẽ văng.
    for existing in sorted(_state_mapping(app.state)):
        if existing not in declared:
            undeclared.append(existing)
            logger.warning(
                "[HC-25] app.state.%s nằm ngoài AppStateContract — khai báo trong "
                "scp/api_server_parts/app_state.py CONTRACT_FIELDS hoặc dỡ gán.",
                existing,
            )

    for f in CONTRACT_FIELDS:
        if hasattr(app.state, f.name):
            kept.append(f.name)
            continue
        try:
            setattr(app.state, f.name, f.default)
            initialized.append(f.name)
        except Exception:  # pragma: no cover — state object chặn ghi là bất thường
            logger.exception("[HC-25] Không set default cho app.state.%s", f.name)
            raise

    if undeclared:
        logger.warning("[HC-25] %d attr ngoài contract: %s", len(undeclared), ", ".join(undeclared))
    logger.info(
        "[HC-25] AppStateContract register: %d defaults set, %d kept, %d undeclared",
        len(initialized),
        len(kept),
        len(undeclared),
    )
    return {"initialized": initialized, "kept": kept, "undeclared": undeclared}


def audit_app_state(app: Any) -> dict[str, Any]:
    """Snapshot ``app.state`` đối chiếu contract — runtime audit dùng sau.

    Trả về dict:
      - ``declared``: {tên: {kind, present, value_type, value_repr}} cho mọi
        field trong contract.
      - ``missing``: field trong contract chưa được set (register chưa chạy
        hoặc bị writer xoá) — mọi field default-None/False vẫn phải present
        sau register.
      - ``undeclared``: attr ngoài contract + type/value của nó.
    """
    declared: dict[str, Any] = {}
    missing: list[str] = []
    for f in CONTRACT_FIELDS:
        if hasattr(app.state, f.name):
            value = getattr(app.state, f.name)
            declared[f.name] = {
                "kind": f.kind,
                "writer": f.writer,
                "present": True,
                "value_type": type(value).__name__,
                "value_repr": repr(value),
            }
        else:
            declared[f.name] = {"kind": f.kind, "writer": f.writer, "present": False}
            missing.append(f.name)

    declared_names = APP_STATE_CONTRACT.names()
    undeclared = {
        name: {"value_type": type(val).__name__, "value_repr": repr(val)}
        for name, val in sorted(_state_mapping(app.state).items())
        if name not in declared_names
    }
    return {
        "declared": declared,
        "missing": missing,
        "undeclared": undeclared,
        "contract_size": len(CONTRACT_FIELDS),
    }
