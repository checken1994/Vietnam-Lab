/**
 * Typed surface for the SCP Brain Console.
 *
 * The runtime is JavaScript (`index.js`, `core.js`, `view.js`) so the service
 * stays build-free and dependency-free. This declaration file describes the
 * same surface for typed consumers without introducing a second, drifting
 * source of truth.
 */

export interface BrainConsoleConfig {
  dataDir: string;
  skillsDir: string;
  backendUrl: string;
  loopSchedulerUrl: string;
  agentDesktopUrl: string;
  httpTimeoutMs: number;
  adminToken: string;
}

export interface StorageRecord {
  path: string;
  name: string;
  sizeBytes: number;
  isBackup: boolean;
  observable: boolean;
  integrity: string | null;
  integrityOk: boolean;
  journalMode: string | null;
  tableCount: number;
  tables: Array<{ name: string; rows: number | null; error?: string }>;
  rowTotal: number | null;
  error: string | null;
}

export interface KernelStateCell {
  state: string;
  count: number;
  terminal: boolean;
}

export interface KernelProjection {
  path: string;
  observable: boolean;
  error: string | null;
  taskCount: number;
  occupancy: Record<string, number>;
  states: KernelStateCell[];
  activeLeases: number;
  checkpoints: number;
  sampleTasks: Array<Record<string, unknown>>;
  eventTypes: Array<{ type: string; count: number }>;
}

export interface JournalProjection {
  path: string;
  observable: boolean;
  error: string | null;
  eventCount: number | null;
  anchorEventCount: number | null;
  anchorDelta: number | null;
  headMatches: boolean | null;
  anchorHeadHash: string | null;
  newestEventHash: string | null;
  taskCount: number;
  chainBrokenTasks: Array<{ taskId: string; seq: number; problems: string[] }>;
  chainBrokenCount: number;
  recentEvents: Array<Record<string, unknown>>;
  eventTypes: Array<{ type: string; count: number }>;
}

export interface ServiceObservation {
  name: string;
  url: string;
  reachable: boolean;
  status: number | null;
  latencyMs: number | null;
  protected: boolean;
  json?: unknown;
  error?: string;
}

export interface BrainSnapshot {
  generatedAt: string;
  elapsedMs: number;
  service: string;
  version: string;
  scope: Record<string, string>;
  reality: {
    severity: "PASS" | "WARN" | "FAIL";
    corruptDatabases: Array<{ path: string; observable: boolean; integrity: string | null; error: string | null }>;
    anchorDivergentDatabases: Array<{ path: string; eventCount: number | null; anchorEventCount: number | null; delta: number | null }>;
    brokenEndpoints: Array<{ name: string; status: number | null; reachable: boolean; error: string | null }>;
  };
  storage: StorageRecord[];
  kernels: KernelProjection[];
  journals: JournalProjection[];
  ledgers: Record<string, unknown>;
  services: ServiceObservation[];
  skills: { present: boolean; path: string; count: number; skills: Array<{ name: string; description: string; status: string }> };
  runtimeAudit: { present: boolean; verdict: unknown; history: unknown[] };
}

export const KERNEL_STATES: readonly string[];
export function inspectDatabase(dbPath: string, dataDir: string): StorageRecord;
export function discoverDatabases(dataDir: string): string[];
export function projectKernel(dbPath: string, dataDir: string): KernelProjection;
export function projectJournal(dbPath: string, dataDir: string): JournalProjection;
export function projectLedgers(dataDir: string): Record<string, unknown>;
export function projectServices(config: BrainConsoleConfig): Promise<ServiceObservation[]>;
export function projectSkills(skillsDir: string): BrainSnapshot["skills"];
export function projectRuntimeAudit(dataDir: string): BrainSnapshot["runtimeAudit"];
export function buildBrainSnapshot(config: BrainConsoleConfig): Promise<BrainSnapshot>;
export function renderBrainPage(pollMs?: number): string;
