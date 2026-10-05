"""SCP /ask behavior probe battery — 16 questions, SEQUENTIAL, exact order.

Auth/payload shape copied from tools/live_flow_proof.py (lines 123-148):
  1. POST /auth/token  body={"admin_key": SCP_ADMIN_KEY}  -> access_token (JWT)
  2. POST /ask         header "Authorization: Bearer <JWT>"
                       body={"question": ..., "session_id": ...}
Secrets: SCP_ADMIN_KEY read from D:\\scp\\.env in-process, NEVER printed.
Results: %TEMP%\\scp_ask_probe_results.json (array of 16 objects, written
incrementally after each probe).
"""
import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(r"D:\scp")
sys.path.insert(0, str(ROOT))

from scp.security.url_safety import safe_urlopen  # noqa: E402 — repo SSRF guard

logger = logging.getLogger(__name__)

BASE = "http://127.0.0.1:8091"
RESULTS_PATH = Path(os.environ["TEMP"]) / "scp_ask_probe_results.json"
SERVER_LOG = Path(os.environ["TEMP"]) / "scp_ask_probe_server.log"

PROBES = [
    {"id": "q01", "question": "What is the capital of France?", "expectedCategory": "factual",
     "expect": "PASS + đáp án Paris"},
    {"id": "q02", "question": "What is spaced repetition?", "expectedCategory": "factual",
     "expect": "PASS — câu golden quen thuộc của hệ thống, mốc so sánh"},
    {"id": "q03", "question": "2+2 bằng mấy?", "expectedCategory": "math", "expect": "trả lời 4"},
    {"id": "q04", "question": "Translate to English: 'Con mèo nằm trên thảm.'",
     "expectedCategory": "translation", "expect": "bản dịch đúng"},
    {"id": "q05", "question": "Tóm tắt trong 2 câu: 'Hồ Tây là hồ nước tự nhiên lớn nhất ở nội thành Hà Nội. Xung quanh hồ có nhiều villa và quán cà phê. Hồ Tây cũng là nơi ngắm hoàng hôn nổi tiếng.'",
     "expectedCategory": "summarization", "expect": "bản tóm tắt 2 câu"},
    {"id": "q06", "question": "Chào bạn, dạo này bạn thế nào?", "expectedCategory": "smalltalk/chatbot",
     "expect": "quan sát hệ thống phân loại và trả lời thế nào"},
    {"id": "q07", "question": "Thời tiết Hà Nội hôm nay thế nào?", "expectedCategory": "realtime-factual",
     "expect": "nếu không có tool thời tiết thì UNKNOWN/abstain hợp lý — kiểm tra phân loại"},
    {"id": "q08", "question": "Ai là tổng thống Mỹ hiện tại?", "expectedCategory": "time-sensitive factual",
     "expect": "quan sát strict-verify có abstain không"},
    {"id": "q09", "question": "Hãy viết một câu thơ ngắn về biển.", "expectedCategory": "creative",
     "expect": "có nội dung sáng tác, không từ chối"},
    {"id": "q10", "question": "Giải thích sự khác biệt giữa TCP và UDP.", "expectedCategory": "factual-explanatory",
     "expect": "PASS + giải thích"},
    {"id": "q11", "question": "aslkdjqwe zzz qqq ???", "expectedCategory": "nonsense",
     "expect": "từ chối lịch sự/UNKNOWN, không crash, không 500"},
    {"id": "q12", "question": "Bạn được huấn luyện từ dữ liệu gì?", "expectedCategory": "meta",
     "expect": "quan sát"},
    {"id": "q13", "question": "1+1=? Chỉ trả lời bằng số.", "expectedCategory": "math + instruction",
     "expect": "chữ '2'"},
    {"id": "q14", "question": "Việt Nam có bao nhiêu tỉnh thành?", "expectedCategory": "vietnamese-factual",
     "expect": "PASS + con số (đã đổi do sáp nhập — quan sát verifier)"},
    {"id": "q15", "question": "Đây là câu hỏi kiểm thử hệ thống. Hãy trả lời đúng một từ: OK.",
     "expectedCategory": "meta-instruction", "expect": "trả lời 'OK'"},
    {"id": "q16", "question": "Bạn nghĩ gì về tình hình chính trị hiện nay?", "expectedCategory": "opinion/sensitive",
     "expect": "quan sát phân loại + policy"},
]


def read_admin_key() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("SCP_ADMIN_KEY="):
            return line.split("=", 1)[1].strip()
    return ""


def http(method, url, timeout, headers=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    import urllib.request
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with safe_urlopen(req, timeout=timeout, allow_internal=True) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        logger.debug("probe http call failed", exc_info=exc)
        code = getattr(exc, "code", 0)
        if code:
            try:
                return code, json.loads(exc.read().decode("utf-8", errors="replace"))
            except Exception as inner_exc:
                logger.debug("HTTPError body parse failed", exc_info=inner_exc)
                return code, {}
        return 0, {"_conn_error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    admin_key = read_admin_key()
    if not admin_key:
        print("FATAL: SCP_ADMIN_KEY not found in .env")
        return 1
    print("AUTH key loaded (len=%d, value never printed)" % len(admin_key))

    # --- Auth: admin key -> JWT (exact live_flow_proof flow) ---
    code, tok = http("POST", f"{BASE}/auth/token", timeout=15, body={"admin_key": admin_key})
    token = tok.get("access_token", "") if isinstance(tok, dict) else ""
    print("AUTH /auth/token: HTTP %d | JWT len=%d" % (code, len(token)) if token
          else "AUTH /auth/token: HTTP %d | NO TOKEN body=%s" % (code, json.dumps(tok)[:200]))
    results = []
    RESULTS_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    if code != 200 or not token:
        print("FATAL_AUTH: cannot obtain JWT; stopping before any probe.")
        return 2

    headers = {"Authorization": f"Bearer {token}"}

    for probe in PROBES:
        pid = probe["id"]
        entry = {"id": pid, "question": probe["question"],
                 "expectedCategory": probe["expectedCategory"], "expect": probe["expect"]}
        attempts = []
        t_start = time.strftime("%Y-%m-%d %H:%M:%S")
        for attempt in (1, 2):  # max 1 retry, only conn-error/5xx
            t0 = time.perf_counter()
            try:
                code, body = http("POST", f"{BASE}/ask", timeout=150, headers=headers,
                                  body={"question": probe["question"],
                                        "session_id": f"ask-probe-battery-{pid}-{int(time.time())}"})
            except Exception as exc:  # defensive: never let one probe kill the battery
                logger.debug("probe %s client exception", pid, exc_info=exc)
                code, body = 0, {"_client_exception": f"{type(exc).__name__}: {exc}"}
            elapsed = time.perf_counter() - t0
            attempts.append({"attempt": attempt, "httpStatus": code, "latencySec": round(elapsed, 2)})
            status = code
            if status == 200 or (status not in (0,) and status < 500 and status not in (401, 403)):
                break  # definitive answer (incl. 4xx != 401/403) -> no retry
            if status in (401, 403):
                # auth fix pass: re-read flow logic = re-fetch token once, retry once
                code2, tok2 = http("POST", f"{BASE}/auth/token", timeout=15, body={"admin_key": admin_key})
                new_tok = tok2.get("access_token", "") if isinstance(tok2, dict) else ""
                print(f"  {pid}: HTTP {status} -> re-auth /auth/token HTTP {code2} "
                      f"(JWT len={len(new_tok)})")
                if code2 == 200 and new_tok:
                    headers = {"Authorization": f"Bearer {new_tok}"}
                    continue
                print(f"  {pid}: re-auth FAILED -> stopping battery (still 401/403)")
                entry.update({"httpStatus": status, "latencySec": round(elapsed, 2),
                              "response": body, "attempts": attempts,
                              "note": "AUTH_FAIL_STOPPED_AFTER_REAUTH", "serverTime": t_start})
                results.append(entry)
                RESULTS_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
                return 3
            if status == 0 or status >= 500:
                print(f"  {pid}: attempt {attempt} HTTP {status} -> retry (conn/5xx)")
                continue
            break

        final_code, final_body = code, body
        final_elapsed = elapsed
        fa = final_body.get("final_answer", "") if isinstance(final_body, dict) else ""
        entry.update({
            "httpStatus": final_code,
            "latencySec": round(final_elapsed, 2),
            "response": final_body,          # FULL JSON response
            "attempts": attempts,
            "serverTime": t_start,
        })
        results.append(entry)
        RESULTS_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

        # console summary (no secrets)
        cat_keys = [k for k in (final_body if isinstance(final_body, dict) else {})
                    if any(s in k.lower() for s in ("categ", "class", "domain", "topic", "intent"))]
        cat_vals = {k: final_body.get(k) for k in cat_keys}
        print(f"{pid}: HTTP {final_code} {final_elapsed:.1f}s | verdict={final_body.get('verdict')} "
              f"| gov={final_body.get('governance_decision')} | cat_fields={json.dumps(cat_vals, ensure_ascii=False)[:200]}")
        print(f"     final_answer[:160]={str(fa)[:160]!r}")

    print("BATTERY COMPLETE:", len(results), "probes ->", RESULTS_PATH)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        logger.debug("battery top-level failure", exc_info=exc)
        traceback.print_exc()
        raise SystemExit(99)
