/**
 * data.js — Bản đồ 12 phòng ban của SCP (chế độ LIVE-ONLY).
 *
 * NGUYÊN TẮC (SCP DNA #1, #4, #14 — tương ứng FA-04):
 *  - File này KHÔNG chứa số liệu mô phỏng nào (không tiến trình giả, không
 *    nhiệm vụ giả, không nhân vật bịa). Nó chỉ mô tả **cấu trúc thật** của
 *    repo: tên phòng, đường dẫn module, và tín hiệu runtime nào quan sát được.
 *  - Mọi giá trị số hiển thị đến từ `/api/scp/*` (xem live-map.js).
 *  - Đường dẫn trong `components` đã được đối chiếu với cây thư mục thật.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.ScpData = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  /** Nhóm tài khoản — phân loại trợ lý theo vai trò trong hệ thống. */
  const ACCOUNTS = {
    G: { id: "G", label: "Điều phối & Sáng tạo", color: "#f59e0b", cssClass: "acc-G" },
    S: { id: "S", label: "SCP & Cố vấn", color: "#38bdf8", cssClass: "acc-S" },
    P: { id: "P", label: "Cổng & Dịch vụ", color: "#a855f7", cssClass: "acc-P" },
    W: { id: "W", label: "Kỹ năng cá nhân hóa", color: "#10b981", cssClass: "acc-W" },
  };

  /**
   * 12 phòng ban = 12 nhóm hệ thống thật của repo.
   *  - `liveSignals`: đường dẫn JSON quan sát được trong payload runtime.
   *  - `components`  : module/skill THẬT của repo (đường dẫn đã kiểm chứng tồn tại).
   */
  const ROOMS = [
    {
      id: "reception",
      name: "API Pipeline :8000",
      codeName: "api-pipeline-8000",
      path: "scp/api_server.py",
      row: 0,
      col: 0,
      floorColor: "#d9b99b",
      floorAltColor: "#cfa987",
      accent: "#f59e0b",
      furnitureType: "reception",
      liveSignals: ["health.fastapi"],
      liveLabel: "FastAPI :8000",
      components: [
        { name: "api_server.py", path: "scp/api_server.py", group: "P" },
        { name: "api_server_parts/pipeline", path: "scp/api_server_parts/pipeline", group: "P" },
        { name: "middleware/ (16-Skill Defense)", path: "scp/middleware", group: "S" },
      ],
    },
    {
      id: "kernel_room",
      name: "TaskKernel 17-State",
      codeName: "task-kernel-17states",
      path: "scp/task_kernel_parts",
      row: 0,
      col: 1,
      floorColor: "#cbd5e1",
      floorAltColor: "#b8c4d4",
      accent: "#38bdf8",
      furnitureType: "kernel",
      liveSignals: ["status.scpHealth.task_kernel", "activity.summary.latestRunStatus"],
      liveLabel: "TaskKernel 17-State",
      components: [
        { name: "state_machine.py", path: "scp/task_kernel_parts/state_machine.py", group: "S" },
        { name: "lease_manager.py", path: "scp/task_kernel_parts/lease_manager.py", group: "S" },
        { name: "checkpoint.py", path: "scp/task_kernel_parts/checkpoint.py", group: "S" },
        { name: "commit_approval.py", path: "scp/task_kernel_parts/commit_approval.py", group: "S" },
        { name: "idempotency.py", path: "scp/task_kernel_parts/idempotency.py", group: "S" },
      ],
    },
    {
      id: "scp_lab",
      name: "Reality & TraceLedger",
      codeName: "scp-dna-verifier",
      path: "scp/trace_ledger.py",
      row: 0,
      col: 2,
      floorColor: "#d6d3d1",
      floorAltColor: "#c7c2be",
      accent: "#0ea5e9",
      furnitureType: "scplab",
      liveSignals: ["status.audit.auditRound"],
      liveLabel: "Reality Verifier / Ledger",
      components: [
        { name: "trace_ledger.py", path: "scp/trace_ledger.py", group: "S" },
        { name: "runtime_audit/", path: "scp/runtime_audit", group: "S" },
        { name: "audit_engine/", path: "scp/audit_engine", group: "S" },
        { name: "epistemic/", path: "scp/epistemic", group: "S" },
      ],
    },
    {
      id: "cap_mcp_room",
      name: "Capability & MCP",
      codeName: "capability-mcp-sec",
      path: "scp/core/capability_token.py",
      row: 0,
      col: 3,
      floorColor: "#e0e7ff",
      floorAltColor: "#c7d2fe",
      accent: "#6366f1",
      furnitureType: "security",
      liveSignals: [],
      liveLabel: "chưa có tín hiệu runtime công khai — UNKNOWN",
      components: [
        { name: "core/capability_token.py", path: "scp/core/capability_token.py", group: "S" },
        { name: "mcp_server/server.py", path: "scp/mcp_server/server.py", group: "P" },
        { name: "capabilities/", path: "scp/capabilities", group: "S" },
        { name: "policy/", path: "scp/policy", group: "S" },
      ],
    },
    {
      id: "desktop_novnc",
      name: "Desktop noVNC :6080",
      codeName: "desktop-novnc-6080",
      path: "desktop/",
      row: 1,
      col: 0,
      floorColor: "#e2e8f0",
      floorAltColor: "#cbd5e1",
      accent: "#a855f7",
      furnitureType: "desktop",
      liveSignals: ["health.agentDesktop"],
      liveLabel: "noVNC :6080",
      components: [
        { name: "pc_control/", path: "scp/pc_control", group: "P" },
        { name: "web_control/", path: "scp/web_control", group: "P" },
        { name: "hands/", path: "scp/hands", group: "P" },
        { name: "desktop/ (Xvfb + Chromium)", path: "desktop", group: "P" },
      ],
    },
    {
      id: "gateway_room",
      name: "LLM Gateway (trực tiếp)",
      codeName: "llm-gateway-direct",
      path: "scp/llm_gateway/",
      row: 1,
      col: 1,
      floorColor: "#f3e8ff",
      floorAltColor: "#e9d5ff",
      accent: "#ec4899",
      furnitureType: "gateway",
      // Kiến trúc Zero-Bun đã XOÁ container/cổng LLM :8081; gateway chạy trực
      // tiếp trong backend. Mục `llmBridge` trong /api/scp/health là HẰNG SỐ tổng
      // hợp (`ok:true, latencyMs:0`) — KHÔNG phải quan sát được ⇒ phòng này
      // không gắn tín hiệu nào (UNKNOWN), và không tính vào "ports live".
      liveSignals: [],
      liveLabel: "gateway chạy trực tiếp trong backend — không còn cổng :8081 riêng để quan sát",
      components: [
        { name: "llm_gateway/client.py", path: "scp/llm_gateway/client.py", group: "P" },
        { name: "llm_gateway/providers.py", path: "scp/llm_gateway/providers.py", group: "P" },
        { name: "llm_gateway/circuit_breaker.py", path: "scp/llm_gateway/circuit_breaker.py", group: "S" },
        { name: "llm_gateway/egress_policy.py", path: "scp/llm_gateway/egress_policy.py", group: "S" },
        { name: "llm_gateway/free_catalog.py", path: "scp/llm_gateway/free_catalog.py", group: "P" },
      ],
    },
    {
      id: "tabula_kb",
      name: "Tabula Rasa & Knowledge",
      codeName: "tabula-rasa-kb",
      path: "scp/knowledge/ · data/knowledge.sqlite3",
      row: 1,
      col: 2,
      floorColor: "#d1fae5",
      floorAltColor: "#a7f3d0",
      accent: "#10b981",
      furnitureType: "library",
      liveSignals: [],
      liveLabel: "chưa có tín hiệu runtime công khai — UNKNOWN",
      components: [
        { name: "knowledge/", path: "scp/knowledge", group: "W" },
        { name: "learning/", path: "scp/learning", group: "W" },
        { name: "data_sources/", path: "scp/data_sources", group: "P" },
        { name: "rag/", path: "scp/rag", group: "W" },
      ],
    },
    {
      id: "loop_sandbox",
      name: "Loop :3030 & Sandbox",
      codeName: "loop-scheduler-3030",
      path: "mini-services/loop-scheduler/index.js",
      row: 1,
      col: 3,
      floorColor: "#ccfbf1",
      floorAltColor: "#99f6e4",
      accent: "#14b8a6",
      furnitureType: "sandbox",
      liveSignals: ["health.loopScheduler", "activity.summary.latestRunStatus"],
      liveLabel: "Loop Scheduler :3030",
      components: [
        { name: "loop-scheduler/index.js", path: "mini-services/loop-scheduler/index.js", group: "P" },
        { name: "sandbox_evaluator/", path: "scp/sandbox_evaluator", group: "S" },
        { name: "autofix/", path: "scp/autofix", group: "S" },
        { name: "observability/", path: "scp/observability", group: "P" },
      ],
    },
    {
      id: "release_ci",
      name: "Release Gate T00–T12",
      codeName: "release-gate-ci",
      path: "tests/ · .github/workflows/",
      row: 2,
      col: 0,
      floorColor: "#ffe4e6",
      floorAltColor: "#fecdd3",
      accent: "#f43f5e",
      furnitureType: "release",
      liveSignals: ["activity.summary.latestFindingsCount", "activity.summary.latestFixesCount"],
      liveLabel: "Chu kỳ Loop gần nhất",
      components: [
        { name: "tests/ (T00..T12)", path: "tests", group: "S" },
        { name: ".github/workflows/", path: ".github/workflows", group: "S" },
        { name: "release/", path: "scp/release", group: "S" },
        { name: "runtime_audit/", path: "scp/runtime_audit", group: "S" },
      ],
    },
    {
      id: "council_lounge",
      name: "Hội đồng 5 Góc & Triad",
      codeName: "hoi-dong-triad",
      path: ".agents/skills/",
      row: 2,
      col: 1,
      floorColor: "#b48a64",
      floorAltColor: "#a37953",
      accent: "#8b5cf6",
      furnitureType: "lounge",
      liveSignals: ["status.engine.modulesWired"],
      liveLabel: "Module SCP đã wiring",
      components: [
        { name: "skills/scp-dna", path: ".agents/skills/scp-dna", group: "S" },
        { name: "skills/scp-reality-verifier", path: ".agents/skills/scp-reality-verifier", group: "S" },
        { name: "brain/", path: "scp/brain", group: "G" },
        { name: "meta/", path: "scp/meta", group: "G" },
      ],
    },
    {
      id: "canvas_studio",
      name: "Dashboard :3000",
      codeName: "xuong-canvas-3000",
      path: "dashboard/src/",
      row: 2,
      col: 2,
      floorColor: "#d6d3d1",
      floorAltColor: "#c4bfba",
      accent: "#f97316",
      furnitureType: "studio",
      liveSignals: ["metrics.cpuPercent", "metrics.memoryPercent"],
      liveLabel: "Tải Dashboard :3000",
      components: [
        { name: "dashboard/src/app/", path: "dashboard/src/app", group: "G" },
        { name: "dashboard/src/components/", path: "dashboard/src/components", group: "G" },
        { name: "dashboard/src/lib/", path: "dashboard/src/lib", group: "G" },
      ],
    },
    {
      id: "energy_cafe",
      name: "Kỹ năng & Năng lượng",
      codeName: "tram-nang-luong",
      path: ".agents/skills/ (16 skill)",
      row: 2,
      col: 3,
      floorColor: "#e2e8f0",
      floorAltColor: "#cbd5e1",
      accent: "#64748b",
      furnitureType: "cafe",
      liveSignals: [],
      liveLabel: "chưa có tín hiệu runtime công khai — UNKNOWN",
      components: [
        { name: "skills/ (16 SCP skills)", path: ".agents/skills", group: "W" },
        { name: "self_model/", path: "scp/self_model", group: "W" },
        { name: "prediction/", path: "scp/prediction", group: "W" },
        { name: "calibration/", path: "scp/calibration", group: "W" },
      ],
    },
  ];

  /**
   * 24 "nhân vật" = 24 thành phần/module THẬT của repo (tên, vai trò, skill,
   * đường dẫn đều là thật — xem `skill` và `desc`).
   *
   * KHÔNG có trường nhiệm vụ giả, không đồng hồ đếm ngược, không tiến trình giả.
   * Hoạt động của nhân vật được dẫn bởi SỰ KIỆN RUNTIME THẬT:
   *   - `service`: định danh dịch vụ mà nhân vật phụ trách — khớp CHÍNH XÁC
   *              với `event.service` (ví dụ "loop-scheduler", "desktop").
   *   - `match`  : định danh RIÊNG của thành phần (tên module/skill) xuất hiện
   *                trong thông điệp sự kiện. CHỈ dùng từ khoá đủ đặc thùy
   *                ("cdp", "mcp", "llm_gateway"…) — không dùng từ chung
   *                ("kernel", "next", "health", "audit", "token") vì bắt nhầm.
   */
  const AGENTS = [
    { id: "api_router", name: "API Pipeline Lead", account: "P", isLead: true, avatar: "🌐", shirtColor: "#f59e0b", hairColor: "#1e293b", roomId: "reception", skill: "scp/api_server.py (:8000)", role: "Điều phối Pipeline 7 Tầng & 7 Trụ cột", desc: "Điều phối tuần tự 7 tầng xử lý trong scp/api_server_parts/pipeline/ theo 3 mức Route Profile (core, standard, full).", match: ["api_server", "api_server_parts", "pipeline", "route profile"] },
    { id: "skill_guard", name: "SkillGuard 16-Defense", account: "S", isLead: false, avatar: "🛡️", shirtColor: "#d97706", hairColor: "#0f172a", roomId: "reception", skill: "RuntimeSkillGuardMiddleware", role: "Lá chắn ASGI 16 Kỹ năng Phòng ngự", desc: "Kiểm soát mọi request vào Port 8000 ở tầng ASGI, lọc SSRF, kiểm tra chữ ký HMAC và kích hoạt ngắt mạch tức thì.", match: ["skillguard", "runtime_skill_guard", "ssrf"] },
    { id: "kernel_k", name: "TaskKernel Master", account: "S", isLead: true, avatar: "⚙️", shirtColor: "#2563eb", hairColor: "#1e293b", roomId: "kernel_room", skill: "scp-task-kernel-review", role: "Máy trạng thái 17-State & Lease Lock", desc: "Quản lý 11 phân hệ trong scp/task_kernel_parts/: state_machine, lease_manager, checkpoint, commit_approval và idempotency.", match: ["task_kernel", "task_kernel_parts", "lease_manager", "checkpoint", "commit_approval"] },
    { id: "eventbus_pg", name: "EventBus & PG Store", account: "P", isLead: false, avatar: "🗄️", shirtColor: "#1d4ed8", hairColor: "#fde047", roomId: "kernel_room", skill: "event_bus_pg.py & storage_pg", role: "Điều phối Sự kiện LISTEN/NOTIFY & WAL", desc: "Vận hành hàng đợi sự kiện bền vững (scp_events) và lưu trữ trạng thái nguyên tử của TaskKernel.", match: ["event_bus", "eventbus_pg", "storage_pg", "listenn", "notify"] },
    { id: "vera", name: "Reality Verifier", account: "S", isLead: true, avatar: "🔬", shirtColor: "#0284c7", hairColor: "#fde047", roomId: "scp_lab", skill: "scp-reality-verifier", role: "Kiểm chứng Bằng chứng Vật lý & Receipt", desc: "Thực thi nguyên tắc cốt lõi Reality > Model và PASS ≠ TRUE. Chỉ công nhận kết quả khi có bằng chứng vật lý (WAL, stdout, socket).", match: ["reality_verifier", "scp-reality-verifier", "verifierreceipt", "merkle chain"] },
    { id: "delta_auditor", name: "Delta & Merkle Auditor", account: "S", isLead: false, avatar: "⛓️", shirtColor: "#0369a1", hairColor: "#f8fafc", roomId: "scp_lab", skill: "scp-delta-audit", role: "Kiểm toán Zero-Trust & Chuỗi Merkle", desc: "Kiểm toán chống giả dược (anti-placebo), ghi nhận nhật ký bất biến Merkle chain và đối chiếu các mục kiểm toán R1–R6.", match: ["scp-delta-audit", "trace_ledger", "audit_engine", "merkle"] },
    { id: "cap_guardian", name: "Capability Guardian", account: "S", isLead: true, avatar: "🔑", shirtColor: "#4f46e5", hairColor: "#e2e8f0", roomId: "cap_mcp_room", skill: "scp-capability-security-review", role: "Cấp quyền CapabilityToken & Egress", desc: "Giữ quyền cấp phát Capability ngoài vùng tự sửa đổi (DNA #6), kiểm soát phạm vi task_id, attempt, TTL và ngăn rò rỉ dữ liệu.", match: ["capability_token", "core/capability", "egress_policy"] },
    { id: "mcp_officer", name: "MCP Server Officer", account: "P", isLead: false, avatar: "🔌", shirtColor: "#4338ca", hairColor: "#f59e0b", roomId: "cap_mcp_room", skill: "scp/mcp_server/server.py", role: "Giao thức JSON-RPC 2.0 & Công cụ Hands", desc: "Quản lý cổng giao tiếp Model Context Protocol (MCP), kiểm soát schema và quyền gọi công cụ điều khiển máy tính.", match: ["mcp_server", "json-rpc", "hands/"] },
    { id: "desktop_op", name: "Desktop Recovery Op", account: "P", isLead: true, avatar: "🖥️", shirtColor: "#9333ea", hairColor: "#1e293b", roomId: "desktop_novnc", skill: "scp-computer-use-recovery", role: "Điều khiển Xvfb :99 / noVNC :6080", desc: "Vận hành container máy tính ảo cô lập (Port 6080) và phục hồi an toàn khi tác vụ GUI bị gián đoạn sau khi tạo side-effect.", service: "desktop", match: ["novnc", "chromium", "xvfb", "pc_control", "cdp"] },
    { id: "web_spider", name: "Web & CDP Safety", account: "S", isLead: false, avatar: "🕸️", shirtColor: "#7e22ce", hairColor: "#fde047", roomId: "desktop_novnc", skill: "scp-web-orchestration-safety", role: "An toàn Trình duyệt CDP & Chống Honeypot", desc: "Kiểm soát tự động hóa trình duyệt qua giao thức CDP, ngăn chặn bẫy honeypot và tấn công giả mạo trên cây DOM.", match: ["web_control", "cdp", "honey", "devtools"] },
    { id: "gateway_cmd", name: "Gateway Commander", account: "P", isLead: true, avatar: "🚦", shirtColor: "#db2777", hairColor: "#0f172a", roomId: "gateway_room", skill: "scp-gateway-resilience", role: "Cầu nối Đa LLM (:8081) & Circuit Breaker", desc: "Quản lý định tuyến mô hình tại Port 8081, tự động ngắt mạch khi quá tải và chuyển tầng dự phòng theo ngân sách.", match: ["llm_gateway", "circuit_breaker", "egress_policy", "free_catalog"] },
    { id: "crosscheck_judge", name: "Cross-Check Judge", account: "S", isLead: false, avatar: "⚖️", shirtColor: "#be185d", hairColor: "#f8fafc", roomId: "gateway_room", skill: "multi_llm_crosscheck.py", role: "Chống Ảo giác Đồng thuận (DNA #5, #14)", desc: "Ngăn chặn lỗi Ảo giác đồng thuận bằng cách yêu cầu bằng chứng từ các nhánh nguồn độc lập trước khi xác nhận.", match: ["multi_llm_crosscheck", "crosscheck"] },
    { id: "curator_tabula", name: "Tabula Rasa Guard", account: "S", isLead: true, avatar: "🧬", shirtColor: "#059669", hairColor: "#fef08a", roomId: "tabula_kb", skill: "scp-learning-loop-guard", role: "Cách ly 3 Tầng & Chống Đầu độc (DNA #27)", desc: "Bảo vệ kho tri thức data/knowledge.sqlite3 (100% Tabula Rasa) qua 3 tầng cách ly, chặn đứng Prompt Injection và quên thảm khốc.", match: ["scp-learning-loop-guard", "knowledge.sqlite3", "quarantine", "auto_web_ingestor"] },
    { id: "api_bridge_bot", name: "Free API Bridge & AutoFix", account: "P", isLead: false, avatar: "⚡", shirtColor: "#047857", hairColor: "#1e293b", roomId: "tabula_kb", skill: "scp/data_sources & autofix", role: "Cầu nối Public APIs & sinh bản vá an toàn", desc: "Ánh xạ động các lĩnh vực tới API công khai với bộ đệm WAL và tự động sinh bản vá không làm yếu chốt bảo vệ (DNA #28).", match: ["data_sources", "autofix", "free_api_catalog"] },
    { id: "loop_master", name: "Loop Scheduler 24/7", account: "P", isLead: true, avatar: "♾️", shirtColor: "#0d9488", hairColor: "#f8fafc", roomId: "loop_sandbox", skill: "scp-continuous-operations-loop", role: "Vòng lặp 8 Bước Tự hành (Port 3030)", desc: "Vận hành vòng lặp tự hành 24/7 trên Bun Runtime (:3030) với cơ chế kiểm tra chéo Maker/Checker và đối soát tác động phụ.", service: "loop-scheduler", match: ["loop-scheduler", "chu kỳ", "vòng lặp"] },
    { id: "bwrap_opt", name: "Sandbox & Latency Opt", account: "S", isLead: false, avatar: "📦", shirtColor: "#0f766e", hairColor: "#fde047", roomId: "loop_sandbox", skill: "scp-safe-latency-optimizer", role: "Cô lập Bubblewrap (bwrap) & Tối ưu I/O", desc: "Chạy thử nghiệm hồi quy cô lập bằng Bubblewrap (bwrap) và giảm độ trễ mà không làm suy yếu bất kỳ rào chắn bảo mật nào.", match: ["sandbox_evaluator", "bwrap"] },
    { id: "gatekeeper_rc", name: "Release Gatekeeper", account: "S", isLead: true, avatar: "🏁", shirtColor: "#e11d48", hairColor: "#1e293b", roomId: "release_ci", skill: "scp-release-evidence-gate", role: "Cổng Phát hành T00–T12 & Golden Tasks", desc: "Kiểm soát 21 phân vùng kiểm thử (T00_integrity → T12_unified_chatbot) và chỉ cho phép phát hành khi đủ bằng chứng PASS_WITHIN_SCOPE.", match: ["tier3", "golden_task", "status-ledger", "release_gate"] },
    { id: "runtime_doc", name: "Runtime & Startup Doctor", account: "S", isLead: false, avatar: "🩺", shirtColor: "#be123c", hairColor: "#fde047", roomId: "release_ci", skill: "scp-runtime-audit & startup", role: "Kiểm toán Tiến trình Sống & Cổng Mạng", desc: "Xác minh trạng thái thực tế của process, port, health check, khắc phục lỗi khởi động (scp-startup-troubleshooter) và chuẩn hóa kỹ năng (scp-skill-review).", match: ["runtime_audit", "startup", "readiness"] },
    { id: "minh_vision", name: "Chủ tịch Hội đồng & Triad", account: "G", isLead: true, avatar: "🧠", shirtColor: "#7c3aed", hairColor: "#f8fafc", roomId: "council_lounge", skill: "get-more-perspectives & Triad", role: "5 Góc nhìn Cố vấn & Phản biện 3 Bên", desc: "Điều phối Hội đồng 5 góc nhìn (Thực thi, Rủi ro, Tầm nhìn, UX, Chi phí) kết hợp Bộ ba Kiểm định Triad trước mọi quyết định chiến lược.", match: ["scp/brain", "triad", "get-more-perspectives"] },
    { id: "skeptic_s", name: "The Provocateur (Red Team)", account: "S", isLead: false, avatar: "🕵️", shirtColor: "#dc2626", hairColor: "#475569", roomId: "council_lounge", skill: "Triad & Red Teaming", role: "Phản biện Ngược & Kiểm thử Điểm mù", desc: "Thành viên Hội đồng Ba Bên (The Triad Council) và Cố vấn Hoài nghi, chuyên tìm cách bẻ gãy giả định và phát hiện điểm mù logic.", match: ["red team", "escalation", "bypass"] },
    { id: "nova_ideas", name: "Trợ lý Sáng tạo 5 Lăng kính", account: "W", isLead: false, avatar: "✨", shirtColor: "#db2777", hairColor: "#f59e0b", roomId: "council_lounge", skill: "generate-fresh-ideas", role: "Biến tấu 5 Lăng kính Nội dung", desc: "Chuyển hóa tài liệu gốc thành 5 ý tưởng mới: Contrarian Take, Personal Story Hook, Data/Evidence, Playbook và What They Missed.", match: ["generate-fresh-ideas", "lăng kính"] },
    { id: "architect_c", name: "Kiến trúc sư Dashboard", account: "G", isLead: true, avatar: "🎨", shirtColor: "#ea580c", hairColor: "#0f172a", roomId: "canvas_studio", skill: "dashboard (:3000) & web-canvas", role: "Next.js 16 Dashboard (:3000) & Canvas 2D", desc: "Xây dựng giao diện điều khiển trung tâm Next.js 16 (Port 3000) của Vietnam-Lab và bản đồ mô phỏng trực quan.", match: ["dashboard/src", "frontend"] },
    { id: "linh_voice", name: "Trợ lý Văn phong", account: "W", isLead: false, avatar: "✍️", shirtColor: "#f97316", hairColor: "#27272a", roomId: "canvas_studio", skill: "match-my-writing-style", role: "Đồng bộ Voice Card Cá nhân", desc: "Đảm bảo mọi tài liệu, báo cáo kiểm toán và câu trả lời đều giữ đúng nhịp điệu, từ vựng và phong cách của bạn.", match: ["match-my-writing-style", "voice card"] },
    { id: "khoa_energy", name: "Điều phối Năng lượng & Họp", account: "W", isLead: true, avatar: "🔋", shirtColor: "#16a34a", hairColor: "#78350f", roomId: "energy_cafe", skill: "focus-my-energy & prep-meetings", role: "Tối ưu Nhịp Sinh học, Glance Card & Bảo mật", desc: "Khớp danh sách tác vụ với mức năng lượng hiện tại, quét ghi chú tạo Glance Card trước cuộc họp và kiểm soát dữ liệu nhạy cảm.", match: ["focus-my-energy", "prep-meetings", "glance card"] },
  ];

  /** Dịch vụ live → phòng tương ứng (để nhân vật đi tới nơi đang có việc). */
  const SERVICE_ROOMS = {
    "loop-scheduler": "loop_sandbox",
    scheduler: "loop_sandbox",
    desktop: "desktop_novnc",
  };

  /** Danh mục tài nguyên tĩnh phục vụ (logo, robots.txt) của dashboard. */
  return { ACCOUNTS, ROOMS, AGENTS, SERVICE_ROOMS };
});
