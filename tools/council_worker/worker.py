"""Council Worker daemon — thư ký + giám thị của hội đồng (không phải thợ).

Vòng 1 ca (mặc định 28 phút, --once để chạy đúng 1 vòng):
  1. PAUSE check — paused_by_owner → chỉ log, không đụng gì.
  2. Guard mtime: file mới trong inbox/ sau mốc ca (PUSH, không POLL).
  3. Phân loại artifact; loại C → queue_owner, dừng.
  4. Cổng deterministic: chạy các lệnh `- check:` ghi trong artifact
     (chỉ cho phép pytest/tools trong repo — whitelist lệnh), exit code là chân lý.
  5. CHECKER LLM đối kháng (sau khi deterministic xanh) → verdict JSON.
  6. GO + nhánh hợp lệ + cây sạch → merge + push origin main (PAT từ env).
  7. NO-GO → ghi feedback.md vào inbox cho maker. Mọi bước ghi bus.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import core  # noqa: E402
import llm as llm_client  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
import os  # noqa: E402
HOME = Path(os.environ.get("COUNCIL_WORKER_HOME",
                           str(Path.home() / ".council-loop")))


def run_step(cmd: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
    """Chạy lệnh, trả (exit_code, đuôi output). Lệnh là chân lý — không parse màu mè."""
    try:
        p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
        tail = ((p.stdout or "") + (p.stderr or ""))[-2000:]
        return p.returncode, tail
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"


def _log(bus: Path, record: dict) -> None:
    """Mọi ghi bus đều qua scrub_secrets TRƯỚC — không cửa sổ race, không fail-open."""
    record["content"] = core.scrub_secrets(str(record.get("content", "")))
    core.bus_append(bus, record)


def one_cycle(state: dict, policy: dict) -> None:
    inbox = HOME / "inbox"
    bus = HOME / "bus.jsonl"
    state_path = HOME / "state_council_worker.json"
    HOME.mkdir(parents=True, exist_ok=True)
    inbox.mkdir(exist_ok=True)

    if state.get("paused_by_owner"):
        _log(bus, {"id": f"watch-{int(time.time())}", "from": "worker",
                   "status": "paused", "content": "paused by owner — im lặng"})
        return

    epoch = state.get("epoch")
    if not epoch:
        epoch = time.time()
        state["epoch"] = epoch
        core.state_save(state_path, state)

    # Tín hiệu git (producer thật cho kind="git"): head mới trên nhánh đang canh
    last_seen = state.setdefault("last_seen_heads", {})
    for br in state.get("watch_branches", []):
        head = core.git_new_head(REPO, br, run_step)
        if head and head != last_seen.get(br):
            _log(bus, {"id": f"signal-git-{int(time.time())}", "from": "git",
                       "status": "new", "content": f"{br} @ {head[:12]}"})
        if head:
            last_seen[br] = head
    core.state_save(state_path, state)

    known = set(state.get("known_files", []))
    hit = core.guard_scan(inbox, epoch, known)
    if not hit:
        _log(bus, {"id": f"watch-{int(time.time())}", "from": "worker",
                   "status": "quiet", "content": "không tín hiệu mới"})
        return

    _log(bus, {"id": f"signal-{int(time.time())}", "from": "inbox",
               "status": "new", "content": hit.detail})
    # Xử lý file ĐẦU TIÊN trong đợt (một vòng một artifact — không dồn)
    name = hit.detail.split(",")[0]
    f = inbox / name
    text = f.read_text(encoding="utf-8", errors="replace")
    task = core.classify_inbox(name, text, state.get("watch_branches", []))
    state.setdefault("known_files", []).append(name)
    core.state_save(state_path, state)

    if task.c_blocked:
        state.setdefault("queue_owner", []).append(
            {"task": task.id, "source": name, "reason": task.c_reason})
        core.state_save(state_path, state)
        _log(bus, {"id": task.id, "from": "worker", "status": "C_BLOCKED",
                   "content": task.c_reason})
        return

    # Cổng deterministic (nếu artifact khai báo) — whitelist chống traversal
    det_ok = True
    for chk in task.checks:
        chk = chk.strip()
        if not core.check_cmd_allowed(chk):
            _log(bus, {"id": task.id, "from": "worker", "status": "DET_REFUSED",
                       "content": f"lệnh không trong whitelist: {chk}"})
            det_ok = False
            continue
        code, tail = run_step(_shellify(chk), REPO, policy["step_timeout_seconds"])
        _log(bus, {"id": task.id, "from": "worker", "status": "DET_RUN",
                   "content": f"{chk} → exit={code} | {tail[-300:]}"})
        if code != 0:
            det_ok = False
    # Cổng mutation attack (nguyên tắc 5 — bắt placebo bắt buộc)
    for target, tcmd in getattr(task, "mutations", []):
        if not core.check_cmd_allowed(tcmd):
            _log(bus, {"id": task.id, "from": "worker", "status": "MUT_REFUSED",
                       "content": f"lệnh test mutation không whitelist: {tcmd}"})
            det_ok = False
            continue
        rec = core.mutation_attack(REPO, target, _shellify(tcmd), run_step)
        _log(bus, {"id": task.id, "from": "worker", "status": "MUTATION",
                   "content": json_short_like(rec)})
        if not rec.get("ok"):
            det_ok = False
    if not det_ok:
        _feedback(inbox, bus, task.id, name,
                  "Cổng deterministic KHÔNG xanh — CHECKER LLM không được gọi. "
                  "Sửa artifact/test rồi gửi lại tín hiệu mới.")
        return

    # CHECKER LLM đối kháng
    api_key = __import__("os").environ.get("COUNCIL_GLM_API_KEY", "")
    base = __import__("os").environ.get("COUNCIL_GLM_BASE_URL", "https://api.z.ai/api/paas/v4")
    model = __import__("os").environ.get("COUNCIL_GLM_MODEL", "glm-5.3")
    if not api_key:
        _log(bus, {"id": task.id, "from": "worker", "status": "NEEDS-HUMAN",
                   "content": "thiếu COUNCIL_GLM_API_KEY — fail-closed"})
        state.setdefault("queue_owner", []).append({"task": task.id, "source": name,
                                                   "reason": "thiếu key CHECKER"})
        core.state_save(state_path, state)
        return
    prompt = core.CHECKER_PROMPT.format(artifact=text[:8000])
    try:
        raw = llm_client.chat(base, api_key, model, prompt,
                              timeout=policy["llm_timeout_seconds"])
    except Exception as e:  # noqa: BLE001 — mọi lỗi mạng = NEEDS-HUMAN, không retry mù
        _log(bus, {"id": task.id, "from": "worker", "status": "NEEDS-HUMAN",
                   "content": f"LLM lỗi: {str(e)[:200]}"})
        return
    verdict = core.parse_llm_verdict(raw)
    _log(bus, {"id": task.id, "from": "checker", "status": verdict.verdict,
               "content": json_short(verdict)})

    if verdict.verdict == "GO" and task.branch:
        allowed = bool(re.match(policy["branch_allowlist_pattern"], task.branch))
        tree_clean = _tree_clean()
        ok, why = core.gate_merge(verdict, tree_clean, allowed, policy)
        if not ok:
            _log(bus, {"id": task.id, "from": "worker", "status": "MERGE_HOLD",
                       "content": why})
            state.setdefault("queue_owner", []).append({"task": task.id, "source": name,
                                                        "reason": f"merge hold: {why}"})
            core.state_save(state_path, state)
            return
        code, tail = run_step(["git", "merge", task.branch, "--no-edit"], REPO, 120)
        _log(bus, {"id": task.id, "from": "worker", "status": "MERGE",
                   "content": f"exit={code} {tail[-200:]}"})
        if code != 0:
            run_step(["git", "merge", "--abort"], REPO, 60)
            _log(bus, {"id": task.id, "from": "worker", "status": "MERGE_ABORT",
                       "content": "conflict → queue owner"})
            state.setdefault("queue_owner", []).append({"task": task.id, "source": name,
                                                        "reason": "merge conflict cần tay"})
            core.state_save(state_path, state)
            return
        _push(bus, task.id)
    elif verdict.verdict == "NO-GO":
        _feedback(inbox, bus, task.id, name,
                  "CHECKER NO-GO: " + "; ".join(verdict.evidence[:4])
                  + " | not_checked: " + "; ".join(verdict.not_checked[:3]))


def _shellify(chk: str) -> list[str]:
    parts = chk.split()
    if parts and parts[0].endswith(("python", "python.exe")):
        return parts
    return [sys.executable, *parts]


def _tree_clean() -> bool:
    code, out = run_step(["git", "status", "--porcelain"], REPO, 30)
    return code == 0 and out.strip() == ""


def _push(bus, task_id: str) -> None:
    pat_env = os.environ.get("COUNCIL_GIT_PAT_ENV", "GITHUB_TOKEN")
    pat = os.environ.get(pat_env, "")
    if not pat:
        _log(bus, {"id": task_id, "from": "worker", "status": "PUSH_HOLD",
                   "content": f"thiếu PAT ({pat_env}) — commit giữ local"})
        return
    url = f"https://x-access-token:{pat}@github.com/checken1994/Vietnam-Lab.git"
    code, _tail = run_step(["git", "push", url, "main"], REPO, 120)
    # KHÔNG ghi tail push (có thể chứa URL+token): chỉ exit — scrub vẫn áp ở _log.
    _log(bus, {"id": task_id, "from": "worker", "status": "PUSH",
               "content": f"exit={code}"})
    if code != 0:
        _log(bus, {"id": task_id, "from": "worker", "status": "PUSH_FAIL",
                   "content": "push lỗi — giữ local, queue owner"})
        return


def _feedback(inbox: Path, bus: Path, task_id: str, source: str, message: str) -> None:
    fb = inbox / f"feedback-{int(time.time())}.md"
    fb.write_text(f"# Feedback cho {source}\n\n{message}\n", encoding="utf-8")
    _log(bus, {"id": task_id, "from": "worker", "status": "FEEDBACK",
               "content": message[:300]})


def json_short(v: core.CheckerVerdict) -> str:
    import json as _j
    return _j.dumps({"verdict": v.verdict, "evidence": v.evidence,
                     "not_checked": v.not_checked}, ensure_ascii=False)[:600]


def json_short_like(rec: dict) -> str:
    import json as _j
    return _j.dumps(rec, ensure_ascii=False)[:400]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="chạy đúng 1 vòng")
    ap.add_argument("--resume", action="store_true",
                    help="xóa cờ paused_by_owner (chủ sở hữu dùng)")
    ap.add_argument("--shift-seconds", type=int, default=0, help="đè thời lượng ca")
    args = ap.parse_args()

    policy = core.load_policy()
    state_path = HOME / "state_council_worker.json"
    state = core.state_load(state_path)
    if args.resume:
        state["paused_by_owner"] = False
        core.state_save(state_path, state)
        print("resumed")
        return
    limit = args.shift_seconds or policy["shift_max_seconds"]
    started = time.time()
    while True:
        one_cycle(state, policy)
        if args.once:
            break
        if time.time() - started > limit:
            break
        time.sleep(policy["watch_poll_seconds"])
    print("ca kết thúc")


if __name__ == "__main__":
    main()
