"""[FU-DOUBTCRON] doubt_cron._check_kernel_integrity không được bơm TOÀN BỘ .env vào os.environ.

Lỗi trên main: khi env thô SCP_JOURNAL_SECRET rỗng, hàm đọc ``<repo>/.env`` và nạp MỌI cặp
key=value chưa có vào ``os.environ``. Nếu K_journal được cấp qua kênh
``SCP_JOURNAL_SECRET_FILE`` (secret_registry HC-08) thì env thô rỗng là trạng thái HỢP LỆ, nên
mọi secret khác của .env bị bơm vào môi trường tiến trình (rò sang subprocess/log, phá
least-privilege).

Zero-mock: code chạy là source THẬT của ``scp/core/doubt_cron.py`` (đọc lại byte-identical lúc
test chạy, nên mọi sửa/mutation ở file thật đều được kiểm), đặt trong một repo giả tạm để
đường dẫn mặc định ``parents[2]/.env`` trỏ vào một .env GIẢ — không bao giờ đọc .env thật
của repo. Kernel là DB SQLite tạm thật, verify_integrity thật. Mọi giá trị là chuỗi test giả.
"""
from __future__ import annotations

import importlib.util
import os
import sqlite3
from pathlib import Path
from types import ModuleType

import pytest

import scp.core.doubt_cron as real_doubt_cron
from scp.task_kernel import TaskKernel

FAKE_JOURNAL_KEY = "fu-doubtcron-fake-journal-key-for-tests-only-0001"
FAKE_DOTENV_JOURNAL_KEY = "fu-doubtcron-fake-dotenv-journal-key-tests-only-02"
FAKE_PREVIOUS_JOURNAL_KEY = "fu-doubtcron-fake-previous-journal-key-tests-only-3"
FAKE_CAPABILITY_KEY = "fu-doubtcron-fake-capability-key-for-tests-only-004"
FAKE_OTHER_SECRET = "fu-doubtcron-fake-unrelated-secret-tests-only-0005"
FAKE_ADMIN_KEY = "fu-doubtcron-fake-admin-key-for-tests-only-000006"

# Khóa mà journal verify của kernel thực sự đọc (scp/task_kernel_parts/definitions.py):
# journal_secret, previous_journal_secrets, legacy_capability_journal_keys.
KERNEL_JOURNAL_KEYS = (
    "SCP_JOURNAL_SECRET",
    "SCP_PREVIOUS_JOURNAL_SECRETS",
    "SCP_CAPABILITY_SECRET",
    "SCP_PREVIOUS_CAPABILITY_SECRETS",
)
UNRELATED_KEYS = ("OTHER_SECRET_X", "SCP_ADMIN_KEY")
ALL_TOUCHED_KEYS = (*KERNEL_JOURNAL_KEYS, *UNRELATED_KEYS, "SCP_JOURNAL_SECRET_FILE")


def _present(keys: tuple[str, ...]) -> list[str]:
    """Tên các key đang có trong os.environ. Assert trên DANH SÁCH TÊN để thông báo lỗi
    không bao giờ in repr của os.environ (có thể chứa secret thật của shell)."""
    return [key for key in keys if key in os.environ]


def _matches(key: str, expected: str) -> bool:
    return os.environ.get(key) == expected


def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Xóa các key liên quan và BẢO ĐẢM teardown xóa cả key do code dưới test tự thêm.

    ``monkeypatch.delenv`` trên key vắng mặt không ghi nhận gì để hoàn tác; setenv trước rồi
    delenv thì monkeypatch ghi nhận trạng thái gốc nên key bị code bơm vào cũng được dọn.
    """
    for key in ALL_TOUCHED_KEYS:
        monkeypatch.setenv(key, "placeholder")
        monkeypatch.delenv(key)


def _load_in_fake_repo(repo_root: Path) -> ModuleType:
    """Source thật của doubt_cron, đặt tại <repo_root>/scp/core/ → parents[2]/.env = repo_root/.env."""
    target = repo_root / "scp" / "core" / "doubt_cron.py"
    target.parent.mkdir(parents=True)
    target.write_bytes(Path(real_doubt_cron.__file__).read_bytes())
    spec = importlib.util.spec_from_file_location("_fu_doubtcron_fake_repo_copy", target)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_fake_dotenv(repo_root: Path) -> None:
    (repo_root / ".env").write_text(
        "\n".join(
            [
                "# fake .env for FU-DOUBTCRON tests",
                f"SCP_JOURNAL_SECRET={FAKE_DOTENV_JOURNAL_KEY}",
                f"SCP_PREVIOUS_JOURNAL_SECRETS={FAKE_PREVIOUS_JOURNAL_KEY}",
                f"SCP_CAPABILITY_SECRET={FAKE_CAPABILITY_KEY}",
                f'OTHER_SECRET_X="{FAKE_OTHER_SECRET}"',
                f"SCP_ADMIN_KEY={FAKE_ADMIN_KEY}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _make_kernel_db(data_dir: Path) -> Path:
    """Kernel DB thật, 2 task có journal ký dưới K_journal đang hiệu lực lúc gọi."""
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "ask_task_kernel.sqlite3"
    kernel = TaskKernel(db_path)
    try:
        kernel.create_task("fu-doubtcron-1", "tester", "first")
        kernel.create_task("fu-doubtcron-2", "tester", "second")
    finally:
        kernel.close()
    return db_path


@pytest.fixture
def fake_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ModuleType, Path, Path]:
    _isolate_env(monkeypatch)
    repo_root = tmp_path / "repo"
    module = _load_in_fake_repo(repo_root)
    return module, repo_root, tmp_path / "data"


def test_file_channel_only_does_not_inject_dotenv(fake_repo, tmp_path, monkeypatch):
    """(a) K_journal chỉ qua SCP_JOURNAL_SECRET_FILE → .env không được nạp, kể cả khóa journal."""
    module, repo_root, data_dir = fake_repo
    secret_file = tmp_path / "k_journal.secret"
    secret_file.write_text(FAKE_JOURNAL_KEY, encoding="utf-8")
    monkeypatch.setenv("SCP_JOURNAL_SECRET_FILE", str(secret_file))
    _make_kernel_db(data_dir)
    _write_fake_dotenv(repo_root)

    result = module._check_kernel_integrity(str(data_dir))

    assert _present(UNRELATED_KEYS) == [], "unrelated .env keys leaked into os.environ"
    assert _present(KERNEL_JOURNAL_KEYS) == [], ".env loaded although the _FILE channel is configured"
    assert result["check"] == "kernel_integrity"
    assert result["ok"] is True, result
    assert result["detail"]["quick_check"] == "ok"
    assert result["detail"]["invalid_chains_count"] == 0


def test_broken_file_channel_stays_fail_closed_without_dotenv(fake_repo, tmp_path, monkeypatch):
    """(a') _FILE trỏ file không tồn tại → fail-closed của registry; .env KHÔNG được lấy bù."""
    module, repo_root, data_dir = fake_repo
    monkeypatch.setenv("SCP_JOURNAL_SECRET", FAKE_JOURNAL_KEY)
    _make_kernel_db(data_dir)
    monkeypatch.delenv("SCP_JOURNAL_SECRET")
    monkeypatch.setenv("SCP_JOURNAL_SECRET_FILE", str(tmp_path / "missing.secret"))
    _write_fake_dotenv(repo_root)

    result = module._check_kernel_integrity(str(data_dir))

    assert _present((*KERNEL_JOURNAL_KEYS, *UNRELATED_KEYS)) == [], ".env used despite a configured (broken) _FILE channel"
    assert result["ok"] is False, result
    assert result["detail"]["invalid_chains_count"] >= 1


def test_no_channel_loads_only_kernel_journal_keys(fake_repo, monkeypatch):
    """(b) Không kênh nào → chỉ khóa journal verify cần được nạp; key khác của .env thì không."""
    module, repo_root, data_dir = fake_repo
    monkeypatch.setenv("SCP_JOURNAL_SECRET", FAKE_DOTENV_JOURNAL_KEY)
    _make_kernel_db(data_dir)
    monkeypatch.delenv("SCP_JOURNAL_SECRET")
    _write_fake_dotenv(repo_root)

    result = module._check_kernel_integrity(str(data_dir))

    assert _present(UNRELATED_KEYS) == [], "unrelated .env keys leaked into os.environ"
    assert _matches("SCP_JOURNAL_SECRET", FAKE_DOTENV_JOURNAL_KEY)
    assert _matches("SCP_PREVIOUS_JOURNAL_SECRETS", FAKE_PREVIOUS_JOURNAL_KEY)
    assert _matches("SCP_CAPABILITY_SECRET", FAKE_CAPABILITY_KEY)
    # K_journal nạp từ .env là khóa đã ký journal → verify thật xanh (khóa thực sự được dùng).
    assert result["ok"] is True, result
    assert result["detail"]["invalid_chains_count"] == 0


def test_present_env_secret_never_reads_dotenv(fake_repo, monkeypatch):
    """(c) SCP_JOURNAL_SECRET có sẵn → không đọc .env (đặt .env là THƯ MỤC: đọc là raise)."""
    module, repo_root, data_dir = fake_repo
    monkeypatch.setenv("SCP_JOURNAL_SECRET", FAKE_JOURNAL_KEY)
    _make_kernel_db(data_dir)
    (repo_root / ".env").mkdir()

    result = module._check_kernel_integrity(str(data_dir))

    assert _matches("SCP_JOURNAL_SECRET", FAKE_JOURNAL_KEY)
    assert _present(UNRELATED_KEYS) == []
    assert result["ok"] is True, result


def test_integrity_check_still_detects_tampered_journal(fake_repo, monkeypatch):
    """(d) Phép kiểm vẫn thật: journal sạch → ok; sửa một event → ok False (anti-placebo)."""
    module, _repo_root, data_dir = fake_repo
    monkeypatch.setenv("SCP_JOURNAL_SECRET", FAKE_JOURNAL_KEY)
    db_path = _make_kernel_db(data_dir)

    clean = module._check_kernel_integrity(str(data_dir))
    assert clean["ok"] is True, clean
    assert clean["detail"]["quick_check"] == "ok"
    assert clean["detail"]["invalid_chains_count"] == 0

    conn = sqlite3.connect(db_path)
    try:
        changed = conn.execute(
            "UPDATE events SET reason = 'tampered-by-test' WHERE task_id = 'fu-doubtcron-1' AND seq = 1"
        ).rowcount
        conn.commit()
    finally:
        conn.close()
    assert changed == 1

    tampered = module._check_kernel_integrity(str(data_dir))
    assert tampered["ok"] is False, tampered
    assert tampered["detail"]["invalid_chains_count"] >= 1


def test_real_module_runs_from_this_checkout():
    """Bằng chứng phạm vi: bản sao test là source của checkout đang chạy, không phải cây khác."""
    here = Path(__file__).resolve().parents[2]
    assert Path(real_doubt_cron.__file__).resolve().is_relative_to(here)
