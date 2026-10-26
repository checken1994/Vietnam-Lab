# QUY CHUẨN NGHIỆM THU MÁY SẠCH ĐỘC LẬP (TRUE CLEAN-ROOM WITNESS PROTOCOL)

> **Mã hiệu:** SCP-SPEC-CLEANROOM-01  
> **Áp dụng cho:** Mọi AI Agent (Gemini, Claude, ChatGPT...), kiểm toán viên độc lập và hệ thống CI/CD khi thực hiện kiểm định hoặc công bố phán quyết bàn giao phát hành (Release Gate / Verification Verdict) cho SCP.  
> **Nguyên tắc nền tảng:** DNA #2 (Reality > Model), DNA #22 (PASS != TRUE), DNA #24 (Scoped Assertions), Fail-Closed by Default.

---

## 1. Mục Đích & Phạm Vi Áp Dụng

Mục đích của quy chuẩn này là đảm bảo mọi tuyên bố "Chạy được từ số 0" hoặc "Sẵn sàng phát hành" (Clean-Clone / Production-Ready) phải dựa trên **bằng chứng thực nghiệm độc lập và tái lập được từ xa**, loại bỏ hoàn toàn các giả định ngầm từ môi trường của người phát triển (Zero Ambient State).

---

## 2. 10 Tiêu Chuẩn Vàng Bắt Buộc (10 Golden Standards)

### Tiêu Chuẩn 1: Remote Git Authority (Clone từ URL Remote Chính Thức)
- Kiểm định viên BẮT BUỘC thực hiện `git clone` trực tiếp từ URL repository chính thức trên GitHub (`https://github.com/checken1994/Vietnam-Lab.git`).
- TUYỆT ĐỐI CẤM clone từ thư mục local trên cùng ổ đĩa để tránh kế thừa các tệp chưa track, cache nội bộ hoặc cấu hình git cục bộ.
- Bắt buộc ghi nhận: Commit HEAD SHA, trạng thái working tree sạch, phiên bản Hệ điều hành và phiên bản Python.

### Tiêu Chuẩn 2: Zero Ambient Environment (Cô Lập Hoàn Toàn Biến Môi Trường)
- Quá trình kiểm định phải chạy trong phiên làm việc cô lập: scrub sạch `PATH`, không nạp tệp `.env` có sẵn của máy host.
- Chỉ cung cấp các dummy secrets tự sinh ngẫu nhiên tạm thời trong phiên phục vụ kiểm thử (ví dụ: `SCP_JWT_SECRET`, `SCP_ADMIN_KEY`), ghi nhận minh bạch tên các biến môi trường được cấp.

### Tiêu Chuẩn 3: Full Clean Dependency Onboarding (Cài Đặt Đầy Đủ Chuỗi Cung Ứng)
- Khởi tạo Python Virtual Environment mới tinh (`python -m venv <dir>/.venv`), CẤM sử dụng lại virtualenv có sẵn.
- Cài đặt với cờ `--no-cache-dir`.
- Bắt buộc cài đặt ĐẦY ĐỦ cả runtime dependencies (`scp/requirements.txt`) VÀ development dependencies (`requirements-dev.hashes.txt` hoặc `requirements-dev.txt`), đặc biệt là `pytest-asyncio` để không làm sai lệch các bài kiểm thử bất đồng bộ.

### Tiêu Chuẩn 4: Strict Scope-Bounded Assertions (Phân Định Phạm Vi Phán Quyết Nghiêm Ngặt)
- TUYỆT ĐỐI CẤM suy rộng kết quả từ một vài bài test (ví dụ 6 test dữ liệu tĩnh) thành phán quyết `VERIFIED` toàn hệ thống (vi phạm trực tiếp DNA #22 PASS != TRUE).
- Phán quyết bắt buộc phải giới hạn chính xác trong phạm vi đã kiểm chứng:
  * `PASS_WITHIN_TARGETED_SCOPE`: Nếu chỉ chạy một lát cắt kiểm thử cụ thể (Domain slice, Security sweep).
  * `PARTIALLY_VERIFIED`: Nếu các thành phần chính đã đạt nhưng vẫn còn phụ thuộc môi trường (ví dụ: thiếu bản build Next.js).
  * `SYSTEM_VERIFIED`: Chỉ được tuyên bố khi toàn bộ battery nghiệm thu (toàn bộ suite) đều đạt kết quả xanh trên môi trường sạch.

### Tiêu Chuẩn 5: True Container Independence (Container Độc Lập Thực Sự)
- CẤM TUYỆT ĐỐI việc bind-mount (`-v`) mã nguồn từ host đè lên container để giả lập kiểm tra.
- Docker image phải được build từ chính `Dockerfile` tại đúng Git commit SHA với cờ `--no-cache --pull`.
- Ghi nhận chính xác Image Digest và nhãn `SCP_GIT_SHA`.
- Tầng runtime của container bắt buộc phải tự chứa đầy đủ mã nguồn và dữ liệu cần thiết (bao gồm catalog `data/knowledge/` và mẫu `config/`), không phụ thuộc vào bất kỳ mount ngoại vi nào.

### Tiêu Chuẩn 6: Default Entrypoint & Boot Verification (Kiểm Chứng Lối Vào Mặc Định)
- CẤM bypass entrypoint mặc định của container.
- Khởi động service SCP thực tế và kiểm tra liveness/readiness probe (`GET /health`), kiểm tra endpoint `/ask` với cả câu chào xác định và câu hỏi phân giải tri thức tĩnh.

### Tiêu Chuẩn 7: True Offline Proof (Chứng Minh Ngoại Tuyến)
- Khi kiểm chứng khả năng tự chứa dữ liệu ngoại tuyến (như catalog địa chấn lịch sử USGS), container BẮT BUỘC phải chạy với cờ `--network none` để chứng minh bằng mắt thật rằng hệ thống không âm thầm gọi ra mạng internet.

### Tiêu Chuẩn 8: Mandatory Negative Anti-Placebo Probes (Kiểm Thử Âm Tính Đối Kháng)
- Bắt buộc thực hiện ít nhất 2 kịch bản kiểm thử phá hoại có chủ đích:
  * Kịch bản 1: Không cung cấp secret kiểm tra -> Chứng minh hệ thống văng lỗi Fail-Closed từ chối khởi động đúng thiết kế bảo mật.
  * Kịch bản 2: Xóa/đổi tên tệp dữ liệu offline -> Chứng minh hệ thống fail-closed an toàn (bỏ qua hoặc từ chối) thay vì crash hoặc sinh ảo giác.

### Tiêu Chuẩn 9: Persistent Forensic Logs (Lưu Trữ Bằng Chứng Độc Lập)
- Toàn bộ raw output (stdout/stderr), exit codes và image digests bắt buộc phải được lưu vào artifact hoặc báo cáo độc lập, không bị xóa theo thư mục tạm.

### Tiêu Chuẩn 10: Automated Safe Teardown (Thu Hồi Dọn Dẹp Sạch Sẽ)
- Sau khi hoàn thành kiểm định và lưu trữ bằng chứng, toàn bộ thư mục tạm, venv tạm và container tạm thời phải được xóa sạch hoàn toàn để đảm bảo vệ sinh đĩa cứng và bảo mật tài nguyên.
