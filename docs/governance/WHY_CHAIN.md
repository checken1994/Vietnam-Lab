# Chuỗi Tại Sao - hướng dẫn cho hội đồng (commit + done file)

Hiệu lực: mọi commit KHÔNG phải tổ tiên của `epoch_sha` trong `spec/why_chain_policy.json`, và mọi done file
đưa cho CHECKER. Thiếu hoặc rỗng chuỗi = bị chặn (commit-msg hook, CI, CHECKER).

## Luật nhận thức của owner (ràng buộc)

- Chuỗi Tại Sao là **đèn soi**, không phải **trọng tài**. Mỗi mắt xích chỉ là **GIẢ THUYẾT** cho tới khi Reality kiểm chứng độc lập.
- **Độ sâu chuỗi KHÔNG phải độ tin** (5 mắt xích x 0.9 = ~59%). Vì vậy checker không thưởng độ dài/độ sâu; không có tối đa, không có điểm cộng.
- Nguyên nhân là **mạng**, không phải chuỗi: phải xét nhánh song song và nói đã loại trừ bằng gì.
- `UNKNOWN_WITH_REASON` là **đầu ra danh dự**. Ghi "không biết, vì ..." (có lý do) luôn được floor chấp nhận cho bất kỳ phần nào.
- CHECKER chấm theo **bằng chứng từng mắt xích**, không chấm theo độ mượt của văn.

## Mẫu (copy-paste, thay phần trong `<...>`)

```text
Chuỗi Tại Sao:
1. GỐC: <nguyên nhân gốc, không dừng ở triệu chứng>. VERIFIED: <con trỏ: lệnh/output/file/test id/SHA> | HYPOTHESIS: <chưa kiểm>. Nguyên nhân song song đã xét: <A, B>; loại trừ bằng <bằng chứng> (hoặc UNKNOWN_WITH_REASON: <vì sao chưa loại trừ được>).
2. SỬA-ĐÚNG: VERIFIED: <`lệnh chạy lại`, số liệu đỏ-trước/xanh-sau, đường dẫn test, SHA> (HYPOTHESIS một mình KHÔNG phải bằng chứng; nếu chưa có thì ghi UNKNOWN_WITH_REASON: <lý do>).
3. TIN-BẰNG-CHỨNG: <nguồn/lineage độc lập với người sửa: ai/cái gì tái chạy>. VERIFIED: <con trỏ> hoặc HYPOTHESIS: <chưa kiểm độc lập>.
4. ĐỔI-QUYẾT-ĐỊNH: <quan sát cụ thể nào sẽ làm đảo/rút quyết định này>.
5. UNKNOWN_WITH_REASON: <chỗ nào còn chưa biết, và VÌ SAO chưa biết>.
```

Quy ước nhãn (viết HOA, giữ nguyên identifier):

| Nhãn | Nghĩa | Floor kiểm |
|---|---|---|
| `VERIFIED:` | Reality đã kiểm | phải kèm con trỏ cụ thể (số, `lệnh`, đường dẫn, `::` test id, sha) |
| `HYPOTHESIS:` | chưa kiểm độc lập | phải có nhãn; phần 2 không được chỉ có HYPOTHESIS |
| `UNKNOWN_WITH_REASON:` | không biết, có lý do | phải có lý do (>= 3 từ thực); ngắn vẫn hợp lệ |

Phần 1, 2, 3 bắt buộc có ít nhất 1 nhãn; phần 2 cần `VERIFIED` có con trỏ **hoặc** `UNKNOWN_WITH_REASON` có lý do;
phần 1 phải nhắc "song song" hoặc "loại trừ" (nhánh nguyên nhân đã xét).

## Định dạng

- Commit message: dòng tiêu đề riêng `Chuỗi Tại Sao:` (KHÔNG bắt đầu bằng `#`, git xoá dòng `#` khi sửa bằng editor), rồi 5 phần
  `1.`..`5.` mỗi phần một đoạn (ngắt đoạn bằng dòng trống hoặc nhãn kế tiếp). Trailer `Co-Authored-By:` để cuối.
- Done file `.md`: tiêu đề `## Chuỗi Tại Sao` (hoặc `Chuỗi Tại Sao:`), 5 phần như trên. Tiêu đề `#` kế tiếp kết thúc mục.
- Nhãn phần chấp nhận không dấu / có dấu (`GỐC`/`GOC`, `SỬA-ĐÚNG`/`SUA DUNG`, `TIN-BẰNG-CHỨNG`, `ĐỔI-QUYẾT-ĐỊNH`, `UNKNOWN_WITH_REASON`).

## Phạm vi và ngoại lệ (quyết định: nghiêm)

- Áp dụng cho **mọi** commit sau `epoch_sha`. Lịch sử trước epoch được miễn (không có chuỗi để kiểm).
- Ngoại lệ DUY NHẤT: merge commit **do git sinh** (tiêu đề mặc định `Merge branch/pull request ...`), 2 parent, và cây của nó
  **đúng bằng** `git merge-tree` của 2 parent (không giải xung đột tay, không sửa thêm). Lý do: các commit được gộp đã nằm trong
  range và đã bị kiểm từng cái.
- KHÔNG miễn: merge viết tay (ví dụ `merge(ask): ...`), revert, fixup/squash, commit của bot (dependabot), octopus merge.
  Merge viết tay phải mang chuỗi riêng (WHY của việc gộp). Ngoại lệ mới = đổi `tools/check_why_chain.py` + test, cần người duyệt (L4).
- Nâng `epoch_sha` (khi merge lane này vào main) là thay đổi `spec/` được bảo vệ, do người duyệt.

## Floor KHÔNG làm được gì

Checker chỉ kiểm hình thức: có mục, đủ 5 phần, không rỗng/placeholder/template, có nhãn, `VERIFIED` có con trỏ,
không sao chép giữa các phần / từ tiêu đề / từ commit trước. Nó **không** biết văn bản có ĐÚNG không, gốc có thật là gốc không,
con trỏ có tồn tại không, "độc lập" có thật độc lập không. Một lời nói dối viết đúng khuôn vẫn qua. CHECKER agent phải mở từng
con trỏ và chấm từng mắt xích (DNA #22: PASS != TRUE).

## Chạy tay

```bash
python tools/check_why_chain.py path/to/done.md [--against-dir C:/Users/check/.council-loop/inbox]
python tools/check_why_chain.py --commit-msg .git/COMMIT_EDITMSG
python tools/check_why_chain.py --range HEAD --base origin/main
python tools/install_git_hooks.py   # cài pre-commit + commit-msg
```
