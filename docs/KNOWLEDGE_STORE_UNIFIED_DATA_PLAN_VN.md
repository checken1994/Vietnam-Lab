# KẾ HOẠCH HỢP NHẤT DỮ LIỆU TRI THỨC SCP THÀNH MỘT KHO CHUNG

**Mục đích:** Giao cho AI khác trên PC của chủ dự án thực hiện việc loại bỏ fact/dữ liệu trả lời bị nhúng rải rác trong Python, xây dựng một kho dữ liệu chuẩn duy nhất cho tri thức tĩnh, và cho mọi module production tra cứu qua một đầu mối chung.

**Ngày lập kế hoạch:** 2026-10-26  
**Trạng thái:** Kế hoạch thực thi chi tiết — chưa phải báo cáo đã hoàn thành  
**Nguyên tắc xuyên suốt:** Làm từng gói nhỏ, có kiểm chứng và điểm dừng; không xóa hàng loạt; không tự biến dữ liệu cũ thành dữ liệu đã xác thực chỉ bằng cách chuyển file.

---

## 0. Cách sử dụng tài liệu này

Tài liệu này là lệnh bàn giao cho AI thực thi. AI thực thi phải:

1. Đọc toàn bộ tài liệu trước khi sửa code.
2. Chỉ thực hiện **một gói công việc (WP)** trong mỗi phiên/PR, trừ khi chủ dự án cho phép gộp.
3. Trước mỗi WP, ghi lại commit đang làm việc, trạng thái git và các file dự kiến thay đổi.
4. Giữ nguyên các thay đổi không liên quan đang tồn tại trong working tree; không tự ý reset, checkout hoặc xóa chúng.
5. Sau mỗi WP, dừng lại và báo cáo theo mẫu ở Mục 18. Không tự tuyên bố toàn bộ dự án hoàn thành khi mới xong một phần.
6. Nếu phát hiện thực tế code khác với kế hoạch, phải cập nhật kế hoạch/inventory và nêu rõ điểm khác biệt; không tự bịa rằng file/module vẫn tồn tại hoặc đang được production sử dụng.
7. Không đọc, in, commit hoặc sao chép bí mật từ `.env`. Nếu cần chạy API, chỉ dùng biến môi trường giả đủ dài trong môi trường test và không ghi giá trị vào báo cáo.
8. GitHub Actions có thể đỏ vì hết credit/billing theo thông tin của chủ dự án. Không dùng màu đỏ đó làm bằng chứng code lỗi; phải dựa vào kiểm thử chạy thật tại máy và ghi riêng phần CI chưa kiểm chứng.

Danh sách file trong kế hoạch là danh sách ứng viên từ audit ngày 2026-10-26. Commit trên PC có thể đã thay đổi. WP0 bắt buộc kiểm chứng lại tồn tại, wiring và mức độ sử dụng thực tế của từng file.

---

## 1. Kết quả cuối cùng cần đạt

Khi toàn bộ kế hoạch hoàn thành, SCP phải đạt các tính chất sau:

1. **Một nguồn sự thật logic** cho fact tĩnh và dữ liệu tham chiếu dùng để trả lời/kiểm chứng: một catalog có schema, phiên bản, checksum và nguồn gốc.
2. **Một đầu mối tra cứu chung trong mỗi tiến trình**: một `KnowledgeStore` dạng snapshot chỉ đọc, được tạo tại composition root và truyền vào các consumer cần dùng.
3. Mọi expert/resolver/verifier production muốn dùng fact tĩnh đều lấy qua `KnowledgeStore`; không tự giữ bảng fact riêng trong `.py`.
4. Dữ liệu động như thời tiết hiện tại, tỷ giá và số liệu thay đổi nhanh đi qua adapter nguồn thật, có `fetched_at`/hạn dùng; không bị đóng băng giả vờ là dữ liệu hiện tại.
5. Fact chưa có nguồn đạt chuẩn thì không được dùng để trả lời như fact đã xác thực. Không đủ bằng chứng thì hệ thống phải **withhold** hoặc nêu rõ giới hạn theo hợp đồng phản hồi hiện hành.
6. Mỗi câu trả lời dựa trên catalog phải truy được về tối thiểu: `record_id`, nguồn, `dataset_version` và trạng thái kiểm chứng.
7. Lỗi F1 của USGS được sửa ở cấp cơ chế: người dùng hỏi theo tên/năm/thuộc tính là tìm được fact, không cần biết trước đáp án.
8. Benchmark/test/seed được tách khỏi kho tri thức production để tránh học thuộc đề và nhiễm dữ liệu đo lường.
9. Có kiểm tra tự động ngăn fact trả lời mới bị nhúng trở lại vào production Python ngoài danh sách ngoại lệ đã phê duyệt.

---

## 2. Những việc không thuộc phạm vi

Kế hoạch này **không** yêu cầu:

1. Tạo một biến toàn cục mutable duy nhất cho mọi dữ liệu của SCP.
2. Thay ledger, task database, dữ liệu người dùng, lịch sử chat hoặc các kho runtime mutable bằng catalog tĩnh.
3. Xóa toàn bộ file `.py` chỉ vì chúng từng chứa dữ liệu nhúng.
4. Chuyển mọi list/dict trong code ra file dữ liệu. Luật nội bộ, enum, state machine, security policy và cấu hình hành vi có thể thuộc về code.
5. Huấn luyện/fine-tune model mới hoặc biến LLM thành nguồn sự thật.
6. Thu thập toàn bộ tri thức thế giới. Phạm vi dữ liệu phải mở rộng theo domain đã kiểm chứng, không chạy theo số lượng.
7. Tự ý thay kiến trúc API, verdict contract hoặc cơ chế governance ngoài phần cần thiết để nối `KnowledgeStore` vào đường production hiện hữu.

---

## 3. Hiện trạng làm cơ sở cho kế hoạch

### 3.1. Lỗi F1: tra cứu USGS theo khóa chứa đáp án

Catalog USGS hiện được ghi trong:

```text
data/knowledge/usgs_historical_earthquakes.jsonl
```

Hai record đã audit dùng token kiểu:

- Valdivia: `match_tokens=["valdivia", "9.5"]`, `alt_match_tokens=["mạnh nhất"]`
- Sumatra: `match_tokens=["sumatra", "9.1"]`, `alt_match_tokens=["2004"]`

Hàm `_resolve_usgs` trong `scp/runtime/domain_data_fork.py` yêu cầu **mọi token trong một nhóm đều xuất hiện trong câu hỏi**. Hệ quả:

- Hỏi “động đất mạnh nhất lịch sử” có thể trúng Valdivia.
- Hỏi tự nhiên “Trận động đất Valdivia năm 1960 có độ lớn bao nhiêu?” lại trượt vì người hỏi chưa biết đáp án `9.5`.
- Sumatra có thể trúng chỉ nhờ “2004”, tạo rủi ro dương tính giả cho câu hỏi khác về năm 2004.
- `scripts/seed_knowledge_from_internet.py` có thể tái tạo lại cấu trúc token lỗi nếu chỉ sửa file JSONL bằng tay.

Kết luận nền: dữ liệu Valdivia không thiếu; cơ chế nhận dạng/tra cứu quá hẹp và sử dụng đáp án làm khóa bắt buộc.

### 3.2. Dữ liệu production đang phân mảnh

SCP đồng thời có:

- File JSONL/JSON/CSV.
- Fact/câu trả lời đóng hộp trong một số expert.
- Bảng tham chiếu trong data source.
- API ngoài và cache runtime.
- Seed/training/benchmark corpus.
- Một số bảng/bộ máy cũ đã rỗng nhưng vẫn còn khung code, dễ gây hiểu lầm rằng đang có dữ liệu thật.

Bởi vậy mục tiêu không phải “gom tất cả vào một biến”, mà là phân loại dữ liệu, chỉ định nguồn sự thật cho từng loại, và ép production đi qua các đầu mối đã kiểm soát.

---

## 4. Nguyên tắc bắt buộc khi thực hiện

1. **Một nguồn sự thật cho mỗi loại dữ liệu.** Index/cache chỉ là bản phái sinh và phải tái tạo được từ nguồn chuẩn.
2. **Chuyển vị trí không đồng nghĩa kiểm chứng.** Fact cũ chép từ `.py` sang JSONL vẫn là `LEGACY_UNVERIFIED` cho đến khi có nguồn đạt chuẩn.
3. **Không dùng đáp án làm khóa bắt buộc.** Giá trị đáp án có thể là tín hiệu phụ để kiểm chứng khẳng định, nhưng tra cứu theo thực thể + thuộc tính phải hoạt động khi đáp án chưa xuất hiện.
4. **Chỉ trả lời khi có một record thắng rõ ràng và duy nhất.** Mơ hồ, hòa điểm hoặc thiếu thực thể thì withhold/yêu cầu làm rõ theo contract hiện hành.
5. **Không suy diễn “nhất toàn cầu” từ catalog không đầy đủ.** Chỉ tính superlative khi dataset khai báo phạm vi đầy đủ có bằng chứng; nếu không, phải nói rõ “trong catalog hiện có”.
6. **Dữ liệu có hạn dùng.** Record phải có chính sách freshness theo domain. Dữ liệu quá hạn không được trình bày như dữ liệu hiện tại.
7. **LLM không phải nguồn.** LLM có thể hỗ trợ phân loại/diễn đạt theo kiến trúc hiện hữu, nhưng không tạo fact đưa vào catalog và không thay thế nguồn.
8. **Benchmark không được nạp vào production knowledge.** Bộ đo phải độc lập tối đa với dữ liệu mà hệ thống dùng để trả lời.
9. **Không xóa trước khi thay thế và kiểm chứng.** Xóa literal chỉ sau khi consumer đã dùng đường mới và test liên quan đạt.
10. **Fail closed đối với dữ liệu hỏng.** Catalog thiếu, sai schema, trùng ID hoặc sai checksum phải bị phát hiện; không âm thầm dùng fact lỗi.
11. **Mọi thay đổi dữ liệu phải có phiên bản và khả năng rollback.**
12. **Không tạo thêm hệ song song nếu chưa kiểm tra wiring.** Trước khi tạo store mới phải khảo sát các thành phần knowledge/runtime hiện hữu và ghi quyết định tái sử dụng hay thay thế.

---

## 5. Phân loại dữ liệu và nơi đích

| Loại | Ví dụ | Nguồn sự thật đích | Production được dùng thế nào |
|---|---|---|---|
| `FACT_STATIC` | Động đất lịch sử, sự kiện lịch sử, tác giả/tác phẩm | Catalog fact có nguồn, trạng thái kiểm chứng và phiên bản | Qua `KnowledgeStore` |
| `REFERENCE_STATIC` | Đổi đơn vị, mã quốc gia, thang Beaufort, mã WMO | Reference catalog có nguồn/phiên bản; hằng số định nghĩa có thể có ngoại lệ trong code | Qua `KnowledgeStore` hoặc module hằng số đã phê duyệt |
| `DYNAMIC` | Thời tiết hiện tại, tỷ giá, chỉ số mới nhất | API/dữ liệu nguồn tại thời điểm lấy | Adapter riêng, có `fetched_at`, TTL và xử lý stale |
| `RULE_CONFIG` | State transition, security policy, enum, routing rule nội bộ | Code/config được review như hành vi hệ thống | Giữ trong code/config; không biến thành fact catalog |
| `RUNTIME_STATE` | Ledger, task, chat, cache người dùng | DB/ledger runtime hiện hữu | Không trộn vào catalog tĩnh |
| `SEED_RECIPE` | Script fetch và tạo corpus | Source manifest + ingestion code | Chỉ chạy offline/có kiểm soát; không import vào đường trả lời |
| `DATASET_EVAL` | Benchmark, probe, fixture test, training examples | Dataset có manifest/hash, tách production | Chỉ dùng để đo/huấn luyện theo mục đích đã khai báo |
| `LEGACY_UNKNOWN` | Fact cũ không rõ nguồn | Khu vực staging/quarantine | Không dùng trả lời như verified |

---

## 6. Kiến trúc đích

### 6.1. Luồng dữ liệu chuẩn

```text
Nguồn được phê duyệt
(USGS, cơ quan/tổ chức chủ quản, tài liệu chính thức, API nguồn)
        |
        v
Ingestion offline
(fetch -> chuẩn hóa -> kiểm tra schema/phạm vi -> deduplicate)
        |
        v
Staging / quarantine
(fact chưa đủ nguồn hoặc chưa review)
        |
        v
Canonical Catalog đã phát hành
(JSONL theo shard + manifest + checksum + dataset_version)
        |
        v
KnowledgeStore
(nạp một lần, lập index, snapshot chỉ đọc)
        |
        +--> Domain resolver / experts
        +--> Verifier dùng fact tham chiếu
        +--> /ask evidence + audit metadata
```

Dữ liệu động đi đường riêng:

```text
API nguồn thật -> adapter -> kiểm tra fetched_at/TTL -> evidence -> trả lời hoặc withhold
```

### 6.2. Định dạng lưu trữ mặc định

Mặc định đề xuất:

- **JSONL theo shard domain** là canonical source cho catalog tĩnh nhỏ/vừa vì dễ review diff và truy vết từng record.
- Một `manifest.json` khai báo phiên bản, danh sách shard, số record, checksum, ngày tạo và chính sách completeness/freshness.
- SQLite/FTS chỉ là **index phái sinh khi quy mô hoặc hiệu năng chứng minh cần**; không biến SQLite thành nguồn khó review nếu JSONL vẫn đủ.
- AI thực thi có thể đề xuất khác đi sau WP0, nhưng phải ghi quyết định kiến trúc và lý do dựa trên quy mô thật của repo.

Cấu trúc thư mục đề xuất, cần đối chiếu convention hiện hữu trước khi chốt:

```text
data/knowledge/catalog/
  manifest.json
  facts/
    geology.earthquakes.jsonl
    history.events.jsonl
    ...
  reference/
    units.jsonl
    weather_codes.jsonl
    ...

data/knowledge/staging/
  legacy_unverified/
    ...

config/knowledge/
  sources.json
```

Không đặt staging vào đường load production.

### 6.3. `KnowledgeStore` — “biến chung” an toàn

`KnowledgeStore` là một đối tượng dùng chung trong từng tiến trình, không phải dict toàn cục tự do sửa.

Yêu cầu tối thiểu:

- Được tạo một lần tại composition root của API/CLI/service.
- Nhận đường dẫn catalog và/hoặc `dataset_version` từ cấu hình đã kiểm soát.
- Nạp, validate checksum/schema và lập index một lần.
- Trả về object/record bất biến hoặc bản sao an toàn; consumer không sửa được dữ liệu gốc.
- Cung cấp API rõ ràng, ví dụ:

```python
store.get_by_id(record_id)
store.find_candidates(domain, entity_terms, year=None)
store.find_unique(domain, entity_terms, attribute=None, year=None)
store.get_reference(table_name, key)
store.dataset_version
store.health()
```

- Hỗ trợ thay snapshot nguyên tử khi reload: snapshot mới validate xong mới thay snapshot cũ.
- Nếu nhiều worker/process chạy song song, mỗi process có snapshot RAM riêng nhưng cùng đọc một `dataset_version` từ nguồn chung.
- Không đọc lại file JSONL cho từng câu hỏi trong đường production sau khi migration hoàn tất.
- Không dùng global mutable làm nơi consumer ghi dữ liệu học được. Dữ liệu runtime/học phải đi qua pipeline và kho riêng theo kiến trúc hiện hữu.

### 6.4. Evidence và audit

Một kết quả tra cứu thành công phải cho phép lớp trên ghi lại:

- `record_id`
- `dataset_version`
- `source_id` hoặc nguồn hiển thị
- trạng thái kiểm chứng
- thuộc tính đã trả lời
- lý do match ở mức an toàn để audit, ví dụ entity/year/attribute đã khớp

Không ghi bí mật, không ghi toàn bộ dữ liệu nhạy cảm của người dùng vào miss ledger nếu không có chính sách lưu trữ phù hợp.

---

## 7. Schema dữ liệu chuẩn phiên bản 1

### 7.1. Fact record

Mẫu tối thiểu:

```json
{
  "schema_version": 1,
  "record_id": "geology.earthquake:valdivia-1960",
  "domain": "geology.earthquake",
  "entity": {
    "entity_id": "valdivia-1960",
    "canonical_name": "Valdivia earthquake",
    "names": ["Valdivia", "Great Chilean earthquake"],
    "aliases": ["động đất Valdivia", "động đất Chile 1960"],
    "place": "Chile",
    "event_date": "1960-05-22",
    "year": 1960
  },
  "facts": {
    "magnitude": {
      "value": 9.5,
      "unit": "Mw"
    }
  },
  "evidence": {
    "text": "Đoạn bằng chứng ngắn đủ chứng minh fact",
    "source_id": "usgs",
    "source_record_id": "mã record tại nguồn nếu có",
    "source_url": "URL nguồn cụ thể",
    "retrieved_at": "2026-10-26T00:00:00Z"
  },
  "review": {
    "status": "verified",
    "method": "source_fetch_and_field_check"
  },
  "freshness": {
    "class": "historical_stable",
    "valid_from": null,
    "valid_until": null,
    "review_after": null
  }
}
```

AI thực thi phải kiểm tra field thật tại nguồn, không sao chép máy móc ví dụ trên nếu nguồn cung cấp dữ liệu khác.

Trạng thái review tối thiểu:

- `legacy_unverified`: dữ liệu cũ chưa có nguồn đạt chuẩn.
- `source_fetched`: đã lấy từ nguồn nhưng chưa qua kiểm tra field/review cần thiết.
- `verified`: field đã đối chiếu nguồn được phê duyệt.
- `rejected`: sai hoặc không đủ điều kiện; giữ ở staging/audit nếu cần truy vết.
- `stale`: đã vượt chính sách tươi mới của domain.

Chỉ record `verified` và còn hiệu lực mới được production dùng làm fact xác thực, trừ ngoại lệ được ghi thành quyết định riêng.

### 7.2. Dataset manifest

Manifest tối thiểu gồm:

- `dataset_version`
- `schema_version`
- thời gian tạo
- danh sách shard và checksum
- tổng số record theo trạng thái
- nguồn dữ liệu đã dùng
- khai báo completeness theo domain, ví dụ `not_complete`, `complete_for_declared_scope`
- chính sách freshness theo domain
- công cụ/phiên bản tạo catalog nếu phù hợp

### 7.3. Source registry

Mỗi nguồn dùng cho ingestion phải khai báo:

- `source_id`
- tên/tổ chức chủ quản
- domain được phép dùng
- endpoint hoặc cách truy xuất chính thức
- loại bằng chứng có thể cung cấp
- chính sách cập nhật/TTL
- ghi chú giấy phép/giới hạn sử dụng nếu có
- field mapping kỳ vọng

Không fetch URL tùy ý từ record tại thời điểm trả lời. Ingestion phải bị giới hạn theo source registry để giảm rủi ro SSRF và nguồn không kiểm soát.

### 7.4. Kiểm tra dữ liệu bắt buộc

Công cụ validate catalog phải kiểm tra tối thiểu:

1. JSONL parse được, mỗi dòng là một record.
2. `record_id` duy nhất và đúng quy ước.
3. Schema/field bắt buộc theo domain.
4. Kiểu dữ liệu và phạm vi hợp lý, ví dụ magnitude không nằm ngoài khoảng vật lý đã khai báo cho domain.
5. Nguồn thuộc registry và phù hợp domain.
6. Record `verified` phải có bằng chứng/nguồn/ngày lấy.
7. Alias không gây va chạm mơ hồ ngoài ý muốn trong cùng domain; va chạm phải được báo cáo.
8. Không có answer-token bắt buộc kiểu cũ trong cơ chế mới.
9. Checksum shard khớp manifest.
10. Không có bí mật hoặc dữ liệu cá nhân không cần thiết trong catalog.

---

## 8. Thuật toán tra cứu đích

### 8.1. Nguyên tắc

Bộ tra cứu phải tách:

- **Thực thể**: Valdivia, Sumatra, một quốc gia, một tác giả...
- **Thuộc tính cần hỏi**: magnitude, capital, year, population...
- **Ý định đặc biệt**: so sánh, cao nhất/thấp nhất, đúng/sai, liệt kê...

Không dùng một danh sách token “tất cả phải xuất hiện” trong đó có đáp án.

### 8.2. Các bước xử lý

1. Chuẩn hóa Unicode, chữ hoa/thường và khoảng trắng. Việc bỏ dấu tiếng Việt chỉ dùng như một dạng index phụ; không làm mất khả năng phân biệt cần thiết.
2. Nhận diện thực thể bằng canonical name/alias theo ranh giới từ/cụm từ, không khớp chuỗi con nguy hiểm.
3. Trích xuất năm/ngày và các định danh phụ khi domain hỗ trợ.
4. Nhận diện thuộc tính bằng bảng phrase có kiểm soát theo domain, hỗ trợ tiếng Việt và tiếng Anh trong phạm vi đã test.
5. Chấm điểm mọi record ứng viên theo bằng chứng độc lập:
   - khớp canonical name/alias;
   - khớp năm/địa điểm/định danh;
   - khớp thuộc tính được hỏi;
   - đáp án xuất hiện trong câu chỉ là tín hiệu corroboration cho câu khẳng định, không phải điều kiện bắt buộc để tìm fact.
6. Chỉ chọn record nếu thắng duy nhất và cách biệt đạt ngưỡng đã định trong test. Nếu hòa hoặc thiếu thực thể, trả trạng thái không đủ chắc chắn để lớp trên withhold.
7. Superlative như “mạnh nhất” phải được tính từ field số học trên tập record thuộc phạm vi đã khai báo. Không hard-code tên record thắng cuộc.
8. Ghi nhận miss/ambiguous theo domain và dạng lỗi đã ẩn danh để tạo backlog phủ câu hỏi, nhưng không tự học fact từ câu hỏi người dùng.

Fuzzy matching/edit distance không được bật tùy tiện trong pilot. Nếu đề xuất dùng, phải có test dương tính giả riêng và ngưỡng bảo thủ.

---

## 9. Inventory ứng viên và quyết định xử lý

WP0 phải kiểm chứng lại danh sách sau tại commit thực tế.

### 9.1. Nhóm F1 và đường tra cứu USGS

| File | Xử lý đích | Ưu tiên |
|---|---|---|
| `data/knowledge/usgs_historical_earthquakes.jsonl` | Chuyển sang schema mới hoặc thành shard canonical tương đương; giữ tương thích tạm thời nếu consumer khác còn dùng | Cao nhất |
| `scp/runtime/domain_data_fork.py` | Giữ logic cần thiết; thay `_resolve_usgs` bằng tra cứu qua store/cơ chế mới; không coi các bảng rỗng là dữ liệu thật | Cao nhất |
| `scripts/seed_knowledge_from_internet.py` | Sửa phần sinh USGS và đầu ra liên quan để không tái tạo token lỗi; về dài hạn chuyển thành ingestion theo source registry | Cao nhất |

### 9.2. Fact/câu trả lời đóng hộp trong expert

| File | Xử lý đích |
|---|---|
| `scp/runtime/experts/humanities.py` | Trích xuất bảng lịch sử sang staging; kiểm chứng từng fact trước khi đưa vào catalog; sau đó xóa dict fact |
| `scp/runtime/experts/agriculture.py` | Xử lý `_LOCAL_KNOWLEDGE` như legacy unverified; kiểm chứng hoặc loại khỏi đường trả lời verified |
| `scp/runtime/experts/art.py` | Như trên |
| `scp/runtime/experts/economics.py` | Như trên; đặc biệt kiểm tra số liệu có thời điểm |
| `scp/runtime/experts/education.py` | Như trên |
| `scp/runtime/experts/engineering.py` | Tách định nghĩa ổn định khỏi fact/số liệu cần nguồn |
| `scp/runtime/experts/law.py` | Thận trọng cao: fact pháp lý phải có nguồn và hiệu lực; không dùng canned text như tư vấn/fact đã xác thực |
| `scp/runtime/experts/military.py` | Như trên; kiểm chứng nguồn chủ quản |
| `scp/runtime/experts/literature.py` | Chuyển hồ sơ tác giả/tác phẩm đã kiểm chứng vào catalog |
| `scp/runtime/experts/philosophy.py` | Chuyển fact về triết gia/khái niệm đã kiểm chứng vào catalog; diễn giải học thuật phải phân biệt với fact |
| `scp/runtime/experts/management.py` | Framework/khái niệm có thể là reference/định nghĩa; ghi nguồn hoặc hạ trạng thái nếu chỉ là nội dung tóm tắt nội bộ |

Không tự động chuyển status của các fact trên thành `verified`.

### 9.3. Reference và verifier

| File | Xử lý đích |
|---|---|
| `scp/data_sources/weather.py` | Tách tọa độ/city và climate normals thành reference có nguồn; dữ liệu hiện tại vẫn lấy live; Beaufort/WMO là reference có phiên bản |
| `scp/data_sources/conversion.py` | Đánh giá giữ hằng số định nghĩa trong code hay chuyển reference; dù chọn cách nào cũng phải có nguồn/phiên bản và test |
| `scp/knowledge/antibody_parts/mixins.py` | Fact dùng kiểm chứng phải vào verified reference; luật kiểm tra vẫn ở code |
| `scp/knowledge/antibody_system.py` | Chủ yếu là rule catalog; không tự động chuyển thành fact. Rà soát field nào là fact thật |
| `scp/data_sources/domain_registry.py` | Metadata/routing; quyết định giữ code hay config sau WP0, không thuộc fact catalog |
| `scp/core/question_fetchers/data_fetchers.py` | Danh sách sinh/lấy câu hỏi chuyển thành dataset/config ngoài đường trả lời nếu còn cần |
| `scp/runtime/experts/lifestyle.py` | Mapping tham chiếu phải phân loại; không để mapping mơ hồ thành fact đã xác thực |

### 9.4. Seed, training và ví dụ học

| File | Xử lý đích |
|---|---|
| `tools/create_verified_seed_corpus_v1.py` | Không dùng làm production knowledge chỉ vì tên gọi; chuyển nội dung thành source manifest/dataset và giữ status đúng thực tế |
| `scp/core/fast_learning_engine.py` | `SEED_QUESTIONS` là mẫu sinh câu hỏi, không phải fact; chuyển dataset/config có manifest nếu còn dùng |
| `scp/core/reality_engine.py` | `TRAINING_DATA` phải thành dataset huấn luyện/eval có phiên bản, không nằm như dữ liệu vận hành mơ hồ |
| `scp/core/fast_learning_engine_parts/auto_web_ingest_step.py` | Seed ingest phải qua source registry; không tự nạp vào verified catalog |
| `scp/security/intel_sources.py` | Attack/counter seeds là dữ liệu kiểm thử/điều tra; tách khỏi fact catalog và kiểm soát mục đích |
| `scp/brain_layer/adapters/fake.py` | Fake/test double; đảm bảo không bị chọn nhầm trong production |
| `scripts/ops/autofix_e2e_fixture_test.py` | Fixture kiểm thử; giữ ngoài production import graph |

### 9.5. Benchmark và probe

Các file như:

- `scp/benchmark/world_standard_verifiability_benchmark.py`
- `scp/benchmark/question_generator.py`
- `scp/benchmark/benchmark_suite.py`
- `tools/showcase_benchmark/dataset_tracks_1_2.py`
- `tools/showcase_benchmark/dataset_tracks_3_4_5.py`
- `tools/ask_probe_battery.py`
- `benchmark/witness_benchmark.py`
- `benchmark/extract_gold_anchor_v2.py`

Quyết định đích:

- Không xóa chỉ vì chứa dữ liệu mẫu.
- Chuyển corpus lớn sang file dataset có manifest/hash khi thực tế giúp quản trị tốt hơn.
- Cấm production import các corpus này làm knowledge.
- Ghi rõ tập nào là development, regression hay held-out để tránh nhiễm benchmark.

### 9.6. Khung dữ liệu rỗng/legacy

Các ứng viên gồm các bảng rỗng trong `domain_data_fork.py`, một số data source khoa học đã bị làm rỗng, `scp/runtime/experts/humanities_data.py` và `BOOTSTRAP_FACTS` trong `scp/core/autonomous_cycle.py`.

Quyết định đích:

- WP0 kiểm tra import/wiring và test phụ thuộc.
- Nếu là dead code, xóa trong WP cleanup riêng có test chứng minh.
- Nếu còn là điểm mở rộng có chủ đích, ghi rõ trạng thái rỗng/deprecated và không để health/docstring quảng bá như đang có dữ liệu.

---

## 10. Các gói công việc chi tiết

### WP0 — Baseline, inventory và bản đồ wiring

**Loại:** Chỉ khảo sát/chứng cứ; không thay đổi hành vi production.  
**Phụ thuộc:** Không.

#### Việc phải làm

1. Ghi commit, branch, trạng thái git và phiên bản Python/môi trường.
2. Tái hiện F1 bằng đường production phù hợp nhất đang tồn tại; ghi câu hỏi, kết quả và evidence/metadata nhìn thấy.
3. Dùng AST/grep có kiểm soát để liệt kê literal dữ liệu đáng kể ngoài `tests/`; không kết luận mọi list/dict đều là dữ liệu mẫu.
4. Với từng ứng viên, ghi:
   - file, symbol/biến, số mục gần đúng;
   - loại dữ liệu theo Mục 5;
   - module nào import/gọi;
   - có nằm trên đường `/ask` hay đường production khác không;
   - có nguồn/ngày cập nhật không;
   - test nào đang bao phủ;
   - quyết định đề xuất và rủi ro.
5. Vẽ bản đồ consumer hiện tại của USGS, các expert có `_LOCAL_KNOWLEDGE`, weather/conversion và verifier facts.
6. Rà soát các knowledge store/runtime/warehouse hiện hữu để tránh tạo hệ song song; ghi rõ thành phần nào thực sự được production import.
7. Chạy targeted tests liên quan tối thiểu để có baseline; không cần chạy toàn bộ suite nếu chưa sửa code, nhưng phải ghi rõ chưa chạy gì.
8. Đề xuất vị trí module/store, cấu trúc catalog và feature flag dựa trên convention thật của repo.

#### Sản phẩm

- Báo cáo inventory cập nhật, đề xuất nằm trong `docs/` hoặc thư mục báo cáo theo convention repo.
- Danh sách quyết định cần chủ dự án/phiên sau xác nhận.
- Baseline test và bằng chứng tái hiện F1.

#### Cổng nghiệm thu

- Không có file ứng viên nào bị gán “production” chỉ dựa vào tên gọi.
- Mọi kết luận wiring đều có đường import/call cụ thể hoặc được ghi “chưa chứng minh”.
- Chưa xóa hoặc chuyển dữ liệu nào.

---

### WP1 — Schema, validator và `KnowledgeStore` nền tảng

**Loại:** Thêm năng lực mới, mặc định chưa đổi hành vi production.  
**Phụ thuộc:** WP0.

#### Việc phải làm

1. Chốt schema v1 theo Mục 7 và ghi quyết định nếu điều chỉnh.
2. Tạo hoặc tái sử dụng module store theo kết quả WP0; tránh duplicate với knowledge runtime chưa được wiring.
3. Implement:
   - load manifest và shards;
   - validate schema/checksum/trùng ID;
   - immutable snapshot;
   - index theo `record_id`, domain, canonical name/alias đã chuẩn hóa và field định danh cần thiết;
   - API tra cứu candidates/unique/reference;
   - health/version;
   - reload nguyên tử ở mức phù hợp phạm vi pilot.
4. Tạo validator CLI hoặc hàm kiểm tra dùng được trong test/CI local.
5. Tạo catalog rỗng hoặc fixture nhỏ chỉ cho test; không đưa fact chưa kiểm chứng vào production catalog.
6. Thêm feature flag/mode cho giai đoạn sau, mặc định an toàn (`off` hoặc tương đương theo convention).
7. Đảm bảo lỗi catalog được báo rõ và không fallback âm thầm sang fact nhúng.

#### Test bắt buộc

- Load catalog hợp lệ.
- Thiếu file/manifest.
- JSONL hỏng.
- Trùng `record_id`.
- Sai checksum.
- Record verified thiếu nguồn.
- Consumer không sửa được snapshot.
- Reload thất bại vẫn giữ snapshot cũ hợp lệ hoặc fail theo contract đã chốt.

#### Cổng nghiệm thu

- Store hoạt động độc lập bằng test thật.
- Chưa thay đổi đáp án production.
- Không tạo thêm đường tắt global mutable.

---

### WP2 — Pilot USGS và sửa F1 ở cấp cơ chế

**Loại:** Thay đổi hành vi trong phạm vi hẹp, có feature flag/shadow nếu khả thi.  
**Phụ thuộc:** WP1.

#### Việc phải làm

1. Lấy và đối chiếu dữ liệu Valdivia/Sumatra từ USGS hoặc nguồn chủ quản được phê duyệt tại thời điểm thực hiện. Không dùng LLM hoặc trang tóm tắt không rõ nguồn làm bằng chứng chính.
2. Tạo record theo schema mới với names/aliases, place, date/year, magnitude kèm unit, evidence và source metadata.
3. Khai báo completeness trung thực. Hai record không đủ để khẳng định mọi superlative toàn cầu nếu nguồn/record không chứng minh điều đó.
4. Viết matcher mới theo Mục 8.
5. Nối `_resolve_usgs` hoặc consumer tương đương vào store/matcher sau feature flag.
6. Sửa seeder/ingestion liên quan để đầu ra là schema mới; cấm tái tạo `match_tokens` chứa đáp án bắt buộc.
7. Giữ khả năng rollback về hành vi cũ bằng flag trong giai đoạn pilot, nhưng phải ghi rõ hành vi cũ có lỗi F1.
8. Nếu có shadow mode, ghi khác biệt old/new cho bộ câu hỏi kiểm soát mà không thay đáp án người dùng cho đến khi bật pilot.

#### Bộ test tối thiểu

Câu phải trả lời đúng nếu record verified tồn tại:

- “Trận động đất Valdivia năm 1960 có độ lớn bao nhiêu?”
- “Động đất Valdivia mạnh bao nhiêu?”
- “Magnitude of the 1960 Valdivia earthquake?”
- “Trận động đất Sumatra năm 2004 có độ lớn bao nhiêu?”
- “Động đất Sumatra 2004 mạnh cỡ nào?”

Câu phải withhold, hỏi rõ hoặc xử lý theo contract an toàn:

- “Động đất năm 2004 mạnh bao nhiêu?” khi chỉ có năm mà không có thực thể đủ rõ.
- Câu về một trận động đất không có trong catalog.
- Câu có tên thực thể mơ hồ hoặc hai ứng viên hòa điểm.
- Câu hỏi năm 2004 nhưng không phải Sumatra, để chống dương tính giả theo năm.

Trường hợp đặc biệt phải có quyết định/test riêng:

- “Trận động đất 9.5 xảy ra ở đâu?” — giá trị có thể là thuộc tính tìm ngược nếu kết quả duy nhất.
- “Valdivia có phải mạnh 8.0 không?” — hệ thống phải kiểm chứng khẳng định, không chỉ lặp đáp án mà bỏ qua ý đúng/sai.
- “Động đất mạnh nhất là gì?” — chỉ trả lời trong phạm vi completeness đã khai báo và có bằng chứng.

#### Cổng nghiệm thu

- Câu Valdivia + 1960 không chứa `9.5` vẫn trúng đúng record.
- Không có test pass nhờ việc đưa đáp án vào câu hỏi.
- Test âm tính theo năm không trả nhầm Sumatra.
- Evidence/audit truy được `record_id` và `dataset_version`.
- Seeder chạy lại không khôi phục schema/token lỗi.

---

### WP3 — Pipeline ingestion và phát hành catalog

**Loại:** Công cụ dữ liệu offline.  
**Phụ thuộc:** WP1; có thể hoàn thiện song song sau khi WP2 chứng minh schema.

#### Việc phải làm

1. Xây source registry và field mapping theo domain.
2. Tách các bước: fetch, normalize, validate, deduplicate, review/promote, build manifest.
3. Lưu đủ metadata để tái lập: nguồn, thời gian lấy, phiên bản công cụ/schema, checksum đầu ra.
4. Không lưu bí mật/API key trong catalog hoặc log.
5. Giới hạn fetch theo source registry; có timeout, giới hạn kích thước và xử lý lỗi. Không fetch URL tùy ý do dữ liệu đầu vào cung cấp.
6. Tạo staging cho record chưa verified; promoter chỉ chuyển record đạt điều kiện vào canonical catalog.
7. Tạo lệnh build/validate có thể chạy local và trong test.
8. Ghi quy trình rollback dataset: quay lại `dataset_version` trước bằng cấu hình/manifest đã phát hành.

#### Cổng nghiệm thu

- Chạy ingestion hai lần trên cùng nguồn/fixture cho kết quả ổn định hoặc diff được giải thích.
- Record thiếu nguồn không thể được promote thành `verified`.
- Catalog build sai checksum/schema sẽ bị validator chặn.

---

### WP4 — Di trú fact đóng hộp trong experts

**Loại:** Nhiều gói con theo domain; không làm một lượt toàn bộ.  
**Phụ thuộc:** WP1, WP3 và bằng chứng pilot WP2.

#### Thứ tự đề xuất

- **WP4A:** Bảng lịch sử trong `humanities.py`.
- **WP4B:** Bảy expert dùng `_LOCAL_KNOWLEDGE` (`agriculture`, `art`, `economics`, `education`, `engineering`, `law`, `military`).
- **WP4C:** `literature`, `philosophy`, `management`.

#### Quy trình cho từng gói con

1. Trích xuất literal sang staging bằng script có thể tái chạy, giữ nguyên nội dung và vị trí nguồn gốc code để truy vết.
2. Gán `legacy_unverified` cho mọi record chưa có nguồn.
3. Phân loại từng mục:
   - fact kiểm chứng được;
   - định nghĩa/reference;
   - nhận định/diễn giải;
   - số liệu có thời điểm;
   - nội dung không đủ chuẩn để production dùng.
4. Tìm nguồn chủ quản/authoritative phù hợp domain và kiểm chứng field. Không dùng chính benchmark/test làm nguồn fact.
5. Chuyển consumer sang `KnowledgeStore` hoặc loại bỏ đường trả lời trực tiếp đối với mục chưa verified.
6. Thêm test paraphrase và negative cho domain đó.
7. Chỉ xóa literal sau khi consumer mới đạt test và không còn consumer cũ.
8. Báo cáo số mục: tổng, verified, rejected, còn staging, đã loại khỏi production.

#### Cổng nghiệm thu cho mỗi domain

- Không còn đáp án production ghi nguồn mơ hồ kiểu `local_knowledge` mà không có record/nguồn.
- Không vì di trú mà biến fact chưa kiểm chứng thành verified.
- Phạm vi trả lời có thể giảm tạm thời để đổi lấy độ tin cậy; phải báo cáo rõ thay vì lấp bằng đoán.

---

### WP5 — Reference data và fact dùng cho verifier

**Phụ thuộc:** WP1, WP3.

#### Việc phải làm

1. Rà soát `weather.py`, `conversion.py`, antibody mixins/system, domain registry, question fetchers và lifestyle mappings theo inventory WP0.
2. Với mỗi bảng, ghi quyết định:
   - chuyển reference catalog;
   - giữ code như hằng số/luật đã phê duyệt;
   - chuyển config;
   - loại bỏ vì dead/legacy.
3. Fact dùng để bác bỏ/chấp nhận câu trả lời phải có nguồn như fact trả lời trực tiếp.
4. Dữ liệu thời tiết hiện tại không được chuyển thành static fact. Chỉ reference ổn định như mã/thang đo và metadata địa điểm mới vào catalog.
5. Thêm test cho đơn vị, mã và trường hợp stale/live adapter không khả dụng.

#### Cổng nghiệm thu

- Không còn verifier fact quan trọng không rõ nguồn trong phạm vi đã migration.
- Ranh giới static/reference/dynamic được tài liệu hóa và test chứng minh.

---

### WP6 — Tách seed, training và benchmark khỏi production knowledge

**Phụ thuộc:** WP0; có thể làm sau WP2.

#### Việc phải làm

1. Kiểm tra production import graph để chứng minh benchmark/probe/fake không được nạp làm tri thức.
2. Với corpus còn dùng, tạo dataset manifest gồm mục đích, nguồn, phiên bản/hash và loại tập (`development`, `regression`, `held_out`, `training`).
3. Chuyển dữ liệu lớn từ `.py` sang file dataset nếu giúp review và quản trị; giữ loader/tooling tối thiểu.
4. Đổi tên/ghi chú các thành phần dễ gây hiểu lầm như seed “verified” nếu trạng thái thực tế chưa phải gold/verified.
5. Đảm bảo quá trình ingestion production không đọc benchmark làm nguồn fact.
6. Fake adapter chỉ được chọn trong test/dev theo cấu hình rõ ràng.

#### Cổng nghiệm thu

- Có kiểm tra import/config chứng minh dataset eval không đi vào catalog production.
- Mỗi dataset còn dùng có mục đích và trạng thái rõ ràng.

---

### WP7 — Dọn literal, dead shell và hàng rào chống tái phát

**Phụ thuộc:** Các domain tương ứng đã migration xong.

#### Việc phải làm

1. Xóa literal fact đã thay thế; không xóa logic vẫn còn dùng.
2. Xóa hoặc đánh dấu deprecated các bảng/builder rỗng sau khi chứng minh wiring.
3. Thêm kiểm tra tự động phù hợp repo, ví dụ AST/lint/grep có allowlist, để phát hiện:
   - `_LOCAL_KNOWLEDGE` mới trong production;
   - bảng fact lớn không khai báo loại;
   - production import benchmark corpus;
   - token matcher bắt buộc chứa đáp án trong catalog dạng cũ còn sót.
4. Allowlist phải có lý do, chủ sở hữu/loại dữ liệu và ngày rà soát; không allowlist toàn thư mục một cách vô hạn.
5. Cập nhật tài liệu vận hành: cách thêm fact, thêm nguồn, phát hành dataset, rollback và xử lý stale.

#### Cổng nghiệm thu

- Kiểm tra chống tái phát chạy thật và bắt được fixture vi phạm cố ý trong test.
- Không còn docstring/health message quảng bá kho dữ liệu rỗng như đang hoạt động.

---

### WP8 — Rollout, quan trắc và nghiệm thu cuối

**Phụ thuộc:** WP2 và các domain đã migration trong phạm vi được duyệt.

#### Việc phải làm

1. Bật theo mức `off -> shadow -> pilot theo domain -> on` hoặc cơ chế tương đương sẵn có.
2. So sánh old/new trên bộ câu hỏi kiểm soát; phân loại khác biệt: sửa đúng, mất coverage, dương tính giả, âm tính giả, mơ hồ.
3. Thêm metric/log an toàn cho:
   - hit duy nhất;
   - miss;
   - ambiguous;
   - stale;
   - catalog load failure;
   - dataset version đang dùng.
4. Kiểm tra hiệu năng khởi động và tra cứu để chứng minh store không đọc file lặp lại bất hợp lý.
5. Kiểm tra rollback bằng cách quay lại dataset version/flag trước trong môi trường test.
6. Chạy targeted tests, integration `/ask` liên quan và một vòng kiểm thử rộng phù hợp với thay đổi. Ghi chính xác số pass/fail/skip và phần chưa chạy.
7. Lập báo cáo nghiệm thu tách bạch:
   - đã kiểm chứng bằng cách chạy;
   - chỉ đọc code;
   - chưa kiểm chứng.

#### Cổng nghiệm thu

- F1 đạt toàn bộ test bắt buộc.
- Không có regression chưa giải thích trong phạm vi đã chạy.
- Rollback đã thử, không chỉ mô tả.
- Báo cáo không biến vùng chưa kiểm chứng thành PASS.

---

## 11. Kế hoạch kiểm thử tổng thể

### 11.1. Unit test

- Schema validator.
- Store load/index/immutability/reload.
- Chuẩn hóa text và alias matching.
- Scoring/unique winner/ambiguous.
- Superlative theo completeness.
- Freshness/stale.

### 11.2. Data test

- Mọi shard parse được.
- Mọi manifest checksum khớp.
- Không trùng ID.
- Mọi record verified có nguồn hợp lệ.
- Không còn schema token lỗi trong shard đã migration.
- Không có benchmark record bị dùng làm nguồn production.

### 11.3. Integration test

- `/ask` hoặc đường production tương đương dùng store và trả evidence truy vết được.
- Catalog lỗi dẫn tới hành vi fail-closed đã chốt.
- Feature flag tắt/bật đúng phạm vi.
- Reload/rollback không làm consumer giữ snapshot lỗi.

### 11.4. Regression và chống dương tính giả

Mỗi domain migration phải có:

- Ít nhất vài cách hỏi khác nhau cho cùng fact.
- Câu thiếu thực thể.
- Câu có thực thể gần giống.
- Câu có năm/số dễ gây nhầm.
- Câu hỏi ngoài catalog.
- Câu khẳng định sai để kiểm tra verifier không bị matcher đánh lừa.

### 11.5. Lệnh kiểm thử

AI thực thi phải tự phát hiện lệnh test đúng từ tài liệu/cấu hình repo tại commit thực tế. Không sao chép mù quáng lệnh từ bản kế hoạch nếu repo đã đổi. Báo cáo phải ghi lệnh đã chạy nguyên văn và kết quả tóm tắt.

---

## 12. Rollout và rollback

### 12.1. Rollout

1. `OFF`: code mới có mặt nhưng production chưa dùng.
2. `SHADOW`: chạy song song trên đầu vào kiểm soát, chỉ ghi khác biệt.
3. `PILOT`: bật cho USGS hoặc từng domain nhỏ.
4. `ON`: bật cho domain đã đạt cổng nghiệm thu.

Không bật toàn bộ domain cùng lúc chỉ vì pilot USGS thành công.

### 12.2. Rollback

Rollback phải độc lập ở hai lớp:

- **Code/mode:** tắt feature flag để quay lại consumer cũ nếu còn giữ trong giai đoạn chuyển đổi.
- **Data:** trỏ store về `dataset_version` đã phát hành trước đó.

Khi literal cũ đã bị xóa ở WP7, rollback code bằng flag có thể không còn khả thi. Vì vậy WP7 chỉ thực hiện sau thời gian pilot và cổng nghiệm thu phù hợp; rollback dữ liệu vẫn phải hoạt động.

---

## 13. An toàn và quản trị dữ liệu

1. Catalog là đầu vào không đáng tin cậy ở mức parser: validate kích thước, kiểu và schema trước khi dùng.
2. Đường dẫn catalog phải bị giới hạn trong thư mục dữ liệu được phê duyệt; không nhận path tùy ý từ request người dùng.
3. Ingestion chỉ fetch nguồn trong registry; có timeout và giới hạn phản hồi.
4. Không thực thi code từ dữ liệu, không dùng `eval`/pickle cho catalog.
5. Không lưu API key, token, dữ liệu cá nhân hoặc nội dung người dùng nhạy cảm trong catalog.
6. Evidence text chỉ giữ đoạn cần thiết và tuân thủ điều kiện nguồn/giấy phép.
7. Mọi thay đổi record verified phải qua diff/review như thay đổi code quan trọng.
8. Log miss phải tối thiểu hóa dữ liệu người dùng; ưu tiên domain, loại lỗi và dạng đã chuẩn hóa/ẩn danh theo chính sách dự án.

---

## 14. Rủi ro chính và cách giảm thiểu

| Rủi ro | Hậu quả | Giảm thiểu |
|---|---|---|
| Chép fact cũ sang JSONL rồi gọi là verified | Vẫn sai nhưng trông đáng tin hơn | Bắt buộc staging + source review |
| Xóa hàng loạt file/bảng | Gãy import, mất logic, regression diện rộng | Migration theo domain, xóa sau cùng |
| Tạo store mới nhưng không wiring production | Thêm một hệ song song, phân mảnh hơn | WP0 bản đồ wiring, WP2 pilot trên đường thật |
| Matcher mới quá rộng | Dương tính giả, trả sai tự tin | Unique-winner, negative tests, ngưỡng bảo thủ |
| Matcher vẫn quá hẹp | Coverage thấp dù dữ liệu đúng | Paraphrase tests, miss backlog theo domain |
| Catalog không đầy đủ nhưng trả lời “nhất toàn cầu” | Khẳng định vượt bằng chứng | Completeness theo domain/shard |
| Dữ liệu cũ theo thời gian | Fact stale | Freshness policy, `as_of`, live adapter |
| Benchmark nhiễm vào knowledge | Điểm đo giả | Import guard, dataset manifest, held-out |
| Một global mutable bị sửa | Toàn hệ thống dùng dữ liệu bẩn | Snapshot bất biến, DI, reload nguyên tử |
| Seeder ghi đè bản sửa tay | Lỗi quay lại | Sửa ingestion và test regenerated output |

---

## 15. Quyết định cần chốt trong quá trình làm

AI thực thi phải đề xuất dựa trên bằng chứng repo và ghi lại, không tự âm thầm quyết định các điểm sau:

1. Tái sử dụng knowledge runtime/warehouse hiện hữu hay tạo store mới nhỏ hơn.
2. Vị trí module và cách composition root truyền store vào consumer.
3. JSONL là canonical source hay cần SQLite ngay vì quy mô/hiệu năng thật.
4. Nguồn authoritative cho từng domain ngoài USGS.
5. Mức review con người cần thiết cho pháp lý, y tế, quân sự và các domain rủi ro cao.
6. Chính sách freshness cho từng loại fact.
7. Domain nào được migration sau USGS và domain nào tạm thời tắt đường trả lời canned vì chưa đủ nguồn.

---

## 16. Definition of Done toàn dự án

Chỉ được coi là hoàn thành khi tất cả điều kiện trong phạm vi đã duyệt đều có bằng chứng:

- [ ] WP0 inventory đã đối chiếu commit thực tế và wiring production.
- [ ] Schema/manifest/source registry đã có validator chạy thật.
- [ ] `KnowledgeStore` là đầu mối chung cho fact tĩnh production trong phạm vi migration.
- [ ] F1 USGS đạt test paraphrase, negative, ambiguity và regenerated-seed.
- [ ] Fact expert đã kiểm chứng hoặc bị loại khỏi đường trả lời verified.
- [ ] Reference/verifier fact có nguồn hoặc được phân loại là luật/hằng số đã phê duyệt.
- [ ] Seed/training/benchmark không bị production dùng làm knowledge.
- [ ] Literal fact đã thay thế được xóa; dead shell được xử lý có bằng chứng.
- [ ] Có guard chống tái phát và allowlist có lý do.
- [ ] Evidence/audit chứa `record_id` và `dataset_version` cho câu trả lời từ catalog.
- [ ] Rollback code/data đã thử trong môi trường test.
- [ ] Báo cáo cuối tách rõ vùng đã chạy kiểm chứng, vùng chỉ đọc code và vùng chưa kiểm chứng.
- [ ] Không còn tuyên bố “dữ liệu thật/verified” cho record chỉ mới được chuyển vị trí.

---

## 17. Điều kiện dừng bắt buộc

AI thực thi phải dừng và báo cáo thay vì tự đi tiếp nếu:

1. Không tái hiện được baseline hoặc không xác định được đường production thật.
2. Nguồn authoritative không truy cập được hoặc field tại nguồn không đủ chứng minh fact.
3. Test hiện hữu thất bại theo cách không giải thích được sau thay đổi.
4. Cần xóa dữ liệu/file ngoài phạm vi đã duyệt để làm test pass.
5. Cần dùng bí mật thật, gọi dịch vụ trả phí hoặc thay đổi hạ tầng ngoài phạm vi.
6. Phát hiện fact rủi ro cao đang được production trả lời mà không có nguồn; phải báo cáo mức độ và đề xuất vô hiệu hóa thay vì tự bịa nguồn.
7. Working tree có thay đổi xung đột trực tiếp với file dự kiến sửa.

---

## 18. Mẫu báo cáo sau mỗi gói công việc

```markdown
## Báo cáo WPx — <tên gói>

- Commit bắt đầu:
- Commit kết thúc (nếu có):
- Phạm vi đã làm:
- Phạm vi cố ý không làm:

### File đã thay đổi
- ...

### Dữ liệu
- Dataset version:
- Số record thêm/sửa/xóa:
- Số record verified/staging/rejected:
- Nguồn đã dùng:

### Kiểm thử đã chạy
- Lệnh:
- Kết quả: pass/fail/skip
- Test mới:

### Bằng chứng hành vi
- Câu hỏi/kịch bản:
- Kết quả trước:
- Kết quả sau:
- Record ID/dataset version:

### Chưa kiểm chứng
- ...

### Rủi ro còn lại và rollback
- ...

### Đề xuất gói tiếp theo
- ...
```

Không dùng từ “hoàn thành toàn bộ” nếu báo cáo mới bao phủ một WP.

---

## 19. Lệnh khởi động để giao cho AI trên PC

Chủ dự án có thể sao chép lệnh sau:

```text
Hãy đọc tài liệu docs/KNOWLEDGE_STORE_UNIFIED_DATA_PLAN_VN.md và thực hiện đúng vai trò AI thực thi trong tài liệu.

Bắt đầu bằng WP0 only: ghi commit/branch/trạng thái git, tái hiện F1, kiểm chứng lại inventory và wiring production tại commit hiện tại. Không sửa hành vi production, không xóa file, không chuyển dữ liệu trong WP0.

Giữ nguyên mọi thay đổi không liên quan trong working tree. Không đọc/in bí mật từ .env. Không dùng CI đỏ do billing làm bằng chứng lỗi code.

Sau WP0, dừng lại và báo cáo đúng mẫu ở Mục 18: điều đã chứng minh bằng cách chạy, điều chỉ đọc code, và điều chưa kiểm chứng. Chỉ đề xuất WP1, không tự thực hiện tiếp khi chưa được tôi duyệt.
```

Lệnh tiếp tục cho các gói sau:

```text
Tiếp tục tài liệu docs/KNOWLEDGE_STORE_UNIFIED_DATA_PLAN_VN.md. Chỉ thực hiện WP<x> trong phạm vi đã ghi. Trước khi sửa, đối chiếu báo cáo WP trước và commit hiện tại. Không mở rộng sang WP khác, không xóa dữ liệu/file ngoài cổng nghiệm thu của WP này. Sau khi xong, dừng và báo cáo theo Mục 18, tách rõ đã chạy kiểm chứng / chỉ đọc code / chưa kiểm chứng.
```

Lệnh kiểm tra độc lập sau khi AI thực thi báo cáo:

```text
Đóng vai trò reviewer độc lập cho WP<x> theo docs/KNOWLEDGE_STORE_UNIFIED_DATA_PLAN_VN.md. Không tin báo cáo của AI thực thi. Hãy đối chiếu diff, chạy lại test và kịch bản trọng yếu trong phạm vi WP, kiểm tra riêng các test âm tính/dương tính giả và bằng chứng nguồn của record. Báo cáo PASS chỉ trong phạm vi đã tự kiểm chứng; liệt kê riêng vùng chưa kiểm chứng. Không tự sửa code trừ khi tôi yêu cầu.
```

---

## 20. Khuyến nghị thứ tự giao việc thực tế

1. Giao **WP0** trước và yêu cầu dừng.
2. Duyệt inventory/wiring và quyết định kiến trúc từ WP0.
3. Giao **WP1 + WP2** thành hai phiên/PR tách biệt để sửa F1 trong phạm vi nhỏ.
4. Chỉ sau khi USGS pilot đạt mới giao WP3 và từng gói WP4 theo domain.
5. Không giao một lệnh “làm toàn bộ kế hoạch” trong một phiên. Mỗi WP phải có báo cáo, test và điểm dừng để tránh AI tuyên bố hoàn thành vượt bằng chứng.
6. Ưu tiên độ tin cậy hơn tốc độ tăng số fact. Một catalog nhỏ nhưng có nguồn và tra cứu đúng có giá trị hơn hàng nghìn fact chép tay không kiểm chứng.
