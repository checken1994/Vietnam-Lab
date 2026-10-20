"use client"

import React, { useState } from "react"
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  ChevronDown,
  Cpu,
  FileCode,
  FileText,
  GitBranch,
  Globe,
  Layers3,
  Loader2,
  Lock,
  RefreshCw,
  Server,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Terminal,
  Wrench,
  Zap,
} from "lucide-react"
import {
  AgentExecutionStep,
  ChatMessage,
  JsonRecord,
  PhysicalFileTrace,
  SubsystemStatus,
  ThreadClusterInfo,
  asText,
} from "./scp-chat-types"

interface ScpExecutionVisualizerProps {
  message: ChatMessage
  onTriggerControlledFix?: () => Promise<void>
  onRefreshSystem?: () => Promise<void>
}

const DEFAULT_CLUSTERS: ThreadClusterInfo[] = [
  { id: "c-a", name: "Cluster A: Agent Control & CDP Navigator", role: "CDP Port 9223 / noVNC :6080", status: "active" },
  { id: "c-b", name: "Cluster B: Task Kernel ACID Engine", role: "SQLite WAL · 17-State Machine", status: "active" },
  { id: "c-c", name: "Cluster C: Governance & Capability RBAC", role: "Allowlist · Policy Choke", status: "active" },
  { id: "c-d", name: "Cluster D: Continuous Operations & AutoFix", role: "Loop Scheduler · AST Transformer", status: "idle" },
]

const DEFAULT_FILES: PhysicalFileTrace[] = [
  { path: "data/task_kernel.sqlite3", action: "LOCK", status: "active", detail: "WAL Journal · Transaction Isolation" },
  { path: "scp/task_kernel_parts/taskkernel.py", action: "CHECK", status: "verified", detail: "State Transition Validator" },
  { path: "scp/security/gov_rbac.py", action: "AUDIT", status: "verified", detail: "Capability Boundary Enforcement" },
  { path: "data/loop_runs.jsonl", action: "READ", status: "verified", detail: "Scheduler Run History Ledger" },
]

const SUBSYSTEMS_MAP: SubsystemStatus[] = [
  { id: "s-kernel", name: "Task Kernel", category: "core", status: "ready" },
  { id: "s-gateway", name: "LLM Gateway", category: "core", status: "ready" },
  { id: "s-browser", name: "Web Navigator", category: "core", status: "ready" },
  { id: "s-autofix", name: "Autofix Engine", category: "core", status: "ready" },
  { id: "s-rbac", name: "Governance RBAC", category: "cross_cutting", status: "verified" },
  { id: "s-egress", name: "Egress Guard", category: "cross_cutting", status: "verified" },
  { id: "s-verifier", name: "Reality Verifier", category: "cross_cutting", status: "verified" },
  { id: "s-memory", name: "Chat Memory", category: "core", status: "ready" },
]

export function ScpExecutionVisualizer({
  message,
  onTriggerControlledFix,
  onRefreshSystem,
}: ScpExecutionVisualizerProps) {
  const [activeTab, setActiveTab] = useState<"journey" | "files" | "clusters" | "trace">("journey")
  const [traceOpen, setTraceOpen] = useState(false)
  const [traceLoading, setTraceLoading] = useState(false)
  const [traceData, setTraceData] = useState<JsonRecord | null>(null)

  const steps = message.executionSteps ?? []
  const files = message.physicalFiles?.length ? message.physicalFiles : DEFAULT_FILES
  const clusters = message.threadClusters?.length ? message.threadClusters : DEFAULT_CLUSTERS
  const traceId = String(message.data?.trace_id || "")

  const toggleTrace = async () => {
    const next = !traceOpen
    setTraceOpen(next)
    if (next && traceId && !traceData) {
      setTraceLoading(true)
      try {
        const res = await fetch(`/api/scp/v3/trace/${encodeURIComponent(traceId)}`, { cache: "no-store" })
        if (res.ok) {
          const data = (await res.json().catch(() => null)) as JsonRecord | null
          if (data) setTraceData(data)
        }
      } catch {
        // fallback
      } finally {
        setTraceLoading(false)
      }
    }
  }

  // Determine active breadcrumb index based on status
  const breadcrumbs = [
    { label: "1. Ý định", state: "done" },
    { label: "2. Cổng Quản trị", state: message.status === "failed" ? "alert" : "done" },
    { label: "3. Task Kernel", state: message.status === "failed" ? "alert" : "done" },
    { label: "4. File & Cụm luồng", state: message.status === "executing" ? "active" : message.status === "failed" ? "alert" : "done" },
    { label: "5. Bằng chứng Thực tế", state: message.status === "completed" ? "done" : message.status === "failed" ? "alert" : "active" },
  ]

  return (
    <div className="mt-3.5 space-y-3 rounded-2xl border border-white/10 bg-black/45 p-3.5 text-xs">
      {/* 1. Header with Breadcrumb Journey */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/[0.08] pb-2.5">
        <div className="flex items-center gap-1.5 font-semibold uppercase tracking-wider text-amber-300">
          <Terminal className="h-4 w-4" />
          <span>Tiến trình Thực thi Thời gian thực (Live Execution Plane)</span>
        </div>
        <div className="flex items-center gap-1 font-mono text-[11px] text-slate-400">
          <span>Trạng thái:</span>
          <span
            className={`rounded px-1.5 py-0.5 font-bold uppercase ${
              message.status === "completed"
                ? "bg-emerald-400/20 text-emerald-300"
                : message.status === "failed"
                ? "bg-rose-400/20 text-rose-300"
                : "bg-cyan-400/20 text-cyan-300 animate-pulse"
            }`}
          >
            {message.status ?? "RUNNING"}
          </span>
        </div>
      </div>

      {/* Execution Journey Breadcrumb */}
      <div className="flex flex-wrap items-center gap-1.5 rounded-xl bg-white/[0.025] px-3 py-2 text-[11px]">
        {breadcrumbs.map((b, idx) => (
          <React.Fragment key={idx}>
            <div
              className={`flex items-center gap-1 font-medium ${
                b.state === "active"
                  ? "text-cyan-300 animate-pulse font-bold"
                  : b.state === "alert"
                  ? "text-rose-400 font-bold"
                  : "text-slate-400"
              }`}
            >
              {b.state === "active" && <span className="h-1.5 w-1.5 rounded-full bg-cyan-400" />}
              {b.state === "alert" && <ShieldAlert className="h-3 w-3 text-rose-400" />}
              {b.state === "done" && <CheckCircle2 className="h-3 w-3 text-emerald-400" />}
              <span>{b.label}</span>
            </div>
            {idx < breadcrumbs.length - 1 && <span className="text-slate-600">→</span>}
          </React.Fragment>
        ))}
      </div>

      {/* 2. Visualizer Tabs (Hành trình, Tệp tin vật lý, Cụm luồng, Trace Tree) */}
      <div className="flex flex-wrap gap-1 border-b border-white/[0.06] pb-1 text-[11px]">
        <button
          type="button"
          onClick={() => setActiveTab("journey")}
          className={`rounded-lg px-2.5 py-1 font-medium transition ${
            activeTab === "journey"
              ? "bg-cyan-400/20 text-cyan-200 border border-cyan-400/30"
              : "text-slate-400 hover:text-white"
          }`}
        >
          Các bước xử lý ({steps.length})
        </button>
        <button
          type="button"
          onClick={() => setActiveTab("files")}
          className={`rounded-lg px-2.5 py-1 font-medium transition ${
            activeTab === "files"
              ? "bg-amber-400/20 text-amber-200 border border-amber-400/30"
              : "text-slate-400 hover:text-white"
          }`}
        >
          Tệp tin vật lý ({files.length})
        </button>
        <button
          type="button"
          onClick={() => setActiveTab("clusters")}
          className={`rounded-lg px-2.5 py-1 font-medium transition ${
            activeTab === "clusters"
              ? "bg-violet-400/20 text-violet-200 border border-violet-400/30"
              : "text-slate-400 hover:text-white"
          }`}
        >
          Cụm luồng & Phân hệ ({clusters.length})
        </button>
        {Boolean(traceId) && (
          <button
            type="button"
            onClick={() => setActiveTab("trace")}
            className={`rounded-lg px-2.5 py-1 font-medium transition ${
              activeTab === "trace"
                ? "bg-emerald-400/20 text-emerald-200 border border-emerald-400/30"
                : "text-slate-400 hover:text-white"
            }`}
          >
            Trace Tree & Phán quyết
          </button>
        )}
      </div>

      {/* Tab 1: Các bước xử lý */}
      {activeTab === "journey" && (
        <div className="space-y-1.5 pt-1">
          {steps.map((step) => (
            <div
              key={step.id}
              className="flex items-start justify-between gap-2 rounded-lg bg-black/40 px-3 py-2 font-mono text-[11px]"
            >
              <div className="flex items-center gap-2">
                {step.status === "running" ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin text-cyan-300" />
                ) : step.status === "success" ? (
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" />
                ) : step.status === "error" ? (
                  <ShieldAlert className="h-3.5 w-3.5 text-rose-400" />
                ) : (
                  <AlertTriangle className="h-3.5 w-3.5 text-amber-400" />
                )}
                <span className={step.status === "running" ? "font-semibold text-cyan-200" : "text-slate-200"}>
                  {step.name}
                </span>
              </div>
              <div className="text-right text-slate-500">
                {step.detail && <span className="mr-2 text-slate-400">{step.detail}</span>}
                {step.timeMs != null && <span>{step.timeMs}ms</span>}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Tab 2: Tệp tin vật lý (Physical Files) */}
      {activeTab === "files" && (
        <div className="space-y-1.5 pt-1">
          <div className="rounded-lg bg-black/30 p-2 text-[11px] text-slate-400">
            Dưới đây là các file mã nguồn và tệp cơ sở dữ liệu SQLite/JSONL thực tế mà SCP đang đọc, khóa chuyển đổi hoặc kiểm tra:
          </div>
          <div className="space-y-1">
            {files.map((file, idx) => (
              <div
                key={idx}
                className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-black/40 px-3 py-2 font-mono text-[11px]"
              >
                <div className="flex items-center gap-2">
                  <FileCode className="h-3.5 w-3.5 text-cyan-300" />
                  <span className="font-semibold text-slate-200">{file.path}</span>
                  <span
                    className={`rounded px-1.5 py-0.2 text-[10px] font-bold ${
                      file.action === "LOCK"
                        ? "bg-amber-400/20 text-amber-200"
                        : file.action === "WRITE"
                        ? "bg-rose-400/20 text-rose-200"
                        : "bg-cyan-400/20 text-cyan-200"
                    }`}
                  >
                    {file.action}
                  </span>
                </div>
                <div className="flex items-center gap-2 text-slate-400">
                  <span>{file.detail}</span>
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Tab 3: Cụm luồng & Phân hệ (Clusters & Subsystems) */}
      {activeTab === "clusters" && (
        <div className="space-y-3 pt-1">
          <div className="grid gap-2 sm:grid-cols-2">
            {clusters.map((c) => (
              <div key={c.id} className="rounded-xl border border-white/[0.06] bg-black/40 p-2.5 text-[11px]">
                <div className="flex items-center justify-between font-semibold text-slate-200">
                  <span className="flex items-center gap-1.5">
                    <Cpu className="h-3.5 w-3.5 text-violet-300" /> {c.name}
                  </span>
                  <span
                    className={`rounded px-1.5 py-0.5 text-[10px] font-bold uppercase ${
                      c.status === "active" ? "bg-emerald-400/20 text-emerald-300" : "bg-slate-700 text-slate-400"
                    }`}
                  >
                    {c.status}
                  </span>
                </div>
                <div className="mt-1 text-slate-400 font-mono">{c.role}</div>
                {c.port && <div className="mt-0.5 text-[10px] text-cyan-300/80 font-mono">Port: {c.port}</div>}
              </div>
            ))}
          </div>

          {/* Subsystems Architecture Grid */}
          <div className="rounded-xl border border-white/[0.06] bg-black/30 p-2.5">
            <div className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
              Phân định Phân hệ SCP (12 Core & 8 Cross-Cutting)
            </div>
            <div className="flex flex-wrap gap-1.5">
              {SUBSYSTEMS_MAP.map((s) => (
                <div
                  key={s.id}
                  className="flex items-center gap-1.5 rounded-lg border border-white/[0.08] bg-white/[0.03] px-2 py-1 text-[10px] font-mono"
                >
                  <span className="h-1.5 w-1.5 rounded-full bg-cyan-400" />
                  <span className="text-slate-200">{s.name}</span>
                  <span className="text-slate-500">({s.status})</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Tab 4: Trace Tree & Decision Ledger */}
      {activeTab === "trace" && (
        <div className="rounded-xl border border-cyan-500/20 bg-slate-900/90 p-3.5 text-xs font-mono">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/[0.08] pb-2 font-semibold text-cyan-300">
            <span className="flex items-center gap-1.5">
              <Layers3 className="h-4 w-4" /> Bằng chứng & Cây quyết định nguyên nhân (Causal Trace)
            </span>
            <span className="text-[11px] text-slate-400">{traceId}</span>
          </div>

          <div className="mt-2.5 grid gap-2 sm:grid-cols-2">
            <div className="rounded-lg bg-black/40 p-2.5">
              <span className="text-slate-400">Luồng xử lý (Routing Lane):</span>
              <div className="mt-1 text-cyan-200 font-bold">
                {String(traceData?.lane || message.data?.lane || "LANE_AGENT_EXEC")}
              </div>
            </div>
            <div className="rounded-lg bg-black/40 p-2.5">
              <span className="text-slate-400">Phán quyết & Độ tin cậy:</span>
              <div className="mt-1 text-emerald-300 font-bold">
                {String(message.data?.verdict || "PASS")} · {Math.round(Number(message.data?.confidence || 1) * 100)}%
              </div>
            </div>
            <div className="rounded-lg bg-black/40 p-2.5">
              <span className="text-slate-400">Cổng Quản trị (Governance):</span>
              <div className="mt-1 text-slate-200">
                {String(traceData?.governance_decision || message.data?.governance_decision || "ALLOW")}
              </div>
            </div>
            <div className="rounded-lg bg-black/40 p-2.5">
              <span className="text-slate-400">Thời gian thực thi tổng:</span>
              <div className="mt-1 text-slate-200 font-mono">
                {message.data?.elapsed_ms != null ? `${message.data.elapsed_ms}ms` : "—"}
              </div>
            </div>
          </div>

          {Array.isArray(message.data?.slm_trace) && message.data.slm_trace.length > 0 && (
            <div className="mt-2.5 rounded-lg bg-black/40 p-2.5">
              <span className="text-slate-400">Mô hình lý luận SLM tham gia:</span>
              <div className="mt-1 space-y-1">
                {(message.data.slm_trace as JsonRecord[]).map((step, idx) => (
                  <div key={idx} className="flex items-center justify-between text-[11px] text-slate-300">
                    <span>• {asText(step.slm_name || step.source || "reasoner")}</span>
                    <span className="text-slate-500">{step.time_ms ? `${step.time_ms}ms` : ""}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* 3. Error Diagnostic Box: "Lỗi đâu nhìn là ra ngay" */}
      {message.errorDetail && (
        <div className="rounded-xl border border-rose-500/40 bg-rose-950/40 p-3.5 text-xs text-rose-100 shadow-md">
          <div className="flex items-start gap-2.5">
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-rose-400" />
            <div className="min-w-0 flex-1 space-y-1.5">
              <div className="font-bold text-rose-200 tracking-wide uppercase">
                BÁO CÁO SỰ CỐ & CHẨN ĐOÁN (Lỗi đâu nhìn là ra ngay)
              </div>
              <div className="font-mono text-rose-300">
                <span className="text-rose-400">Mã lỗi:</span> [{message.errorDetail.code ?? "ERR_RUNTIME"}]{" "}
                {message.errorDetail.message}
              </div>
              {message.errorDetail.rootCause && (
                <div>
                  <span className="text-rose-300/80">Nguyên nhân gốc rễ:</span>{" "}
                  <span className="font-semibold text-rose-100">{message.errorDetail.rootCause}</span>
                </div>
              )}
              {message.errorDetail.hint && (
                <div className="italic text-rose-200/90">
                  Gợi ý xử lý: {message.errorDetail.hint}
                </div>
              )}

              {/* Action buttons */}
              <div className="mt-2 flex flex-wrap items-center gap-2 pt-1">
                {onTriggerControlledFix && (
                  <button
                    type="button"
                    onClick={() => void onTriggerControlledFix()}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-amber-400/40 bg-amber-400/20 px-3 py-1.5 text-xs font-semibold text-amber-100 transition hover:bg-amber-400/30"
                  >
                    <Wrench className="h-3.5 w-3.5" />
                    <span>Sửa có kiểm soát</span>
                  </button>
                )}
                {onRefreshSystem && (
                  <button
                    type="button"
                    onClick={() => void onRefreshSystem()}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.08] px-3 py-1.5 text-xs font-semibold text-slate-200 transition hover:bg-white/15"
                  >
                    <RefreshCw className="h-3.5 w-3.5" />
                    <span>Kiểm tra lại hệ thống</span>
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
