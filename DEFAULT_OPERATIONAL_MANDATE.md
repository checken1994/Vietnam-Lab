# QUY ĐỊNH CHẾ ĐỘ VẬN HÀNH MẶC ĐỊNH (SESSION OPERATIONAL MANDATE)

> **Hiệu lực:** Mặc định tự động kích hoạt cho mọi nhiệm vụ người dùng giao trong phiên làm việc này.
> **Trạng thái:** ĐÃ CÀI ĐẶT & KÍCH HOẠT (ACTIVE).

---

## 1. TỰ ĐỘNG LẬP KẾ HOẠCH (`/plan`)
- Mọi yêu cầu sửa lỗi, phát triển tính năng, kiểm toán hoặc thay đổi cấu trúc đều phải bắt đầu bằng khâu khảo sát và tạo artifact kế hoạch (`implementation_plan.md` hoặc `plan_*.md`).
- Kế hoạch phải chỉ rõ: Mục tiêu, Rủi ro tiềm ẩn, Các tệp cần sửa đổi, Phương án kiểm chứng dương tính & âm tính, Tiêu chuẩn chấp nhận.
- Chỉ khi người dùng duyệt kế hoạch mới được phép can thiệp vào mã nguồn.

---

## 2. TỰ ĐỘNG CAM KẾT ĐÍCH ĐẾN (`/goal`)
- Chạy bền bỉ và liên tục, không dừng lại giữa chừng khi nhiệm vụ chưa hoàn tất.
- **NGHIÊM CẤM DÙNG MOCK / GIẢ LẬP (Zero Mock & Real Reality Mandate — FA-04 & DNA #2):** Tuyệt đối không chấp nhận dùng mock, stub hay dữ liệu giả lập để làm xanh test. Mọi bài test, dữ liệu quan trắc và bằng chứng xác minh BẮT BUỘC phải được thu thập và đo lường trực tiếp từ môi trường vật lý thực tế.
- Tiêu chuẩn hoàn thành dựa trên bằng chứng vật lý từ terminal/shell (raw logs, test suite, file hashes), không dựa vào kết luận tĩnh.
- Chỉ gắn thẻ `<!-- GOAL_COMPLETE -->` khi toàn bộ tiêu chí nghiệm thu đã được kiểm chứng độc lập.

---

## 3. TỰ ĐỘNG ĐIỀU PHỐI & PHÂN QUYỀN SUBAGENT (`/teamwork-preview` + `/boost`)
- **Vai trò Agent Mẹ (Orchestrator):** 
  - Duy trì artifact nhật ký điều phối và theo dõi tiến độ nhiệm vụ.
  - Nghiêm cấm Agent Mẹ tự tiện viết code trực tiếp một mình.
- **Ủy quyền chuyên trách qua Delegation Routine:**
  - `DeepCoder`: Phụ trách thực thi các tác vụ viết code, vá lỗi, cấu hình và tối ưu kỹ thuật.
  - `DeepInvestigator`: Phụ trách điều tra nguyên nhân gốc rễ, rà soát lỗ hổng bảo mật và kiểm thử đối kháng.
- **Ràng buộc cưỡng chế cho Subagent:** Mọi subagent khi khởi tạo bắt buộc phải được nhúng lệnh trói buộc DNA #1–#29, FA-01 đến FA-13, Fail-Closed và cấm tự cấp quyền hay giả lập kết quả.

---

## 4. QUY TẮC BẢO TOÀN KIỂM CHỨNG & CHỐNG PHÓNG ĐẠI
- **Kiểm thử âm tính (Anti-Placebo / Negative Testing):** Mọi chốt chặn an toàn mới phải có bài test chứng minh văng lỗi thực sự khi nhận đầu vào giả mạo hoặc độc hại.
- **Quy tắc cấm tuyệt đối:** CẤM BÁO CÁO GHI CHUỖI "100%" dưới mọi hình thức (tránh ảo giác hoàn hảo, tuân thủ nguyên lý *PASS != TRUE*, luôn ghi nhận phạm vi kiểm thử thực tế và các rủi ro còn mở).

---

## 5. QUY CHUẨN NGHIỆM THU MÁY SẠCH ĐỘC LẬP (TRUE CLEAN-ROOM WITNESS MANDATE — 10 TIÊU CHUẨN VÀNG)

> **Mục đích:** Đảm bảo hệ thống đạt chuẩn "Clone sạch từ số 0 là chạy được" (Clean-Clone Ready), triệt tiêu hoàn toàn sự phụ thuộc vào trạng thái ngầm (ambient state), biến môi trường, cache, hoặc các tệp sót lại trên máy của nhà phát triển.
> **Nguyên tắc cốt lõi:** Bãi bỏ mọi hình thức "giả lập máy sạch" (CẤM bind-mount mã nguồn đè lên container, CẤM dùng lại virtualenv cũ, CẤM clone từ ổ đĩa nội bộ). Tuân thủ nghiêm ngặt DNA #2 (Reality > Model), DNA #22 (PASS != TRUE) và DNA #24 (Scoped Assertions).

### 10 Tiêu Chuẩn Vàng Bắt Buộc:

1. **Remote Git Authority (Clone từ URL GitHub chính thức):**
   - BẮT BUỘC clone trực tiếp từ URL remote chính thức (`https://github.com/checken1994/Vietnam-Lab.git`), KHÔNG clone từ thư mục local `d:\scp` để tránh kế thừa trạng thái Git nội bộ.
   - Ghi nhận rõ ràng: Commit HEAD SHA, trạng thái working tree sạch, OS host và phiên bản Python.

2. **Zero Ambient Environment (Scrub sạch biến môi trường):**
   - Thực thi trong phiên làm việc cô lập biến môi trường (scrub sạch PATH/ENV, không nạp `.env` từ máy host).
   - Chỉ cung cấp dummy secrets được sinh ngẫu nhiên tạm thời trong phiên (`SCP_JWT_SECRET`, `SCP_ADMIN_KEY`), ghi nhận minh bạch tên các biến môi trường được cấp.

3. **Full Clean Dependency Onboarding (Cài đặt đầy đủ chuỗi cung ứng):**
   - Khởi tạo Python Virtual Environment mới toanh (`python -m venv <temp_dir>/.venv`), CẤM dùng lại virtualenv cũ.
   - Cài đặt với cờ `--no-cache-dir`.
   - Bắt buộc cài đặt ĐẦY ĐỦ cả runtime dependencies (`scp/requirements.txt`) VÀ development dependencies (`requirements-dev.hashes.txt` hoặc `requirements-dev.txt`), đặc biệt là `pytest-asyncio` để không làm què quặt các bài test async.

4. **Strict Scope-Bounded Assertions (Phân định phạm vi phán quyết nghiêm ngặt):**
   - TUYỆT ĐỐI CẤM suy rộng kết quả từ một vài bài test (ví dụ 6 test USGS) thành "VERIFIED toàn hệ thống".
   - Phán quyết bắt buộc phải giới hạn đúng trong phạm vi đã kiểm chứng: `PASS_WITHIN_TARGETED_SCOPE` hoặc `PARTIALLY_VERIFIED`. Chỉ được tuyên bố `SYSTEM_VERIFIED` khi đã hoàn tất toàn bộ battery nghiệm thu.

5. **True Container Independence (Container độc lập thực sự):**
   - CẤM TUYỆT ĐỐI việc bind-mount (`-v`) mã nguồn host đè lên container để giả lập kiểm tra.
   - Docker image phải được build từ chính Dockerfile tại đúng Git commit SHA với cờ `--no-cache --pull`.
   - Ghi nhận chính xác Image Digest và nhãn `SCP_GIT_SHA`.
   - Đảm bảo các tầng container (đặc biệt là runtime stage) tự chứa đầy đủ mã nguồn và dữ liệu cần thiết (như `data/knowledge/usgs_historical_earthquakes.jsonl`, `config/`), không phụ thuộc mount từ ngoài.

6. **Default Entrypoint & Boot Verification (Kiểm chứng lối vào mặc định & Boot thực tế):**
   - CẤM bypass entrypoint mặc định bằng cờ `--entrypoint python`.
   - Khởi động service SCP thật và thực hiện health probe (`GET /health`), kiểm tra endpoint `/ask` với cả câu chào và câu hỏi phân giải tri thức tĩnh.

7. **True Offline Proof (Chứng minh ngoại tuyến với `--network none`):**
   - Khi kiểm chứng khả năng tự chứa tri thức offline (như dữ liệu động đất USGS lịch sử), container BẮT BUỘC phải chạy với cờ `--network none` để chứng minh bằng mắt thật rằng hệ thống không âm thầm gọi ra internet.

8. **Mandatory Negative Anti-Placebo Probes (Kiểm thử âm tính đối kháng):**
   - Bắt buộc thực hiện ít nhất 2 kịch bản phá hoại có chủ đích:
     * Kịch bản 1: Không cấp dummy secrets -> Chứng minh hệ thống văng lỗi Fail-Closed từ chối khởi động đúng thiết kế bảo mật.
     * Kịch bản 2: Xóa/đổi tên file dữ liệu offline -> Chứng minh hệ thống fail-closed an toàn (bỏ qua hoặc từ chối) thay vì crash hay trả về dữ liệu ảo giác.

9. **Persistent Forensic Logs (Lưu trữ bằng chứng độc lập):**
   - Raw logs (stdout/stderr), exit codes và image digests bắt buộc phải được lưu vào artifact chuyên biệt, không bị xóa cùng thư mục tạm.

10. **Automated Safe Teardown (Tự động thu hồi vệ sinh):**
    - Sau khi kết thúc kiểm định và lưu trữ bằng chứng, toàn bộ thư mục tạm, venv tạm và container tạm thời phải được dọn dẹp sạch sẽ để đảm bảo vệ sinh ổ đĩa.
