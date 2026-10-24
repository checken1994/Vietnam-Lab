/**
 * ui.js — Lớp giao diện DOM cho bản đồ LIVE của SCP.
 *
 * Chế độ LIVE-ONLY: không có nhân vật, không có tác vụ mô phỏng, không có
 * nhật ký giả. Sidebar liệt kê 12 phòng (hệ thống thật) kèm tín hiệu runtime
 * quan sát được và các module thật của repo. Nhật ký chỉ chứa sự kiện LIVE.
 *
 * AN TOÀN: mọi chuỗi nội suy vào HTML đều đi qua escapeHtml().
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.ScpUI = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const STATE_META = {
    ok: { label: "LIVE-OK", color: "#22c55e", text: "#bbf7d0" },
    warn: { label: "LIVE-WARN", color: "#f59e0b", text: "#fde68a" },
    fail: { label: "LIVE-FAIL", color: "#ef4444", text: "#fecaca" },
    unknown: { label: "UNKNOWN", color: "#64748b", text: "#cbd5e1" },
  };

  function escapeHtml(value) {
    return String(value === null || value === undefined ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function byId(id) {
    return document.getElementById(id);
  }

  function createUI(options) {
    const { data, sim } = options;
    const { ROOMS, AGENTS, ACCOUNTS } = data;

    let latestLive = null;
    let selectedRoomId = null;
    let activeGroup = "all";
    let selectedComponent = null;

    /* ------------------------------------------------------------------ *
     * Thẻ số liệu SCP thật
     * ------------------------------------------------------------------ */
    function accountBadgeHTML(account) {
      const acc = ACCOUNTS[account];
      const cls = acc ? acc.cssClass : "acc-W";
      return `<span class="mini-acc-badge ${cls}" title="${escapeHtml(
        acc ? acc.label : account
      )}">${escapeHtml(account)}</span>`;
    }

    function updateTopStats(derived) {
      const set = (id, value) => {
        const el = byId(id);
        if (el) el.textContent = String(value);
      };
      set("statRooms", ROOMS.length);
      const badge = byId("sidebarRoomCount");
      if (badge) badge.textContent = `${ROOMS.length} phòng`;
    }

    /** Cập nhật thẻ "Trạng thái dữ liệu" + "Cổng dịch vụ" + "SCP runtime". */
    function updateLivePanel(derived) {
      latestLive = derived;
      if (!derived) return;

      const set = (id, value) => {
        const el = byId(id);
        if (el) el.textContent = value;
      };

      /* ---- Xác nhận trạng thái: LIVE (đọc dữ liệu SCP thật) vs MÔ PHỎNG ---- */
      const badge = byId("liveModeBadge");
      const modeMeta = derived.isLive
        ? { text: "LIVE — đọc dữ liệu SCP thật", color: "#10b981" }
        : derived.mode === "STALE"
          ? { text: "MÔ PHỎNG — dữ liệu SCP đã cũ", color: "#f59e0b" }
          : { text: "MÔ PHỎNG — không đọc được SCP", color: "#f59e0b" };
      if (badge) {
        badge.textContent = modeMeta.text;
        badge.style.color = modeMeta.color;
        badge.style.borderColor = modeMeta.color;
      }

      const overallEl = byId("liveOverall");
      if (overallEl) {
        const meta = STATE_META[derived.overall.state] || STATE_META.unknown;
        overallEl.textContent =
          derived.overall.state === "ok"
            ? "MỌI DỊCH VỤ ĐANG PHỤC VỤ"
            : derived.overall.state === "fail"
              ? "CÓ DỊCH VỤ ĐANG LỖI"
              : derived.overall.state === "warn"
                ? "SUY GIẢM"
                : "CHƯA QUAN SÁT ĐƯỢC";
        overallEl.style.color = meta.color;
      }
      set("liveOverallDetail", derived.overall.detail);
      set("liveCpu", derived.counters.cpuPercent === null ? "—" : `${derived.counters.cpuPercent}%`);
      set("liveMemory", derived.counters.memoryPercent === null ? "—" : `${derived.counters.memoryPercent}%`);

      const up = derived.upstream || {};
      const parts = [];
      parts.push(derived.isLive ? "ĐÃ XÁC NHẬN LIVE" : "CHƯA XÁC NHẬN LIVE");
      parts.push(`nguồn: ${up.base || "/api/scp/*"}`);
      parts.push(`tín hiệu quan sát được: ${derived.observedRooms}/${derived.totalRooms} phòng`);
      if (derived.cached && derived.cacheAgeMs !== null) {
        parts.push(`cache ${Math.round(derived.cacheAgeMs / 1000)}s`);
      }
      set("liveConfirmation", parts.join(" · "));
      set("liveSource", `trường: ${derived.overall.source}`);
      const ts = derived.fetchedAt ? new Date(derived.fetchedAt) : null;
      set(
        "liveUpdatedAt",
        ts && !Number.isNaN(ts.getTime())
          ? `đọc lúc ${ts.toLocaleTimeString("vi-VN")}`
          : "chưa đọc được dữ liệu nào"
      );
      set("liveError", derived.errors && derived.errors.client ? `lỗi: ${derived.errors.client}` : "");

      /* ---- Thẻ Cổng dịch vụ ---- */
      const health = derived.health || null;
      const portCell = (el, service, label) => {
        const node = byId(el);
        if (!node) return;
        if (!health || !health[service] || typeof health[service].ok !== "boolean") {
          node.textContent = "—";
          node.style.color = "#64748b";
          return;
        }
        const s = health[service];
        const latency = typeof s.latencyMs === "number" ? ` · ${s.latencyMs}ms` : "";
        node.textContent = `${s.ok ? "OK" : "LỖI"}${latency}`;
        node.style.color = s.ok ? "#34d399" : "#ef4444";
        node.title = `${label} — HTTP ${s.status ?? "?"}`;
      };
      portCell("portFastapi", "fastapi", "API Server :8000");
      portCell("portLoop", "loopScheduler", "Loop Scheduler :3030");
      portCell("portDesktop", "agentDesktop", "noVNC :6080");
      const dashNode = byId("portDashboard");
      if (dashNode) {
        dashNode.textContent = derived.isLive ? "OK · qua proxy" : "—";
        dashNode.style.color = derived.isLive ? "#34d399" : "#64748b";
      }
      set("portsNote", derived.ports.detail);

      /* ---- Thẻ SCP runtime ---- */
      const c = derived.counters;
      set("scpRelease", c.engineRelease || "—");
      set("scpModel", c.modelId || c.engineVersion || "—");
      set("scpAuditRound", c.auditRound === null || c.auditRound === undefined ? "—" : `R${c.auditRound}`);
      const wired = derived.modulesWired;
      set("scpModulesWired", wired ? `v3 ${wired.v3 ?? "?"}/6 · v4 ${wired.v4 ?? "?"}/6` : "—");
      set(
        "scpV4Loc",
        c.v4Loc === null || c.v4Loc === undefined ? "—" : `${c.v4Loc}${c.v4LocLive ? "" : " (fallback)"}`
      );

      /* ---- Bộ đếm trên thanh trên cùng ---- */
      const terminals = byId("statTerminals");
      if (terminals) {
        terminals.textContent = derived.ports.live === null ? "—" : String(derived.ports.live);
        terminals.style.color = derived.ports.live === null ? "#94a3b8" : "#34d399";
      }
      const human = byId("statHumanReview");
      if (human) {
        human.textContent = derived.needsReview.count === null ? "—" : String(derived.needsReview.count);
        human.style.color = derived.needsReview.count === null ? "#94a3b8" : "#fbbf24";
      }
      // Số yêu cầu đang CHỜ CON NGƯỜI DUYỆT (live) — tách bạch với cảnh báo runtime.
      const pending = byId("statPendingApproval");
      if (pending) {
        pending.textContent = derived.pendingApproval === null ? "—" : String(derived.pendingApproval);
        pending.style.color =
          derived.pendingApproval === null
            ? "#94a3b8"
            : derived.pendingApproval > 0
              ? "#c084fc"
              : "#34d399";
      }

      renderSidebar();
      if (selectedRoomId) updateInspector(selectedRoomId);
    }

    /* ------------------------------------------------------------------ *
     * Sidebar: 12 phòng (hệ thống thật) + tín hiệu runtime
     * ------------------------------------------------------------------ */
    function liveChipHTML(roomId) {
      const state = latestLive && latestLive.rooms ? latestLive.rooms[roomId] : null;
      if (!state) return "";
      const meta = STATE_META[state.state] || STATE_META.unknown;
      return `<span class="live-chip" style="color:${meta.color};border-color:${meta.color}55;background:${meta.color}14" title="${escapeHtml(
        state.detail
      )}">● ${escapeHtml(meta.label)}</span>`;
    }

    function renderSidebar(groupFilter) {
      const container = byId("projectTreeList");
      if (!container) return;
      const q = (options.getSearchQuery ? options.getSearchQuery() : "").trim().toLowerCase();
      const group = groupFilter || activeGroup;

      const htmlParts = [];
      ROOMS.forEach((room) => {
        const roomAgents = AGENTS.filter((a) => a.roomId === room.id);
        if (group !== "all" && !roomAgents.some((a) => a.account === group)) return;
        if (q) {
          const hay = `${room.name} ${room.codeName} ${room.path} ${roomAgents
            .map((a) => `${a.name} ${a.skill} ${a.role}`)
            .join(" ")}`.toLowerCase();
          if (!hay.includes(q)) return;
        }
        const live = latestLive && latestLive.rooms ? latestLive.rooms[room.id] : null;
        // Nhân vật = thành phần THẬT của hệ thống, kèm trạng thái runtime hiện tại.
        const componentRows = roomAgents
          .map((a) => {
            const s = sim && sim.agents3d ? sim.agents3d[a.id] : null;
            const busy = !!(s && s.busy);
            const detail = busy && s.bubble ? s.bubble : a.role;
            return `
            <div class="component-row ${selectedComponent && selectedComponent.path === a.skill ? "component-active" : ""}" data-agent-id="${escapeHtml(a.id)}" title="${escapeHtml(a.skill)}">
              ${accountBadgeHTML(a.account)}
              <span class="component-name">${escapeHtml(a.avatar)} ${escapeHtml(a.name)}</span>
              <span class="component-path">${escapeHtml(detail)}</span>
              ${busy ? '<span class="agent-busy" title="đang có hoạt động runtime">●</span>' : ""}
            </div>`;
          })
          .join("");

        htmlParts.push(`
          <div class="room-group-card ${selectedRoomId === room.id ? "room-selected" : ""}" data-room-id="${escapeHtml(room.id)}" role="button" tabindex="0">
            <div class="flex items-center justify-between">
              <div class="flex items-center gap-1.5 min-w-0">
                <span class="truncate room-code">${escapeHtml(room.codeName)}</span>
                ${liveChipHTML(room.id)}
              </div>
            </div>
            <div class="font-mono-code truncate room-path">${escapeHtml(room.path)}</div>
            <div class="room-live-detail">${escapeHtml(live ? live.detail : room.liveLabel)}</div>
            <div class="component-list">${componentRows || '<div class="empty-room">—</div>'}</div>
          </div>`);
      });

      container.innerHTML = htmlParts.join("");
      container.querySelectorAll(".room-group-card").forEach((card) => {
        const activate = () => selectRoom(card.getAttribute("data-room-id"));
        card.addEventListener("click", activate);
        card.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            activate();
          }
        });
      });
    }

    function selectRoom(roomId) {
      selectedRoomId = roomId;
      renderSidebar();
      updateInspector(roomId);
    }

    /** Lọc theo nhóm module (S/P/G/W). */
    function setFilterGroup(group) {
      activeGroup = group || "all";
      renderSidebar();
    }

    /* ------------------------------------------------------------------ *
     * Inspector: chi tiết một phòng (hệ thống thật)
     * ------------------------------------------------------------------ */
    function updateInspector(roomId) {
      const drawer = byId("inspectorDrawer");
      const room = ROOMS.find((r) => r.id === roomId);
      if (!room) {
        if (drawer) drawer.classList.add("hidden");
        return;
      }
      if (drawer) drawer.classList.remove("hidden");

      const set = (id, value) => {
        const el = byId(id);
        if (el) el.textContent = value;
      };

      const live = latestLive && latestLive.rooms ? latestLive.rooms[room.id] : null;
      const meta = live ? STATE_META[live.state] || STATE_META.unknown : STATE_META.unknown;

      set("inspAvatar", "🏢");
      set("inspName", room.name);
      set("inspAccountBadge", live ? meta.label : "UNKNOWN");
      const badge = byId("inspAccountBadge");
      if (badge) {
        badge.style.color = meta.color;
        badge.style.borderColor = meta.color;
      }
      set("inspRole", room.codeName);
      set("inspTask", live ? live.detail : room.liveLabel);
      set("inspRoom", room.path);
      set("inspSkill", (room.liveSignals || []).join(", ") || "chưa có tín hiệu runtime công khai");
      set("inspDesc", live ? `Nguồn: ${live.source || "—"}` : room.liveLabel);
      const list = byId("inspComponentList");
      if (list) {
        const roomAgents = AGENTS.filter((a) => a.roomId === room.id);
        list.innerHTML = roomAgents
          .map((a) => {
            const s = sim && sim.agents3d ? sim.agents3d[a.id] : null;
            const busy = !!(s && s.busy);
            return `
            <div class="component-row ${selectedComponent && selectedComponent.path === a.skill ? "component-active" : ""}">
              ${accountBadgeHTML(a.account)}
              <span class="component-name">${escapeHtml(a.avatar)} ${escapeHtml(a.name)}</span>
              <span class="component-path">${escapeHtml(a.skill)}</span>
              ${busy ? '<span class="agent-busy" title="đang có hoạt động runtime">●</span>' : ""}
            </div>`;
          })
          .join("");
      }
    }

    /** Chọn một module/skill cụ thể (bấm vào node trên bản đồ). */
    function selectComponent(component) {
      selectedComponent = component;
      updateInspector(selectedRoomId);
    }

    /* ------------------------------------------------------------------ *
     * Nhật ký: CHỈ dữ liệu LIVE
     * ------------------------------------------------------------------ */
    const liveRows = [];

    function renderActivityLogs() {
      const list = byId("liveLogList");
      const badge = byId("logCounterBadge");
      const empty = byId("liveLogEmpty");
      if (!list) return;
      if (badge) badge.textContent = `${liveRows.length} sự kiện live`;
      list.innerHTML = liveRows
        .map(
          (item) => `
        <div class="log-row">
          <span class="log-time">${escapeHtml(item.time)}</span>
          <span class="log-origin origin-live">LIVE</span>
          <span class="log-agent">${escapeHtml(item.agentName)}</span>
          <span class="log-room">${escapeHtml(item.roomCode)}</span>
          <span class="truncate log-text">${escapeHtml(item.icon)} ${escapeHtml(item.text)}</span>
        </div>`
        )
        .join("");
      list.scrollTop = list.scrollHeight;
      if (empty) empty.classList.toggle("hidden", liveRows.length > 0);
    }

    function pushLiveLog(entry) {
      liveRows.push(entry);
      if (liveRows.length > 60) liveRows.shift();
      renderActivityLogs();
    }

    /* ------------------------------------------------------------------ *
     * Trợ giúp
     * ------------------------------------------------------------------ */
    let toastTimer = null;
    function toast(message) {
      const el = byId("toastHost");
      if (!el) return;
      el.textContent = message;
      el.classList.add("toast-show");
      if (toastTimer) window.clearTimeout(toastTimer);
      toastTimer = window.setTimeout(() => el.classList.remove("toast-show"), 3200);
    }

    function openModal(id) {
      const el = byId(id);
      if (el) el.classList.remove("hidden");
    }

    function closeModal(id) {
      const el = byId(id);
      if (el) el.classList.add("hidden");
    }

    function setZoomLabel(zoom) {
      const lbl = byId("zoomLabel");
      if (lbl) lbl.textContent = `${Math.round(zoom * 100)}%`;
    }

    function initProfileModal() {
      const host = byId("profileRoomList");
      if (!host) return;
      host.innerHTML = ROOMS.map(
        (r, idx) => `
        <div class="profile-room-row">
          <span class="profile-room-idx">${idx + 1}.</span>
          <span class="profile-room-name" style="color:${escapeHtml(r.accent)};">${escapeHtml(r.codeName)}</span>
          <span class="profile-room-path">${escapeHtml(r.path)}</span>
        </div>`
      ).join("");
      const counts = byId("profileCounts");
      if (counts) counts.textContent = `${ROOMS.length} phòng · ${ROOMS.reduce((n, r) => n + (r.components || []).length, 0)} module/skill thật`;
    }

    return {
      escapeHtml,
      renderSidebar,
      renderActivityLogs,
      pushLiveLog,
      updateInspector,
      updateTopStats,
      updateLivePanel,
      initProfileModal,
      selectRoom,
      selectComponent,
      setFilterGroup,
      toast,
      openModal,
      closeModal,
      setZoomLabel,
      get latestLive() {
        return latestLive;
      },
      get selectedRoomId() {
        return selectedRoomId;
      },
    };
  }

  return { createUI, escapeHtml, STATE_META };
});
