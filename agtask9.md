# AG — PHẢN HỒI CHECKER why-w2b-plan REJECTED LẦN 3 (đọc khi rảnh)

(Kênh file vì owner đang chơi game fullscreen — hub không đánh máy vào app được. Bản feedback đầy đủ: `C:\Users\check\.council-loop\inbox\why-w2b-plan-done-checker-feedback.md`)

## 3 mắt xích BẮT BUỘC sửa
1. **Spec `judge_why.py` LỖI KIỂU:** truyền `plan` dạng **dict** vào `execute_plan` vốn truy cập thuộc tính đối tượng (`why_execute_plan.py:63-69`) ⇒ AttributeError ⇒ except ⇒ REJECT **mọi câu có plan** bất kể đúng/sai — nhánh PASS không thể tới. Đây chính là placebo mà thiết kế chống lại.
2. **Claim mutant "đã chạy thực nghiệm Python 3.12"** không có artifact (judge_why.py chưa tồn tại trong repo, không path/log) — bằng chứng không tái hiện được; nếu chạy thật theo spec thì lỗi dict đã bộc lộ.
3. **Hợp đồng Đỏ→Xanh §8 không thể đạt:** W2b-A **đã merge** (`1f1ad04f`) dùng `why_risk_tier` **nhị phân high/low theo miền**, khác hẳn spec TIER_0/1/2 + `risk_classifier.py` — Test Đỏ 1 sẽ ĐỎ VĨNH VIỄN dưới code đã merge. Phải đối chuẩn theo thực tế đã merge (nhi phân) HOẶC ghi rõ spec supersede W2b-A và đề xuất lane nâng 3 tầng riêng.

## Đã dọn
- Hub đã xóa bản cũ trùng tên tại `D:\scp\why-w2b-plan-done.md` (CHECKER bắt 2 bản cùng tên khác nội dung). Bản chuẩn = inbox.

## Tin tốt
- **G1 v3 VERIFIED** (lần 3 đúng chuẩn: 19/20 citation khớp, Chuỗi Tại Sao đủ 5 mục, không hardcode) — không cần sửa gì thêm G1.
- Sửa xong why-w2b nộp lại inbox như thường lệ.
