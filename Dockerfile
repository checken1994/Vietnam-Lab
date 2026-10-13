# SCP API container
# Runtime and audit/test runner use the repository's version-pinned transitive lock.

FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS runtime

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

# Audit stage: for running verification & audit scripts in test profile
FROM runtime AS audit
USER root
COPY scripts/ ./scripts/
USER 10001
