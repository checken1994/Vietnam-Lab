/**
 * render.js — Bộ vẽ bản đồ 2D (Canvas) cho 12 phòng ban của SCP.
 *
 * Chế độ LIVE-ONLY: không còn nhân vật đi lại, không bong bóng thoại, không xe
 * chạy, không tiến trình giả. Trang vẽ chỉ cấu trúc hệ thống + trạng thái
 * runtime quan sát được. Ngày/đêm lấy từ đồng hồ thật của máy (không có nút
 * bật/tắt giả lập).
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.ScpRender = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  /** Kích thước thế giới bản đồ (px trong không gian world). */
  const WORLD = {
    buildingX: 220,
    buildingY: 75,
    roomW: 220,
    roomH: 138,
    corridorH: 46,
    cols: 4,
    rows: 3,
  };

  const TREES = [
    { x: 120, y: 70 }, { x: 165, y: 165 }, { x: 110, y: 265 }, { x: 160, y: 375 }, { x: 125, y: 490 }, { x: 175, y: 580 },
    { x: 1145, y: 70 }, { x: 1195, y: 165 }, { x: 1140, y: 270 }, { x: 1200, y: 380 }, { x: 1145, y: 490 }, { x: 1190, y: 585 },
    { x: 310, y: 35 }, { x: 540, y: 32 }, { x: 770, y: 35 }, { x: 1000, y: 32 },
  ];

  const STATE_COLORS = {
    ok: "#22c55e",
    warn: "#f59e0b",
    fail: "#ef4444",
    unknown: "#64748b",
  };

  /** Đồng hồ thật: 22:00–06:00 là ban đêm. Không có nút giả lập. */
  function isNightLocal(date) {
    const h = (date || new Date()).getHours();
    return h >= 22 || h < 6;
  }

  function getRoomRect(room) {
    return {
      x: WORLD.buildingX + room.col * WORLD.roomW,
      y: WORLD.buildingY + room.row * (WORLD.roomH + WORLD.corridorH),
      w: WORLD.roomW,
      h: WORLD.roomH,
    };
  }

  function drawRoundedRectPath(c, x, y, w, h, r) {
    const radius = Math.max(0, Math.min(r, w / 2, h / 2));
    c.beginPath();
    c.moveTo(x + radius, y);
    c.lineTo(x + w - radius, y);
    c.quadraticCurveTo(x + w, y, x + w, y + radius);
    c.lineTo(x + w, y + h - radius);
    c.quadraticCurveTo(x + w, y + h, x + w - radius, y + h);
    c.lineTo(x + radius, y + h);
    c.quadraticCurveTo(x, y + h, x, y + h - radius);
    c.lineTo(x, y + radius);
    c.quadraticCurveTo(x, y, x + radius, y);
    c.closePath();
  }

  function createRenderer(canvas, options) {
    const ctx = canvas.getContext("2d");
    const rooms = options.rooms;
    const agents = options.agents || [];
    const agents3d = options.agents3d || {};
    const getLiveState = options.getLiveState || (() => null);
    let hasAutoCollapsedOnce = false;

    function syncCanvasSize(recenter) {
      const rawW = window.innerWidth || document.documentElement.clientWidth || 0;
      const rawH = window.innerHeight || document.documentElement.clientHeight || 0;
      const w = rawW > 120 ? rawW : 760;
      const h = rawH > 120 ? rawH : 680;
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      const pixelW = Math.round(w * dpr);
      const pixelH = Math.round(h * dpr);
      if (canvas.width !== pixelW || canvas.height !== pixelH) {
        canvas.width = pixelW;
        canvas.height = pixelH;
        canvas.style.width = w + "px";
        canvas.style.height = h + "px";
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        if (!hasAutoCollapsedOnce && w < 960) {
          hasAutoCollapsedOnce = true;
          const sb = document.getElementById("leftSidebar");
          const exp = document.getElementById("expandSidebarBtn");
          if (sb && exp) {
            sb.classList.add("collapsed-sidebar");
            exp.classList.remove("hidden");
          }
        }
        if (recenter && options.onNeedFit) options.onNeedFit();
      }
    }

    function fitMapToScreen() {
      syncCanvasSize(false);
      const sidebarEl = document.getElementById("leftSidebar");
      const isCollapsed = sidebarEl && sidebarEl.classList.contains("collapsed-sidebar");
      const sidebarW = isCollapsed || canvas.clientWidth < 920 ? 0 : 315;

      const availW = Math.max(300, canvas.clientWidth - sidebarW - 20);
      const availH = Math.max(280, canvas.clientHeight - 130);

      const totalW = WORLD.roomW * WORLD.cols;
      const totalH = WORLD.roomH * WORLD.rows + WORLD.corridorH * 2;
      const mapCenterX = WORLD.buildingX + totalW / 2;
      const mapCenterY = WORLD.buildingY + totalH / 2 + 20;

      const rawScale = Math.min(availW / (totalW + 110), availH / (totalH + 110));
      const zoom = Math.max(0.42, Math.min(1.25, Math.round(rawScale * 100) / 100));
      options.setZoom(zoom);
      options.setPan(
        sidebarW + (canvas.clientWidth - sidebarW) / 2 - mapCenterX * zoom,
        48 + (canvas.clientHeight - 100) / 2 - mapCenterY * zoom
      );
    }

    function drawPineTree(x, y, night) {
      ctx.fillStyle = "rgba(0,0,0,0.22)";
      ctx.beginPath();
      ctx.ellipse(x, y + 18, 14, 6, 0, 0, Math.PI * 2);
      ctx.fill();

      ctx.fillStyle = "#5c3a21";
      ctx.fillRect(x - 3, y + 4, 6, 14);

      ctx.fillStyle = night ? "#14532d" : "#2d6a3e";
      ctx.beginPath();
      ctx.arc(x, y, 14, 0, Math.PI * 2);
      ctx.fill();

      ctx.fillStyle = night ? "#166534" : "#3b854f";
      ctx.beginPath();
      ctx.arc(x - 3, y - 4, 10, 0, Math.PI * 2);
      ctx.fill();
    }

    function drawPottedPlant(x, y) {
      ctx.fillStyle = "#b45309";
      ctx.fillRect(x - 5, y, 10, 9);
      ctx.fillStyle = "#15803d";
      ctx.beginPath();
      ctx.arc(x, y - 3, 8, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = "#22c55e";
      ctx.beginPath();
      ctx.arc(x - 2, y - 5, 4.5, 0, Math.PI * 2);
      ctx.fill();
    }

    function drawDeskWithWorkstation(x, y, accentColor) {
      ctx.fillStyle = "#d4a373";
      ctx.fillRect(x, y, 44, 22);
      ctx.strokeStyle = "#8d5b4c";
      ctx.lineWidth = 1.4;
      ctx.strokeRect(x, y, 44, 22);

      ctx.fillStyle = "#1e293b";
      ctx.fillRect(x + 12, y + 3, 20, 10);
      ctx.fillStyle = accentColor || "#38bdf8";
      ctx.fillRect(x + 14, y + 5, 16, 6);

      ctx.fillStyle = "#334155";
      ctx.fillRect(x + 15, y + 24, 14, 7);
    }

    function drawServerRack(x, y, ledColor) {
      ctx.fillStyle = "#0f172a";
      ctx.fillRect(x, y, 22, 42);
      ctx.strokeStyle = "#475569";
      ctx.lineWidth = 1;
      ctx.strokeRect(x, y, 22, 42);
      ctx.fillStyle = ledColor || "#22c55e";
      ctx.fillRect(x + 4, y + 6, 14, 3);
      ctx.fillRect(x + 4, y + 14, 14, 3);
      ctx.fillRect(x + 4, y + 22, 14, 3);
      ctx.fillRect(x + 4, y + 30, 14, 3);
    }

    function drawFurniture(room, rect) {
      const ft = room.furnitureType;
      if (ft === "reception") {
        ctx.fillStyle = "#3b5998";
        ctx.fillRect(rect.x + 16, rect.y + 24, 72, 40);
        ctx.fillStyle = "#0f172a";
        ctx.fillRect(rect.x + 102, rect.y + 12, 102, 18);
        ctx.fillStyle = "#f59e0b";
        ctx.font = "bold 9.5px sans-serif";
        ctx.fillText("PORT :8000 PIPELINE", rect.x + 108, rect.y + 24);
        ctx.fillStyle = "#e6ccb2";
        ctx.fillRect(rect.x + 112, rect.y + 38, 76, 24);
        drawDeskWithWorkstation(rect.x + 24, rect.y + 82, "#f59e0b");
        drawServerRack(rect.x + 184, rect.y + 78, "#f59e0b");
        drawPottedPlant(rect.x + 16, rect.y + 22);
      } else if (ft === "kernel") {
        drawServerRack(rect.x + 16, rect.y + 18, "#38bdf8");
        drawServerRack(rect.x + 42, rect.y + 18, "#38bdf8");
        drawDeskWithWorkstation(rect.x + 82, rect.y + 26, "#38bdf8");
        drawDeskWithWorkstation(rect.x + 148, rect.y + 26, "#60a5fa");
        drawDeskWithWorkstation(rect.x + 115, rect.y + 84, "#2563eb");
        drawPottedPlant(rect.x + 22, rect.y + 112);
      } else if (ft === "scplab") {
        drawDeskWithWorkstation(rect.x + 22, rect.y + 28, "#0ea5e9");
        drawDeskWithWorkstation(rect.x + 88, rect.y + 28, "#10b981");
        drawDeskWithWorkstation(rect.x + 22, rect.y + 82, "#38bdf8");
        drawDeskWithWorkstation(rect.x + 140, rect.y + 82, "#0284c7");
        drawServerRack(rect.x + 186, rect.y + 20, "#10b981");
      } else if (ft === "security") {
        ctx.fillStyle = "#312e81";
        ctx.fillRect(rect.x + 20, rect.y + 20, 65, 36);
        ctx.strokeStyle = "#818cf8";
        ctx.strokeRect(rect.x + 20, rect.y + 20, 65, 36);
        drawDeskWithWorkstation(rect.x + 108, rect.y + 26, "#6366f1");
        drawDeskWithWorkstation(rect.x + 56, rect.y + 82, "#818cf8");
        drawServerRack(rect.x + 184, rect.y + 72, "#6366f1");
        drawPottedPlant(rect.x + 20, rect.y + 110);
      } else if (ft === "desktop") {
        ctx.fillStyle = "#1e1b4b";
        ctx.fillRect(rect.x + 24, rect.y + 14, 140, 20);
        ctx.fillStyle = "#c084fc";
        ctx.font = "bold 9px sans-serif";
        ctx.fillText("noVNC :6080 • Xvfb :99 GUI", rect.x + 34, rect.y + 27);
        drawDeskWithWorkstation(rect.x + 28, rect.y + 52, "#a855f7");
        drawDeskWithWorkstation(rect.x + 132, rect.y + 52, "#c084fc");
        drawPottedPlant(rect.x + 195, rect.y + 112);
      } else if (ft === "gateway") {
        drawServerRack(rect.x + 18, rect.y + 22, "#ec4899");
        drawDeskWithWorkstation(rect.x + 62, rect.y + 32, "#ec4899");
        drawDeskWithWorkstation(rect.x + 138, rect.y + 32, "#f43f5e");
        drawDeskWithWorkstation(rect.x + 98, rect.y + 84, "#db2777");
        drawPottedPlant(rect.x + 24, rect.y + 112);
      } else if (ft === "library") {
        ctx.fillStyle = "#064e3b";
        ctx.fillRect(rect.x + 18, rect.y + 16, 184, 18);
        ctx.fillStyle = "#6ee7b7";
        ctx.font = "bold 9px sans-serif";
        ctx.fillText("TIER 1 → TIER 2 QUARANTINE → KB_FTS", rect.x + 26, rect.y + 28);
        drawDeskWithWorkstation(rect.x + 28, rect.y + 54, "#10b981");
        drawDeskWithWorkstation(rect.x + 134, rect.y + 54, "#34d399");
        drawServerRack(rect.x + 96, rect.y + 78, "#10b981");
      } else if (ft === "sandbox") {
        ctx.fillStyle = "#134e4a";
        ctx.fillRect(rect.x + 24, rect.y + 24, 76, 52);
        ctx.strokeStyle = "#2dd4bf";
        ctx.strokeRect(rect.x + 24, rect.y + 24, 76, 52);
        ctx.fillStyle = "#99f6e4";
        ctx.font = "bold 8.5px sans-serif";
        ctx.fillText("BWRAP SANDBOX", rect.x + 29, rect.y + 52);
        drawDeskWithWorkstation(rect.x + 126, rect.y + 34, "#14b8a6");
        drawDeskWithWorkstation(rect.x + 126, rect.y + 84, "#2dd4bf");
      } else if (ft === "release") {
        ctx.fillStyle = "#881337";
        ctx.fillRect(rect.x + 20, rect.y + 16, 180, 18);
        ctx.fillStyle = "#fda4af";
        ctx.font = "bold 9px sans-serif";
        ctx.fillText("T00..T12 • GATE KIẾM PHÁT HÀNH", rect.x + 36, rect.y + 28);
        drawDeskWithWorkstation(rect.x + 30, rect.y + 54, "#f43f5e");
        drawDeskWithWorkstation(rect.x + 134, rect.y + 54, "#fb7185");
        drawPottedPlant(rect.x + 195, rect.y + 112);
      } else if (ft === "lounge") {
        ctx.fillStyle = "#3f6212";
        ctx.fillRect(rect.x + 20, rect.y + 42, 88, 64);
        ctx.fillStyle = "#2563eb";
        ctx.fillRect(rect.x + 30, rect.y + 48, 54, 14);
        ctx.fillRect(rect.x + 30, rect.y + 84, 54, 14);
        ctx.fillStyle = "#0284c7";
        ctx.fillRect(rect.x + 138, rect.y + 58, 52, 34);
        ctx.strokeStyle = "#f8fafc";
        ctx.strokeRect(rect.x + 138, rect.y + 58, 52, 34);
        drawPottedPlant(rect.x + 196, rect.y + 24);
      } else if (ft === "studio") {
        drawDeskWithWorkstation(rect.x + 26, rect.y + 34, "#f97316");
        drawDeskWithWorkstation(rect.x + 132, rect.y + 34, "#38bdf8");
        drawDeskWithWorkstation(rect.x + 80, rect.y + 84, "#10b981");
        drawPottedPlant(rect.x + 18, rect.y + 22);
      } else {
        ctx.fillStyle = "#475569";
        ctx.fillRect(rect.x + 14, rect.y + 14, 135, 20);
        ctx.fillStyle = "#dc2626";
        ctx.fillRect(rect.x + 165, rect.y + 12, 26, 30);
        ctx.fillStyle = "#e2e8f0";
        ctx.fillRect(rect.x + 42, rect.y + 62, 118, 38);
        ctx.strokeStyle = "#64748b";
        ctx.strokeRect(rect.x + 42, rect.y + 62, 118, 38);
        drawPottedPlant(rect.x + 22, rect.y + 112);
      }
    }

    function drawRoomInterior(room, rect, night) {
      const tileSize = 22;
      for (let ty = rect.y; ty < rect.y + rect.h; ty += tileSize) {
        for (let tx = rect.x; tx < rect.x + rect.w; tx += tileSize) {
          const isEven =
            (Math.floor((tx - rect.x) / tileSize) + Math.floor((ty - rect.y) / tileSize)) % 2 === 0;
          ctx.fillStyle = isEven ? room.floorColor : room.floorAltColor;
          const w = Math.min(tileSize, rect.x + rect.w - tx);
          const h = Math.min(tileSize, rect.y + rect.h - ty);
          ctx.fillRect(tx, ty, w, h);
        }
      }

      drawFurniture(room, rect);

      // Viền trạng thái runtime (chỉ khi có tín hiệu quan sát được).
      const liveState = getLiveState(room.id);
      if (liveState) {
        ctx.save();
        ctx.strokeStyle = STATE_COLORS[liveState.state] || STATE_COLORS.unknown;
        ctx.lineWidth = 3;
        ctx.setLineDash(liveState.state === "unknown" ? [4, 4] : []);
        ctx.strokeRect(rect.x - 3, rect.y - 3, rect.w + 6, rect.h + 6);
        ctx.restore();
      }

      // Tường phòng
      ctx.strokeStyle = "#334155";
      ctx.lineWidth = 5;
      ctx.strokeRect(rect.x, rect.y, rect.w, rect.h);

      // Cửa ra hành lang
      ctx.fillStyle = "#cbd5e1";
      const doorX = rect.x + rect.w / 2 - 18;
      if (room.row === 0) {
        ctx.fillRect(doorX, rect.y + rect.h - 4, 36, 8);
      } else if (room.row === 1) {
        ctx.fillRect(doorX, rect.y - 4, 36, 8);
        ctx.fillRect(doorX, rect.y + rect.h - 4, 36, 8);
      } else {
        ctx.fillRect(doorX, rect.y - 4, 36, 8);
      }

      // Nhãn tên phòng (tên hệ thống thật)
      const labelText = room.name;
      ctx.font = "bold 10.5px sans-serif";
      const textW = ctx.measureText(labelText).width + 18;
      const pillX = rect.x + rect.w / 2 - textW / 2;
      const pillY = rect.y - 10;

      ctx.fillStyle = "rgba(15, 23, 42, 0.93)";
      drawRoundedRectPath(ctx, pillX, pillY, textW, 19, 5);
      ctx.fill();
      ctx.strokeStyle = room.accent;
      ctx.lineWidth = 1.2;
      ctx.stroke();

      ctx.fillStyle = "#f8fafc";
      ctx.textAlign = "center";
      ctx.fillText(labelText, rect.x + rect.w / 2, pillY + 13.5);
      ctx.textAlign = "left";
    }

    /**
     * Vẽ một thành phần (nhân vật) — dữ liệu hiển thị đều là THẬT:
     * tên, skill và thông điệp sự kiện runtime; không có nhiệm vụ/đồng hồ giả.
     */
    function drawAgent(agent) {
      const s = agents3d[agent.id];
      if (!s) return;
      const x = s.x;
      const y = s.y;
      const bob = Math.sin(s.walkFrame) * 1.8;
      const live = getLiveState(agent.roomId);
      const stateColor =
        (live && STATE_COLORS[live.state]) || STATE_COLORS.unknown;

      // Bóng
      ctx.fillStyle = "rgba(0,0,0,0.32)";
      ctx.beginPath();
      ctx.ellipse(x, y + 12, 10, 5, 0, 0, Math.PI * 2);
      ctx.fill();

      // Chân
      ctx.fillStyle = "#1e293b";
      ctx.fillRect(x - 5, y + 4, 4, 7);
      ctx.fillRect(x + 1, y + 4, 4, 7);

      // Áo
      ctx.fillStyle = agent.shirtColor;
      ctx.fillRect(x - 7, y - 6 + bob, 14, 11);
      // Viền nhỏ theo trạng thái live của hệ thống
      ctx.fillStyle = stateColor;
      ctx.fillRect(x - 7, y - 6 + bob, 14, 2);

      // Đầu
      ctx.fillStyle = "#fed7aa";
      ctx.fillRect(x - 6, y - 16 + bob, 12, 10);

      // Tóc
      ctx.fillStyle = agent.hairColor;
      ctx.fillRect(x - 7, y - 18 + bob, 14, 4);

      // Mắt
      ctx.fillStyle = "#0f172a";
      ctx.fillRect(x - 3, y - 12 + bob, 2, 2);
      ctx.fillRect(x + 2, y - 12 + bob, 2, 2);

      // Bảng tên
      ctx.font = "bold 9px sans-serif";
      const nameW = ctx.measureText(agent.name).width + 18;
      const npX = x - nameW / 2;
      const npY = y + 14;

      ctx.fillStyle = "rgba(15, 23, 42, 0.9)";
      drawRoundedRectPath(ctx, npX, npY, nameW, 13, 4);
      ctx.fill();

      ctx.fillStyle = agent.account === "G" ? "#f59e0b" : agent.account === "S" ? "#38bdf8" : agent.account === "P" ? "#a855f7" : "#10b981";
      ctx.fillRect(npX + 2.5, npY + 2.5, 8, 8);
      ctx.fillStyle = agent.account === "P" ? "#ffffff" : "#0f172a";
      ctx.font = "bold 6.5px sans-serif";
      ctx.fillText(agent.account, npX + 4, npY + 9);

      ctx.fillStyle = "#ffffff";
      ctx.font = "600 8.5px sans-serif";
      ctx.fillText(agent.name, npX + 13, npY + 9.5);

      // Bong bóng: thông điệp sự kiện RUNTIME THẬT (hoặc báo rõ là chưa có).
      const text = s.bubble || "chưa có hoạt động mới";
      ctx.font = "600 9px sans-serif";
      const clipped = text.length > 30 ? text.slice(0, 29) + "…" : text;
      const bw = ctx.measureText(clipped).width + 14;
      const bx = x - bw / 2;
      const by = y - 38 + bob;

      ctx.fillStyle = s.bubble ? "rgba(255, 255, 255, 0.96)" : "rgba(226, 232, 240, 0.88)";
      drawRoundedRectPath(ctx, bx, by, bw, 17, 5);
      ctx.fill();
      ctx.strokeStyle = s.bubble ? stateColor : "#94a3b8";
      ctx.lineWidth = 1;
      ctx.stroke();

      ctx.beginPath();
      ctx.moveTo(x - 4, by + 17);
      ctx.lineTo(x + 4, by + 17);
      ctx.lineTo(x, by + 22);
      ctx.closePath();
      ctx.fill();

      ctx.fillStyle = "#0f172a";
      ctx.fillText(clipped, bx + 7, by + 11.5);
    }

    function drawWorld() {
      syncCanvasSize(false);
      const night = isNightLocal();
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      ctx.save();
      ctx.translate(options.getPan().x, options.getPan().y);
      ctx.scale(options.getZoom(), options.getZoom());

      // 1. Bãi cỏ
      ctx.fillStyle = night ? "#132a17" : "#4d7c3b";
      ctx.fillRect(-900, -600, 3200, 2200);

      ctx.fillStyle = night ? "#16301b" : "#558742";
      for (let gx = -700; gx < 2100; gx += 80) {
        ctx.fillRect(gx, -500, 40, 1900);
      }

      const totalW = WORLD.roomW * WORLD.cols;
      const totalH = WORLD.roomH * WORLD.rows + WORLD.corridorH * 2;

      // 2. Vỉa hè & đường (không còn xe chạy — không phải dữ liệu thật)
      const roadY = WORLD.buildingY + totalH + 34;
      ctx.fillStyle = "#d6c7b2";
      ctx.fillRect(WORLD.buildingX - 42, WORLD.buildingY + WORLD.roomH, 42, WORLD.corridorH);
      ctx.fillRect(WORLD.buildingX - 42, WORLD.buildingY + WORLD.roomH + WORLD.corridorH, 22, totalH - WORLD.roomH + 34);
      ctx.fillRect(-600, roadY - 15, 2600, 15);

      ctx.fillStyle = night ? "#1e293b" : "#57534e";
      ctx.fillRect(-600, roadY, 2600, 60);

      ctx.fillStyle = "#d6d3d1";
      for (let rx = -550; rx < 1950; rx += 42) {
        ctx.fillRect(rx, roadY + 28, 22, 3);
      }

      // Đèn đường (bật theo đồng hồ thật)
      for (let lx = 200; lx <= 1120; lx += 180) {
        ctx.fillStyle = "#1e293b";
        ctx.fillRect(lx, roadY - 26, 3, 22);
        ctx.fillStyle = night ? "#fde047" : "#94a3b8";
        ctx.beginPath();
        ctx.arc(lx + 1.5, roadY - 28, 4.5, 0, Math.PI * 2);
        ctx.fill();
        if (night) {
          ctx.fillStyle = "rgba(253, 224, 71, 0.18)";
          ctx.beginPath();
          ctx.arc(lx + 1.5, roadY - 12, 32, 0, Math.PI * 2);
          ctx.fill();
        }
      }

      // 3. Bóng tòa nhà & 2 tầng hành lang
      ctx.fillStyle = "rgba(0,0,0,0.36)";
      ctx.fillRect(WORLD.buildingX + 8, WORLD.buildingY + 8, totalW, totalH);

      const corr1Y = WORLD.buildingY + WORLD.roomH;
      const corr2Y = WORLD.buildingY + WORLD.roomH * 2 + WORLD.corridorH;
      ctx.fillStyle = "#cbd5e1";
      ctx.fillRect(WORLD.buildingX, corr1Y, totalW, WORLD.corridorH);
      ctx.fillRect(WORLD.buildingX, corr2Y, totalW, WORLD.corridorH);

      ctx.fillStyle = "#64748b";
      for (let c = 0; c < WORLD.cols; c++) {
        ctx.fillRect(WORLD.buildingX + c * WORLD.roomW + 40, corr1Y + 15, 140, 15);
        ctx.fillRect(WORLD.buildingX + c * WORLD.roomW + 40, corr2Y + 15, 140, 15);
      }

      // 4. Vẽ 12 phòng
      rooms.forEach((room) => {
        drawRoomInterior(room, getRoomRect(room), night);
      });

      // 5. Cây
      TREES.forEach((t) => drawPineTree(t.x, t.y, night));

      // 6. Các thành phần (vẽ sau cây để nằm trên cùng)
      [...agents]
        .sort((a, b) => (agents3d[a.id] ? agents3d[a.id].y : 0) - (agents3d[b.id] ? agents3d[b.id].y : 0))
        .forEach((agent) => drawAgent(agent));

      ctx.restore();
    }

    return { drawWorld, fitMapToScreen, syncCanvasSize, drawRoundedRectPath };
  }

  return { createRenderer, drawRoundedRectPath, WORLD, TREES, STATE_COLORS, isNightLocal, getRoomRect };
});
