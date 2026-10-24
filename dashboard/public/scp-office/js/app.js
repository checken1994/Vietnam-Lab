/**
 * app.js — Điểm khởi động của bản đồ LIVE.
 *
 * Trang này KHÔNG có dữ liệu mô phỏng: 24 "nhân vật" là các thành phần THẬT
 * của repo (tên, skill, vai trò đều thật); chúng chỉ di chuyển theo SỰ KIỆN
 * RUNTIME THẬT đọc từ /api/scp/activity. Nhật ký chỉ chứa sự kiện live.
 */
(function () {
  "use strict";

  const data = window.ScpData;
  const Render = window.ScpRender;
  const UI = window.ScpUI;
  const Live = window.ScpLive;
  const Sim = window.ScpSim;

  if (!data || !Render || !UI || !Live || !Sim) {
    document.body.insertAdjacentHTML(
      "afterbegin",
      '<div style="padding:24px;font-family:sans-serif;color:#fecaca;background:#450a0a">Không nạp được các module của ứng dụng. Hãy mở qua máy chủ: <code>npm start</code> trong thư mục <code>simulation/</code>.</div>'
    );
    return;
  }

  const canvas = document.getElementById("officeCanvas");

  /* Trạng thái giao diện (chỉ camera + module đang chọn) — không có trạng thái mô phỏng. */
  const view = { zoom: 0.78, panX: 100, panY: 30, isDragging: false, dragStartX: 0, dragStartY: 0 };
  let selectedComponent = null;

  /* ------------------------------------------------------------------ *
   * Động cơ chuyển động (chỉ đi theo sự kiện runtime thật)
   * ------------------------------------------------------------------ */
  const sim = Sim.createSimulation({
    rooms: data.ROOMS,
    agents: data.AGENTS,
    serviceRooms: data.SERVICE_ROOMS,
    onChange: () => ui.renderSidebar(),
  });

  /* ------------------------------------------------------------------ *
   * Lớp UI
   * ------------------------------------------------------------------ */
  const ui = UI.createUI({
    data,
    sim,
    getSearchQuery: () => (document.getElementById("searchInput") || {}).value || "",
  });

  /* ------------------------------------------------------------------ *
   * Bộ vẽ
   * ------------------------------------------------------------------ */
  const renderer = Render.createRenderer(canvas, {
    rooms: data.ROOMS,
    agents: data.AGENTS,
    agents3d: sim.agents3d,
    getLiveState: (roomId) => (ui.latestLive && ui.latestLive.rooms ? ui.latestLive.rooms[roomId] : null),
    getSelectedComponent: () => (selectedComponent ? selectedComponent.path : null),
    getZoom: () => view.zoom,
    getPan: () => ({ x: view.panX, y: view.panY }),
    setZoom: (z) => {
      view.zoom = z;
      ui.setZoomLabel(z);
    },
    setPan: (x, y) => {
      view.panX = x;
      view.panY = y;
    },
    onNeedFit: () => renderer.fitMapToScreen(),
  });

  /* ------------------------------------------------------------------ *
   * Kênh dữ liệu runtime thật (chỉ đọc)
   * ------------------------------------------------------------------ */
  const live = Live.createClient({
    baseUrl: "/api/live",
    intervalMs: 10000,
    rooms: data.ROOMS,
    onUpdate: (derived) => {
      ui.updateLivePanel(derived);
      appendLiveEvents(derived);
      // [Sự kiện thật → nhân vật đi theo] Các thành phần liên quan tới sự kiện
      // runtime vừa tới sẽ đi tới phòng đang có việc và hiện thông điệp thật.
      const activity = live.client.lastPayload && live.client.lastPayload.activity;
      if (activity && Array.isArray(activity.events)) sim.applyLiveEvents(activity.events);
    },
    onError: (err, derived) => {
      ui.updateLivePanel(derived);
      const el = document.getElementById("liveError");
      if (el) el.textContent = err ? `lỗi đọc runtime: ${err}` : "";
    },
  });

  /** Nạp sự kiện LIVE vào nhật ký kiểm toán (bỏ qua sự kiện đã thấy). */
  const seenLiveEventIds = new Set();
  function appendLiveEvents(derived) {
    if (!derived || !derived.isLive) return;
    const activity = live.client.lastPayload && live.client.lastPayload.activity;
    if (!activity || !Array.isArray(activity.events)) return;
    let added = 0;
    for (const ev of activity.events) {
      if (!ev || !ev.id || seenLiveEventIds.has(ev.id)) continue;
      seenLiveEventIds.add(ev.id);
      ui.pushLiveLog({
        time: ev.timestamp ? new Date(ev.timestamp).toTimeString().split(" ")[0] : "--:--:--",
        agentName: ev.service || "scp",
        roomCode: ev.status === "error" ? "lỗi" : ev.status === "warning" ? "cảnh báo" : "ok",
        icon: ev.status === "error" ? "⛔" : ev.status === "warning" ? "⚠️" : "✅",
        text: ev.message || "",
      });
      added += 1;
      if (added >= 20) break;
    }
  }

  /* ------------------------------------------------------------------ *
   * Tương tác chuột / cảm ứng trên canvas
   * ------------------------------------------------------------------ */
  function worldPointFromEvent(clientX, clientY) {
    const rect = canvas.getBoundingClientRect();
    return {
      x: (clientX - rect.left - view.panX) / view.zoom,
      y: (clientY - rect.top - view.panY) / view.zoom,
    };
  }

  function roomAt(worldX, worldY) {
    for (const room of data.ROOMS) {
      const r = Render.getRoomRect(room);
      if (worldX >= r.x && worldX <= r.x + r.w && worldY >= r.y && worldY <= r.y + r.h) return room.id;
    }
    return null;
  }

  /** Nhân vật (thành phần thật) được bấm — dùng để mở chi tiết trong inspector. */
  function agentAt(worldX, worldY) {
    for (const agent of data.AGENTS) {
      const s = sim.agents3d[agent.id];
      if (!s) continue;
      if (Math.hypot(worldX - s.x, worldY - (s.y - 4)) < 20) {
        return { roomId: agent.roomId, agent, path: agent.skill, name: agent.name };
      }
    }
    return null;
  }

  canvas.addEventListener("mousedown", (e) => {
    const p = worldPointFromEvent(e.clientX, e.clientY);
    const picked = agentAt(p.x, p.y);
    if (picked) {
      selectedComponent = { path: picked.path, name: picked.name };
      ui.selectRoom(picked.roomId);
      ui.selectComponent(selectedComponent);
      return;
    }
    const hit = roomAt(p.x, p.y);
    if (hit) {
      selectedComponent = null;
      ui.selectRoom(hit);
      return;
    }
    view.isDragging = true;
    view.dragStartX = e.clientX - view.panX;
    view.dragStartY = e.clientY - view.panY;
  });

  window.addEventListener("mousemove", (e) => {
    if (!view.isDragging) return;
    view.panX = e.clientX - view.dragStartX;
    view.panY = e.clientY - view.dragStartY;
  });

  window.addEventListener("mouseup", () => {
    view.isDragging = false;
  });

  canvas.addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      const factor = e.deltaY < 0 ? 1.1 : 0.9;
      setZoom(view.zoom * factor);
    },
    { passive: false }
  );

  let touchStart = null;
  canvas.addEventListener(
    "touchstart",
    (e) => {
      const t = e.changedTouches[0];
      const p = worldPointFromEvent(t.clientX, t.clientY);
      const hit = roomAt(p.x, p.y);
      if (hit) {
        ui.selectRoom(hit);
        touchStart = null;
        return;
      }
      touchStart = { x: t.clientX - view.panX, y: t.clientY - view.panY };
    },
    { passive: true }
  );

  canvas.addEventListener(
    "touchmove",
    (e) => {
      if (!touchStart) return;
      const t = e.changedTouches[0];
      view.panX = t.clientX - touchStart.x;
      view.panY = t.clientY - touchStart.y;
      e.preventDefault();
    },
    { passive: false }
  );

  canvas.addEventListener(
    "touchend",
    () => {
      touchStart = null;
    },
    { passive: true }
  );

  function setZoom(value) {
    const z = Math.min(2.2, Math.max(0.45, value));
    view.zoom = z;
    ui.setZoomLabel(z);
  }

  /* ------------------------------------------------------------------ *
   * Thanh điều khiển
   * ------------------------------------------------------------------ */
  function bind(id, event, handler) {
    const el = document.getElementById(id);
    if (el) el.addEventListener(event, handler);
  }

  bind("fitViewBtn", "click", () => renderer.fitMapToScreen());
  bind("zoomInBtn", "click", () => setZoom(view.zoom + 0.12));
  bind("zoomOutBtn", "click", () => setZoom(view.zoom - 0.12));

  bind("liveRefreshBtn", "click", () => {
    live.refresh().then((derived) => {
      ui.toast(
        derived.isLive
          ? `Đã đọc runtime lúc ${new Date().toLocaleTimeString("vi-VN")}`
          : "Không đọc được runtime — dữ liệu SCP không được hiển thị (không dùng số liệu mô phỏng)"
      );
    });
  });

  bind("collapseSidebarBtn", "click", () => {
    document.getElementById("leftSidebar").classList.add("collapsed-sidebar");
    document.getElementById("expandSidebarBtn").classList.remove("hidden");
  });

  bind("expandSidebarBtn", "click", () => {
    document.getElementById("leftSidebar").classList.remove("collapsed-sidebar");
    document.getElementById("expandSidebarBtn").classList.add("hidden");
  });

  bind("searchInput", "input", () => ui.renderSidebar());

  bind("toggleLogBtn", "click", (e) => {
    const list = document.getElementById("liveLogList");
    const empty = document.getElementById("liveLogEmpty");
    if (!list) return;
    const hidden = list.classList.toggle("hidden");
    if (empty) empty.classList.toggle("hidden", !hidden || list.children.length > 0);
    e.currentTarget.textContent = hidden ? "▲" : "▼";
    e.currentTarget.setAttribute("aria-expanded", String(!hidden));
  });

  bind("closeInspectorBtn", "click", () => {
    ui.selectRoom(null);
    const drawer = document.getElementById("inspectorDrawer");
    if (drawer) drawer.classList.add("hidden");
  });

  document.querySelectorAll(".filter-pill").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".filter-pill").forEach((b) => b.classList.remove("active-pill"));
      btn.classList.add("active-pill");
      ui.setFilterGroup(btn.getAttribute("data-filter"));
    });
  });

  bind("profileModalBtn", "click", () => ui.openModal("profileModal"));
  bind("closeProfileModalBtn", "click", () => ui.closeModal("profileModal"));

  /* ------------------------------------------------------------------ *
   * Bàn phím
   * ------------------------------------------------------------------ */
  window.addEventListener("keydown", (e) => {
    const active = document.activeElement;
    const typing =
      active &&
      (active.id === "searchInput" || active.tagName === "INPUT" || active.tagName === "SELECT" || active.tagName === "TEXTAREA");

    if (e.key === "Escape") {
      ui.closeModal("profileModal");
      return;
    }
    if (typing) return;

    switch (e.key) {
      case "/":
        e.preventDefault();
        document.getElementById("searchInput").focus();
        break;
      case "+":
      case "=":
        setZoom(view.zoom + 0.12);
        break;
      case "-":
      case "_":
        setZoom(view.zoom - 0.12);
        break;
      case "f":
        renderer.fitMapToScreen();
        break;
      case "ArrowLeft":
        view.panX += 40;
        break;
      case "ArrowRight":
        view.panX -= 40;
        break;
      case "ArrowUp":
        view.panY += 40;
        break;
      case "ArrowDown":
        view.panY -= 40;
        break;
      default:
        break;
    }
  });

  /* ------------------------------------------------------------------ *
   * Vòng lặp vẽ
   * ------------------------------------------------------------------ */
  function frame() {
    sim.tick(Math.min(0.05, (performance.now() - (frame.last || performance.now())) / 1000));
    frame.last = performance.now();
    renderer.drawWorld();
    window.requestAnimationFrame(frame);
  }

  /**
   * [FIX · 2026-10-22 · trang trắng] Trước đây trang CHỈ vẽ trong
   * requestAnimationFrame. Chromium KHÔNG gọi rAF cho tab nền / cửa sổ bị che
   * ⇒ mở trang ở tab nền thì canvas trắng hoàn toàn (không có lỗi JS nào) và
   * người dùng tưởng trang hỏng. Nay:
   *   1) vẽ MỘT LẦN ngay khi boot (không phụ thuộc rAF),
   *   2) vẽ lại mỗi khi tab trở lại hiển thị (visibilitychange).
   */
  function drawNow() {
    renderer.drawWorld();
  }

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) drawNow();
  });
  window.addEventListener("focus", drawNow);

  window.addEventListener("resize", () => renderer.fitMapToScreen());

  /* ------------------------------------------------------------------ *
   * Khởi động
   * ------------------------------------------------------------------ */
  (function boot() {
    ui.initProfileModal();
    ui.updateTopStats(null);
    ui.renderSidebar();
    ui.setZoomLabel(view.zoom);
    renderer.fitMapToScreen();
    drawNow(); // vẽ ngay lần đầu — không phụ thuộc requestAnimationFrame
    window.requestAnimationFrame(frame);
    live.start(); // kênh runtime thật (chỉ đọc)
  })();
})();
