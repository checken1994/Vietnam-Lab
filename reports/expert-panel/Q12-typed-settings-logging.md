# Q12 — Typed settings / structlog logging: production wiring

- **Worker:** Q12
- **Ngày:** 2026-09-15
- **Branch/HEAD audit base:** `audit/runtime-guard-AUDIT-20260909` @ `bf5af3b3f5b1e8f5d43fa30d820671cad3714dbf`
- **Trạng thái:** IMPLEMENTED + RUNTIME_PROVEN (candidate bf5af3b + Q12 overlay) — không commit, không push.
- **Skill binding:** `scp-dna` (evidence-first, 5-Whys, fail-closed) + `scp-runtime-audit` (snapshot, health vs readiness, runtime proof).

## 1. Problem statement

`scp/core/config.py` (SCPSettings, pydantic-settings) và `scp/core/logging_config.py`
(configure_logging, structlog) tồn tại nhưng **untracked và chỉ có test caller**
(`test_m2_auth_settings_challenger`, `test_m2_architecture_backlog`,
`test_m2_adversarial_challenger`) — không một đường production nào import.
Boot vẫn dùng `logging.basicConfig` thô ở `scp/api_server.py:66` và chỉ validate env qua
`config_contract.validate_boot_config()` trong lifespan.

## 2. Why chain

1. **Vì sao typed settings/logging "có mà như không"?** → Được viết kèm test nhưng không có call-site production → thành "test-only API", debt "production wiring".
2. **Vì sao không thể nối thẳng `get_settings()` vào boot?** → `config_contract` đã là gate fail-closed duy nhất với security rules (FORBIDDEN_VALUES, MIN_LENGTH). SCPSettings không enforce required secrets (default `""`) → gọi song song sẽ tạo **2 nguồn sự thật** phân kỳ ngầm.
3. **Vì sao không thể chỉ "gọi cho đủ"?** → pydantic-settings parse bool/int/float **nghiêm hơn** legacy readers: `SCP_PRODUCTION_MODE=""` raise ValidationError, trong khi mọi reader cũ (`os.environ.get(x,"").strip() in {...}`) coi `""` = mặc định. Nối thô sẽ abort những boot mà contract chấp nhận (đổi behavior cũ) — đặc biệt vì `compose.yml` render `${VAR:-}` thành chuỗi rỗng.
4. **Vì sao basicConfig cũ bị "thô"?** → Nó vô hiệu khi root đã có handler (semantics của basicConfig) và format hardcode; không đọc `SCP_LOG_LEVEL`/`SCP_LOG_JSON`, không JSON mode, không interop structlog.
5. **Root:** wiring thiếu điểm gọi duy nhất (single call site at boot) + thiếu bridge để 2 lớp config nhất quán, không phải thiếu module.

## 3. Bằng chứng đã dùng (independent lineages)

| Lineage | Nguồn | Nội dung |
|---|---|---|
| Static code | `grep`/`rg` production | `config_contract` chỉ được gọi ở `api_server_parts/lifespan.py:60`; `SCPSettings`/`configure_logging` không có production import (trước sửa) |
| Test pin API | 3 file M2 tests | Chữ ký `SCPSettings`, `get_settings` (lru_cache + cache_clear, raise trên env hỏng), `configure_logging(log_level, json_output)` bị khóa chặt → không được đổi semantics |
| Reality: pytest | 57 passed | T01_boot (44) + T03 m2 backlog (13, gồm 7 test Q12 mới) sau khi restore wiring |
| Reality: runtime | Docker candidate `bf5af3b`+overlay | `/health` 200, `/readiness` 503→503→200, log boot format mới, negative fail-fast exit 3 |
| Causal A/B | Revert 2 file production về HEAD | 3 streaming failure `test_m2_adversarial_challenger` **tồn tại y hệt ở baseline** → không do Q12 |
| Guardrail | `tools/t00_meta_audit.py` + `verify_scp_test_skill_contract.py` | "0 new regressions" / `PASS_WITHIN_SCOPE` |

Rủi ro shared-origin: pytest + docker đều chạy trên cùng candidate → đã độc lập thêm bằng compile/subprocess test và negative runtime injection (`SCP_LLM_HEDGE_MAX_SECONDS=never`).

## 4. Đã thay đổi gì (không commit)

| File | Thay đổi | SHA-256 (xem `.tmp/q12/final-file-hashes.txt`) |
|---|---|---|
| `scp/api_server.py` | Thay `logging.basicConfig` bằng `_bootstrap_logging()` — gọi `configure_logging()` **một lần** tại composition-root import, đọc `SCP_LOG_LEVEL`/`SCP_LOG_JSON` (semantics legacy), fallback khi thiếu structlog nằm trong `logging_config`. Guard `if root.handlers: return` giữ đúng no-op semantics của basicConfig cũ (không phá pytest capture / process đã tự config logging) | `be7e…`→`ddac4f98…` |
| `scp/api_server_parts/lifespan.py` | Ngay sau `validate_boot_config()` (vẫn là authority, vẫn fail-closed, vẫn ngoài try/except): gọi `validate_boot_settings(_validated_env)` + log dòng `[Q12]` | `74db4058…` |
| `scp/core/config.py` | Thêm **bridge** `boot_settings_for_validation()` (normalize `""`→unset trước khi parse, restore env trong `finally`) và `validate_boot_settings(validated)`: type-check fail-fast (wrap `ValidationError`→`ConfigContractError`, **chỉ tên field, không bao giờ in giá trị/secret**) + cross-check typed view khớp giá trị contract đã validate (chống 2 lớp diverge trong tương lai). Không đổi `SCPSettings`/`get_settings` (test pin giữ nguyên) | `be7e3ab8…` |
| `tests/T03_capability/test_m2_architecture_backlog.py` | +7 test Q12: garbage typed env → ConfigContractError; message không lộ value; tolerance `""`; normalization restore env kể cả khi parse fail; divergence detection; lifespan-order source pin; **subprocess test** chứng minh import api_server trên root-clean cài đúng 1 handler với `ProcessorFormatter` + JSON mode render NDJSON | `62197233…` |
| `scp/core/logging_config.py` | **Không đổi** (API đã đúng; chỉ được gọi thật) | `96480c7b…` |
| requirements | `structlog==24.4.0` đã có sẵn trong `requirements.txt` + `scp/requirements.txt` (dirty có trước) → không đổi | — |

## 5. Quyết định integrate (không REJECT): causal map

```
python -m scp (docker ENTRYPOINT)          TestClient / uvicorn "scp.api_server:app"
        │                                            │
 _load_env_at_startup / load_selected_env ──► import scp.api_server
        │                                   └─ _bootstrap_logging()  ◄── Q12 #1 (một lần, có guard, fallback)
 uvicorn.run ─► lifespan()                          │
        ├─ validate_boot_config()   [AUTHORITY — không đổi]  
        ├─ validate_boot_settings() [Q12 #2 bridge: type-check + cross-check → ConfigContractError fail-fast]
        └─ startup subsystems → /health, /readiness
```

- **Không 2 nguồn sự thật:** contract vẫn quyết định required secrets/security rules;
  typed layer chỉ *validate* + *cross-check khớp* với kết quả contract; **không có hành vi
  runtime nào đọc từ `SCPSettings`** (không đổi `is_production`→docs, không đổi host/port bind).
- **Behavior cũ giữ nguyên với config hợp lệ**; hành vi mới duy nhất: (a) boot abort sớm hơn trên
  giá trị SCP_ *sai kiểu rõ ràng* (`"never"`, `"maybe"`...) — chính là fail-fast mà MỤC TIÊU 2 yêu cầu,
  (b) log boot đổi format sang structlog khi root chưa có handler (docker/compose path).

## 6. Docker/PC proof (bắt buộc) — RUNTIME_PROVEN cho scope boot

- Candidate: `git archive bf5af3b` + overlay đúng 5 file scope; hash `sha256sum` các file production trong candidate; build log `.tmp/q12/build.log`.
- Image `scp-q12-proof:bf5af3b`, ID `sha256:5ccd2f0b421d7036cf678ba91846f102013d3d0a5cd68e36d8b67064e75feb22`, build `--build-arg SCP_GIT_SHA=bf5af3b3f5b1e8f5d43fa30d820671cad3714dbf`.
- Run isolated (credentials throwaway qua `-e`, volume `scp-q12-data` đã xoá sau proof, port loopback `127.0.0.1:18012`):
  - `GET /health` → **200**, `service_identity.commit=bf5af3b3f5b1…dbf` (`config_hash=unknown` — container không có `.env`, đúng fail-safe).
  - `GET /readiness` → **503 → 503 → 200** (transition thật), `status=ready`, checks judge/scheduler/identity ok.
  - Startup log **format mới** (`.tmp/q12/startup-console.log`):
    `2026-09-15T19:49:34.576659Z [info] [ConfigContract] Boot config OK …` và
    `2026-09-15T19:49:34.630636Z [info] [Q12] Typed boot settings validated against config contract (fail-closed) [scp.api]`.
    Log của riêng uvicorn (`INFO:  Started server process`) giữ format uvicorn — logger uvicorn độc lập, không đụng root.
  - JSON mode (`SCP_LOG_JSON=1`, `.tmp/q12/startup-json.log`): NDJSON thật, ví dụ `{"event": "[Security] CSRF protection: Bearer token auth", "logger": "scp.api", "level": "info", ...}`.
  - **Negative fail-fast** (`SCP_LLM_HEDGE_MAX_SECONDS=never`): exit code **3**, traceback raise `ConfigContractError` tại `lifespan.py:68` với message **chỉ nêu tên field**; `grep -c "never"` trong log = **0** (không echo value/secret).
- Không dòng log nào chứa credential throwaway đã mount (grep = 0 trên cả 3 log).
- Startup không vỡ: 2/2 container positive boot đủ lâu để trả readiness 200.

## 7. Verify commands (kết quả)

| Lệnh | Kết quả |
|---|---|
| `python -m py_compile <5 file>` | OK |
| `python -m pytest -q tests/T01_boot tests/T03_capability/test_bootstrap_and_production_auth.py -p no:cacheprovider` | **44 passed** |
| `+ tests/T03_capability/test_m2_architecture_backlog.py` (chạy lại sau restore từ backup A/B) | **57 passed** |
| `tests/test_m2_auth_settings_challenger.py` | all pass |
| `tests/test_m2_adversarial_challenger.py` | 48 pass, **3 pre-existing fail** (xem §8) |
| `tests/T02_contract/test_god_split_semantic_parity.py` | 35 passed |
| `python tools/t00_meta_audit.py` | All integrity checks passed (0 new regressions) |
| `python tools/verify_scp_test_skill_contract.py` | `PASS_WITHIN_SCOPE` |

`PASS` ở đây = không failure quan sát được trong scope đã nêu ở bảng; **không** phải tuyên bố
toàn bộ hệ thống xanh.

## 8. PHÁT HIỆN MỚI (NEW FINDINGS)

1. **[HIGH, pre-existing, ngoài scope — đề xuất slot Q08/openai_compat]**
   `tests/test_m2_adversarial_challenger.py::test_streaming_missing_or_null_model`,
   `::test_streaming_invalid_temperature`, `::test_stream_aborted_midway` fail **y hệt tại HEAD
   baseline không có wiring Q12** (A/B đã chứng minh: revert `api_server.py`+`lifespan.py` →
   3 failed, cùng 503). Fixture dùng app isolated + `EnvCompatProvider` loopback nhưng
   `openai_compat` trả 503 "server_error" — mâu thuẫn thiết kế với
   `test_openai_compat_stream_endpoint` (T03 backlog) vốn *đòi* 503 khi adapter unavailable.
   Hai test file khoá hai hành vi ngược nhau trên cùng endpoint. Không được sửa vì cấm zone.
2. **[MEDIUM] `SCPSettings(env_file=".env")` là bẫy nguồn-thứ-hai tiềm tàng:** pydantic đọc
   `.env` theo **CWD**, trong khi toàn bộ runtime đọc `os.environ` (sau `load_selected_env`
   tôn trọng `SCP_ENV_FILE`). Trên máy dev có `.env` (repo root) với `SCP_PRODUCTION_MODE=1`,
   nếu tương lai ai đó để `SCPSettings` *điều khiển hành vi* thì sẽ diverge đúng cái bug mà
   conftest EE-G1 (`pytest_collection_finish` restore egress keys) đã phải vá. Bridge hiện tại
   vô hiệu hoá rủi ro này bằng cách **chỉ validate, không drive behavior** + cross-check — nhưng
   cần giữ nguyên tắc đó khi integrate tiếp (host/port/docs/CORS vẫn đọc os.environ như cũ).
3. **[HIGH, đã vá trong bridge] Empty-string env là đường production thật** (`compose.yml`
   `${SCP_LLM_EGRESS_ALLOWLIST:-}`): pydantic-settings raise trên `""` cho bool/int/float trong
   khi mọi legacy reader coi `""` = unset. `boot_settings_for_validation()` normalize `""`→unset,
   restore trong `finally` (pinned by test), nên gate kiểu không phá boot hợp lệ.
4. **[LOW] `configure_logging()` clear root handlers** — dưới pytest mid-phase sẽ xoá handler
   capture của harness. Wiring mới chạy ở import-time với guard `if root.handlers: return`,
   khớp chính xác no-op semantics của `basicConfig` cũ; test subprocess chứng minh đường boot
   thật vẫn cài handler. Các test M2 cũ gọi `configure_logging` trực tiếp giữa test là hành vi
   đã pin từ trước, giữ nguyên.
5. **[LOW, cosmetic, có sẵn] Log dòng `[R20-ROOT-FIX-REAL] Yielding NOW │…` hiển thị mojibake**
   trong `docker logs` (emoji/box-drawing trong message nguồn, không phải do format mới —
   cùng byte stream). Đề xuất slot cleanup message, không sửa vì ngoài scope.
6. **[INFO] `Dockerfile` lint warning** `ENV OPENROUTER_API_KEY=""` — có sẵn, không đụng.

## 9. Scope deviation (khai báo)

- `scp/api_server_parts/lifespan.py` được phép sửa "chỉ logging init", nhưng **bridge
  `validate_boot_settings` đã được thêm (3 dòng)**. Lý do bắt buộc về causality: lifespan là
  điểm boot **duy nhất** mà cả `python -m scp`, `uvicorn "scp.api_server:app"` lẫn TestClient
  proof-path đi qua; đặt ở `__main__.py` sẽ bỏ trống đường TestClient/uvicorn-string, đặt ở
  module-import `api_server` sẽ bắt `validate_boot_config()` chạy lúc collection và đổi
  semantics fail-closed hiện hữu của lifespan. Diff chỉ là wiring thuận theo gate có sẵn,
  không sửa logic khác.
- Ghi chú thêm: `configure_logging()` được gọi từ module-scope `api_server.py` (composition
  root) chứ không phải `create_app` (repo không có factory) — vẫn là MỘT call site duy nhất.

## 10. Open questions (không thể kết luận với evidence hiện tại)

- OTel `LoggingInstrumentor` (nếu bật `SCP_OTEL_ENABLED=1`) có tương tác gì với
  `ProcessorFormatter` trên root không — chưa chạy proof với OTel bật; đề xuất slot observability.
- 3 streaming failure pre-existing (§8.1) chỉ được kết luận đúng gốc khi Q08 chốt
  contract 503-vs-200 của `openai_compat`.
- Full-suite (T01→T05 + unit) chưa chạy toàn bộ trong phiên này; kết luận xanh chỉ trong các
  suite đã nêu ở §7.

## 11. Rollback

Revert 5 file (không commit): `git checkout -- scp/api_server.py scp/api_server_parts/lifespan.py`
+ xoá 2 untracked (`scp/core/config.py`, `scp/core/logging_config.py`) hoặc giữ nguyên trạng
thái untracked ban đầu + `git checkout -- tests/T03_capability/test_m2_architecture_backlog.py`.
Behavior quay về đúng `bf5af3b` (logging basicConfig, chỉ config_contract).
