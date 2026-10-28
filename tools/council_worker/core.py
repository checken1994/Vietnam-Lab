"""Council Worker core: state, bus, guard mtime, classifier, policy gate.

Nguyên tắc thiết kế xem README.md. Module này KHÔNG import network/git —
để unit test hermetic (FA-04: phần đang kiểm không mock).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

POLICY_PATH = Path(__file__).with_name("policy.json")

C_TYPE_MARKERS = re.compile(
    r"(secret|credential|password|api\s*[_\-]?\s*key|mật\s*khẩu|xóa\s+vĩnh\s+viễn|"
    r"delete\s+permanently|publish|thanh\s+toán|payment|"
    r"(admin|jwt|capability|journal|ledger)[_\-](key|secret)|"
    r"private[_\-]?\s*key|ssh[_\-]?\s*key|\btoken\b|dotenv)",
    re.IGNORECASE,
)

# .env trần (không phải .env.example) trong bất kỳ ngữ cảnh đọc/in nào → loại C.
ENV_FILE = re.compile(r"\.env\b(?!\.example)", re.IGNORECASE)

# git push chỉ được phép đích origin của repo này; mọi đích khác → loại C.
ALLOWED_PUSH_TARGET = "github.com/checken1994/Vietnam-Lab.git"


def c_type_reason(text: str) -> str:
    """Trả "" nếu artifact lành, ngược lại lý do loại C. Máy chặn CỨNG — LLM
    không phải lớp phòng thủ đầu cho việc này (bài học CHECKER bypass 3 vector).
    Normalize NFKC trước match để homoglyph Cyrillic/Greek không đánh lừa regex."""
    norm = unicodedata.normalize("NFKC", text)
    if C_TYPE_MARKERS.search(norm):
        return "artifact nhắc secret/credential/token/private-key/publish/thanh toán"
    if ENV_FILE.search(norm):
        return "artifact nhắc file .env (ngoài .env.example)"
    for m in re.finditer(r"git\s+push\s+(\S+)", norm, re.IGNORECASE):
        target = m.group(1)
        if ALLOWED_PUSH_TARGET not in target:
            return f"git push đích ngoài repo: {target[:60]}"
    return ""


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    """Nạp policy; thiếu file/hỏng JSON → raise (fail-closed, không mặc định mở)."""
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Bus + state (durable, append-only bus; state atomic write)
# ---------------------------------------------------------------------------

def bus_append(bus_path: Path, record: dict[str, Any]) -> None:
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **record}
    with bus_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def state_load(state_path: Path) -> dict[str, Any]:
    """Đọc state.yaml-mini (JSON cho bản đầu — 1 nguồn, dễ atomic)."""
    if not state_path.exists():
        return {"shift": 0, "epoch": None, "paused_by_owner": False,
                "queue_owner": [], "watch_branches": ["claude/r5-1"]}
    return json.loads(state_path.read_text(encoding="utf-8"))


def state_save(state_path: Path, state: dict[str, Any]) -> None:
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, state_path)


# ---------------------------------------------------------------------------
# Guard mtime (bài học guard v1: KHÔNG nuốt tín hiệu có sẵn trước mốc)
# ---------------------------------------------------------------------------

@dataclass
class GuardHit:
    kind: str            # "inbox" | "git" | None
    detail: str


def guard_scan(inbox: Path, epoch: float, known_files: set[str]) -> GuardHit | None:
    """Trả tín hiệu ĐẦU TIÊN: file mtime > epoch (không so 'đổi so baseline').

    `epoch` = thời điểm bắt đầu ca (unix giây). File có sẵn TRƯỚC ca không báo —
    nhưng caller chịu trách nhiệm đặt epoch ĐÚNG lúc khởi ca (không phải sau khi
    file đã kịp xuất hiện — bài học nuốt tín hiệu).
    """
    if not inbox.is_dir():
        return None
    fresh = []
    for f in sorted(inbox.iterdir()):
        if not f.is_file() or f.name.startswith("."):
            continue
        if f.name in known_files:
            continue
        try:
            if f.stat().st_mtime > epoch:
                fresh.append(f.name)
        except OSError:
            continue
    if fresh:
        return GuardHit(kind="inbox", detail=",".join(fresh))
    return None


# ---------------------------------------------------------------------------
# Phân loại tín hiệu (inbox artifact)
# ---------------------------------------------------------------------------

@dataclass
class Task:
    id: str
    source: str                       # tên file / ref git
    kind: str                         # "inbox_artifact" | "git_commit"
    headline: str
    c_blocked: bool = False
    c_reason: str = ""
    checks: list[str] = field(default_factory=list)   # lệnh deterministic ghi trong artifact
    branch: str = ""


def classify_inbox(source: str, text: str, watch_branches: list[str]) -> Task:
    """Phân loại artifact markdown trong inbox.

    Quy ước dòng đầu `# <tiêu đề>`; các dòng `- check: <lệnh>` khai báo cổg
    deterministic maker muốn worker chạy (pytest/t00). `- mutation: <file> ::
    <lệnh test>` khai báo cổng mutation attack. Nếu nội dung rơi loại C →
    c_blocked (fail-closed, không suy diễn thêm).
    """
    headline = ""
    checks: list[str] = []
    mutations: list[tuple[str, str]] = []
    for ln in text.splitlines():
        if not headline and ln.startswith("# "):
            headline = ln[2:].strip()
        if ln.strip().startswith("- check:"):
            checks.append(ln.split(":", 1)[1].strip())
        m = re.match(r"-\s*mutation:\s*(\S+)\s*::\s*(.+)$", ln.strip())
        if m:
            mutations.append((m.group(1), m.group(2).strip()))
    m = re.search(r"branch:\s*([A-Za-z0-9._/\-]+)", text)
    branch = m.group(1) if m and m.group(1) in watch_branches else ""
    reason = c_type_reason(text)
    t = Task(id=f"inbox-{int(time.time())}-{hashlib.sha1(source.encode()).hexdigest()[:6]}",
             source=source, kind="inbox_artifact", headline=headline or source,
             c_blocked=bool(reason), c_reason=reason, checks=checks, branch=branch)
    t.mutations = mutations  # type: ignore[attr-defined]
    return t


# ---------------------------------------------------------------------------
# Verdict CHECKER + policy gate cho merge
# ---------------------------------------------------------------------------

@dataclass
class CheckerVerdict:
    verdict: str                      # GO | NO-GO | NEEDS-HUMAN
    evidence: list[str] = field(default_factory=list)
    not_checked: list[str] = field(default_factory=list)


def gate_merge(verdict: CheckerVerdict, tree_clean: bool, branch_allowed: bool,
               policy: dict[str, Any]) -> tuple[bool, str]:
    """Cổng merge/push: mọi điều kiện phải cùng xanh — thiếu một cái là không."""
    if verdict.verdict != "GO":
        return False, f"checker={verdict.verdict}"
    if not branch_allowed:
        return False, "nhánh không khớp branch_allowlist_pattern"
    if not tree_clean:
        return False, "cây chính bẩn"
    if "git_merge_listed_branch" not in policy["allowed_actions"]:
        return False, "policy không cho merge"
    return True, "ok"


def parse_llm_verdict(raw: str) -> CheckerVerdict:
    """Rút verdict JSON mà LLM trả về (dòng có \"verdict\"). Không tin gì khác."""
    m = re.search(r'\{[^{}]*"verdict"[^{}]*\}', raw, re.DOTALL)
    if not m:
        return CheckerVerdict(verdict="NEEDS-HUMAN", not_checked=["LLM không trả JSON verdict"])
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return CheckerVerdict(verdict="NEEDS-HUMAN", not_checked=["JSON verdict hỏng"])
    v = str(obj.get("verdict", "NEEDS-HUMAN")).upper()
    if v not in ("GO", "NO-GO", "NEEDS-HUMAN"):
        v = "NEEDS-HUMAN"
    return CheckerVerdict(verdict=v,
                          evidence=[str(x) for x in obj.get("evidence", [])][:8],
                          not_checked=[str(x) for x in obj.get("not_checked", [])][:8])


CHECKER_PROMPT = """Bạn là CHECKER độc lập của hội đồng SCP (đối kháng, không phải người làm).
Nhiệm vụ: TÌM CÁCH PHÁ claim dưới đây. Không tin lời maker — mọi kết luận phải kèm
evidence (lệnh đã chạy + output thật) hoặc ghi rõ not_checked. Không sửa code, không push.
Chỉ phán khi đã có bằng chứng; không đủ → NEEDS-HUMAN.

CLAIM/ARTIFACT:
---
{artifact}
---

Trả về ĐÚNG một khối JSON:
{{"verdict": "GO|NO-GO|NEEDS-HUMAN", "evidence": ["..."], "not_checked": ["..."]}}"""


# ---------------------------------------------------------------------------
# Whitelist lệnh deterministic (chống path traversal — bài học CHECKER attack 3b)
# ---------------------------------------------------------------------------

def check_cmd_allowed(cmd: str) -> bool:
    """Lệnh `- check:` hợp lệ: pytest đúng 1 đường dẫn THUỘC tests/ của repo
    (không `..`, không tuyệt đối) hoặc tools/t00_meta_audit.py trơn."""
    cmd = cmd.strip()
    m = re.match(r"^(?:-m\s+)?pytest\s+(tests/\S+)$", cmd) or \
        re.match(r"^[A-Za-z0-9_./\\\-:]*python(?:\.exe)?\s+-m\s+pytest\s+(tests/\S+)$", cmd)
    if m:
        rel = m.group(1).replace("\\", "/")
        parts = [p for p in rel.split("/") if p]
        if ".." in parts or not parts or parts[0] != "tests":
            return False
        if any(c in rel for c in (";", "&", "|", "`", "$")):
            return False
        return True
    return cmd == "tools/t00_meta_audit.py"


def scrub_secrets(text: str) -> str:
    """Lọc PAT/key khỏi chuỗi TRƯỚC khi ghi đĩa (không có cửa sổ race,
    không fail-open: scrub là hàm thuần bắt buộc qua trước mọi bus_append)."""
    text = re.sub(r"x-access-token:[A-Za-z0-9_]+", "x-access-token:***", text)
    text = re.sub(r"(ghp|gho|sk)_[A-Za-z0-9]{16,}", r"\1_***", text)
    text = re.sub(r"Bearer\s+[A-Za-z0-9._\-]{20,}", "Bearer ***", text)
    return text


# ---------------------------------------------------------------------------
# Cổng mutation attack (nguyên tắc 5 — có code thật, không còn claim trống)
# ---------------------------------------------------------------------------

def mutation_attack(repo: Path, target_file: str, test_cmd: list[str],
                    run_fn) -> dict[str, object]:
    """Gỡ fix → test phải ĐỎ → khôi phục bytes gốc → sha256 phải khớp.

    run_fn(cmd, cwd, timeout) -> (exit_code, tail) do worker tiêm (test tiêm giả).
    Trả record {ok, red_exit, restored_identical, detail}. Fail ở bất kỳ bước → ok=False.
    """
    import shutil
    import subprocess
    f = repo / target_file
    if not f.is_file():
        return {"ok": False, "red_exit": None, "restored_identical": None,
                "detail": f"không thấy file {target_file}"}
    before = f.read_bytes()
    sha_before = hashlib.sha256(before).hexdigest()
    # gỡ fix: checkout từ HEAD; file untracked thì rename tạm
    untracked = False
    tmp_backup: Path | None = None
    code, _ = run_fn(["git", "ls-files", "--error-unmatch", target_file], repo, 30)
    if code != 0:
        untracked = True
        tmp_backup = f.with_suffix(f".mutbak{int(time.time())}")
        shutil.move(str(f), str(tmp_backup))
    else:
        run_fn(["git", "checkout", "--", target_file], repo, 60)
    try:
        red_code, red_tail = run_fn(test_cmd, repo, 600)
        red_ok = red_code != 0
    finally:
        # khôi phục bytes fix CHO MỌI TRƯỜNG HỢP (tracked: checkout đã ghi đè;
        # untracked: trả file từ backup) — restore byte-identical là bắt buộc.
        if untracked and tmp_backup is not None and tmp_backup.is_file():
            shutil.move(str(tmp_backup), str(f))
        elif not untracked:
            f.write_bytes(before)
    after = f.read_bytes()
    identical = hashlib.sha256(after).hexdigest() == sha_before
    ok = bool(red_ok and identical and not untracked)
    return {"ok": ok, "red_exit": red_code, "restored_identical": identical,
            "detail": ("" if ok else
                       ("test KHÔNG đỏ khi gỡ fix (placebo!)" if not red_ok else
                        ("file untracked — không so được với HEAD" if untracked else
                         "restore không byte-identical")))}


# ---------------------------------------------------------------------------
# Tín hiệu git: commit mới trên nhánh đang canh (producer thật cho kind="git")
# ---------------------------------------------------------------------------

def git_new_head(repo: Path, branch: str, run_fn) -> str | None:
    """Trả SHA head mới nếu khác last_seen, ngược lại None. Lỗi git → None
    (im lặng, không phán — caller ghi bus nếu cần)."""
    code, out = run_fn(["git", "rev-parse", branch], repo, 30)
    if code != 0:
        return None
    return out.strip() or None
