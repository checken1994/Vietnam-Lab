# TASK AG — bundle 3 việc ĐỘC LẬP, KHÔNG xung đột ai (giao 13:1x)

Phân cho 3 sub-agent chạy song song (luật owner mới: nhận nhiều việc → tự spawn agent). Mỗi việc 1 file done riêng vào inbox.

## Việc 1 — SEC-06 (BLOCKER P8, ưu tiên cao nhất): daemon.py bỏ subprocess trực tiếp
- `scp/verifier_service/daemon.py:24,166,167,221` có 11 reference subprocess trực tiếp — vi phạm pin SEC-06 (phải qua `scp.core.safe_process`).
- Fix: chuyển toàn bộ qua safe_process wrapper, giữ nguyên hành vi (semantics), test đỏ-trước nếu có thể (bỏ 1 chỗ ⇒ test sec06 đỏ).
- File done: `sec06-done.md` — diff + bằng chứng test sec06 xanh trên fix / đỏ khi gỡ fix.

## Việc 2 — safe_math thêm sqrt + factorial (buyer B1 "căn bậc hai của 144" trả "12")
- `scp/runtime/safe_math.py` hiện chỉ + - * / và ^ (XOR legacy). Thêm: `sqrt(...)`, `can(...)`/`căn bậc hai của N` tiếng Việt, factorial dạng `N!`. Giữ an toàn: không eval tự do, chỉ parser tách biệt như hiện có.
- Đổi `^` thành lũy thừa thật? KHÔNG tự quyết — báo trong done-file như đề xuất kèm rủi ro (hiện 2^10=8 XOR; đổi thành 1024 là breaking behavior — chờ owner).
- Anti-regression: "1+1"=2, "17*23"=391 giữ nguyên; sqrt(144)=12, 5!=120 mới.
- File done: `safemath-done.md` — diff + test đỏ-trước + mutant.

## Việc 3 — BUYER-06 vệ sinh nhỏ (3 gói độc lập)
- a) `tests/T00_integrity/test_meta_audit.py:1148` assert dư `<=1000` ngay trước `<=800` — xóa dòng dư (trần thật 800).
- b) `tools/scan_secrets.py` false positive: `scp/security/approval_issuer.py:26 "approval_ed25519"` là nhãn — tune scanner cho hết FP này KHÔNG hạ tổng thể (FP test case giữ đỏ khi gặp secret thật).
- c) Pin 7 gói float trong `scp/requirements-optional-ml.txt` (pillow, edge_tts, huggingface_hub, networkx, pytesseract, websockets, openai-whisper) — pin theo version hiện lắp được (pip index hoặc lock có sẵn), ghi rõ version nguồn.
- File done: `buyer06-done.md` — 3 diff nhỏ + bằng chứng từng cái.

## BẤT BIẾN
- KHÔNG đụng: api_server.py, pipeline stages, router_rules, delivery_rules, verification/generation/lookup (Claude đang integrate), daemon.py chỉ việc 1 được đụng.
- Không hạ chuẩn test. Mọi verify chạy thật (.venv/Scripts/python.exe).
- Quy trình: guard CHECKER verify từng file done; REJECTED = sửa theo feedback.
