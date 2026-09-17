"""
scp/core/config.py
==================
Typed Settings for SCP Agent OS using pydantic-settings.
Provides centralized, validated, type-safe settings with .env support.

Boot integration (Q12): ``validate_boot_settings`` is the bridge from
``scp.core.config_contract.validate_boot_config`` — the contract stays the
single fail-closed authority for required secrets; this module adds the typed
validation view of SCP_* environment values without driving runtime behavior,
so the two layers cannot silently diverge.
"""
from __future__ import annotations

import functools
import os
from typing import List

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class SCPSettings(BaseSettings):
    """Canonical typed configuration for SCP Agent OS."""

    model_config = SettingsConfigDict(
        env_prefix="SCP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Core Server & Environment
    mode: str = Field(default="development", description="Runtime mode (development/production/test)")
    production_mode: bool = Field(default=False, description="Strict production hardening")
    host: str = Field(default="127.0.0.1", description="Server bind host")
    port: int = Field(default=8000, description="Server bind port")
    data_dir: str = Field(default="data", description="Data directory path")
    cors_origins: str = Field(default="", description="Comma-separated allowed CORS origins")

    # Security & Authentication
    jwt_secret: str = Field(default="", description="JWT signing secret (SCP_JWT_SECRET)")
    admin_key: str = Field(default="", description="Admin key for /auth/token (SCP_ADMIN_KEY)")
    auth_password: str = Field(default="", description="Static admin auth password (SCP_AUTH_PASSWORD)")
    auth_token_secret: str = Field(default="", description="Static admin auth token (SCP_AUTH_TOKEN_SECRET)")

    # LLM Gateway & Network Egress
    egress_mode: str = Field(default="deny", description="Egress mode: deny/allowlist/unrestricted")
    llm_egress_allowlist: str = Field(default="", description="Allowed outbound LLM domains")
    llm_cost_mode: str = Field(default="standard", description="Cost mode: free_only or standard")
    llm_hedge: str = Field(default="on", description="Enable hedged LLM racing")
    llm_attempt_timeout_seconds: float = Field(default=10.0, description="Hedge attempt deadline")
    llm_hedge_max_seconds: float = Field(default=90.0, description="Total hedge ceiling")

    # Observability & Logging
    log_level: str = Field(default="INFO", description="Log level: DEBUG/INFO/WARNING/ERROR")
    log_json: bool = Field(default=False, description="Emit structured JSON logs")
    otel_enabled: bool = Field(default=False, description="Enable OpenTelemetry tracing")

    # Cognitive & Security Gates
    why_llm_enabled: bool = Field(default=False, description="Enable LLM analysis in WHY gate")
    sandbox_strict: bool = Field(default=True, description="Enforce strict sandbox mode")

    @property
    def is_production(self) -> bool:
        return bool(self.production_mode or self.mode.lower() == "production")

    @property
    def parsed_cors_origins(self) -> List[str]:
        if not self.cors_origins:
            return ["http://localhost:8000", "http://127.0.0.1:8000"]
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@functools.lru_cache(maxsize=1)
def get_settings() -> SCPSettings:
    """Return the cached singleton SCPSettings instance."""
    return SCPSettings()


def _settings_env_keys() -> List[str]:
    """Env variable names consumed by SCPSettings (SCP_ + upper(field name))."""
    return [f"SCP_{field.upper()}" for field in SCPSettings.model_fields]


def boot_settings_for_validation() -> SCPSettings:
    """Build the typed boot view with the legacy empty-string tolerance.

    Legacy readers consume env via ``os.environ.get(x, "").strip()``, so an
    empty value means "unset/default" everywhere in the current codebase.
    pydantic-settings instead rejects "" for bool/int/float fields; without
    this normalization the typed gate could abort a boot the contract happily
    accepts (behavior change). compose.yml legitimately renders empty strings
    for ``${VAR:-}`` interpolations, so this is a real production path, not a
    hypothetical. Values are never logged.
    """
    normalized: dict[str, str] = {}
    for key in _settings_env_keys():
        if os.environ.get(key) == "":
            normalized[key] = os.environ.pop(key)
    try:
        return SCPSettings()
    finally:
        for key, value in normalized.items():
            os.environ[key] = value


def validate_boot_settings(validated: dict[str, str]) -> SCPSettings:
    """Typed boot gate bridged to ``config_contract.validate_boot_config``.

    Call AFTER ``validate_boot_config()`` with its return dict. Contract
    remains the authority for required secrets and security rules; this layer
    (1) fail-fast validates that every typed SCP_* environment value parses to
    its declared type, and (2) cross-checks that the typed view resolves the
    same required secret values the contract validated, so a future rename or
    prefix change in either layer aborts boot instead of diverging silently.

    Raises:
        ConfigContractError: on type violation or typed/contract divergence.
            Only field names are reported — never env values (secrets).
    """
    from scp.core.config_contract import ConfigContractError

    try:
        settings = boot_settings_for_validation()
    except ValidationError as exc:
        bad_fields = sorted(
            {
                "SCP_" + str(loc[0]).upper()
                for loc in (err.get("loc") or () for err in exc.errors())
                if loc
            }
        )
        raise ConfigContractError(
            "\n\n[SCP typed config] Giá trị biến môi trường SCP_ vi phạm kiểu đã khai báo:\n"
            + "\n".join(f"  - {name}" for name in bad_fields)
            + "\n  → Sửa giá trị hợp lệ hoặc bỏ biến để dùng mặc định. (giá trị không được in ra log)\n"
        ) from None

    mismatches: list[str] = []
    for env_name, contract_value in validated.items():
        field = env_name[len("SCP_"):].lower() if env_name.startswith("SCP_") else env_name
        typed_value = getattr(settings, field, None)
        if typed_value is None:
            mismatches.append(f"{env_name} (thiếu field typed tương ứng trong SCPSettings)")
            continue
        if str(typed_value).strip() != str(contract_value).strip():
            mismatches.append(f"{env_name} (typed view khác giá trị contract đã validate)")
    if mismatches:
        raise ConfigContractError(
            "\n\n[SCP typed config] Mâu thuẫn giữa SCPSettings và config_contract — "
            "hai lớp cấu hình boot phải đồng nhất:\n"
            + "\n".join(f"  - {item}" for item in mismatches)
            + "\n  → Không được in giá trị secret ra log.\n"
        )

    return settings
