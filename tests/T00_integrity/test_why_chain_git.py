"""WHY-CHAIN gate against REAL git repositories: range check (CI), merge exemption, commit-msg hook.

Everything runs in throw-away repos under tmp_path; the SCP repo itself is never touched.
Red-first targets: a commit without a chain accepted by CI / hook, an unwired commit-msg hook,
an exemption wider than "clean git-default merge".
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "check_why_chain.py"
INSTALLER = ROOT / "tools" / "install_git_hooks.py"

_spec = importlib.util.spec_from_file_location("check_why_chain_git_under_test", TOOL)
wc = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = wc
_spec.loader.exec_module(wc)

_ispec = importlib.util.spec_from_file_location("install_git_hooks_under_test", INSTALLER)
installer = importlib.util.module_from_spec(_ispec)
sys.modules[_ispec.name] = installer
_ispec.loader.exec_module(installer)

_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
_ENV.update({"PYTHONUTF8": "1", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
             "SCP_PYTHON": sys.executable})


def git(repo: Path, *args: str, check: bool = True, input_text: str | None = None) -> subprocess.CompletedProcess:
    res = subprocess.run(["git", *args], cwd=repo, capture_output=True, env=_ENV,
                         input=input_text.encode("utf-8") if input_text is not None else None)
    if check and res.returncode != 0:
        raise AssertionError(f"git {args} failed: {res.stderr.decode('utf-8', 'replace')}")
    return res


def chain_n(n: int) -> str:
    """A valid chain whose wording differs per n (so reuse detection does not fire)."""
    topics = ["khoá reentrant", "bộ nhớ đệm token", "đồng hồ lease", "cổng egress", "parser nhãn",
              "sổ journal", "bộ lập lịch", "đường dẫn checkpoint"]
    t = topics[n % len(topics)] + f" số {n}"
    return (
        "Chuỗi Tại Sao:\n"
        f"1. GỐC: VERIFIED: `git grep -n {t.split()[0]}` {n + 3} dòng cho thấy lỗi ở {t}. "
        f"Nguyên nhân song song đã xét: thời gian chờ mạng, loại trừ bằng log `run-{n}` sạch.\n"
        f"2. SỬA-ĐÚNG: VERIFIED: `pytest tests/x.py::test_{n}` đỏ {n + 1} failed trên base rồi xanh sau sửa {t}.\n"
        f"3. TIN-BẰNG-CHỨNG: HYPOTHESIS: checker khác lineage chưa tái chạy lần {n}, mới có lần chạy của maker.\n"
        f"4. ĐỔI-QUYẾT-ĐỊNH: nếu {t} còn tái hiện sau {n + 10} lần chạy liên tiếp thì rollback patch này.\n"
        f"5. UNKNOWN_WITH_REASON: chưa biết hành vi {t} trên máy khác vì chỉ thử trên một host duy nhất.\n"
    )


def commit(repo: Path, subject: str, body: str = "", *, fname: str | None = None, hooks: bool = False) -> str:
    f = fname or f"f_{abs(hash(subject)) % 10**8}.txt"
    (repo / f).write_text(subject + "\n", encoding="utf-8")
    git(repo, "add", "-A")
    message = subject + ("\n\n" + body if body else "") + "\n"
    args = ["commit", "-q", "-F", "-"] + ([] if hooks else ["--no-verify"])
    git(repo, *args, input_text=message)
    return git(repo, "rev-parse", "HEAD").stdout.decode().strip()


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / "r"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "core.autocrlf", "false")
    git(r, "config", "core.hooksPath", str(r / ".git" / "hooks"))
    return r


def policy_for(tmp_path: Path, epoch: str) -> Path:
    p = tmp_path / "policy.json"
    p.write_text(json.dumps({"version": 1, "epoch_sha": epoch, "recent_window": 20}), encoding="utf-8")
    return p


def run_range(repo: Path, policy: Path, head="HEAD", base=None):
    args = [sys.executable, str(TOOL), "--range", head, "--policy", str(policy), "--repo", str(repo)]
    if base:
        args += ["--base", base]
    res = subprocess.run(args, capture_output=True, env=_ENV)
    return res.returncode, res.stdout.decode("utf-8", "replace") + res.stderr.decode("utf-8", "replace")


# --------------------------------------------------------------------------- range (CI)

def test_range_rejects_commit_without_chain_and_accepts_with_chain(repo, tmp_path):
    epoch = commit(repo, "legacy: lịch sử cũ không có chuỗi")
    pol = policy_for(tmp_path, epoch)
    commit(repo, "fix(a): đã làm gì + test xanh", "Test xanh 12 passed.")
    rc, out = run_range(repo, pol)
    assert rc == 1 and "REJECT" in out and "failed=1" in out
    git(repo, "reset", "-q", "--hard", epoch)
    commit(repo, "fix(a): có chuỗi", chain_n(1))
    rc, out = run_range(repo, pol)
    assert rc == 0 and "failed=0" in out and "why_chain_commits_checked=1" in out


def test_history_up_to_the_epoch_is_exempt_and_epoch_itself_too(repo, tmp_path):
    commit(repo, "root cũ")
    epoch = commit(repo, "epoch cũ")
    rc, out = run_range(repo, policy_for(tmp_path, epoch))
    assert rc == 0 and "why_chain_commits_checked=0" in out


def test_in_range_verbatim_reuse_between_commits_is_rejected(repo, tmp_path):
    epoch = commit(repo, "epoch")
    pol = policy_for(tmp_path, epoch)
    commit(repo, "fix(a): một", chain_n(1))
    commit(repo, "fix(b): hai copy nguyên chuỗi", chain_n(1))
    rc, out = run_range(repo, pol)
    assert rc == 1 and "dung lai nguyen van" in out and "failed=1" in out


def test_reuse_is_also_detected_against_commits_before_the_base(repo, tmp_path):
    epoch = commit(repo, "epoch")
    pol = policy_for(tmp_path, epoch)
    base = commit(repo, "fix(a): đã có", chain_n(2))
    commit(repo, "fix(b): chép lại chuỗi của commit trước base", chain_n(2))
    rc, out = run_range(repo, pol, base=base)
    assert rc == 1 and "dung lai nguyen van" in out


def test_vietnamese_subjects_do_not_break_decoding(repo, tmp_path):
    epoch = commit(repo, "epoch")
    commit(repo, "fix(ł): Łódź Ǝ ỗ ợ — tiêu đề có byte lạ", chain_n(3))
    rc, out = run_range(repo, policy_for(tmp_path, epoch))
    assert rc == 0, out


def test_revert_commit_is_not_exempt(repo, tmp_path):
    epoch = commit(repo, "epoch")
    commit(repo, "fix(a): thật", chain_n(4))
    git(repo, "revert", "--no-edit", "HEAD")
    rc, out = run_range(repo, policy_for(tmp_path, epoch))
    assert rc == 1 and "Revert" in out


def test_fixup_and_bot_style_commits_are_not_exempt(repo, tmp_path):
    epoch = commit(repo, "epoch")
    commit(repo, "fixup! fix(a): thật")
    commit(repo, "chore(deps): bump foo from 1 to 2")
    rc, out = run_range(repo, policy_for(tmp_path, epoch))
    assert rc == 1 and "failed=2" in out


def test_missing_epoch_or_policy_is_fail_closed_exit_2(repo, tmp_path):
    commit(repo, "x")
    rc, out = run_range(repo, policy_for(tmp_path, "0123456789abcdef0123456789abcdef01234567"))
    assert rc == 2 and "epoch_sha" in out
    rc, out = run_range(repo, tmp_path / "does-not-exist.json")
    assert rc == 2


def test_unusable_base_falls_back_to_checking_everything_after_epoch(repo, tmp_path):
    epoch = commit(repo, "epoch")
    commit(repo, "fix(a): thiếu chuỗi")
    rc, out = run_range(repo, policy_for(tmp_path, epoch), base="0" * 40)
    assert rc == 1 and "unusable" in out


# --------------------------------------------------------------------------- merge exemption (the only one)

def _diverged(repo: Path):
    epoch = commit(repo, "epoch")
    git(repo, "checkout", "-q", "-b", "side")
    commit(repo, "fix(side): bên nhánh", chain_n(5), fname="side.txt")
    git(repo, "checkout", "-q", "main")
    commit(repo, "fix(main): bên chính", chain_n(6), fname="main.txt")
    return epoch


def test_clean_git_default_merge_is_exempt(repo, tmp_path):
    epoch = _diverged(repo)
    git(repo, "merge", "--no-ff", "--no-verify", "-q", "side")  # git-generated subject
    rc, out = run_range(repo, policy_for(tmp_path, epoch))
    assert rc == 0 and "EXEMPT clean git-default merge" in out and "exempt=1" in out


def test_handwritten_merge_message_is_not_exempt(repo, tmp_path):
    epoch = _diverged(repo)
    git(repo, "merge", "--no-ff", "--no-verify", "-q", "-m", "merge(ask): gộp tay không có chuỗi", "side")
    rc, out = run_range(repo, policy_for(tmp_path, epoch))
    assert rc == 1 and "merge(ask)" in out and "exempt=0" in out


def test_default_subject_but_evil_merge_with_extra_changes_is_not_exempt(repo, tmp_path):
    epoch = _diverged(repo)
    git(repo, "merge", "--no-ff", "--no-commit", "-q", "side")
    (repo / "smuggled.txt").write_text("không nằm trong hai nhánh\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", "Merge branch 'side'")
    rc, out = run_range(repo, policy_for(tmp_path, epoch))
    assert rc == 1 and "exempt=0" in out


def test_default_subject_on_a_non_merge_commit_is_not_exempt(repo, tmp_path):
    epoch = commit(repo, "epoch")
    commit(repo, "Merge branch 'side' (giả danh, chỉ một parent)")
    rc, out = run_range(repo, policy_for(tmp_path, epoch))
    assert rc == 1 and "exempt=0" in out


# --------------------------------------------------------------------------- installer + commit-msg hook

def _tooled_repo(repo: Path, tmp_path: Path):
    """Repo carrying the checker + policy (as a real SCP checkout does), hooks installed."""
    base = commit(repo, "base trước khi có công cụ")
    (repo / "tools").mkdir()
    shutil.copy(TOOL, repo / "tools" / "check_why_chain.py")
    (repo / "spec").mkdir()
    (repo / "spec" / "why_chain_policy.json").write_text(
        json.dumps({"version": 1, "epoch_sha": base, "recent_window": 20}), encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", "chore: thêm công cụ")
    hooks = installer.resolve_hooks_dir(repo)
    hooks.mkdir(parents=True, exist_ok=True)
    for name, marker, body, label in installer.HOOKS:
        if name == "commit-msg":  # pre-commit would run the full T00; only the WHY gate is under test
            assert installer.install_one(hooks, name, marker, body, label) == 0
    return base


def test_installer_manages_both_hooks_and_they_are_the_real_scripts():
    names = {h[0] for h in installer.HOOKS}
    assert names == {"pre-commit", "commit-msg"}
    bodies = {h[0]: h[2] for h in installer.HOOKS}
    assert "t00_meta_audit.py" in bodies["pre-commit"]
    assert 'check_why_chain.py" --commit-msg' in bodies["commit-msg"] or "--commit-msg \"$1\"" in bodies["commit-msg"]
    assert "exit 1" in bodies["commit-msg"] and "fail-closed" in bodies["commit-msg"]


def test_installer_is_idempotent_and_never_overwrites_a_foreign_hook(tmp_path):
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    name, marker, body, label = installer.HOOKS[1]
    assert installer.install_one(hooks, name, marker, body, label) == 0
    assert installer.install_one(hooks, name, marker, body, label) == 0
    assert (hooks / name).read_text(encoding="utf-8") == body
    foreign = tmp_path / "hooks2"
    foreign.mkdir()
    (foreign / name).write_text("#!/bin/sh\necho foreign\n", encoding="utf-8")
    assert installer.install_one(foreign, name, marker, body, label) == 1
    assert (foreign / name).read_text(encoding="utf-8") == "#!/bin/sh\necho foreign\n"


def test_hooks_dir_resolves_for_linked_worktrees(repo, tmp_path):
    commit(repo, "x")
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", str(wt), "-b", "wtb")
    git(repo, "config", "--unset", "core.hooksPath")
    assert installer.resolve_hooks_dir(wt).resolve() == (repo / ".git" / "hooks").resolve()


def test_commit_msg_hook_blocks_a_commit_without_chain_and_allows_one_with_chain(repo, tmp_path):
    _tooled_repo(repo, tmp_path)
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    git(repo, "add", "-A")
    res = git(repo, "commit", "-q", "-m", "fix(a): đã làm gì + test xanh", check=False)
    assert res.returncode != 0 and b"COMMIT BLOCKED" in (res.stdout + res.stderr)
    ok = git(repo, "commit", "-q", "-F", "-", input_text="fix(a): có chuỗi\n\n" + chain_n(7), check=False)
    assert ok.returncode == 0, ok.stdout + ok.stderr


def test_commit_msg_hook_rejects_verbatim_reuse_of_the_previous_commit(repo, tmp_path):
    _tooled_repo(repo, tmp_path)
    for n, expect in ((8, 0), (8, 1)):
        (repo / f"a{n}{expect}.txt").write_text("a\n", encoding="utf-8")
        git(repo, "add", "-A")
        res = git(repo, "commit", "-q", "-F", "-", input_text=f"fix(a{expect}): thay đổi\n\n" + chain_n(n), check=False)
        assert (res.returncode != 0) == bool(expect), res.stdout + res.stderr


def test_commit_msg_hook_blocks_when_the_checker_is_missing_everywhere(repo, tmp_path):
    hooks = installer.resolve_hooks_dir(repo)
    hooks.mkdir(parents=True, exist_ok=True)
    name, marker, body, label = installer.HOOKS[1]
    installer.install_one(hooks, name, marker, body, label)
    commit(repo, "x", hooks=False)
    (repo / "b.txt").write_text("b\n", encoding="utf-8")
    git(repo, "add", "-A")
    res = git(repo, "commit", "-q", "-m", "fix: bất kỳ", check=False)
    assert res.returncode != 0 and b"not found" in (res.stdout + res.stderr)


def test_old_linked_worktree_without_the_tool_falls_back_to_the_main_checkout(repo, tmp_path):
    base = _tooled_repo(repo, tmp_path)
    wt = tmp_path / "oldwt"
    git(repo, "worktree", "add", "-q", str(wt), "-b", "oldb", base)  # predates tools/
    assert not (wt / "tools" / "check_why_chain.py").exists()
    (wt / "w.txt").write_text("w\n", encoding="utf-8")
    git(wt, "add", "-A")
    res = git(wt, "commit", "-q", "-m", "fix(w): thiếu chuỗi", check=False)
    assert res.returncode != 0 and b"WHY-Chain" in (res.stdout + res.stderr) and b"not found" not in (res.stdout + res.stderr)
    ok = git(wt, "commit", "-q", "-F", "-", input_text="fix(w): có chuỗi\n\n" + chain_n(2), check=False)
    assert ok.returncode == 0, ok.stdout + ok.stderr


def test_hook_exempts_a_clean_default_merge_but_not_a_handwritten_one(repo, tmp_path):
    _tooled_repo(repo, tmp_path)
    git(repo, "checkout", "-q", "-b", "side")
    commit(repo, "fix(side): nhánh", chain_n(1), fname="side.txt", hooks=True)
    git(repo, "checkout", "-q", "main")
    commit(repo, "fix(main): chính", chain_n(2), fname="main.txt", hooks=True)
    res = git(repo, "merge", "--no-ff", "-q", "side", check=False)
    assert res.returncode == 0, res.stdout + res.stderr
    git(repo, "reset", "-q", "--hard", "HEAD^")
    res = git(repo, "merge", "--no-ff", "-q", "-m", "merge(x): gộp tay không chuỗi", "side", check=False)
    assert res.returncode != 0 and b"COMMIT BLOCKED" in (res.stdout + res.stderr)
    git(repo, "merge", "--abort", check=False)
