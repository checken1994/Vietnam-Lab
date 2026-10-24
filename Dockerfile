# SCP API container
# Runtime and audit/test runner use the repository's version-pinned transitive lock.

FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS runtime

WORKDIR /app

# Copy dependency and cryptographic hash pinning files first for layer caching
COPY scp/requirements.hashes.txt ./

# Deterministic fail-closed installation with cryptographic SHA256 hash validation (DEP-03 / SLSA Level 2)
RUN pip install --no-cache-dir --require-hashes -r requirements.hashes.txt

# Copy runtime source code and specifications
COPY scp/ ./scp/
COPY spec/ ./spec/
# DoubtCron fitness_drift check reads the frozen golden suite at runtime
COPY tests/golden/ ./tests/golden/
# [EXTERNAL-TRUST fix · 2026-10-23] scp/meta/external_trust.py yêu cầu anchor
# .github/workflows/ + tests/external_audit/ tồn tại trên đĩa; thiếu chúng
# startup log cảnh báo "External trust BROKEN" (scp/api_server_parts/lifespan.py).
COPY tests/external_audit/ ./tests/external_audit/
COPY .github/ ./.github/
COPY README* ./

# Environment variable placeholders (override at runtime)
ENV OPENROUTER_API_KEY=""
ENV OPENROUTER_MODEL="deepseek/deepseek-v4-flash-0731"
ENV OPENROUTER_MODEL_AUTO="0"
ENV SCP_FALLBACK_WATCH_INTERVAL="21600"
ENV SCP_RETRY_TIMEOUT_SEC="300"
ENV SCP_KW_ENABLE="0"

# Bind the image to its source repository and commit for traceability.
ARG SCP_GIT_SHA=unknown
LABEL org.opencontainers.image.source="https://github.com/checken1994/Vietnam-Lab" \
      org.opencontainers.image.title="SCP — Self-Correcting Pipeline" \
      org.opencontainers.image.description="Experimental SCP API/runtime candidate; not a production-readiness claim." \
      org.opencontainers.image.revision="${SCP_GIT_SHA}"
ENV SCP_GIT_SHA=${SCP_GIT_SHA}

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

RUN useradd -u 10001 -m scpuser && \
    mkdir -p /app/data && \
    chown -R scpuser:scpuser /app/data
USER 10001

ENTRYPOINT ["python", "-m", "scp"]
CMD ["8000"]

# [D-W1 fix · 2026-10-23] Stage node-bin: chỉ làm nguồn binary node (glibc,
# cùng base Debian bookworm với python:3.12-slim) để COPY vào stage audit.
# KHÔNG dùng node:20-alpine: binary alpine là musl-dynamic
# (/lib/ld-musl-x86_64.so.1) ⇒ không chạy được trên slim glibc — đã verify ldd.
# [D-W1 fix 2] node:24 (LTS) thay node:20: T02/integration chạy node với
# --experimental-strip-types (cần ≥22.6); host dùng node v26. Digest pin DEP-03.
FROM node:24-bookworm-slim@sha256:55fb5f5a527ea2c5c4a95f963e48ef90aa38c7b346054fdc8ee9957a2b55d205 AS node-bin

# Audit stage: for running verification & audit scripts in test profile
FROM runtime AS audit
USER root
# [F-01 fix · 2026-10-23] run_full_audit.py step 7 chạy pytest trong container,
# nhưng stage audit trước đây không có pytest lẫn tests/ ⇒ battery thiết kế
# không thể thực thi. Bổ sung pytest hash-pinned (chuẩn DEP-03 như runtime deps)
# + tests/ + tools/ + pytest.ini để đường audit chuẩn chạy được.
COPY requirements-dev.hashes.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements-dev.hashes.txt
COPY scripts/ ./scripts/
COPY tests/ ./tests/
COPY tools/ ./tools/
COPY pytest.ini ./
# [F-12 fix · 2026-10-23] Battery cần cây phụ thuộc mà runtime không có:
# sdk/ (import bởi T02/T03), launcher contract (T01 đọc start/stop/launch.bat),
# benchmark/ (gsm8k_sample_10.jsonl cho step rag_benchmark_reference).
COPY sdk/ ./sdk/
COPY start-scp.bat stop-scp.bat launch.bat ./
COPY benchmark/ ./benchmark/
# [F-16 · 2026-10-23] Battery kiểm tra cả cây repo ngoài runtime: T00 cần
# PROJECT.md/GA.md; reality 4-c/4-d + các contract test đọc dashboard/src.
# [F-17 · 2026-10-23 · theo phản biện Vòng 1V] T00 còn đòi .coveragerc,
# CONTRIBUTING.md, .env.example, docs/…; e2e đọc docker-compose.full.yml.
# Bổ sung trọn tầng top-level + docs/ để audit image phản chiếu repo.
COPY .coveragerc .env.example ./
COPY *.md ./
COPY *.yml ./
COPY *.bat ./
COPY Dockerfile* ./
COPY docs/ ./docs/
COPY dashboard/package.json ./dashboard/
COPY dashboard/public/ ./dashboard/public/
COPY dashboard/src/ ./dashboard/src/
# [F-19 · 2026-10-23 · theo AUDITOR-2] reality 4-d-004..029 đọc mini-services/
# (llm-bridge/core.ts, loop-scheduler/index.ts), reality 4-c-019 đọc
# dashboard/public/logo.svg, reality 4-d-017 đọc start-scp.sh.
COPY mini-services/ ./mini-services/
COPY start-scp.sh stop-scp.sh ./
# [C3-W2 fix · 2026-10-23] T02 contract đọc docker/entrypoint-desktop.sh
# (6 test TestVirtualDesktopAndContainerContract) — trước đây docker/ thiếu
# khỏi audit image ⇒ 6 fail FileNotFoundError '/app/docker/entrypoint-desktop.sh'
# (battery 2026-10-23 23:10). Harness fix tại điểm lỗi, strictness giữ nguyên.
COPY docker/ ./docker/
# [F-23 · 2026-10-23] verify_scp_future_target dò 16 skill trong
# .agents/skills/*/SKILL.md khi compose target 4.0.2.
COPY .agents/ ./.agents/
# [D-W1 fix · 2026-10-23] T06 test_11 đọc .gitignore, test_15 đọc
# dashboard/package-lock.json — trước đây 2 file này thiếu khỏi audit image
# ⇒ FileNotFoundError trong container (host pass vì có đủ cây repo).
COPY .gitignore ./
COPY dashboard/package-lock.json ./dashboard/
# [D-W1 fix 2] T02 gateway proxy contract đọc dashboard/gateway.js; T02/integration
# đọc deploy/vps/Caddyfile.dashboard.example — trước đây thiếu khỏi audit image.
COPY dashboard/gateway.js ./dashboard/
COPY deploy/ ./deploy/
# [D-W1 fix · 2026-10-23] data/documents/canonical_seed_corpus.jsonl là seed
# corpus product: atomic_verifier.verify_and_prune → default
# CanonicalFTSWarehouse auto-ingest thư mục này (statute EXPIRED, local
# resolution). Thiếu ⇒ T06 test_25/test_26 refuted_count 0≠1 + mất local
# resolution ở test_04/09/12/13/16 trong container.
COPY data/documents/ ./data/documents/
# [D-W1 fix · 2026-10-23] Audit container thiếu git + node ⇒ 27 test conditional
# skip theo which("node")/which("git"). Bổ sung:
# - git: apt install (build-time network), không khuyến nghị runtime egress.
# - node: binary glibc COPY từ node:20-bookworm-slim; deps (libstdc++/libgcc_s/
#   libc/libm/libpthread/libdl) đã có sẵn trong python:3.12-slim — verify ldd.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY --from=node-bin /usr/local/bin/node /usr/local/bin/node
# [F-09 fix · 2026-10-23] Stage audit thừa kế ENTRYPOINT ["python","-m","scp"]
# từ runtime ⇒ command "sh -c …" của compose.test.yml thành "python -m scp sh …"
# và crash ValueError ngay khi khởi động. Reset ENTRYPOINT để stage này là
# script-runner thuần.
ENTRYPOINT []
# [F-11 fix · 2026-10-23] run_full_audit.py ghi .env.audit-run tại /app; user
# 10001 cần quyền ghi trên chính thư mục /app (chỉ chown dir, không -R để
# không nhân bản layer site-packages).
RUN chown scpuser:scpuser /app
USER 10001
