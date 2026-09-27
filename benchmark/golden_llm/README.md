# Golden LLM Benchmarks — SCP vs plain LLM

Bộ bài kiểm tra chuẩn vàng quốc tế để so sánh **SCP+LLM** với **LLM thuần** trên cùng đề.

## Suites (100 câu/bộ, tải từ HuggingFace datasets-server)

| Suite | File | Nguồn | License | Chấm điểm |
|---|---|---|---|---|
| MMLU | `mmlu/mmlu_100.jsonl` | cais/mmlu (config=all, test) | MIT | Khớp chữ cái A–D |
| GSM8K | `gsm8k/gsm8k_100.jsonl` | openai/gsm8k (main, test) | MIT | Số cuối cùng (regex, chịu phẩy ngăn cách) |
| HellaSwag | `hellaswag/hellaswag_100.jsonl` | Rowan/hellaswag (validation) | MIT | Khớp chữ cái A–D |
| TruthfulQA | `truthfulqa/truthfulqa_100.jsonl` | truthfulqa/truthful_qa (mc1, validation) | Apache-2.0 | Khớp chữ cái A–D |

`manifest.json` ghi nguồn + số câu + đường dẫn từng bộ.

## Cách chạy

```powershell
# LLM thuần (gọi thẳng provider)
python run_golden.py --suite mmlu --limit 100 `
  --endpoint https://openrouter.ai/api/v1 --api-key <OPENROUTER_KEY> `
  --model openai/gpt-4o-mini --label plain --out results_mmlu_plain.json

# Qua SCP (cùng câu hỏi, đi qua pipeline /ask: governance + verification)
python run_golden.py --suite mmlu --limit 100 `
  --endpoint http://127.0.0.1:8000/v1 --api-key <SCP_ADMIN_KEY> `
  --model <scp-model> --label scp --out results_mmlu_scp.json
```

Lưu ý:
- `--rpm` mặc định 30 request/phút, tự retry 429/5xx (backoff 5/15/30s).
- Câu bị SCP từ chối (withheld/abstain) tính **sai** — chấm conservative fail-closed.

## Diễn giải kết quả

`results.json` chứa `accuracy`, `latency_avg_ms`, và `per_item` (id, gold, pred, correct, answer_head). So sánh `accuracy` giữa label `plain` và `scp` trên **cùng suite, cùng model**: SCP hoàn thành nhiệm vụ nếu `accuracy_scp >= accuracy_plain − ε` (ε nhỏ, ví dụ 0.02) **kèm** bằng chứng governance từ trace ledger; ngược lại mọi case SCP sai nhiều hơn phải trace được lý do (withheld vì fail-closed là hành vi đúng, hallucination là hành vi sai).
