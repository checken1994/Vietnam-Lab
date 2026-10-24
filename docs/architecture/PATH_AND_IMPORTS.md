# PATH_AND_IMPORTS — sys.path mutations & sys.modules lookups (HC-14, HC-15)

> B4-W1 · 2026-10-23 · branch `feat/runtime-audit-brain-console` @ 6d365d0e
> Nguồn: `rg -n "sys.path.(insert|append)" scp/ --type py` và
> `rg -n "sys.modules" scp/llm_gateway/ --type py` (kết quả chép nguyên xi
> tại thời điểm audit).

## HC-14 — `sys.path.insert/append` trong `scp/`

15 match, trong đó chỉ 12 là mutation runtime thật (2 còn lại là chuỗi code
sinh ra cho sandbox con, 1 là comment ghi nhận đã xoá).

| # | Vị trí | Loại | Lý do tồn tại | Trạng thái |
|---|--------|------|---------------|------------|
| 1 | `scp/experience/experience.py:55` | runtime insert (SCRIPT_DIR) | Dual-use: module package + CLI script độc lập (docstring có menu lệnh `python v14_experience.py --chinh-sach`); insert cho phép chạy trực tiếp file khi `scp` chưa trên path | ACCEPTED — needed (standalone bootstrap) |
| 2 | `scp/benchmark/world_standard_verifiability_benchmark.py:33` | runtime insert (ROOT, có guard `if str(ROOT) not in sys.path`) | Benchmark chạy standalone từ bất kỳ cwd nào; guard chống trùng entry | ACCEPTED — needed (standalone bootstrap, guarded) |
| 3 | `scp/benchmark/run_benchmark_v2_parts/extract_claims_from_answer.py:17` | runtime insert (trong try, noqa `[hygiene-keep]`) | Script auto-extract từ `run_benchmark_v2.py`; `BENCHMARK_DIR` được inject bằng rebind/wire của script cha — chạy độc lập thì insert là bootstrap duy nhất | ACCEPTED — needed (generated standalone script) |
| 4 | `scp/autofix/evidence_replay.py:604` | CHUỖI code (không phải mutation của process host) | Đoạn source được ghi ra/ejecuted trong sandbox replay con | ACCEPTED — not a real mutation |
| 5 | `scp/core/crypto_verifier.py:60` | runtime insert (SCRIPT_DIR) | Module có `__main__` CLI (verify coin); insert là legacy standalone bootstrap. Khi import qua package (`scp.*`) thì thừa — nhưng xoá làm gãy chạy-trực-tiếp | ACCEPTED — containable (dual-use script) |
| 6 | `scp/core/multi_source_verifier.py:61` | như trên | như trên (`__main__` CLI) | ACCEPTED — containable (dual-use script) |
| 7 | `scp/data_sources/live_knowledge.py:38` | comment | Ghi nhận đã REMOVE insert (G3-CONSOLIDATE RE-10) | N/A — already removed |
| 8 | `scp/meta/cognitive_engine.py:50` | như #5 | `__main__` CLI + re-export layer | ACCEPTED — containable (dual-use script) |
| 9 | `scp/meta/reverify_scheduler.py:59` | như #5 | `__main__` CLI | ACCEPTED — containable (dual-use script) |
| 10 | `scp/meta/principle_rules.py:50` | như #5 | `__main__` CLI | ACCEPTED — containable (dual-use script) |
| 11 | `scp/meta/policy_applier.py:44` | như #5 | `__main__` CLI | ACCEPTED — containable (dual-use script) |
| 12 | `scp/meta/verdict_predictor.py:47` | như #5 | `__main__` CLI | ACCEPTED — containable (dual-use script) |
| 13 | `scp/meta/meta.py:66` | như #5 | Không có `__main__` nhưng là module aggregating re-export, giữ pattern đồng bộ cả khối `meta/` | ACCEPTED — containable (pattern-consistency) |
| 14 | `scp/prediction/predictive.py:43` | như #5 | Không có `__main__`; insert legacy từ khi file là script | ACCEPTED — containable (legacy) |
| 15 | `scp/sandbox_evaluator/evaluator.py:102` | CHUỖI code (code template cho sandbox con) | Cài path cho tiến trình con được sandbox hoá, không đụng path process host | ACCEPTED — not a real mutation |

### Vì sao không xoá loạt #5–#14 trong đợt này

- Insert `SCRIPT_DIR` (thư mục con của package, vd `scp/meta/`) KHÔNG phá
  import `scp.*` đầy đủ; rủi ro duy nhất là shadowing namespace top-level
  (`import cognitive_layers` trỏ vào `scp/meta/cognitive_layers/`). Đây là
  debt vệ sinh, không phải lỗi hành vi.
- Xoá 11 chỗ cùng lúc trong wave trả-nợ 25 phút vi phạm "small reversible
  patch": mỗi file cần test standalone-run riêng (`python scp/meta/meta.py`
  kiểu đó) để chứng minh không gãy dual-use. B4-W1 chỉ xoá chỗ **dư thừa rõ
  ràng**: KHÔNG có chỗ nào thỏa "đã có pythonpath/config thay thế" — tất cả
  match đều tự chịu trách nhiệm bootstrap cho kịch bản chạy-trực-tiếp.
  → Kết luận: 0 xoá, 13 ACCEPTED documented, 2 không phải mutation thật.

## HC-15 — `sys.modules.get("scp.llm_gateway.client")` trong gateway

3 điểm lookup động:

| Vị trí | Đọc gì | Vì sao tồn tại |
|--------|--------|----------------|
| `scp/llm_gateway/circuit_breaker.py:20` (`_monotonic`) | `client.time` | Circuit breaker cần thấy `time` ĐÃ BỊ MONKEYPATCH trên `scp.llm_gateway.client` (test seam freeze đồng hồ). Import trực tiếp `from .client import time` sẽ snapshot object trước khi patch — seam chết. Tránh circular import: `client.py` import circuit_breaker, nên breaker không được import ngược client lúc module-load. |
| `scp/llm_gateway/providers.py:48` (`enforce_egress_policy`) | `client.enforce_egress_policy` | Cùng pattern: honors client-level monkeypatch (test chặn egress ở tầng client) mà không tạo import cycle client↔providers. |
| `scp/llm_gateway/providers.py:341` (`_call_model_once`) | `client._llm_egress_allowed` | Honors egress patch tại lúc gọi (late binding) — policy có thể bị thay sau boot trong test/audit. |

### Rủi ro đã đánh giá

- **Rủi ro chấp nhận được:** late binding qua `sys.modules` là đường "ẩn" —
  đọc code tĩnh dễ tưởng hàm client gốc luôn được gọi. Bù lại đây chính là
  contract test-seam có chủ ý (monkeypatch trên `scp.llm_gateway.client`
  phải lan tới breaker/providers). Fallback luôn có (`getattr(mod, x, real)`
  / `if mod is None`), không crash khi client chưa import.
- **Không refactor** (khuyến nghị B4-W1): đổi sang dependency injection sẽ
  đụng toàn bộ call-path client↔providers↔circuit_breaker — rủi ro lớn hơn
  lợi ích ở Wave trả nợ này.

**Trạng thái: ACCEPTED — documented** (test seam có chủ ý + tránh circular
import; fallback an toàn khi module chưa load).
