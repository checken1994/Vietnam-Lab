# Q02 — CISA KEV cache provenance + force-refresh dedupe

**Worker:** Q02 · **Ngày:** 2026-09-15 · **Base:** `29dc446` (HEAD tại thời điểm nhận task; HEAD hiện `bd0f46b` — commit Q04 song song **không** đụng `scp/security/cisa_kev.py`, xác nhận `git diff 29dc446 HEAD -- scp/security/cisa_kev.py` rỗng). **Không commit** theo chỉ đạo.

## 1. Kết luận điều hành

Hai finding từ audit/verifier **được tái hiện bằng runtime probe trên code hiện hành trước khi sửa** (AUDIT-FIRST): (1) cache đĩa `data/cisa_kev.json` được tin theo `timestamp`/schema nguyên vẹn, kẻ có quyền ghi file cục bộ dựng cache future-date → `refresh()` trả `skipped/cache fresh` và `is_exploited()==True` — confidence CVE dương tính từ tệp chưa được kiểm chứng, ngay cả khi `SCP_EGRESS_MODE=deny`; (2) 8 caller `force=True` đồng thời → 8 transports.

Sau fix: cache chỉ được tin khi envelope mang provenance tự kiểm chứng (`schemaVersion`, `source` nằm trong tập trusted, fetch timestamp trong cửa skew/staleness, `count` khớp, checksum SHA-256 canonical của payload); mọi dạng khác → **neutral fail-closed** (`is_exploited` False, `refresh` không bao giờ báo "cache fresh" giả). Force-refresh vẫn vượt TTL/backoff theo ý operator nhưng **dedupe qua single-flight lock**: 8 caller song song → 1 transport (cả success lẫn failure path). Bằng chứng mức **module isolated-runtime** trong container `--network=none`; **không** phải bằng chứng production service mới restart.

## 2. Snapshot

| Trường | Giá trị |
|---|---|
| Commit base | `29dc446` (pushed, trong stack cee04da/bb6cb00/29dc446) |
| HEAD lúc báo cáo | `bd0f46b` (Q04, không conflict file này) |
| Blob file sửa | `cisa_kev.py=337d9f82…`, `test_cisa_kev_provenance.py=bee01ca4…`, `test_cisa_kev_cache.py=cfb9e20c…` |
| Cache đĩa | `data/cisa_kev.json` sha256 `e1d16a5e…` (new format, 1710 records, regenerated bằng module sau fix) |
| Skills + hashes | `scp-dna` `4aada0be…`, `scp-capability-security-review` `83f16332…`, `scp-runtime-audit` `63680fd1…` (sha256 prefix, `.agents/skills/*/SKILL.md`) |
| Python | 3.12.10 host; Docker 29.7.2, image `python:3.12-slim` |

## 3. Causal map (Why-chain)

1. **Tại sao false confidence?** `_load_cache` copy `data["timestamp"]` vào `_last_refresh` không kiểm tra gì → attacker/sai lệch cục bộ đặt timestamp tương lai → `now - _last_refresh < TTL` luôn đúng → `refresh()` short-circuit `skipped/cache fresh`.
2. **Tại sao payload độc cũng chạy?** Không có checksum/source/schema — `vulnerabilities[]` giả với `cveID` tùy ý đi thẳng vào `_vulns` → `is_exploited()==True` dương tính theo ý file.
3. **Tại sao predictor ăn dương tính?** `_cisa_kev_lookup_sync` coi `refreshed|skipped` là feed khả dụng → `cisa_boost +0.40` và evidence `cisa_kev:actively_exploited` (predictor.py:293,557).
4. **Tại sao stampede force?** Trong lock, cả TTL re-check lẫn backoff đều gate `not force` → N caller `force=True` nối đuôi nhau **mỗi người một transport**; lock chỉ serialize, không dedupe.
5. **Assumption đã soi:** file cache là **untrusted data** (AGENTS.md invariant) — pre-fix vi phạm đúng invariant này bằng cách coi nó là lineage đã verify khi transport bị deny.

## 4. Fix (product: chỉ `scp/security/cisa_kev.py`)

- `_validate_cache_payload()` (mới): envelope phải có `schemaVersion==1`, `source ∈ {CISA_KEV_URL, CISA_KEV_URL_FALLBACK}`, `timestamp` numeric/finite/`>0`, **future-skew ≤ 300s**, **staleness ≤ 7 ngày**, entries là dict có `cveID` str, `count` khớp, `checksum == "sha256:" + SHA256(canonical-json(vulnerabilities))`. Lỗi → `(False, reason)` ghi log, **không** load.
- `_load_cache()` rewrite: reject → giữ `_vulns={}`, `_last_refresh=0` → mọi lookup neutral; `refresh` buộc đi transport (vẫn qua choke point `safe_urlopen`).
- `_write_cache_atomic()` (mới): `mkstemp + os.replace`, ghi envelope provenance **trước** khi đổi in-memory state (disk và memory không bao giờ bất nhất).
- `refresh()`: giữ TTL/backoff/timeout như cũ; thêm `_refresh_generation` + `_last_refresh_summary` — caller chờ trong lock mà generation đã đổi thì **dùng lại kết quả attempt vừa xong** (`deduplicated: true`), kể cả khi attempt đó `failed` → 1 transport cho cả cụm force. Không đổi signature/action vocabulary; caller (`predictor.py`) không cần sửa.
- Predictsor **không đổi**: `action not in {refreshed, skipped}` → neutral đã đúng, vì sau fix `skipped` chỉ còn khi cache validated-fresh.
- Extras nhỏ (không đổi policy): getter `get_recent/get_by_product/get_by_vendor` snapshot dưới `_refresh_lock`; `stats()` thêm `catalog_version`.

## 5. Evidence table

| # | Claim | Pre-fix (probe trên code hiện hành) | Post-fix | Lineage |
|---|---|---|---|---|
| E1 | future-date cache → skipped+True | `refresh()={'action':'skipped','reason':'cache fresh','count':1}`, `is_exploited('CVE-2026-FAKE999')=True`, `cache_age_hours=-240.0`, gate THẬT `SCP_EGRESS_MODE=deny` | `{'action':'failed','error':'EgressDeniedError'}` + `False` + log `Cache rejected (missing or unknown schemaVersion)` | host process, real choke point |
| E2 | 8 concurrent force → 8 transports | `transports=8` | `transports=1` | host process, counting transport |
| E3 | Container isolated (`--network=none`, repo `:ro`, env deny) | — | Q1 forged-future / Q2 legacy / Q3 tampered-after-write → `failed`+`False`; Q4 valid cache → `skipped`+True, **zero transport** (vật lý không có mạng: `gaierror`); Q5' barrier-8 force → **1 transport, 7 deduplicated**, `is_exploited=True` | **independent lineage** (Docker, khác lineage host) |
| E4 | Unit suite | — | `tests/T03_capability/test_cisa_kev_provenance.py` (19 tests, gồm 13 rejection-variant parametrized, dedupe success+failure, sequential-force-refetch, force-vượt-backoff, failed-refresh không phá cache tin cậy) + `test_cisa_kev_cache.py` (3) + `test_egress_enforcement.py` (26 pass/2 skip-declared-sẵn) + `test_m2_empirical_challenger.py` (12) → **64 passed, 2 skipped** | pytest, transport kiểm soát hoàn toàn, không network thật |
| E5 | Gates | — | `py_compile` OK; `ruff check` full + `--select E9,F` pass; `tools/t00_meta_audit.py`: **0 new regressions** (lần 1 FAIL tripwire `silent_except_pass 7>6.3` vì `except OSError: pass` mới — đã sửa thành `logger.debug`, không nới baseline); `tools/verify_scp_test_skill_contract.py`: `PASS_WITHIN_SCOPE` | toolchain |
| E6 | Happy-path real feed (network thật, dev box, cho phép) | — | fetch catalog 2026.09.14, 1710 records; `is_in_kev('CVE-2023-38606')=True`; cache ghi new-format, reload skip | host, **có** network (ngoài phạm vi isolated proof) |

## 6. NEW FINDINGS (file:line · severity · next slot)

| ID | Finding | Vị trí | Severity | Next slot |
|---|---|---|---|---|
| N1 | **Time-bomb test harness (pre-existing):** `test_m2` tự đặt `SCP_EGRESS_MODE=deny` (fixture autouse) rồi assert positive từ cache đĩa; nếu `data/cisa_kev.json` > 6h TTL hoặc absent (fresh clone) → `failed`→neutral→assert True fail. Tồn tại y hệt ở HEAD-base (không phải regression Q02). | `tests/test_m2_empirical_challenger.py:18-21,127,159` | MEDIUM | Test-hermeticity: seed fixture-cache new-format qua helper module hoặc monkeypatch `_FEED_SINGLETON` trong chính file m2 |
| N2 | **flow_06 teardown errors:** `sqlite3.OperationalError: database is locked` tại cleanup fixture (`db_exec`) — 35s busy-timeout; **tái hiện giống hệt khi product file revert về HEAD** (baseline `.E.E.E.E.E.EFE…`) → không liên quan Q02; một test F cuối chuỗi cũng thấy ở cả 2 phía. | `tests/T03_capability/test_flow_06_prediction_scp_standard.py:114` ← `scp/core/db_manager_parts/db_exec.py:27` | HIGH (chắn suite xanh release) | Task DB-lifecycle riêng (connection/transaction quanh prediction run-cycle) |
| N3 | **Provenance ≠ authenticity:** attacker có write vào `data/` vẫn forge được envelope hợp lệ hoàn chỉnh (source constant công khai, digest tự tính, timestamp hiện tại). Digest chỉ là integrity; cần signature/HMAC-key để anti-forgery. | `scp/security/cisa_kev.py:60-108` | MEDIUM (cần local write) | Next wave: ký catalog (cosign/notation hoặc HMAC từ secret store) + OS ACL trên `data/` |
| N4 | Predictor `_KEV_RESULT_CACHE` giữ boolean tới 6h sau validated lookup; cache file đổi/expire không invalidate in-memory per-CVE results. Không phải surface mới (memory chỉ tới từ validated lineage), nhưng stale-positive khi catalog retract entry. | `scp/security/predictor.py:154,200-206` | LOW | Invalidate result cache theo `catalogVersion`/generation |
| N5 | `is_exploited()` chờ `_refresh_lock` xuyên qua transport (tới `SCP_CISA_KEV_TIMEOUT_SECONDS`≤30s) — pre-existing; predictor async đã có outer timeout (`SCP_CISA_KEV_LOOKUP_TIMEOUT_SECONDS`). | `scp/security/cisa_kev.py` refresh/is_exploited | LOW (INFO) | Fetch ngoài lock + double-check generation nếu cần latency |
| N6 | `SCP_EGRESS_ALLOWLIST` trong `.env` dev có `raw.githubusercontent.com` → ngoài pytest, fetch KEV succeed tự do; conftest chỉ restore parent-env keys sau collection, một số code path vẫn load `.env` lúc runtime. Đúng policy nhưng cần operator biết. | `tests/conftest.py:22-40`, `.env` (không trích giá trị) | LOW (INFO) | Docs/ops note |

## 7. Verdict (scp-runtime-audit labels)

- `OBSERVED`: E1/E2/E3/E4/E5/E6 như bảng trên.
- `SUPPORTED_INFERENCE`: predictor không đổi mà vẫn neutral-on-unvalidated, vì `skipped` giờ chỉ xuất hiện sau validation (đọc code + E4 test `test_cisa_no_egress_is_neutral_and_backed_off`).
- `UNPROVEN`: production service thật restart + dashboard consumption của feed; chưa chạy lại toàn bộ flow_06 đến hết (N2 pre-existing DB-lock); chưa chạy CI.
- Trạng thái phạm vi Q02: **module-level runtime-proven trong isolated container**; hệ thống SCP tổng thể không tuyên bố gì thêm.

## 8. Commands (reproduce)

```bash
# Host verification
python -m py_compile scp/security/cisa_kev.py tests/T03_capability/test_cisa_kev_provenance.py tests/T03_capability/test_cisa_kev_cache.py
ruff check scp/security/cisa_kev.py tests/T03_capability/test_cisa_kev_provenance.py tests/T03_capability/test_cisa_kev_cache.py
python -m pytest tests/T03_capability/test_cisa_kev_provenance.py tests/T03_capability/test_cisa_kev_cache.py tests/T03_capability/test_egress_enforcement.py tests/test_m2_empirical_challenger.py -q
python tools/t00_meta_audit.py && python tools/verify_scp_test_skill_contract.py
# Isolated container proof (no network, repo read-only, egress gate env)
MSYS_NO_PATHCONV=1 docker run --rm -i --network=none -v "$PWD:/app:ro" -w /app \
  -e SCP_EGRESS_MODE=deny python:3.12-slim python -   # probes Q1–Q5' (script inline trong transcript)
```

## 9. Open questions

1. Có đưa **signature verification** (N3) vào wave bảo mật trí tuệ dữ liệu không — nếu `data/` nằm trên volume share?
2. `MAX_CACHE_STALENESS_SECONDS=7d` chọn theo nhịp cập nhật CISA "multiple times/week"; operator production có muốn env-override không (hiện giữ mặt config hẹp)?
3. N1: seed fixture cho m2 ngay slot này hay gộp vào test-hermeticity pass?
4. N2: DB-lock flow_06 cần slot riêng trước release gate nào tiếp theo?

## 10. Boundary

- Sản phẩm sửa duy nhất: `scp/security/cisa_kev.py`. Tests: `test_cisa_kev_provenance.py` (mới), `test_cisa_kev_cache.py` (strengthen: pin `_FEED_SINGLETON` vào feed tmp — tăng tính deterministic, không skip/xfail/hạ chuẩn). Không đổi `predictor.py`. Không đổi egress policy/URL allowlist. Không commit.
- `data/cisa_kev.json` (untracked, runtime artifact) đã regenerate bằng code mới → các test positive offline chạy được tới TTL 6h (xem N1).
