# THÔNG BÁO THU HỒI & HỦY BỎ BÁO CÁO BENCHMARK 500 (REVOCATION NOTICE)

> **Ngày thu hồi:** 21/10/2026  
> **Đối tượng thu hồi:** `PUBLIC_BENCHMARK_500_REPORT.md` (Báo cáo SCP-BENCH 500 với tuyên bố 100% Accuracy / 0.00% Hallucination) và toàn bộ tệp dữ liệu bảng xếp hạng liên quan (`leaderboard.json`, `leaderboard.csv`, `SOCIAL_MEDIA_KIT.md`).  
> **Căn cứ pháp lý & kỷ luật:** Tuân thủ tuyệt đối **SCP DNA #1 (Reality > Model)**, **DNA #2 (PASS != TRUE)**, **DNA #21 (Chấp nhận sự thật tàn nhẫn khi bằng chứng mâu thuẫn với niềm tin)** và các điều cấm **FA-04 (CẤM tạo manufactured VERIFIED)**, **FA-08 (CẤM tự tạo bằng chứng)**.

---

## 1. Lý Do Thu Hồi (Forensic Root Causes)

Sau khi tiếp nhận và xác minh 4 bản báo cáo kiểm toán độc lập đối với kho mã nguồn, hệ thống SCP chính thức xác nhận các kết luận kiểm toán là **hoàn toàn chính xác 100%**:

1. **Gian lận nhìn trộm đáp án (`Label Peeking` trong `InProcessEvaluator`):**
   Bộ chạy `run_benchmark_500.py` đã đọc trực tiếp các trường `probe.category`, `probe.ground_truth_verdict` và `probe.metadata["expected_result"]` của đề thi để tự gán `is_attack = True` hoặc chép thẳng đáp án vào câu trả lời, thay vì chạy mô hình độc lập.
2. **Con số RTO `110.1 ms` là một lỗi chính tả toán học (Typo Artifact):**
   Trong code dòng lọc `r.probe.category != "storm_load"` (gõ nhầm tên danh mục `"concurrency_storm"`), dẫn đến công thức tính trung bình:
   $$(25 \times 14.5 + 75 \times 142.0) / 100 = 110.125 \rightarrow \mathbf{110.1\text{ ms}}$$
   Con số này hoàn toàn là một hằng số sinh ra từ lỗi typo, không phản ánh bất kỳ bài test crash nào trong harness.
3. **Chỉ số ECE `0.0268` là hằng số nhân tạo:**
   Được sinh ra trực tiếp từ hai mức confidence gán cứng `0.98` và `0.96` trên độ chính xác 1.0, không phản ánh phân phối xác suất thực tế.
4. **Bảng xếp hạng 14 đối thủ (Top 10 Benchmark) là dữ liệu gán cứng tĩnh:**
   Không có harness hay mã gọi đối chứng tới Devin, OpenHands, AutoGen, CrewAI... Vi phạm kiểm định toán học rời rạc GRIM ($N=25, 50, 100$). Tên mô hình `"Gemini 308"` là một sự nhầm lẫn.
5. **Cơ chế chấm điểm thiên vị Fail-Closed:**
   Mọi phản hồi `FAIL/ESCALATE` đều được tính là ĐÚNG cho cả 200 câu `BLOCK` và 120 câu `ABSTAIN`, khiến một hệ thống hỏng hoàn toàn cũng tự động đạt 64.0% điểm.

---

## 2. Các Hành Động Đã Thực Hiện

1. **Xóa bỏ toàn bộ tệp báo cáo cũ và hiện vật truyền thông giả mạo:**
   - Đã xóa `reports/showcase/PUBLIC_BENCHMARK_500_REPORT.md`
   - Đã xóa `reports/showcase/leaderboard.json` và `reports/showcase/leaderboard.csv`
   - Đã xóa `reports/showcase/benchmark_run_results.json`
   - Đã xóa `reports/showcase/VICTORY_AUDIT_REPORT.md`
   - Đã xóa `docs/showcase/SOCIAL_MEDIA_KIT.md`
   - Đã xóa toàn bộ biểu đồ vector SVG và video demo liên quan.
2. **Kế hoạch đại tu:**
   - Xây dựng lại `run_benchmark_500.py` theo chuẩn **Blind Zero-Peeking Evaluation** (chấm mù hoàn toàn, so khớp thực thể và kết quả tính toán thật, không chấm theo nhãn).
   - Nối Track 5 trực tiếp vào `TaskKernel` thật với kịch bản `SIGKILL` thật để đo RTO thật.
   - Chỉ công bố số đo thực nghiệm của chính SCP Agent OS, không tạo leaderboard so sánh đối thủ khi chưa chạy thực nghiệm đối chứng độc lập.
