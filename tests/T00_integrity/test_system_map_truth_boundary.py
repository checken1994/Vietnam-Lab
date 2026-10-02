from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCANNER = ROOT / "dashboard" / "src" / "lib" / "system-map.ts"
ROUTE = ROOT / "dashboard" / "src" / "app" / "api" / "system-map" / "route.ts"
UI = ROOT / "dashboard" / "src" / "components" / "system-map" / "system-map.tsx"
PAGE = ROOT / "dashboard" / "src" / "app" / "system-map" / "page.tsx"


def test_system_map_files_exist():
    for path in (SCANNER, ROUTE, UI, PAGE):
        assert path.is_file(), f"missing System Map file: {path.relative_to(ROOT)}"


def test_system_map_v1_is_explicitly_static_not_runtime_truth():
    scanner = SCANNER.read_text(encoding="utf-8")
    ui = UI.read_text(encoding="utf-8")

    assert 'evidenceMode: "SOURCE_SCAN"' in scanner
    assert 'evidenceLevel: "STATIC"' in scanner
    assert "V1 only proves static source/import relationships" in scanner
    assert "it does not claim runtime execution" in scanner

    assert "Evidence: STATIC" in ui
    assert "Runtime overlay:" in ui
    assert "NOT CONNECTED" in ui
    assert "Không suy đoán trạng thái thay thế" in ui


def test_system_map_api_fails_closed_without_source_graph():
    route = ROUTE.read_text(encoding="utf-8")
    assert 'status: "UNKNOWN"' in route
    assert 'nodes: []' in route
    assert 'edges: []' in route
    assert "status: 503" in route
    assert '"Cache-Control": "no-store"' in route


def test_system_map_scanner_avoids_runtime_and_large_artifact_trees():
    scanner = SCANNER.read_text(encoding="utf-8")
    for excluded in ('"data"', '"reports"', '"node_modules"', '".next"', '"__pycache__"'):
        assert excluded in scanner

    assert "MAX_FILES" in scanner
    assert "MAX_EDGES" in scanner
