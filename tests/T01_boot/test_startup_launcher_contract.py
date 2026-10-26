"""Launcher contract tests — updated for API-only era (no Ollama, port 8000)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_START_SHIM = (ROOT / "start-scp.bat").read_text(encoding="utf-8")
LAUNCHER = (
    _START_SHIM + "\n" + (ROOT / "launch.bat").read_text(encoding="utf-8")
    if "launch.bat" in _START_SHIM
    else _START_SHIM
)


def test_manual_launcher_preflights_dashboard_dependency_and_build():
    assert 'dashboard\\node_modules\\next\\dist\\bin\\next' in LAUNCHER
    assert 'npm install' in LAUNCHER
    assert 'start "SCP-Dashboard" cmd /k "cd /d %~dp0dashboard && set SCP_INTERNAL_URL=http://127.0.0.1:8000' in LAUNCHER
    assert 'npm run start"' in LAUNCHER
    assert 'npm run build' in LAUNCHER
    assert not re.search(r'\bbun(\.exe)?\b', LAUNCHER, re.IGNORECASE), "FAIL: launcher must not use bun"


def test_launcher_is_api_only_no_ollama():
    """[MẢNH 12 — Map vs Territory] Launcher phải khớp code: không Ollama, không fake bridge 8081, port 8000."""
    assert '11434' not in LAUNCHER, "FAIL: launcher vẫn tham chiếu Ollama port 11434"
    assert 'SCP-LLM-Bridge' not in LAUNCHER, "FAIL: launcher không được chứa SCP-LLM-Bridge (fake bridge đã bị phế truất)"
    assert '8081' not in LAUNCHER, "FAIL: launcher không được cấu hình port 8081"
    assert '8002' not in LAUNCHER, "FAIL: launcher vẫn dùng port cũ 8002"
    assert 'set SCP_INTERNAL_URL=http://127.0.0.1:8000' in LAUNCHER
    assert 'set LOOP_SCHEDULER_URL=http://127.0.0.1:3030' in LAUNCHER
    assert 'node index.js' in LAUNCHER, "FAIL: loop-scheduler phải khởi chạy bằng node index.js"


def test_stop_launcher_never_kills_external_ollama_port():
    _stop_shim = (ROOT / "stop-scp.bat").read_text(encoding="utf-8")
    stop = (
        _stop_shim + "\n" + (ROOT / "launch.bat").read_text(encoding="utf-8")
        if "launch.bat" in _stop_shim
        else _stop_shim
    )
    assert 'findstr ":11434 "' not in stop
    assert 'findstr ":8081 "' not in stop, "FAIL: stop launcher không được kill port 8081"
    kill_lines = [line.lower() for line in stop.splitlines() if 'taskkill' in line.lower()]
    assert all('11434' not in line for line in kill_lines)
    assert all('8081' not in line for line in kill_lines)
    assert 'taskkill' in stop.lower()


def test_launch_bat_has_crlf_line_endings_and_executes_help():
    """Verify launch.bat has Windows CRLF line endings and executes help cleanly without hang or error."""
    import subprocess

    launch_bat_path = ROOT / "launch.bat"
    raw_bytes = launch_bat_path.read_bytes()
    assert b"\r\n" in raw_bytes, "launch.bat must contain CRLF line endings"
    assert raw_bytes.count(b"\n") == raw_bytes.count(b"\r\n"), (
        "launch.bat must not contain bare LF line endings"
    )

    proc = subprocess.run(
        'cmd /c "launch.bat help"',
        cwd=str(ROOT),
        shell=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0, f"launch.bat help failed with return code {proc.returncode}: {proc.stderr}"
    assert "SCP UNIFIED LAUNCHER" in proc.stdout, "launch.bat help output must contain 'SCP UNIFIED LAUNCHER'"
