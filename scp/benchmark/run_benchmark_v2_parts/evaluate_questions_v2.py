# Auto-extracted from run_benchmark_v2.py
from __future__ import annotations
import argparse
import json
import os
import re
import sys
import time
import statistics
from pathlib import Path
from typing import Any
import requests

import logging
logger = logging.getLogger(__name__)


def _response_evidence_surface(data: dict) -> list[dict]:
    """[R1 2026-09-15] Nguồn đọc evidence cho D_evidence_recall / claim-B.

    CHỈ cập nhật NGUỒN ĐỌC (surface bằng chứng thật của response), KHÔNG đổi
    luật overlap trong ``compute_evidence_metrics`` (gold words length>3,
    ratio>0.5 — giữ nguyên). Nguồn đúng là:
      * ``slm_trace`` — surface chính: canonical_bm25 (F-2), lookup_data_api
        (fork, sau fix R1), expert entries, web-fallback;
      * fallback ``expert_trace`` — AskResponse khai slm_* là alias read-only
        của canonical vocabulary expert_*; nếu caller chỉ điền expert_trace,
        evidence vẫn phải được đo;
      * ``data_api_evidence`` — fork payload khi CÒN trong response dict
        (đường in-process; HTTP serialization drop field ngoài model): chỉ
        merge khi fork chưa tự surface ``lookup_data_api`` trong slm_trace.
    Không đọc ``final_answer``: đó là answer, không phải retrieved evidence —
    metric đếm bằng chứng được retrieve, không đếm sự tự khớp của answer
    (chống vòng lặp tự-duyệt ở tầng đo lường).
    """
    entries = data.get('slm_trace') or data.get('expert_trace')
    out = [e for e in (entries or []) if isinstance(e, dict)]
    fork_ev = data.get('data_api_evidence')
    if isinstance(fork_ev, str) and fork_ev.strip() and not any(
        e.get('slm_name') == 'lookup_data_api' for e in out
    ):
        out.append({'answer': fork_ev, 'source': 'data-api', 'slm_name': 'lookup_data_api'})
    return out


def evaluate_questions_v2(url: str, token: str, categories: list[str], inject_corrupted: bool=True, random_questions: list[dict] | None=None) -> list[dict]:
    """Evaluate questions with proper methodology.

    Args:
        url: SCP server URL
        token: auth token
        categories: question categories (used if random_questions is None)
        inject_corrupted: whether to inject corrupted_answer for self-correction test
        random_questions:  if provided, use these generated questions
                          instead of loading from JSONL files
    """
    results = []
    headers = {'Authorization': f'Bearer {token}'} if token else {}
    if random_questions is not None:
        by_cat: dict[str, list] = {}
        for q in random_questions:
            cat = q.get('category', 'unknown')
            by_cat.setdefault(cat, []).append(q)
        categories = sorted(by_cat.keys())
        all_questions_map = by_cat
    else:
        all_questions_map = None
    for cat in categories:
        if all_questions_map is not None:
            questions = all_questions_map.get(cat, [])
        else:
            q_file = BENCHMARK_DIR / QUESTION_CATEGORIES_V2.get(cat, f'questions_v2/{cat}_sample.jsonl')
            if not q_file.exists():
                print(f'  ⚠️  {q_file} not found, skipping {cat}')
                continue
            questions = []
            with open(q_file, encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        questions.append(json.loads(line))
        print(f'\n  [{cat}] {len(questions)} questions...')
        correct_count = 0
        for q in questions:
            q_id = q.get('id', '?')
            question = q.get('question', '')
            expected = q.get('expected_answer', '')
            answer_type = q.get('answer_type', 'string')
            answerable = q.get('answerable', True)
            gold_evidence = q.get('gold_evidence', [])
            corrupted = q.get('corrupted_answer', '')
            ai_answer = corrupted if inject_corrupted and corrupted and answerable else ''
            try:
                t0 = time.time()
                resp = requests.post(f'{url}/ask', json={'question': question, 'ai_answer': ai_answer, 'source': 'benchmark_v2'}, headers=headers, timeout=120)
                latency_ms = (time.time() - t0) * 1000
                if resp.status_code != 200:
                    results.append({'id': q_id, 'category': cat, 'question': question, 'expected_answer': expected, 'answer_type': answer_type, 'answerable': answerable, 'gold_evidence': gold_evidence, 'corrupted_answer': corrupted, 'ai_answer_injected': ai_answer, 'correct': False, 'error': f'HTTP {resp.status_code}', 'latency_ms': round(latency_ms, 2)})
                    continue
                data = resp.json()
                scp_answer = data.get('final_answer', '')
                verdict = data.get('verdict', '')
                is_correct, match_method = check_factual_correctness(scp_answer, expected, answer_type)
                if answerable and verdict != 'UNKNOWN':
                    if is_correct:
                        correct_count += 1
                claims = extract_claims_from_answer(scp_answer, question)
                scp_evidence = _response_evidence_surface(data)
                claim_analysis = compute_claim_hallucination(claims, gold_evidence, scp_evidence)
                evidence_metrics = compute_evidence_metrics(scp_evidence, gold_evidence)
                results.append({'id': q_id, 'category': cat, 'question': question, 'expected_answer': expected, 'answer_type': answer_type, 'answerable': answerable, 'gold_evidence': gold_evidence, 'corrupted_answer': corrupted, 'ai_answer_injected': ai_answer, 'correct': is_correct, 'match_method': match_method, 'verdict': verdict, 'confidence': data.get('confidence', 0), 'scp_answer': scp_answer[:500], 'latency_ms': round(latency_ms, 2), 'claim_analysis': claim_analysis, 'evidence_metrics': evidence_metrics, 'response': data})
            except requests.exceptions.Timeout:
                logger.debug('evaluate_questions_v2: requests.exceptions.Timeout ignored', exc_info=True)
                results.append({'id': q_id, 'category': cat, 'question': question, 'expected_answer': expected, 'answer_type': answer_type, 'answerable': answerable, 'gold_evidence': gold_evidence, 'corrupted_answer': corrupted, 'ai_answer_injected': ai_answer, 'correct': False, 'error': 'timeout', 'latency_ms': 120000})
            except Exception as e:
                logger.warning('evaluate_questions_v2: Exception not handled: %s', e)
                results.append({'id': q_id, 'category': cat, 'question': question, 'expected_answer': expected, 'answer_type': answer_type, 'answerable': answerable, 'gold_evidence': gold_evidence, 'corrupted_answer': corrupted, 'ai_answer_injected': ai_answer, 'correct': False, 'error': str(e)})
        denom = sum((1 for r in results if r.get('category') == cat and r.get('answerable')))
        acc = correct_count / denom if denom > 0 else 0
        print(f'  → [{cat}] Accuracy: {correct_count}/{denom} = {acc:.1%}' + (' (N/A — no answerable questions)' if denom == 0 else ''))
    return results
