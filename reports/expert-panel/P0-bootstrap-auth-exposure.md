# P0 Bootstrap / Auth / Exposure Review

## Phạm vi và bằng chứng

- Snapshot làm việc: `a2edec2c32d0be12901b268d797f018c15ce1fdc` trước patch; workspace có thay đổi không thuộc worker này, nên verifier phải đối chiếu diff theo file.
- Worker scope: bootstrap first-run, production API exposure, admin auth contract, Docker liveness healthcheck.
- Skills áp dụng: `scp-dna`, `scp-capability-security-review`.
- PASS trong báo cáo này chỉ có nghĩa không thấy lỗi trong test scope và snapshot hiện tại; không phải production-ready claim.

## Thay đổi

1. `scripts/bootstrap_local_env.py`
   - Sinh `SCP_CAPABILITY_SECRET` bằng `secrets.token_hex(32)` cùng JWT/admin secrets khi tạo `.env` lần đầu.
   - Giữ create-only; dùng `O_EXCL` để tránh overwrite trong race giữa installer processes.
   - Secret-bearing files được tạo với mode hạn chế (`0600`, directory `0700` best-effort); secret values không được in.
   - Existing `.env` không bị sửa.
2. `scp/api_server.py`
   - Production được nhận diện bởi `SCP_PRODUCTION_MODE` hoặc `SCP_MODE=production`.
   - Production tắt `/docs`, `/redoc`, `/openapi.json` (FastAPI trả 404, không phải 401 giả).
   - Production bảo vệ `/metrics` và `/health/detailed` bằng canonical `verify_admin`; `/health` và `/readiness` vẫn public để liveness/readiness không bị phá.
   - `/auth/token` dùng exact `SCP_ADMIN_KEY` và constant-time compare; không tự mở admin route.
3. `scp/security/auth.py`
   - `verify_admin` là contract canonical duy nhất, hỗ trợ JWT HS256 admin hoặc static token/password, không dev bypass.
   - `401`: thiếu/sai credential hoặc không có credential source.
   - `503`: credential file/config đã được khai báo nhưng unreadable/conflicting; đây là configuration failure, khác 404 route disabled và 503 readiness.
4. `compose.yml`
   - Thêm Docker liveness healthcheck gọi `/health`; không gọi `/ready`, vì `/health` không giả readiness.

## Regression tests

`tests/T03_capability/test_bootstrap_and_production_auth.py` kiểm tra:

- first-run tạo capability key; output không chứa secret; second run giữ nguyên output;
- production docs/Redoc/OpenAPI là 404;
- production `/metrics` và `/health/detailed` là 401 nếu chưa auth, 200 sau JWT;
- `/health` vẫn 200, `/readiness` vẫn 503 khi judge chưa ready;
- `/auth/token` wrong key 401, right key 200;
- local/test profile giữ docs và developer metrics behavior.

## Verification observed

- Focused auth/profile/secret/bootstrap suite: `45 passed, 3 warnings`.
- Adjacent M2/API contract suite: `38 passed`; có logging errors do background threads sau test teardown, thuộc code ngoài scope.
- T01/T02/T03/T11 scoped run: `1056 passed, 2 skipped, 1 failed`; failure là test cũ `tests/T01_boot/test_flow_01_boot_background_scp_standard.py::TestFlow01BootBackground::test_health_detailed_endpoint_returns_full_status`, vẫn kỳ vọng public `/health/detailed` trong môi trường production. Đây là contract expectation cần owner/verifier reconcile; worker không sửa test ngoài scope và không nới production gate.
- `python tools/t00_meta_audit.py`: exit 0, `0 new regressions`; output còn baseline debt/L4 warnings có sẵn.
- `python tools/verify_scp_test_skill_contract.py`: exit 0, `PASS_WITHIN_SCOPE`.
- `py_compile` cho file scoped: pass; `git diff --check` cho file scoped: pass.

## Limitations / open questions

- Chưa chạy full `python -m pytest tests/` trên exact final tree; mandatory full-suite evidence còn thiếu.
- Chưa chạy Docker build/up healthcheck thực tế; compose healthcheck mới có static/syntax evidence.
- Production docs are disabled rather than authenticated because FastAPI's generated schema routes are intentionally absent in production. If operators require authenticated schema, cần design riêng (không tự mở trong patch này).
- `/dashboard` và public `/health` remain intentionally available; their information disclosure policy cần owner confirm nếu production threat model yêu cầu tighter surface.
- `compose.test.yml` vẫn chứa test-only interpolation defaults; không thay đổi vì đây là test profile, nhưng production must not reuse it.

## PHÁT HIỆN MỚI (NEW FINDINGS)

| File:line | Severity | Phát hiện | Đề xuất slot |
|---|---|---|---|
| `tests/T01_boot/test_flow_01_boot_background_scp_standard.py:371-378` | P1 contract drift | Test legacy yêu cầu `/health/detailed` trả 200/503 nhưng production policy mới trả 401 nếu chưa auth. Không nên nới policy để làm xanh test. | Sửa ở integration/release test-contract slot sau owner review; giữ production auth gate.
| `scp/api_server.py:549-551` | P1 exposure review | `/dashboard` vẫn public trong production và trả HTML dashboard; chưa có auth/profile policy trong worker scope. | Security/API exposure follow-up slot; probe trước patch.
| `scp/api_server.py:554-564` | P2 exposure review | `/health` public chứa service identity, route count và release metadata. Liveness cần public nhưng disclosure boundary chưa được threat-model xác nhận. | Production observability policy slot; quyết định field minimization hoặc authenticated diagnostic endpoint.
| `scp/api_server.py:336-339` | P2 consistency | `SCP_MODE=production` được tính cho docs/metrics gating, còn `route_profile.resolve_api_profile()` chỉ dựa vào `SCP_PRODUCTION_MODE`; hai biến có thể tạo policy split. | Config/profile contract slot; hợp nhất production predicate sau owner approval.
| `scp/security/auth.py:167-168` | P2 secret/log hygiene | JWT decode exception được đưa vào `logger.debug`; thư viện exception hiện không chứa token nhưng cần regression guard để ngăn credential-bearing exceptions bị log trong tương lai. | Auth logging hygiene slot; redact/structured exception policy.
| `compose.yml:78-83` | P2 runtime evidence | Healthcheck đã thêm liveness `/health`, nhưng Docker runtime proof chưa chạy trên exact tree. | Runtime/release evidence slot; chạy build/up/probe/teardown độc lập.
