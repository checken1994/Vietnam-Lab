/**
 * live.js — Client nối dữ liệu runtime THẬT (chỉ đọc) vào bản đồ.
 *
 * NGUYÊN TẮC:
 *  - Chỉ GET. Không có endpoint ghi. Không side-effect.
 *  - Nếu không lấy được dữ liệu → mode MOCK/STALE, mọi số liệu runtime hiển thị
 *    là null/unknown. TUYỆT ĐỐI không thay bằng số liệu bịa ra (FA-04).
 *  - Mọi giá trị đều kèm nguồn (đường dẫn JSON) để truy vết.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory(root);
  } else {
    root.ScpLive = factory(root);
  }
})(typeof self !== "undefined" ? self : this, function (root) {
  "use strict";

  /**
   * @param {object} options
   *   baseUrl   — tiền tố endpoint proxy (mặc định '/api/live')
   *   intervalMs— chu kỳ polling
   *   rooms     — danh sách phòng để ánh xạ tín hiệu
   *   onUpdate  — callback(derived)
   *   onError   — callback(error)
   */
  function createClient(options) {
    const intervalMs = options.intervalMs || 5000;
    const rooms = options.rooms || [];
    const onUpdate = options.onUpdate || function () {};
    const onError = options.onError || function () {};

    const client = {
      timer: null,
      lastDerived: null,
      lastPayload: null,
      lastError: null,
      consecutiveFailures: 0,
      running: false,
      activeBase: null,
    };

    /**
     * Nguồn dữ liệu runtime, theo thứ tự ưu tiên:
     *  1. <meta name="scp-live-base">     — chỉ định tường minh
     *  2. <meta name="scp-live-fallback"> — dự phòng (mặc định: máy chủ độc lập :5177)
     *  3. cùng origin '/api/live'         — khi chính máy chủ này phục vụ trang
     * Bản đồ phục vụ từ dashboard (:3000/scp-office/) không có /api/live trên
     * cùng origin → tự chuyển sang máy chủ độc lập (cần CORS cho origin dashboard).
     */
    const metaValue = (name) => {
      const el = document.querySelector(`meta[name="${name}"]`);
      return el ? (el.getAttribute("content") || "").trim() : "";
    };
    const DEFAULT_FALLBACK = "http://127.0.0.1:5177/api/live";
    const bases = [];
    const pushBase = (value) => {
      if (value && !bases.includes(value)) bases.push(value);
    };
    pushBase(metaValue("scp-live-base"));
    // [FIX · 2026-10-22] Khi trang được phục vụ từ Dashboard
    // (:3000/scp-office/) thì KHÔNG có route /api/live trên cùng origin ⇒ thử
    // cùng-origin chỉ tạo ra một 404 vô nghĩa trong console (và trong log
    // của dashboard). Bỏ qua nó, đi thẳng tới proxy độc lập.
    const servedFromDashboard = /^\/scp-office(\/|$)/.test(window.location.pathname);
    if (!servedFromDashboard) pushBase(options.baseUrl || "/api/live");
    pushBase(metaValue("scp-live-fallback") || DEFAULT_FALLBACK);

    function derive(payload) {
      const map =
        (root && root.ScpLiveMap) ||
        (typeof require === "function" ? require("./live-map.js") : null);
      if (!map) throw new Error("không nạp được live-map.js");
      return map.deriveRuntimeState(payload, rooms);
    }

    /** Thử từng nguồn theo thứ tự; trả về payload hợp lệ đầu tiên. */
    async function fetchFirstValid() {
      let lastErr = null;
      // Nguồn đã thành công ở lần trước được thử trước → không spam 404
      // khi trang được phục vụ từ dashboard (thiếu /api/live trên cùng origin).
      const ordered = client.activeBase
        ? [client.activeBase, ...bases.filter((b) => b !== client.activeBase)]
        : bases;
      for (const base of ordered) {
        try {
          const res = await fetch(`${base}/snapshot`, {
            method: "GET",
            headers: { Accept: "application/json" },
            cache: "no-store",
          });
          const raw = await res.text();
          let payload = null;
          try {
            payload = JSON.parse(raw);
          } catch (e) {
            payload = null;
          }
          if (!payload || typeof payload !== "object") {
            lastErr = new Error(`${base}: phản hồi không phải JSON (HTTP ${res.status})`);
            continue;
          }
          client.activeBase = base;
          return payload;
        } catch (err) {
          lastErr = err;
        }
      }
      throw lastErr || new Error("không có nguồn dữ liệu runtime khả dụng");
    }

    async function refresh() {
      try {
        const payload = await fetchFirstValid();
        client.consecutiveFailures = 0;
        client.lastError = payload.upstream && payload.upstream.error ? payload.upstream.error : null;
        client.lastPayload = payload;
        const derived = derive(payload);
        client.lastDerived = derived;
        onUpdate(derived, payload);
        return derived;
      } catch (err) {
        client.consecutiveFailures += 1;
        client.lastError = err && err.message ? err.message : String(err);
        // Thông báo rõ ràng: không đọc được runtime KHÁC với "mô phỏng có sẵn" —
        // trang này không có dữ liệu mô phỏng để thay thế (xem README §3).
        const hint =
          "Không đọc được dữ liệu SCP. Hãy chạy proxy chỉ đọc: cd simulation && npm start " +
          "(hoặc kiểm tra dashboard tại http://127.0.0.1:3000/scp-office/).";
        client.lastError = `${client.lastError} — ${hint}`;
        // Nếu trước đó đã có dữ liệu quan sát được → đó là dữ liệu CŨ, không
        // phải hiện trạng: truyền lại payload cuối cùng với reachable=false để
        // lớp ánh xạ gắn nhãn STALE (không hiện số cũ như số hiện hành).
        // Chưa từng có dữ liệu → MOCK.
        const stalePayload = client.lastPayload
          ? Object.assign({}, client.lastPayload, {
              upstream: Object.assign({}, client.lastPayload.upstream, { reachable: false }),
              errors: { client: client.lastError },
            })
          : { errors: { client: client.lastError }, upstream: { reachable: false } };
        const derived = derive(stalePayload);
        client.lastDerived = derived;
        onError(client.lastError, derived);
        return derived;
      }
    }

    function start() {
      if (client.running) return;
      client.running = true;
      const loop = () => {
        if (!client.running) return;
        refresh().then(() => {
          client.timer = window.setTimeout(loop, intervalMs);
        });
      };
      loop();
    }

    function stop() {
      client.running = false;
      if (client.timer) {
        window.clearTimeout(client.timer);
        client.timer = null;
      }
    }

    return { client, start, stop, refresh };
  }

  return { createClient };
});
