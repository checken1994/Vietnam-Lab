# Deploying SCP on Google Cloud Platform

## Prerequisites
- GCP project with Cloud Run enabled
- Docker installed locally
- `gcloud` CLI authenticated

## Steps

### 1. Build and push image
```bash
docker build -t gcr.io/<PROJECT_ID>/scp-cli:latest .
docker push gcr.io/<PROJECT_ID>/scp-cli:latest
```

### 2. Deploy to Cloud Run

```bash
gcloud run deploy scp-api \
  --image gcr.io/<PROJECT_ID>/scp-cli:latest \
  --region asia-southeast1 \
  --set-env-vars OPENROUTER_API_KEY=<your-key> \
  --no-allow-unauthenticated \
  --port 8080
```

> **Auth bắt buộc (fail-closed):** KHÔNG deploy với `--allow-unauthenticated`.
> Mặc định an toàn là `--no-allow-unauthenticated`: mọi caller cần IAM role
> `roles/run.invoker` trên service này. Public access (nếu thật sự cần) phải đi
> qua một auth gateway riêng phía trước — ví dụ Identity-Aware Proxy (IAP) hoặc
> service-to-service ID token — không bao giờ mở service thẳng ra Internet.
> Bật thêm auth tầng ứng dụng của SCP (`SCP_AUTH_PASSWORD_FILE` /
> `SCP_AUTH_TOKEN_SECRET`) để còn một lớp bảo vệ khi IAM token bị lộ.
>
> **Secret:** thay `--set-env-vars OPENROUTER_API_KEY=...` bằng
> `--set-secrets OPENROUTER_API_KEY=openrouter-api-key:latest` (Secret Manager)
> để key không nằm trong env plaintext của revision.

### 3. Verify
```bash
curl https://<CLOUD_RUN_URL>/health
```