import "server-only"

import fs from "node:fs"
import path from "node:path"

export type SystemMapEvidenceLevel = "STATIC" | "RUNTIME" | "UNKNOWN"

export type SystemMapNode = {
  id: string
  label: string
  path: string
  kind: "python" | "typescript" | "spec" | "test" | "tool"
  subsystem: string
  lineCount: number
}

export type SystemMapEdge = {
  id: string
  from: string
  to: string
  relation: "IMPORT"
  evidenceLevel: SystemMapEvidenceLevel
  sourcePath: string
  sourceLine: number
}

export type SystemMapSnapshot = {
  schemaVersion: 1
  generatedAt: string
  sourceRoot: string
  evidenceMode: "SOURCE_SCAN"
  limitations: string[]
  nodes: SystemMapNode[]
  edges: SystemMapEdge[]
  truncated: boolean
}

const MAX_FILES = 900
const MAX_EDGES = 2400
const ROOTS = ["scp", "dashboard/src", "tools", "tests", "spec"] as const
const SKIP_DIRS = new Set([
  ".git",
  ".next",
  ".venv",
  "venv",
  "node_modules",
  "__pycache__",
  ".pytest_cache",
  ".mypy_cache",
  "dist",
  "build",
  "data",
  "reports",
])

function normalizeRepoPath(value: string) {
  return value.split(path.sep).join("/")
}

function isAllowedFile(filePath: string) {
  return /\.(py|ts|tsx|yaml|yml)$/.test(filePath)
}

function kindFor(repoPath: string): SystemMapNode["kind"] {
  if (repoPath.startsWith("tests/")) return "test"
  if (repoPath.startsWith("tools/")) return "tool"
  if (repoPath.startsWith("spec/")) return "spec"
  if (repoPath.endsWith(".py")) return "python"
  return "typescript"
}

function subsystemFor(repoPath: string) {
  const p = repoPath.toLowerCase()
  if (p.startsWith("tests/")) return "Tests"
  if (p.startsWith("spec/")) return "Specifications"
  if (p.startsWith("tools/")) return "Tooling"
  if (p.startsWith("dashboard/")) return "Dashboard"
  if (p.includes("/epistemic/") || p.includes("evidence") || p.includes("lineage")) return "Epistemic"
  if (p.includes("/cognitive/") || p.includes("reasoning")) return "Cognitive"
  if (p.includes("/knowledge/") || p.includes("memory")) return "Knowledge"
  if (p.includes("/governance/") || p.includes("policy") || p.includes("drift")) return "Governance"
  if (p.includes("/security/") || p.includes("sandbox") || p.includes("capability")) return "Security"
  if (p.includes("task_kernel") || p.includes("/kernel/") || p.includes("/hands/")) return "Execution"
  if (p.includes("llm_gateway") || p.includes("provider") || p.includes("router")) return "Intelligence"
  if (p.includes("/world/") || p.includes("risk") || p.includes("prediction")) return "World / Risk"
  if (p.includes("autofix") || p.includes("evolution")) return "Self-Improvement"
  if (p.includes("api") || p.includes("server")) return "Interfaces"
  return "SCP Core"
}

function fileLabel(repoPath: string) {
  const base = path.posix.basename(repoPath).replace(/\.(py|ts|tsx|yaml|yml)$/, "")
  if (base === "__init__") {
    const parent = path.posix.basename(path.posix.dirname(repoPath))
    return `${parent}.__init__`
  }
  return base
}

function detectRepoRoot() {
  const candidates = [
    process.cwd(),
    path.resolve(process.cwd(), ".."),
    path.resolve(process.cwd(), "../.."),
  ]
  for (const candidate of candidates) {
    if (fs.existsSync(path.join(candidate, "scp")) && fs.existsSync(path.join(candidate, "dashboard"))) {
      return candidate
    }
  }
  throw new Error("SCP repository root not found from dashboard runtime")
}

function walk(dir: string, root: string, out: string[]) {
  if (out.length >= MAX_FILES) return
  if (!fs.existsSync(dir)) return
  const entries = fs.readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))
  for (const entry of entries) {
    if (out.length >= MAX_FILES) return
    if (SKIP_DIRS.has(entry.name)) continue
    const absolute = path.join(dir, entry.name)
    if (entry.isDirectory()) {
      walk(absolute, root, out)
    } else if (entry.isFile() && isAllowedFile(absolute)) {
      out.push(normalizeRepoPath(path.relative(root, absolute)))
    }
  }
}

function moduleIdFromRepoPath(repoPath: string) {
  if (!repoPath.endsWith(".py")) return null
  const withoutExt = repoPath.slice(0, -3)
  if (withoutExt.endsWith("/__init__")) return withoutExt.slice(0, -9).replaceAll("/", ".")
  return withoutExt.replaceAll("/", ".")
}

function resolvePythonImport(importName: string, modules: Map<string, string>) {
  let candidate = importName.trim()
  while (candidate) {
    const exact = modules.get(candidate)
    if (exact) return exact
    const cut = candidate.lastIndexOf(".")
    if (cut === -1) break
    candidate = candidate.slice(0, cut)
  }
  return null
}

function resolveTsImport(sourcePath: string, specifier: string, knownPaths: Set<string>) {
  let base: string | null = null
  if (specifier.startsWith("@/")) {
    base = `dashboard/src/${specifier.slice(2)}`
  } else if (specifier.startsWith(".")) {
    const sourceDir = path.posix.dirname(sourcePath)
    base = path.posix.normalize(path.posix.join(sourceDir, specifier))
  }
  if (!base) return null

  const candidates = [
    base,
    `${base}.ts`,
    `${base}.tsx`,
    `${base}/index.ts`,
    `${base}/index.tsx`,
  ]
  return candidates.find((candidate) => knownPaths.has(candidate)) ?? null
}

export function buildSystemMapSnapshot(): SystemMapSnapshot {
  const root = detectRepoRoot()
  const repoPaths: string[] = []
  for (const rootName of ROOTS) {
    walk(path.join(root, rootName), root, repoPaths)
    if (repoPaths.length >= MAX_FILES) break
  }

  const nodes: SystemMapNode[] = []
  const nodeByPath = new Map<string, SystemMapNode>()
  for (const repoPath of repoPaths) {
    const absolute = path.join(root, ...repoPath.split("/"))
    let content = ""
    try {
      content = fs.readFileSync(absolute, "utf8")
    } catch {
      continue
    }
    const node: SystemMapNode = {
      id: `file:${repoPath}`,
      label: fileLabel(repoPath),
      path: repoPath,
      kind: kindFor(repoPath),
      subsystem: subsystemFor(repoPath),
      lineCount: content === "" ? 0 : content.split(/\r?\n/).length,
    }
    nodes.push(node)
    nodeByPath.set(repoPath, node)
  }

  const knownPaths = new Set(nodeByPath.keys())
  const pyModules = new Map<string, string>()
  for (const repoPath of knownPaths) {
    const moduleId = moduleIdFromRepoPath(repoPath)
    if (moduleId) pyModules.set(moduleId, repoPath)
  }

  const edges: SystemMapEdge[] = []
  const edgeKeys = new Set<string>()
  const pushEdge = (sourcePath: string, targetPath: string, sourceLine: number) => {
    if (edges.length >= MAX_EDGES || sourcePath === targetPath) return
    const source = nodeByPath.get(sourcePath)
    const target = nodeByPath.get(targetPath)
    if (!source || !target) return
    const key = `${source.id}->${target.id}`
    if (edgeKeys.has(key)) return
    edgeKeys.add(key)
    edges.push({
      id: `edge:${edges.length + 1}`,
      from: source.id,
      to: target.id,
      relation: "IMPORT",
      evidenceLevel: "STATIC",
      sourcePath,
      sourceLine,
    })
  }

  for (const repoPath of knownPaths) {
    if (edges.length >= MAX_EDGES) break
    if (!repoPath.endsWith(".py") && !repoPath.endsWith(".ts") && !repoPath.endsWith(".tsx")) continue
    const absolute = path.join(root, ...repoPath.split("/"))
    let content = ""
    try {
      content = fs.readFileSync(absolute, "utf8")
    } catch {
      continue
    }
    const lines = content.split(/\r?\n/)
    lines.forEach((line, index) => {
      if (edges.length >= MAX_EDGES) return
      if (repoPath.endsWith(".py")) {
        const fromMatch = line.match(/^\s*from\s+([a-zA-Z0-9_.]+)\s+import\s+/)
        const importMatch = line.match(/^\s*import\s+([a-zA-Z0-9_.]+)/)
        const importName = fromMatch?.[1] ?? importMatch?.[1]
        if (!importName || !importName.startsWith("scp")) return
        const target = resolvePythonImport(importName, pyModules)
        if (target) pushEdge(repoPath, target, index + 1)
        return
      }

      const tsMatch = line.match(/(?:from\s+|import\s*\()(["'])([^"']+)\1/)
      const specifier = tsMatch?.[2]
      if (!specifier) return
      const target = resolveTsImport(repoPath, specifier, knownPaths)
      if (target) pushEdge(repoPath, target, index + 1)
    })
  }

  return {
    schemaVersion: 1,
    generatedAt: new Date().toISOString(),
    sourceRoot: ".",
    evidenceMode: "SOURCE_SCAN",
    limitations: [
      "V1 only proves static source/import relationships; it does not claim runtime execution.",
      "Runtime, Evidence Authority, test-maturity and same-SHA overlays are not connected in V1.",
      "Dynamic imports/calls and reflection may be absent unless represented by a static import.",
    ],
    nodes,
    edges,
    truncated: repoPaths.length >= MAX_FILES || edges.length >= MAX_EDGES,
  }
}
