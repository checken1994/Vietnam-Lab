# PROP-restcountries-deprecation-news-evidence-w16 — v3.1 chết upstream (v5 đòi key) + realtime news: không wire được không key

- **ID:** PROP-restcountries-deprecation-news-evidence-w16-2026-10-07
- **Date:** 2026-10-07
- **Author:** SCP Worker Agent W16
- **Branch:** `fix/wave16`
- **Base commit (trước change):** `21ddd241` (main; W1-W15 + W16-f4/f5/g1 đã merge)
- **Status:** `PENDING_OWNER_APPROVAL`
- **Spec touched:** NO. Đây là proposal quyết định owner cho 2 mục Nhiệm vụ của Wave 16 mà **Reality phá premise** — không có code change nào kèm proposal này cho 2 mục đó (lý do bên dưới).

## 0. Tóm tắt điều hành

Wave 16 nhận 3 nhiệm vụ: (1) kích hoạt geocode fallback theo approval W14 — **ĐÃ merge sẵn trong W14** (`c3835989` + `8f9fe6ba`), verify + live check PASS @ `21ddd241`; (2) wire RestCountries cho geography facts — **KHÔNG wire được**: upstream `restcountries.com/v3.1` đã bị deprecate server-side (301 → JSON deprecation), API mới `api.restcountries.com/v5` đòi API key (401 authKeyMissing) — wire vào v3.1 là placebo runtime (hermetic test xanh trên fixture nhưng runtime LUÔN fail-closed None); (3) realtime news no-key cho time-signal — **không có nguồn phù hợp** (26 News entries / 7 no-key, không cái nào realtime-news-grade) và W14 gen-with-evidence (DuckDuckGo/Bing scraping, no-key) đã phủ time-signal runtime-verified. Quyết owner cần cho (2): chọn 1 trong 3 options §2.4.

## 1. Nhiệm vụ 1 — geocode fallback: ĐÃ MERGE SẴN, verify-only

Premise của task W16 ("tests W14 pin geocode-MISS chờ duyệt") là **stale**: approval W14 đã được implement + merge vào main trước khi W16 bắt đầu:

- `c3835989` — geocode fallback trong `WeatherDataSource` (`_geocode_city`, `geocode_host_allowed`, `build_geocode_url`, `_sanitize_city_name`) + flip 3 test T02 sang semantics hit + cập nhật T03 (`test_fail_closed_on_unknown_location_no_network_attempt`: city lạ = đúng 1 geocode fetch, 0 forecast fetch khi miss; NodeID giữ nguyên).
- `8f9fe6ba` — egress seam EE-G1 cho CI (SCP_EGRESS_MODE=deny ở CI baseline; test cần fetch tự khai allowlist).

Bằng chứng verify trên `fix/wave16` @ `21ddd241` (không sửa 1 dòng code nào cho mục này):

- Region W13/W14 weather-geocode: **54 passed** (T02 geocode contract + T03 W13 lookup + T05 egress ×2).
- `.env.example` line 267 đã có `geocoding-api.open-meteo.com` trong `SCP_EGRESS_ALLOWLIST`.
- **Live check FA-12 (1 lần):** `_geocode_city("Đà Nẵng")` qua đường wire thật (SCP_EGRESS_MODE=allowlist, SCP_EGRESS_ALLOWLIST=api.open-meteo.com — mirror runtime; geocode host từ scoped grant code-level) → `(16.06778, 108.22083, 'Đà Nẵng')` — toạ độ thật từ Open-Meteo Geocoding.

## 2. Nhiệm vụ 2 — RestCountries: upstream chết, KHÔNG wire (anti-placebo)

### 2.1 Chain of evidence (FA-09 probe, 2026-10-07)

1. `curl https://restcountries.com/v3.1/name/vietnam` → **HTTP 301** → `Location: https://files-03.restcountries.com/countries.00/legacy.json` → body: `{"success": false, "data": null, "errors": [{"message": "This API version has been deprecated. Please visit https://restcountries.com/docs/countries/legacy-api-deprecation to migrate to our new version (v5)."}]}`.
2. `https://api.restcountries.com/v5/name/vietnam` → **HTTP 401** `{"errors":[{"message":"Authorization key required.","code":"authKeyMissing"}]}` (v3.1 trên host api cũng 401 — toàn bộ new API là keyed).
3. **Qua SCP wire thật** (SCP_EGRESS_MODE=allowlist, SCP_EGRESS_ALLOWLIST=restcountries.com): `GeographyDataSource._fetch_from_api('vietnam','capital')` → **None** — `_SafeHTTPRedirectHandler` re-gate từng hop (url_safety.py:266) chặn redirect sang `files-03.restcountries.com` (không nằm trong scoped grant — least-privilege đúng thiết kế); `_live_ping()` → **False** (health fail-closed trung thực, không lie).
4. Kể cả khi owner thêm `restcountries.com` + `files-03.restcountries.com` vào allowlist: body cuối vẫn là JSON deprecation, KHÔNG có country data. v3.1 không còn data ở bất kỳ cấu hình egress nào.

### 2.2 Vì sao KHÔNG wire theo chỉ thị gốc

Wire `restcountries.com/v3.1/name/{name}` như task mô tả sẽ sinh ra: test hermetic xanh trên fixture + egress pin xanh + commit "(owner duyệt egress)" — nhưng runtime LUÔN None (redirect hop bị re-gate chặn; và nếu mở thì body là deprecation). Đó chính xác là **placebo wire** — dạng anti-pattern W12 ("source CÓ nhưng KHÔNG được wire" đảo ngược: "wire NHƯNG KHÔNG có data"). Vi phạm FA-03 (claim không có evidence runtime), FA-12 (không thể nghiệm thu E2E), ràng buộc task "mọi giá trị từ API thật". Acceptance "live check RestCountries 'vietnam' — xác nhận hoạt động" KHÔNG THỂ đạt.

### 2.3 Trạng thái geography facts hiện tại (không mất coverage)

`domain_registry.DOMAINS['geography'].preferred_apis = ['rest_countries', 'wikipedia']` — API [1] **wikipedia ĐÃ wire sẵn** (`_wiki_lookup` cho `_KNOWLEDGE_DOMAINS`, runtime .env có en/vi.wikipedia.org). Live check: `resolve_lookup_data('What is the capital of France?', domain='geography')` → Wikipedia (en) — France, extract thật. Geography questions KHÔNG rơi hố coverage; chỉ thiếu structured country facts (capital/population/region dạng bảng) từ RestCountries.

Ngoài ra `GeographyDataSource._local_data` (27 entry) vẫn trả lời qua registry fetch() — state DEGRADED hợp lệ (health False trung thực).

### 2.4 Options cho owner (chọn 1)

- **Option A (khuyến nghị nếu cần structured facts):** owner đăng ký key v5 (restcountries.com/docs), set secret `RESTCOUNTRIES_API_KEY` trong `.env`, duyệt egress host `api.restcountries.com` (exact host) → wave sau wire v5 kèm scoped grant + hermetic test + live check FA-12 thật. Lưu ý: v5 là API keyed — cần quyết về secret handling + quota.
- **Option B:** duyệt một nguồn country-data no-key KHÁC (owner chỉ đích danh host, ví dụ tự host mirror dataset country tĩnh) → wire theo cùng pattern W13/W14.
- **Option C:** chấp nhận coverage hiện tại (wiki + local data), gỡ `rest_countries` khỏi `preferred_apis` geography (hoặc giữ làm ghi chú) — zero egress expansion.

### 2.5 Việc KHÔNG làm trong wave này

- KHÔNG thêm `restcountries.com` vào `.env.example` SCP_EGRESS_ALLOWLIST: approval còn nhưng endpoint chết — allowlist entry cho host không lấy được data chỉ mở bề mặt egress vô ích và gây hiểu sai cho operator.
- KHÔNG sửa `GeographyDataSource._live_ping`/`_fetch_from_api`: probe chứng minh health đã fail-closed đúng (False) và fetch None đúng — không có product bug tại điểm lỗi.

## 3. Nhiệm vụ 3 — realtime news cho time-signal: KHÔNG wire, giữ proposal

- **Hiện trạng:** W14 gen-with-evidence (`653814a4`) đã cho time-signal question web evidence TRƯỚC generation qua `InternetSearch` (DuckDuckGo/Bing scraping, **no-key**, egress hosts duckduckgo.com/bing.com đã allowlist runtime) — W14-battery: q08 PASS-thuần 3/3, `web_fallback_used=True` (runtime-verified, GA.md B1b).
- **Catalog News:** 26 entries category News, 7 no-key: Chronicling America (archive báo Mỹ lịch sử — không realtime), DataCube AI, Florida Man (novelty), Graphs for Coronavirus (domain stale), Inshorts News (API unofficial scraper qua GitHub — không có SLA/reliability), Noozra, Spaceflight News (domain-specific). **0/7 phù hợp làm evidence source realtime tổng quát.**
- **Kết luận:** wire RSS/news bổ sung là dư thừa so với InternetSearch path + cần duyệt egress host mới + không có nguồn no-key đáng tin. Ghi nhận đây là decision point của owner nếu muốn evidence "news-grade" (vd Google News RSS `news.google.com` no-key — cần owner duyệt egress + quarantine rules như W14); KHÔNG tự wire (không ép).

## 4. Evidence limits

- Probe REST Countries thực hiện 2026-10-07 từ máy owner (curl + SCP wire) — upstream có thể thay đổi lại; owner re-probe khi xem xét Option A/B.
- Proposal này KHÔNG phải runtime proof cho bất kỳ wire nào — chỉ là trạng thái reality của 2 nguồn data + đề xuất.
