/**
 * SCP Brain Console — projection engine.
 *
 * Projects the ENTIRE brain of a running SCP deployment into one place:
 * the Task Kernel state machine and its event journal, every live SQLite
 * database, the self-audit ledgers, the service topology, the skill pack
 * and the live route registry.
 *
 * ── Design contract ────────────────────────────────────────────────────────
 * 1. READ-ONLY. Every database is opened with `readOnly: true`. The console
 *    observes; it never mutates the system it describes (DNA #6).
 * 2. ZERO DEPENDENCIES. `node:sqlite` and `node:http` only. No npm install,
 *    no build step — the same discipline as the other mini-services.
 * 3. HONEST. A surface that could not be observed is reported as
 *    `observable: false` with the reason, never as "healthy". The console
 *    must not repeat the system's habit of reporting green while broken.
 * 4. NO SECRETS. Only secret *presence* and *source* are projected. The
 *    values are never read into a response, never logged.
 *
 * [S10c taint-removal] The resolved env-file path is derived from
 * `process.env` and must never reach a log sink.
 */

import { DatabaseSync } from "node:sqlite";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, basename, relative } from "node:path";

/** Canonical Task Kernel state machine (17 states). */
export const KERNEL_STATES = [
  "CREATED",
  "PLANNING",
  "READY",
  "QUEUED",
  "LEASED",
  "RUNNING",
  "WAITING_TOOL",
  "VERIFYING",
  "CHECKPOINTED",
  "UNKNOWN",
  "RECOVERING",
  "RECONCILING",
  "HUMAN_REVIEW",
  "RETRY_SCHEDULED",
  "COMPLETED",
  "FAILED",
  "CANCELLED",
];

export const TERMINAL_STATES = new Set(["COMPLETED", "FAILED", "CANCELLED"]);

/** Databases that belong to the audit subsystem itself — never graded. */
const SELF_AUDIT_DB_NAMES = new Set(["runtime_audit.sqlite3", "runtime_audit.sqlite"]);

/** Directories holding historical copies — inventoried, never graded. */
const BACKUP_DIR_MARKERS = ["backups", "backup", "archive"];

const DB_PATTERNS = [".sqlite3", ".sqlite", ".db"];

const MAX_ROW_CENSUS_TABLES = 60;
const MAX_JSONL_RECORDS = 5000;

/**
 * Open a SQLite database strictly read-only.
 * Returns null when the file cannot be opened as a database.
 */
function openReadOnly(dbPath) {
  try {
    return new DatabaseSync(dbPath, { readOnly: true });
  } catch (err) {
    return { __error: `${err.constructor.name}: ${String(err.message).slice(0, 200)}` };
  }
}

export function isBackupPath(filePath) {
  const parts = filePath.split(/[\\/]/).map((p) => p.toLowerCase());
  return parts.some((p) => BACKUP_DIR_MARKERS.some((marker) => p.includes(marker)));
}

function truncate(text, max = 200) {
  const value = String(text ?? "");
  return value.length > max ? `${value.slice(0, max)}...` : value;
}

/** Read a JSONL ledger, tolerating malformed lines. */
function readJsonl(filePath, maxRecords = MAX_JSONL_RECORDS) {
  if (!existsSync(filePath)) {
    return { present: false, records: [], error: null };
  }
  const records = [];
  let error = null;
  try {
    const raw = readFileSync(filePath, "utf8");
    const lines = raw.split(/\r?\n/);
    for (let i = 0; i < lines.length && records.length < maxRecords; i += 1) {
      const line = lines[i].trim();
      if (!line) continue;
      try {
        const parsed = JSON.parse(line);
        if (parsed && typeof parsed === "object") records.push(parsed);
      } catch (parseErr) {
        error = `line ${i + 1}: ${truncate(parseErr.message, 120)}`;
      }
    }
  } catch (readErr) {
    error = `${readErr.constructor.name}: ${truncate(readErr.message)}`;
  }
  return { present: true, records, error };
}

// ─── Storage census ─────────────────────────────────────────────────────────

/**
 * Inspect one database: integrity, journal mode, table and row census.
 * Never throws — an unreadable database is reported, not crashed on.
 */
export function inspectDatabase(dbPath, dataDir) {
  const relPath = relative(dataDir, dbPath).split(/[\\/]/).join("/");
  const record = {
    path: relPath,
    name: basename(dbPath),
    sizeBytes: 0,
    isBackup: isBackupPath(dbPath),
    observable: false,
    integrity: null,
    integrityOk: false,
    journalMode: null,
    tableCount: 0,
    tables: [],
    rowTotal: null,
    error: null,
  };

  try {
    record.sizeBytes = statSync(dbPath).size;
  } catch (statErr) {
    record.error = `${statErr.constructor.name}: ${truncate(statErr.message)}`;
    return record;
  }

  const opened = openReadOnly(dbPath);
  if (!opened || opened.__error) {
    record.error = opened ? opened.__error : "open failed";
    return record;
  }

  const db = opened;
  try {
    try {
      const modeRows = db.prepare("PRAGMA journal_mode").all();
      record.journalMode = modeRows[0] ? String(modeRows[0].journal_mode) : null;
    } catch (modeErr) {
      record.error = `journal_mode: ${truncate(modeErr.message)}`;
    }

    try {
      const integrityRows = db.prepare("PRAGMA integrity_check").all();
      const first = integrityRows[0];
      const value = first ? String(first.integrity_check) : "unknown";
      record.integrity = value.slice(0, 300);
      record.integrityOk = value === "ok";
    } catch (integrityErr) {
      record.error = `integrity_check: ${truncate(integrityErr.message)}`;
    }

    let tableRows = [];
    try {
      tableRows = db
        .prepare("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        .all();
    } catch (tableErr) {
      record.error = `sqlite_master: ${truncate(tableErr.message)}`;
    }
    record.tableCount = tableRows.length;

    let rowTotal = 0;
    let rowsKnown = true;
    for (const tableRow of tableRows.slice(0, MAX_ROW_CENSUS_TABLES)) {
      const tableName = String(tableRow.name);
      const quoted = `"${tableName.replace(/"/g, '""')}"`;
      try {
        const countRow = db.prepare(`SELECT COUNT(*) AS n FROM ${quoted}`).get();
        const count = countRow ? Number(countRow.n) : 0;
        rowTotal += count;
        record.tables.push({ name: tableName, rows: count });
      } catch (countErr) {
        rowsKnown = false;
        record.tables.push({
          name: tableName,
          rows: null,
          error: `${countErr.constructor.name}: ${truncate(countErr.message, 120)}`,
        });
      }
    }
    record.rowTotal = rowsKnown ? rowTotal : null;
    record.observable = true;
  } finally {
    try {
      db.close();
    } catch {
      /* closing a read-only handle never invalidates the observation */
    }
  }
  return record;
}

/** Recursively discover every SQLite database under the data directory. */
export function discoverDatabases(dataDir) {
  if (!existsSync(dataDir)) return [];
  const found = [];
  const seen = new Set();
  const walk = (dir) => {
    let entries = [];
    try {
      entries = readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries) {
      const full = join(dir, entry.name);
      if (entry.isDirectory()) {
        walk(full);
        continue;
      }
      if (!entry.isFile()) continue;
      if (!DB_PATTERNS.some((ext) => entry.name.toLowerCase().endsWith(ext))) continue;
      if (SELF_AUDIT_DB_NAMES.has(entry.name.toLowerCase())) continue;
      const key = full.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      found.push(full);
    }
  };
  walk(dataDir);
  return found.sort();
}

// ─── Kernel projection ──────────────────────────────────────────────────────

/** Project the Task Kernel: state occupancy, tasks, leases, checkpoints. */
export function projectKernel(dbPath, dataDir) {
  const relPath = relative(dataDir, dbPath).split(/[\\/]/).join("/");
  const projection = {
    path: relPath,
    observable: false,
    error: null,
    taskCount: 0,
    occupancy: {},
    states: KERNEL_STATES.map((state) => ({ state, count: 0 })),
    activeLeases: 0,
    checkpoints: 0,
    sampleTasks: [],
    eventTypes: [],
  };

  const opened = openReadOnly(dbPath);
  if (!opened || opened.__error) {
    projection.error = opened ? opened.__error : "open failed";
    return projection;
  }
  const db = opened;

  try {
    const names = new Set(
      db
        .prepare("SELECT name FROM sqlite_master WHERE type='table'")
        .all()
        .map((row) => String(row.name)),
    );
    if (!names.has("tasks") || !names.has("events")) {
      projection.error = "not a Task Kernel database";
      return projection;
    }

    const taskRows = db.prepare("SELECT COUNT(*) AS n FROM tasks").get();
    projection.taskCount = taskRowCount(taskRows);

    const occupancy = {};
    for (const state of KERNEL_STATES) occupancy[state] = 0;
    for (const row of db.prepare("SELECT state, COUNT(*) AS n FROM tasks GROUP BY state").all()) {
      const state = String(row.state);
      const count = Number(row.n) || 0;
      occupancy[state] = (occupancy[state] || 0) + count;
    }
    projection.occupancy = occupancy;
    projection.states = KERNEL_STATES.map((state) => ({
      state,
      count: occupancy[state] || 0,
      terminal: TERMINAL_STATES.has(state),
    }));

    if (names.has("leases")) {
      const leaseRow = db
        .prepare("SELECT COUNT(*) AS n FROM leases WHERE released = 0")
        .get();
      projection.activeLeases = taskRowCount(leaseRow);
    }
    if (names.has("checkpoints")) {
      const cpRow = db.prepare("SELECT COUNT(*) AS n FROM checkpoints").get();
      projection.checkpoints = taskRowCount(cpRow);
    }

    try {
      projection.sampleTasks = db
        .prepare(
          "SELECT task_id, state, risk_tier, attempts, created_at, updated_at, error FROM tasks ORDER BY updated_at DESC LIMIT 25",
        )
        .all()
        .map((row) => ({
          taskId: row.task_id,
          state: row.state,
          riskTier: row.risk_tier,
          attempts: row.attempts,
          createdAt: row.created_at,
          updatedAt: row.updated_at,
          error: row.error ? truncate(row.error, 160) : null,
        }));
    } catch (sampleErr) {
      projection.sampleTasksError = truncate(sampleErr.message, 160);
    }

    try {
      projection.eventTypes = db
        .prepare("SELECT type, COUNT(*) AS n FROM events GROUP BY type ORDER BY n DESC")
        .all()
        .map((row) => ({ type: row.type, count: Number(row.n) || 0 }));
    } catch (eventErr) {
      projection.eventTypesError = truncate(eventErr.message, 160);
    }

    projection.observable = true;
  } catch (err) {
    projection.error = `${err.constructor.name}: ${truncate(err.message)}`;
  } finally {
    try {
      db.close();
    } catch {
      /* ignore */
    }
  }
  return projection;
}

function taskRowCount(row) {
  return row ? Number(row.n) || 0 : 0;
}

/**
 * Verify the event journal structure and cross-check the global anchor.
 *
 * The anchor cross-check is the part the in-process verifier does not do:
 * `control.event_count` is compared against the physical row count and
 * `control.journal_head_hash` against the newest physical event hash.
 */
export function projectJournal(dbPath, dataDir) {
  const relPath = relative(dataDir, dbPath).split(/[\\/]/).join("/");
  const projection = {
    path: relPath,
    observable: false,
    error: null,
    eventCount: null,
    anchorEventCount: null,
    anchorDelta: null,
    headMatches: null,
    anchorHeadHash: null,
    newestEventHash: null,
    taskCount: 0,
    chainBrokenTasks: [],
    chainBrokenCount: 0,
    recentEvents: [],
    eventTypes: [],
  };

  const opened = openReadOnly(dbPath);
  if (!opened || opened.__error) {
    projection.error = opened ? opened.__error : "open failed";
    return projection;
  }
  const db = opened;

  try {
    const names = new Set(
      db
        .prepare("SELECT name FROM sqlite_master WHERE type='table'")
        .all()
        .map((row) => String(row.name)),
    );
    if (!names.has("events")) {
      projection.error = "no events table";
      return projection;
    }

    const actualRow = db.prepare("SELECT COUNT(*) AS n FROM events").get();
    projection.eventCount = taskRowCount(actualRow);

    if (names.has("control")) {
      const columns = new Set(
        db
          .prepare("PRAGMA table_info(control)")
          .all()
          .map((row) => String(row.name)),
      );
      if (columns.has("event_count") && columns.has("journal_head_hash")) {
        const controlRow = db.prepare("SELECT journal_head_hash, event_count FROM control WHERE id=1").get();
        if (controlRow) {
          projection.anchorEventCount = Number(controlRow.event_count) || 0;
          projection.anchorDelta = projection.eventCount - projection.anchorEventCount;
          projection.anchorHeadHash = controlRow.journal_head_hash
            ? String(controlRow.journal_head_hash)
            : null;
        }
      } else {
        projection.anchorSchemaVariant = true;
      }
    }

    const newest = db.prepare("SELECT event_hash FROM events ORDER BY rowid DESC LIMIT 1").get();
    projection.newestEventHash = newest ? String(newest.event_hash) : null;
    projection.headMatches =
      projection.anchorHeadHash !== null && projection.anchorHeadHash !== undefined
        ? projection.anchorHeadHash === projection.newestEventHash
        : null;

    const rows = db
      .prepare(
        "SELECT task_id, seq, type, from_state, to_state, actor, reason, prev_event_hash, event_hash, created_at FROM events ORDER BY task_id, seq",
      )
      .all();

    let currentTask = null;
    let expectedSeq = 0;
    let expectedPrev = null;
    let taskCount = 0;
    const broken = [];

    for (const row of rows) {
      const taskId = String(row.task_id);
      if (taskId !== currentTask) {
        currentTask = taskId;
        expectedSeq = 0;
        expectedPrev = null;
        taskCount += 1;
      }
      expectedSeq += 1;
      const seq = Number(row.seq);
      const problems = [];
      if (seq !== expectedSeq) problems.push(`seq_gap:expected=${expectedSeq}:actual=${seq}`);
      if ((row.prev_event_hash ?? null) !== expectedPrev) problems.push("prev_hash_break");
      if (!row.event_hash) problems.push("missing_event_hash");
      if (problems.length > 0) {
        broken.push({ taskId, seq, problems });
      }
      expectedPrev = row.event_hash ?? null;
    }

    projection.taskCount = taskCount;
    projection.chainBrokenCount = broken.length;
    projection.chainBrokenTasks = broken.slice(0, 25);

    try {
      projection.recentEvents = db
        .prepare(
          "SELECT event_id, task_id, seq, type, from_state, to_state, actor, reason, created_at FROM events ORDER BY rowid DESC LIMIT 40",
        )
        .all()
        .map((row) => ({
          eventId: row.event_id,
          taskId: row.task_id,
          seq: row.seq,
          type: row.type,
          fromState: row.from_state,
          toState: row.to_state,
          actor: row.actor,
          reason: truncate(row.reason, 160),
          createdAt: row.created_at,
        }));
    } catch (recentErr) {
      projection.recentEventsError = truncate(recentErr.message, 160);
    }

    try {
      projection.eventTypes = db
        .prepare("SELECT type, COUNT(*) AS n FROM events GROUP BY type ORDER BY n DESC")
        .all()
        .map((row) => ({ type: row.type, count: Number(row.n) || 0 }));
    } catch (typeErr) {
      projection.eventTypesError = truncate(typeErr.message, 160);
    }

    projection.observable = true;
  } catch (err) {
    projection.error = `${err.constructor.name}: ${truncate(err.message)}`;
  } finally {
    try {
      db.close();
    } catch {
      /* ignore */
    }
  }
  return projection;
}

// ─── Ledger effectiveness ───────────────────────────────────────────────────

/** Compute the effectiveness of the self-audit loops from their ledgers. */
export function projectLedgers(dataDir) {
  const loop = readJsonl(join(dataDir, "loop_runs.jsonl"));
  const deep = readJsonl(join(dataDir, "deep_audit_results.jsonl"));

  const statusCounts = {};
  let withFindings = 0;
  let nonzeroFindings = 0;
  let errorLike = 0;
  const ERROR_LIKE = new Set(["error", "scp_offline", "bridge_offline", "auth_required"]);

  for (const record of loop.records) {
    const status = String(record.status ?? "unknown");
    statusCounts[status] = (statusCounts[status] || 0) + 1;
    if (ERROR_LIKE.has(status)) errorLike += 1;
    if ("findings_count" in record) {
      withFindings += 1;
      if (Number(record.findings_count) > 0) nonzeroFindings += 1;
    }
  }

  const actionCounts = {};
  const skipReasons = {};
  for (const record of deep.records) {
    const result = record.result;
    const action = result && typeof result === "object" ? String(result.action ?? "unknown") : "unstructured";
    actionCounts[action] = (actionCounts[action] || 0) + 1;
    if (action === "skipped" && result.reason) {
      const reason = String(result.reason).slice(0, 140);
      skipReasons[reason] = (skipReasons[reason] || 0) + 1;
    }
  }

  const total = loop.records.length || 1;
  const deepTotal = deep.records.length || 1;

  return {
    loop: {
      present: loop.present,
      totalRuns: loop.records.length,
      statusCounts,
      runsWithFindingsField: withFindings,
      runsWithNonzeroFindings: nonzeroFindings,
      zeroFindingRatio: withFindings ? Number(((withFindings - nonzeroFindings) / withFindings).toFixed(4)) : null,
      errorLikeRuns: errorLike,
      errorLikeRatio: Number((errorLike / total).toFixed(4)),
      lastRuns: loop.records.slice(-15).reverse().map((record) => ({
        ts: record.ts ?? null,
        status: record.status ?? null,
        httpStatus: record.http_status ?? null,
        durationMs: record.duration_ms ?? null,
        findings: record.findings_count ?? null,
        triggeredBy: record.triggered_by ?? null,
        error: record.error ? truncate(record.error, 160) : null,
      })),
      parseError: loop.error,
    },
    deepAudit: {
      present: deep.present,
      totalRecords: deep.records.length,
      actionCounts,
      skippedRatio: Number(((actionCounts.skipped ?? 0) / deepTotal).toFixed(4)),
      topSkipReasons: Object.entries(skipReasons)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 6)
        .map(([reason, count]) => ({ reason, count })),
      parseError: deep.error,
    },
  };
}

// ─── Service topology ───────────────────────────────────────────────────────

/** Probe the normative services and report reachability honestly. */
export async function projectServices(config) {
  const targets = [
    { name: "backend-health", url: `${config.backendUrl}/health` },
    { name: "backend-ready", url: `${config.backendUrl}/ready` },
    { name: "backend-audit-stats", url: `${config.backendUrl}/v105/audit/stats` },
    { name: "backend-runtime-subsystems", url: `${config.backendUrl}/v105/runtime/subsystems` },
    { name: "backend-openapi", url: `${config.backendUrl}/openapi.json` },
    { name: "loop-scheduler", url: `${config.loopSchedulerUrl}/healthz` },
    { name: "loop-scheduler-status", url: `${config.loopSchedulerUrl}/` },
    { name: "agent-desktop", url: `${config.agentDesktopUrl}/` },
  ];

  const authHeader = config.adminToken ? { Authorization: `Bearer ${config.adminToken}` } : {};

  const results = await Promise.all(targets.map(async (target) => {
    const started = Date.now();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), config.httpTimeoutMs);
    try {
      const response = await fetch(target.url, {
        method: "GET",
        signal: controller.signal,
        headers: { "User-Agent": "scp-brain-console/1.0", ...authHeader },
      });
      const body = await response.text();
      let json = null;
      try {
        json = JSON.parse(body);
      } catch {
        json = null;
      }
      return {
        name: target.name,
        url: target.url,
        reachable: true,
        status: response.status,
        latencyMs: Date.now() - started,
        protected: response.status === 401 || response.status === 403,
        json,
      };
    } catch (err) {
      return {
        name: target.name,
        url: target.url,
        reachable: false,
        status: null,
        latencyMs: Date.now() - started,
        protected: false,
        error: `${err.constructor.name}: ${truncate(err.message)}`,
      };
    } finally {
      clearTimeout(timer);
    }
  }));

  return results;
}

// ─── Skill pack ─────────────────────────────────────────────────────────────

/** Inventory the normative SCP skill pack from disk. */
export function projectSkills(skillsDir) {
  if (!existsSync(skillsDir)) {
    return { present: false, path: skillsDir, count: 0, skills: [] };
  }
  const skills = [];
  let entries = [];
  try {
    entries = readdirSync(skillsDir, { withFileTypes: true });
  } catch (err) {
    return { present: false, path: skillsDir, count: 0, skills: [], error: truncate(err.message) };
  }
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const manifest = join(skillsDir, entry.name, "SKILL.md");
    if (!existsSync(manifest)) continue;
    let description = "";
    let status = "";
    try {
      const text = readFileSync(manifest, "utf8");
      const descMatch = text.match(/^description:\s*(.+)$/m);
      if (descMatch) description = descMatch[1].trim().slice(0, 300);
      const statusMatch = text.match(/^status:\s*(.+)$/m);
      if (statusMatch) status = statusMatch[1].trim().slice(0, 60);
    } catch (err) {
      description = `unreadable: ${truncate(err.message, 80)}`;
    }
    skills.push({ name: entry.name, description, status });
  }
  skills.sort((a, b) => a.name.localeCompare(b.name));
  return { present: true, path: skillsDir, count: skills.length, skills };
}

// ─── Runtime audit verdict ──────────────────────────────────────────────────

/** Read the most recent runtime audit verdict recorded by the Python authority. */
export function projectRuntimeAudit(dataDir) {
  const ledgerPath = join(dataDir, "runtime_audit.sqlite3");
  if (!existsSync(ledgerPath)) {
    return { present: false, reason: "runtime_audit.sqlite3 not found", verdict: null, history: [] };
  }
  const opened = openReadOnly(ledgerPath);
  if (!opened || opened.__error) {
    return {
      present: false,
      reason: opened ? opened.__error : "open failed",
      verdict: null,
      history: [],
    };
  }
  const db = opened;
  try {
    const rows = db
      .prepare(
        "SELECT run_id, started_at, completed_at, verdict, target, elapsed_sec, finding_count, summary_json FROM audit_runs ORDER BY started_at DESC LIMIT 10",
      )
      .all();
    const history = rows.map((row) => ({
      runId: row.run_id,
      startedAt: row.started_at,
      completedAt: row.completed_at,
      verdict: row.verdict,
      target: row.target,
      elapsedSec: row.elapsed_sec,
      findingCount: row.finding_count,
    }));
    let verdict = null;
    if (rows.length > 0) {
      try {
        const parsed = JSON.parse(rows[0].summary_json);
        verdict = {
          verdict: parsed.verdict,
          startedAt: parsed.started_at,
          completedAt: parsed.completed_at,
          counts: parsed.counts,
          scope: parsed.scope,
          findings: (parsed.findings || []).map((finding) => ({
            probeId: finding.probe_id,
            severity: finding.severity,
            title: finding.title,
            summary: finding.summary,
          })),
        };
      } catch (parseErr) {
        verdict = { error: truncate(parseErr.message, 160) };
      }
    }
    return { present: true, verdict, history };
  } catch (err) {
    return { present: false, reason: `${err.constructor.name}: ${truncate(err.message)}`, verdict: null, history: [] };
  } finally {
    try {
      db.close();
    } catch {
      /* ignore */
    }
  }
}

// ─── Aggregate snapshot ─────────────────────────────────────────────────────

/**
 * Build the complete brain snapshot.
 * Every section reports its own observability; nothing is assumed.
 */
export async function buildBrainSnapshot(config) {
  const startedAt = Date.now();
  const dataDir = config.dataDir;

  const dbPaths = discoverDatabases(dataDir);
  const storage = dbPaths.map((dbPath) => inspectDatabase(dbPath, dataDir));

  const kernelPaths = dbPaths.filter((dbPath) => {
    if (isBackupPath(dbPath)) return false;
    const record = storage.find((entry) => entry.path === relative(dataDir, dbPath).split(/[\\/]/).join("/"));
    return record && record.observable && record.tableCount > 0 && record.tables.some((t) => t.name === "tasks");
  });

  const kernels = kernelPaths.map((dbPath) => projectKernel(dbPath, dataDir));
  const journals = kernelPaths.map((dbPath) => projectJournal(dbPath, dataDir));

  const services = await projectServices(config);
  const ledgers = projectLedgers(dataDir);
  const skills = projectSkills(config.skillsDir);
  const runtimeAudit = projectRuntimeAudit(dataDir);

  const corrupt = storage.filter(
    (entry) => !entry.isBackup && (!entry.observable || !entry.integrityOk),
  );
  const anchorDivergent = journals.filter(
    (entry) => entry.observable && entry.anchorDelta !== null && entry.anchorDelta !== 0,
  );
  const brokenEndpoints = services.filter(
    (entry) => entry.reachable === false || (typeof entry.status === "number" && entry.status >= 500),
  );

  const severity = corrupt.length || anchorDivergent.length || brokenEndpoints.length ? "FAIL" : "PASS";

  return {
    generatedAt: new Date().toISOString(),
    elapsedMs: Date.now() - startedAt,
    service: "scp-brain-console",
    version: "1.0.0",
    scope: {
      dataDir,
      backendUrl: config.backendUrl,
      loopSchedulerUrl: config.loopSchedulerUrl,
      agentDesktopUrl: config.agentDesktopUrl,
      skillsDir: config.skillsDir,
    },
    reality: {
      severity,
      corruptDatabases: corrupt.map((entry) => ({
        path: entry.path,
        observable: entry.observable,
        integrity: entry.integrity,
        error: entry.error,
      })),
      anchorDivergentDatabases: anchorDivergent.map((entry) => ({
        path: entry.path,
        eventCount: entry.eventCount,
        anchorEventCount: entry.anchorEventCount,
        delta: entry.anchorDelta,
      })),
      brokenEndpoints: brokenEndpoints.map((entry) => ({
        name: entry.name,
        status: entry.status,
        reachable: entry.reachable,
        error: entry.error ?? null,
      })),
    },
    storage,
    kernels,
    journals,
    ledgers,
    services,
    skills,
    runtimeAudit,
  };
}
