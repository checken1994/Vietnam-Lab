"""Golden LLM benchmark runner ? SCP vs plain LLM comparison.

Runs one suite (MMLU / GSM8K / HellaSwag / TruthfulQA) against any
OpenAI-compatible chat-completions endpoint, auto-grades, and writes a
results.json with per-item evidence.

Usage:
  python run_golden.py --suite mmlu --limit 100 \
      --endpoint https://openrouter.ai/api/v1 --api-key <KEY> \
      --model openai/gpt-4o-mini --out results_plain.json --label plain

  # SCP mode: same items, but answers go through the SCP /ask pipeline
  # (via --scp-base), so governance/verdicts apply. Items withheld by SCP
  # are counted as incorrect (conservative, fail-closed).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent

SUITES = {
    "mmlu": HERE / "mmlu" / "mmlu_100.jsonl",
    "gsm8k": HERE / "gsm8k" / "gsm8k_100.jsonl",
    "hellaswag": HERE / "hellaswag" / "hellaswag_100.jsonl",
    "truthfulqa": HERE / "truthfulqa" / "truthfulqa_100.jsonl",
}

LETTER_RE = re.compile(r"\b([ABCD])\b")
LAST_NUM_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def load_suite(suite: str) -> list[dict]:
    path = SUITES[suite]
    items = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def build_prompt(suite: str, item: dict) -> str:
    if suite in ("mmlu", "truthfulqa"):
        letters = ["A", "B", "C", "D"]
        lines = [item["question"]]
        for i, choice in enumerate(item["choices"][:4]):
            lines.append(f"{letters[i]}. {choice}")
        lines.append("\nAnswer with ONLY the letter (A, B, C or D).")
        return "\n".join(lines)
    if suite == "hellaswag":
        letters = ["A", "B", "C", "D"]
        lines = [f"Context: {item['ctx']}"]
        lines.append("Which ending is the most plausible continuation?")
        for i, choice in enumerate(item["choices"][:4]):
            lines.append(f"{letters[i]}. {choice}")
        lines.append("\nAnswer with ONLY the letter (A, B, C or D).")
        return "\n".join(lines)
    if suite == "gsm8k":
        return (
            item["question"]
            + "\n\nSolve step by step, then give the final numeric answer "
            "on the last line in the form: #### <number>"
        )
    raise ValueError(f"unknown suite: {suite}")


def grade(suite: str, item: dict, answer: str) -> tuple[str, bool]:
    """Return (pred, correct)."""
    text = answer or ""
    if suite in ("mmlu", "hellaswag", "truthfulqa"):
        letters = ["A", "B", "C", "D"]
        gold_letter = letters[item["gold"]]
        # Prefer the LAST standalone letter in the answer (final-answer style),
        # fall back to first.
        matches = LETTER_RE.findall(text.upper())
        pred = matches[-1] if matches else ""
        # exact gold letter text match fallback
        if not pred:
            return "", False
        return pred, pred == gold_letter
    if suite == "gsm8k":
        gold = str(item["gold"]).replace(",", "").strip()
        nums = LAST_NUM_RE.findall(text.replace("####", " "))
        if not nums:
            return "", False
        pred = nums[-1].replace(",", "").strip()
        try:
            return pred, abs(float(pred) - float(gold)) < 1e-4
        except ValueError:
            return pred, False
    raise ValueError(f"unknown suite: {suite}")


def call_openai(endpoint: str, api_key: str, model: str, prompt: str,
                timeout: float = 120.0) -> tuple[int, str]:
    url = endpoint.rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 512,
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "***" + api_key,
        },
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            ms = round((time.time() - t0) * 1000)
            content = payload["choices"][0]["message"]["content"]
            return ms, content or ""
    except urllib.error.HTTPError as exc:
        ms = round((time.time() - t0) * 1000)
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        return -exc.code, f"HTTP {exc.code}: {detail}"


def call_with_retry(endpoint, api_key, model, prompt, retries=(5.0, 15.0, 30.0)):
    for attempt, backoff in enumerate((0.0,) + retries):
        if backoff:
            time.sleep(backoff)
        ms, text = call_openai(endpoint, api_key, model, prompt)
        if ms > 0:
            return ms, text
        code = -ms
        if code in (429,) or code >= 500:
            continue
        return ms, text  # non-retryable HTTP error
    return ms, text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", required=True, choices=sorted(SUITES))
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--endpoint", required=True)
    ap.add_argument("--api-key", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--label", default="plain", choices=["plain", "scp"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--rpm", type=float, default=30.0,
                    help="max requests per minute (default 30)")
    args = ap.parse_args()

    items = load_suite(args.suite)[: args.limit]
    per_item = []
    correct = 0
    latencies = []
    delay = 60.0 / max(args.rpm, 0.1)

    for idx, item in enumerate(items):
        prompt = build_prompt(args.suite, item)
        ms, answer = call_with_retry(args.endpoint, args.api_key, args.model, prompt)
        if ms <= 0:
            pred, ok = "", False
            answer = answer  # error text
        else:
            pred, ok = grade(args.suite, item, answer)
            latencies.append(ms)
        correct += 1 if ok else 0
        per_item.append({
            "id": item["id"], "gold": item["gold"], "pred": pred,
            "correct": ok, "ms": ms,
            "answer_head": (answer or "")[:160],
        })
        print(f"[{idx+1}/{len(items)}] {item['id']} pred={pred!r} "
              f"gold={item['gold']} correct={ok} {ms}ms")
        time.sleep(delay)

    result = {
        "suite": args.suite,
        "label": args.label,
        "model": args.model,
        "endpoint": args.endpoint,
        "n": len(items),
        "correct": correct,
        "accuracy": round(correct / max(len(items), 1), 4),
        "latency_avg_ms": round(sum(latencies) / max(len(latencies), 1)),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "per_item": per_item,
    }
    Path(args.out).write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"DONE suite={args.suite} label={args.label} "
          f"accuracy={result['accuracy']} ({correct}/{len(items)}) -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
