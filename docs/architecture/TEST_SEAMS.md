# TEST_SEAMS.md — Bảng seam kiểm thử chính thức của scp.api_server

> [HC-61 2026-10-23] Tài liệu này là nguồn sự thật cho các seam động mà test
> monkeypatch. Mục đích: (1) test mới patch ĐÚNG namespace, (2) khi symbol bị
> xóa/dời, test pin `tests/internal/test_test_seams_docs.py` văng buộc cập
> nhật bảng này cùng lúc.

## Quy tắc chọn namespace khi patch (bắt buộc đọc trước)

Monkeypatch chỉ có tác dụng tại **namespace nơi production ĐỌC binding**:

| Kiểu import ở production | Patch đúng chỗ |
|---|---|
| Import top-level: `from scp.api_server_parts.helpers import get_judge` trong module `M` | `scp.api_server_parts.M.get_judge` (call-site). Patch `helpers.get_judge` **không** ảnh hưởng `M` |
| Import runtime (bên trong hàm): `from scp.llm_gateway import get_gateway` | `scp.llm_gateway.get_gateway` (canonical). Patch call-site vô dụng vì import lại mỗi lần gọi |
| Binding tái xuất có wrapper riêng (vd `scp/api/_shared.py::get_judge` delegate helpers lúc gọi) | Patch binding của consumer (`scp.api._shared.get_judge`) hoặc canonical — đọc wrapper để chọn |

Seam tồn tại **có chủ đích cho testability** — không đổi production code để
"không còn seam"; nếu phải đổi, giữ tên cũ backward-compat (mẫu:
`api_server._pc_kill_switch_engaged` delegate `kill_switch.is_engaged`).

## Bảng seam chính thức

Cột Symbol là đường dẫn import đầy đủ — test pin
`tests/internal/test_test_seams_docs.py` parse bảng này và import từng symbol.

| Symbol (module.attr) | Loại seam | Vì sao tồn tại | Ai ĐƯỢC patch (test) | Lưu ý namespace |
|---|---|---|---|---|
| `scp.api_server_parts.helpers.get_judge` | Canonical judge accessor (helpers.py:268) | Điểm chặn duy nhất lấy RealityJudge singleton để test thay judge | test_ask_pipeline_refactor.py:229,273; mọi code làm runtime import từ helpers | Consumers import top-level phải patch call-site của họ, xem 2 dòng dưới |
| `scp.api_server_parts._ask_impl.get_judge` | Call-site binding (import top từ helpers tại _ask_impl.py:23) | Test đi qua `handle_ask`/`_ask_impl` phải patch đây — patch `helpers.get_judge` không tới được | test_critical_chain_severed.py:509,711 | Patch nhầm `helpers.get_judge` cho path này = mock bị bỏ qua (anti-pattern đã tránh) |
| `scp.api._shared.get_judge` | Wrapper riêng (_shared.py:45) delegate helpers lúc gọi | Consumer của `_shared` (openai_compat.py:28 import top) cần binding riêng | test_downstream_abstain_handling.py:130-167; test_security.py:251 (via `import scp.api._shared as shared`) | Patch canonical `helpers.get_judge` không ảnh hưởng binding này |
| `scp.api_server.get_judge` | Call-site binding (api_server.py import từ helpers) | Test health/boot cần judge giả mà không khởi judge thật | test_p1_boundary_security.py:423; test_health_minimal_identity.py:132,153 | Chỉ đúng cho code nằm trong namespace api_server |
| `scp.api_server._pc_kill_switch_engaged` | Backward-compat delegate (api_server.py:161) → `scp.security.kill_switch.is_engaged()` | Giữ tên cũ cho chat.py/openai_compat.py/middleware import lúc runtime; test thay admission gate | test_chat_ws_w8_abstain_delivery.py:99 | Consumers import runtime từ `scp.api_server` → patch namespace này có tác dụng; để giả lập trạng thái thật hơn, patch `scp.security.kill_switch.is_engaged` hoặc set env `SCP_KILL_SWITCH` |
| `scp.security.kill_switch.is_engaged` | Canonical kill-switch gate (kill_switch.py) | Seam hợp nhất HC-22: file flag + env, fail-closed | test_kill_switch_unified.py (trực tiếp + qua wrapper) | Ưu tiên seam này cho test mới về kill-switch |
| `scp.middleware.runtime_skill_guard._pc_kill_switch_engaged_safe` | Wrapper fail-closed trong middleware (runtime_skill_guard.py:103) | Middleware cần đọc kill-switch an toàn, test giả lập engaged/disengaged | test_kill_switch_unified.py:226-253 | Patch wrapper này chỉ ảnh hưởng middleware |
| `scp.llm_gateway.get_gateway` | Canonical gateway accessor | Test thay LLM gateway cho mọi code runtime-import từ `scp.llm_gateway` | test_cascade.py:34; test_ask_w3_identity_postguard.py:74 (hermetic guard) | Consumer import top-level (evaluation_routes) phải patch call-site — dòng dưới |
| `scp.api.routes.evaluation_routes.get_gateway` | Call-site binding của get_gateway | Test crash/tamper/dead gateway tại route evaluation | test_critical_chain_severed.py:550,582,734 | Patch `scp.llm_gateway.get_gateway` không tới được binding này |
| `scp.api_server_parts._ask_impl._async_fact_check` | Call-site binding fact-check | Hermetic fixture tắt fact-check network trong test pipeline | test_ask_w3_identity_postguard.py:77 | — |
| `scp.api_server_parts.helpers.start_crawl_thread` | Module-level snapshot (helpers.py:86, bind lazily từ `scp.security.attack_crawler`) | Chặn thread crawl thật trong unit test | test_helpers_fast_learning_gate.py:60,89,110,135 | helpers giữ snapshot globals nên patch namespace helpers là đúng |
| `scp.api_server_parts.helpers.AttackCrawler` | Module-level snapshot (helpers.py:85) | Thay crawler class bằng stub, không IO thật | test_helpers_fast_learning_gate.py:59 | — |
| `scp.api_server_parts.helpers.start_fast_learning_thread` / `start_learning_thread` | Module attrs của helpers | Chặn fast-learning background thread thật | test_helpers_fast_learning_gate.py:89,135 | — |
| `scp.api_server_parts.helpers._judge` / `_predictive_engine` / `_attack_crawler` / `_cross_language_learner` | Module globals singleton mà `get_judge()` mutate | Test snapshot/phục hồi singleton để monkeypatch restore sạch | test_helpers_fast_learning_gate.py:61-72 | Đừng patch ở api_server namespace — state nằm ở helpers |

## Seam patch NHẦM namespace (đã audit, kết quả)

Quét `rg "monkeypatch.setattr" tests/ | grep -iE "api_server|get_judge|_scp_helpers|gateway|helpers"`
tại ec4cc1eb: **không phát hiện test nào patch sai namespace đang kích hoạt**
— các test đi qua `_ask_impl` đều patch `_ask_impl.get_judge`
(test_critical_chain_severed.py:509,711), test qua `_shared` patch binding
`_shared`, test qua route evaluation patch `evaluation_routes.get_gateway`.
Rủi ro còn lại là **test mới** patch canonical thay vì call-site (mock âm thầm
không áp dụng) → quy tắc ở đầu trang + test pin bên dưới là hàng rào.

## Bảo trì

- Thêm seam mới → thêm 1 dòng vào bảng trên (Symbol phải full-path import được).
- Xóa/dời symbol → cập nhật bảng CÙNG commit với code; nếu không,
  `tests/internal/test_test_seams_docs.py` văng (anti-placebo).
- Test pin parse các bảng markdown có cột đầu là `` | `module.attr` | `` —
  giữ đúng format cột.
