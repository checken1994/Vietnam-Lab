"""Seed script for SCP Lesson Ledger (10 Real Lessons from tonight's session).

Luật 12 & 15:
- Mỗi bài học = bản ghi JSONL trong data/lessons/lessons.jsonl
- Trạng thái mặc định: HYPOTHESIS
- Chứa Chuỗi Tại Sao 5 mục theo Luật Owner 30/10
- Bằng chứng và lineage thực nghiệm thật
- Idempotent: chạy lại không nhân đôi bản ghi (deduped theo lesson_id)
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

# Add repo root to sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scp.learning.lesson_ledger import get_ledger_file_path, record_lesson

logger = logging.getLogger("scp.learning.seed_lesson")

SEED_LESSONS = [
    {
        "lesson_id": "L-20261031-shadow-llm-unauthorized",
        "trigger": {
            "source": "audit",
            "sha": "c9aa96e7",
            "file": "scp/runtime/shadow_judge.py",
            "test_name": "T05_gateway_audit",
        },
        "observation": "Trong shadow mode, pipeline có thể gửi egress request ra LLM bên ngoài mà không kiểm tra cờ cấp quyền tường lửa nếu không có chốt chặn fail-closed.",
        "hypothesis": "Cơ chế shadow executor mặc định kích hoạt egress mà thiếu chốt kiểm tra quyền hạn mạng cấp sandbox (Fail-Closed).",
        "evidence": [
            {
                "type": "probe",
                "command": "python -m pytest tests/T05_gateway/test_judge_sync_crosscheck_alive.py",
                "result_hash": "STATIC_AUDIT_SHADOW_JUDGE_SEAM",
            }
        ],
        "status": "HYPOTHESIS",
        "independent_check": {"by": "Checker audit", "verdict": "UNKNOWN", "at": "2026-10-31T02:00:00Z"},
        "why_chain": [
            "Gốc lỗi sai: Shadow evaluator gọi API ngoài không qua Policy Pipeline kiểm duyệt token egress.",
            "Vì sao sửa đúng Reality: Cần chốt chặn ở mức Network Socket / Mocking layer để ngăn rò rỉ dữ liệu khi chạy ngầm.",
            "Vì sao tin bằng chứng lineage: Phân tích cấu trúc luồng gọi trong shadow_judge.py.",
            "Điều gì làm đổi quyết định anti-Goodhart: Không được fake response trong shadow mode để làm xanh test.",
            "Chỗ UNKNOWN: UNKNOWN_WITH_REASON: Chưa có test mang tên test_shadow_egress_unauthorized trong tests/T05_gateway/; đây là nhận diện rủi ro kiến trúc.",
        ],
        "next_action": "shadow",
        "kill_switch_ref": "owner-on-top",
    },
    {
        "lesson_id": "L-20261031-keyword-regex-overrides-chat",
        "trigger": {
            "source": "e2e",
            "sha": "c9aa96e7",
            "file": "scp/router.py",
            "question": "Câu hỏi hội thoại chứa từ khóa kỹ thuật",
        },
        "observation": "Câu hỏi hội thoại có từ khóa kỹ thuật đơn lẻ có nguy cơ bị router l0-keyword bắt nhầm sang nhánh factual/code thay vì chatbot/conversational.",
        "hypothesis": "Quy tắc khớp regex từ khóa có độ ưu tiên cao hơn và thực thi trước khi xem xét ý định toàn thể của câu (intent priority inversion).",
        "evidence": [
            {
                "type": "docker",
                "command": "docker exec scp-full-backend-1 python /tmp/e2e_runner_extra.py crosscheck",
                "result_hash": "ROUTING_ANALYSIS_KEYWORD_PRIORITY",
            }
        ],
        "status": "HYPOTHESIS",
        "independent_check": {"by": "e2e observation", "verdict": "UNKNOWN", "at": "2026-10-31T02:00:00Z"},
        "why_chain": [
            "Gốc lỗi sai: Router l0 chỉ kiểm tra từng từ đơn lẻ thay vì cấu trúc ngữ pháp câu.",
            "Vì sao sửa đúng Reality: Cần chuyển sang phân loại cấu trúc cú pháp hoặc embedding intent trước khi tra regex.",
            "Vì sao tin bằng chứng lineage: Thử nghiệm thực tế với các câu hỏi hội thoại chứa từ khóa chuyên môn.",
            "Điều gì làm đổi quyết định anti-Goodhart: Không thêm regex đặc thù (vi phạm Luật 14).",
            "Chỗ UNKNOWN: UNKNOWN_WITH_REASON: Ký hiệu 'ca 13' là nhãn thảo luận của phiên trước, không phải test ID trong repo.",
        ],
        "next_action": "watch",
        "kill_switch_ref": "owner-on-top",
    },
    {
        "lesson_id": "L-20261031-b1-sqrt144-fastmath-bypass",
        "trigger": {
            "source": "e2e",
            "sha": "c9aa96e7",
            "file": "scp/fast_math.py",
            "question": "Căn bậc hai của 144?",
        },
        "observation": "Câu hỏi căn bậc hai tốn 22.325s (server: 22322.77ms ở Pass 1) và 16.892s (server: 16889ms ở buyer run) vì rơi vào full LLM thay vì chạy fast-math dưới 1s như 1+1 (0.987s).",
        "hypothesis": "Bộ phân tích biểu thức số học chỉ nhận diện ký hiệu toán học số (sqrt, ^, *, +) mà thiếu tầng phân tích cú pháp ngôn ngữ tự nhiên cho phép toán căn bậc hai.",
        "evidence": [
            {
                "type": "probe",
                "command": "docker exec scp-full-backend-1 python /tmp/e2e_runner_extra.py buyer",
                "result_hash": "LATENCY_22322MS_PASS1_16889MS_BUYER",
            }
        ],
        "status": "HYPOTHESIS",
        "independent_check": {"by": "E2E extra runner JSON (/tmp/e2e_extra_results.json)", "verdict": "PASS", "at": "2026-10-30T18:45:00Z"},
        "why_chain": [
            "Gốc lỗi sai: Bộ nhận diện toán học chỉ hỗ trợ ký tự đại số, không bao hàm mẫu ngữ pháp số học tự nhiên.",
            "Vì sao sửa đúng Reality: Cần bộ parser ngữ pháp toán học tổng quát (MathGrammarParser), không hardcode regex câu cụ thể.",
            "Vì sao tin bằng chứng lineage: Header x-scp-latency-ms: 22322.77 và 16889ms chứng minh rơi vào LLM cloud thay vì fast path.",
            "Điều gì làm đổi quyết định anti-Goodhart: Tuyệt đối không thêm regex r'căn bậc hai của 144' (Luật 14).",
            "Chỗ UNKNOWN: Các hàm toán học cao cấp khác (logarit, tích phân) có nên đưa vào fast-path hay để LLM xử lý?",
        ],
        "next_action": "autofix-lane",
        "kill_switch_ref": "owner-on-top",
    },
    {
        "lesson_id": "L-20261031-wsl2-9p-mount-stall",
        "trigger": {
            "source": "runtime",
            "sha": "c9aa96e7",
            "file": "docker-compose.full.yml",
        },
        "observation": "Chạy full test suite qua volume mount 9p từ Windows host bị nghẽn I/O treo hơn 3 giờ. Khi build trực tiếp mã nguồn vào image container (embedded), 6144 tests hoàn tất: 6130 passed, 12 failed trong 808.02s.",
        "hypothesis": "Giao thức 9p của WSL2 không đáp ứng được tần suất truy cập file cực lớn từ pytest và SQLite WAL locking khi kiểm thử phân tán.",
        "evidence": [
            {
                "type": "docker",
                "command": "docker run scp-test:p8 pytest tests/ (embedded vs mount)",
                "result_hash": "6130P_12F_808.02S_P8_EMB2",
            }
        ],
        "status": "HYPOTHESIS",
        "independent_check": {"by": "p8-baseline-done.md:149", "verdict": "PASS", "at": "2026-10-30T17:30:00Z"},
        "why_chain": [
            "Gốc lỗi sai: Sử dụng bind mount qua ranh giới Windows/Linux FS cho khối lượng test I/O chuyên sâu.",
            "Vì sao sửa đúng Reality: Đóng gói toàn bộ source vào container image để thực thi native trên Linux ext4.",
            "Vì sao tin bằng chứng lineage: File p8-baseline-done.md:149 ghi nhận đúng 6130 passed, 12 failed trong 808.02s.",
            "Điều gì làm đổi quyết định anti-Goodhart: Không chia nhỏ test suite để chạy đối phó; giữ nguyên 6144 tests.",
            "Chỗ UNKNOWN: Tác động của Docker Desktop virtiofs so với 9p truyền thống trên Windows 11.",
        ],
        "next_action": "watch",
        "kill_switch_ref": "owner-on-top",
    },
    {
        "lesson_id": "L-20261031-bind-v13db-readonly-breaks-tests",
        "trigger": {
            "source": "audit",
            "sha": "c9aa96e7",
            "file": "docker-compose.test.yml",
        },
        "observation": "Mount v13.db ở chế độ read-only (:ro) vào container khiến 30 bài test văng Error (so với 0 error của emb2) do các hàm test cần ghi WAL hoặc cập nhật metadata.",
        "hypothesis": "Nhiều test case giả định database có thể ghi tạm thời hoặc kích hoạt PRAGMA WAL trong quá trình thực thi, mount readonly vi phạm giả định này.",
        "evidence": [
            {
                "type": "docker",
                "command": "docker run -v /v13.db:/app/data/v13.db:ro ...",
                "result_hash": "30_ERRORS_READONLY_DB",
            }
        ],
        "status": "HYPOTHESIS",
        "independent_check": {"by": "p8-emb3.log audit", "verdict": "PASS", "at": "2026-10-30T19:00:00Z"},
        "why_chain": [
            "Gốc lỗi sai: Áp đặt thuộc tính read-only ở tầng mount hạ tầng cho file mà application code kỳ vọng read-write.",
            "Vì sao sửa đúng Reality: Copy DB vào thư mục nội bộ của container thay vì bind mount readonly từ host.",
            "Vì sao tin bằng chứng lineage: So sánh diff giữa emb2 (0 errors) và emb3 (30 errors do operational error).",
            "Điều gì làm đổi quyết định anti-Goodhart: Không sửa code application để nuốt lỗi readonly DB.",
            "Chỗ UNKNOWN: Danh sách chính xác các test nào cần ghi vào v13.db và test nào chỉ đọc.",
        ],
        "next_action": "none",
        "kill_switch_ref": "owner-on-top",
    },
    {
        "lesson_id": "L-20261031-compose-command-missing-entrypoint",
        "trigger": {
            "source": "runtime",
            "sha": "c9aa96e7",
            "file": "compose.override.start.yml",
        },
        "observation": "Cấu hình command: ['8000'] khiến container crash với lỗi `exec: '8000': not found` do image Dockerfile mới không set ENTRYPOINT mặc định.",
        "hypothesis": "Trong Docker Compose, thuộc tính `command` thay thế toàn bộ CMD; nếu image thiếu ENTRYPOINT thì giá trị đầu tiên trong command được dùng làm executable.",
        "evidence": [
            {
                "type": "docker",
                "command": "docker compose -f docker-compose.full.yml -f compose.override.start.yml up",
                "result_hash": "EXIT_127_EXEC_8000_NOT_FOUND",
            }
        ],
        "status": "HYPOTHESIS",
        "independent_check": {"by": "docker compose logs", "verdict": "PASS", "at": "2026-10-30T23:50:00Z"},
        "why_chain": [
            "Gốc lỗi sai: Giả định image luôn có ENTRYPOINT ['python', '-m', 'scp'].",
            "Vì sao sửa đúng Reality: Khai báo tường minh đầy đủ: `command: ['python', '-m', 'scp', '8000']`.",
            "Vì sao tin bằng chứng lineage: Container scp-full-backend-1 chuyển trạng thái Up (healthy) ngay sau khi sửa.",
            "Điều gì làm đổi quyết định anti-Goodhart: Luôn chỉ định binary thực thi đầy đủ, không dựa dẫm vào implicit entrypoint.",
            "Chỗ UNKNOWN: Các image phụ khác (desktop, worker) có bị phụ thuộc entrypoint ngầm hay không?",
        ],
        "next_action": "watch",
        "kill_switch_ref": "owner-on-top",
    },
    {
        "lesson_id": "L-20261031-git-pat-push-basic-auth",
        "trigger": {
            "source": "runtime",
            "sha": "c9aa96e7",
            "file": "git push command",
        },
        "observation": "Dùng PAT trong header Authorization Bearer bị GitHub từ chối 401 khi push git qua HTTPS.",
        "hypothesis": "Giao thức Git Smart HTTP yêu cầu HTTP Basic Authentication, trong đó username là 'x-access-token' và password là giá trị token.",
        "evidence": [
            {
                "type": "git",
                "command": "git push https://x-access-token:$PAT@github.com/...",
                "result_hash": "PUSH_SUCCESS_38_COMMITS",
            }
        ],
        "status": "HYPOTHESIS",
        "independent_check": {"by": "Hub git push lineage", "verdict": "PASS", "at": "2026-10-30T22:30:00Z"},
        "why_chain": [
            "Gốc lỗi sai: Nhầm lẫn giữa cơ chế xác thực REST API (Bearer) và Git Smart HTTP (Basic Auth).",
            "Vì sao sửa đúng Reality: Cấu hình đúng format URL remote với basic credentials.",
            "Vì sao tin bằng chứng lineage: 38 commits đã được đồng bộ lên remote GitHub main.",
            "Điều gì làm đổi quyết định anti-Goodhart: Không lưu trữ raw PAT vào file cấu hình git tracked.",
            "Chỗ UNKNOWN: Hạn sử dụng của GitHub fine-grained token và chính sách cấp quyền cho repository push.",
        ],
        "next_action": "none",
        "kill_switch_ref": "owner-on-top",
    },
    {
        "lesson_id": "L-20261031-claude-desktop-cdp-focus-needed",
        "trigger": {
            "source": "runtime",
            "sha": "c9aa96e7",
            "file": "C:/Users/check/.council-loop/cdp_send.js",
        },
        "observation": "Gửi phím Enter qua CDP vào Electron App của Claude không kích hoạt gửi tin nhắn, văn bản bị kẹt trong draft ảo.",
        "hypothesis": "Khung soạn thảo ProseMirror của Claude yêu cầu sự kiện focus chuột vật lý (click focus) và chuỗi sự kiện composition trước khi chấp nhận Enter submit.",
        "evidence": [
            {
                "type": "probe",
                "command": "node C:/Users/check/.council-loop/cdp_send.js",
                "result_hash": "CDP_CLICK_AND_DISPATCH_OK",
            }
        ],
        "status": "HYPOTHESIS",
        "independent_check": {"by": "council-loop worker", "verdict": "PASS", "at": "2026-10-30T21:00:00Z"},
        "why_chain": [
            "Gốc lỗi sai: Giả định element textarea chuẩn của web truyền thống, bỏ qua state machine của modern rich-text editor.",
            "Vì sao sửa đúng Reality: Kích hoạt bounding rect click + dispatch sự kiện chuột trước khi gửi phím.",
            "Vì sao tin bằng chứng lineage: Tin nhắn được đưa vào luồng chat thực tế của Claude Desktop.",
            "Điều gì làm đổi quyết định anti-Goodhart: Không bypass bằng cách dán thẳng qua clipboard mà không có sự kiện tương tác.",
            "Chỗ UNKNOWN: Khi Claude Desktop cập nhật phiên bản Electron mới, cấu trúc DOM có thay đổi không?",
        ],
        "next_action": "watch",
        "kill_switch_ref": "owner-on-top",
    },
    {
        "lesson_id": "L-20261031-agent-idle-after-delivery",
        "trigger": {
            "source": "checker",
            "sha": "c9aa96e7",
            "file": "GA.md",
        },
        "observation": "AI Agent rơi vào trạng thái dừng (idle) sau khi hoàn thành tạo file done, không chủ động kiểm tra các nhiệm vụ tiếp theo trong hàng đợi council-loop.",
        "hypothesis": "Agent vận hành theo mô hình turn-based của LLM, thiếu cơ chế heartbeat nội tại hoặc scheduler tự đánh thức khi chưa có prompt mới từ user.",
        "evidence": [
            {
                "type": "probe",
                "command": "user prompt alert: 'AG - IDLE 44 VOI 2 VIEC...'",
                "result_hash": "IDLE_GAP_DETECTED",
            }
        ],
        "status": "HYPOTHESIS",
        "independent_check": {"by": "Checker feedback", "verdict": "PASS", "at": "2026-10-31T00:30:00Z"},
        "why_chain": [
            "Gốc lỗi sai: Thiếu vòng lặp tự quản (Continuous Operations Loop) kích hoạt chuỗi task liên tục.",
            "Vì sao sửa đúng Reality: Bổ sung quy trình kiểm tra hàng đợi inbox và backlog ngay trước khi kết thúc turn.",
            "Vì sao tin bằng chứng lineage: Lời nhắc từ user và thời gian chênh lệch giữa các commit.",
            "Điều gì làm đổi quyết định anti-Goodhart: Không tạo vòng lặp vô tận tiêu tốn quota khi không có việc.",
            "Chỗ UNKNOWN: Mức độ tự chủ tối ưu giữa việc tự kéo việc tiếp theo và việc dừng lại xin ý kiến con người.",
        ],
        "next_action": "watch",
        "kill_switch_ref": "owner-on-top",
    },
    {
        "lesson_id": "L-20261031-powershell-pipe-swallows-output",
        "trigger": {
            "source": "runtime",
            "sha": "c9aa96e7",
            "file": "PowerShell pipeline execution",
        },
        "observation": "PowerShell pipeline `| Select-String` nuốt mất stdout/stderr khi chạy tiến trình test nặng P8, khiến 3 lần chạy không thấy tiến độ và mất log khi bị ngắt.",
        "hypothesis": "Bộ đệm stream (stdout buffer) của PowerShell 5.1/7 giữ lại dữ liệu trong buffer nội bộ mà không flush lập tức, nếu tiến trình con thoát hoặc bị kill, toàn bộ buffer đuôi bị drop.",
        "evidence": [
            {
                "type": "probe",
                "command": "python -m pytest ... | Tee-Object ...",
                "result_hash": "TRUNCATED_BUFFER_MISSING_TAIL",
            }
        ],
        "status": "HYPOTHESIS",
        "independent_check": {"by": "run_p8_base.ps1 refactor", "verdict": "PASS", "at": "2026-10-30T16:00:00Z"},
        "why_chain": [
            "Gốc lỗi sai: Lạm dụng pipeline của PowerShell cho các tiến trình native sinh output lớn.",
            "Vì sao sửa đúng Reality: Chuyển sang redirect file trực tiếp `> file.log 2>&1` và dùng thread/process riêng đọc file.",
            "Vì sao tin bằng chứng lineage: File p8-emb2.log thu thập đủ 808.02s và 6144 dòng test không bị thiếu.",
            "Điều gì làm đổi quyết định anti-Goodhart: Không dùng sleep để chờ pipe xả dữ liệu.",
            "Chỗ UNKNOWN: Tùy chọn $OutputEncoding và PYTHONUNBUFFERED=1 có khắc phục được hoàn toàn trên mọi phiên bản PowerShell không?",
        ],
        "next_action": "none",
        "kill_switch_ref": "owner-on-top",
    },
]


def seed_lessons(ledger_path: Path | None = None) -> int:
    """Seed the 10 real lessons into the ledger idempotently."""
    target_path = ledger_path or get_ledger_file_path()
    print(f"Target ledger file: {target_path}")

    existing_ids = set()
    if target_path.exists():
        with open(target_path, "r", encoding="utf-8") as f:
            for line_idx, line in enumerate(f, 1):
                sline = line.strip()
                if not sline:
                    continue
                try:
                    obj = json.loads(sline)
                    if "lesson_id" in obj:
                        existing_ids.add(obj["lesson_id"])
                except json.JSONDecodeError as err:
                    print(f"Warning: corrupt json at line {line_idx} in {target_path}: {err}")

    count = 0
    for lesson_data in SEED_LESSONS:
        lid = lesson_data["lesson_id"]
        if lid in existing_ids:
            print(f"Lesson [{lid}] already exists, skipping.")
            continue

        record_lesson(
            observation=lesson_data["observation"],
            hypothesis=lesson_data["hypothesis"],
            trigger=lesson_data["trigger"],
            evidence=lesson_data["evidence"],
            lesson_id=lid,
            status=lesson_data["status"],
            independent_check=lesson_data["independent_check"],
            why_chain=lesson_data["why_chain"],
            next_action=lesson_data["next_action"],
            kill_switch_ref=lesson_data["kill_switch_ref"],
            ledger_path=target_path,
        )
        print(f"Recorded lesson: {lid}")
        count += 1

    print(f"Seeding completed. Total new lessons added: {count}")
    return count


if __name__ == "__main__":
    seed_lessons()
