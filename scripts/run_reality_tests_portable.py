from __future__ import annotations
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DIR = ROOT / "tests" / "reality-tests"
PYTHON = os.environ.get("SCP_PYTHON_BIN") or sys.executable
if PYTHON.startswith("/c/"):
    PYTHON = "C:" + PYTHON[2:].replace("/", "\\")
PYTHON_COMMAND = [PYTHON, "-X", "utf8"] if os.name == "nt" else [PYTHON]
results = []
env = os.environ.copy()
env.setdefault("PYTHONUTF8", "1")
env.setdefault("PYTHONIOENCODING", "utf-8")

# Isolated test data directory to ensure tests never contend with live background services
_TEST_DATA_DIR = ROOT / "data" / ".test_reality_data"
_TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)
_TEST_DB_PATH = _TEST_DATA_DIR / "v13.db"

env["SCP_DATA_DIR"] = str(_TEST_DATA_DIR)
env["SCP_DB_PATH"] = str(_TEST_DB_PATH)

# Reality tests must never inherit a production env file. Create one explicit
# child-safe file when the suite itself did not provide an override.
_TEST_ENV_FILE = ROOT / ".env.test"
_TEST_ENV_CREATED = False
if not _TEST_ENV_FILE.exists():
    _TEST_ENV_FILE.write_text(
        f"# generated isolated reality-test environment\n"
        f"SCP_DATA_DIR={_TEST_DATA_DIR}\n"
        f"SCP_DB_PATH={_TEST_DB_PATH}\n",
        encoding="utf-8",
    )
    _TEST_ENV_CREATED = True
else:
    _env_text = _TEST_ENV_FILE.read_text(encoding="utf-8")
    if "SCP_DATA_DIR=" not in _env_text:
        _TEST_ENV_FILE.write_text(
            _env_text.rstrip() + f"\nSCP_DATA_DIR={_TEST_DATA_DIR}\nSCP_DB_PATH={_TEST_DB_PATH}\n",
            encoding="utf-8",
        )
env["SCP_ENV_FILE"] = str(_TEST_ENV_FILE)
env["SCP_SIDECAR_ENV_FILE"] = str(_TEST_ENV_FILE)
env.setdefault("SCP_DEV_MODE", "0")
env.setdefault("SCP_SKIP_STARTUP_GATE", "0")

for test in sorted(TEST_DIR.glob("reality_*.py")):
    started = time.time()
    try:
        proc = subprocess.run(
            PYTHON_COMMAND + [str(test)], cwd=str(ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        status = "SKIP" if proc.returncode == 77 else ("PASS" if proc.returncode == 0 else "FAIL")
        results.append({"test": test.name, "status": status, "returncode": proc.returncode,
                        "duration_sec": round(time.time() - started, 2),
                        "output_tail": proc.stdout[-4000:]})
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or b"") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        results.append({"test": test.name, "status": "TIMEOUT", "returncode": None,
                        "duration_sec": round(time.time() - started, 2),
                        "output_tail": output[-4000:]})
    except Exception as exc:
        results.append({"test": test.name, "status": "ERROR", "returncode": None,
                        "duration_sec": round(time.time() - started, 2),
                        "output_tail": repr(exc)})
    print(f"{results[-1]['status']:7} {test.name} ({results[-1]['duration_sec']}s)", flush=True)
summary = {
    "root": str(ROOT), "python": PYTHON, "test_count": len(results),
    "pass": sum(r["status"] == "PASS" for r in results),
    "skip": sum(r["status"] == "SKIP" for r in results),
    "fail": sum(r["status"] == "FAIL" for r in results),
    "timeout": sum(r["status"] == "TIMEOUT" for r in results),
    "error": sum(r["status"] == "ERROR" for r in results),
    "results": results,
}
out = ROOT / "reports" / "reality" / "reality-tests-results.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
if _TEST_ENV_CREATED:
    try:
        _TEST_ENV_FILE.unlink()
    except OSError:
        pass
print(json.dumps({k: summary[k] for k in ("test_count", "pass", "skip", "fail", "timeout", "error")}, ensure_ascii=False))
raise SystemExit(0 if summary["fail"] == 0 and summary["timeout"] == 0 and summary["error"] == 0 else 1)
