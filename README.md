# SCP — Self-Correcting Pipeline

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**SCP** là một hệ thống thử nghiệm dành cho AI Agent, tập trung vào khả năng **thực thi có kiểm soát, tự kiểm tra, tự phát hiện sai và phục hồi an toàn**.

SCP không phải là một mô hình ngôn ngữ. Hệ thống đứng giữa AI và môi trường thực thi để quản lý task, quyền hành động, trạng thái bền vững, bằng chứng, xác minh độc lập và các vòng tự sửa có giới hạn.

Nguyên tắc cốt lõi:

> **Reality > Model**  
> Kết quả chỉ đáng tin khi có bằng chứng kiểm chứng được. Khi trạng thái không chắc chắn, hệ thống ưu tiên dừng hoặc chuyển sang kiểm tra thay vì tự đoán.

## SCP có gì?

- **TaskKernel** — quản lý vòng đời task, state machine, journal, lease, checkpoint và recovery.
- **Capability & Security** — giới hạn quyền theo hành động và trạng thái hệ thống; hỗ trợ revoke, sandbox và egress policy.
- **Independent Verification** — kiểm post-condition và evidence thay vì tin rằng agent tự báo “đã xong”.
- **Hands / Tool Runtime** — thực hiện các hành động có side effect qua lớp kiểm soát và ghi nhận trạng thái.
- **LLM Gateway** — hỗ trợ nhiều provider, timeout, circuit breaker và fallback có kiểm soát.
- **AutoFix / Self-Audit** — phát hiện vấn đề, kiểm evidence, đề xuất/sửa trong phạm vi giới hạn, verify và rollback khi cần.
- **Complete-SCP test architecture** — bộ kiểm thử T00–T11 đang được phát triển để kiểm cả sản phẩm lẫn chính test/verifier/auditor.

## Trạng thái đã kiểm chứng (2026-09-12)

> Tổng quan đầy đủ: [GA.md](GA.md) · Closure records: [reports/circuit-closures/](reports/circuit-closures/) · Witness: [reports/witness/](reports/witness/)

**Giới hạn đã đo (đọc trước khi đánh giá):** **1** node, loopback, **1** worker; answer-rate
**0.167** (bộ verify nghiêm ngặt — abstain thay vì đoán); LLM quota scarce (**429** measured);
**4** bug mới đã được witness báo cáo và đang theo dõi. Đây là **bằng chứng phạm vi hẹp** —
không phải tuyên bố production-ready.

**14/14 mạch kiến trúc đã đóng** với closure record D0–D8 + SHA pin riêng
([STATUS-LEDGER](reports/circuit-closures/STATUS-LEDGER.md)). Mimosa deep scan:
**HIGH 190 → 0** (medium 15 / low 98 — đã triage documented). Verification 6 lớp
độc lập (worker ≠ verifier, sealed scan, machine consistency).

**Witness độc lập (ngoài SCP lineage)** đã tự dựng real API cluster + real cloud
LLM và đo SCP bằng lưu lượng thật — [báo cáo đầy đủ](reports/witness/WITNESS-REPORT-W2-2026-09-11.md):

| Đo được | Kết quả |
|---|---|
| Golden chain (real LLM) | `/ask` → TaskKernel → gateway → real LLM → cross-verify 2 families → **PASS**, governance UPHOLD |
| Độ trung thực (N=30) | accuracy-answered **1.0** · hallucination **0** · 19 abstain-by-strict-verification |
| Chaos (fault injection thật) | kill -9 giữa traffic → breaker mở, recovery 21s · 429/500 storm được retry ladder hấp thụ |
| Soak 5.5 phút | **184,276 requests, zero error**, RSS +14MB (no leak) |

> SCP hiện vẫn đang được phát triển và kiểm định. Không nên coi trạng thái hiện tại là một hệ thống production đã hoàn thiện.

## Cách chạy

### Windows — cách đơn giản

Yêu cầu: **Python 3.12+** và **Node.js** đã có trong `PATH`.

```bat
install-scp.bat
```

Installer sẽ cài dependency, cài Bun nếu cần, tạo cấu hình `.env` an toàn và kiểm tra boot configuration.

Sau đó cấu hình provider LLM trong `.env` nếu cần rồi chạy:

```bat
start-scp.bat
```

### Chạy API thủ công

```bash
python -m pip install -r scp/requirements.txt
```

Tạo file cấu hình từ template:

**Windows:**

```bat
copy .env.example .env
```

**Linux/macOS:**

```bash
cp .env.example .env
```

Cấu hình ít nhất các secret bắt buộc và một LLM provider trong `.env` nếu muốn dùng các chức năng cần LLM.

Khởi động SCP:

```bash
python -m scp 8000
```

Mặc định API chạy trên:

```text
http://127.0.0.1:8000
```

Có thể đổi port:

```bash
python -m scp 8080
```

hoặc đặt `SCP_PORT` / `SCP_HOST` trong môi trường.

## Cấu hình LLM

SCP hỗ trợ OpenRouter hoặc API tương thích OpenAI. Ví dụ:

```env
# OpenRouter
OPENROUTER_API_KEY=...
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_MODEL=...

# Hoặc OpenAI-compatible API
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=...
```

Nếu bật external LLM access, hostname của provider phải được cho phép trong `SCP_LLM_EGRESS_ALLOWLIST`.

Xem đầy đủ cấu hình tại [`.env.example`](.env.example).

## Các hệ thống tương tự để tham khảo

SCP là dự án độc lập, không phải fork của các dự án dưới đây. Tuy nhiên, một số hướng thiết kế có thể được so sánh hoặc tham khảo với các hệ thống trong cùng lĩnh vực AI Agent/runtime:

- **[OpenHands](https://github.com/OpenHands/OpenHands)** — nền tảng agent cho software engineering, chú trọng runtime, sandbox và khả năng thực hiện tác vụ phát triển phần mềm.
- **[AutoGPT](https://github.com/Significant-Gravitas/AutoGPT)** — hệ sinh thái xây dựng, triển khai và vận hành AI agents/workflows tự động.
- **[LangGraph](https://github.com/langchain-ai/langgraph)** — framework cho agent có trạng thái, durable execution, persistence và human-in-the-loop.

Điểm SCP tập trung mạnh hơn là kết hợp **Reality verification, fail-closed behavior, durable task state, capability boundaries và vòng self-audit/self-correction** thành một hệ thống kiểm soát thống nhất.

## Trạng thái dự án

SCP đang trong giai đoạn **nghiên cứu, phát triển và kiểm định kiến trúc**. Các capability và test được thay đổi thường xuyên; một SHA chỉ đại diện cho trạng thái của repository tại thời điểm kiểm chứng.

## License

MIT License.

---

**Minh Nguyen Van**  
Email: **checken1994@gmail.com**
