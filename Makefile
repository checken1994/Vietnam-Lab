.PHONY: audit test reality fitness install clean

# SCP Unified Commands — 1 lệnh cho auditor ngoài
# make audit = chạy TOÀN BỘ bằng chứng và xuất JSON report

audit:
	python scripts/run_full_audit.py

# Chạy riêng từng tầng
test:
	python -m pytest -q --tb=short

reality:
	python scripts/run_reality_tests_portable.py

fitness:
	python -c "from scp.core.fitness_engine import run_and_gate; import json; r = run_and_gate(); print(json.dumps(r, indent=2, ensure_ascii=False))"

benchmark:
	python benchmark/run_world_exam.py benchmark/gsm8k_sample_10.jsonl
	python benchmark/grader.py raw_results.jsonl --dataset gsm8k

install:
	pip install -r requirements.txt

clean:
	rm -rf reports/pytest-basetemp reports/audit data-test/pytest-tmp __pycache__ .pytest_cache
