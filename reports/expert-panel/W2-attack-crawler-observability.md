# W2 — AttackCrawler honest aggregate logging (observability)

Worker: W2 · Repo: `C:\Users\check\Downloads\scp` · HEAD (snapshot): `018ed2176a4a17ff3993e5c6cd3555b05943d520`
Skills loaded: `scp-dna` + `scp-reality-verifier`. No commit/push performed.

## 1. Problem statement (Why-chain)

`AttackCrawler.crawl_all()` (in `scp/security/attack_crawler.py`) aggregated three
crawl sources — GitHub, HuggingFace, Reddit — and logged one final line. Before the
fix, when the run produced no new attacks it always logged at INFO:

```python
logger.info("AttackCrawler: no new attacks found")
```

Tại sao đó là lỗi?
1. Tại sao operator tin "không có attack mới"? Vì log nói "no new attacks found" ở mức INFO.
2. Tại sao log nói vậy dù crawler có thể đang bị chặn? Vì dòng aggregate không biết gì về tình trạng từng nguồn.
3. Tại sao nó không biết? Vì `EgressDeniedError` **không** chạm tới `except` cấp `crawl_all`. Mỗi `_crawl_*` nuốt nó trong `except Exception` cấp repo/dataset/keyword (GitHub `:188,:208`; HF `:227,:267`; Reddit `:307,:317`), return `[]`, rồi `crawl_all` coi nguồn đó "thành công với 0 kết quả".
4. Kết luận (missing piece được phát hiện): **chỉ đếm exception cấp `crawl_all` KHÔNG đủ** — trong môi trường `SCP_EGRESS_MODE=deny`, cả ba nguồn trả `[]` mà không raise, nên vẫn in INFO "no new attacks". Đó chính xác là "crawler mù nhưng báo yên ổn".

## 2. Reality evidence (audit-first)

- `_crawl_github`: 14 repo × (readme + issues). `EgressDeniedError` không chứa "403"/"rate limit" → rơi vào nhánh warning thường, lặp hết repo, return `[]`.
- `_crawl_reddit`: `EgressDeniedError` (subclass của `PermissionError`/`ValueError`, không phải `URLError`) → bị `except Exception` (:317) bắt, retry 3 lần/subreddit, return `[]`.
- `_crawl_huggingface`: dùng `datasets` (stack HTTP riêng, không qua `safe_urlopen`); nếu thiếu lib → `ImportError` (:227) return `[]`; nếu lỗi network → `except` (:267) return `[]`.
- Hệ quả: dòng `logger.info("no new attacks found")` và dòng "found N" (`:141`) tham chiếu `len(gh_attacks)` không guard → khi GitHub raise mà nguồn khác có kết quả sẽ `NameError`.

## 3. Fix (small, reversible)

File: `scp/security/attack_crawler.py` (±87/−11). Test mới: `tests/T03_capability/test_attack_crawler_observability_w2.py`.

1. **Tally bất chấp nuốt-exception.** Mỗi `except` fetch thật sự gọi `self._record_crawl_error(source, e)`, lưu **CLASS NAME** duy nhất (`type(e).__name__`) — không bao giờ `str(e)` (message của `EgressDeniedError`/`HTTPError` chứa URL). `_crawl_errors` reset đầu mỗi `crawl_all()`.
2. **Phân loại nguồn.** Trong `crawl_all`, một nguồn là FAILED khi `raw==0 AND _crawl_errors[name]` non-empty. Nguồn trả dữ liệu (>0) hoặc chạy sạch 0 (cache hết / thật sự không mới) → không phải FAILED.
3. **Aggregate trung thực:**
   - `unique` > 0 → INFO "found N" (kèm raw breakdown; nếu có nguồn lỗi thì phụ chú `| DEGRADED: n/m ...`).
   - `unique == 0` và có nguồn FAILED → **WARNING** (một phần) / **ERROR** (mù toàn bộ `n==m`), nội dung: `"0 new attacks but N/M crawl sources FAILED (...) 'no new attacks' is NOT trustworthy — the crawler may be blind (e.g. egress denied)"`, chỉ kèm tên nguồn + class name lỗi.
   - `unique == 0` và không nguồn nào FAILED → giữ nguyên INFO "AttackCrawler: no new attacks found".
4. **Không đổi logic an toàn.** Vẫn `safe_urlopen` → `enforce_egress_policy` + chỉ http/https; không mở allowlist; loopback/private vẫn bị chặn. `crawl_all` vẫn return `[]` khi deny (không raise), khớp nguyên tắc PRED-8. Việc duy nhất thêm vào là tally + logging. Sửa phụ: biến `gh/hf/reddit_attacks` luôn được bind → xóa `NameError` tiềm ẩn.

## 4. Verification (same snapshot)

| Gate | Lệnh | Kết quả |
|---|---|---|
| Compile | `python -m py_compile scp/security/attack_crawler.py tests/.../test_attack_crawler_observability_w2.py` | PYCOMPILE_OK |
| New W2 tests | `pytest tests/T03_capability/test_attack_crawler_observability_w2.py -v` | **6 passed** (blind→ERROR, partial→WARNING, healthy 0→INFO, found→INFO, GitHub-raise regression, real `_crawl_github` tallies `EgressDeniedError`) |
| Crawler + egress + PRED-8 | `pytest test_egress_enforcement.py test_flow_06::...test_prediction_crawl_external_apis test_flow_09...` | **50 passed, 2 skipped** (2 skip = pre-existing opt-in container proof, not from this change) |
| Contract | `python tools/verify_scp_test_skill_contract.py` | `PASS_WITHIN_SCOPE`, exit 0 |
| t00 | `python tools/t00_meta_audit.py` | "All integrity checks passed (0 new regressions)", exit 0 |

Test design note: chỉ fake **transport seam** (`safe_urlopen`) và, ở các test aggregate, thay fetch method bằng stub gọi `_record_crawl_error` **thật** + trả `[]`. Toàn bộ `crawl_all` (orchestration, dedup, phân loại, logger) và `_record_crawl_error` chạy thật → không mock logic crawler. Test `TestBlindSourceDetection` chạy `_crawl_github` **thật** với `safe_urlopen` raise `EgressDeniedError` để chứng minh cơ chế phát hiện mù hoạt động ở tầng nguồn.

## 5. Scope / limits (PASS != TRUE)

- Bằng chứng ở cấp **A/B (unit/integration)**: chứng minh classifier + tally + log-level đúng trong các kịch bản dựng sẵn. CHƯA có cấp C/D — chưa chạy crawler thật với internet thật/egress-thật, chưa đo trên `start_crawl_thread` đang chạy nền.
- Chỉ sửa `attack_crawler.py` + 1 test mới. Không đụng `url_safety.py`, egress policy, hay file khác.
- Các `logger.warning(f"... failed: {e}")` per-source CŨ vẫn in `str(e)` (chứa URL công khai của api.github.com/reddit) — KHÔNG đổi để giữ tối thiểu diff; yêu cầu secret-safe của task áp cho **aggregate log mới**, đã thỏa (chỉ class name).

## 6. PHÁT HIỆN MỚI (new findings)

1. **[Medium, đã sửa một phần] Blind-crawler detection không thể làm ở tầng `crawl_all`.** `EgressDeniedError` bị nuốt trong `_crawl_*`. Mọi fix chỉ đếm top-level exception sẽ giả mạo "all-clear". Fix này tally tại chỗ nuốt.
2. **[Low, ĐÃ SỬA] Latent `NameError`** tại dòng "found N" cũ (`len(gh_attacks)` không guard, trong khi `hf/reddit` dùng `dir()` guard) → crash `crawl_all` nếu GitHub raise mà nguồn khác có kết quả. Nay cả ba list luôn bind. Có test regression.
3. **[Low, CÒN MỞ] HuggingFace không đi qua egress choke point.** `_crawl_huggingface` dùng `datasets` (HTTP stack riêng), nên `SCP_EGRESS_MODE` **không** chặn được HF — đây là lỗ hổng nhất quán choke-point (url_safety.py ghi "single EGRESS CHOKE POINT"). Ngoài scope W2 (không được sửa `datasets` path / không mở rộng), nhưng cần ticket riêng: HF crawl có thể là đường egress chưa kiểm soát.
4. **[Low, CÒN MỞ] Reddit retry `time.sleep` khi deny** (~1–2s × 3 × subreddit) làm chậm chu kỳ mù; không đổi vì thuộc retry-safety cũ, nhưng "blind + egress deny" sẽ lặp vô ích mỗi cycle.

## 7. Open questions

- Classifier coi nguồn có `raw==0` + `ImportError` (thiếu `datasets`) là FAILED → WARNING khi 0 kết quả. Đây có phải "tín hiệu operator muốn" hay cần phân loại riêng "config-missing" vs "network-denied"?
- Có nên sanitizer cả per-source warning cũ (item 5) để nhất quán secret-hygiene không, hay giữ như evidence hiện hành?
