# P0-B Health Detail Test Migration

## Phạm vi

- **Chỉ sửa:** `tests/T01_boot/test_flow_01_boot_background_scp_standard.py:371-398`
- **Không sửa:** product code, `.env`, compose hoặc file ngoài scope được giao.
- **Mục tiêu:** phản ánh policy P0-B trong `SCP_PRODUCTION_MODE=1`: `/health/detailed` phải trả `401` khi thiếu credential; credential hợp lệ vẫn được phép nhận diagnostic payload.
- **Snapshot kiểm tra:** `a2edec2c32d0be12901b268d797f018c15ce1fdc`
- **Skill hashes:**
  - `.agents/skills/scp-dna/SKILL.md`: `4aada0be4873598dc50c3a7f38d90151429bb5263c511a838ed1cdcb4d594d10`
  - `.agents/skills/scp-reality-verifier/SKILL.md`: `a9d65ce53b18f8310ceeb302b18b341a0e1b8b19cddc7f46d4fa6ee99432269e`
  - `.agents/skills/scp-capability-security-review/SKILL.md`: `83f1633256756f8e4951471a11b9d45c1738d09f4db851df8ee91ee123a235ee`

## Bằng chứng implementation và fixture

`scp/api_server.py:336-339` derives production mode from `SCP_PRODUCTION_MODE`/`SCP_MODE`; `scp/api_server.py:567-568` attaches canonical `verify_admin` to `/health/detailed` in production. `scp/security/auth.py:125-165` accepts a JWT signed by the configured `SCP_JWT_SECRET` when its subject/role is `admin`. The test module already establishes test-only `SCP_JWT_SECRET` and `SCP_ADMIN_KEY` via `os.environ.setdefault` at lines 35-38; it does not add a usable production credential literal.

The migrated test obtains a token through the real `/auth/token` route using the existing environment-backed `SCP_ADMIN_KEY` fixture, then sends only the returned bearer token. It does not print or persist the key/token.

## Thay đổi assertion

1. `GET /health/detailed` without headers: exact `401`.
2. `POST /auth/token` with the existing test-only environment fixture: exact `200`; extract `access_token` from the response.
3. `GET /health/detailed` with `Authorization: Bearer <issued token>`: exact `200`.
4. Authenticated response is required to be a JSON object with diagnostic `status` (`ok` or `initializing`) and `version`/`routes` fields. This preserves the diagnostic payload contract without asserting readiness timing.

No `skip`, `xfail`, deleted test, production-auth weakening, or fallback acceptance was introduced.

## Reality checks

| Check | Result | Scope / limitation |
|---|---|---|
| Baseline focused test before migration | **FAIL as expected**: `401` was rejected by the old `[200, 503]` assertion | Demonstrated contract drift at the target test. |
| `python -m pytest tests/T01_boot/test_flow_01_boot_background_scp_standard.py::TestFlow01BootBackground::test_health_detailed_endpoint_returns_full_status -q --tb=short` | **1 passed** | Test-process environment had production mode loaded from the selected environment. |
| Same focused test with `SCP_PRODUCTION_MODE=1` explicitly set | **1 passed** | Direct production-mode check; no service process was started. |
| `python -m pytest tests/T01_boot/test_flow_01_boot_background_scp_standard.py -q --tb=short` | **27 passed** | Full target file; local pytest evidence only. |
| `python tools/t00_meta_audit.py` | **Exit 0 — All integrity checks passed (0 new regressions)** | Existing baseline debt/tripwire warning remains; T00 is not full-suite evidence. |
| `git diff --check -- tests/T01_boot/test_flow_01_boot_background_scp_standard.py` | **PASS** | No whitespace errors. |

The pytest run emitted existing OpenTelemetry shutdown/export noise (`ValueError: I/O operation on closed file`) after focused execution; it did not change the test result. The full target-file run completed with `27 passed`.

## Scope and limitations

- This is test-contract migration evidence, not a full repository or release verdict.
- The test verifies the production-auth branch as loaded by the test process. It does not prove a separately deployed service configuration.
- `SCP_ADMIN_KEY` is read from the existing test environment fixture rather than hardcoded in the migrated assertion. If that fixture is absent or invalid, the test fails closed instead of weakening the authenticated branch.
- No product code was changed.

## PHÁT HIỆN MỚI (NEW FINDINGS)

Không có phát hiện mới ngoài contract drift đã được giao và sửa trong test tại `tests/T01_boot/test_flow_01_boot_background_scp_standard.py:371-398` (severity: test-contract drift; slot: P0-B health-detail migration).
