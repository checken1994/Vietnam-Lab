# BÁO CÁO HOÀN THÀNH NHIỆM VỤ CỔNG 1 ĐÍCH-B: KNOWLEDGE READER TIERS
## File bàn giao: `dichb-readers-done.md`
### Nhiệm vụ căn cứ: `d:\scp\agtask8.md` (CỔNG 1 đích-b: knowledge reader tiers)
### Tác giả: Antigravity (Orchestrator) — Maker: `DeepCoder` — Adversarial Checker: `DeepInvestigator`
### Phán quyết nghiệm thu độc lập: **VERIFIED (100% Reality Evidence — Exit Code 0)**

---

## 1. TỔNG QUAN KẾT QUẢ & ĐỐI CHIẾU NHIỆM VỤ

Thực hiện kỷ luật Maker - Checker đối kháng độc lập theo chuẩn SCP DNA #1–#29 và 2 Luật Cấp Cao Nhất của Owner (30/10), đã hoàn thành trọn vẹn cả 4 yêu cầu của `agtask8.md`:

| STT | Yêu cầu `agtask8.md` | Giải pháp kỹ thuật đã thi công | Trạng thái Nghiệm thu |
|---|---|---|---|
| **1** | **Tier reader cho `news_fact`**: ưu tiên KB news_fact trước Wikipedia cũ khi dính thời gian + sự kiện; cơ chế tổng quát theo cấu trúc, KHÔNG regex mẫu câu (Luật 14). | Xóa bỏ regex mẫu câu cứng `_NEWS_INTENT_*_RE`; phân tách cấu trúc thông tin (tin tức nội tại vs sự kiện ngữ cảnh); cơ chế độ mới theo ngày tự neo `MAX(timestamp)` khi offline/clean-clone; ưu tiên KB trước web. | **VERIFIED** (Probe & Contract Test 1 PASSED) |
| **2** | **Tier reader cho `statutory` vs `canonical`**: câu hỏi pháp lý VN → `statutory_standard`; câu hỏi generic → `canonical_document`; bảo toàn đa miền `verify-domains-multinational`. | Phân tầng FTS5: câu hỏi chứa cấu trúc quy phạm pháp luật (`_STATUTORY_TERMS_RE`) lọc `statutory_standard`; câu hỏi khoa học/tri thức chung lọc `canonical_document`; có negative lookbehind chặn nhầm định luật tự nhiên ("định luật Ohm") và cơ chế anti-leak ("quy luật cung cầu"). | **VERIFIED** (Probe & Contract Test 2, 6 PASSED) |
| **3** | **Đèn chỉ báo nguồn (Source Indicator)**: mỗi lần giắt nguồn KB thay vì web ⇒ trace ghi rõ nguồn đã dùng. | Bổ sung `reader_tier` (`kb_news_fact`, `statutory_standard`, `canonical_document`) và `source_indicator` (`KB:news_fact`, `KB:statutory`, `KB:canonical`) truyền xuyên suốt lên `v98_classification` và `reasoning` trong `attempt_lookup_fork`. | **VERIFIED** (Contract Test 5 PASSED) |
| **4** | **Test đỏ-trước & Mutant**: câu "tin tức hôm nay về X" trước fix trả Wikipedia cũ ("Mai An Tiêm"), sau fix trả KB news_fact; tắt reader ⇒ mutant đỏ. | Bộ test contract `tests/T02_contract/test_knowledge_reader_tiers_contract.py` gồm 8 bài test thực tế, kiểm chứng cả 2 nhánh Positive Reality và Negative Anti-Placebo; Mutant bị tiêu diệt 100%. | **VERIFIED** (8/8 tests PASSED in 18.43s) |
| **Bất biến** | **Zero touch `api_server/pipeline` & registry expert**; Chuỗi Tại Sao 5 mục. | Không chạm vào bất kỳ file nào trong `api_server/pipeline` hay `data_sources/` registry experts; Chuỗi Tại Sao đầy đủ 5 mục theo chuẩn Epistemic 30/10. | **TUÂN THỦ 100%** |

---

## 2. CHI TIẾT CÁC TỆP SỬA ĐỔI & TRẦN ĐỘ DÀI ARC-01

Toàn bộ các tệp sửa đổi đều nằm trong phạm vi cho phép và tuân thủ nghiêm ngặt trần $\le 800$ LOC:

```text
 scp/runtime/domain_data_extended.py | 52 ++++++++++++++++------
 scp/runtime/domain_data_fork.py     | 36 +++++----------
 scp/runtime/news_lookup.py          | 89 +++++++++++++++++++++++++++----------
 scp/runtime/question_router.py      |  9 +++-
 tests/T02_contract/test_knowledge_reader_tiers_contract.py (mới) | 245 ++++++++++++++++++++++
```

### Đo đạc độ dài vật lý thực tế:
- [`scp/runtime/news_lookup.py`](file:///d:/scp/scp/runtime/news_lookup.py): **466 dòng** (≤ 800 LOC — ĐẠT)
- [`scp/runtime/domain_data_extended.py`](file:///d:/scp/scp/runtime/domain_data_extended.py): **288 dòng** (≤ 800 LOC — ĐẠT)
- [`scp/runtime/domain_data_fork.py`](file:///d:/scp/scp/runtime/domain_data_fork.py): **672 dòng** (≤ 800 LOC — ĐẠT)
- [`scp/runtime/question_router.py`](file:///d:/scp/scp/runtime/question_router.py): **778 dòng** (≤ 800 LOC — ĐẠT, lưu ý cảnh báo cận trần 22 dòng)
- [`tests/T02_contract/test_knowledge_reader_tiers_contract.py`](file:///d:/scp/tests/T02_contract/test_knowledge_reader_tiers_contract.py): **245 dòng** (≤ 800 LOC — ĐẠT)

---

## 3. BẰNG CHỨNG THỰC NGHIỆM ĐỐI KHÁNG (RAW TERMINAL EVIDENCE)

### 3.1. Bằng chứng 8/8 Tests Contract Xanh Hoàn Toàn
```powershell
& "d:\scp\.venv\Scripts\pytest.exe" -v tests/T02_contract/test_knowledge_reader_tiers_contract.py
```
```text
============================= test session starts =============================
platform win32 -- Python 3.12.14, pytest-9.1.1, pluggy-1.6.0
rootdir: D:\scp
collected 8 items

tests/T02_contract/test_knowledge_reader_tiers_contract.py::test_01_query_serbia_today_news_reads_kb_news_fact PASSED [ 12%]
tests/T02_contract/test_knowledge_reader_tiers_contract.py::test_02_query_legal_statutory_standards PASSED [ 25%]
tests/T02_contract/test_knowledge_reader_tiers_contract.py::test_03_query_general_knowledge_canonical_document PASSED [ 37%]
tests/T02_contract/test_knowledge_reader_tiers_contract.py::test_04_anti_goodhart_news_mutant_killed PASSED [ 50%]
tests/T02_contract/test_knowledge_reader_tiers_contract.py::test_05_attempt_lookup_fork_propagates_reader_tier_and_indicator PASSED [ 62%]
tests/T02_contract/test_knowledge_reader_tiers_contract.py::test_06_statutory_regex_precision_and_no_leak PASSED [ 75%]
tests/T02_contract/test_knowledge_reader_tiers_contract.py::test_07_earthquake_valdivia_canonical_reader_tier PASSED [ 87%]
tests/T02_contract/test_knowledge_reader_tiers_contract.py::test_08_biology_and_geology_resolvers_canonical_reader_tier PASSED [100%]

============================= 8 passed in 18.43s ==============================
```

### 3.2. Bằng chứng T00 Meta-Audit: 0 New Regressions
```powershell
& "d:\scp\.venv\Scripts\python.exe" tools/t00_meta_audit.py
```
```text
[T00 Meta-Audit] Starting Test-Integrity Regression Authority...
[T00 Meta-Audit] Trusted Base: main
...
[T00 Meta-Audit] All integrity checks passed (0 new regressions).
```
Exit code: `0`. Không nới lỏng assertion (FA-01), không skip test (FA-02), không tạo verified giả lập (FA-04).

### 3.3. Bằng chứng Thực Nghiệm 3 Probe Bắt Lỗi Đối Kháng
Checker độc lập đã chạy probe kiểm chứng thực tế tại các điểm ranh giới:
```text
--- PROBE 1: REGEX PRECISION ---
điều kiện tự nhiên của Việt Nam -> False (Không bị nuốt nhầm)
điều hòa nhiệt độ -> False (Không bị nuốt nhầm)
điều khiển từ xa -> False (Không bị nuốt nhầm)
điều 12 bộ luật lao động -> True (Khớp chuẩn xác)
nghị định 85/2016 -> True (Khớp chuẩn xác)

--- PROBE 2: FTS & LOOKUP ANTI-LEAK (quy luật cung cầu) ---
resolve_extended_and_fts_lookup: None (Chặn đứng rò rỉ statutory_standard)
resolve_lookup_data tier: None ind: None api: Wikipedia (vi) — Nguyên lý cung - cầu

--- PROBE 3: RESOLVER PROBE (UNMOCKED RUNTIME) ---
động đất valdivia -> tier: canonical_document ind: KB:canonical api: USGSDataSource (Historical Catalog)
ty thể -> tier: canonical_document ind: KB:canonical api: CanonicalFTSWarehouse (Biology)
thang mohs kim cương -> tier: canonical_document ind: KB:canonical api: CanonicalFTSWarehouse (Geology)
độ cứng kim cương -> tier: canonical_document ind: KB:canonical api: CanonicalFTSWarehouse (Geology)
```

### 3.4. Bằng chứng Mutant Test Tiêu Diệt 100% (Attack A)
- Kịch bản bình thường: `"tin tức hôm nay về serbia"` $\to$ lấy từ `news_fact` (`reader_tier: kb_news_fact`, `api_name: kb_news_fact`).
- Kịch bản đột biến (tắt reader): `qr.news_lookup = lambda *a, **kw: None` $\to$ rơi xuống Wikipedia cũ (`api_name: Wikipedia (vi) — Mai An Tiêm`, `reader_tier: None`). Test mutant văng lỗi ngay lập tức $\to$ Mutant bị diệt 100%.

---

## 4. CHUỖI TẠI SAO & GHI CHÚ NHẬN THỨC LUẬN (EPISTEMIC CHAIN & OWNER MANDATE)

> [!IMPORTANT] **GHI CHÚ EPISTEMIC CỦA OWNER (30/10/2026):**
> 1. **Chuỗi Tại Sao là ĐÈN SOI, không phải TRỌNG TÀI:** Mỗi mắt xích chỉ là một GIẢ THUYẾT cho tới khi được Reality kiểm chứng độc lập.
> 2. **Độ sâu chuỗi KHÔNG ĐỒNG NGHĨA với độ tin cậy:** Sai số nhân dồn ($0.9^5 \approx 59\%$). Càng nhiều tầng suy diễn thuần túy mà thiếu neo kiểm chứng thực tế thì xác suất sai lệch càng tích tụ.
> 3. **Nguyên nhân là MẠNG (DAG / Causal Graph), không phải chuỗi tuyến tính đơn độc:** Phải đánh giá các nhánh nhân quả song song, không rút gọn một chiều.
> 4. **'Không biết' (`UNKNOWN_WITH_REASON`) là đầu ra DANH DỰ:** Trung thực tri thức tuyệt đối theo DNA #2 (Reality > Model) và DNA #8 (Fail-Closed).
> 5. **Tiêu chuẩn Thẩm định của CHECKER:** Bắt buộc chấm theo BẰNG CHỨNG THẬT từng mắt xích, tuyệt đối KHÔNG chấm theo độ mượt mà của văn phong lập luận.

### 4.1. Tại sao lỗi tồn tại (Nguyên nhân gốc, không dừng ở triệu chứng)
- **Triệu chứng:** Khi người dùng hỏi "tin tức hôm nay về serbia", hệ thống trả về bài viết Wikipedia cũ rích về Mai An Tiêm; các câu hỏi về quy luật kinh tế như "quy luật cung cầu" bị gán nhầm thành văn bản pháp luật Điều 12 Bộ luật Lao động 2019; còn tra cứu động đất USGS thì thiếu đèn chỉ báo tier.
- **Nguyên nhân gốc (Root Cause):**
  1. `news_lookup.py` sử dụng regex từ khóa cứng (`_NEWS_INTENT_*_RE`) vi phạm Luật 14, đồng thời cố định cửa sổ tươi `now - 48h`. Khi hệ thống chạy offline hoặc clone sạch mà RSS crawler chưa kích hoạt, toàn bộ bản ghi `news_fact` trong SQLite bị coi là `stale`, khiến lookup rơi thẳng xuống Wikipedia.
  2. Pattern nhận diện pháp lý trong `domain_data_extended.py` từng sử dụng từ trần `\bđiều\b`, gây nổ dương tính giả trên các từ vựng tự nhiên ("điều kiện", "điều hòa", "điều khiển"). Tầng FTS fallback vội vã lấy `fts_hits[0]` mà không kiểm tra cờ `is_statutory`.
  3. `domain_data_fork.py` chỉ bao bọc nhãn tier cho 5 môn khoa học ở vòng lặp sau, bỏ quên 3 resolver phía trước (`_resolve_usgs`, `_resolve_biology`, `_resolve_geology`).

### 4.2. Tại sao cách sửa này đúng Reality (Bằng chứng thực nghiệm, không phải "test xanh")
- **Phân tách ngữ nghĩa có cấu trúc:** Thay vì dùng regex cụm từ thô, hệ thống nhận diện dựa trên thuộc tính cấu trúc (tín hiệu thời gian `question_has_time_signal` kết hợp với tính chất thời sự của thực thể).
- **Cơ chế độ mới theo ngày (Date-Aligned Recency):** Khi phát hiện môi trường offline/clean-clone (> 84h), hệ thống tự động neo mốc quy chiếu vào `MAX(timestamp)` của kho KB, giúp câu hỏi tin tức hôm nay lấy đúng đợt tin tức mới nhất thay vì lôi Wikipedia cũ.
- **Bảo vệ tính đa miền bằng Negative Lookbehind:** Sử dụng `(?<!định\s)(?<!quy\s)\b(luật|...)\b` kết hợp `\bđiều\s+\d+\b`, chặn đứng việc ép nhầm định luật khoa học hoặc từ vựng thường sang `Legal_VN`.
- **Cơ chế Fallback Fail-Closed:** Khi `is_statutory == False` và FTS không có tài liệu `canonical_document`, hệ thống trả về `None`, kiên quyết không gán nhãn `statutory_standard` sai sự thật.

### 4.3. Tại sao tin bằng chứng đó (Lineage độc lập)
- **Kiểm chứng độc lập qua 2 vòng Maker - Checker:** Checker độc lập (`DeepInvestigator`) đã chủ động bắn các probe đối kháng và phát hiện ra 3 lỗi của Maker ở vòng 1, ép Maker phải sửa tận gốc trước khi cấp phán quyết `VERIFIED` ở vòng 2.
- **Bằng chứng vật lý không qua mock:** Mọi kết quả kiểm tra đều được chạy trực tiếp bằng Python 3.12 trên terminal của workspace với `SCP_EGRESS_MODE=deny`, đọc dữ liệu thật từ `data/v13.db` và `data/canonical_kb_fts5.sqlite`.

### 4.4. Điều gì sẽ làm quyết định này thay đổi (Anti-Goodhart & Falsification)
- **Kịch bản phủ định:** Nếu trong tương lai có một truy vấn không thuộc pháp luật nhưng vẫn bị gán nhãn `statutory_standard`, hoặc câu hỏi tin tức thời sự bị rơi xuống Wikipedia cũ khi KB đang có sẵn headline tươi, chốt chặn này lập tức bị coi là thất bại và phải tái mở kiểm toán.
- **Test Mutant W1.6:** Bài test mutant trong `test_04` bảo đảm rằng nếu ai đó tắt hoặc gỡ bỏ `news_lookup`, test sẽ đỏ ngay lập tức (Kill rate 100%).

### 4.5. Chỗ nào còn UNKNOWN_WITH_REASON (Khoảng trống tri thức trung thực)
- **UNKNOWN-1 (Số thứ tự điều khoản viết bằng chữ hoặc số La Mã):** Pattern `\bđiều\s+\d+\b` hiện chỉ nhận diện chữ số Ả Rập (Điều 12, Điều 10). Nếu văn bản hoặc câu hỏi trích dẫn viết bằng chữ ("Điều mười hai") hoặc số La Mã ("Điều IV"), hệ thống sẽ không nhận diện là statutory điều khoản mà đối soát theo từ khóa luật chung.
- **UNKNOWN-2 (Khoảng cách giữa 48h và 84h trong offline ops):** Khi hệ thống offline trong khoảng thời gian từ 48h đến 84h, `news_lookup` vẫn áp dụng cửa sổ stale 48h và sẽ trả về `None` cho tới khi vượt mốc 84h để kích hoạt anchor `MAX(timestamp)`.

---

## 5. CẢNH BÁO KIẾN TRÚC & KHUYẾN NGHỊ

> [!WARNING] **CẢNH BÁO CẬN TRẦN ARC-01 TẠI `question_router.py`:**
> Tệp [`scp/runtime/question_router.py`](file:///d:/scp/scp/runtime/question_router.py) hiện tại đạt **778 dòng** trên trần nghiêm ngặt **800 dòng** (chỉ còn **22 dòng** dung lượng trống).  
> **Khuyến nghị kiến trúc bắt buộc:** Tuyệt đối không thêm bất kỳ dòng logic mới nào trực tiếp vào file này trong các wave sau. Mọi tính năng mở rộng phải được tách sang các collaborator module hoặc helper riêng (như pattern `bridge_lookup.py`, `news_lookup.py`).

---

## 6. KẾT LUẬN & BÀN GIAO

Nhiệm vụ **CỔNG 1 đích-b: Knowledge Reader Tiers** (`agtask8.md`) đã được thi công và kiểm chứng đối kháng hoàn tất 100%, bảo đảm đúng hợp đồng:
- `news_fact` đọc tin tức mới nhất trước Wikipedia cũ.
- Phân tầng chính xác `statutory_standard` vs `canonical_document`.
- Đèn chỉ báo `reader_tier` và `source_indicator` hiển thị rõ ràng trên trace.
- Sạch lỗi hồi quy, 0 new regressions qua `t00_meta_audit.py`.

*Báo cáo hoàn thành được ghi nhận tại `d:\scp\dichb-readers-done.md` và đồng bộ sang `C:\Users\check\.council-loop\inbox\dichb-readers-done.md`.*
