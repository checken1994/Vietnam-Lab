/**
 * sim.js — Động cơ chuyển động của các thành phần trên bản đồ.
 *
 * [NGUYÊN TẮC · DNA #4 / FA-04] Nhân vật KHÔNG đi theo tác vụ giả. Một
 * thành phần chỉ di chuyển khi SỰ KIỆN RUNTIME THẬT liên quan tới nó tới
 * (đọc từ /api/scp/activity: service + message). Không có sự kiện ⇒ nhân vật
 * đứng yên tại phòng của mình, bong bóng thoại ghi rõ "chưa có hoạt động mới".
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.ScpSim = factory();
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

  const WALK_SPEED = 111; // px/giây — khớp nhịp bản gốc (1.85 px/khung @60fps)
  const MAX_LOGS = 45;
  const BUBBLE_MS = 9000; // thời gian giữ bong bóng sự kiện live

  function getRoomRect(room) {
    return {
      x: WORLD.buildingX + room.col * WORLD.roomW,
      y: WORLD.buildingY + room.row * (WORLD.roomH + WORLD.corridorH),
      w: WORLD.roomW,
      h: WORLD.roomH,
    };
  }

  function getCorridorYForRow(row) {
    if (row === 0 || row === 1) return WORLD.buildingY + WORLD.roomH + WORLD.corridorH / 2;
    return WORLD.buildingY + WORLD.roomH * 2 + WORLD.corridorH + WORLD.corridorH / 2;
  }

  /**
   * Lộ trình đi từ phòng hiện tại tới phòng đích (hàm thuần — kiểm thử được).
   * Đi qua hành lang và trục dọc trung tâm khi đổi tầng.
   */
  function computePathway(currentRoomId, targetRoomId, destX, destY, rooms) {
    const currentRoom = rooms.find((r) => r.id === currentRoomId);
    const targetRoom = rooms.find((r) => r.id === targetRoomId);
    if (!currentRoom || !targetRoom) return [];

    const currentRect = getRoomRect(currentRoom);
    const targetRect = getRoomRect(targetRoom);
    const curDoorX = currentRect.x + currentRect.w / 2;
    const targetDoorX = targetRect.x + targetRect.w / 2;
    const curCorrY = getCorridorYForRow(currentRoom.row);
    const targetCorrY = getCorridorYForRow(targetRoom.row);
    const atriumX = WORLD.buildingX + WORLD.roomW * 2;

    if (currentRoom.id === targetRoom.id) return [{ x: destX, y: destY }];
    if (Math.abs(curCorrY - targetCorrY) < 5) {
      return [
        { x: curDoorX, y: curCorrY },
        { x: targetDoorX, y: targetCorrY },
        { x: destX, y: destY },
      ];
    }
    return [
      { x: curDoorX, y: curCorrY },
      { x: atriumX, y: curCorrY },
      { x: atriumX, y: targetCorrY },
      { x: targetDoorX, y: targetCorrY },
      { x: destX, y: destY },
    ];
  }

  /**
   * Tạo động cơ.
   * @param {object} options rooms, agents, serviceRooms, random, onChange
   */
  function createSimulation(options) {
    const rooms = options.rooms;
    const agents = options.agents;
    const serviceRooms = options.serviceRooms || {};
    const onChange = options.onChange || function () {};
    const random = options.random || Math.random;

    const state = { logs: [], liveLogs: [] };

    /** Trạng thái hiển thị của mỗi thành phần. */
    const agents3d = {};
    agents.forEach((a, idx) => {
      const room = rooms.find((r) => r.id === a.roomId) || rooms[0];
      const rect = getRoomRect(room);
      const peers = agents.filter((x) => x.roomId === room.id);
      const i = peers.indexOf(a);
      const step = 34;
      const startX = rect.x + rect.w / 2 - ((peers.length - 1) * step) / 2;
      agents3d[a.id] = {
        x: startX + i * step,
        y: rect.y + rect.h - 52,
        homeRoomId: room.id,
        waypoints: [],
        walkFrame: idx * 1.1,
        bubble: null,
        bubbleUntil: 0,
        busy: false,
      };
    });

    function roomCenterSpot(roomId) {
      const room = rooms.find((r) => r.id === roomId) || rooms[0];
      const rect = getRoomRect(room);
      return { x: rect.x + rect.w / 2, y: rect.y + rect.h - 52 };
    }

    /**
     * Nhân vật khớp với sự kiện live nào?
     *
     * [FIX · 2026-10-22 · khớp nhầm] Trước đây dò từ khoá trong
     * `service + " " + message` ⇒ một dòng log của container desktop chứa
     * "node_modules/...kernel" làm bật nhầm TaskKernel Master. Nay:
     *   1. `service` khớp CHÍNH XÁC (`event.service === agent.service`) → chắc chắn đúng;
     *   2. từ khoá trong message → chỉ dùng định danh RIÊNG của thành phần
     *      ("cdp", "mcp_server", "llm_gateway"…), không dùng từ chung.
     */
    function matchAgents(event) {
      const service = (event.service || "").toLowerCase();
      const message = String(event.message || "").toLowerCase();
      return agents.filter((a) => {
        if (a.service && service) return service === a.service.toLowerCase();
        return (a.match || []).some((kw) => message.includes(kw.toLowerCase()));
      });
    }

    /**
     * Thông điệp hiển thị phải là MỘT DÒNG lấy từ sự kiện thật: log của
     * service thường nhiều dòng ⇒ lấy dòng cuối có nội dung (dòng mới nhất).
     */
    function oneLine(message, maxLen) {
      // Log của service có thể chứa cả newline thật lẫn chuỗi "\n" literal.
      const flat = String(message || "").replace(/\\n/g, " ");
      const lines = flat
        .split(/\r?\n/)
        .map((l) => l.trim())
        .filter(Boolean);
      const last = lines.length ? lines[lines.length - 1] : "";
      return last.length > maxLen ? last.slice(0, maxLen - 1) + "…" : last;
    }

    /**
     * Nạp sự kiện runtime THẬT → các thành phần liên quan đi tới nơi đang có việc
     * và hiện thông điệp thật trong bong bóng.
     */
    function applyLiveEvents(events) {
      if (!Array.isArray(events) || !events.length) return;
      const now = Date.now();
      for (const ev of events) {
        const matched = matchAgents(ev);
        if (!matched.length) continue;
        const targetRoom = serviceRooms[ev.service] || null;
        for (const agent of matched) {
          const s = agents3d[agent.id];
          if (!s) continue;
          s.busy = true;
          s.bubble = oneLine(ev.message, 70);
          s.bubbleUntil = now + BUBBLE_MS;
          const destRoom = targetRoom || agent.roomId;
          const spot = roomCenterSpot(destRoom);
          const jitterX = (random() - 0.5) * 40;
          s.waypoints = computePathway(
            currentRoomOf(agent.id),
            destRoom,
            spot.x + jitterX,
            spot.y,
            rooms
          );
        }
      }
      onChange();
    }

    /** Phòng hiện tại của một thành phần (suy ra từ vị trí). */
    function currentRoomOf(agentId) {
      const s = agents3d[agentId];
      if (!s) return null;
      for (const room of rooms) {
        const rect = getRoomRect(room);
        if (s.x >= rect.x && s.x <= rect.x + rect.w && s.y >= rect.y && s.y <= rect.y + rect.h) {
          return room.id;
        }
      }
      return s.homeRoomId;
    }

    /** Bước mô phỏng theo thời gian thực. */
    function tick(dt) {
      const now = Date.now();
      for (const agent of agents) {
        const s = agents3d[agent.id];
        if (!s) continue;
        if (s.waypoints.length > 0) {
          const wp = s.waypoints[0];
          const dx = wp.x - s.x;
          const dy = wp.y - s.y;
          const dist = Math.hypot(dx, dy);
          const step = WALK_SPEED * dt;
          if (dist <= step) {
            s.x = wp.x;
            s.y = wp.y;
            s.waypoints.shift();
          } else {
            s.x += (dx / dist) * step;
            s.y += (dy / dist) * step;
            s.walkFrame += 0.26 * dt * 60;
          }
        }
        if (s.bubble && now > s.bubbleUntil) {
          s.bubble = null;
          s.busy = false;
        }
      }
    }

    return {
      WORLD,
      state,
      agents3d,
      getRoomRect,
      getCorridorYForRow,
      computePathway,
      applyLiveEvents,
      currentRoomOf,
      tick,
      matchAgents,
      oneLine,
    };
  }

  return { WORLD, createSimulation, computePathway, getRoomRect, getCorridorYForRow, WALK_SPEED };
});
