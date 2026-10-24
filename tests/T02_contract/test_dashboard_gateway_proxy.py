"""[AUDIT-FIX M-DASHBOARD] Contract test — Dashboard in-container gateway proxy.

Verifies that dashboard/gateway.js:
1. Injects x-forwarded-for: 127.0.0.1 and x-scp-proxy-secret for local browser/host requests.
2. Preserves fail-closed invariants by refusing to inject credentials for:
   - External/spoofed Host headers (e.g. evil.com).
   - Existing external x-forwarded-for hops (e.g. 203.0.113.1).
   - Pre-existing explicit client secrets (does not overwrite bad secret with valid secret).
   - Remote non-local client socket addresses.
"""
import json
import secrets
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_DIR = ROOT / "dashboard"
GATEWAY_JS = DASHBOARD_DIR / "gateway.js"

NODE_PROBE_SCRIPT = r"""
const http = require('http');
const path = require('path');
const {
  createGatewayServer,
  isLocalOrDockerIp,
  isLocalHost,
  hasExternalHop,
} = require('./dashboard/gateway.js');

// 1. Unit tests for helpers
const ipTests = [
  ['127.0.0.1', true],
  ['::1', true],
  ['::ffff:127.0.0.1', true],
  ['172.18.0.1', true],
  ['172.20.0.5', true],
  ['10.0.1.2', true],
  ['192.168.1.100', true],
  ['203.0.113.195', false],
  ['8.8.8.8', false],
  ['', false],
  [null, false],
];
for (const [ip, expected] of ipTests) {
  const actual = isLocalOrDockerIp(ip);
  if (actual !== expected) {
    console.error(`IP check failed for ${ip}: expected ${expected}, got ${actual}`);
    process.exit(1);
  }
}

const hostTests = [
  ['localhost', true],
  ['localhost:3000', true],
  ['127.0.0.1:3000', true],
  ['[::1]:3000', true],
  ['dashboard:3000', true],
  ['scp-full-dashboard-1:3000', true],
  ['my-app.localhost:3000', true],
  ['evil.com', false],
  ['attacker.org:3000', false],
  ['203.0.113.1', false],
];
for (const [host, expected] of hostTests) {
  const actual = isLocalHost(host);
  if (actual !== expected) {
    console.error(`Host check failed for ${host}: expected ${expected}, got ${actual}`);
    process.exit(1);
  }
}

// 2. Integration probe with mock target server
async function runIntegrationTests() {
  const TEST_SECRET = 'test-secret-gateway-contract-12345';
  process.env.SCP_DASHBOARD_PROXY_SECRET = TEST_SECRET;

  let lastTargetHeaders = null;
  const mockTarget = http.createServer((req, res) => {
    lastTargetHeaders = req.headers;
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true }));
  });

  await new Promise(resolve => mockTarget.listen(0, '127.0.0.1', resolve));
  const targetPort = mockTarget.address().port;
  process.env.INTERNAL_PORT = String(targetPort);

  const gatewayServer = createGatewayServer();
  await new Promise(resolve => gatewayServer.listen(0, '127.0.0.1', resolve));
  const gatewayPort = gatewayServer.address().port;

  function doRequest(options) {
    return new Promise((resolve, reject) => {
      const req = http.request({
        hostname: '127.0.0.1',
        port: gatewayPort,
        method: 'GET',
        path: '/api/scp/status',
        ...options,
      }, (res) => {
        let body = '';
        res.on('data', chunk => body += chunk);
        res.on('end', () => resolve({ status: res.statusCode, body, headers: res.headers }));
      });
      req.on('error', reject);
      req.end();
    });
  }

  // Case A: Local client, local host, no extra headers -> Injects XFF 127.0.0.1 & proxy secret
  lastTargetHeaders = null;
  const resA = await doRequest({ headers: { host: 'localhost:' + gatewayPort } });
  if (resA.status !== 200) throw new Error(`Case A failed status: ${resA.status}`);
  if (lastTargetHeaders['x-forwarded-for'] !== '127.0.0.1') {
    throw new Error(`Case A missing x-forwarded-for: ${lastTargetHeaders['x-forwarded-for']}`);
  }
  if (lastTargetHeaders['x-scp-proxy-secret'] !== TEST_SECRET) {
    throw new Error(`Case A missing x-scp-proxy-secret: ${lastTargetHeaders['x-scp-proxy-secret']}`);
  }

  // Case B: Negative / Anti-placebo: Spoofed Host header (evil.com) -> Does NOT inject proxy secret
  lastTargetHeaders = null;
  const resB = await doRequest({ headers: { host: 'evil.com' } });
  if (lastTargetHeaders['x-scp-proxy-secret']) {
    throw new Error(`Case B leak: injected secret for evil.com`);
  }

  // Case C: Negative / Anti-placebo: Spoofed external XFF -> Does NOT inject proxy secret, preserves hop
  lastTargetHeaders = null;
  const resC = await doRequest({
    headers: {
      host: 'localhost:' + gatewayPort,
      'x-forwarded-for': '203.0.113.1'
    }
  });
  if (lastTargetHeaders['x-scp-proxy-secret']) {
    throw new Error(`Case C leak: injected secret despite external XFF hop`);
  }
  if (lastTargetHeaders['x-forwarded-for'] !== '203.0.113.1') {
    throw new Error(`Case C error: did not preserve external XFF hop`);
  }

  // Case D: Negative / Anti-placebo: Bad secret provided by client -> Does NOT overwrite with valid secret
  lastTargetHeaders = null;
  const resD = await doRequest({
    headers: {
      host: 'localhost:' + gatewayPort,
      'x-scp-proxy-secret': 'wrong-client-secret'
    }
  });
  if (lastTargetHeaders['x-scp-proxy-secret'] !== 'wrong-client-secret') {
    throw new Error(`Case D error: overwrote bad client secret`);
  }

  await new Promise(r => mockTarget.close(r));
  await new Promise(r => gatewayServer.close(r));

  console.log(JSON.stringify({ status: 'GATEWAY_CONTRACT_PASS' }));
}

runIntegrationTests().catch(err => {
  console.error('Test suite error:', err);
  process.exit(1);
});
"""


def test_gateway_file_exists():
    assert GATEWAY_JS.exists(), f"Missing {GATEWAY_JS}"
    content = GATEWAY_JS.read_text(encoding="utf-8")
    assert "x-scp-proxy-secret" in content
    assert "x-forwarded-for" in content
    assert "isLocalOrDockerIp" in content
    assert "isLocalHost" in content


@pytest.mark.skipif(
    shutil.which("node") is None,
    reason="[Wave-C C3-W2 2026-10-23] env-layer: gateway contract probe chạy node thật "
    "(dashboard/gateway.js); audit container không có node binary — fail "
    "FileNotFoundError trước khi chạm assertion. Skip có điều kiện, ledger Wave-C C3-W2.",
)
def test_gateway_contract_node_execution():
    node = shutil.which("node") or "node"
    probe = ROOT / f".tmp-gateway-probe-{secrets.token_hex(6)}.js"
    probe.write_text(NODE_PROBE_SCRIPT, encoding="utf-8")
    try:
        proc = subprocess.run(
            [node, str(probe)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        probe.unlink(missing_ok=True)

    assert proc.returncode == 0, f"Gateway contract failed:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    assert "GATEWAY_CONTRACT_PASS" in proc.stdout
