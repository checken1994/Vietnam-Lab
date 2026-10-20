"use client"

import React, { useState, useMemo } from "react"
import {
  RotateCcw,
  ShieldCheck,
  Cpu,
  Check,
  Undo2,
  Lock,
} from "lucide-react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"

export interface StateNode {
  id: string
  label: string
  phase: "intake" | "execution" | "resilience" | "terminal"
  description: string
  allowedNext: string[]
}

const TASK_KERNEL_STATES: StateNode[] = [
  // Intake & Planning
  {
    id: "CREATED",
    label: "CREATED",
    phase: "intake",
    description: "Nhiệm vụ được khởi tạo, gán TaskID và ghi journal ban đầu.",
    allowedNext: ["PLANNING", "CANCELLED"],
  },
  {
    id: "PLANNING",
    label: "PLANNING",
    phase: "intake",
    description: "Bộ lập kế hoạch phân tách nhiệm vụ thành DAG các bước thực thi.",
    allowedNext: ["READY", "WAITING_APPROVAL", "FAILED", "CANCELLED"],
  },
  {
    id: "READY",
    label: "READY",
    phase: "intake",
    description: "Kế hoạch hoàn tất và sẵn sàng đưa vào hàng đợi thực thi.",
    allowedNext: ["QUEUED", "CANCELLED"],
  },
  {
    id: "QUEUED",
    label: "QUEUED",
    phase: "intake",
    description: "Đang chờ worker nhận lease khả dụng với lease timeout.",
    allowedNext: ["LEASED", "CANCELLED"],
  },

  // Execution Phase
  {
    id: "LEASED",
    label: "LEASED",
    phase: "execution",
    description: "Worker đã nhận khóa lease độc quyền (OCC version gắn kèm).",
    allowedNext: ["RUNNING", "RECOVERING", "CANCELLED"],
  },
  {
    id: "RUNNING",
    label: "RUNNING",
    phase: "execution",
    description: "Worker đang thực thi mã/hành động dưới sandbox và policy gate.",
    allowedNext: ["WAITING_TOOL", "VERIFYING", "CHECKPOINTED", "RECOVERING", "HUMAN_REVIEW", "RETRY_SCHEDULED", "FAILED", "CANCELLED"],
  },
  {
    id: "WAITING_TOOL",
    label: "WAITING_TOOL",
    phase: "execution",
    description: "Tạm dừng đợi phản hồi từ công cụ ngoại vi hoặc API Gateway.",
    allowedNext: ["VERIFYING", "UNKNOWN", "RECOVERING", "RETRY_SCHEDULED", "FAILED", "CANCELLED"],
  },
  {
    id: "VERIFYING",
    label: "VERIFYING",
    phase: "execution",
    description: "Verifier cổng thực tế kiểm chứng căn cứ epistemic & an toàn.",
    allowedNext: ["RUNNING", "COMPLETED", "HUMAN_REVIEW", "RETRY_SCHEDULED", "FAILED"],
  },

  // Resilience & Recovery Phase
  {
    id: "CHECKPOINTED",
    label: "CHECKPOINTED",
    phase: "resilience",
    description: "Snapshot trạng thái an toàn được lưu trữ bền vững vào journal.",
    allowedNext: ["RUNNING", "QUEUED", "CANCELLED"],
  },
  {
    id: "UNKNOWN",
    label: "UNKNOWN",
    phase: "resilience",
    description: "Mất kết nối hoặc phản hồi bất định; kích hoạt fail-closed.",
    allowedNext: ["RECONCILING", "HUMAN_REVIEW", "RECOVERING", "FAILED", "CANCELLED"],
  },
  {
    id: "RECOVERING",
    label: "RECOVERING",
    phase: "resilience",
    description: "Khôi phục trạng thái từ snapshot gần nhất hoặc rollback workspace.",
    allowedNext: ["RECONCILING", "CHECKPOINTED", "QUEUED", "HUMAN_REVIEW", "FAILED"],
  },
  {
    id: "RECONCILING",
    label: "RECONCILING",
    phase: "resilience",
    description: "Đối chiếu side-effect bên ngoài để xác minh tính idempotent.",
    allowedNext: ["RECOVERING", "CHECKPOINTED", "QUEUED", "HUMAN_REVIEW", "FAILED", "CANCELLED"],
  },
  {
    id: "HUMAN_REVIEW",
    label: "HUMAN_REVIEW",
    phase: "resilience",
    description: "Chuyển giao cho người điều hành duyệt khi vi phạm chính sách rủi ro cao.",
    allowedNext: ["READY", "CANCELLED", "FAILED"],
  },
  {
    id: "RETRY_SCHEDULED",
    label: "RETRY_SCHEDULED",
    phase: "resilience",
    description: "Lập lịch thử lại sau backoff lũy thừa (tối đa max_attempts).",
    allowedNext: ["QUEUED", "FAILED", "CANCELLED"],
  },

  // Terminal States
  {
    id: "COMPLETED",
    label: "COMPLETED",
    phase: "terminal",
    description: "Nhiệm vụ hoàn thành trọn vẹn, đã xác thực bằng chứng.",
    allowedNext: [],
  },
  {
    id: "FAILED",
    label: "FAILED",
    phase: "terminal",
    description: "Thất bại không thể phục hồi hoặc vượt quá số lần thử.",
    allowedNext: [],
  },
  {
    id: "CANCELLED",
    label: "CANCELLED",
    phase: "terminal",
    description: "Nhiệm vụ bị hủy bởi người dùng hoặc hệ thống giám sát an toàn.",
    allowedNext: [],
  },
]

interface RollbackRecord {
  id: string
  timestamp: string
  taskId: string
  attempt: number
  tier: "PERMISSIVE" | "STANDARD" | "STRICT"
  reason: string
  action: "SNAPSHOT_RESTORED" | "OCC_RETRY" | "CLEAN_ROLLBACK"
}

const SAMPLE_ROLLBACK_HISTORY: RollbackRecord[] = [
  {
    id: "rb-01",
    timestamp: "10:14:22",
    taskId: "task-b58-0091",
    attempt: 1,
    tier: "STANDARD",
    reason: "AssertionFailed: Grounding token missing in response",
    action: "SNAPSHOT_RESTORED",
  },
  {
    id: "rb-02",
    timestamp: "10:14:24",
    taskId: "task-b58-0091",
    attempt: 2,
    tier: "STANDARD",
    reason: "Remediation verified: regenerated with grounded context",
    action: "CLEAN_ROLLBACK",
  },
  {
    id: "rb-03",
    timestamp: "10:15:02",
    taskId: "task-b58-0094",
    attempt: 1,
    tier: "STRICT",
    reason: "EgressSecurityTripwire: Unauthorized IP destination",
    action: "CLEAN_ROLLBACK",
  },
]

export function TaskKernelStateMachine() {
  const [selectedState, setSelectedState] = useState<string>("RUNNING")
  const [selectedTier, setSelectedTier] = useState<"PERMISSIVE" | "STANDARD" | "STRICT">("STANDARD")

  const currentState = useMemo(() => {
    return TASK_KERNEL_STATES.find((s) => s.id === selectedState) || TASK_KERNEL_STATES[0]
  }, [selectedState])

  const phaseColors: Record<string, { bg: string; text: string; border: string }> = {
    intake: { bg: "bg-cyan-500/10", text: "text-cyan-300", border: "border-cyan-500/30" },
    execution: { bg: "bg-emerald-500/10", text: "text-emerald-300", border: "border-emerald-500/30" },
    resilience: { bg: "bg-amber-500/10", text: "text-amber-300", border: "border-amber-500/30" },
    terminal: { bg: "bg-rose-500/10", text: "text-rose-300", border: "border-rose-500/30" },
  }

  return (
    <div className="space-y-6">
      {/* Overview & Header */}
      <Card className="border-white/10 bg-black/40 backdrop-blur-xl">
        <CardHeader className="pb-3">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
            <div>
              <div className="flex items-center gap-2">
                <Badge variant="outline" className="border-cyan-400/40 text-cyan-300 bg-cyan-400/10">
                  TaskKernel v3.8
                </Badge>
                <Badge variant="outline" className="border-emerald-400/40 text-emerald-300 bg-emerald-400/10">
                  17 Trạng thái Chuẩn
                </Badge>
                <Badge variant="outline" className="border-violet-400/40 text-violet-300 bg-violet-400/10">
                  B58 Tiered Verification
                </Badge>
              </div>
              <CardTitle className="mt-2 text-xl font-bold text-white flex items-center gap-2">
                <Cpu className="h-5 w-5 text-cyan-400" />
                TaskKernel State Machine & Verifier Proof Flow
              </CardTitle>
              <CardDescription className="text-slate-400">
                Mô hình 17 trạng thái bất biến, khóa chuyển đổi nguyên tử OCC và cơ chế Soft Feedback Remediation Loop.
              </CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {/* 17 States Map Grouped by Lifecycle */}
          <div className="space-y-4">
            <div className="text-xs font-semibold uppercase tracking-wider text-slate-400">
              Bản đồ 17 trạng thái máy (Nhấp để xem chuyển tiếp hợp lệ)
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-9 gap-2">
              {TASK_KERNEL_STATES.map((node) => {
                const isSelected = node.id === selectedState
                const isNext = currentState.allowedNext.includes(node.id)
                const styling = phaseColors[node.phase]

                return (
                  <button
                    key={node.id}
                    onClick={() => setSelectedState(node.id)}
                    className={`relative flex flex-col items-center justify-center p-2.5 rounded-xl border text-center transition-all ${
                      isSelected
                        ? "border-cyan-400 bg-cyan-400/20 ring-2 ring-cyan-400/50 shadow-lg shadow-cyan-500/20"
                        : isNext
                        ? "border-emerald-400/60 bg-emerald-500/10 hover:bg-emerald-500/20 animate-pulse"
                        : "border-white/10 bg-white/[0.02] hover:bg-white/[0.06] hover:border-white/20"
                    }`}
                  >
                    <span className="text-[11px] font-mono font-bold tracking-tight text-white">
                      {node.label}
                    </span>
                    <span className={`mt-1 text-[9px] uppercase font-semibold tracking-wider ${styling.text}`}>
                      {node.phase}
                    </span>
                    {isNext && (
                      <span className="absolute -top-1.5 -right-1.5 flex h-3.5 w-3.5 items-center justify-center rounded-full bg-emerald-500 text-[8px] font-bold text-black">
                        →
                      </span>
                    )}
                  </button>
                )
              })}
            </div>

            {/* Selected State Inspector */}
            <div className="mt-4 rounded-xl border border-white/10 bg-white/[0.03] p-4">
              <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3 pb-3 border-b border-white/10">
                <div className="flex items-center gap-3">
                  <span className="text-base font-bold font-mono text-cyan-300">
                    Trạng thái: {currentState.id}
                  </span>
                  <Badge variant="outline" className={`${phaseColors[currentState.phase].border} ${phaseColors[currentState.phase].text}`}>
                    Pha: {currentState.phase.toUpperCase()}
                  </Badge>
                </div>
                <div className="text-xs text-slate-400">
                  Chuyển tiếp hợp lệ từ trạng thái này:{" "}
                  <span className="font-semibold text-white">
                    {currentState.allowedNext.length > 0 ? currentState.allowedNext.length : "0 (Điểm dừng cuối cùng)"}
                  </span>
                </div>
              </div>

              <p className="mt-3 text-sm text-slate-300 leading-relaxed">
                {currentState.description}
              </p>

              {currentState.allowedNext.length > 0 && (
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <span className="text-xs font-medium text-slate-400">Có thể chuyển sang:</span>
                  {currentState.allowedNext.map((target) => (
                    <Badge
                      key={target}
                      variant="secondary"
                      className="bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 text-xs font-mono cursor-pointer hover:bg-emerald-500/25"
                      onClick={() => setSelectedState(target)}
                    >
                      → {target}
                    </Badge>
                  ))}
                </div>
              )}
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Verifier Proof Flow & Tier Architecture (B58) */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card className="border-white/10 bg-black/40 backdrop-blur-xl">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-base font-bold text-white flex items-center gap-2">
                <ShieldCheck className="h-5 w-5 text-emerald-400" />
                B58 Verifier Proof Flow
              </CardTitle>
              <div className="flex gap-1">
                {(["PERMISSIVE", "STANDARD", "STRICT"] as const).map((tier) => (
                  <button
                    key={tier}
                    onClick={() => setSelectedTier(tier)}
                    className={`px-2.5 py-1 text-xs font-mono font-medium rounded-lg transition-all ${
                      selectedTier === tier
                        ? "bg-cyan-500 text-black font-bold shadow-md shadow-cyan-500/30"
                        : "bg-white/5 text-slate-400 hover:text-white hover:bg-white/10"
                    }`}
                  >
                    {tier}
                  </button>
                ))}
              </div>
            </div>
            <CardDescription className="text-slate-400">
              Quy chuẩn 3 tầng kiểm chứng epistemic và bảo toàn biên giới an toàn bất biến.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="rounded-xl border border-white/10 bg-black/30 p-4 space-y-3">
              <div className="flex items-center justify-between text-xs">
                <span className="text-slate-400">Tầng hoạt động:</span>
                <span className="font-mono font-bold text-cyan-300">{selectedTier}</span>
              </div>
              <div className="flex items-center justify-between text-xs">
                <span className="text-slate-400">Mục đích chính:</span>
                <span className="text-slate-200">
                  {selectedTier === "PERMISSIVE" && "Hội thoại sáng tạo & trải nghiệm UX mượt mà"}
                  {selectedTier === "STANDARD" && "Sinh mã nguồn & tổng hợp tài liệu (Mặc định)"}
                  {selectedTier === "STRICT" && "Sửa đổi Workspace, Hệ thống & Tệp tin vật lý"}
                </span>
              </div>
              <div className="flex items-center justify-between text-xs">
                <span className="text-slate-400">Yêu cầu Epistemic Grounding:</span>
                <span className={selectedTier === "PERMISSIVE" ? "text-amber-300" : "text-emerald-300"}>
                  {selectedTier === "PERMISSIVE" ? "Nới lỏng (Không withhold phản hồi hợp lệ)" : "Nghiêm ngặt (Bắt buộc căn cứ chứng minh)"}
                </span>
              </div>
              <div className="flex items-center justify-between text-xs">
                <span className="text-slate-400">Biên giới An toàn (Egress/Sandbox/PEP):</span>
                <span className="font-mono text-emerald-400 font-bold">100% FAIL-CLOSED</span>
              </div>
            </div>

            {/* Proof Pipeline Steps */}
            <div className="space-y-2">
              <div className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                Quy trình xác thực từng bước
              </div>
              <div className="space-y-2 text-xs">
                <div className="flex items-center gap-3 p-2.5 rounded-lg border border-white/5 bg-white/[0.02]">
                  <div className="flex h-6 w-6 items-center justify-center rounded-full bg-cyan-500/20 text-cyan-300 font-mono">1</div>
                  <div className="flex-1">
                    <span className="font-semibold text-white">Input Screening:</span> Lọc injection, jailbreak và tampered token.
                  </div>
                  <Check className="h-4 w-4 text-emerald-400" />
                </div>
                <div className="flex items-center gap-3 p-2.5 rounded-lg border border-white/5 bg-white/[0.02]">
                  <div className="flex h-6 w-6 items-center justify-center rounded-full bg-cyan-500/20 text-cyan-300 font-mono">2</div>
                  <div className="flex-1">
                    <span className="font-semibold text-white">Execution Isolation:</span> Chạy trong snapshot thư mục độc lập.
                  </div>
                  <Check className="h-4 w-4 text-emerald-400" />
                </div>
                <div className="flex items-center gap-3 p-2.5 rounded-lg border border-white/5 bg-white/[0.02]">
                  <div className="flex h-6 w-6 items-center justify-center rounded-full bg-cyan-500/20 text-cyan-300 font-mono">3</div>
                  <div className="flex-1">
                    <span className="font-semibold text-white">Epistemic Verifier:</span> Đối chiếu căn cứ theo tầng ({selectedTier}).
                  </div>
                  <Check className="h-4 w-4 text-emerald-400" />
                </div>
                <div className="flex items-center gap-3 p-2.5 rounded-lg border border-white/5 bg-white/[0.02]">
                  <div className="flex h-6 w-6 items-center justify-center rounded-full bg-cyan-500/20 text-cyan-300 font-mono">4</div>
                  <div className="flex-1">
                    <span className="font-semibold text-white">Commit / Feedback:</span> Ghi journal hoặc kích hoạt Remediation Loop.
                  </div>
                  <Check className="h-4 w-4 text-emerald-400" />
                </div>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Soft Feedback Loop & Rollback History */}
        <Card className="border-white/10 bg-black/40 backdrop-blur-xl">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-base font-bold text-white flex items-center gap-2">
                <RotateCcw className="h-5 w-5 text-amber-400" />
                Soft Feedback & Rollback History
              </CardTitle>
              <Badge variant="outline" className="border-amber-400/40 text-amber-300 bg-amber-400/10">
                Max 3 Attempts
              </Badge>
            </div>
            <CardDescription className="text-slate-400">
              Lịch sử hoàn nguyên snapshot tự động và cơ chế phản hồi lỗi có hướng dẫn.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-3">
              {SAMPLE_ROLLBACK_HISTORY.map((item) => (
                <div
                  key={item.id}
                  className="p-3 rounded-xl border border-white/10 bg-white/[0.02] hover:bg-white/[0.04] transition-all space-y-2"
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Undo2 className="h-4 w-4 text-amber-400" />
                      <span className="font-mono text-xs font-bold text-white">{item.taskId}</span>
                      <Badge variant="outline" className="text-[10px] py-0 px-1.5 border-white/20 text-slate-300">
                        Lần {item.attempt}/3
                      </Badge>
                    </div>
                    <span className="font-mono text-[11px] text-slate-500">{item.timestamp}</span>
                  </div>
                  <p className="text-xs text-rose-300/90 font-mono line-clamp-1 bg-black/30 p-1.5 rounded-lg border border-rose-500/20">
                    {item.reason}
                  </p>
                  <div className="flex items-center justify-between text-[11px] text-slate-400">
                    <span>Hành động:</span>
                    <Badge variant="secondary" className="bg-amber-500/10 text-amber-300 text-[10px] font-mono">
                      {item.action}
                    </Badge>
                  </div>
                </div>
              ))}
            </div>

            <div className="rounded-xl border border-cyan-400/20 bg-cyan-500/5 p-3 text-xs text-slate-300 flex items-start gap-2.5">
              <Lock className="h-4 w-4 text-cyan-400 shrink-0 mt-0.5" />
              <div>
                <span className="font-semibold text-white">Nguyên tắc Bất biến:</span> Mọi chu kỳ Remediation
                đều diễn ra trong snapshot cô lập. Nếu hết 3 lần thử mà không đạt chuẩn Verifier, hệ thống sẽ
                thực hiện hoàn nguyên sạch sẽ (Clean Rollback) và chuyển sang FAILED hoặc HUMAN_REVIEW.
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
