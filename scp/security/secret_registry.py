"""Central secret registry for SCP — trả nợ HC-07/HC-08 (K2-W2).

HC-07: 11 loại token/secret riêng biệt, không nơi nào khai báo vai trò/chủ sở
hữu → bảng khai báo tĩnh ``SECRET_PURPOSES`` (tên → purpose, consumer, hỗ trợ
``*_FILE``, min length). Không hợp nhất giá trị — mỗi secret giữ vai trò mật
mã riêng.

HC-08: secret cấp phát qua env HOẶC ``{name}_FILE`` (Docker/K8s secret mount).
``resolve()`` chuẩn hóa precedence:

  1. ``{name}_FILE`` được set (khác rỗng) → đọc file. FILE THẮNG.
  2. Không có file → đọc env ``name``.

Precedence FILE-wins (pinned, lý do):
  ``*_FILE`` là kênh cấp phát do orchestrator kiểm soát (docker secrets/K8s
  mounted secret). Khi orchestrator chủ động mount file, đó là ý định hiện tại
  của operator; giá trị env cùng lúc chỉ là residue từ shell/bootstrap (vd.
  default của compose.test.yml). Do đó file được set → file phải thắng.

Fail-closed (pinned, lý do):
  ``{name}_FILE`` được set NHƯNG file thiếu/không đọc được/rỗng → trả ``None``
  + log ERROR (tên var + loại lỗi, KHÔNG BAO GIỜ in giá trị). KHÔNG fallback
  về env: fallback âm thầm sẽ là fail-open — operator tin rằng kênh file đang
  hiệu lực trong khi env residue bị dùng ngược lại.

Không cache giá trị: mỗi lần ``resolve()`` đọc lại env/file để hành vi phản
ánh trạng thái hiện tại (secret có thể bị rotate/xóa giữa chừng).

Tính nhất quán với các loader hiện có (không thay trong đợt này):
  - ``scp/security/auth_config.py`` có precedence riêng NGHIÊM hơn: file wins
    nhưng env cùng set và khác giá trị → raise ``_FILE_CONFLICT``. Giữ nguyên.
  - ``scp/security/secret_loader.read_secret`` (production_guard, auth.py):
    file wins, lỗi file raise. Giữ nguyên; migrate dần theo wave sau.
  - ``scp/core/config_contract.MIN_LENGTH`` vẫn là nơi enforce min length tại
    boot cho SCP_JWT_SECRET/SCP_ADMIN_KEY — [HC-07 2026-10-23] từ nay boot gate
    đọc presence qua ``resolve()`` (env HOẶC *_FILE) thay vì env trực tiếp,
    min-length/forbidden check chạy trên giá trị đã resolve; ``min_length``
    ở đây vẫn chỉ là metadata khai báo, chưa enforce tại registry.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("scp.security.secret_registry")

_FILE_SUFFIX = "_FILE"


@dataclass(frozen=True)
class SecretSpec:
    """Khai báo tĩnh cho một secret của SCP."""

    name: str
    purpose: str
    consumers: tuple[str, ...]
    # HC-08: True nếu biến {name}_FILE được công nhận là kênh cấp phát thay thế.
    file_variant: bool = False
    # Metadata khai báo (chars). None = chưa có constraint ràng buộc tại boot.
    # Enforcement hiện tại nằm ở scp/core/config_contract.MIN_LENGTH.
    min_length: int | None = None


SECRET_PURPOSES: dict[str, SecretSpec] = {
    "SCP_ADMIN_KEY": SecretSpec(
        name="SCP_ADMIN_KEY",
        purpose="Admin key/password cấp admin token (POST /auth-token) và gate mint JWT admin",
        consumers=(
            "scp.security.auth_config",
            "scp.api_server",
            "scp.runtime_audit.runner",
            "scp.api.routes.hands_routes",
        ),
        file_variant=True,
        min_length=8,
    ),
    "SCP_AUTH_TOKEN_SECRET": SecretSpec(
        name="SCP_AUTH_TOKEN_SECRET",
        purpose="Shared secret xác thực admin/benchmark token (HS256 bearer)",
        consumers=(
            "scp.security.auth_config",
            "scp.security.auth",
            "scp.benchmark.run_benchmark",
            "scp.benchmark.run_benchmark_v2",
        ),
        file_variant=True,
    ),
    "SCP_BRAIN_ADMIN_TOKEN": SecretSpec(
        name="SCP_BRAIN_ADMIN_TOKEN",
        purpose="Admin token cho brain-console mini-service (Node.js)",
        consumers=("mini-services/brain-console/index.js",),
        file_variant=False,
    ),
    "SCP_CAPABILITY_SECRET": SecretSpec(
        name="SCP_CAPABILITY_SECRET",
        purpose="HMAC signing cho capability token + TaskKernel journal hash chain (GAP-09, fail-closed)",
        consumers=(
            "scp.core.capability_token",
            "scp.core.verifier_receipt",
            "scp.core.autonomous_cycle",
            "scp.task_kernel_parts.definitions",
            "scp.data_sources.auto_web_ingestor",
            "scp.core.doubt_cron",
        ),
        file_variant=False,
    ),
    "SCP_DASHBOARD_PROXY_SECRET": SecretSpec(
        name="SCP_DASHBOARD_PROXY_SECRET",
        purpose="Shared secret giữa reverse proxy (gateway.js/Caddy) và Next.js dashboard middleware",
        consumers=(
            "dashboard/src/middleware.ts",
            "dashboard/src/lib/scp-service-auth.ts",
            "dashboard/gateway.js",
        ),
        file_variant=False,
    ),
    "SCP_ENCRYPTION_KEY": SecretSpec(
        name="SCP_ENCRYPTION_KEY",
        purpose="Khóa AES-256 mã hóa state nhạy cảm at-rest (bypass_encrypt)",
        consumers=("scp.security.bypass_encrypt",),
        file_variant=False,
    ),
    "SCP_INTERNAL_SECRET": SecretSpec(
        name="SCP_INTERNAL_SECRET",
        purpose="HMAC shared secret giữa các dịch vụ nội bộ (agent routes)",
        consumers=("scp.api.routes.agent_routes",),
        file_variant=False,
    ),
    "SCP_JWT_SECRET": SecretSpec(
        name="SCP_JWT_SECRET",
        purpose="Khóa ký JWT xác thực API (HS256), required tại boot",
        consumers=(
            "scp.security.jwt_guard",
            "scp.core.config_contract",
        ),
        file_variant=False,
        min_length=32,
    ),
    "SCP_PC_CONTROLLER_TOKEN": SecretSpec(
        name="SCP_PC_CONTROLLER_TOKEN",
        purpose="Bearer token điều khiển PC Controller / web control / MCP transport (deny-by-default)",
        consumers=(
            "scp.api.routes.pc_controller_routes",
            "scp.api.routes.web_control_routes",
            "scp.api.routes.hands_routes",
            "scp.api.routes.call_routes",
            "scp.api.routes.agent_routes",
            "scp.middleware.runtime_skill_guard",
            "scp.mcp_server.server",
        ),
        file_variant=False,
    ),
    "SCP_SCHEDULER_ADMIN_TOKEN": SecretSpec(
        name="SCP_SCHEDULER_ADMIN_TOKEN",
        purpose="Admin token cho scheduler loop endpoints; compose fail-closed khi thiếu",
        consumers=(
            "scp.security.auth_config",
            "scp.runtime_audit.runner",
        ),
        file_variant=True,
    ),
    "SCP_VERIFIER_SECRET": SecretSpec(
        name="SCP_VERIFIER_SECRET",
        purpose="Secret ký verifier receipt (FA-04 / GAP-03/R3); fallback SCP_CAPABILITY_SECRET",
        consumers=("scp.core.verifier_receipt",),
        file_variant=False,
    ),
}


def resolve(name: str) -> str | None:
    """Đọc một secret đã khai báo theo precedence chuẩn HC-08.

    Returns:
        Giá trị secret đã strip, hoặc ``None`` nếu thiếu/không đọc được
        (fail-closed, kèm log ERROR — không bao giờ log giá trị).

    Raises:
        ValueError: nếu ``name`` chưa được khai báo trong ``SECRET_PURPOSES``
            (registry là nguồn sự thật; dùng secret ngoài registry là lỗi lập
            trình, phải khai báo trước).
    """
    spec = SECRET_PURPOSES.get(name)
    if spec is None:
        raise ValueError(
            f"[secret_registry] Unknown secret {name!r}: "
            "declare it in scp.security.secret_registry.SECRET_PURPOSES first"
        )
    file_env = f"{name}{_FILE_SUFFIX}"
    raw_file_ref = os.environ.get(file_env)
    file_ref = (raw_file_ref or "").strip()
    if file_ref:
        if not spec.file_variant:
            logger.error(
                "[secret_registry] %s is set but %s does not declare a _FILE "
                "variant; ignoring file channel (declared convention HC-08)",
                file_env,
                name,
            )
        else:
            path = Path(file_ref).expanduser()
            try:
                value = path.read_text(encoding="utf-8-sig").strip()
            except (OSError, UnicodeDecodeError) as exc:
                logger.error(
                    "[secret_registry] %s=%s unreadable (%s): fail-closed, "
                    "no env fallback, value never logged",
                    file_env,
                    file_ref,
                    type(exc).__name__,
                )
                return None
            if not value:
                logger.error(
                    "[secret_registry] %s points to an empty file: fail-closed",
                    file_env,
                )
                return None
            return value
    elif raw_file_ref:
        logger.warning(
            "[secret_registry] %s is set but blank; treated as unset", file_env
        )
    value = os.environ.get(name, "").strip()
    return value or None


def audit_missing(required: Iterable[str]) -> list[str]:
    """Boot gate: trả về danh sách secret bắt buộc đang thiếu (theo thứ tự vào).

    Một secret được tính "có" khi ``resolve()`` trả giá trị (env hoặc file).
    Tên chưa khai báo trong registry → ValueError (fail-closed, cùng hợp đồng
    với ``resolve``).
    """
    missing: list[str] = []
    for name in required:
        if resolve(name) is None:
            missing.append(name)
    return missing


__all__ = ["SECRET_PURPOSES", "SecretSpec", "audit_missing", "resolve"]
