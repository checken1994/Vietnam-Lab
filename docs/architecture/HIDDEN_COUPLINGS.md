# LEDGER — 64 Hidden Couplings (HC-01 → HC-64)

> Bản đồ nợ kiến trúc của SCP, tổng hợp từ khảo sát độc lập 2026-10-24, được
> owner duyệt chương trình giải quyết cùng ngày. **Mục đích:** checklist bắt
> buộc khi chạm bất kỳ subsystem nào — mỗi finding phải ở một trong 3 trạng
> thái: `RESOLVED` (kèm commit/bằng chứng), `ACCEPTED` (chủ đích thiết kế, có
> lý do), `OPEN` (wave nào nhận, còn chờ).
>
> Quy tắc cập nhật: sửa ledger PHẢI đi cùng commit code/test tương ứng; test
> `tests/T00_integrity/test_hidden_couplings_ledger.py` chặn ledger lệch
> thực tế (đủ 64 mục, mỗi mục có trạng thái hợp lệ, RESOLVED phải dẫn commit).

## A. Cấu hình, env và secret

| ID | Coupling | Trạng thái | Ghi chú |
|---|---|---|---|
| HC-01 | .env nạp nhiều lần, lần sau đè shell env | **RESOLVED** | W4-2 · commit `43a3630d` — idempotency per-path + warning loud (scp/security/env_loader.py) |
| HC-02 | Nhiều entrypoint tự nạp env khác nhau | **RESOLVED** | Wave K2 · commit 5e1d5a17 — `__main__._load_env_at_startup` delegate env_loader; het loader divergent |
| HC-03 | Không có config object hiệu lực duy nhất (~437 điểm đọc env) | **OPEN (K2)** | EffectiveSettings typed + migrate từng cụm |
| HC-04 | Lifespan tạm mutate `SCP_WHY_LLM_ENABLED` rồi khôi phục | **RESOLVED** | Wave K2 · commit 5e1d5a17 — temporary_override() finally-restore (sửa class lỗi mất-restore-khi-exception) |
| HC-05 | Canonical thắng legacy env, tưởng Postgres vẫn SQLite | **RESOLVED** | Wave K2 · commit 5e1d5a17 — CANONICAL_TO_LEGACY trong settings.py (cặp thật SCP_KERNEL_BACKEND/SCP_STORAGE_BACKEND) + warning loud |
| HC-06 | Profile core/standard/full đổi cả việc route tồn tại | **ACCEPTED** | thiết kế chủ đích (route_profile.py); quảng bá đã trung thực (W4-4, `43a3630d`) |
| HC-07 | 11 loại token/secret riêng biệt | **OPEN (K2)** | không hợp nhất giá trị — token registry (vai trò/owner/risk); registry tĩnh đã landing: `scp/security/secret_registry.py` + docs/architecture/SECRETS.md (K2-W2) |
| HC-08 | Secret từ env hoặc `*_FILE` | **OPEN (K2)** | chuẩn hóa convention trong loader; `resolve()` precedence FILE-wins fail-closed đã pin trong `scp/security/secret_registry.py` (K2-W2), migrate điểm đọc hàng loạt còn chờ |

## B. Import, namespace và global state

| ID | Coupling | Trạng thái | Ghi chú |
|---|---|---|---|
| HC-09 | Executor/controller/ledger tạo ngay khi import | **OPEN (K5)** | audit + document; chỉ refactor khi đụng |
| HC-10 | Singleton giữ config/state lần tạo đầu | **OPEN (K5)** | accessor pattern chuẩn (mẫu từ K1 kill_switch) |
| HC-11 | `scp/api/_shared.py` PEP 562 ủy quyền tên | **OPEN (K5)** | contract doc |
| HC-12 | `_ask_impl`/lifespan rebind globals của api_server | **RESOLVED (safety)** | F-18 · `10a584d1` — NameError đã chặn; cơ chế rebind còn, accessor hóa dần theo K1 |
| HC-13 | Middleware import ngược kill-switch từ api_server | **RESOLVED** | Wave K1 · commit ca708596 — middleware import scp.security.kill_switch + AST-check cấm import ngược |
| HC-14 | Tự sửa `sys.path` (15 chỗ) | **OPEN (K5)** | document + contain, không refactor đại |
| HC-15 | `sys.modules` tra cứu động trong gateway | **OPEN (K5)** | |

## C. File, DB và trạng thái dùng chung

| ID | Coupling | Trạng thái | Ghi chú |
|---|---|---|---|
| HC-16 | `data/` theo cwd hiện tại | **RESOLVED** | Wave K3 · commit 12ddd369 — runtime_paths phủ 5 hardcode còn lại; 21 file tự đọc SCP_DATA_DIR → theo kế hoạch dần |
| HC-17 | `data/v13.db` nhiều subsystem sở hữu ngầm (18 module) | **OPEN (K3)** | phase 1: accessor qua db_manager; tách DB long-term |
| HC-18 | Hàng chục SQLite/JSONL làm giao thức tích hợp | **OPEN (K3)** | ownership map từng file |
| HC-19 | Dữ liệu runtime gitignored nhưng test giả định tồn tại | **OPEN (K5)** | lane "core profile, máy sạch" |
| HC-20 | Kho tri thức rỗng trông như hệ thống hoạt động | **RESOLVED** | Wave K3 · commit 12ddd369 — trust gate tại store() (blocked/watchlist REJECT, lạ → unassessed), schema không đổi |
| HC-21 | Module ngoài TaskKernel truy cập SQL nội bộ kernel | **RESOLVED** | Wave K3 · commit 12ddd369 — 4 accessor + 4 call-site thay; còn có chủ đích: probe out-of-process, kernel_crash_ingestor read-only |
| HC-22 | Kill switch phân tán 3 kênh (file/SQLite/env) | **RESOLVED** | Wave K1 · commit ca708596 — scp/security/kill_switch.py is_engaged/engaged_source, fail-closed mọi kênh, 10 test anti-placebo |
| HC-23 | Scheduler/dashboard vừa HTTP vừa đọc chung file state | **OPEN (K4)** | document + boundary doc |
| HC-24 | Capability epoch nối Hands/PC/TaskKernel lease | **OPEN (K4)** | doc giao thức ba phía |
| HC-25 | request.state/app.state là kênh truyền run_id/trace_id | **OPEN (K5)** | typed registry (nền app.state đã map) |

## D. Service, mạng, giao thức và provider

| ID | Coupling | Trạng thái | Ghi chú |
|---|---|---|---|
| HC-26 | Cổng lặp Python/TS/Compose/Electron | **OPEN (K4)** | port registry + drift test |
| HC-27 | Thứ tự boot bridge→scheduler→backend→dashboard thuộc kiến trúc | **ACCEPTED** | thiết kế; docs-truth đã trung thực (W4-3) |
| HC-28 | LLM Bridge phụ thuộc ngầm dashboard/node_modules/jiti | **RESOLVED** | W4-3 · `43a3630d` — loader đa-candidate + khai dependency |
| HC-29 | Facade Ollama/OpenAI + TASK_MODEL_MAP nhân đôi Python/TS | **OPEN (K4)** | codegen + drift test |
| HC-30 | Provider identity theo chuỗi tên (openai_compat shadow) | **RESOLVED** | F-21/F-05 · `78d87a6c`+`10a584d1` |
| HC-31 | 1 provider → judge tự chấm (self-certify) | **OPEN (K4)** | nhãn `self_certified` bắt buộc + crosscheck auto |
| HC-32 | Egress policy đa tầng (7 module ngoài enforcement) | **OPEN (K4)** | inventory + test chặn; đưa crawler/catalog qua policy |
| HC-33 | Hai dashboard (Next :3000 + backend /dashboard) | **ACCEPTED** | document + route profile gate; không merge |
| HC-34 | Cross-verify phụ thuộc Chrome CDP/cookie/DOM | **ACCEPTED** | thiết kế; graceful degradation đã có |
| HC-35 | Electron sở hữu cây tiến trình/manifest/cổng/CSP | **ACCEPTED** | app desktop riêng; document matrix |
| HC-36 | Telemetry giả định OTLP collector ngoài | **ACCEPTED** | degraded theo thiết kế; document |
| HC-37 | Local-first nhưng vướng external (fonts/OpenRouter/GitHub) | **OPEN (K4)** | docs-truth + graceful degradation |

## E. Thời gian, job nền và vòng phản hồi

| ID | Coupling | Trạng thái | Ghi chú |
|---|---|---|---|
| HC-38 | HTTP sống trước judge ready (health tốt ≠ /ask dùng được) | **RESOLVED (đã có)** | bằng chứng probe AUD-S3 E1 2026-10-24: /ready judge-gated + /ask 503 fail-closed; giữ contract |
| HC-39 | Nhiều job nền cùng ghi trạng thái, 10/15 ngoài registry | **OPEN (K4)** | migrate vào BackgroundJobRegistry (async-capable) |
| HC-40 | Tín hiệu tấn công đổi AutoFix từ 2 nơi | **OPEN (K4)** | hợp nhất đường kích hoạt + audit |
| HC-41 | Free catalog refresh đè biến module; breaker giữ lịch sử | **OPEN (K4)** | phần breaker đã verify sạch (S4); phần catalog theo K4 |
| HC-42 | Rate limit/đếm lỗi auth trong RAM per-process | **OPEN (K4)** | limitation doc + design-note Redis (không implement) |
| HC-43 | Kết nối DB TaskKernel gắn ContextVar/async context | **ACCEPTED** | thiết kế per-context ownership đã verify (S2 — 0 cross-context leak) |
| HC-44 | `WAITING_APPROVAL` trong transitions nhưng ngoài 17 STATES | **RESOLVED** | Wave K1 · commit ca708596 — state 18 + xóa 2 carve-out hack + 12 test + negative control |
| HC-45 | Lease + idempotency header + capability epoch = giao thức ba phía | **OPEN (K4)** | schema doc; idempotency CLAIMED-vĩnh-viễn (TK-02) kèm |
| HC-46 | 5 stage /ask dùng chung context mutable | **OPEN (K5)** | AskPipelineContext dataclass typed |
| HC-47 | Học/phòng thủ ghi vào attack memory/knowledge mà suy luận dùng lại | **OPEN (K3)** | gắn với trust hook + ownership map |
| HC-48 | AutoFix nối backend chạy + source + scanner + gateway | **ACCEPTED** | bản chất self-correcting pipeline; guard đã verify (S4) |
| HC-49 | "Học" chỉ khi thread nền bật; copy code ≠ copy hành vi | **ACCEPTED** | document: dữ liệu học là phần state bắt buộc phải backup |
| HC-50 | Đồng hồ hệ thống/ngày trong prompt đổi đáp án theo thời điểm | **ACCEPTED** | đã có guard W8/time-signal (tests T02); pin thêm contract test |

## F. Hợp đồng dữ liệu và ngữ nghĩa

| ID | Coupling | Trạng thái | Ghi chú |
|---|---|---|---|
| HC-51 | Route/trường nhiều thế hệ v98→v106 cùng hiện hành | **OPEN (K5)** | versioning policy + deprecation doc |
| HC-52 | Verdict/governance/provenance/epistemic là chuỗi tự do | **OPEN (K5)** | Literal enum + validation (nền F1-S3 flags) |
| HC-53 | PASS/FAIL + `<think>` stripping là giao thức máy-máy | **OPEN (K5)** | contract test pin |
| HC-54 | System prompt gắn hứa năng lực kể cả khi profile thiếu công cụ | **OPEN (K5)** | prompt theo profile capability-aware |
| HC-55 | Tên task nội bộ quyết model/chi phí; thiếu biến rơi model cũ | **OPEN (K4)** | gắn TASK_MODEL_MAP codegen (HC-29) + explicit default |
| HC-56 | Nhiều lớp tự dịch shape OpenAI/Ollama | **OPEN (K4)** | gắn codegen (HC-29) |
| HC-57 | Root/health metadata khai tay (quảng bá 404/số cũ) | **RESOLVED (root)** | W4-4 · `43a3630d` — chỉ quảng bá route mounted; openapi_snapshot/GATEWAY.md còn → K5 |
| HC-58 | Dashboard chứa audit data tĩnh dễ nhầm live | **OPEN (K5)** | nhãn "historical snapshot" + timestamp |

## G. Test, phát hành, nền tảng và quy mô

| ID | Coupling | Trạng thái | Ghi chú |
|---|---|---|---|
| HC-59 | Test ép full profile + secret riêng, không đại diện mặc định core | **OPEN (K5)** | lane "core, no-secret, máy sạch" (kỷ luật 1) |
| HC-60 | Test nhạy thứ tự (singleton/limiter RAM/env rò) | **OPEN (K5)** | order-independence subset + isolation fix |
| HC-61 | Test monkeypatch đúng seam động production | **OPEN (K5)** | seam audit → module-level accessor |
| HC-62 | pytest/Python/NumPy/jiti là hợp đồng ngầm tái lập | **RESOLVED (docs)** | W4-1 · `43a3630d` — guard ≥3.12 + CONTRIBUTING; giữ pin |
| HC-63 | Sandbox OS/Docker/Electron/Chromium/Postgres quyết năng lực thật | **ACCEPTED** | matrix document |
| HC-64 | Giả định 1 máy/1 operator/1 process (SQLite/file state/singleton) | **ACCEPTED** | thiết kế local-first chủ đích; multi-worker readiness checklist trong ledger này |

## Tóm tắt theo trạng thái

- **RESOLVED: 10** — HC-01, HC-12(safety), HC-13, HC-22, HC-28, HC-30, HC-38(đã có), HC-44, HC-57(root), HC-62(docs).
- **ACCEPTED: 12** — HC-06, HC-27, HC-33, HC-34, HC-35, HC-36, HC-43, HC-48, HC-49, HC-50, HC-63, HC-64.
- **OPEN: 42** — phân bổ: K2 (6) · K3 (6) · K4 (14) · K5 (16). Wave K1 đã đóng 3 mục (HC-13, HC-22, HC-44 — commit ca708596).

## Multi-worker readiness checklist (đi kèm HC-64)

Nếu tương lai cần chạy nhiều worker/process trên cùng data:
1. Rate limiter + auth-fail counter (RAM) → phải chuyển Redis/DB.
2. Free catalog refresh + breaker in-memory → phải chia sẻ hoặc per-worker scope.
3. File state (KILL_SWITCH, escalation_state.json, scheduler state) → phải DB.
4. Singleton detectors/ledgers tạo lúc import → phải per-processidentity + epoch.
5. BackgroundJobRegistry → phải có leader election.
6. SQLite (kernel, knowledge, v13) → chuyển Postgres (PgEventBus + pg_storage_parity đã có nền).
