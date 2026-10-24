# SINGLETON_LIFECYCLE — import-time singletons của `scp/api_server.py` (HC-09/10/11, HC-41)

> B4-W1 · 2026-10-23 · branch `feat/runtime-audit-brain-console` @ 6d365d0e
> Ràng buộc nền: cơ chế rebind F-18 (`_rebind_part_function`) tạo lại hàm của
> các part module với `__globals__ = api_server.globals()` — hàm rebind đọc
> singleton bằng LOAD_GLOBAL, mà LOAD_GLOBAL **KHÔNG** đi qua module
> `__getattr__` (PEP 562 chỉ chặn attribute access `mod.name`). Bất kỳ
> singleton nào nằm trong namespace rebind đều KHÔNG thể lazify.

## Bảng lifecycle (state sau patch B4-W1)

| Object | Tạo tại | Owner lifecycle | Test seam | Vì trạng thái này |
|---|---|---|---|---|
| `_REQUEST_RUN_LEDGER` | import-time, `api_server.py:107` | Process-lifetime; không reset/close (append-only ledger); shutdown không dọn | Tests đọc trực tiếp; `@traced_request(_REQUEST_RUN_LEDGER)` tại def-time line 629 | Import-time BẮT BUỘC: decorator `@traced_request(...)` chạy lúc import, cần instance ngay |
| `_ASK_KERNEL_ADAPTERS` + lock | import-time (dict rỗng) | Lười nạp theo (model, lane); lock bảo vệ | `app_state` tests | Dict rỗng rẻ — không cần lazy |
| `_judge` / `_judge_lock` | import-time = `None` + Lock | Dựng lười trong `get_judge()` (helpers part) dưới lock; không close | Judge seam monkeypatch qua api_server globals | Đã là lazy-by-design từ trước (HC-09 pattern chuẩn) |
| `_multi_turn_tracker` | import-time `MultiTurnTracker()` | Process-lifetime; reset qua route/endpoint test (clear state) | Tests monkeypatch `api_server._multi_turn_tracker` | Rebind machinery LOAD_GLOBAL (`_ask_impl`) — không lazify được |
| `_image_detector` | import-time | Process-lifetime | Tests thay instance qua setattr | Rebind machinery (`_ask_impl`) — không lazify được |
| `_voice_detector` | import-time | Process-lifetime (+ temp cleanup test) | `test_voice_detector_temp_cleanup` | Rebind machinery — không lazify được |
| `_cross_language_learner` | import-time | Process-lifetime | Tests setattr | Rebind machinery (`helpers`) — không lazify được |
| `_fact_checker` | import-time | Process-lifetime | Namespace hook `_async_fact_check` override | Rebind machinery (`_async_fact_check`) — không lazify được |
| `_simple_explainer` | import-time | Process-lifetime (stateless explainer) | Tests setattr | Rebind machinery (`_ask_impl` khai báo cùng tên) — không lazify được |
| `_real_learning` | **PEP 562 LAZY (B4-W1)** — dựng ở lần đọc đầu, cache ngược globals, lock chống double-construct | Process-lifetime; không close (chỉ giữ path) | `tests/internal/test_hc_singleton_lazy_seams.py` | An toàn: KHÔNG hàm rebind nào LOAD_GLOBAL nó (rg toàn `api_server_parts/`); consumer duy nhất là delegate `scp/api/_shared.py` (getattr → chạm được `__getattr__`) và `chat.py` import function-local lúc call time; constructor chỉ lưu path, không mở DB |
| `_fast_learning` | **PEP 562 LAZY (B4-W1)** — như trên; `None` hợp lệ khi `_V1042_AVAILABLE=False`, kết quả None cũng được cache | Process-lifetime; thread start do `start_fast_learning_thread` (opt-in env) | `test_helpers_fast_learning_gate`, storage-boundary probe | Cùng lý do `_real_learning`; flag `_V1042_AVAILABLE` được tái dùng nguyên si trong getter |
| `_attack_crawler` / `_predictive_engine` / `_background_task` / `_scheduler_offloop_stop` | import-time = `None` | Dựng/dừng trong lifespan + `start_crawl_thread` | F-14 shutdown seam | Đã lazy-by-design |
| `_async_factcheck_tasks`, `_fact_check_retract_queue` | import-time (set/deque rỗng) | Bounded (deque maxlen=1000) | app_state tests | Rẻ, không cần lazy |
| `_V1042_AVAILABLE`, `_V1044_AVAILABLE` | import-time flag | Đọc-only sau import | boot_config tests | Flag, không phải singleton nặng |

### Tiêu chí lazify đã áp dụng (B4-W1)

1. Không được nằm trong namespace mà rebind machinery LOAD_GLOBAL.
2. Không được đọc lúc import của module khác (attribute access thì OK —
   PEP 562 bắt được cả `from X import y` lẫn `getattr`).
3. Constructor không side-effect nặng (không mở DB, không start thread).
4. Cache ngược vào `globals()` sau lần dựng đầu → các lần đọc sau là dict hit
   thuần, `monkeypatch.setattr` hoạt động bình thường.

Kết quả: convert **2/7** object nặng (`_real_learning`, `_fast_learning`).
5 object còn lại giữ import-time — mỗi cái viền tiêu chí #1 (rebind) hoặc
decorator def-time; ledger này là chấp nhận có chủ ý, không phải bỏ sót.

## HC-41 — free catalog refresh ↔ gateway module var (coupling đã pin bằng test)

`scp/llm_gateway/free_catalog.py:128` (`refresh_free_catalog`) ghi:

```python
gateway_client.OPENROUTER_FREE_MODELS = [model["id"] for model in free_models]
```

— tức là **ghi đè biến module** trên `scp.llm_gateway.client` (bản copy được
client import từ providers lúc import-time), KHÔNG in-place mutation, KHÔNG
đụng `providers.OPENROUTER_FREE_MODELS` gốc.

- Consumer đọc module var trực tiếp: `client.py` (LOAD_GLOBAL nội bộ sau khi
  refresh vì refresh ghi thẳng vào `client.__dict__`),
  `scp/core/free_discovery_scheduler.py:152` (`getattr(_gw_client, ...)`).
- Phân kỳ đã biết: `providers.py:457` (`status()` đếm
  `len(OPENROUTER_FREE_MODELS)`) đọc bản copy import-time của CHÍNH NÓ → đếm
  stale sau refresh. Đây là hệ quả chấp nhận được của biến module; đổi sang
  instance-state đòi hỏi sửa call-path providers/client/tests seam (rủi ro >
  lợi ích ở wave này).
- **Pin bằng test:** `tests/internal/test_free_catalog.py::
  test_refresh_overwrites_gateway_client_module_var_hc41` — khoá hành vi
  "new list object vào client namespace, providers copy giữ nguyên" thành
  contract có test thay vì coupling ngầm.

**Trạng thái: ACCEPTED — documented + pinned test.**
