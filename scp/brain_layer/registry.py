"""Brain Layer P0 — registry bất biến từ config dict.

Parse providers/models/tasks thành frozen dataclass + mapping chỉ-đọc.
Vi phạm cấu trúc (trùng id, trỏ provider/model không tồn tại, thiếu
champion/fallback) ⇒ BrainConfigError fail-closed (thiết kế §3.1).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from scp.brain_layer.errors import BrainConfigError

PROVIDER_EGRESS_LEVELS: frozenset[str] = frozenset({"local", "external"})
# Kinds provider P0 hỗ trợ (external OpenAI-compatible + local/fake).
PROVIDER_KINDS: frozenset[str] = frozenset(
    {"external_openai_compatible", "local", "fake"}
)


def _mapping(value: Any) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


@dataclass(frozen=True)
class ProviderDef:
    provider_id: str
    kind: str
    base_url: str = ""
    # Chỉ là TÊN biến secret (ví dụ SCP_OPENROUTER_API_KEY) — không bao giờ
    # chứa giá trị secret; resolve qua scp.security.secret_registry khi cần.
    secret_ref: str = ""
    egress: str = "external"


def _empty_mapping() -> Mapping[str, Any]:
    return _mapping({})


@dataclass(frozen=True)
class ModelDef:
    model_id: str
    provider_id: str
    roles: tuple[str, ...] = ()
    # Giới hạn vận hành khai trong registry (vd timeout_s) — chỉ là TÊN cấu
    # hình số, không chứa secret. Adapter đọc (vd timeout HTTP) từ đây.
    limits: Mapping[str, Any] = field(default_factory=_empty_mapping)


@dataclass(frozen=True)
class TaskDef:
    task: str
    role: str = ""
    champion: str = ""
    fallbacks: tuple[str, ...] = ()
    output_required: tuple[str, ...] = ()
    data_classes: tuple[str, ...] = ("public",)
    # Task names mà champion của chúng phải khác champion của task này
    # (judge ≠ generate theo thiết kế §3.1).
    independence: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class BrainRegistry:
    version: str
    providers: Mapping[str, ProviderDef]
    models: Mapping[str, ModelDef]
    tasks: Mapping[str, TaskDef]


def _require_list(cfg: Mapping[str, Any], key: str, errs: list[str]) -> list[Any]:
    value = cfg.get(key)
    if not isinstance(value, list) or not value:
        errs.append(f"{key}: bắt buộc là list không rỗng")
        return []
    return value


def _parse_providers(cfg: Mapping[str, Any], errs: list[str]) -> dict[str, ProviderDef]:
    providers: dict[str, ProviderDef] = {}
    for i, raw in enumerate(_require_list(cfg, "providers", errs)):
        if not isinstance(raw, dict):
            errs.append(f"providers[{i}]: phải là mapping")
            continue
        provider_id = str(raw.get("provider_id") or "").strip()
        if not provider_id:
            errs.append(f"providers[{i}]: thiếu provider_id")
            continue
        if provider_id in providers:
            errs.append(f"providers[{i}]: provider_id '{provider_id}' bị trùng")
            continue
        kind = str(raw.get("kind") or "").strip()
        if kind not in PROVIDER_KINDS:
            errs.append(
                f"providers[{i}] '{provider_id}': kind '{kind}' không thuộc {sorted(PROVIDER_KINDS)}"
            )
            continue
        egress = str(raw.get("egress") or "").strip() or (
            "local" if kind in {"local", "fake"} else "external"
        )
        if egress not in PROVIDER_EGRESS_LEVELS:
            errs.append(f"providers[{i}] '{provider_id}': egress '{egress}' không hợp lệ")
            continue
        base_url = str(raw.get("base_url") or "").strip()
        secret_ref = str(raw.get("secret_ref") or "").strip()
        if kind == "external_openai_compatible":
            if not base_url:
                errs.append(f"providers[{i}] '{provider_id}': external provider thiếu base_url")
            if not secret_ref:
                errs.append(f"providers[{i}] '{provider_id}': external provider thiếu secret_ref")
        if secret_ref and not _is_secret_ref_name(secret_ref):
            errs.append(
                f"providers[{i}] '{provider_id}': secret_ref phải là TÊN biến env "
                f"(A-Z0-9_), không được chứa giá trị secret"
            )
        providers[provider_id] = ProviderDef(
            provider_id=provider_id,
            kind=kind,
            base_url=base_url,
            secret_ref=secret_ref,
            egress=egress,
        )
    return providers


def _is_secret_ref_name(value: str) -> bool:
    # Tên biến env hợp lệ: chặn vô tình nhúng giá trị secret thật vào registry.
    return value.replace("_", "").isalnum() and value.isupper() and len(value) <= 128


def _parse_models(
    cfg: Mapping[str, Any],
    providers: Mapping[str, ProviderDef],
    errs: list[str],
) -> dict[str, ModelDef]:
    models: dict[str, ModelDef] = {}
    for i, raw in enumerate(_require_list(cfg, "models", errs)):
        if not isinstance(raw, dict):
            errs.append(f"models[{i}]: phải là mapping")
            continue
        model_id = str(raw.get("model_id") or "").strip()
        if not model_id:
            errs.append(f"models[{i}]: thiếu model_id")
            continue
        if model_id in models:
            errs.append(f"models[{i}]: model_id '{model_id}' bị trùng")
            continue
        provider_id = str(raw.get("provider_id") or "").strip()
        if provider_id not in providers:
            errs.append(
                f"models[{i}] '{model_id}': provider_id '{provider_id}' không tồn tại trong providers"
            )
            continue
        roles_raw = raw.get("roles") or []
        roles = tuple(str(r).strip() for r in roles_raw if str(r).strip())
        if not roles:
            errs.append(f"models[{i}] '{model_id}': thiếu roles")
            continue
        limits_raw = raw.get("limits")
        if limits_raw is None:
            limits: Mapping[str, Any] = _empty_mapping()
        elif isinstance(limits_raw, dict):
            limits = _mapping(limits_raw)
        else:
            errs.append(f"models[{i}] '{model_id}': limits phải là mapping")
            continue
        models[model_id] = ModelDef(
            model_id=model_id, provider_id=provider_id, roles=roles, limits=limits
        )
    return models


def _parse_tasks(
    cfg: Mapping[str, Any],
    models: Mapping[str, ModelDef],
    errs: list[str],
) -> dict[str, TaskDef]:
    tasks: dict[str, TaskDef] = {}
    for i, raw in enumerate(_require_list(cfg, "tasks", errs)):
        if not isinstance(raw, dict):
            errs.append(f"tasks[{i}]: phải là mapping")
            continue
        task = str(raw.get("task") or "").strip()
        if not task:
            errs.append(f"tasks[{i}]: thiếu task")
            continue
        if task in tasks:
            errs.append(f"tasks[{i}]: task '{task}' bị trùng")
            continue
        champion = str(raw.get("champion") or "").strip()
        fallbacks = tuple(
            str(f).strip() for f in (raw.get("fallbacks") or []) if str(f).strip()
        )
        if not champion and not fallbacks:
            errs.append(f"tasks[{i}] '{task}': phải khai champion hoặc fallback")
        if champion and champion not in models:
            errs.append(f"tasks[{i}] '{task}': champion '{champion}' không tồn tại trong models")
        for fb in fallbacks:
            if fb not in models:
                errs.append(f"tasks[{i}] '{task}': fallback '{fb}' không tồn tại trong models")
        output_required = tuple(
            str(k).strip() for k in (raw.get("output_required") or []) if str(k).strip()
        )
        data_classes = tuple(
            str(d).strip() for d in (raw.get("data_classes") or ["public"]) if str(d).strip()
        ) or ("public",)
        independence = tuple(
            str(t).strip() for t in (raw.get("independence", {}).get("must_differ_from") or [])
            if str(t).strip()
        ) if isinstance(raw.get("independence"), dict) else ()
        tasks[task] = TaskDef(
            task=task,
            role=str(raw.get("role") or "").strip() or task,
            champion=champion,
            fallbacks=fallbacks,
            output_required=output_required,
            data_classes=data_classes,
            independence=independence,
        )
    return tasks


def build_registry(cfg: Mapping[str, Any]) -> BrainRegistry:
    """Parse + validate cấu trúc registry. Fail-closed bằng BrainConfigError."""
    if not isinstance(cfg, Mapping):
        raise BrainConfigError("brain registry phải là mapping ở mức top-level")
    errs: list[str] = []

    version = cfg.get("version")
    if version is None or str(version).strip() == "":
        errs.append("version: bắt buộc phải có")

    providers = _parse_providers(cfg, errs)
    models = _parse_models(cfg, providers, errs)
    tasks = _parse_tasks(cfg, models, errs)

    if errs:
        raise BrainConfigError("brain registry không hợp lệ: " + "; ".join(errs))

    return BrainRegistry(
        version=str(version),
        providers=_mapping(providers),
        models=_mapping(models),
        tasks=_mapping(tasks),
    )
