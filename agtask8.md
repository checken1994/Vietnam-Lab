# TASK AG — CỔNG 1 đích-b: knowledge reader tiers (giao 20:5x)

Nền ĐÃ VERIFIED: KB đang có news_fact 4 bản ghi answer=verified_true (nguồn news+ollama) nhưng 0 tier nào đọc "tin hôm nay" (GA.md:373 gọi là Ưu tiên 2 — nhỏ, lợi lớn, không cần egress).

## Việc
1. Tier reader cho news_fact: khi câu hỏi lookup dính "hôm nay/ngày nay + tin/sự kiện", reader ưu tiên KB news_fact (độ mới theo ngày) TRƯỚC khi lôi Wikipedia cũ — cơ chế tổng quát theo cấu trúc thời gian+loại-thông-tin, KHÔNG regex mẫu câu (luật 14).
2. Tier reader cho statutory/canonical (documents 56 bản ghi, 46 canonical + 10 statutory — verify-domains đã map): câu hỏi pháp lý VN → tier statutory; generic → canonical. Đừng làm sai chế độ multi-domain của verify-domains-multinational.
3. Đèn chỉ báo: mỗi lần tier reader GIẮT nguồn (KB thay vì web) ⇒ trace ghi rõ nguồn đã dùng (để CHECKER đối chiếu độc lập).
4. Test đỏ-trước: câu "tin tức hôm nay về X" hiện trả Wikipedia cũ ⇒ sau fix phải ưu tiên KB news; mutant tắt reader ⇒ đỏ.

## Bất biến
Không đụng api_server/pipeline (Claude giữ knowledge-experts riêng tầng registry — file các cậu có thể giao nhau: AG đụng tier READER trong lookup, Claude đụng REGISTRY expert — tách file rõ). Chuỗi Tại Sao 5 mục. Done: `dichb-readers-done.md`.
