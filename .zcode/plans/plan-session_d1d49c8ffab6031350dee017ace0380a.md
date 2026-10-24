# KẾ HOẠCH GIẢI QUYẾT 64 HIDDEN COUPLINGS (HC-01→HC-64) — SCP/Vietnam-Lab

**Quyết định của owner:** Toàn bộ 5 wave · cho phép behavior change có kiểm soát (mọi thay đổi đều phải test pin + cập nhật consumer) · push sau mỗi wave (không cần đợi CI).

**Nguyên tắc thực thi (không đổi giữa các wave):**
- Orchestrator ủy quyền worker subagent + CHECKER độc lập từng wave (khuôn khổ wave 4); 5 kỷ luật audit (lane người lạ, docs-truth, nguồn thật ngoài, sweep tĩnh, vùng âm) áp dụng mọi wave.
- Tái sử dụng hạ tầng có sẵn: `BackgroundJobRegistry` (scp/api/background_jobs.py:109), `route_profile.py` (scp/api/route_profile.py:32), `runtime_paths.py` (scp/core/runtime_paths.py:8), `DomainKnowledge.store()` choke point (scp/knowledge/domain_knowledge.py:186), `config_contract.py`, `PgEventBus`.
- Mỗi gate/cơ chế mới bắt buộc ≥1 test âm anti-placebo. GA.md handoff sau mỗi wave. KHÔNG big-bang: mỗi wave nhỏ, đảo ngược được, có rollback (`git checkout`/revert).

---

## WAVE 0 — Ledger kiến trúc (nửa phiên)
1. Tạo `docs/architecture/HIDDEN_COUPLINGS.md`: 64 mục HC-01→HC-64, mỗi mục có trạng thái `RESOLVED / ACCEPTED (lý do thiết kế) / OPEN (wave nào nhận)`.
2. Drift test chặn xóa/sửa ledger sai lệch thực tế (anti-placebo: xóa mục trong file mà code vẫn còn coupling → test văng).
3. Commit + push branch (đẩy toàn bộ 10+ commit đang chờ).

## WAVE K1 — An toàn điều khiển (bug-proven trước)
| HC | Việc | Ghi chú |
|---|---|---|
| HC-23, HC-13 | Tạo `scp/security/kill_switch.py` hợp nhất 3 kênh (file `data/pc_controller/KILL_SWITCH`, SQLite `control.global_kill/epoch`, env `SCP_KILL_SWITCH`); `middleware/runtime_skill_guard.py:106` bỏ import ngược `api_server`; `api_server._pc_kill_switch_engaged` delegate | Anti-placebo: từng kênh engage ĐỘC LẬP phải chặn; clear epoch phải qua API có auth |
| HC-46/47 | `WAITING_APPROVAL` vào `STATES` (state thứ 18) trong `scp/task_kernel_parts/definitions.py` + transition map + audit consumer (lease recovery, dashboard, 54 chỗ CREATE TABLE trong tests — tối thiểu 6 test file) | Schema split-brain đã PROVEN |
| HC-10 (một phần) | Global dùng chéo module đi qua accessor (mẫu kill_switch), cấm import ngược — sweep `rg "from scp.api_server import _"` | Bổ sung test chặn import ngược mới |

## WAVE K2 — Config Settings hợp nhất (HC-01/02/03/04/05/07/08 + vùng âm a)
1. `scp/core/settings.py`: EffectiveSettings typed-lazy; precedence tường minh `process env > .env file > default` (giữ semantics SCP_ENV_FILE của W4-2: idempotency per-path + warning loud). `scp/__main__._load_env_at_startup` delegate sang env_loader (hết loader divergent).
2. Migrate theo cụm (mỗi cụm 1 worker, test pin): core (61 lượt), lifespan (23), api_server (10), llm_gateway (18), task_kernel_parts (4) — tổng ~437 điểm đọc env.
3. HC-04: lifespan bỏ tạm-mutate `SCP_WHY_LLM_ENABLED` — settings override theo context.
4. HC-05: bảng canonical→legacy (backend kernel/storage, SCP_DB_PATH…) + warning khi legacy được dùng.
5. HC-07/08: token registry (11 loại secret — bảng vai trò/owner/risk trong docs + constant index trong code); convention `*_FILE` chuẩn hóa trong loader. KHÔNG hợp nhất giá trị token (chấp nhận thiết kế, làm cho minh bạch).

## WAVE K3 — Data ownership & knowledge trust (HC-16→HC-20, HC-25, phần S4-F-01)
1. `runtime_paths.py` phủ 21 file còn lại tự đọc `SCP_DATA_DIR` + 5 hardcode (`github_backup.py:15`, `data_partitioner.py:116`, `autonomous_cycle.py:191`, `fast_learning_engine.py:119`, `partition/shard.py`).
2. TaskKernel methods mới thay SQL thô: `list_recent()` (api_server.py:764), `query_by_state()` (lifespan.py:408), `pending_review_breakdown()` (doubt_cron.py:95), `purge_tasks()` có journal event (knowledge/deduplication_maintenance.py:204). Probe `runtime_audit/probes/kernel.py` GIỮ NGUYÊN (out-of-process là chủ đích).
3. HC-19: ownership map `data/` (bảng file→owner module trong HIDDEN_COUPLINGS.md); `v13.db` expose qua `db_manager` accessor cho 18 caller (phase 1 — tách DB là long-term, ghi ledger).
4. Knowledge trust hook tại `DomainKnowledge.store()` (:186 — mọi writer đã hội tụ tại đây): tích hợp `trust_hierarchy` + `source_reputation` + `source_watchlist` sẵn có. **Behavior change đã duyệt:** source không trust → trạng thái pending + audit log, không bịa verification. Readers SQL thô (autonomous_cycle.py:219,308; free_api_bridge.py:221 tự CREATE INDEX — schema drift) chuyển qua accessor.

## WAVE K4 — Provider, ports, jobs runtime (HC-26/28/29/31/33/35-37/39/40/42/43/44)
1. Port registry: `docs/architecture/ports.md` + constants + drift test đối chiếu compose ↔ Python ↔ TS (anti-placebo: sửa 1 bên mà quên bên kia → test văng).
2. HC-56: codegen `TASK_MODEL_MAP` TS từ Python (`scripts/generate_task_model_map.py` + drift test) — hết bảo trì tay 2 bản.
3. HC-42: khi chuỗi provider chỉ còn 1 → verdict bắt buộc nhãn `self_certified: true` + surface cho downstream (`response_verifier`); crosscheck tự kích hoạt khi có ≥2 provider. (Behavior change đã duyệt — nền từ F1-S3.)
4. HC-33: inventory map các điểm gọi HTTP + test chặn module gọi ra ngoài mà không qua enforcement; ưu tiên đưa 7 module S1-F-1 (crawler/catalog/crosscheck) qua `EgressPolicy`/`safe_fetch`.
5. HC-39/44: migrate raw threads vào `BackgroundJobRegistry` (mở rộng hỗ trợ async task): doubt_cron, InboxDaemon, FreeDiscovery, why-verify, retry-policy heartbeat, deep-audit heartbeat; app.state.stop-event gom về registry.
6. HC-43: rate-limit/limiter RAM — ghi limitation multi-worker + design-note Redis adapter (không implement). HC-31/35/36/37: docs-truth + graceful degradation, không refactor lớn.

## WAVE K5 — Contract & test infrastructure (HC-47/49/51→HC-64)
1. HC-49: `AskPipelineContext` dataclass typed thay mutable dict chảy qua 5 stage `/ask` (struct-safe).
2. HC-51/52/57: chính sách route versioning + deprecation; test **anti-stale openapi_snapshot** (regen + diff — đóng vùng âm e) + GATEWAY.md ghi chú profile gate.
3. HC-53/54: verdict/governance/provenance thành `Literal` enum + validation test; contract test `<think>` stripping (đánh dấu nguồn reasoning).
4. HC-58: dashboard `src/lib/audit-data/*` gắn nhãn "historical snapshot" + timestamp hiển thị (không bị nhầm live).
5. HC-59: **test lane "core profile, không secret, máy sạch"** — mô phỏng hành trình người lạ (kỷ luật 1) chạy thường xuyên.
6. HC-60/61: audit seam monkeypatch (chuyển seam động sang module-level accessor chuẩn) + order-independence (chạy subset ngẫu nhiên local-CI); fix singleton/limiter rò trạng thái.
7. HC-62: docs version pinning (py3.12/numpy/pytest/jiti — nền từ W4-1). HC-63: OS/Electron matrix document. **HC-64: ACCEPT** — single-operator là thiết kế local-first chủ đích: ghi "multi-worker readiness checklist" vào ledger, không implement.
8. Vùng âm wave 4 còn lại: `dashboard/.../routes/route.ts` sửa `authRequired` đúng (HC-53 liên quan).

---

## Đầu ra từng wave (khuôn khổ cố định)
Worker probe-before-patch → orchestrator duyệt diff + test tổng trong container → commit → CHECKER độc lập (anti-placebo + rerun probe + cập nhật vùng âm) → push (không chờ CI) → GA.md handoff (B2a, B2b…).

## Tiêu chí hoàn thành chương trình
1. `HIDDEN_COUPLINGS.md`: 100% mục là RESOLVED (kèm test/bằng chứng) hoặc ACCEPTED (kèm lý do thiết kế + ngày duyệt owner).
2. 0 regression: battery chuẩn + lane core mới + drift tests xanh same-SHA.
3. Không còn: import ngược middleware→api_server; SQL thô kernel ngoài probe runtime_audit; nạp env divergent; quảng bá route không resolve.
4. Verdict cuối là PASS_WITHIN_SCOPE theo wave — không tuyên bố production-ready.

## Rủi ro & xử lý
- **K2 (config, 437 điểm)** rủi ro cao nhất → typed Settings opt-in, migrate từng cụm có test pin, rollback per-commit.
- **K3 trust hook** thay đổi hành vi luồng học → bật sau khi test anti-placebo + shadow so sánh trước/sau.
- **K4 registry migration** chạm shutdown lifecycle (đã có F-14 stop-event làm nền) → test boot/shutdown sạch là gate bắt buộc.
- Thuật ngữ: mỗi wave dùng đúng 25 phút deadline tự giác/worker như wave 4; quá 2 lần probe thất bại cho 1 mục → ghi OPEN + escalation, không manufacture fix.