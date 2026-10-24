"""[F-05 audit-r2 2026-10-01 → HC-02 2026-10-23] __main__ env loading works
with dotenv poisoned/missing, keys land in os.environ.

Lịch sử harness (không hạ chuẩn — strictness tăng):
  * F-05 gốc: _load_env_at_startup có nhánh ImportError cho python-dotenv
    thiếu, gọi logger.debug trước khi logger được bind → NameError OBSERVED
    (probe probe2_main_logger_nameerror). Harness pin: no crash + fallback
    parser chạy + stderr notice.
  * [HC-02 2026-10-23] __main__ KHÔNG còn loader divergent: nó delegate
    scp/security/env_loader.load_selected_env() — loader duy nhất, KHÔNG phụ
    thuộc python-dotenv từ đầu (parser riêng). Nhánh lỗi F-05 bị loại BẬT
    NGUỒN GỐC, nên stderr notice của nhánh dotenv-fallback không còn là
    surface cần pin. Contract thay thế (strict hơn về mặt kết quả):
      1. Với dotenv BỊ POISON (sys.modules['dotenv']=None — chặt hơn "thiếu"):
         import scp.__main__ vẫn sống, không NameError/traceback.
      2. Key của .env (fallback branch) vẫn vào os.environ — hiệu ứng cuối
         phải giống hệt trước, bất kể implementation.
      3. Control: SCP_ENV_FILE explicit boundary vẫn áp key.
  Probe chạy SUBPROCESS với sandbox fake repo root: scp/__main__.py VÀ
  scp/security/env_loader.py là bản copy working-tree thật để nhánh
  repo-root-.env của env_loader (parents[2]) resolve về sandbox/.env —
  không đụng .env của repo thật.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

_FALLBACK_PROBE = """
import sys
# Poison dotenv: mọi `import dotenv` giờ raise ImportError. Loader hợp nhất
# KHÔNG được phụ thuộc nó (chặt hơn F-05 gốc vốn chỉ mô phỏng "thiếu").
sys.modules['dotenv'] = None
import os
os.environ.pop('SCP_ENV_FILE', None)
sys.path.insert(0, r'{sandbox}')
# Importing the entry module executes _load_env_at_startup() at module scope;
# sandbox copy của env_loader resolve parents[2] -> sandbox/.env.
import scp.__main__
loaded = os.environ.get('SCP_MAIN_FALLBACK_PROBE')
assert loaded == 'ok-f05', f'unified loader did not apply sandbox .env: SCP_MAIN_FALLBACK_PROBE={{loaded!r}}'
print('F05-FALLBACK-OK')
"""

_EXPLICIT_PROBE = """
import os
import sys
os.environ['SCP_ENV_FILE'] = r'{env_file}'
sys.path.insert(0, r'{repo_root}')
import scp.__main__
assert os.environ.get('SCP_MAIN_EXPLICIT_PROBE') == 'ok-explicit', (
    'explicit env file keys must apply')
print('F05-EXPLICIT-OK')
"""


def _make_sandbox(tmp_path: Path) -> Path:
    """Fake repo root whose scp/__main__.py + scp/security/env_loader.py are
    the REAL working-tree files (copy2, không làm fallback resolve .env thật)."""
    sandbox = tmp_path / "sandbox"
    (sandbox / "scp" / "security").mkdir(parents=True)
    shutil.copy2(_REPO_ROOT / "scp" / "__main__.py", sandbox / "scp" / "__main__.py")
    shutil.copy2(
        _REPO_ROOT / "scp" / "security" / "env_loader.py",
        sandbox / "scp" / "security" / "env_loader.py",
    )
    real_pkg_dir = str((_REPO_ROOT / "scp")).replace("\\", "\\\\")
    real_security_dir = str((_REPO_ROOT / "scp" / "security")).replace("\\", "\\\\")
    sandbox_pkg_dir = str((sandbox / "scp")).replace("\\", "\\\\")
    sandbox_security_dir = str((sandbox / "scp" / "security")).replace("\\", "\\\\")
    # Package shims: SANDBOX dir đầu tiên trong __path__ (scp.__main__ và
    # scp.security.env_loader resolve về bản sandbox whose parents[2] là
    # sandbox root), rồi kéo dài bằng dir THẬT cho mọi submodule khác
    # (production_guard, secret_loader, release_identity, ...).
    (sandbox / "scp" / "__init__.py").write_text(
        f"__path__ = [r'{sandbox_pkg_dir}', r'{real_pkg_dir}']\n"
        "from scp.core.release_identity import RELEASE_VERSION\n"
        "__version__ = RELEASE_VERSION\n",
        encoding="utf-8",
    )
    (sandbox / "scp" / "security" / "__init__.py").write_text(
        f"__path__ = [r'{sandbox_security_dir}', r'{real_security_dir}']\n",
        encoding="utf-8",
    )
    return sandbox


def test_env_loader_fallback_runs_without_dotenv(tmp_path):
    """[NodeID giữ nguyên theo FA-02/Phase 1.1 — thân test assert hành vi MỚI:
    loader hợp nhất (env_loader.load_selected_env, K2-W1) áp fallback .env
    mà không cần python-dotenv; contract cũ (stderr notice) đã bị loại tận
    gốc khi __main__ gộp vào env_loader.]"""
    sandbox = _make_sandbox(tmp_path)
    (sandbox / ".env").write_text("SCP_MAIN_FALLBACK_PROBE=ok-f05\n", encoding="utf-8")
    probe = _FALLBACK_PROBE.format(sandbox=str(sandbox).replace("\\", "\\\\"))
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=110,
    )
    assert proc.returncode == 0, (
        "env loader crashed with dotenv poisoned (F-05 regression class)\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    )
    assert "F05-FALLBACK-OK" in proc.stdout
    assert "NameError" not in proc.stderr, (
        "F-05 regression: logger referenced before binding in the "
        f"dotenv-missing branch\nstderr={proc.stderr!r}"
    )
    assert "Traceback" not in proc.stderr, f"loader crashed:\n{proc.stderr!r}"


def test_env_loader_explicit_file_applies_keys_with_dotenv_present(tmp_path):
    """Control: with the explicit SCP_ENV_FILE boundary, keys still apply."""
    env_file = tmp_path / "explicit.env"
    env_file.write_text("SCP_MAIN_EXPLICIT_PROBE=ok-explicit\n", encoding="utf-8")
    probe = _EXPLICIT_PROBE.format(
        env_file=str(env_file).replace("\\", "\\\\"),
        repo_root=str(_REPO_ROOT).replace("\\", "\\\\"),
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=110,
    )
    assert proc.returncode == 0, proc.stderr
    assert "F05-EXPLICIT-OK" in proc.stdout
