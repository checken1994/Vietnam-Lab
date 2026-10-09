# Chỉ Mục Trạng Thái Báo Cáo Kiểm Toán (Audit Report Status Index)

> **Quy tắc phân định nguồn sự thật (Ground Truth Rule - GA.md § A1 & DNA #22):**  
> Các báo cáo trong thư mục này phản ánh kết quả kiểm toán tại các mốc thời gian và Git commit SHA cụ thể. Mọi phán quyết kỹ thuật hiện hành phải căn cứ vào mã nguồn thực tế, các bài kiểm tra thực nghiệm (Reality verification) trên đúng commit SHA, và tài liệu điều phối trung tâm [`GA.md`](../../GA.md) trên nhánh `main`.

---

## 1. Bảng Chỉ Mục Trạng Thái Báo Cáo (Status Index Matrix)

| Tên Tệp Báo Cáo | Ngày Thực Hiện | Baseline Git SHA / Nhánh | Phân Loại Trạng Thái | Mô Tả & Phạm Vi Đánh Giá |
|---|:---:|:---:|:---:|---|
| [`LIVE_OPERATIONAL_AUDIT_REPORT_20261004.md`](LIVE_OPERATIONAL_AUDIT_REPORT_20261004.md) | 2026-10-04 | `1aef24a8` (main) | **`CURRENT / ACTIVE GROUND TRUTH`** | Báo cáo kiểm toán vận hành thực tế mới nhất trong thư mục `docs/audits/`. Đạt chuẩn bằng chứng Level C (End-to-end Runtime Proof) trên cụm dịch vụ live (FastAPI :8000, Bun :8081, Next.js :3000), kiểm chứng sạch sẽ socket teardown và toàn vẹn sổ cái HMAC. |
| [`DEEP_AUDIT_REPORT_20260927.md`](DEEP_AUDIT_REPORT_20260927.md) | 2026-09-27 | Snapshot `2026-09-27` (~2,466 tests) | **`SUPERSEDED / HISTORICAL ARCHIVE`** | Hết hiệu lực từ 2026-10-04 sau khi đợt cải tổ lớn và Live Operational Audit hoàn thành. Lưu trữ lịch sử 57 phát hiện kiểm toán chuyên sâu line-by-line làm tiền đề cho các chiến dịch remediation. |
| [`COMPLETION_REPORT_20260928.html`](COMPLETION_REPORT_20260928.html) | 2026-09-28 | Snapshot `2026-09-28` | **`SUPERSEDED / HISTORICAL ARCHIVE`** | Hết hiệu lực từ 2026-10-04. Báo cáo nghiệm thu kết thúc giai đoạn kiểm toán chuyên sâu cuối tháng 09/2026. |
| [`COMPREHENSIVE_AUDIT_REPORT.md`](COMPREHENSIVE_AUDIT_REPORT.md) | 2026-09-22 | `6d6256f` (`feature/autonomous-mode-antigravity-v2`) | **`SUPERSEDED / HISTORICAL ARCHIVE`** | Hết hiệu lực từ 2026-09-27 khi Deep Audit phát hành. Đợt kiểm toán toàn diện 6 dòng R1-R6 trên snapshot nhánh cũ. |
| [`BAO_CAO_KIEM_TOAN_TOAN_HE_THONG.md`](BAO_CAO_KIEM_TOAN_TOAN_HE_THONG.md) | 2026-09-22 | `6d6256f` (`feature/autonomous-mode-antigravity-v2`) | **`SUPERSEDED / HISTORICAL ARCHIVE`** | Hết hiệu lực từ 2026-09-27. Phiên bản tiếng Việt tương ứng của `COMPREHENSIVE_AUDIT_REPORT.md`. |

---

## 2. Tiêu Chí Phân Loại & Vòng Đời Báo Cáo

- **`CURRENT / ACTIVE GROUND TRUTH`**:
  - Là báo cáo vận hành gần nhất có giá trị quy chiếu thực nghiệm hợp lệ cho kiến trúc và các luồng nghiệp vụ hiện tại.
  - Được bảo lưu làm mốc so sánh độ lệch (drift baseline) cho tới khi có một cuộc kiểm toán vận hành toàn diện mới thay thế.
- **`SUPERSEDED / HISTORICAL ARCHIVE`**:
  - Các tài liệu kiểm toán đã được thay thế bởi các đợt kiểm toán mới hơn hoặc đã giải quyết xong các lỗ hổng/phát hiện qua các đợt remediation PR.
  - Được lưu trữ nguyên trạng nhằm phục vụ truy nguyên pháp y (forensic lineage) và đối chiếu lịch sử tiến hóa của hệ thống.
