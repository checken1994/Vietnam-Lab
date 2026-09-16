# A1 — HF egress choke: bịt bypass `datasets.load_dataset` trong AttackCrawler

- Ngày: 2026-09-16 · Branch: `audit/runtime-guard-AUDIT-20260909` · Base HEAD: `d1f3433` (chưa commit — orchestrator verify rồi commit)
- Skill bindings đã đọc trước khi làm: `scp-dna`, `scp-capability-security-review`
- Verdict: **PASS_WITHIN_SCOPE** — "PASS" chỉ nghĩa là không quan sát thấy failure trong phạm vi test nêu ở mục VERIFY; KHÔNG phải tuyên bố hệ thống hoàn chỉnh/an toàn tuyệt đối.

## 1. Problem statement

`AttackCrawler._crawl_huggingface` fetch payload qua `datasets.load_dataset`
(HTTP stack riêng của `huggingface_hub`), **không đi qua** egress choke point
`scp.security.url_safety.enforce_egress_policy`. Hệ quả: `SCP_EGRESS_MODE=deny`
mặc định (compose.yml:33) vẫn để HF tải payload từ mạng; và nguồn HF không bao
giờ sinh `EgressDeniedError` nên tally W2 không thể xem HF là FAILED.

## 2. Why chain (tóm tắt)

1. Tại sao HF ra được mạng khi deny? → `load_dataset` tự quản lý session
   (requests/httpx nội bộ của huggingface_hub), không import `url_safety`.
2. Tại sao tally W2 mù với HF? → W2 tally lỗi phát sinh tại các call-site
   `safe_urlopen`/`load_dataset`; nhưng bypass không đi qua choke nên mode deny
   không raise gì — HF "thành công im lặng" hoặc fail vì lý do khác.
3. Tại sao bug sống lâu? → static latch `egress_static_scan.py` chỉ scan
   urllib/requests/httpx/aiohttp + client-tracked; **không** phát hiện import
   stack HTTP của bên thứ ba (`datasets`). → NEW FINDINGS NF-01/NF-05.
4. Bằng chứng độc lập bổ sung: `datasets` không khai báo ở requirements.txt
   hay Dockerfile → trên container production, đường cũ chỉ là nhánh ImportError
   (tức HF crawler vốn đã hỏng sẵn); đường "có mạng" chỉ tồn tại ở dev có cài
   tay `datasets`. Sửa sang HTTP API chính thống qua `safe_urlopen` là strictly-better.

## 3. Audit trước khi sửa (tự kiểm, khớp orchestrator)

| Nguồn | Đường fetch | Qua choke? | Vị trí |
|---|---|---|---|
| GitHub | `safe_urlopen` | CÓ | `scp/security/attack_crawler.py:249,:269` (line cũ) |
| Reddit | `safe_urlopen` | CÓ | `:366` (line cũ) |
| HuggingFace | `datasets.load_dataset` | **KHÔNG** | import `:298`; gọi `:313,:321` (line cũ) |

- `grep load_dataset` toàn repo (ngoài `.debug-context-*`/`.tmp-*` snapshots): đúng 1 call-site.
- `url_safety.safe_urlopen`: `enforce_egress_policy` → `validate_url` (scheme http/https + reject private/loopback/reserved IP) → chỉ sau đó mới `urlopen`. ĐIỀU KIỆN CHẤP NHẬN không bị nới ở bất kỳ đâu.

## 4. Thay đổi (small, reversible)

### `scp/security/attack_crawler.py`
- Thêm constants block `HF_DATASETS_SERVER_URL/HF_SCAN_ITEM_LIMIT/HF_ROWS_PAGE_SIZE/HF_SPLIT_PRIORITY` (host **không** vào default allowlist — operator tự opt qua `SCP_EGRESS_ALLOWLIST`).
- Thay `_crawl_huggingface` bằng 3 helper mới, **chỉ** dùng `safe_urlopen`:
  - `_hf_api_get` — GET `https://datasets-server.huggingface.co{path}?...`; token `HF_TOKEN` (nếu có) chỉ vào header `Authorization: Bearer`, không vào URL, không vào log.
  - `_hf_resolve_split` — `/splits`, giữ nguyên ưu tiên split cũ (train > jailbreak > regular > test > validation, fallback split đầu khả dụng).
  - `_hf_fetch_rows` — `/rows` phân trang 100, cap 200 rows/dataset.
- Lỗi per-dataset: tally `_record_crawl_error("huggingface", e)` (CHỈ class name) + log `type(e).__name__` — không in str(e)/URL.
- Hành vi: deny → mỗi dataset raise `EgressDeniedError` → `raw_counts["huggingface"]=0` + error set khác rỗng → **W2 phân loại HF=FAILED** (WARNING một phần / ERROR nếu cả 3 nguồn blind). Không còn trả 0 im lặng.

### `tests/T03_capability/test_attack_crawler_hf_egress_a1.py` (mới)
Fake transport ở **TẦNG HTTP** (`urllib.request.urlopen` dưới choke + `socket.getaddrinfo` cho DNS SSRF deterministic); crawler logic, `enforce_egress_policy`, `validate_url`, tally W2 chạy THẬT:
- (a) deny → 0 byte tới transport, `{"EgressDeniedError"}` được tally, `crawl_all` escalate ERROR "3/3 ... NOT trustworthy", log không chứa host/URL.
- (b) allowlist hợp lệ → fetch end-to-end chạy, URLs toàn bộ `https://datasets-server.huggingface.co/...`, split preference giữ nguyên, error tally rỗng; (b2) allowlist miss → vẫn chặn tại choke.
- (c) AST scan: `load_dataset` không còn là symbol trong code của attack_crawler; `datasets` không còn import ở bất kỳ file nào trong `scp/`.

KHÔNG đổi: `url_safety.py`, allowlist defaults, compose, static gate pins, semantics W2.

## 5. VERIFY (lệnh thật đã chạy, kết quả)

| # | Lệnh | Kết quả |
|---|---|---|
| 1 | `python -m py_compile scp/security/attack_crawler.py tests/T03_capability/test_attack_crawler_hf_egress_a1.py` | OK |
| 2 | `pytest test_attack_crawler_hf_egress_a1.py test_attack_crawler_observability_w2.py test_egress_enforcement.py -v` | **39 passed, 2 skipped** (skip = container opt-in `SCP_EE_CONTAINER_TESTS` có sẵn từ trước, declared, không phải skip mới) |
| 3 | `pytest tests/T03_capability/test_flow_09_threat_analysis_scp_standard.py -q` | **23 passed** (consumer của AttackCrawler không regression) |
| 4 | `python tools/verify_scp_test_skill_contract.py` | **PASS_WITHIN_SCOPE, rc=0** (profile_sha256 `31728d958b529bd5...480bfe`, commit `d1f3433...`) |
| 5 | `python tools/t00_meta_audit.py` | **rc=1 — PRE-EXISTING, KHÔNG do change này** (xem §6) |
| 6 | `grep -rn "load_dataset" scp/ --include="*.py"` | chỉ còn trong prose comment/docstring (attack_crawler:67,:370); AST test chứng minh không còn code symbol |

## 6. T00 pre-existing failure (ghi trung thực, không paper over)

`tools/t00_meta_audit.py` fail-closed ở bước **"Collecting baseline pytest
nodeids (origin/main)"** — snapshot origin/main trong temp dir (`tmpnakqdzs7`),
TRƯỚC khi đánh giá working tree. Nguyên nhân: `tests/test_api.py:5` (bản
origin/main) gọi `_url_open(req)` tới `http://127.0.0.1:8000/...` ngay lúc
import → ConnectionRefused khi server không chạy. Đã independently xác nhận
bằng `git show origin/main:tests/test_api.py`. Không liên quan HF/egress; để
NGUYÊN cho slot harness (NF-03).

## 7. Rollback path

`git restore scp/security/attack_crawler.py` + xoá file test mới → trở lại
nguyên trạng d1f3433. Non-fatal guard: nếu datasets-server API đổi schema, mỗi
dataset fail riêng lẻ → tally FAILED (W2) chứ không crash crawl_all.

## 8. Semantics deltas (chấp nhận có ý thức, cần operator biết)

1. Mất pin `revision="main"` — datasets-server resolve revision mặc định mới nhất. Nếu cần reproducibility, thêm param `revision` vào `_hf_api_get` (open question §9).
2. Cap 200 giờ tính theo **rows fetch về** (trước: 200 item có text hợp lệ qua streaming). Khác biệt nhỏ vì extract vẫn lọc theo cùng keyword rules.
3. Nhánh `ImportError → "pip install datasets"` biến mất: `datasets` không còn là dependency mềm.

## 9. Open questions

- Gated datasets (cần consent HF) sẽ trả 4xx khi không có token đủ quyền → tally FAILED đúng, nhưng operator cần đọc warning class name để phân biệt với deny. Có nên thêm label riêng `HTTPError` vs `EgressDeniedError`? (Đã có sẵn class name trong tally — khả năng đủ.)
- Có nên cho phép override base URL datasets-server cho enterprise mirror? Nếu có, phải validate qua cùng choke và không cho host-list rộng.

## PHÁT HIỆN MỚI (NEW FINDINGS)

| ID | Severity | Vị trí | Mô tả | Đề xuất slot |
|---|---|---|---|---|
| NF-01 | HIGH | `scp/core/github_backup.py:90-93` | `from huggingface_hub import HfApi, upload_file` — upload backup đi HTTP stack riêng của huggingface_hub, **vẫn là egress bypass còn lại** (cùng lớp lỗi vừa bịt ở crawler) và là external-write (risk tier R2) | A2/W4: route qua choke hoặc pin có approval; đồng thời mở rộng static scan bắt import stack HTTP bên thứ ba (xem NF-05) |
| NF-02 | MEDIUM | `scp/security/attack_crawler.py:232` | `gh_token = os.environ.get("GITHUB_TOKEN", os.environ.get("HF_TOKEN", ""))` — khi thiếu GITHUB_TOKEN, **token HF bị gửi sang api.github.com** trong header `Authorization: token ...` (cross-service credential misuse; token lên wire tới service không thuộc về nó) | Security hygiene nhỏ: bỏ fallback chéo service, chỉ dùng GITHUB_TOKEN |
| NF-03 | MEDIUM (harness) | `tests/test_api.py:1-5` (origin/main) | Test script thô chạy `urlopen` at import-time tới 127.0.0.1:8000 → mọi pytest collection (kể cả T00 baseline) fail-closed khi server không chạy; chính nó cũng là raw urllib call không qua choke (may: loopback) | Slot test-integrity: convert thành test có fixture loopback thật hoặc move khỏi pytest path |
| NF-04 | LOW | `scp/security/attack_crawler.py:67,:370` + module docstring `:5-12` | Prose còn tham chiếu `datasets.load_dataset`/nguồn cũ ("4 datasets") — chỉ là lịch sử, AST gate không bắt; dễ gây nhầm cho reader | Docs polish khi orchestrator commit |
| NF-05 | MEDIUM (gap điều khiển) | `scp/security/egress_static_scan.py` (thiết kế) + `tests/T03_capability/test_egress_enforcement.py:237-268` | Static latch chỉ phát hiện raw urllib/requests/httpx/aiohttp + client vars; **không** phát hiện stack HTTP của third-party libs (đúng kiểu lỗi HF bypass vừa đóng). NF-01 sống sót qua latch này | Mở rộng census: scan AST import allowlist-based các module có HTTP stack (datasets, huggingface_hub, boto3, ... ngoài allowlist phải pin + justify) |

## Evidence limits

- Chưa chạy runtime thật chống `datasets-server.huggingface.co` (môi trường test deterministic offline; payload thật của API được fixture theo schema công khai của datasets-server `/splits`,`/rows`).
- Container tests (section h của egress suite) vẫn opt-in, chưa bật ở phiên này.
- T00 toàn phần rc=1 vì lỗi baseline nêu trên; các gate bắt buộc trong phạm vi (compile, T03 liên quan, skill contract) đều xanh.
