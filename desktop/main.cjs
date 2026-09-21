const { app, BrowserWindow, dialog, Menu, shell, session, ipcMain, Notification: ElectronNotification } = require('electron');
const { spawn } = require('node:child_process');
const path = require('node:path');
const REQUESTED_USER_DATA_DIR = process.env.SCP_USER_DATA_DIR;
if (REQUESTED_USER_DATA_DIR) app.setPath('userData', path.resolve(REQUESTED_USER_DATA_DIR));
const fs = require('node:fs');
const http = require('node:http');
const { randomBytes } = require('node:crypto');

const DEV_ROOT = path.resolve(__dirname, '..');
const IS_PACKAGED = app.isPackaged;
const SCP_ROOT = IS_PACKAGED ? path.join(app.getPath('userData'), 'config') : DEV_ROOT;
const RUNTIME_ROOT = IS_PACKAGED ? path.join(process.resourcesPath, 'runtime') : DEV_ROOT;
fs.mkdirSync(SCP_ROOT, { recursive: true });
function ensurePackagedDefaults() {
  if (!IS_PACKAGED) return;
  const envFile = path.join(SCP_ROOT, '.env');
  const secretDir = path.join(SCP_ROOT, 'secrets');
  fs.mkdirSync(secretDir, { recursive: true });
  if (!fs.existsSync(envFile)) {
    const defaults = [
      'SCP_PRODUCTION_MODE=1',
      'SCP_DEV_MODE=0',
      'SCP_SKIP_STARTUP_GATE=0',
      'SCP_AUTO_APPROVE_TIER3=0',
      'SCP_EGRESS_MODE=deny',
      'SCP_STARTUP_SCAN_ENTERPRISE=0',
      'SCP_AUTH_PASSWORD_FILE=secrets/scp-auth-password',
      'SCP_AUTH_TOKEN_SECRET_FILE=secrets/scp-auth-token',
      'SCP_SCHEDULER_ADMIN_TOKEN_FILE=secrets/scp-scheduler-admin-token',
      'SCP_LLM_BRIDGE_PORT=11434',
      'SCP_DESKTOP_CSP_MODE=production',
    ].join('\n') + '\n';
    fs.writeFileSync(envFile, defaults, { flag: 'wx', encoding: 'utf8' });
  }
  for (const name of ['scp-auth-password', 'scp-auth-token', 'scp-scheduler-admin-token']) {
    const file = path.join(secretDir, name);
    if (!fs.existsSync(file)) fs.writeFileSync(file, randomBytes(32).toString('hex') + '\n', { flag: 'wx', encoding: 'utf8' });
  }
}
ensurePackagedDefaults();
const DASHBOARD_URL = 'http://127.0.0.1:3000';
const MODEL_VERSION = '1.6';
const STARTUP_TIMEOUT_MS = 180000;

function loadRootEnv() {
  const file = path.join(SCP_ROOT, '.env');
  if (!fs.existsSync(file)) return {};
  const parsed = {};
  for (const rawLine of fs.readFileSync(file, 'utf8').split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) continue;
    const match = line.match(/^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/);
    if (!match) continue;
    let value = match[2].trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    parsed[match[1]] = value;
  }
  return parsed;
}

const rootEnv = loadRootEnv();
const DESKTOP_BRIDGE_PORT = Number.parseInt(rootEnv.SCP_LLM_BRIDGE_PORT || process.env.SCP_LLM_BRIDGE_PORT || '11434', 10) || 11434;
const DESKTOP_BRIDGE_URL = 'http://127.0.0.1:' + DESKTOP_BRIDGE_PORT;
// [GLM-AUDIT-FIX-①] Single source of truth for backend port.
// Set SCP_PORT in .env or environment to override. Default: 8000 (production).
const SCP_BACKEND_PORT = Number.parseInt(
  rootEnv.SCP_PORT || process.env.SCP_PORT || '8000', 10
) || 8000;
const SCP_BASE_URL = `http://127.0.0.1:${SCP_BACKEND_PORT}`;
const PRODUCTION_CSP = [
  "default-src 'self'",
  "base-uri 'self'",
  "object-src 'none'",
  "frame-ancestors 'none'",
  "form-action 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  // [GLM-AUDIT-FIX-①] Use SCP_BACKEND_PORT so CSP matches actual backend port
  `connect-src 'self' http://127.0.0.1:3000 http://localhost:3000 ws://127.0.0.1:${SCP_BACKEND_PORT} ws://localhost:${SCP_BACKEND_PORT}`,
].join('; ');
function installDesktopCsp() {
  const mode = String(rootEnv.SCP_DESKTOP_CSP_MODE || process.env.SCP_DESKTOP_CSP_MODE || "dev").trim().toLowerCase();
  if (mode !== "production") return;
  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    callback({ responseHeaders: { ...details.responseHeaders, "Content-Security-Policy": [PRODUCTION_CSP] } });
  });
}

let mainWindow = null;
let splashWindow = null;
let quitting = false;
const children = new Map();
const hasSingleInstanceLock = app.requestSingleInstanceLock();
if (!hasSingleInstanceLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.show();
      mainWindow.focus();
    }
  });
}

function envForService(extra = {}) {
  const merged = {
    ...rootEnv,
    ...process.env,
    ...extra,
    SCP_ROOT,
    SCP_DESKTOP_MODE: '1',
    SCP_MODEL_VERSION: MODEL_VERSION,
    ELECTRON_RUN_AS_NODE: extra.ELECTRON_RUN_AS_NODE,
  };
  const tokenFile = path.join(SCP_ROOT, ".private-secrets", "scp-scheduler-admin-token")
  if (!merged.SCP_SCHEDULER_ADMIN_TOKEN && !merged.SCP_SCHEDULER_ADMIN_TOKEN_FILE && fs.existsSync(tokenFile)) merged.SCP_SCHEDULER_ADMIN_TOKEN_FILE = tokenFile
  for (const key of Object.keys(merged)) {
    if (key.endsWith("_FILE") && merged[key] && !path.isAbsolute(String(merged[key]))) {
      merged[key] = path.resolve(SCP_ROOT, String(merged[key]));
    }
  }
  return merged
}

function commandFor(label) {
  if (IS_PACKAGED) {
    return {
      bridge: { cwd: RUNTIME_ROOT, file: path.join(RUNTIME_ROOT, 'scp-llm-bridge.exe'), args: [], env: { SCP_BASE_URL, ZAI_BRIDGE_PORT: String(DESKTOP_BRIDGE_PORT), ZAI_BRIDGE_HOST: '127.0.0.1' } },
      scheduler: { cwd: RUNTIME_ROOT, file: path.join(RUNTIME_ROOT, 'scp-loop-scheduler.exe'), args: [], env: { SCP_BASE_URL, LLM_BRIDGE_URL: DESKTOP_BRIDGE_URL } },
      worker: { cwd: RUNTIME_ROOT, file: path.join(RUNTIME_ROOT, 'scp-autofix-worker.exe'), args: ['--max-jobs', '1', '--watch'], env: { SCP_AUTOFIX_WORKER_ROOT: SCP_ROOT, SCP_AUTOFIX_WORKER_DATA_DIR: path.join(SCP_ROOT, 'data'), SCP_AUTOFIX_WORKER_AUTO_APPLY_RISK: 'low' } },
      // [GLM-AUDIT-FIX-①] Use SCP_BACKEND_PORT instead of hardcoded 8000
      scp: { cwd: RUNTIME_ROOT, file: path.join(RUNTIME_ROOT, 'scp-backend.exe'), args: [String(SCP_BACKEND_PORT)], env: { OLLAMA_HOST: DESKTOP_BRIDGE_URL, SCP_PORT: String(SCP_BACKEND_PORT) } },
      dashboard: { cwd: path.join(RUNTIME_ROOT, 'dashboard'), file: process.execPath, args: [path.join(RUNTIME_ROOT, 'dashboard', 'server.js')], env: { HOSTNAME: '127.0.0.1', PORT: '3000', ELECTRON_RUN_AS_NODE: '1', NEXT_TELEMETRY_DISABLED: '1' } },
    }[label];
  }
  const commands = {
    bridge: {
      cwd: path.join(SCP_ROOT, 'mini-services', 'llm-bridge'),
      file: 'bun',
      args: ['run', 'dev'],
      env: {
        SCP_BASE_URL,
        ZAI_BRIDGE_PORT: String(DESKTOP_BRIDGE_PORT),
        ZAI_BRIDGE_HOST: '127.0.0.1',
      },
    },
    scheduler: {
      cwd: path.join(SCP_ROOT, 'mini-services', 'loop-scheduler'),
      file: 'bun',
      args: ['run', 'dev'],
      env: {
        SCP_ROOT,
        SCP_MODEL_VERSION: MODEL_VERSION,
        SCP_BASE_URL,
        LLM_BRIDGE_URL: DESKTOP_BRIDGE_URL,
      },
    },
    worker: {
      cwd: SCP_ROOT,
      file: path.join(SCP_ROOT, 'scp', 'venv', 'Scripts', 'python.exe'),
      args: ['-m', 'scp.autofix.deterministic_worker', '--max-jobs', '1', '--watch'],
      env: {
        SCP_AUTOFIX_WORKER_ROOT: SCP_ROOT,
        SCP_AUTOFIX_WORKER_DATA_DIR: path.join(SCP_ROOT, 'data'),
        SCP_AUTOFIX_WORKER_AUTO_APPLY_RISK: 'low',
      },
    },
    scp: {
      cwd: SCP_ROOT,
      file: path.join(SCP_ROOT, 'scp', 'venv', 'Scripts', 'python.exe'),
      // [GLM-AUDIT-FIX-①] Use SCP_BACKEND_PORT; also pass SCP_PORT env var for api_server.py
      args: ['-m', 'scp', String(SCP_BACKEND_PORT)],
      env: { SCP_PORT: String(SCP_BACKEND_PORT) },
    },
    dashboard: {
      cwd: path.join(SCP_ROOT, 'dashboard'),
      file: 'bun',
      args: ['run', 'dev'],
    },
  };
  return commands[label];
}

function appendLog(label, chunk) {
  const line = `[${new Date().toISOString()}] ${chunk.toString()}`;
  const logDir = path.join(SCP_ROOT, 'data', 'desktop-logs');
  try {
    fs.mkdirSync(logDir, { recursive: true });
    fs.appendFileSync(path.join(logDir, `${label}.log`), line);
  } catch (_) {
    // Logging must never prevent the desktop shell from opening.
  }
}

function startService(label) {
  if (children.has(label)) return children.get(label);
  const spec = commandFor(label);
  if (!spec || !fs.existsSync(spec.cwd)) {
    throw new Error(`Không tìm thấy thư mục dịch vụ: ${label}`);
  }
  if (IS_PACKAGED && !fs.existsSync(spec.file)) {
    throw new Error(`Thiếu packaged runtime cho dịch vụ ${label}: ${spec.file}`);
  }

  const child = spawn(spec.file, spec.args, {
    cwd: spec.cwd,
    env: envForService(spec.env || {}),
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  children.set(label, child);
  child.stdout.on('data', (chunk) => appendLog(label, chunk));
  child.stderr.on('data', (chunk) => appendLog(`${label}-error`, chunk));
  child.on('error', (error) => appendLog(label, `process error: ${error.message}\n`));
  child.on('exit', (code, signal) => {
    appendLog(label, `process exited code=${code} signal=${signal}\n`);
    children.delete(label);
    if (!quitting && label === 'dashboard' && mainWindow) {
      showError(`Dashboard đã dừng (code ${code ?? 'không xác định'}).`);
    }
  });
  return child;
}

function killTree(child) {
  if (!child || child.killed || !child.pid) return;
  try {
    spawn('taskkill', ['/pid', String(child.pid), '/t', '/f'], { windowsHide: true, stdio: 'ignore' });
  } catch (_) {
    try { child.kill(); } catch (_) {}
  }
}

function stopServices() {
  for (const child of children.values()) killTree(child);
  children.clear();
}

function probeHttp(url) {
  return new Promise((resolve) => {
    try {
      const target = new URL(url);
      const req = http.get({ hostname: target.hostname, port: Number(target.port), path: target.pathname, timeout: 1200 }, (res) => {
        res.resume();
        resolve(res.statusCode >= 200 && res.statusCode < 300);
      });
      req.setTimeout(1200, () => { req.destroy(); resolve(false); });
      req.on('error', () => resolve(false));
    } catch (_) {
      resolve(false);
    }
  });
}

async function classifyExistingStack() {
  const checks = await Promise.all([
    probeHttp('http://127.0.0.1:3000/'),
    probeHttp('http://127.0.0.1:3030/'),
    probeHttp(`${SCP_BASE_URL}/health`),  // [GLM-AUDIT-FIX-①] use SCP_BASE_URL
    probeHttp(`http://127.0.0.1:${DESKTOP_BRIDGE_PORT}/api/tags`),
  ]);
  const healthy = checks.filter(Boolean).length;
  if (healthy === checks.length) return 'external-healthy';
  if (healthy === 0) return 'none';
  return 'partial';
}

async function waitForStableStackState(timeoutMs = 45000) {
  const deadline = Date.now() + timeoutMs;
  let state = 'partial';
  while (Date.now() < deadline) {
    state = await classifyExistingStack();
    if (state !== 'partial') return state;
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  return state;
}

function isPortReady(port, host = '127.0.0.1') {
  return probeHttp(`http://${host}:${port}/`);
}

async function waitForDashboard() {
  const started = Date.now();
  while (Date.now() - started < STARTUP_TIMEOUT_MS) {
    if (await isPortReady(3000)) return true;
    await new Promise((resolve) => setTimeout(resolve, 1500));
  }
  return false;
}

function showError(message) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    dialog.showMessageBox(mainWindow, { type: 'error', title: 'SCP Desktop', message });
  } else {
    dialog.showErrorBox('SCP Desktop', message);
  }
}

function createSplash() {
  splashWindow = new BrowserWindow({
    width: 480,
    height: 300,
    frame: false,
    resizable: false,
    center: true,
    backgroundColor: '#08111f',
    webPreferences: { contextIsolation: true },
  });
  const html = `<!doctype html><html><body style="margin:0;background:#08111f;color:#dbeafe;font:14px Segoe UI,Arial;display:grid;place-items:center;height:100vh"><main style="text-align:center"><div style="font-size:12px;letter-spacing:3px;color:#60a5fa">SCP DNA</div><h1 style="margin:12px 0 8px;color:#f8fafc">Desktop Control Center</h1><p style="margin:0;color:#94a3b8">Đang khởi động hệ thống · mô hình ${MODEL_VERSION}</p><div style="margin:22px auto 0;width:260px;height:4px;background:#1e293b;border-radius:99px;overflow:hidden"><div style="width:45%;height:100%;background:#38bdf8;animation:load 1.2s infinite ease-in-out"></div></div></main><style>@keyframes load{0%{transform:translateX(-110%)}100%{transform:translateX(600%)}}</style></body></html>`;
  splashWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`);
}

function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1080,
    minHeight: 680,
    show: true,
    title: `SCP DNA · Mô hình ${MODEL_VERSION}`,
    backgroundColor: '#08111f',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      sandbox: true,
    },
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('http://127.0.0.1:') || url.startsWith('http://localhost:')) {
      return { action: 'allow' };
    }
    shell.openExternal(url);
    return { action: 'deny' };
  });

  mainWindow.on('closed', () => { mainWindow = null; });
  mainWindow.webContents.on('console-message', (event) => {
    const level = event?.level ?? 'info';
    const message = event?.message ?? '';
    const line = event?.lineNumber ?? event?.line ?? 0;
    const sourceId = event?.sourceId ?? '';
    appendLog('desktop-console', `level=${level} ${sourceId}:${line} ${message}\\n`);
  });
  mainWindow.webContents.on('render-process-gone', (_event, details) => {
    appendLog('desktop-console', `render process gone: ${details.reason}\\n`);
  });
  mainWindow.webContents.on('did-finish-load', () => appendLog('desktop-console', 'dashboard did-finish-load\\n'));
  mainWindow.loadURL(DASHBOARD_URL).catch((error) => {
    appendLog('desktop', `dashboard load failed: ${error.message}\\n`);
    showError(`Không tải được dashboard: ${error.message}`);
  });
}

function buildMenu() {
  const template = [
    {
      label: 'SCP',
      submenu: [
        { label: `Mô hình đang dùng: ${MODEL_VERSION}`, enabled: false },
        { type: 'separator' },
        { label: 'Tải lại dashboard', accelerator: 'Ctrl+R', click: () => mainWindow?.reload() },
        { label: 'Mở thư mục dự án', click: () => shell.openPath(SCP_ROOT) },
        { label: 'Mở thư mục log', click: () => shell.openPath(path.join(SCP_ROOT, 'data', 'desktop-logs')) },
        { type: 'separator' },
        { label: 'Thoát', accelerator: 'Alt+F4', click: () => app.quit() },
      ],
    },
    {
      label: 'Trợ giúp',
      submenu: [
        { label: 'Mở README Windows', click: () => shell.openPath(path.join(SCP_ROOT, 'WINDOWS-README.md')) },
        { label: 'Mở DevTools', click: () => mainWindow?.webContents.openDevTools() },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

async function boot() {
  createSplash();
  const required = ['bridge', 'scheduler', 'scp', 'worker', 'dashboard'];
  try {
    const existingStack = await waitForStableStackState();
    if (existingStack === 'external-healthy') {
      appendLog('desktop', 'Healthy external SCP stack detected; attaching without starting duplicate children.\\n');
    } else if (existingStack === 'partial') {
      throw new Error('SCP đang ở trạng thái partial: một hoặc nhiều port đã bị chiếm nhưng stack chưa healthy. Không khởi động duplicate; hãy chờ supervisor recovery hoặc mở log.');
    } else {
      for (const service of required) startService(service);
    }
  } catch (error) {
    splashWindow?.close();
    showError(error.message);
    app.quit();
    return;
  }

  const ready = await waitForDashboard();
  if (!ready) {
    splashWindow?.close();
    showError('Dashboard chưa phản hồi sau 180 giây. Hãy mở thư mục data\\desktop-logs để xem log.');
    app.quit();
    return;
  }
  createMainWindow();
  buildMenu();
  splashWindow?.close();
  mainWindow.show();
  mainWindow.focus();
}

app.whenReady().then(() => {
  const isLocalDashboard = (webContents, requestingOrigin = '') => {
    const raw = requestingOrigin || (webContents && webContents.getURL ? webContents.getURL() : '');
    try {
      const url = new URL(raw);
      return (url.hostname === '127.0.0.1' || url.hostname === 'localhost') && url.port === '3000';
    } catch (_) {
      return false;
    }
  };
  // The previous callback(false) silently blocked camera and microphone for
  // every renderer, so the dashboard buttons could never work in Desktop.
  // Allow only media permission for the local SCP dashboard; keep all other
  // permissions denied by default.
  session.defaultSession.setPermissionRequestHandler((webContents, permission, callback) => {
    callback(permission === 'media' && isLocalDashboard(webContents));
  });
  session.defaultSession.setPermissionCheckHandler((webContents, permission, requestingOrigin) => {
    return permission === 'media' && isLocalDashboard(webContents, requestingOrigin);
  });
  installDesktopCsp();
  ipcMain.handle('notify-critical', (_event, payload = {}) => {
    const title = String(payload.title || 'SCP · Cảnh báo nghiêm trọng');
    const body = String(payload.body || 'SCP cần được kiểm tra ngay.');
    if (ElectronNotification.isSupported()) {
      new ElectronNotification({ title, body, urgency: 'critical', silent: false }).show();
      return true;
    }
    return false;
  });
  boot();
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createMainWindow(); });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  if (quitting) return;
  quitting = true;
  stopServices();
});
