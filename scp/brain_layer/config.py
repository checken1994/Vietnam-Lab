"""Brain Layer P0 — nạp + validate brain registry (fail-closed, §3.1).

`BrainConfig.load(registry_path)` nạp YAML/JSON, validate schema + policy
(independence judge ≠ generate), tính `config_hash` = sha256 nội dung chuẩn
hóa (sort_keys). Registry chỉ chứa secret_ref (TÊN), không bao giờ chứa giá
trị secret — hash do đó không gồm secret.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from scp.brain_layer.errors import BrainConfigError
from scp.brain_layer.registry import BrainRegistry, build_registry

# Key tuyệt đối không được xuất hiện (kèm giá trị) trong registry — chỉ nhận
# secret qua secret_ref. Chặn placebo "dán luôn API key vào YAML".
_FORBIDDEN_SECRET_KEYS: frozenset[str] = frozenset(
    {"api_key", "apikey", "secret", "secret_value", "token", "password", "credential"}
)

TASK_JUDGE = "judge"


def canonical_hash(raw: Mapping[str, Any]) -> str:
    """sha256 của nội dung chuẩn hóa (sort_keys) — ổn định, không gồm secret."""
    payload = json.dumps(
        raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _assert_no_secret_values(node: Any, path: str = "$") -> None:
    if isinstance(node, Mapping):
        for key, value in node.items():
            key_path = f"{path}.{key}"
            if (
                isinstance(key, str)
                and key.strip().lower() in _FORBIDDEN_SECRET_KEYS
                and value not in (None, "")
            ):
                raise BrainConfigError(
                    f"{key_path}: registry chỉ được chứa secret_ref (tên biến env), "
                    f"không được chứa giá trị secret"
                )
            _assert_no_secret_values(value, key_path)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            _assert_no_secret_values(item, f"{path}[{i}]")


def _load_raw(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BrainConfigError(f"không đọc được brain registry '{path}': {exc}") from exc
    if path.suffix.lower() == ".json":
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise BrainConfigError(f"brain registry JSON lỗi: {exc}") from exc
    # PyYAML đã là dependency runtime pinned (scp/requirements.txt: PyYAML==6.0.3).
    try:
        import yaml  # noqa: PLC0415 — import cục bộ, giữ package import nhẹ
    except ImportError as exc:  # pragma: no cover - môi trường chuẩn luôn có
        raise BrainConfigError("PyYAML không khả dụng để nạp brain registry") from exc
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise BrainConfigError(f"brain registry YAML lỗi: {exc}") from exc


@dataclass(frozen=True)
class BrainConfig:
    """Kết quả nạp registry: registry bất biến + config_hash + warnings."""

    registry: BrainRegistry
    config_hash: str
    source_path: str
    warnings: tuple[str, ...] = ()

    @classmethod
    def load(cls, registry_path: str | Path) -> "BrainConfig":
        path = Path(registry_path)
        raw = _load_raw(path)
        if not isinstance(raw, Mapping):
            raise BrainConfigError("brain registry phải là mapping ở mức top-level")
        _assert_no_secret_values(raw)

        if raw.get("version") in (None, ""):
            raise BrainConfigError("brain registry thiếu 'version' — fail-closed")

        registry = build_registry(raw)

        warnings: list[str] = []
        judge = registry.tasks.get(TASK_JUDGE)
        if judge is not None and judge.independence:
            for other_task in judge.independence:
                other = registry.tasks.get(other_task)
                if other is None:
                    raise BrainConfigError(
                        f"task '{TASK_JUDGE}': independence.must_differ_from trỏ tới "
                        f"task không tồn tại '{other_task}'"
                    )
                if judge.champion and other.champion and judge.champion == other.champion:
                    raise BrainConfigError(
                        f"task '{TASK_JUDGE}': champion '{judge.champion}' trùng champion "
                        f"của '{other_task}' — vi phạm independence.must_differ_from"
                        f" (fail-closed §3.1)"
                    )
        elif judge is not None:
            warnings.append(
                "task 'judge' chưa khai independence.must_differ_from — "
                "bắt buộc bổ sung trước khi bật mode live"
            )

        return cls(
            registry=registry,
            config_hash=canonical_hash(raw),
            source_path=str(path),
            warnings=tuple(warnings),
        )
