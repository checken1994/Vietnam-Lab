# Getting Started with SCP

SCP (SCP-OS) is an autonomous AI agent operating system built on top of LLM APIs.

## Installation

> SCP chưa phát hành package lên PyPI hay Docker Hub (`pip install scp-cli`,
> `docker pull scp/cli` là hướng dẫn cũ, sai). Cài từ repository:

### Windows — installer (khuyên dùng)
```bat
git clone <repo-url> scp
cd scp
install-scp.bat
```
`install-scp.bat` cài dependency, cài Bun nếu cần, tạo cấu hình `.env` an toàn
và kiểm tra boot configuration.

### Thủ công (Linux/macOS/WSL)
```bash
python -m pip install -r scp/requirements.txt
cp .env.example .env   # Windows: copy .env.example .env
```

### Docker (local compose)
```bash
docker compose up -d
```
`compose.yml` mặc định chỉ khởi động `scp-api` (port 8000, expose loopback-only
`127.0.0.1:8000`). Scheduler/loop là profile opt-in
(`docker compose --profile loop up`).

### Docker (observability stack)
```bash
docker compose -f docker-compose.observability.yml up -d
```

## Configuration

Copy `.env.example` to `.env` and fill in your API keys:
```env
OPENROUTER_API_KEY=your-key-here
OPENROUTER_MODEL=deepseek/deepseek-v4-flash-0731
OPENROUTER_MODEL_AUTO=0
```

## Quick Start

```bash
# Start the SCP API server
python -m scp

# Run a benchmark
python -m scp.benchmark.benchmark_suite

# Start observability stack (requires Docker)
docker compose -f docker-compose.observability.yml up -d
```

## Features
- Multi-provider LLM gateway with circuit breaker and failover
- Task kernel with checkpoint and auto-recovery
- HandsPlanner for step-by-step action execution
- OpenTelemetry observability (Grafana + Prometheus + Loki)
- Benchmark suite (MMLU, GSM-8K, Code-generation)
- Knowledge warehouse with FAISS embeddings