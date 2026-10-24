"""T00 integrity: SCP port registry drift guard (HC-26, Wave K4, 2026-10-24).

Ba nguồn bắt buộc khớp nhau (anti-placebo: sửa 1 nguồn thiếu 1 -> test văng):

1. ``docs/architecture/PORTS.md``            — bảng port -> service -> nguồn
2. ``scp/core/ports.py``                     — constants Python
3. ``docker-compose.full.yml``               — port map compose publishes

Nguồn phụ được pin thêm (registry accuracy):
- ``CHROME_DEBUG_PORT`` env trong agent-desktop (CDP 9222)
- ``docker-compose.observability.yml`` (OTLP gRPC 4317 / HTTP 4318)
- ``scripts/run_full_audit.py`` AUDIT_PORT (8055)

Lịch sử: LLM Bridge (8081/11434, ``mini-services/llm-bridge/``) đã bị xóa khỏi
kiến trúc 2026-10-24 (Zero-Bun deprecation) — constant ``LLM_BRIDGE`` và test
pin ``core.ts`` được gỡ cùng thay đổi này; tái sử dụng port 8081/11434 cho
service mới cần cập nhật lại registry cả 3 nguồn.

Scope (DNA #16): test này chỉ chứng minh 3 nguồn KHÔNG LỆCH nhau ở thời điểm
chạy; không chứng minh service thật đang lắng nghe đúng port (runtime audit
thuộc scp-runtime-audit skill, ngoài phạm vi T00 static).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PORTS_MD = REPO / "docs" / "architecture" / "PORTS.md"
PORTS_PY = REPO / "scp" / "core" / "ports.py"
COMPOSE_FULL = REPO / "docker-compose.full.yml"
COMPOSE_OBS = REPO / "docker-compose.observability.yml"
RUN_FULL_AUDIT = REPO / "scripts" / "run_full_audit.py"

# services publish trong compose.full và constant tương ứng bắt buộc khớp
COMPOSE_SERVICE_TO_CONSTANT = {
    "backend": "BACKEND",
    "dashboard": "DASHBOARD",
    "loop-scheduler": "LOOP_SCHEDULER",
    "agent-desktop": "AGENT_DESKTOP",
    "brain-console": "BRAIN_CONSOLE",
}


def _read(path: Path) -> str:
    assert path.is_file(), (
        f"Thiếu file bắt buộc: {path} — port registry là checklist kiến trúc, "
        "không được xóa để 'gọn repo' (anti-placebo)."
    )
    return path.read_text(encoding="utf-8")


def _ports_py_constants() -> dict[str, int]:
    """Parse constants INT từ scp/core/ports.py (regex, không import)."""
    text = _read(PORTS_PY)
    found = {
        name: int(value)
        for name, value in re.findall(
            r"^([A-Z][A-Z0-9_]+)\s*[:=]\s*(\d{2,5})\s*$", text, re.MULTILINE
        )
    }
    required = {
        "BACKEND",
        "DASHBOARD",
        "LOOP_SCHEDULER",
        "AGENT_DESKTOP",
        "CDP",
        "BRAIN_CONSOLE",
    }
    missing = sorted(required - set(found))
    assert not missing, f"scp/core/ports.py thiếu constants: {missing}"
    return found


def _ports_md_rows() -> dict[int, str]:
    """Parse bảng port trong PORTS.md -> {port: service}."""
    text = _read(PORTS_MD)
    rows: dict[int, str] = {}
    for line in text.splitlines():
        match = re.match(r"^\|\s*(\d{2,5})\s*\|\s*([^|]+?)\s*\|", line)
        if match:
            rows[int(match.group(1))] = match.group(2)
    assert rows, "PORTS.md không parse được dòng nào — bảng port bị xóa/sửa format?"
    return rows


def _compose_full_published_ports() -> dict[str, set[int]]:
    """Parse docker-compose.full.yml -> {service: {container_port}}.

    Chỉ nhận port map dạng "127.0.0.1:<host>:<container>" và bắt buộc
    host-port == container-port (lệch 2 vế cũng là drift HC-26).
    """
    text = _read(COMPOSE_FULL)
    service = None
    published: dict[str, set[int]] = {}
    for line in text.splitlines():
        head = re.match(r"^  ([a-z0-9_-]+):\s*$", line)
        if head:
            service = head.group(1)
            continue
        if service is None:
            continue
        for host, container in re.findall(
            r"[\"']127\.0\.0\.1:(\d{2,5}):(\d{2,5})[\"']", line
        ):
            assert host == container, (
                f"docker-compose.full.yml: service '{service}' publish lệch vế "
                f"(host {host} != container {container}) — drift HC-26, phải "
                "giữ loopback same-port hoặc cập nhật cả 3 nguồn registry."
            )
            published.setdefault(service, set()).add(int(container))
    expected_services = set(COMPOSE_SERVICE_TO_CONSTANT)
    missing = sorted(expected_services - set(published))
    assert not missing, (
        f"docker-compose.full.yml không còn publish port cho service: {missing} "
        "(service bị xóa/đổi tên mà chưa cập nhật registry?)"
    )
    return published


def test_ports_py_matches_ports_md() -> None:
    constants = _ports_py_constants()
    md_rows = _ports_md_rows()
    for name, port in constants.items():
        assert port in md_rows, (
            f"port {port} ({name}) có trong scp/core/ports.py nhưng thiếu trong "
            "PORTS.md — sửa 1 nguồn thiếu 1 (anti-placebo)."
        )
    # Port trong compose-registry mapping phải đúng service như bảng dưới.
    compose_services = _compose_full_published_ports()
    for svc, const_name in COMPOSE_SERVICE_TO_CONSTANT.items():
        port = constants[const_name]
        assert port in compose_services[svc], (
            f"service '{svc}' (port {port} = {const_name}) không còn được "
            f"publish trong docker-compose.full.yml (publishes: "
            f"{sorted(compose_services[svc])}) — drift HC-26."
        )
        assert port in md_rows, f"port {port} ({svc}) thiếu trong PORTS.md"


def test_compose_ports_all_registered_in_md() -> None:
    """Mọi port compose.full publish phải xuất hiện trong PORTS.md."""
    md_rows = _ports_md_rows()
    published = _compose_full_published_ports()
    for svc, ports in published.items():
        for port in ports:
            assert port in md_rows, (
                f"docker-compose.full.yml publish port {port} (service '{svc}') "
                "nhưng PORTS.md không ghi — thêm vào registry cả 3 nguồn."
            )


def test_cdp_env_declared_in_compose() -> None:
    constants = _ports_py_constants()
    text = _read(COMPOSE_FULL)
    match = re.search(r"CHROME_DEBUG_PORT:\s*(\d{2,5})", text)
    assert match, (
        "docker-compose.full.yml mất env CHROME_DEBUG_PORT — nguồn khai báo "
        "CDP không còn (anti-placebo)."
    )
    assert int(match.group(1)) == constants["CDP"], (
        f"CHROME_DEBUG_PORT={match.group(1)} != ports.CDP={constants['CDP']} — "
        "drift HC-26 giữa compose và ports.py."
    )
    assert constants["CDP"] in _ports_md_rows(), (
        f"port CDP {constants['CDP']} thiếu trong PORTS.md"
    )


def test_observability_and_audit_ports_registered() -> None:
    """4318 (OTLP) và 8055 (audit hermetic) phải được ghi trong PORTS.md và
    đúng nguồn khai báo gốc."""
    md_rows = _ports_md_rows()
    obs = _read(COMPOSE_OBS)
    # Reality: compose observability publish CẢ OTLP gRPC (4317) lẫn OTLP
    # HTTP (4318) — cả hai đều phải same-port loopback và có trong PORTS.md.
    match_grpc = re.search(r"127\.0\.0\.1:(\d{2,5}):(\d{2,5})(?:\s*#\s*OTLP gRPC)?", obs)
    match_otlp = re.search(r"127\.0\.0\.1:4318:4318", obs)
    assert match_grpc and match_otlp, (
        "docker-compose.observability.yml mất port map OTLP gRPC (4317) hoặc "
        "OTLP HTTP (4318) — nguồn khai báo 4318 không còn (anti-placebo)."
    )
    assert match_grpc.group(1) == match_grpc.group(2) == "4317", (
        f"OTLP gRPC port map đổi thành {match_grpc.group(0)} — cập nhật "
        "PORTS.md cùng lúc (anti-placebo)."
    )
    assert 4318 in md_rows, "PORTS.md thiếu port 4318 (OTLP HTTP)"
    assert 4317 in md_rows, "PORTS.md thiếu port 4317 (OTLP gRPC)"
    # Ghi chú phạm vi (DNA #16): endpoint bên trong infra/otel-collector.yaml
    # không được mount vào audit container (lệnh T00 chỉ mount tests/scp/docs/
    # scripts/mini-services) nên chỉ pin được nguồn compose publish.

    audit_src = _read(RUN_FULL_AUDIT)
    match_audit = re.search(r"AUDIT_PORT\s*[:=]\s*(\d{2,5})", audit_src)
    assert match_audit, (
        "scripts/run_full_audit.py mất AUDIT_PORT — nguồn khai báo 8055 không còn."
    )
    assert match_audit.group(1) == "8055" and 8055 in md_rows, (
        "AUDIT_PORT đổi nhưng PORTS.md chưa cập nhật (anti-placebo)."
    )
