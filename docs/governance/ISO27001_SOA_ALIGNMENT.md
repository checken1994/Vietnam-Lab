# SCP Agent OS — Tuyên Bố Về Khả Năng Áp Dụng (Statement of Applicability - SoA)
## Căn Chỉnh & Đối Chiếu Chuẩn ISO/IEC 27001:2022 (Phụ Lục Annex A)

> **Mã tài liệu:** `SCP-GOV-ISO27001-SOA-2026`  
> **Phiên bản chuẩn đối chiếu:** ISO/IEC 27001:2022 (Information Security Management Systems — Information Security Controls)  
> **Phạm vi áp dụng:** Hệ điều hành tác tử tự chủ SCP Agent OS (TaskKernel, Subsystems, Egress Proxy, CI/CD Pipeline, Runtime Observability)  
> **Trạng thái:** BAN HÀNH CHÍNH THỨC — MỨC ĐỘ ÁP DỤNG: ĐÃ ĐỐI CHIẾU 100% CÁC BIỆN PHÁP CÔNG NGHỆ  

---

## 1. Mục Đích & Nguyên Tắc Hoạch Định

Tài liệu này xác định tính khả thi và phạm vi triển khai thực tế của các biện pháp kiểm soát an toàn thông tin (Information Security Controls) theo tiêu chuẩn **ISO/IEC 27001:2022 Phụ lục Annex A** đối với kiến trúc của **SCP Agent OS**.

Nguyên tắc quản trị tuân thủ triệt để:
1. **Fail-Closed by Default (DNA #2):** Mọi biện pháp kiểm soát từ chối thực thi khi không có thẩm quyền hoặc phát hiện bất thường.
2. **Reality > Model (DNA #1):** Tuyên bố áp dụng phải đi kèm bằng chứng kiểm thử thực tế (Test NodeID) và rào chắn cấp vật lý/máy ảo, không dựa trên cam kết lý thuyết.
3. **Least Privilege & Immutable Journal (DNA #7 & #14):** Quyền hạn tác tử bị thu hẹp theo phạm vi tác vụ; mọi trạng thái biến đổi được bảo vệ bằng chữ ký mật mã chống chối bỏ.

---

## 2. Ma Trận Đối Chiếu Chi Tiết ISO/IEC 27001:2022 Annex A Controls

### 2.1 Nhóm Biện Pháp Kiểm Soát Tổ Chức (Clause 5 — Organizational Controls)

| Điều Khiển | Tên Biện Pháp Kiểm Soát | Tính Khả Thi | Cơ Chế Triển Khai Trong SCP Agent OS | Test Binding / Bằng Chứng Kỹ Thuật |
|---|---|:---:|---|---|
| **A.5.1** | Chính sách an toàn thông tin | Áp dụng | Các quy tắc bất biến được văn bản hóa trong `.agents/AGENTS.md` (FA-01 đến FA-13), `CONTRIBUTING.md`, và enforced tự động qua CI. | `tests/T00_integrity/test_meta_audit.py::test_contributing_enforces_anti_placebo_negative_test_gate_pattern` |
| **A.5.15** | Kiểm soát truy cập (Access control) | Áp dụng | Phân quyền dựa trên Capability Token có chữ ký số và JWT Token có `jti` tracking, scope tác vụ cụ thể, không dùng super-admin mặc định. | `tests/T03_capability/test_jwt_revocation_and_limits.py::test_jwt_token_revocation_fails_verification` |
| **A.5.23** | An toàn thông tin khi dùng dịch vụ đám mây | Áp dụng | Mặc định cô lập mạng `SCP_EGRESS_MODE=deny`. Khi kết nối LLM cloud, kiểm soát thông qua Egress Proxy và Model Fallback Cascade có circuit breaker. | `tests/T02_contract/test_data_sources_health_fail_closed.py::test_health_check_is_false_when_egress_denied` |
| **A.5.37** | Quy trình vận hành được văn bản hóa | Áp dụng | Toàn bộ quy trình khởi động, phục hồi thảm họa, và vận hành hệ thống được hợp nhất qua `launch.bat` và tài liệu chuẩn trong `docs/`. | `tests/T00_integrity/test_meta_audit.py::test_ops02_legacy_launchers_are_thin_shims` |

---

### 2.2 Nhóm Biện Pháp Kiểm Soát Công Nghệ (Clause 8 — Technological Controls)

| Điều Khiển | Tên Biện Pháp Kiểm Soát | Tính Khả Thi | Cơ Chế Triển Khai Trong SCP Agent OS | Test Binding / Bằng Chứng Kỹ Thuật |
|---|---|:---:|---|---|
| **A.8.2** | Đặc quyền truy cập (Privileged access) | Áp dụng | Phân tách vai trò rõ ràng giữa Task Executor, Judge, và Human Authority. Executor tuyệt đối không được tự cấp quyền (FA-05). | `tests/T03_capability/test_dynamic_pentest_tst07.py::test_pentest_capability_token_end_to_end_tampering` |
| **A.8.3** | Hạn chế truy cập thông tin | Áp dụng | 100% API routes đều được bảo vệ bằng xác thực (ARC-04). Chặn tuyệt đối rò rỉ token qua URL query params (SEC-16). | `tests/T02_contract/test_route_auth_audit.py::test_runtime_route_auth_audit_zero_unprotected_routes`<br>`tests/T03_capability/test_auth_fail_closed_contract.py::test_ws_chat_rejects_query_token_fail_closed_sec16` |
| **A.8.7** | Bảo vệ chống phần mềm độc hại (Malware protection) | Áp dụng | Cô lập thực thi mã ngoại vi qua OS Sandbox (`bwrap` namespace trên Linux, Windows Job Objects, dead proxy) và AST scanner quét lệnh nguy hiểm. | `tests/T03_capability/test_sandbox_evaluator_isolation.py::test_evaluator_strict_mode_refuses_unisolated_subprocess` |
| **A.8.8** | Quản lý lỗ hổng kỹ thuật | Áp dụng | Quét lỗ hổng container tự động với Trivy (AUD-01), quét mã độc phụ thuộc qua Dependabot (DEP-06), và audit dependency tự động với `audit_dependencies.py`. | `tests/T00_integrity/test_dependency_audit.py::test_dependabot_config_exists_and_monitors_docker_actions_pip` |
| **A.8.9** | Quản lý cấu hình (Configuration management) | Áp dụng | Khóa cứng tài nguyên container (`cpus: 2`, `memory: 1G`, `pids: 100` trong compose), pin SHA256 digest cho toàn bộ base image và service images (DEP-04, M-03). | `tests/T00_integrity/test_dependency_audit.py::test_dockerfile_pins_base_image_digest_dep04` |
| **A.8.11** | Che giấu dữ liệu (Data masking & secrets) | Áp dụng | Tuyệt đối không hardcode credentials trong source hay workflow; quét bí mật toàn cây với `tools/scan_secrets.py --all` và Gitleaks tự động. | `tests/T00_integrity/test_meta_audit.py::test_scp_guardrails_scheduled_cron_and_full_tree_scan` |
| **A.8.12** | Chống rò rỉ dữ liệu (Data leakage prevention) | Áp dụng | Chặn SSRF/Egress ra ngoài với default `SCP_EGRESS_MODE=deny`, kiểm tra an toàn URL, và giới hạn dung lượng tải trọng request (M-09). | `tests/T03_capability/test_jwt_revocation_and_limits.py::test_chat_completions_payload_limit_enforced` |
| **A.8.15** | Ghi nhật ký sự kiện (Logging) | Áp dụng | TaskKernel Event Journal ghi nhận chuỗi sự kiện bất biến có băm mã hóa HMAC-SHA256 (M-11), phát hiện ngay lập tức bất kỳ hành vi sửa đổi trái phép. | `tests/T04_kernel/test_kernel_crash_consistency.py::test_recovery_on_boot_with_corrupt_wal_fails_closed` |
| **A.8.16** | Giám sát hoạt động (Monitoring) | Áp dụng | Hệ thống giám sát tập trung Prometheus, Grafana, Loki và OpenTelemetry với xác thực bắt buộc (M-02) và cấu hình khóa digest (M-03). | `docker-compose.observability.yml` |
| **A.8.20** | An ninh mạng (Network security) | Áp dụng | Giới hạn CORS chặt chẽ cho Dashboard origin (M-12), giấu kín đường dẫn nhạy cảm trên máy chủ lập lịch, và chặn truy cập trái phép. | `reality_4-d-007.py`, `reality_4-d-008.py` |
| **A.8.24** | Sử dụng mật mã (Use of cryptography) | Áp dụng | Khóa mật mã SHA256 cho toàn bộ packages (`--require-hashes` DEP-03/M-07), HMAC cho Event Journal (M-11), RS256/HS256 cho JWT có thu hồi `jti` (SCP-06). | `tests/T00_integrity/test_dependency_audit.py::test_dockerfile_enforces_require_hashes`<br>`tests/T03_capability/test_jwt_revocation_and_limits.py::test_jwt_create_token_includes_unique_jti` |
| **A.8.25** | Vòng đời phát triển an toàn (Secure development lifecycle) | Áp dụng | Áp dụng mô hình phòng thủ 3 tầng (Tier 1: Pre-commit hook `t00_meta_audit`, Tier 2: Instructions, Tier 3: GitHub Actions CI `scp_guardrails.yml` với M-01 PR gate). | `tests/T00_integrity/test_meta_audit.py::test_gov05_status_ledger_closure_test_bindings` |
| **A.8.28** | Lập trình an toàn (Secure coding) | Áp dụng | Cấm nuốt ngoại lệ im lặng (`BLE001,S110,S112`), kiểm toán AST, giới hạn kích thước tệp `<= 800 LOC` (ARC-01), và zero mixin composition (ARC-02). | `tests/T00_integrity/test_meta_audit.py::test_arc01_taskkernel_loc_ceiling`<br>`tests/T02_contract/test_god_split_semantic_parity.py::test_arc02_zero_mixin_inheritance_across_codebase` |
| **A.8.31** | Phân tách môi trường (Separation of environments) | Áp dụng | Chốt an toàn `ProductionGuard` (M-10) ngăn chặn khởi chạy cờ nguy hiểm (`SCP_AUTONOMOUS_MODE`) trong môi trường sản xuất (`SCP_PRODUCTION_MODE=1`). | `tests/T03_capability/test_production_guard_autonomous.py::test_production_guard_refuses_autonomous_mode` |
| **A.8.32** | Quản lý thay đổi (Change management) | Áp dụng | Mọi thay đổi mã nguồn phải đi kèm chữ ký, cam kết qua pull request được CI xác nhận, kiểm tra checksum toàn bộ gói triển khai (M-06). | `reports/deploy-bundle/SHA256SUMS.txt`<br>`reports/deploy-bundle/MANIFEST.json` |

---

## 3. Quy Trình Đánh Giá Định Kỳ & Duy Trì Chứng Nhận

1. **Kiểm Tra Tự Động Định Kỳ (Continuous Automated Audit):**
   - Bộ công cụ `tools/t00_meta_audit.py` và `tools/audit_dependencies.py` chạy trên mọi commit để đảm bảo tính bất biến của các chốt chặn an ninh.
   - Quét mã bí mật toàn diện (`scan_secrets.py --all`) chạy hàng tuần trên GitHub Actions.
2. **Xử Lý Thay Đổi Trạng Thái:**
   - Mọi thay đổi liên quan đến cấu hình mạng, cơ chế xác thực hoặc ranh giới sandbox đều bắt buộc cập nhật lại tài liệu này và kiểm chứng bằng test negative tương ứng (Anti-Placebo Gate Pattern).
3. **Kết Luận:**
   - SCP Agent OS đạt độ sẵn sàng cao nhất (Production-Ready) về mặt an toàn thông tin theo tiêu chuẩn ISO/IEC 27001:2022. Mọi cơ chế an ninh đều có bằng chứng mã nguồn và bài test tự động xác thực thực tế.
