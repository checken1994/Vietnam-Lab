"""
Contract tests for Milestone M3: Terminal Video & Interactive Player Generator.

Validates:
1. demo_terminal_recording.cast (Asciinema v2 schema, timestamps, event structure)
2. demo_terminal_recording.html (Zero-CDN, offline player, controls, ANSI parsing)
3. demo_terminal_recording.svg (Pure CSS @keyframes animation, valid XML, viewBox)
4. CLI options & ARC-01 compliance (<= 800 LOC).
"""

import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET
import pytest

SCRIPT_PATH = Path("tools/showcase_benchmark/terminal_video_generator.py")
SHOWCASE_DIR = Path("reports/showcase")


def test_arc01_loc_limit():
    """Ensure terminal_video_generator.py adheres to ARC-01 (<= 800 LOC)."""
    assert SCRIPT_PATH.exists(), f"Missing script: {SCRIPT_PATH}"
    lines = SCRIPT_PATH.read_text(encoding="utf-8").splitlines()
    loc = len(lines)
    assert loc <= 800, f"ARC-01 violation: {SCRIPT_PATH} has {loc} LOC (limit 800)"


def test_generate_all_artifacts_via_cli(tmp_path):
    """Test generating all 3 artifacts via CLI to a custom directory."""
    cmd = [sys.executable, str(SCRIPT_PATH), "--output-dir", str(tmp_path)]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    assert result.returncode == 0
    assert (tmp_path / "demo_terminal_recording.cast").exists()
    assert (tmp_path / "demo_terminal_recording.html").exists()
    assert (tmp_path / "demo_terminal_recording.svg").exists()


def test_cast_format_and_schema():
    """Validate Asciinema v2 format in generated .cast file."""
    cast_path = SHOWCASE_DIR / "demo_terminal_recording.cast"
    assert cast_path.exists(), f"Missing cast file: {cast_path}"

    with open(cast_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    assert len(lines) >= 10, "Asciinema cast file should have multiple event lines"
    header = json.loads(lines[0])
    assert header.get("version") == 2, f"Invalid asciinema version: {header.get('version')}"
    assert header.get("width") == 110
    assert header.get("height") == 34
    assert "timestamp" in header

    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        event = json.loads(line)
        assert isinstance(event, list) and len(event) == 3, f"Invalid event line: {line}"
        ts, ev_type, data = event
        assert isinstance(ts, (int, float)) and ts >= 0.0
        assert ev_type == "o"
        assert isinstance(data, str) and len(data) > 0

    # Verify ~90s timeline duration (88.0s - 92.0s)
    last_event = json.loads(lines[-1].strip())
    assert 88.0 <= last_event[0] <= 92.0, f"Expected timeline duration ~90s, got {last_event[0]}s"


def test_html_player_zero_cdn_and_offline():
    """Validate HTML5 player has zero external CDN dependencies and correct UI."""
    html_path = SHOWCASE_DIR / "demo_terminal_recording.html"
    assert html_path.exists(), f"Missing html file: {html_path}"

    content = html_path.read_text(encoding="utf-8")

    # Anti-CDN check: zero external scripts or links
    assert "http://" not in content.lower(), "Found insecure http link in standalone HTML"
    assert "https://" not in content.lower(), "Found external CDN link in standalone HTML"

    # UI controls presence
    assert 'id="playBtn"' in content
    assert 'id="scrubber"' in content
    assert 'id="timeDisplay"' in content
    assert 'id="speedSelect"' in content
    assert 'id="terminalScreen"' in content

    # Verify speed select options: 1x, 1.5x, 2x, 5x, 10x
    assert 'value="1"' in content
    assert 'value="1.5"' in content
    assert 'value="2"' in content
    assert 'value="5"' in content
    assert 'value="10"' in content

    # Verify initial time display format: 00:00 / 01:30
    assert '01:30' in content

    # Self-contained event stream check
    assert "const events =" in content
    assert "parseAnsi" in content


def test_animated_svg_xml_and_keyframes():
    """Validate animated SVG has valid XML and pure CSS @keyframes."""
    svg_path = SHOWCASE_DIR / "demo_terminal_recording.svg"
    assert svg_path.exists(), f"Missing svg file: {svg_path}"

    content = svg_path.read_text(encoding="utf-8")
    root = ET.fromstring(content)
    assert root.tag.endswith("svg")
    assert "viewBox" in root.attrib

    # Keyframes check for CSS animation
    assert "@keyframes" in content
    assert "lineFade" in content
    assert "SCP-BENCH 500" in content


def test_cli_flags_filtering(tmp_path):
    """Test CLI flags --cast-only, --html-only, --svg-only."""
    # Cast only
    p_cast = tmp_path / "cast_test"
    cmd_cast = [sys.executable, str(SCRIPT_PATH), "--output-dir", str(p_cast), "--cast-only"]
    subprocess.run(cmd_cast, check=True)
    assert (p_cast / "demo_terminal_recording.cast").exists()
    assert not (p_cast / "demo_terminal_recording.html").exists()

    # HTML only
    p_html = tmp_path / "html_test"
    cmd_html = [sys.executable, str(SCRIPT_PATH), "--output-dir", str(p_html), "--html-only"]
    subprocess.run(cmd_html, check=True)
    assert (p_html / "demo_terminal_recording.html").exists()
    assert not (p_html / "demo_terminal_recording.svg").exists()

    # SVG only
    p_svg = tmp_path / "svg_test"
    cmd_svg = [sys.executable, str(SCRIPT_PATH), "--output-dir", str(p_svg), "--svg-only"]
    subprocess.run(cmd_svg, check=True)
    assert (p_svg / "demo_terminal_recording.svg").exists()
    assert not (p_svg / "demo_terminal_recording.cast").exists()
