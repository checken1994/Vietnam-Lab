"""[SEC-FIX 2026-10-27 · F2 audit af88fd31] Compose pass-through SCP_PRODUCTION_MODE.

Finding F2 (audit bảo mật af88fd31, CHECKER chứng minh): docker-compose.full.yml
map ``SCP_DOCKER_PRODUCTION_MODE:-0`` — biến KHÔNG tồn tại trong .env operator
(operator đặt ``SCP_PRODUCTION_MODE=1``) ⇒ stack Docker local lặng lẽ chạy
posture dev (production guard tắt, /docs mở, redirect tắt) trong khi comment
compose hướng dẫn "Trên VPS: đặt SCP_PRODUCTION_MODE=1 trong .env" — hướng dẫn
đó không bao giờ tới được container.

Fix được pin ở đây: mapping pass-through ``${SCP_PRODUCTION_MODE:-1}``
(fail-closed: KHÔNG có .env ⇒ posture production — an toàn hơn).

Anti-placebo: nếu ai đổi NGƯỢC mapping (về biến Docker-only cũ, hay hạ default
về 0) thì test dưới đây ĐỎ — không thể lặng lẽ quay lại posture dev.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.full.yml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"


def _load_compose() -> dict:
    import yaml

    raw = COMPOSE_FILE.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    assert isinstance(data, dict), "docker-compose.full.yml phải parse thành dict YAML"
    return data


def test_backend_maps_operator_production_mode_passthrough():
    """Pin mapping F2: pass-through biến operator, default fail-closed = 1."""
    compose = _load_compose()
    backend_env = compose["services"]["backend"]["environment"]
    assert "SCP_PRODUCTION_MODE" in backend_env, (
        "compose backend phải map SCP_PRODUCTION_MODE vào container"
    )
    raw_value = backend_env["SCP_PRODUCTION_MODE"]
    assert raw_value == "${SCP_PRODUCTION_MODE:-1}", (
        f"F2 regression: mapping SCP_PRODUCTION_MODE thành {raw_value!r} — "
        "phải giữ pass-through '${SCP_PRODUCTION_MODE:-1}' (fail-closed, "
        "đúng giá trị .env operator)"
    )


def test_compose_no_longer_maps_docker_only_production_var():
    """Biến Docker-only SCP_DOCKER_PRODUCTION_MODE không được còn là mapping
    ACTIVE trong bất kỳ service nào — nếu còn, cấu hình .env operator lại bị
    lặng lẽ bỏ qua. Kiểm trên YAML đã parse (chỉ cấu hình thật; comment lịch
    sử ghi lại finding F2 là hợp lệ)."""
    compose = _load_compose()
    for service_name, service in compose["services"].items():
        env = (service or {}).get("environment") or {}
        for key, value in env.items():
            assert "SCP_DOCKER_PRODUCTION_MODE" not in str(key), (service_name, key)
            assert "SCP_DOCKER_PRODUCTION_MODE" not in str(value), (
                service_name, key, value,
            )


def test_env_example_documents_operator_production_mode():
    """Biến operator phải được document trong .env.example — pass-through chỉ
    có ý nghĩa khi operator biết key điều khiển posture."""
    raw = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "SCP_PRODUCTION_MODE=" in raw, (
        ".env.example phải khai báo SCP_PRODUCTION_MODE"
    )


def test_compose_still_parses_with_five_services():
    """Sanity sau fix: compose vẫn parse và giữ đúng topology 5 services
    (fix F2 chỉ đổi 1 mapping + comment, không được đụng topology)."""
    compose = _load_compose()
    assert set(compose["services"].keys()) == {
        "backend",
        "agent-desktop",
        "loop-scheduler",
        "brain-console",
        "dashboard",
    }
