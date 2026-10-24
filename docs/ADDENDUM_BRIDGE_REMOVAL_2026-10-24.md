# ADDENDUM — Cập nhật gói tài liệu SCP 2026-10-24 sau khi LLM Bridge bị xóa sổ

> Tài liệu này sửa 3 điểm đã lỗi thời trong gói `SCP_TAI_LIEU_2026-10-24.zip`
> (10 phần) — các phần được viết trên baseline `main @ 451ebdf` (squash merge
> PR #80, 16:22 +07), **trước** quyết định xóa hoàn toàn LLM Bridge của owner
> (commit `d0125739`, sau đó main đã fast-forward lên `778832c1` chứa removal).

## 1. LLM Bridge KHÔNG còn tồn tại — mọi khuyến nghị giữ bridge hết hiệu lực

| Tài liệu ghi | Trạng thái thực tế sau removal |
|---|---|
| PHẦN 1 §4.8 "LLM Bridge và gateway" (jiti loader, codegen TASK_MODEL_MAP) | Bridge đã bị XÓA: `mini-services/llm-bridge/` biến mất cùng 8 reality tests `4-d-{004,005,006,009,013,022,023,029}`, `test_llm_bridge_cache_auth.py`, codegen script + test |
| PHẦN 2 Wave 1 "giữ `TASK_FREE_FALLBACK_MAP` + codegen hiện tại" | Codegen đã xóa theo bridge. Nguồn map duy nhất còn lại: `TASK_FREE_FALLBACK_MAP` trong `scp/llm_gateway/providers.py` (Python) — Brain Task Registry (PHẦN 3 §3.4) sẽ là nguồn chuẩn hóa cuối |
| PHẦN 3 §3.10 serving runtime lựa chọn 2 "LLM Bridge 8081" | Đã bỏ khỏi phương án serving. Thứ tự serving mới: (1) API ngoài qua gateway hiện tại, (2) vLLM self-host (đăng ký port qua registry), (3) adapter bọc RealityJudge cũ (`adjudicated_by=reality_judge_legacy`) |
| PHẦN 6 P0-02 "Đóng gói LLM Bridge độc lập hoặc loại khỏi đường pilot" | Đã chọn phương án LOẠI — nhưng mạnh hơn đề xuất: xóa sổ toàn bộ (code + tests + port registry), không chỉ tắt khỏi pilot |
| `scp/core/ports.py` / `docs/architecture/PORTS.md` còn 8081/11434 | Đã xóa cả hai hàng + constants `LLM_BRIDGE`; PORTS.md ghi quy tắc "retired — cấm tái dùng 8081/11434" |

## 2. Cập nhật timeline

| Thời điểm | Sự kiện (bổ sung vào PHẦN 1 §2) |
|---|---|
| Sau merge PR #80 | Owner ra quyết định xóa sổ LLM Bridge (Zero-Bun hoàn tất) |
| Commit `d0125739` | Xóa 18 file git-tracked (7 code + 8 reality tests + 1 contract test + codegen script/test) + sửa 22 file (start/stop scripts, ports, PORTS.md, README, HIDDEN_COUPLINGS, dashboard, monitors). Reality suite: 76 → 68 tests (65 pass, 3 skip scheduler-node, 0 fail) |
| Merge `origin/main` vào branch (`778832c1`) + fast-forward main `451ebdf1 → 778832c1` | main giờ chứa bridge removal + mọi fix sau merge (ledger stale bullet, .dockerignore fresh-install fixes) |

## 3. Ledger đã sửa theo phát hiện của chính gói tài liệu

PHẦN 1 §5 phát hiện đúng: bullet tóm tắt ledger ghi stale "RESOLVED: 10" trong
khi bảng có 27. **Đã sửa** (`d34eb290`): bullet giờ liệt kê đủ 27 mục RESOLVED.
Ghi chú kỹ thuật: drift test `test_hidden_couplings_ledger.py` chỉ phủ bảng,
không phủ bullet tóm tắt — nếu muốn triệt để, cần thêm 1 test parse bullet
summary (để backlog Wave tối ưu).

## 4. Trạng thái phân nhánh hiện tại

- `main` = `778832c1` (merge commit): chứa PR #80 + bridge removal + ledger fix
- Branch `feat/runtime-audit-brain-console` = cùng `778832c1` (đã sync)
- Baseline Brain Layer P0 dùng `778832c1` (không phải `451ebdf` như PHẦN 2/3 ghi)

## 5. Ghi nhận còn đúng của gói tài liệu (không thay đổi)

- Đánh giá về PR #80 (PHẦN 1 §4.1→4.7, §5) — chính xác với người thực hiện.
- 25 coupling OPEN và hệ quả với Brain Layer (PHẦN 1 §5, PHẦN 4) — vẫn đúng.
- Thiết kế Brain Layer (PHẦN 3) — giữ nguyên trừ serving option bridge.
- Kế hoạch tối ưu Wave 0-6 (PHẦN 2) — giữ nguyên trừ Wave 1 dòng codegen.
- Kế hoạch pilot 90 ngày (PHẦN 6) — giữ nguyên; P0-02 đã thực thi theo hướng loại.
