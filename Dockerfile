# SCP CLI Docker Image
# Multi-stage build: clean production runtime & audit/test runner
# Base: python:3.12-slim with uv for deterministic, fail-closed dependency installation

FROM python:3.12-slim AS runtime

# Install uv
RUN pip install --no-cache-dir uv

WORKDIR /app

# Copy dependency files first for layer caching
COPY scp/requirements.txt scp/requirements-otel.txt ./

# Install dependencies deterministically (fail-closed, no silent fallback)
RUN uv pip install --system --no-cache -r requirements.txt -r requirements-otel.txt

# Copy runtime source code and specifications
COPY scp/ ./scp/
COPY spec/ ./spec/
# DoubtCron fitness_drift check reads the frozen golden suite at runtime
COPY tests/golden/ ./tests/golden/
COPY README* ./

# Environment variable placeholders (override at runtime)
ENV OPENROUTER_API_KEY=""
ENV OPENROUTER_MODEL="deepseek/deepseek-v4-flash-0731"
ENV OPENROUTER_MODEL_AUTO="0"
ENV SCP_FALLBACK_WATCH_INTERVAL="21600"
ENV SCP_RETRY_TIMEOUT_SEC="300"
ENV SCP_KW_ENABLE="0"

# [MACH1-FIX-6] Bind the image to its source SHA at build time
ARG SCP_GIT_SHA=unknown
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

# Audit stage: for running verification & audit scripts in test profile
FROM runtime AS audit
USER root
RUN pip install --no-cache-dir bandit
COPY scripts/ ./scripts/
USER 10001