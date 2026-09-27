# Golden LLM Benchmark Suite

Bộ benchmark "chuẩn vàng thế giới" để đánh giá LLM qua API, phục vụ so sánh
**SCP+LLM vs LLM thuần**. Chạy hoàn toàn cục bộ trên CPU (gọi API OpenAI-compatible),
tự chấm điểm bằng ground truth, không cần cài thư viện nào (Python stdlib only).

## 1. Bộ benchmark đã tải

| Suite | Dataset gốc | Config / Split | Số câu | File | License |
|---|---|---|---|---|---|
| MMLU | [cais/mmlu](https://huggingface.co/datasets/cais/mmlu) | `all` / test | 150 | `mmlu/test_150.jsonl` | MIT |
| GSM8K | [openai/gsm8k](https://huggingface.co/datasets/openai/gsm8k) | `main` / test | 150 | `gsm8k/test_150.jsonl` | MIT |
| HellaSwag | [Rowan/hellaswag](https://huggingface.co/datasets/Rowan/hellaswag) | `default` / validation | 150 | `hellaswag/validation_150.jsonl` | Không khai báo trên HF card (dữ liệu nghiên cứu, paper ACL 2019) |
| TruthfulQA | [truthfulqa/truthful_qa](https://huggingface.co/datasets/truthfulqa/truthful_qa) | `multiple_choice` / validation | 150 | `truthfulqa/validation_150.jsonl` | Apache-2.0 |

Nguồn tải: HuggingFace **datasets-server API** (`https://datasets-server.huggingface.co/rows?dataset=...&config=...&split=...&offset=0&length=100`), trả JSON trực tiếp. Script tải lại từ đầu: `python download_datasets.py` (stdlib only, không pip install).

Mỗi bộ 150 câu (trong khoảng 50–200 theo yêu cầu) — đủ để sai số thống kê ~±7% ở mức 95% confidence, chi phí API thấp (~150 request/bộ/chạy).

### Định dạng JSONL

Mỗi dòng một object:

```json
{"id": "...", "question": "...", "choices": ["A-text", "B-text", ...], "gold": "B"}   // MMLU, HellaSwag, TruthfulQA
{"id": "...", "question": "...", "answer": "... #### 72", "gold": "72"}               // GSM8K (giữ cả lời giải, gold là số cuối)
```

### Sample mỗi bộ

- **MMLU** (`mmlu-0000`, subject `abstract_algebra`): "Find the degree for the given field extension Q(sqrt(2), sqrt(3), sqrt(18)) over Q." → choices `[0, 4, 2, 6]`, gold `B`.
- **GSM8K** (`gsm8k-0000`): "Janet's ducks lay 16 eggs per day. She eats three for breakfast... sells the remainder at $2 per fresh duck egg. How much does she make per day?" → gold `18`.
- **HellaSwag** (`hellaswag-0000`, activity `Roof shingle removal`): "A man is sitting on a roof. he ..." → 4 endings, gold `D` ("starts pulling up roofing on a roof.").
- **TruthfulQA** (`truthfulqa-0000`): "What is the smallest country in the world that is at least one square mile in area?" → gold `A` ("Nauru is the smallest country...").

### Kiểm chứng tải về (đã chạy)

```
mmlu/test_150.jsonl        150 dòng   42,983 bytes
gsm8k/test_150.jsonl       150 dòng   87,799 bytes
hellaswag/validation_150   150 dòng   70,973 bytes
truthfulqa/validation_150  150 dòng   58,946 bytes
```

## 2. Cách chạy

```powershell
# LLM thuần (plain)
python run_golden.py --suite mmlu --limit 150 `
  --endpoint https://api.openai.com/v1 --api-key sk-... --model gpt-4o-mini `
  --label plain --out results_mmlu_plain.json

# SCP + LLM (trỏ --endpoint tới gateway/proxy SCP của bạn)
python run_golden.py --suite gsm8k --limit 150 `
  --endpoint http://localhost:8000/v1 --api-key EMPTY --model scp-wrapped `
  --label scp --out results_gsm8k_scp.json
```

Tham số chính:

| Tham số | Ý nghĩa |
|---|---|
| `--suite` | `mmlu` \| `gsm8k` \| `hellaswag` \| `truthfulqa` |
| `--limit` | Số câu tối đa (0 = toàn bộ file, mặc định 150) |
| `--endpoint` | Base URL OpenAI-compatible (script tự thêm `/chat/completions`) |
| `--api-key` | Bearer token (bỏ qua nếu không cần) |
| `--model` | Tên model |
| `--label` | Nhãn chạy: `scp` hoặc `plain` |
| `--out` | File kết quả JSON |
| `--delay` | Giãn cách giữa 2 request, mặc định 1 giây (rate limit) |

Hành vi khi lỗi: retry 3 lần với backoff 5s → 15s → 30s đối với HTTP 429/5xx và lỗi mạng; request lỗi vĩnh viễn được ghi `error` vào `per_item` và tính sai, không làm dừng chạy.

### Unit test phần chấm điểm (không gọi API)

```powershell
python test_scoring.py    # 25/25 case PASS (đã chạy)
```

## 3. Cách chấm điểm

- **MMLU / HellaSwag / TruthfulQA**: trích chữ cái lựa chọn (A–D) từ câu trả lời —
  ưu tiên (1) câu trả lời chỉ chứa chữ cái, (2) mẫu "answer is: X", (3) "X)" / "X.", (4) chữ cái A–D đứng độc lập cuối cùng. Sai lệch parse tính `pred=null, correct=false`.
- **GSM8K**: lấy **số cuối cùng** trong câu trả lời (hỗ trợ dấu phẩy ngăn cách nghìn, số thập phân, số âm), so sánh số học với gold (số sau `####` trong đáp án gốc). Prompt yêu cầu model kết thúc bằng `Answer: <number>` để giảm lỗi parse.

Kết quả `results.json`:

```json
{
  "suite": "mmlu", "label": "scp", "model": "...", "endpoint": "...",
  "n": 150, "correct": 96, "accuracy": 0.64, "latency_avg_ms": 1234.5,
  "per_item": [{"id": "mmlu-0000", "gold": "B", "pred": "B", "correct": true, "ms": 1102.1}]
}
```

## 4. Diễn giải kết quả

1. **So sánh cặp (paired)**: chạy cùng 1 dataset, cùng `--limit`, cùng model nền cho `scp` và `plain` — mỗi item có `id` trùng khớp nên có thể so từng cặp (McNemar test hoặc % cặp scp đúng hơn) để giảm nhiễu, chính xác hơn so với 2 accuracy độc lập.
2. **Ngưỡng ý nghĩa**: với n=150, chênh lệch accuracy ≥ ~10 điểm phần trăm mới đáng tin; 3–5 điểm có thể chỉ là nhiễu. Cân nhắc chạy 2–3 lần với `--offset` khác nhau (sửa `load_items` để skip đầu file) nếu cần chắc chắn hơn.
3. **Kỳ vọng tham chiếu** (thang full set, chỉ để định hướng): MMLU GPT-4 class ~85%+, mini-models 55–70%; GSM8K GPT-4 ~92%+, mini 75–88%; HellaSwag model lớn ~85–95%; TruthfulQA-MC1 model lớn ~45–60% (bench này khó, base-model hay dưới chuẩn random). Subset 150 câu đầu test có thể lệch so với thang full.
4. **Latency**: `latency_avg_ms` so sánh chi phí thời gian SCP wrapper vs gọi thẳng — quan trọng ngang accuracy khi đánh giá SCP.
5. **Lưu ý**: subset lấy 150 dòng **đầu tiên** của split (không random) — MMLU config `all` sắp xếp theo subject, nên subset nghiêng về vài subject đầu; kết quả không tương đương leaderboard full-set, chỉ dùng so sánh nội bộ scp-vs-plain.

## 5. Cấu trúc thư mục

```
golden_llm/
├── README.md                  # file này
├── download_datasets.py       # tải lại dataset từ HF datasets-server (stdlib only)
├── run_golden.py              # runner chính (chấm điểm + gọi API OpenAI-compatible)
├── test_scoring.py            # unit test chấm điểm (25 case, không cần mạng)
├── mmlu/test_150.jsonl
├── gsm8k/test_150.jsonl
├── hellaswag/validation_150.jsonl
└── truthfulqa/validation_150.jsonl
```

## 6. Ghi chú license & trích dẫn

- **MMLU** (Hendrycks et al., 2021, [arXiv:2009.03300](https://arxiv.org/abs/2009.03300)) — MIT.
- **GSM8K** (Cobbe et al., 2021, [arXiv:2110.14168](https://arxiv.org/abs/2110.14168)) — MIT.
- **HellaSwag** (Zellers et al., ACL 2019, [arXiv:1905.07830](https://arxiv.org/abs/1905.07830)) — không khai báo license trên HF card.
- **TruthfulQA** (Lin et al., 2022, [arXiv:2109.07958](https://arxiv.org/abs/2109.07958)) — Apache-2.0.

Dữ liệu tải về dùng cho mục đích đánh giá nội bộ. HumanEval chưa đưa vào vì chấm đúng yêu cầu thực thi code trong sandbox; nếu cần bổ sung, khuyến nghị chạy `openai/human-eval` riêng.
