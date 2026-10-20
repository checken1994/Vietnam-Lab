/**
 * SCP Dashboard In-Container Gateway Proxy
 * 
 * Sits in front of Next.js standalone server inside the dashboard container.
 * Next.js runs on internal port (default 3001) bound strictly to 127.0.0.1.
 * Gateway runs on container port (default 3000) bound to 0.0.0.0.
 * 
 * Security Contract:
 * - Legitimate requests from localhost/docker bridge to local hostnames
 *   receive injected trusted reverse proxy headers (x-forwarded-for: 127.0.0.1,
 *   x-scp-proxy-secret: process.env.SCP_DASHBOARD_PROXY_SECRET) so Next.js
 *   middleware allows web browsers on host to access dashboard APIs without manual headers.
 * - Any request with spoofed external host, external XFF hops, or bad secret
 *   is passed through untouched and rejected fail-closed (403) by middleware.ts.
 */

const http = require('http');
const net = require('net');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');

const GATEWAY_PORT = parseInt(process.env.PORT || '3000', 10);
const GATEWAY_HOST = process.env.GATEWAY_HOST || '0.0.0.0';
const getInternalPort = () => parseInt(process.env.INTERNAL_PORT || '3001', 10);
const INTERNAL_HOST = '127.0.0.1';

const LOCAL_IPS = new Set([
  '127.0.0.1',
  '::1',
  'localhost',
  '::ffff:127.0.0.1',
]);

const ALLOWED_LOCAL_HOSTNAMES = new Set([
  'localhost',
  '127.0.0.1',
  '[::1]',
  '::1',
  '[::ffff:127.0.0.1]',
  'dashboard',
  'scp-full-dashboard-1',
]);

try {
  const os = require('os');
  ALLOWED_LOCAL_HOSTNAMES.add(os.hostname().toLowerCase());
} catch (_) {}

function isLocalOrDockerIp(ip) {
  if (!ip) return false;
  const clean = ip.replace(/^::ffff:/, '');
  if (LOCAL_IPS.has(clean) || clean === '127.0.0.1') return true;

  const parts = clean.split('.').map(Number);
  if (parts.length === 4 && parts.every(n => !isNaN(n) && n >= 0 && n <= 255)) {
    if (parts[0] === 127) return true;
    if (parts[0] === 10) return true;
    if (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31) return true;
    if (parts[0] === 192 && parts[1] === 168) return true;
  }
  return false;
}

function extractHostname(hostHeader) {
  if (!hostHeader) return '';
  if (hostHeader.startsWith('[')) {
    const end = hostHeader.indexOf(']');
    if (end !== -1) {
      return hostHeader.slice(0, end + 1).toLowerCase();
    }
  }
  return hostHeader.split(':')[0].toLowerCase();
}

function isLocalHost(hostHeader) {
  if (!hostHeader) return false;
  const host = extractHostname(hostHeader);
  return ALLOWED_LOCAL_HOSTNAMES.has(host) || host.endsWith('.localhost');
}

function hasExternalHop(req) {
  const forwarded = req.headers['x-forwarded-for'];
  const realIp = req.headers['x-real-ip'];
  const hops = forwarded
    ? forwarded.split(',').map(s => s.trim()).filter(Boolean)
    : realIp
    ? [realIp.trim()]
    : [];

  if (hops.length === 0) return false;
  return hops.some(ip => !LOCAL_IPS.has(ip));
}

// Locate server.js
function findServerJs() {
  const candidates = [
    path.join(__dirname, 'server.js'),
    path.join(__dirname, '.next', 'standalone', 'server.js'),
    path.join(process.cwd(), 'server.js'),
    path.join(process.cwd(), '.next', 'standalone', 'server.js'),
  ];
  return candidates.find(p => fs.existsSync(p));
}

let nextProcess = null;

function startNextServer() {
  const internalPort = getInternalPort();
  const serverJs = findServerJs();
  if (!serverJs) {
    console.warn('[gateway] server.js not found in candidate paths; assuming Next.js started externally on port ' + internalPort);
    return;
  }

  console.log(`[gateway] Spawning Next.js server from ${serverJs} on internal port ${internalPort}...`);
  const env = {
    ...process.env,
    PORT: String(internalPort),
    HOSTNAME: INTERNAL_HOST,
  };

  nextProcess = spawn(process.execPath, [serverJs], {
    cwd: path.dirname(serverJs),
    env,
    stdio: 'inherit',
  });

  nextProcess.on('exit', (code, signal) => {
    console.log(`[gateway] Next.js process exited with code ${code} signal ${signal}`);
    if (code !== 0 && code !== null) {
      process.exit(code);
    }
  });

  nextProcess.on('error', (err) => {
    console.error('[gateway] Failed to spawn Next.js process:', err);
    process.exit(1);
  });
}

function waitForServer(host, port, timeoutMs = 30000) {
  const startTime = Date.now();
  return new Promise((resolve, reject) => {
    function tryConnect() {
      const socket = net.connect({ host, port }, () => {
        socket.end();
        resolve();
      });
      socket.on('error', () => {
        socket.destroy();
        if (Date.now() - startTime > timeoutMs) {
          reject(new Error(`Timed out waiting for internal server on ${host}:${port}`));
        } else {
          setTimeout(tryConnect, 150);
        }
      });
    }
    tryConnect();
  });
}

function createGatewayServer() {
  const srv = http.createServer((req, res) => {
    const remoteIp = req.socket.remoteAddress;
    const host = req.headers['host'];
    const isClientLocal = isLocalOrDockerIp(remoteIp);
    const isHostLocal = isLocalHost(host);
    const isExternalHop = hasExternalHop(req);
    const hasExplicitSecret = Boolean(req.headers['x-scp-proxy-secret']);

    const targetHeaders = { ...req.headers };

    // Only inject trusted proxy credentials if client IP and Host are local,
    // and no external spoofed hops are present.
    if (isClientLocal && isHostLocal && !isExternalHop) {
      targetHeaders['x-forwarded-for'] = '127.0.0.1';
      targetHeaders['x-real-ip'] = '127.0.0.1';
      targetHeaders['x-forwarded-proto'] = targetHeaders['x-forwarded-proto'] || 'http';
      targetHeaders['x-forwarded-host'] = host || 'localhost';

      if (!hasExplicitSecret) {
        const proxySecret = process.env.SCP_DASHBOARD_PROXY_SECRET || '';
        if (proxySecret) {
          targetHeaders['x-scp-proxy-secret'] = proxySecret;
        }
      }
    }

    const proxyReq = http.request(
      {
        hostname: INTERNAL_HOST,
        port: getInternalPort(),
        path: req.url,
        method: req.method,
        headers: targetHeaders,
      },
      (proxyRes) => {
        res.writeHead(proxyRes.statusCode, proxyRes.statusMessage, proxyRes.headers);
        proxyRes.pipe(res);
      }
    );

    proxyReq.on('error', (err) => {
      if (!res.headersSent) {
        res.writeHead(502, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: 'Bad Gateway', details: err.message }));
      }
    });

    req.pipe(proxyReq);
  });

  // Support WebSockets / HTTP upgrades
  srv.on('upgrade', (req, socket, head) => {
    const remoteIp = req.socket.remoteAddress;
    const host = req.headers['host'];
    const isClientLocal = isLocalOrDockerIp(remoteIp);
    const isHostLocal = isLocalHost(host);
    const isExternalHop = hasExternalHop(req);
    const hasExplicitSecret = Boolean(req.headers['x-scp-proxy-secret']);

    const targetHeaders = { ...req.headers };

    if (isClientLocal && isHostLocal && !isExternalHop) {
      targetHeaders['x-forwarded-for'] = '127.0.0.1';
      targetHeaders['x-real-ip'] = '127.0.0.1';
      targetHeaders['x-forwarded-proto'] = targetHeaders['x-forwarded-proto'] || 'http';
      targetHeaders['x-forwarded-host'] = host || 'localhost';

      if (!hasExplicitSecret) {
        const proxySecret = process.env.SCP_DASHBOARD_PROXY_SECRET || '';
        if (proxySecret) {
          targetHeaders['x-scp-proxy-secret'] = proxySecret;
        }
      }
    }

    const proxyReq = http.request({
      hostname: INTERNAL_HOST,
      port: getInternalPort(),
      path: req.url,
      method: req.method,
      headers: targetHeaders,
    });

    proxyReq.on('upgrade', (proxyRes, proxySocket, proxyHead) => {
      socket.write(
        `HTTP/${proxyRes.httpVersion} ${proxyRes.statusCode} ${proxyRes.statusMessage}\r\n` +
        Object.entries(proxyRes.headers)
          .map(([k, v]) => Array.isArray(v) ? v.map(val => `${k}: ${val}\r\n`).join('') : `${k}: ${v}\r\n`)
          .join('') +
        '\r\n'
      );
      if (proxyHead && proxyHead.length) socket.write(proxyHead);
      if (head && head.length) proxySocket.write(head);
      proxySocket.pipe(socket);
      socket.pipe(proxySocket);
    });

    proxyReq.on('error', () => {
      socket.destroy();
    });

    proxyReq.end();
  });

  return srv;
}

const server = createGatewayServer();

function shutdown() {
  console.log('[gateway] Shutting down gateway and internal Next.js server...');
  if (nextProcess && !nextProcess.killed) {
    nextProcess.kill('SIGTERM');
  }
  server.close(() => {
    process.exit(0);
  });
  setTimeout(() => process.exit(0), 5000).unref();
}

process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);

// Main bootstrap
async function main() {
  const internalPort = getInternalPort();
  startNextServer();
  console.log(`[gateway] Waiting for Next.js internal server on http://${INTERNAL_HOST}:${internalPort}...`);
  await waitForServer(INTERNAL_HOST, internalPort);
  console.log(`[gateway] Internal server ready. Binding gateway to http://${GATEWAY_HOST}:${GATEWAY_PORT}...`);

  server.listen(GATEWAY_PORT, GATEWAY_HOST, () => {
    console.log(`[gateway] SCP Dashboard Gateway listening on http://${GATEWAY_HOST}:${GATEWAY_PORT}`);
  });
}

if (require.main === module) {
  main().catch((err) => {
    console.error('[gateway] Fatal error during startup:', err);
    process.exit(1);
  });
}

module.exports = {
  server,
  createGatewayServer,
  isLocalOrDockerIp,
  isLocalHost,
  hasExternalHop,
};
