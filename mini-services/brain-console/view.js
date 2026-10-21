/**
 * SCP Brain Console — self-contained HTML view.
 *
 * One file, no build step, no CDN. The page polls /api/brain and renders the
 * projection. It is deliberately dependency-free so it works inside the
 * hardened compose profile (read-only fs, no network egress beyond loopback).
 */

/** Render the live brain projection page. */
export function renderBrainPage(pollMs = 5000) {
  return `<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>SCP Brain Console</title>
<style>
  :root {
    --bg: #0b0f14; --panel: #131a22; --panel2: #0f151c; --line: #223040;
    --text: #d7e2ee; --muted: #7e93a8; --ok: #34d399; --warn: #fbbf24;
    --fail: #f87171; --unknown: #a78bfa; --accent: #38bdf8;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font: 13px/1.5 ui-monospace, SFMono-Regular, "Cascadia Code", Menlo, monospace;
  }
  header {
    position: sticky; top: 0; z-index: 5; background: rgba(11,15,20,.95);
    border-bottom: 1px solid var(--line); padding: 14px 20px;
    display: flex; gap: 16px; align-items: center; flex-wrap: wrap;
  }
  h1 { font-size: 15px; margin: 0; letter-spacing: .5px; }
  h2 { font-size: 12px; margin: 0 0 10px; text-transform: uppercase; letter-spacing: 1px; color: var(--muted); }
  .badge { padding: 3px 12px; border-radius: 3px; font-weight: 700; letter-spacing: 1px; }
  .PASS { background: rgba(52,211,153,.15); color: var(--ok); border: 1px solid var(--ok); }
  .WARN { background: rgba(251,191,36,.15); color: var(--warn); border: 1px solid var(--warn); }
  .FAIL { background: rgba(248,113,113,.15); color: var(--fail); border: 1px solid var(--fail); }
  .UNKNOWN { background: rgba(167,139,250,.15); color: var(--unknown); border: 1px solid var(--unknown); }
  .meta { color: var(--muted); font-size: 11px; }
  main { padding: 18px 20px 60px; display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(460px, 1fr)); }
  section { background: var(--panel); border: 1px solid var(--line); border-radius: 6px; padding: 14px 16px; overflow: hidden; }
  section.wide { grid-column: 1 / -1; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th, td { text-align: left; padding: 4px 8px 4px 0; border-bottom: 1px solid var(--panel2); vertical-align: top; }
  th { color: var(--muted); font-weight: 500; font-size: 11px; text-transform: uppercase; }
  tr:last-child td { border-bottom: none; }
  .ok { color: var(--ok); } .warn { color: var(--warn); } .fail { color: var(--fail); }
  .unknown { color: var(--unknown); } .muted { color: var(--muted); }
  .states { display: grid; grid-template-columns: repeat(auto-fill, minmax(128px, 1fr)); gap: 6px; }
  .state { background: var(--panel2); border: 1px solid var(--line); border-radius: 4px; padding: 6px 8px; }
  .state .n { font-size: 17px; font-weight: 700; }
  .state .s { color: var(--muted); font-size: 10px; letter-spacing: .4px; }
  .state.terminal { border-color: #3a4b60; }
  .state.hot { border-color: var(--warn); }
  .state.bad { border-color: var(--fail); }
  .bar { height: 4px; background: var(--panel2); border-radius: 2px; overflow: hidden; margin-top: 4px; }
  .bar > i { display: block; height: 100%; background: var(--accent); }
  .finding { border-left: 3px solid var(--line); padding: 6px 10px; margin-bottom: 6px; background: var(--panel2); }
  .finding.FAIL { border-left-color: var(--fail); }
  .finding.WARN { border-left-color: var(--warn); }
  .finding.PASS { border-left-color: var(--ok); }
  .finding.UNKNOWN { border-left-color: var(--unknown); }
  .finding .t { font-weight: 700; }
  .finding .s { color: var(--muted); font-size: 11px; }
  .empty { color: var(--muted); font-style: italic; padding: 6px 0; }
  .pill { display: inline-block; padding: 1px 7px; border-radius: 10px; background: var(--panel2); border: 1px solid var(--line); font-size: 11px; margin: 0 4px 4px 0; }
  .scroll { max-height: 330px; overflow: auto; }
  footer { color: var(--muted); font-size: 11px; padding: 10px 20px 30px; border-top: 1px solid var(--line); }
  code { background: var(--panel2); padding: 1px 5px; border-radius: 3px; }
</style>
</head>
<body data-poll="${Number(pollMs) || 5000}">
<header>
  <h1>SCP BRAIN CONSOLE</h1>
  <span id="verdict" class="badge UNKNOWN">...</span>
  <span id="stamp" class="meta">connecting...</span>
  <span id="poll" class="meta"></span>
</header>
<main id="main"><div class="empty">Loading brain projection...</div></main>
<footer>
  Read-only projection. The console never mutates the system it describes.
  <span class="muted">PASS means no defect was observed inside the probed scope - it does not mean the system is correct.</span>
</footer>
<script>
const $ = (id) => document.getElementById(id);
let timer = null;

const esc = (v) => String(v ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const cls = (v) => {
  const s = String(v ?? "").toUpperCase();
  return ["PASS", "WARN", "FAIL", "UNKNOWN"].includes(s) ? s : "UNKNOWN";
};

function section(title, body, wide) {
  return '<section class="' + (wide ? "wide" : "") + '"><h2>' + esc(title) + "</h2>" + body + "</section>";
}

function renderReality(r) {
  const rows = [];
  const push = (kind, label, detail) =>
    rows.push('<div class="finding ' + cls(kind) + '"><div class="t">' + esc(label) + '</div><div class="s">' + esc(detail) + "</div></div>");
  if (!r.corruptDatabases.length && !r.anchorDivergentDatabases.length && !r.brokenEndpoints.length) {
    return '<div class="empty">No contradiction observed between the running system and its physical state.</div>';
  }
  for (const db of r.corruptDatabases) {
    push("FAIL", "Corrupt database: " + db.path, db.observable ? ("integrity_check -> " + db.integrity) : ("unreadable: " + (db.error || "unknown")));
  }
  for (const db of r.anchorDivergentDatabases) {
    push("FAIL", "Journal anchor divergence: " + db.path,
      "physical events " + db.eventCount + " vs control.event_count " + db.anchorEventCount + " (delta " + db.delta + ")");
  }
  for (const ep of r.brokenEndpoints) {
    push("FAIL", "Broken endpoint: " + ep.name, ep.reachable ? ("HTTP " + ep.status) : ("unreachable: " + (ep.error || "unknown")));
  }
  return rows.join("");
}

function renderKernels(kernels) {
  if (!kernels.length) return '<div class="empty">No Task Kernel database is observable.</div>';
  let html = "";
  for (const k of kernels) {
    if (!k.observable) {
      html += '<div class="finding FAIL"><div class="t">' + esc(k.path) + '</div><div class="s">' + esc(k.error || "not observable") + "</div></div>";
      continue;
    }
    const max = Math.max(1, ...k.states.map((s) => s.count));
    html += '<h2 style="margin-top:10px">' + esc(k.path) + ' &middot; ' + k.taskCount + " tasks &middot; " + k.activeLeases + " active leases</h2>";
    html += '<div class="states">';
    for (const s of k.states) {
      const hot = s.count > 0 && !s.terminal ? " hot" : "";
      const bad = s.state === "HUMAN_REVIEW" && s.count > 0 ? " bad" : "";
      html += '<div class="state' + (s.terminal ? " terminal" : "") + hot + bad + '"><div class="n">' + s.count +
        '</div><div class="s">' + esc(s.state) + '</div><div class="bar"><i style="width:' + Math.round((s.count / max) * 100) + '%"></i></div></div>';
    }
    html += "</div>";
  }
  return html;
}

function renderStorage(storage) {
  if (!storage.length) return '<div class="empty">No SQLite database found.</div>';
  let html = '<div class="scroll"><table><thead><tr><th>database</th><th>size</th><th>mode</th><th>tables</th><th>rows</th><th>integrity</th></tr></thead><tbody>';
  for (const db of storage) {
    const state = db.isBackup ? "muted" : (db.integrityOk ? "ok" : "fail");
    const label = db.observable ? (db.integrityOk ? "ok" : (db.integrity || "corrupt")) : (db.error || "unreadable");
    html += "<tr><td>" + esc(db.path) + (db.isBackup ? ' <span class="muted">(backup)</span>' : "") +
      "</td><td>" + (db.sizeBytes / 1048576).toFixed(2) + " MB</td><td>" + esc(db.journalMode || "-") +
      "</td><td>" + db.tableCount + "</td><td>" + (db.rowTotal === null ? "?" : db.rowTotal) +
      '</td><td class="' + state + '">' + esc(label) + "</td></tr>";
  }
  return html + "</tbody></table></div>";
}

function renderJournals(journals) {
  if (!journals.length) return '<div class="empty">No kernel journal observable.</div>';
  let html = "";
  for (const j of journals) {
    if (!j.observable) {
      html += '<div class="finding FAIL"><div class="t">' + esc(j.path) + '</div><div class="s">' + esc(j.error || "not observable") + "</div></div>";
      continue;
    }
    const anchorClass = j.anchorDelta === null ? "unknown" : (j.anchorDelta === 0 ? "ok" : "fail");
    html += "<table><tbody>";
    html += "<tr><th>database</th><td>" + esc(j.path) + "</td></tr>";
    html += '<tr><th>physical events</th><td>' + j.eventCount + "</td></tr>";
    html += '<tr><th>control.event_count</th><td class="' + anchorClass + '">' + (j.anchorEventCount === null ? "n/a" : j.anchorEventCount) + "</td></tr>";
    html += '<tr><th>anchor delta</th><td class="' + anchorClass + '">' + (j.anchorDelta === null ? "n/a" : j.anchorDelta) + "</td></tr>";
    html += '<tr><th>head hash matches</th><td class="' + (j.headMatches === null ? "unknown" : (j.headMatches ? "ok" : "fail")) + '">' + (j.headMatches === null ? "n/a" : (j.headMatches ? "yes" : "NO")) + "</td></tr>";
    html += '<tr><th>tasks / chain breaks</th><td>' + j.taskCount + " / <span class='" + (j.chainBrokenCount ? "fail" : "ok") + "'>" + j.chainBrokenCount + "</span></td></tr>";
    html += "</tbody></table>";
    if (j.recentEvents && j.recentEvents.length) {
      html += '<div class="scroll" style="margin-top:8px"><table><thead><tr><th>seq</th><th>type</th><th>transition</th><th>actor</th><th>at</th></tr></thead><tbody>';
      for (const e of j.recentEvents) {
        html += "<tr><td>" + e.seq + "</td><td>" + esc(e.type) + "</td><td>" + esc(e.fromState || "-") + " &rarr; " + esc(e.toState || "-") +
          "</td><td>" + esc(e.actor) + "</td><td class='muted'>" + esc(e.createdAt) + "</td></tr>";
      }
      html += "</tbody></table></div>";
    }
    html += "<hr style='border:none;border-top:1px solid var(--line);margin:12px 0'>";
  }
  return html;
}

function renderLedgers(l) {
  let html = "";
  if (l.loop.present) {
    html += "<h2>audit loop</h2><table><tbody>";
    html += "<tr><th>total runs</th><td>" + l.loop.totalRuns + "</td></tr>";
    html += '<tr><th>status distribution</th><td>';
    for (const [k, v] of Object.entries(l.loop.statusCounts)) html += '<span class="pill">' + esc(k) + ": " + v + "</span>";
    html += "</td></tr>";
    html += '<tr><th>runs producing findings</th><td class="' + (l.loop.runsWithNonzeroFindings ? "ok" : "fail") + '">' +
      l.loop.runsWithNonzeroFindings + " / " + l.loop.runsWithFindingsField + "</td></tr>";
    html += '<tr><th>error-like runs</th><td class="' + (l.loop.errorLikeRatio > 0.1 ? "warn" : "ok") + '">' +
      (l.loop.errorLikeRatio * 100).toFixed(1) + "%</td></tr>";
    html += "</tbody></table>";
  } else {
    html += '<div class="empty">loop_runs.jsonl is not present.</div>';
  }
  if (l.deepAudit.present) {
    html += "<h2 style='margin-top:12px'>deep audit</h2><table><tbody>";
    html += "<tr><th>records</th><td>" + l.deepAudit.totalRecords + "</td></tr>";
    html += '<tr><th>actions</th><td>';
    for (const [k, v] of Object.entries(l.deepAudit.actionCounts)) html += '<span class="pill">' + esc(k) + ": " + v + "</span>";
    html += "</td></tr>";
    html += '<tr><th>skipped ratio</th><td class="' + (l.deepAudit.skippedRatio > 0.9 ? "fail" : "ok") + '">' +
      (l.deepAudit.skippedRatio * 100).toFixed(1) + "%</td></tr>";
    html += "</tbody></table>";
    if (l.deepAudit.topSkipReasons.length) {
      html += '<div style="margin-top:6px">';
      for (const r of l.deepAudit.topSkipReasons) html += '<div class="muted">&bull; ' + esc(r.reason) + " (" + r.count + ")</div>";
      html += "</div>";
    }
  }
  return html || '<div class="empty">No ledger observable.</div>';
}

function renderServices(services) {
  let html = '<table><thead><tr><th>service</th><th>status</th><th>latency</th><th>note</th></tr></thead><tbody>';
  for (const s of services) {
    const clsName = s.reachable === false ? "fail" : (s.status >= 500 ? "fail" : (s.protected ? "warn" : "ok"));
    const note = s.error ? s.error : (s.protected ? "auth enforced (no credential)" : "");
    html += "<tr><td>" + esc(s.name) + '<div class="muted" style="font-size:10px">' + esc(s.url) + "</div></td><td class='" + clsName + "'>" +
      (s.reachable ? s.status : "DOWN") + "</td><td>" + (s.latencyMs === null ? "-" : s.latencyMs + "ms") +
      "</td><td class='muted'>" + esc(note) + "</td></tr>";
  }
  return html + "</tbody></table>";
}

function renderSkills(skills) {
  if (!skills.present) return '<div class="empty">Skill pack directory not observable: ' + esc(skills.path || "") + "</div>";
  let html = '<div class="muted" style="margin-bottom:6px">' + skills.count + " skills in " + esc(skills.path) + "</div>";
  html += '<div class="scroll"><table><thead><tr><th>skill</th><th>status</th><th>description</th></tr></thead><tbody>';
  for (const s of skills.skills) {
    html += "<tr><td>" + esc(s.name) + "</td><td>" + esc(s.status || "-") + "</td><td class='muted'>" + esc(s.description) + "</td></tr>";
  }
  return html + "</tbody></table></div>";
}

function renderRuntimeAudit(a) {
  if (!a.present) {
    return '<div class="empty">No runtime audit verdict recorded yet: ' + esc(a.reason || "unknown") +
      '. Run <code>python tools/run_runtime_audit.py</code>.</div>';
  }
  if (!a.verdict) return '<div class="empty">Runtime audit ledger is present but empty.</div>';
  let html = "<table><tbody>";
  html += "<tr><th>verdict</th><td><span class='badge " + cls(a.verdict.verdict) + "'>" + esc(a.verdict.verdict) + "</span></td></tr>";
  html += "<tr><th>completed</th><td>" + esc(a.verdict.completedAt) + "</td></tr>";
  if (a.verdict.counts) {
    html += "<tr><th>counts</th><td>";
    for (const [k, v] of Object.entries(a.verdict.counts)) html += '<span class="pill">' + esc(k) + ": " + v + "</span>";
    html += "</td></tr>";
  }
  html += "</tbody></table>";
  if (a.verdict.findings && a.verdict.findings.length) {
    html += '<div style="margin-top:8px">';
    for (const f of a.verdict.findings) {
      html += '<div class="finding ' + cls(f.severity) + '"><div class="t">' + esc(f.title) +
        '</div><div class="s">' + esc(f.summary) + "</div></div>";
    }
    html += "</div>";
  }
  if (a.history && a.history.length > 1) {
    html += "<h2 style='margin-top:12px'>history</h2><table><thead><tr><th>run</th><th>verdict</th><th>findings</th><th>at</th></tr></thead><tbody>";
    for (const h of a.history) {
      html += "<tr><td class='muted'>" + esc(h.runId) + "</td><td class='" + cls(h.verdict) + "'>" + esc(h.verdict) +
        "</td><td>" + h.findingCount + "</td><td class='muted'>" + esc(h.startedAt) + "</td></tr>";
    }
    html += "</tbody></table>";
  }
  return html;
}

async function refresh() {
  try {
    const res = await fetch("/api/brain", { headers: { Accept: "application/json" } });
    const snap = await res.json();
    $("verdict").textContent = snap.reality.severity;
    $("verdict").className = "badge " + cls(snap.reality.severity);
    $("stamp").textContent = "generated " + snap.generatedAt + " | " + snap.elapsedMs + "ms";
    $("poll").textContent = "services " + snap.services.filter((s) => s.reachable !== false && s.status < 500).length + "/" + snap.services.length +
      " | dbs " + snap.storage.length + " | tasks " + snap.kernels.reduce((a, k) => a + (k.taskCount || 0), 0);

    const main = $("main");
    main.innerHTML = [
      section("Reality", renderReality(snap.reality), true),
      section("Task Kernel state machine", renderKernels(snap.kernels), true),
      section("Storage", renderStorage(snap.storage)),
      section("Event journal", renderJournals(snap.journals)),
      section("Loop effectiveness", renderLedgers(snap.ledgers)),
      section("Service topology", renderServices(snap.services)),
      section("Skill pack", renderSkills(snap.skills)),
      section("Runtime audit verdict", renderRuntimeAudit(snap.runtimeAudit), true),
    ].join("");
  } catch (err) {
    $("main").innerHTML = '<section><div class="finding FAIL"><div class="t">Projection failed</div><div class="s">' +
      esc(err.message) + "</div></div></section>";
  }
}

const pollMs = Number(document.body.dataset.poll || 5000);
refresh();
timer = setInterval(refresh, pollMs);
</script>
</body>
</html>`;
}
