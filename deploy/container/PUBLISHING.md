# SCP container image — build, publish, and run

This repository has a Docker image build. The GitHub Actions workflow
`.github/workflows/container-publish.yml` builds the image and checks the
container's `/health` endpoint before publishing it to GitHub Container
Registry (GHCR).

## When publishing happens

- A pull request to `main`: build and smoke-test only; no image is published.
- A push to `main`: publish `edge` and `sha-<12-character-commit>`.
- A pushed tag beginning with `v` (for example, `v0.1.0-preview`): publish
  the version tag, `latest`, and `sha-<12-character-commit>`.

The current repository state is experimental. A successful image build and
`/health` response prove only that the container builds and the API process
answers its liveness check; they do not prove production readiness, model
provider access, answer quality, security certification, or long-duration
reliability.

## Pull the image

After the workflow succeeds:

```bash
docker pull ghcr.io/checken1994/vietnam-lab:edge
```

For a release, replace `edge` with the exact version tag shown in GitHub
Packages (for example, `v0.1.0-preview`). For reproducible deployment, use
the SHA tag or the image digest reported by GHCR instead of a moving tag.

## Run locally

Use a private env file; do not commit API keys or production secrets:

```bash
docker run --detach --name scp-api \
  --publish 127.0.0.1:8000:8000 \
  --env-file .env \
  --env SCP_HOST=0.0.0.0 \
  --env SCP_PRODUCTION_MODE=0 \
  --env SCP_EGRESS_MODE=deny \
  ghcr.io/checken1994/vietnam-lab:edge
```

Then check:

```bash
curl --fail http://127.0.0.1:8000/health
curl --include http://127.0.0.1:8000/ready
docker logs --follow scp-api
```

The port is published only on loopback. Do not expose port 8000 directly to
the Internet. A public deployment needs an explicitly configured TLS gateway,
production-mode authentication secrets, egress policy, backups, monitoring,
and independent security and runtime validation. Do not switch to production
mode by copying the CI smoke-test values.

If the package is private, authenticate to GHCR with a GitHub account that
has package read access before running `docker pull`. To let the public pull
the image, check the package settings in GitHub Packages and explicitly set
package visibility to public after the first successful publication.
