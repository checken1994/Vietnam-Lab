# SCP — Self-Correcting Pipeline

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**SCP** là một nền tảng nghiên cứu và thử nghiệm cho **AI Agent có kiểm soát**, tập trung vào bốn việc: **thực thi có giới hạn, kiểm chứng bằng Reality, duy trì bằng chứng/trạng thái bền vững, và tự phát hiện–sửa sai mà không tự phong kết quả là đúng**.

SCP **không phải một mô hình ngôn ngữ**. Nó là lớp kiểm soát nằm giữa model/agent và môi trường thực thi: quản lý task, capability, sandbox/egress, provider LLM, evidence, verifier, knowledge, governance, recovery và các vòng self-audit/self-correction.

Nguyên tắc trung tâm:

> **Reality > Model**  
> `PASS != TRUE` · `UNKNOWN != VERIFIED` · confidence/consensus không thay thế bằng chứng.  
> Khi thiếu authority hoặc evidence quan trọng, SCP ưu tiên **fail-closed, dừng, rollback, hoặc chuyển sang kiểm tra** thay vì tự đoán.

## SCP hiện có gì?

Trên `main`, hệ thống đã có một tập hợp lớn các subsystem và control boundary, trong đó có:

- **TaskKernel / Durable Execution** — state machine, journal, lease, checkpoint, idempotency, recovery và fencing cho worker cũ/stale.
- **Capability & Security Control** — giới hạn quyền hành động, revoke, sandbox, egress policy, secret/privacy boundary và các policy fail-closed.
- **Independent Verification / Evidence** — post-condition, provenance, evidence store, lineage, same-SHA binding và các cơ chế tách “agent nói đã xong” khỏi “Reality chứng minh đã xong”.
- **Hands / Tool Runtime** — thực thi side effect qua lớp kiểm soát thay vì để model gọi hành động trực tiếp.
- **LLM Gateway** — nhiều provider, timeout/circuit-breaker/failover, privacy policy và các lớp kiểm soát outbound.
- **Knowledge & Epistemic Control** — ontology, claim/evidence, lineage, contradiction, calibration, self-model, question/hypothesis/experiment/lesson/benchmark authority.
- **AutoFix / Self-Audit** — phát hiện vấn đề, tạo thay đổi có giới hạn, kiểm policy, snapshot, verify và rollback.
- **Autonomous Mode (bounded)** — `main` đã tích hợp vòng autonomous mới với bounded tools, egress policy, provenance ledger và cluster/E2E verifier. Đây vẫn là autonomy **bị governance và capability boundary giới hạn**, không phải quyền tự trị không hạn chế.
- **Test architecture T00–T12** — integrity, boot, contract, capability, kernel, gateway, verifier, learning, runtime, golden task, recovery, release và unified-chatbot coverage. Việc một test tồn tại **không tự động** có nghĩa capability đã đạt Reality level C/D hoặc production-ready.

## Trạng thái hiện tại

**Nguồn trạng thái chính thức là nhánh `main`.** Một SHA chỉ là định danh kỹ thuật của snapshot/evidence; không phải tên phiên bản “hoàn thiện” của SCP.

Tại lần cập nhật README này, `main` đang ở snapshot:

```text
7344c1e85d7ffa2880d476f1d304798773fdd020
```

Commit này merge **Autonomous Mode v2** vào `main` ngày **22/09/2026**.

### Điều có thể khẳng định

- SCP hiện là một codebase lớn, đa subsystem, có backend Python/FastAPI, dashboard web và microservice TypeScript/Bun.
- `main` đã chứa các authority/control plane về execution, security, evidence, epistemic/knowledge, governance, self-audit và autonomous execution có giới hạn.
- Repository có hệ thống target/spec/test/evidence riêng để phân biệt **implementation tồn tại** với **capability đã được chứng minh**.
- Các campaign trước đã tạo closure records và witness evidence riêng theo từng snapshot. Những evidence đó vẫn có giá trị **trong đúng scope/SHA của chúng**, không tự động kế thừa sang HEAD mới.

### Điều chưa được tuyên bố

- **Không tuyên bố SCP đã production-ready.**
- **Không tuyên bố toàn bộ Complete SCP đã hoàn thiện.**
- **Không dùng số lượng test, class/module tồn tại hay model confidence để thay cho Reality proof.**
- HEAD hiện tại chỉ được coi là release/verified khi các mandatory gate có **same-SHA evidence** và blocker bằng 0. Nếu chưa có evidence đó, trạng thái đúng là `UNKNOWN/BLOCKED`, không phải “PASS theo suy đoán”.

> Lưu ý: GitHub Actions/CI hiện không phát sinh status mới vì tài khoản duy trì repository đang bị giới hạn **billing/credit**. Đây là giới hạn hạ tầng CI, không phải bằng chứng rằng HEAD đã PASS hay FAIL; vì vậy README không gắn nhãn release PASS cho `7344c1e8…`.

## Evidence và lịch sử kiểm chứng

Các kết quả kiểm chứng trước đây được giữ làm evidence lịch sử, không dùng để quảng cáo HEAD mới như thể chúng vừa được chạy lại:

- [GA.md](GA.md) — session authority, engineering invariants và handoff kỹ thuật.
- [reports/circuit-closures/](reports/circuit-closures/) — closure records theo scope/SHA.
- [reports/witness/](reports/witness/) — witness reports độc lập.

Một witness campaign trước đã đo real API/real LLM, chaos và soak test trên snapshot riêng. Đây là **bằng chứng phạm vi hẹp của snapshot đó**, không phải chứng nhận production cho `main` hiện tại.

## Kiến trúc tư tưởng

SCP đi theo chuỗi authority:

```text
External Data
    ↓
Evidence
    ↓
Epistemic Assessment
    ↓
Knowledge / Reasoning
    ↓
Proposal
    ↓
Governance / Capability / Risk
    ↓
Execution
    ↓
Reality
    ↓
Evidence / Learning / Audit
    ↺
```

Không có shortcut hợp lệ kiểu:

```text
LLM answer → TRUE
Internet text → Knowledge
Knowledge → direct action
Generated test PASS → VERIFIED
```

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

Cấu hình các secret/provider cần thiết trong `.env`, sau đó khởi động:

```bash
python -m scp 8000
```

Mặc định API chạy tại:

```text
http://127.0.0.1:8000
```

Có thể đổi port:

```bash
python -m scp 8080
```

hoặc đặt `SCP_PORT` / `SCP_HOST` trong môi trường.

## Cấu hình LLM

SCP hỗ trợ OpenRouter và các API tương thích OpenAI. Ví dụ:

```env
OPENROUTER_API_KEY=...
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_MODEL=...

OPENAI_API_KEY=...
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=...
```

Nếu bật external LLM access, provider còn phải thỏa các policy egress/privacy/cost tương ứng. Xem cấu hình đầy đủ tại [`.env.example`](.env.example).

## Các hệ thống liên quan để tham khảo

SCP là dự án độc lập, không phải fork của các dự án dưới đây:

- **[OpenHands](https://github.com/OpenHands/OpenHands)** — agent/software-engineering runtime và sandbox.
- **[AutoGPT](https://github.com/Significant-Gravitas/AutoGPT)** — hệ sinh thái agent/workflow tự động.
- **[LangGraph](https://github.com/langchain-ai/langgraph)** — stateful/durable agent workflows và human-in-the-loop.

SCP tập trung vào việc ghép **durable execution + capability/security boundaries + evidence/Reality verification + epistemic control + governance + bounded self-correction** thành một control plane thống nhất.

## Trạng thái dự án

SCP đang trong giai đoạn **nghiên cứu, phát triển và kiểm định kiến trúc ở quy mô lớn**. `main` là nguồn code/trạng thái phối hợp chính thức, nhưng **main ≠ production certification**. Capability hoặc release claim chỉ hợp lệ trong scope mà evidence thực sự chứng minh.

## License

MIT License.

---

**Minh Nguyen Van**  
Email: **checken1994@gmail.com**
