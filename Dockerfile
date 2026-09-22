# SCP CLI Docker Image
# Base: python:3.12-slim with uv for fast dependency installation
FROM python:3.12-slim

# Install uv
RUN pip install --no-cache-dir uv

WORKDIR /app

# Copy dependency files first for layer caching
COPY scp/requirements.txt scp/requirements-otel.txt ./

# Install dependencies
RUN uv pip install --system --no-cache -r requirements.txt || pip install --no-cache-dir -r requirements.txt

# Install bandit for security audit (external_audit tests)
RUN pip install --no-cache-dir bandit

# Copy source code
COPY scp/ ./scp/
COPY spec/ ./spec/
# DoubtCron fitness_drift check reads the frozen golden suite at runtime;
# without it the check FAILs in the container (observed in Docker logs).
COPY tests/golden/ ./tests/golden/
COPY README* ./


# Environment variable placeholders (override at runtime)
ENV OPENROUTER_API_KEY=""
ENV OPENROUTER_MODEL="deepseek/deepseek-v4-flash-0731"
ENV OPENROUTER_MODEL_AUTO="0"
ENV SCP_FALLBACK_WATCH_INTERVAL="21600"
ENV SCP_RETRY_TIMEOUT_SEC="300"
ENV SCP_KW_ENABLE="0"

# [MACH1-FIX-6] Bind the image to its source SHA at build time:
#   docker build --build-arg SCP_GIT_SHA=$(git rev-parse HEAD) ...
# _scp_service_identity() reads SCP_GIT_SHA first, so containers can report
# the exact commit even without a .git directory in the image.
ARG SCP_GIT_SHA=unknown
ENV SCP_GIT_SHA=${SCP_GIT_SHA}

EXPOSE 8000

RUN useradd -u 10001 -m scpuser && \
    mkdir -p /app/data && \
    chown -R scpuser:scpuser /app/data
USER 10001

ENTRYPOINT ["python", "-m", "scp"]
CMD ["8000"]