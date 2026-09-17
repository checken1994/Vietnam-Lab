# SCP — Agent Control Plane

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**SCP** là một prototype control-plane cho AI agent: hệ thống đứng giữa mô hình ngôn ngữ và môi trường thực thi để quản lý task, quyền hạn, trạng thái bền vững, bằng chứng và xác minh độc lập. SCP không phải là một mô hình ngôn ngữ.

> Nguyên tắc cốt lõi: **Reality > Model** — kết quả chỉ đáng tin khi có bằng chứng kiểm chứng được. Khi không chắc chắn, hệ thống dừng hoặc chuyển human review thay vì tự đoán.

## Trạng thái (2026-09-17)

- **Prototype nghiên cứu** — chạy API-only trên `:8000`, chưa phải sản phẩm end-user, chưa production-ready.
- Chiến dịch bảo mật 2026-09-16/17 đã đóng trên nhánh `audit/runtime-guard-AUDIT-20260909`: SEC-A (per-hop redirect revalidation tại egress choke — chặn SSRF, scrub credential), SEC-B (token boundary GitHub/HF), A3 (HF backup fail-closed), SMTP guard, secret-env scrub trong test. Mỗi mục có verifier độc lập + evidence gắn đúng SHA.
- Finding Mimosa đang chặn AI-commit đã được adjudicate là false positive (docstring test — hồ sơ: [reports/expert-panel/COMMITMSG-REAUDIT.md](reports/expert-panel/COMMITMSG-REAUDIT.md)).
- Còn mở: policy unset-mode cho egress PEP, một số test-gap nhỏ, benchmark e2e — danh sách đầy đủ tại [GA.md](GA.md) §B13.

## Có gì trong hệ thống

- **TaskKernel** — state machine, lease, journal, checkpoint, recovery.
- **Egress choke** ([`scp/security/url_safety.py`](scp/security/url_safety.py)) — mọi HTTP outbound đi qua policy; redirect được re-validate từng hop; external write (HF backup, SMTP) fail-closed theo allowlist.
- **LLM Gateway** — multi-provider, hedge, circuit breaker, fail-closed.
- **Ask pipeline** — retrieval (BM25) → judge → verify → withhold; self-correction có giới hạn (Reflection).
- **AutoFix / Self-Audit** — sửa trong phạm vi cho phép, verify và rollback.
- **Kiểm thử** — bộ test T00–T11 (~2.000 item) kèm meta-audit chống gian lận test (FA-01→FA-07).

## Chạy

### Windows — nhanh

```bat
install-scp.bat
start-scp.bat
```

### Thủ công

```bash
python -m pip install -r requirements.txt
copy .env.example .env    # Linux/macOS: cp .env.example .env
python -m scp 8000        # API tại http://127.0.0.1:8000
python -m pytest tests/ -q
```

Cấu hình secret bắt buộc và LLM provider (OpenRouter / OpenAI-compatible) trong `.env` — xem [`.env.example`](.env.example). Nếu bật external LLM, hostname provider phải có trong `SCP_LLM_EGRESS_ALLOWLIST`. Đặc tả field của `/ask`: [docs/api/ask-request-fields.md](docs/api/ask-request-fields.md).

## Quy trình phát triển

Mọi thay đổi đi qua quy trình **worker → verifier độc lập** với same-SHA evidence (xem [.agents/EXECUTION_PROTOCOL.md](.agents/EXECUTION_PROTOCOL.md)). Không tuyên bố "xong/an toàn" nếu không có bằng chứng. Session authority + handoff: [GA.md](GA.md).

## License

MIT License — **Minh Nguyen Van** (checken1994@gmail.com)
