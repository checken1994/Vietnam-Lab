# TASK AG (xếp HÀNG sau task hiện tại): BUYER-02/INST-01 — installer nuốt lỗi smoke

(Giao 04:1x. Nguồn: báo cáo buyer ngoài trên main 05e0e328; hub đã đối chiếu code thật.)

## Bằng chứng (hub đọc file, có thật)
`setup-scp.sh` ~dòng 261-263:
```
"$VENV_PYTHON" tools/run_bounded_system_smoke.py || true
echo -e "  ✅ System integrity verified"
```
Smoke FAIL (RuntimeError bounded system smoke checks failed — đang FAIL thật trên main do BUYER-01) nhưng installer in "System integrity verified" + "INSTALLATION COMPLETE" + exit 0.

## Việc
1. Bỏ `|| true`; truyền exit code smoke ra ngoài: smoke fail ⇒ installer exit != 0, KHÔNG in "verified"/"COMPLETE".
2. In thông báo FAIL rõ ràng (mục nào đỏ, lệnh chạy lại để verify).
3. Test: chạy `setup-scp.sh --api-only --no-start` trên main — phải THẤT BẠI exit != 0 (vì smoke đang đỏ thật). Sau khi BUYER-01 xanh (Claude làm), chạy lại phải exit 0. Đây là cặp test đỏ→xinh tự nhiên.
4. Harness-fix phải tăng nghiêm ngặt (AGENTS.md: không làm xanh bằng hạ chuẩn).

## Đầu ra
- File inbox: `fe2e-installer-done.md` — diff, bằng chứng exit code trước/sau, giới hạn.
- Guard CHECKER verify; REJECTED = làm lại theo feedback.
