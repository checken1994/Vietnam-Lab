/**
 * live-map.js — Hàm thuần (pure) ánh xạ payload runtime thật → trạng thái hiển thị.
 *
 * NGUYÊN TẮC BẤT BIẾN (SCP DNA #1, #4, #19, #22 — tương ứng FA-04):
 *  - Chỉ gán 'ok'/'warn'/'fail' khi quan sát được giá trị thật trong payload.
 *  - Thiếu tín hiệu → 'unknown' (KHÔNG đoán, KHÔNG mặc định là tốt).
 *  - Không có dữ liệu → mode 'MOCK' và mọi trường số là null (không phải 0).
 *  - Mỗi giá trị đều mang `source` (đường dẫn JSON) để truy vết nguồn gốc.
 *
 * Module thuần, không chạm DOM → kiểm thử được bằng node --test.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.ScpLiveMap = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const STATE = {
    OK: "ok",
    WARN: "warn",
    FAIL: "fail",
    UNKNOWN: "unknown",
  };

  /** Đọc một đường dẫn dạng "a.b.c" ra khỏi payload. Trả null nếu không tồn tại. */
  function readPath(obj, path) {
    if (!obj || typeof obj !== "object") return null;
    const parts = path.split(".");
    let cur = obj;
    for (const part of parts) {
      if (cur === null || cur === undefined || typeof cur !== "object") return null;
      if (!(part in cur)) return null;
      cur = cur[part];
    }
    return cur === undefined ? null : cur;
  }

  function num(v) {
    return typeof v === "number" && Number.isFinite(v) ? v : null;
  }

  /**
   * Đã từng có chu kỳ loop thật chưa? Nguồn: activity.summary.totalRunsRecorded.
   * Không có chu kỳ ⇒ mọi số đếm "0 phát hiện"/"0 bản vá" là KHÔNG QUAN SÁT
   * ĐƯỢC (chưa chạy), không phải "kết quả tốt".
   */
  function hasObservedRun(ctx) {
    if (!ctx || !ctx.activity || !ctx.activity.summary) return false;
    const runs = num(ctx.activity.summary.totalRunsRecorded);
    return runs !== null && runs > 0;
  }

  /**
   * Registry xử lý từng loại tín hiệu.
   * Mỗi handler nhận giá trị đã đọc được và ngữ cảnh payload đầy đủ.
   */
  const SIGNAL_HANDLERS = {
    "health.fastapi": (v) => {
      if (!v || typeof v.ok !== "boolean") return { state: STATE.UNKNOWN, detail: "không có trường ok" };
      const latency = num(v.latencyMs);
      const status = v.status !== undefined && v.status !== null ? `HTTP ${v.status}` : "HTTP ?";
      return {
        state: v.ok ? STATE.OK : STATE.FAIL,
        detail: `${status}${latency !== null ? ` · ${latency}ms` : ""}`,
      };
    },
    "health.loopScheduler": (v) => {
      if (!v || typeof v.ok !== "boolean") return { state: STATE.UNKNOWN, detail: "không có trường ok" };
      const latency = num(v.latencyMs);
      return {
        state: v.ok ? STATE.OK : STATE.FAIL,
        detail: `${v.ok ? "healthz OK" : "healthz FAIL"}${latency !== null ? ` · ${latency}ms` : ""}`,
      };
    },
    "health.agentDesktop": (v) => {
      if (!v || typeof v.ok !== "boolean") return { state: STATE.UNKNOWN, detail: "không có trường ok" };
      const latency = num(v.latencyMs);
      return {
        state: v.ok ? STATE.OK : STATE.FAIL,
        detail: `noVNC ${v.ok ? "OK" : "FAIL"}${latency !== null ? ` · ${latency}ms` : ""}`,
      };
    },
    "health.llmBridge": (v) => {
      // [FIX · 2026-10-22] Cổng LLM :8081 ĐÃ BỊ XOÁ (kiến trúc Zero-Bun:
      // gateway chạy TRỰC TIẾP trong backend, không còn container/port riêng).
      // Route /api/scp/health trả về một mục "llmBridge" TỔNG HỢP (ok:true,
      // latencyMs:0, hint "Direct API-First Gateway active") — đó KHÔNG phải
      // quan sát được, mà là hằng số. Hiển thị là LIVE-OK sẽ là bịa bằng
      // chứng (FA-04) → nay trả UNKNOWN kèm lý do.
      if (!v || typeof v.ok !== "boolean") return { state: STATE.UNKNOWN, detail: "không có mục llmBridge" };
      const hint = typeof v.hint === "string" ? v.hint : "";
      const synthetic = hint.includes("Direct API-First Gateway") || (v.latencyMs === 0 && v.status === 200);
      if (synthetic) {
        return {
          state: STATE.UNKNOWN,
          detail: "gateway chạy trực tiếp trong backend — không còn cổng :8081 riêng để quan sát",
        };
      }
      return { state: v.ok ? STATE.OK : STATE.FAIL, detail: v.ok ? "Gateway sẵn sàng" : "Gateway không sẵn sàng" };
    },
    "status.scpHealth.task_kernel": (v) => {
      if (v === null || v === undefined) return { state: STATE.UNKNOWN, detail: "scpHealth.task_kernel không có trong payload" };
      const text = typeof v === "object" ? JSON.stringify(v).slice(0, 90) : String(v);
      return { state: STATE.OK, detail: `task_kernel: ${text}` };
    },
    "status.audit.auditRound": (v) => {
      if (v === null || v === undefined) return { state: STATE.UNKNOWN, detail: "auditRound không có" };
      return { state: STATE.OK, detail: `Audit round ${v}` };
    },
    "status.engine.modulesWired": (v) => {
      if (!v || typeof v !== "object") return { state: STATE.UNKNOWN, detail: "modulesWired không có" };
      const v3 = num(v.v3);
      const v4 = num(v.v4);
      return {
        state: STATE.OK,
        detail: `module wired — v3 ${v3 ?? "?"}/6 · v4 ${v4 ?? "?"}/6 (metadata khai báo)`,
      };
    },
    "activity.summary.latestRunStatus": (v) => {
      if (typeof v !== "string" || !v) return { state: STATE.UNKNOWN, detail: "chưa có chu kỳ loop nào" };
      const s = v.trim().toLowerCase();
      if (s === "ok") return { state: STATE.OK, detail: `chu kỳ loop: ${v}` };
      if (s === "running") return { state: STATE.WARN, detail: `chu kỳ loop: ${v}` };
      // [FIX · 2026-10-22] Trạng thái "chưa có" (dashboard trả về khi chưa từng
      // chạy chu kỳ nào) là KHÔNG QUAN SÁT ĐƯỢC, không phải lỗi. Chỉ những
      // trạng thái lỗi đã biết mới được gán FAIL; mọi chuỗi lạ khác → UNKNOWN
      // (DNA #4: không suy diễn "hỏng" từ dữ liệu chưa hiểu rõ).
      if (["error", "failed", "fail", "errored", "timeout"].includes(s)) {
        return { state: STATE.FAIL, detail: `chu kỳ loop lỗi: ${v}` };
      }
      return { state: STATE.UNKNOWN, detail: `trạng thái chu kỳ chưa phân loại được: "${v}"` };
    },
    "activity.summary.latestFindingsCount": (v, ctx) => {
      const n = num(v);
      if (n === null) return { state: STATE.UNKNOWN, detail: "chưa có số liệu phát hiện" };
      // [FIX · 2026-10-22] "0 phát hiện" chỉ có nghĩa là "0 phát hiện" khi đã
      // CÓ một chu kỳ loop thật. Nếu chưa từng chạy (totalRunsRecorded = 0) thì
      // đây là "không quan sát được" → UNKNOWN, KHÔNG phải OK.
      if (!hasObservedRun(ctx)) {
        return { state: STATE.UNKNOWN, detail: "chưa có chu kỳ loop nào để quan sát" };
      }
      return {
        state: n > 0 ? STATE.WARN : STATE.OK,
        detail: `${n} phát hiện trong chu kỳ loop gần nhất (chờ người xem)`,
      };
    },
    "activity.summary.latestFixesCount": (v, ctx) => {
      const n = num(v);
      if (n === null) return { state: STATE.UNKNOWN, detail: "chưa có số liệu bản vá" };
      if (!hasObservedRun(ctx)) {
        return { state: STATE.UNKNOWN, detail: "chưa có chu kỳ loop nào để quan sát" };
      }
      return { state: STATE.OK, detail: `${n} bản vá đã áp dụng` };
    },
    "metrics.cpuPercent": (v) => {
      const n = num(v);
      if (n === null) return { state: STATE.UNKNOWN, detail: "không đọc được cpuPercent" };
      return { state: n > 85 ? STATE.WARN : STATE.OK, detail: `CPU ${n}%` };
    },
    "metrics.memoryPercent": (v) => {
      const n = num(v);
      if (n === null) return { state: STATE.UNKNOWN, detail: "không đọc được memoryPercent" };
      return { state: n > 90 ? STATE.WARN : STATE.OK, detail: `RAM ${n}%` };
    },
  };

  /** Trạng thái tổng thể của các dịch vụ THẬT SỰ được probe. */
  function deriveOverall(health) {
    if (!health || typeof health !== "object") {
      return { state: STATE.UNKNOWN, detail: "không nhận được /api/scp/health", source: "health" };
    }
    const observed = REAL_PROBED_SERVICES.filter((s) => health[s] && typeof health[s].ok === "boolean");
    if (observed.length === 0) {
      return { state: STATE.UNKNOWN, detail: "health không có trường ok cho dịch vụ nào", source: "health.*.ok" };
    }
    const up = observed.filter((s) => health[s].ok === true).length;
    if (up === observed.length && observed.length === REAL_PROBED_SERVICES.length) {
      return { state: STATE.OK, detail: `cả ${REAL_PROBED_SERVICES.length} dịch vụ được probe trả lời OK`, source: "health.*.ok" };
    }
    if (up === observed.length) {
      return { state: STATE.WARN, detail: `${up}/${observed.length} dịch vụ quan sát được đang OK`, source: "health.*.ok" };
    }
    return { state: STATE.FAIL, detail: `${up}/${observed.length} dịch vụ quan sát được đang OK`, source: "health.*.ok" };
  }

  /**
   * Đếm cổng dịch vụ đang sống.
   *
   * [FIX · 2026-10-22] Chỉ tính các cổng THẬT SỰ được probe:
   * fastapi (:8000), loop-scheduler (:3030), noVNC (:6080) + chính dashboard
   * (:3000, nơi phục vụ trang này qua proxy). Mục `llmBridge` trong payload
   * health là HẰNG SỐ tổng hợp (đã xoá cổng :8081 từ kiến trúc Zero-Bun) nên
   * KHÔNG được tính là một cổng đang sống — nếu tính thì "ports live" sẽ bị
   * thổi phồng (bịa bằng chứng).
   */
  const REAL_PROBED_SERVICES = ["fastapi", "loopScheduler", "agentDesktop"];

  /** Đếm cổng dịch vụ đang sống. `reachable=false` → null (không quan sát được), KHÔNG phải 0. */
  function derivePorts(health, reachable) {
    const total = REAL_PROBED_SERVICES.length + 1; // + dashboard :3000 (qua proxy)
    if (reachable === false) {
      return { live: null, total, detail: "không kết nối được tới dashboard :3000", source: "upstream.reachable" };
    }
    if (!health || typeof health !== "object") {
      return { live: null, total, detail: "chưa quan sát được health", source: "health.*.ok" };
    }
    const observed = REAL_PROBED_SERVICES.filter((s) => health[s] && typeof health[s].ok === "boolean");
    if (observed.length === 0) {
      return { live: null, total, detail: "chưa quan sát được dịch vụ nào", source: "health.*.ok" };
    }
    // Dashboard :3000 chính là nơi phục vụ trang này qua proxy → quan sát được là đang sống.
    const live = 1 + observed.filter((s) => health[s].ok === true).length;
    return {
      live,
      total,
      detail: `${live}/${total} cổng thật trả lời (${REAL_PROBED_SERVICES.length} dịch vụ được probe + dashboard :3000)`,
      source: "health.*.ok + upstream.reachable",
    };
  }

  /** Sự kiện lỗi đang chờ người xử lý (chỉ đọc từ activity.events). */
  function deriveNeedsReview(activity) {
    if (!activity || !Array.isArray(activity.events)) {
      return { count: null, detail: "chưa quan sát được activity", source: "activity.events" };
    }
    const errors = activity.events.filter((e) => e && e.status === "error");
    const warnings = activity.events.filter((e) => e && e.status === "warning");
    const parts = [];
    if (errors.length) parts.push(`${errors.length} lỗi`);
    if (warnings.length) parts.push(`${warnings.length} cảnh báo`);
    return {
      count: errors.length + warnings.length,
      detail: parts.length ? parts.join(" · ") : "không có lỗi/cảnh báo trong luồng sự kiện",
      source: "activity.events[].status",
    };
  }

  /**
   * Hàm chính: snapshot (đã gom 4 endpoint) → trạng thái hiển thị.
   * @param {object} snapshot {status, health, activity, metrics, fetchedAt, upstream, errors}
   * @param {object[]} rooms danh sách phòng (từ data.js) để ánh xạ tín hiệu
   */
  function deriveRuntimeState(snapshot, rooms) {
    const snap = snapshot && typeof snapshot === "object" ? snapshot : {};
    const health = snap.health && typeof snap.health === "object" ? snap.health : null;
    const status = snap.status && typeof snap.status === "object" ? snap.status : null;
    const activity = snap.activity && typeof snap.activity === "object" ? snap.activity : null;
    const metrics = snap.metrics && typeof snap.metrics === "object" ? snap.metrics : null;
    const upstreamReachable = snap.upstream ? snap.upstream.reachable !== false : null;

    const hasAnyData = Boolean(health || status || activity || metrics);
    // fresh = ta vừa quan sát được upstream trong lần gọi này.
    // Khi KHÔNG fresh, mọi số liệu runtime trở về unknown — không hiển thị dữ
    // liệu cũ như hiện tại (DNA #22: PASS ≠ TRUE; số cũ ≠ số hiện hành).
    const fresh = upstreamReachable !== false;
    const mode = !hasAnyData ? "MOCK" : fresh ? "LIVE" : "STALE";

    const staleNote = "không quan sát được: upstream không trả lời";
    const overall = fresh
      ? deriveOverall(health)
      : { state: STATE.UNKNOWN, detail: staleNote, source: "upstream.reachable" };
    const ports = derivePorts(health, upstreamReachable);
    const needsReview = fresh
      ? deriveNeedsReview(activity)
      : { count: null, detail: staleNote, source: "upstream.reachable" };

    // Hàng đợi duyệt của con người (đọc live từ /api/scp/permissions).
    // count = null nghĩa là KHÔNG đọc được — khác hẳn 0 (không có gì chờ).
    const permissions = snap.permissions && typeof snap.permissions === "object" ? snap.permissions : null;
    const pendingApproval = permissions && typeof permissions.count === "number" ? permissions.count : null;

    const roomStates = {};
    (rooms || []).forEach((room) => {
      const signals = room.liveSignals || [];
      if (!signals.length) {
        roomStates[room.id] = {
          state: STATE.UNKNOWN,
          detail: room.liveLabel || "chưa có tín hiệu runtime",
          source: null,
          kind: "none",
        };
        return;
      }
      if (!fresh) {
        roomStates[room.id] = {
          state: STATE.UNKNOWN,
          detail: staleNote,
          source: signals.join(", "),
          kind: "stale",
        };
        return;
      }
      let best = null;
      const parts = [];
      const ctx = { health, status, activity, metrics };
      for (const path of signals) {
        const raw = readPath(ctx, path);
        const handler = SIGNAL_HANDLERS[path];
        const resolved = handler
          ? handler(raw, ctx)
          : {
              state: raw === null ? STATE.UNKNOWN : STATE.OK,
              detail: raw === null ? "không có giá trị" : String(raw),
            };
        parts.push(`${path} → ${resolved.detail}`);
        // [FIX · 2026-10-22] Thứ tự ưu tiên: FAIL > WARN > OK > UNKNOWN.
        // Trước đây UNKNOWN "thắng" OK ⇒ một phòng đã quan sát được dịch vụ
        // đang sống vẫn hiện UNKNOWN chỉ vì một tín hiệu phụ chưa có dữ liệu.
        // Nay UNKNOWN chỉ thắng khi KHÔNG có tín hiệu dương nào.
        const rank = { [STATE.FAIL]: 3, [STATE.WARN]: 2, [STATE.OK]: 1, [STATE.UNKNOWN]: 0 };
        if (!best || rank[resolved.state] > rank[best.state]) best = resolved;
      }
      roomStates[room.id] = {
        state: best ? best.state : STATE.UNKNOWN,
        detail: parts.join(" · "),
        source: signals.join(", "),
        kind: "observed",
      };
    });

    const counters = {
      portsLive: ports.live,
      portsTotal: ports.total,
      needsReview: needsReview.count,
      servicesObserved: fresh && health
        ? REAL_PROBED_SERVICES.filter((s) => health[s] && typeof health[s].ok === "boolean").length
        : null,
      latestRunStatus: fresh && activity && activity.summary ? activity.summary.latestRunStatus ?? null : null,
      latestFindings: fresh && activity && activity.summary ? activity.summary.latestFindingsCount ?? null : null,
      latestFixes: fresh && activity && activity.summary ? activity.summary.latestFixesCount ?? null : null,
      totalRunsRecorded: fresh && activity && activity.summary ? activity.summary.totalRunsRecorded ?? null : null,
      cpuPercent: fresh && metrics ? num(metrics.cpuPercent) : null,
      memoryPercent: fresh && metrics ? num(metrics.memoryPercent) : null,
      engineRelease: fresh && status && status.engine ? status.engine.release ?? null : null,
      engineVersion: fresh && status && status.engine ? status.engine.version ?? null : null,
      modelId: fresh && status && status.engine ? status.engine.modelId ?? null : null,
      auditRound: fresh && status && status.audit ? status.audit.auditRound ?? null : null,
      v4Loc: fresh && status && status.engine ? num(status.engine.v4Loc) : null,
      v4LocLive: fresh && status && status.engine ? status.engine.v4LocLive === true : null,
    };

    // [FIX · 2026-10-22] Trạng thái phải xác nhận rõ: chỉ khi ĐỌC ĐƯỢC dữ
    // liệu SCP ở lần gọi này thì là LIVE; còn lại (không đọc được / đọc được
    // nhưng đã cũ) thì là MÔ PHỎNG — không bao giờ trộn hai thứ.
    const isLive = mode === "LIVE";
    const observedRooms = Object.values(roomStates).filter(
      (r) => r.state === STATE.OK || r.state === STATE.WARN || r.state === STATE.FAIL
    ).length;

    const stateText = isLive
      ? "LIVE — đang đọc dữ liệu SCP thật"
      : mode === "STALE"
        ? "MÔ PHỎNG — dữ liệu SCP đã cũ (không đọc mới được)"
        : "MÔ PHỎNG — không đọc được dữ liệu SCP";

    return {
      mode,
      isLive,
      stateText,
      observedRooms,
      totalRooms: (rooms || []).length,
      fresh,
      fetchedAt: snap.fetchedAt ?? null,
      upstream: snap.upstream ?? null,
      cached: Boolean(snap.upstream && snap.upstream.cached),
      cacheAgeMs: snap.upstream && typeof snap.upstream.ageMs === "number" ? snap.upstream.ageMs : null,
      errors: snap.errors ?? {},
      overall,
      ports,
      needsReview,
      pendingApproval,
      counters,
      // Payload thô đã lọc: chỉ truyền xuống UI để hiển thị số liệu SCP thật.
      health: fresh ? health : null,
      modulesWired: fresh && status && status.engine ? status.engine.modulesWired ?? null : null,
      rooms: roomStates,
    };
  }

  return { STATE, readPath, deriveRuntimeState, deriveOverall, derivePorts, SIGNAL_HANDLERS };
});
