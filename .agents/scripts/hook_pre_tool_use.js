const fs = require('fs');
const path = require('path');

function isSubagent(transcriptPath) {
  if (!transcriptPath || !fs.existsSync(transcriptPath)) {
    return false;
  }
  try {
    const content = fs.readFileSync(transcriptPath, 'utf-8');
    // Check first 5 lines for sender=
    const lines = content.split('\n').slice(0, 5);
    for (const line of lines) {
      if (line.includes('sender=')) {
        return true;
      }
    }
  } catch (e) {
    // If error reading, default to false
  }
  return false;
}

function main() {
  let raw = '';
  try {
    raw = fs.readFileSync(0, 'utf-8');
  } catch (e) {}

  let payload = {};
  try {
    payload = JSON.parse(raw);
  } catch (e) {}

  const toolCall = payload.toolCall || {};
  const toolName = toolCall.name || '';
  const args = toolCall.args || {};
  let filePath = (args.Path || args.path || '').replace(/\\/g, '/').toLowerCase();

  // Strip workspace prefix to get relative path
  let relPath = filePath;
  const workspacePaths = payload.workspacePaths && payload.workspacePaths.length > 0 
    ? payload.workspacePaths 
    : ['d:/scp', 'd:\\scp'];

  for (const ws of workspacePaths) {
    const normWs = ws.replace(/\\/g, '/').toLowerCase();
    if (relPath.startsWith(normWs + '/')) {
      relPath = relPath.substring(normWs.length + 1);
      break;
    }
  }

  // Check if modifying product code, test code, or docker infra
  const isProtectedCode = (
    relPath.startsWith('scp/') ||
    relPath.startsWith('tests/') ||
    relPath === 'dockerfile' ||
    relPath.startsWith('dockerfile.') ||
    relPath.startsWith('docker-compose.')
  );

  if (!isProtectedCode) {
    // Non-protected paths (e.g. docs, reports, plans, inbox, .agents, .council-loop) are allowed
    process.stdout.write(JSON.stringify({ decision: 'allow' }));
    return;
  }

  // If protected code: verify if caller is an authorized subagent
  const transcriptPath = payload.transcriptPath || '';
  if (isSubagent(transcriptPath)) {
    // Authorized subagent (DeepCoder / worker)
    process.stdout.write(JSON.stringify({ decision: 'allow' }));
    return;
  }

  // Root Orchestrator attempting to modify protected code solo -> HARD DENY
  const denyResponse = {
    decision: 'deny',
    reason: '[BLOCKED BY ORCHESTRATOR HOOK]: Antigravity là Orchestrator tuyệt đối. Bạn BỊ CẤM tự ý sửa đổi code sản phẩm (scp/) hoặc test (tests/) (vi phạm FO-01). Mọi thay đổi code bắt buộc phải phân rã và ủy quyền cho Subagent chuyên trách (DeepCoder / teamwork) thông qua invoke_subagent!'
  };

  process.stdout.write(JSON.stringify(denyResponse));
}

main();
