# Bản đồ 12 Hệ thống — Vietnam-Lab (SCP Agent OS)

> **Công cụ trực quan hoá kiến trúc.** Vẽ 12 hệ thống (nhóm module) của SCP kèm
> module/skill thật của repo.
> Mọi số liệu trên **thẻ SCP runtime / Cổng dịch vụ / Trạng thái dữ liệu** và trong
> **Nhật ký & Kiểm toán** đến từ runtime thật (`/api/scp/*`, **chỉ đọc**).
> Trang KHÔNG chứa dữ liệu mô phỏng: không nhân vật, không tác vụ giả, không nhật ký giả, không số bịa.

Port mặc định: **5177**. Không phụ thuộc thư viện ngoài (chỉ Node built-ins).

## 0. Ba cách truy cập

| Cách | URL | Lớp dữ liệu | Ghi chú |
|---|---|---|---|
| Máy chủ độc lập (khuyến nghị) | [http://127.0.0.1:5177/](http://127.0.0.1:5177/) | **LIVE** | Phục vụ bản đồ + proxy chỉ đọc trong 1 tiến trình |
| Qua Dashboard Next.js | [http://127.0.0.1:3000/scp-office/](http://127.0.0.1:3000/scp-office/) | **LIVE** nếu proxy `:5177` đang chạy | Bản tĩnh trong `dashboard/public/scp-office/`; tự dự phòng sang `:5177` |
| Mở file trực tiếp | `file://.../scp-office/index.html` | **MÔ PHỎNG** | Vẽ bản đồ bình thường, không đọc được runtime (không có origin HTTP) |

Nguồn dự phòng được khai báo bằng thẻ meta trong `index.html`:

```html
<meta name="scp-live-base" content="" />                    <!-- ưu tiên 1 -->
<meta name="scp-live-fallback" content="http://127.0.0.1:5177/api/live" />
```

Khi trang chạy từ Dashboard (`:3000`), `GET /api/live/snapshot` trên cùng origin trả 404 →
trang tự chuyển sang proxy độc lập `:5177` (CORS **giới hạn đúng origin dashboard**,
không dùng `*`). Sau lần thử đầu, trang bám nguồn đã thành công nên không gọi lại 404.

---

## 1. Chạy

### 1a. Bật SCP (chỉ trong Docker)

SCP **không** chạy trực tiếp trên host — dùng stack chuẩn của repo:

```bash
cd <repo-root>          # ví dụ D:\scp

# Secret + key đã nằm sẵn trong .env của repo (gitignored).
# File .env của máy này: D:\scp\.env — có OPENROUTER_API_KEY, GROQ_API_KEY,
# SCP_CAPABILITY_SECRET, SCP_JWT_SECRET, SCP_AUTH_TOKEN_SECRET, SCP_ADMIN_KEY…
# Nếu tạo .env mới: dùng scripts/bootstrap_local_env.py (sinh 7 secret chuẩn
# của repo) rồi bổ sung OPENROUTER_API_KEY=sk-or-... .

docker compose -f docker-compose.full.yml up -d --build
docker compose -f docker-compose.full.yml ps      # chờ tất cả "healthy"
```

Quyền của SCP trong Docker (ghi trong `docker-compose.full.yml`):
`SCP_CAPABILITY_LEVEL=FULL_PRODUCTION` · `SCP_AUTOFIX_MODE=apply` ·
`SCP_AUTO_APPROVE_TIER3=1` · `SCP_EVOLUTION_ENABLED=1` · cổng publish chỉ trên
`127.0.0.1` (không lộ ra LAN).

Lưu ý: `docker-compose.full.yml` **ghi cứng** một số biến (`SCP_DEV_MODE=1`,
`SCP_PRODUCTION_MODE=0`, `SCP_AUTO_APPROVE_TIER3=1`) nên chúng **ghi đè** giá trị
cùng tên trong `.env` khi chạy bằng Compose. Muốn khác thì sửa compose, không
sửa `.env`.

Về lớp bảo vệ API của dashboard: container dashboard chạy `dashboard/gateway.js`
— reverse proxy nội bộ **tự chèn** `x-forwarded-for: 127.0.0.1` và
`x-scp-proxy-secret` cho request từ client local. Nhờ đó:
- trình duyệt trên máy này đọc được `/api/scp/*` (không cần header tay);
- `x-scp-proxy-secret` **có** hiệu lực (đường mạnh của middleware) vì
  `SCP_AUTH_TOKEN_SECRET` trong `.env` được compose map sang
  `SCP_DASHBOARD_PROXY_SECRET`;
- request từ ngoài vẫn bị từ chối fail-closed.

Hai điều chỉnh đã phải làm khi chạy thật trong Docker (đều kiểm chứng bằng
log/container thật):

1. **`SCP_ROOT=/app` cho container dashboard** (đã thêm vào `docker-compose.full.yml`).
   Route `/api/scp/activity` tự dò `SCP_ROOT` bằng heuristic “có `data` **và**
   (`spec`|`scp`)”; container chỉ có `/app/data` ⇒ không dò được ⇒ **mọi chu kỳ
   loop của scheduler và log dịch vụ đều rỗng** trên Live Activity Stream.
2. **Proxy có cache 30 s** (`SCP_LIVE_CACHE_MS`). Backend SCP giới hạn
   `tier=public limit=30/phút` cho `/health`; poll 4 endpoint mỗi 5 s ⇒ 48
   probe/phút ⇒ backend trả **429** ⇒ dashboard báo nhầm “FastAPI down” dù
   dịch vụ vẫn sống. Nay proxy gọi upstream tối đa 1 lần/30 s và phục vụ bản
   cache kèm nhãn tuổi dữ liệu (`upstream.cached`, `upstream.ageMs`).

### 1b. Bật bản đồ

```bash
cd simulation
npm start                      # http://127.0.0.1:5177
```

Proxy cần secret của dashboard để đi qua đường mạnh:

```bash
# PowerShell
$env:SCP_DASHBOARD_PROXY_SECRET = ((Get-Content ..\.env |
  Where-Object { $_ -match '^SCP_AUTH_TOKEN_SECRET=' }) -replace '^SCP_AUTH_TOKEN_SECRET=','')
npm start
```

```bash
cd simulation
npm start -- --port 5200       # đổi cổng
npm test                       # 50 test contract (node --test)
npm run sync:dashboard         # đồng bộ bản phục vụ sang dashboard/public/scp-office/
npm run sync:check             # kiểm tra drift (exit 1 nếu lệch)
```

---

## 2. Kiến trúc thư mục

```
simulation/
├─ package.json              # scripts: start / test / sync:dashboard (không dependency)
├─ server/
│  ├─ serve.js               # máy chủ tĩnh + proxy chỉ đọc + CORS giới hạn origin + cache 30s
│  └─ live-proxy.js          # allowlist route upstream, header, timeout, cap kích thước
├─ scripts/
│  └─ sync-dashboard.js      # đồng bộ + phát hiện drift sang dashboard/public/
├─ scp-office/               # web app 0-dependency (mở trực tiếp hoặc qua máy chủ)
│  ├─ index.html
│  ├─ css/styles.css
│  └─ js/
│     ├─ data.js             # 12 hệ thống · module/skill thật của repo
│     ├─ live-map.js         # hàm thuần: payload runtime → trạng thái hiển thị
│     ├─ live.js             # client polling (GET, fallback nguồn, fail-closed)
│     ├─ render.js           # bộ vẽ canvas pixel-art (12 phòng, xe, cây, ngày/đêm)
│     ├─ audio.js            # tổng hợp âm 8-bit (Web Audio API)
│     ├─ ui.js               # DOM: sidebar, inspector, modal, nhật ký (escapeHtml)
│     └─ app.js              # bootstrap + vòng lặp hoạt hình + bàn phím
└─ tests/                    # node --test: data · live-map · proxy · sync
```

Mọi module dùng mẫu UMD: chạy được trong trình duyệt (kể cả `file://`) **và**
`require()` được trong Node để kiểm thử — không cần bundler.

Bản phục vụ trong dashboard nằm ở `dashboard/public/scp-office/` (sinh ra từ
`npm run sync:dashboard`, không sửa tay).

---

## 3. Nguồn dữ liệu (chỉ LIVE — không có lớp mô phỏng)

| Nơi hiển thị | Nguồn | Nhãn |
|---|---|---|
| Thẻ **SCP runtime** (release / model / audit / module / v4 LOC) | `/api/scp/status` | số liệu thật, có đường dẫn nguồn |
| Thẻ **Cổng dịch vụ** (:8000 / :3030 / :6080 / :3000) | `/api/scp/health` | số liệu thật |
| Thẻ **Trạng thái dữ liệu** | cả hai | `LIVE` hoặc “không đọc được” — xác nhận tường minh |
| Vòng tròn/viền quanh hệ thống | cả hai | `LIVE-OK` · `LIVE-WARN` · `LIVE-FAIL` · `UNKNOWN` |
| **Nhật ký & Kiểm toán SCP** | `/api/scp/activity` | **CHỈ dữ liệu LIVE** |
| Danh sách module/skill trong sidebar | `data.js` (đường dẫn repo đã kiểm chứng) | *cấu trúc*, không phải số liệu runtime |

### Xác nhận trạng thái (bắt buộc)

- **LIVE** ⇔ lần đọc hiện tại lấy được dữ liệu SCP thật.
- **Không đọc được** ⇔ mất liên kết với runtime (hoặc dữ liệu đã cũ → `STALE`).
  Khi đó mọi số liệu runtime là `—`/`null`/`UNKNOWN` — **không** dùng số cũ.
- Dòng xác nhận hiển thị: nguồn, thời điểm đọc, số hệ thống có tín hiệu quan sát
  được, và tuổi dữ liệu nếu đang dùng cache.

### Nguyên tắc bất biến của lớp ánh xạ (`live-map.js`)

1. Không quan sát được ⇒ `UNKNOWN` (không phải `OK`, cũng không phải `0`).
2. “0 phát hiện” chỉ có nghĩa là “0 phát hiện” khi **đã có** một chu kỳ loop thật
   (`activity.summary.totalRunsRecorded > 0`); nếu chưa từng chạy thì là `UNKNOWN`.
3. Trạng thái chu kỳ chỉ được gán `FAIL` khi nằm trong danh sách lỗi đã biết
   (`error`/`failed`/`timeout`…); chuỗi lạ (vd `"chưa có"`) ⇒ `UNKNOWN`.
4. Ưu tiên tín hiệu trong một hệ thống: `FAIL` > `WARN` > `OK` > `UNKNOWN`.
5. Mọi giá trị đều mang đường dẫn nguồn (`health.fastapi.ok`, …) để truy vết.

### Ánh xạ 12 hệ thống ↔ tín hiệu runtime

| Hệ thống | Mã | Tín hiệu quan sát được | Nếu không có tín hiệu |
|---|---|---|---|
| API Pipeline :8000 | `api-pipeline-8000` | `health.fastapi.ok` | — |
| TaskKernel 17-State | `task-kernel-17states` | `status.scpHealth.task_kernel`, `activity.summary.latestRunStatus` | `UNKNOWN` |
| Reality & TraceLedger | `scp-dna-verifier` | `status.audit.auditRound` | `UNKNOWN` |
| Capability & MCP | `capability-mcp-sec` | *(chưa có tín hiệu runtime công khai)* | `UNKNOWN` |
| Desktop noVNC :6080 | `desktop-novnc-6080` | `health.agentDesktop.ok` | — |
| LLM Gateway (trực tiếp) | `llm-gateway-direct` | *(không còn cổng :8081 — xem ghi chú)* | `UNKNOWN` |
| Tabula Rasa & Knowledge | `tabula-rasa-kb` | *(chưa có tín hiệu runtime công khai)* | `UNKNOWN` |
| Loop :3030 & Sandbox | `loop-scheduler-3030` | `health.loopScheduler.ok`, `activity.summary.latestRunStatus` | — |
| Release Gate T00–T12 | `release-gate-ci` | `activity.summary.latestFindingsCount`, `…latestFixesCount` | `UNKNOWN` |
| Hội đồng 5 Góc & Triad | `hoi-dong-triad` | `status.engine.modulesWired` | `UNKNOWN` |
| Dashboard :3000 & Voice | `xuong-canvas-3000` | `metrics.cpuPercent`, `metrics.memoryPercent` | `UNKNOWN` |
| Khu Năng lượng & Bảo mật | `tram-nang-luong` | *(chưa có endpoint công khai)* | `UNKNOWN` |

> **Ghi chú cổng LLM :8081** — kiến trúc Zero-Bun đã **xoá** container/cổng LLM
> riêng; gateway chạy trực tiếp trong backend. Route `/api/scp/health` vẫn trả một
> mục `llmBridge` **tổng hợp** (`ok:true`, `latencyMs:0`, hint “Direct API-First
> Gateway active”) — đó là hằng số, **không phải quan sát được**. Bản đồ vì vậy
> để phòng này ở `UNKNOWN` và **không** tính nó vào "ports live" (chỉ 4 cổng thật:
> :8000, :3030, :6080, :3000). Hiển thị nó là `LIVE-OK` sẽ là bịa bằng chứng.

### Phạm vi "đồng bộ" khi bật SCP

| Tầng | Có đồng bộ không | Nguồn |
|---|---|---|
| Tình trạng dịch vụ (sống / lỗi), độ trễ | ✅ Có | `health.*.ok` |
| Số cổng đang trả lời (4 cổng thật) | ✅ Có | `health.*.ok` + sự tồn tại của proxy |
| Audit round, engine metadata, module wiring | ✅ Có | `status.audit`, `status.engine` |
| Chu kỳ loop gần nhất, số phát hiện / bản vá | ✅ Có | `activity.summary` |
| CPU / RAM của host dashboard | ✅ Có | `metrics` |
| Nhật ký kiểm toán (sự kiện thật) | ✅ Có | `activity.events` |
| Số yêu cầu đang chờ con người duyệt | ✅ Có | `permissions.count` |
| Module/skill của từng hệ thống | ✅ Có (cấu trúc, đường dẫn đã kiểm chứng) | `data.js` |

---

## 4. Hợp đồng bảo mật của lớp proxy (chỉ đọc)

`server/live-proxy.js` + `server/serve.js` cố tình **fail-closed**:

- Chỉ nhận `GET`/`HEAD` trên `/api/live/*`; `POST/PUT/DELETE` → `405`.
- **Không phải open proxy:** chỉ 4 đường dẫn upstream cố định được phép
  (`status`, `health`, `activity`, `metrics`) + `snapshot` tổng hợp. Route lạ → `404`.
- Upstream chỉ lấy từ biến môi trường; không nhận URL đích từ trình duyệt.
- Chỉ chấp nhận `http:`/`https:` (chặn `file:`, `javascript:`…), timeout cứng 4 s,
  cap kích thước phản hồi 4 MB.
- URL giả mã hỏng (`/%ZZ`) → `403`, **không** làm chết tiến trình.
- Chặn path traversal khi phục vụ file tĩnh (đường dẫn phải nằm trong `scp-office/`).
- **CORS giới hạn theo allowlist origin** (`SCP_SIMULATION_ALLOWED_ORIGINS`), KHÔNG dùng
  `*`: nếu để `*`, bất kỳ website nào người dùng mở cũng đọc được dữ liệu
  `/api/scp/*` — tự mở lại đúng rủi ro mà middleware của dashboard đã gia cố.
- Không có endpoint ghi, không side-effect, không gọi LLM, không đụng `scp/`.

Các bài kiểm thử tương ứng nằm ở `tests/proxy.test.js` (nhánh âm chứng minh cổng
thực sự chặn ghi, chặn route ngoài allowlist, **từ chối origin lạ** và **không chết
tiến trình** khi URL hỏng).

---

## 5. Điều khiển

| Thao tác | Cách |
|---|---|
| Tìm kiếm | `/` (hoặc ô tìm kiếm) |
| Lọc theo nhóm module | các pill S / P / G / W ở đầu sidebar |
| Zoom | `+` / `-`, lăn chuột, nút `+`/`−` |
| Căn giữa bản đồ | `F` hoặc nút 🎯 |
| Kéo bản đồ | chuột trái / một ngón tay; phím mũi tên |
| Xem chi tiết một hệ thống | bấm vào ô hệ thống trên bản đồ hoặc trong sidebar |
| Đọc lại dữ liệu runtime | nút 🔄 (chỉ đọc) |
| Đóng modal | `Esc` |

Ngày/đêm được suy ra từ **đồng hồ thật** của máy (22:00–06:00), không có nút bật/tắt giả lập.

---

## 6. Kiểm thử

```bash
cd simulation
npm test
```

Kết quả thực tế trên Node v26.5.0 (Windows):

```
ℹ tests 50
ℹ pass 50
ℹ fail 0
```

Bao gồm các nhánh âm (anti-placebo) bắt buộc:

- payload rỗng ⇒ mọi phòng `unknown`, `ports.live = null` (không phải `0`);
- thiếu trường `ok` trong `health` ⇒ phòng đó `unknown`, **không** fail-open;
- **chưa từng có chu kỳ loop** ⇒ `release_ci` = `unknown` (không phải `OK` vì “0 phát hiện”);
- **hàng đợi duyệt**: đọc được `permissions.count` thì lấy số đang chờ; không đọc được thì **null** (không phải 0 — “không biết” khác “không có gì”);
- trạng thái chu kỳ lạ (vd `"chưa có"`) ⇒ `unknown`; chỉ trạng thái lỗi đã biết mới là `fail`;
- **mục `llmBridge` tổng hợp (direct) không được coi là quan sát được** và không tính vào cổng;
- upstream không trả lời ⇒ `STALE`/`MOCK`, không kết luận “hỏng”, không hiện số cũ;
- `POST /api/live/*` ⇒ `405`; route ngoài allowlist ⇒ `404`; path traversal ⇒ `403` **và không rò nội dung file ngoài thư mục phục vụ**;
- URL giả mã hỏng (`/%ZZ`) ⇒ `403` và **tiến trình không chết**;
- origin CORS lạ ⇒ không có header `Access-Control-Allow-Origin`, preflight ⇒ `403`;
- upstream treo ⇒ timeout fail-closed; phản hồi 5 MB ⇒ bị cắt, không trả payload khổng lồ;
- bản sao trong `dashboard/public/scp-office/` lệch nguồn ⇒ `npm test` fail.

---

## 7. Reality notes

1. **Đã gỡ toàn bộ lớp mô phỏng.** Bản HTML gốc vẽ 24 nhân vật đi lại, bong bóng
   nhiệm vụ, xe chạy, thanh tiến trình `7/7`… và có nút “giao việc”. Tất cả là **dữ
   liệu bịa** — không có tín hiệu runtime nào ứng với chúng. Bản này bỏ hẳn: không
   nhân vật, không bong bóng, không xe, không tác vụ giả, không nhật ký giả. Những gì
   còn lại là 12 hệ thống thật + module/skill thật + số liệu runtime thật.
2. **Không còn thẻ số liệu bịa.** Bản gốc hiển thị `76/76 PASS`, `18/18 Verified`,
   `34%`, `38%`, `0.299ms`, `2120 APIs`, `29 Nguyên tắc` như số liệu của hệ thống.
   Đó là số liệu minh hoạ, **không** phải bằng chứng runtime ⇒ đã thay bằng số liệu
   thật từ `/api/scp/status` và `/api/scp/health`, kèm đường dẫn nguồn.
3. **Không có side-effect.** Trang **không** gọi bất kỳ endpoint ghi nào
   (`/api/scp/loop/trigger` là POST — bản đồ không bao giờ gọi).
4. **XSS.** Mọi chuỗi nội suy vào DOM đều qua `escapeHtml()` (`js/ui.js`).
5. **Cổng LLM :8081** đã bị xoá khỏi kiến trúc (Zero-Bun) — xem ghi chú §3.
6. **Nhãn nhóm module** đã đổi cho đúng thực tế repo: `G` = Điều phối, `S` = SCP &
   Cố vấn, `P` = Cổng & Dịch vụ, `W` = Kỹ năng cá nhân hóa (không có “Lõi Gemini” —
   SCP không có thành phần Gemini).
7. **Ngày/đêm lấy từ đồng hồ thật** của máy (22:00–06:00), không có nút bật/tắt giả.
8. **[FIX 2026-10-22 · trang trắng]** Trang **chỉ** vẽ trong `requestAnimationFrame`.
   Chromium không gọi rAF cho tab nền / cửa sổ bị che ⇒ mở trang ở tab nền thì canvas
   trắng hoàn toàn, **không có lỗi JS nào** (dễ tưởng trang hỏng). Nay trang **vẽ ngay
   lúc boot** và vẽ lại mỗi khi tab trở lại hiển thị (`visibilitychange` / `focus`).
9. **[FIX 2026-10-22 · 404 thừa]** Khi trang được phục vụ từ Dashboard (`:3000/scp-office/`)
   thì không có route `/api/live` trên cùng origin ⇒ mỗi lần poll tạo một 404 vô nghĩa
   trong console và log của dashboard. Nay trang nhận ra đường dẫn đó và đi thẳng tới
   proxy độc lập.
10. **Khi không đọc được runtime**: badge chuyển sang “MÔ PHỎNG — không đọc được SCP”,
    mọi số hiện “—”, và thông báo lỗi nêu rõ cách khắc phục (`cd simulation && npm start`).
11. **24 "nhân vật" là 24 thành phần THẬT của repo** (đúng tên, đúng skill, đúng vai trò:
    `scp-task-kernel-review`, `scp-reality-verifier`, `scp-gateway-resilience`…). Chúng
    **không** đi theo tác vụ giả: mỗi thành phần chỉ di chuyển khi có **sự kiện runtime
    thật** liên quan tới nó (đọc `service`/`message` từ `/api/scp/activity`) và bong bóng
    hiện nguyên văn thông điệp sự kiện đó; không có sự kiện thì nhân vật đứng yên và
    bong bóng ghi rõ “chưa có hoạt động mới”. Không còn nhiệm vụ giả, không còn đồng hồ
    đếm ngược, không còn kho tác vụ tự sinh.
12. **[FIX 2026-10-22 · khớp nhầm]** Khớp sự kiện được siết: (1) `service` khớp **chính
    xác** (`event.service === agent.service`) được ưu tiên; (2) từ khoá trong message chỉ
    dùng **định danh riêng** của thành phần (`cdp`, `mcp_server`, `llm_gateway`…), bỏ các
    từ chung (`kernel`, `next`, `health`, `audit`, `token`…) vì một dòng log chứa
    `node_modules/...kernel` từng làm bật nhầm TaskKernel Master. Có test chặn lại.
13. **[FIX 2026-10-22 · trang trắng]** Trang **chỉ** vẽ trong `requestAnimationFrame`.
    Chromium không gọi rAF cho tab nền / cửa sổ bị che ⇒ mở trang ở tab nền thì canvas
    trắng hoàn toàn, **không có lỗi JS nào** (dễ tưởng trang hỏng). Nay trang **vẽ ngay
    lúc boot** và vẽ lại mỗi khi tab trở lại hiển thị (`visibilitychange` / `focus`).
14. **[FIX 2026-10-22 · 404 thừa]** Khi trang được phục vụ từ Dashboard (`:3000/scp-office/`)
    thì không có route `/api/live` trên cùng origin ⇒ mỗi lần poll tạo một 404 vô nghĩa
    trong console và log của dashboard. Nay trang nhận ra đường dẫn đó và đi thẳng tới
    proxy độc lập.
15. **Khi không đọc được runtime**: badge chuyển sang “MÔ PHỎNG — không đọc được SCP”,
    mọi số hiện “—”, và thông báo lỗi nêu rõ cách khắc phục (`cd simulation && npm start`).

## 8. Hạn chế đã biết

- Chưa có tín hiệu runtime công khai cho 3 hệ thống (`capability-mcp-sec`,
  `tabula-rasa-kb`, `tram-nang-luong`) ⇒ chúng luôn `UNKNOWN` cho tới khi có nguồn
  quan sát được.
- `status.scpHealth.task_kernel` phụ thuộc payload `/health` của backend; nếu backend
  không trả khoá đó ⇒ phòng TaskKernel ở `UNKNOWN` (không đoán).
- Bản đồ tối ưu cho màn hình rộng ≥ 1280 px; dưới 900 px sidebar tự thu gọn và bảng
  “Hoạt động mô phỏng” bị ẩn.
- Bản đồ vẽ bằng Canvas 2D; đo trên máy này (Chrome, 1600×900): ~623 màu riêng biệt /
  khung hình, ổn trên laptop thông thường.
- Bản phục vụ trong `dashboard/public/` là **bản sao**; sửa nguồn mà quên
  `npm run sync:dashboard` sẽ bị `npm test` bắt (drift check).

---

## 9. Liên hệ repo

Nguồn kiến trúc được mô phỏng: `scp/api_server_parts/`, `scp/task_kernel_parts/`,
`scp/verification`, `scp/security`, `scp/mcp_server/`, `scp/llm_gateway`,
`scp/data_sources`, `scp/sandbox_evaluator`, `tests/T00..T12`,
`kynang/get-more-perspectives`, `dashboard/`, `kynang/focus-energy`.

> Bản đồ này là **công cụ minh hoạ kiến trúc**. Muốn biết hệ thống thật đang ở trạng
> thái nào, hãy đọc `GA.md`, `docs/audits/STATUS-LEDGER.md` và chạy `pytest tests/`
> trên đúng SHA — không đọc số liệu trên bản đồ này.
