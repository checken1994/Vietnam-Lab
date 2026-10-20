"use client"

export type JsonRecord = Record<string, unknown>
export type MessageRole = "user" | "assistant" | "system"
export type InteractionMode = "agent_task" | "chat"
export type TaskExecutionStatus = "planning" | "executing" | "verified" | "completed" | "failed"

export interface AgentExecutionStep {
  id: string
  name: string
  status: "pending" | "running" | "success" | "warning" | "error"
  detail?: string
  timeMs?: number
}

export interface PhysicalFileTrace {
  path: string
  action: "READ" | "WRITE" | "SCAN" | "AUDIT" | "LOCK" | "CHECK"
  status: "active" | "verified" | "modified" | "blocked"
  detail?: string
  lines?: string
}

export interface ThreadClusterInfo {
  id: string
  name: string
  role: string
  port?: string
  status: "active" | "idle" | "standby" | "alert"
  workerLock?: string
}

export interface SubsystemStatus {
  id: string
  name: string
  category: "core" | "cross_cutting"
  status: "ready" | "busy" | "verified" | "degraded"
}

export interface ChatMessage {
  id: string
  role: MessageRole
  content: string
  timestamp: string
  mode: InteractionMode
  status?: TaskExecutionStatus
  data?: JsonRecord
  imageData?: string | null
  executionSteps?: AgentExecutionStep[]
  physicalFiles?: PhysicalFileTrace[]
  threadClusters?: ThreadClusterInfo[]
  subsystems?: SubsystemStatus[]
  currentBreadcrumb?: string
  errorDetail?: {
    code?: string
    message: string
    hint?: string
    rootCause?: string
  }
}

export interface ChatThread {
  id: string
  title: string
  createdAt: string
  updatedAt: string
  mode: InteractionMode
  messages: ChatMessage[]
}

export interface SpeechRecognitionResultItem {
  transcript: string
}
export interface SpeechRecognitionResultList {
  [index: number]: { [index: number]: SpeechRecognitionResultItem }
  length: number
}
export interface SpeechRecognitionEventLike {
  results?: SpeechRecognitionResultList
  error?: string
}
export interface SpeechRecognitionLike {
  lang: string
  interimResults: boolean
  continuous: boolean
  onstart: (() => void) | null
  onresult: ((event: SpeechRecognitionEventLike) => void) | null
  onerror: ((event: SpeechRecognitionEventLike) => void) | null
  onend: (() => void) | null
  start: () => void
  stop: () => void
}
export type SpeechRecognitionConstructor = new () => SpeechRecognitionLike

export const STORAGE_THREADS_KEY = "scp_chat_threads_v1"
export const STORAGE_VOICE_KEY = "scp_voice_output_enabled"
export const DEFAULT_THREAD_ID = "thread-default-primary"

export function asText(value: unknown, fallback = ""): string {
  return typeof value === "string" || typeof value === "number" ? String(value) : fallback
}

export function firstValue(record: JsonRecord | null | undefined, keys: string[], fallback = ""): string {
  if (!record) return fallback
  for (const key of keys) {
    const val = record[key]
    if (typeof val === "string" && val.trim()) return val
    if (typeof val === "number") return String(val)
  }
  return fallback
}

export function cleanTextForSpeech(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, " đoạn mã kỹ thuật ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/[*_#~>]/g, "")
    .replace(/\s+/g, " ")
    .trim()
}

export function createDefaultThread(): ChatThread {
  const now = new Date().toISOString()
  return {
    id: DEFAULT_THREAD_ID,
    title: "Tổng quan & Điều khiển",
    createdAt: now,
    updatedAt: now,
    mode: "agent_task",
    messages: [
      {
        id: "welcome-msg",
        role: "assistant",
        content:
          "Chào mừng bạn đến với Trung tâm Điều khiển Tác vụ Tự chủ SCP (Agent OS Control Plane)!\n\n" +
          "• Chọn **⚡ Ra lệnh thực thi (Agent Task)** để giao việc cho SCP: kiểm tra lỗi hệ thống, tự sửa chữa có kiểm soát, tra cứu web, hoặc quản lý tiến trình.\n" +
          "• Chọn **💬 Hỏi đáp (Q&A Chat)** để đàm thoại, tra cứu kiến thức và truy vết nhân quả.\n" +
          "• Toàn bộ tiến trình thực thi thời gian thực hiển thị chi tiết: Tệp tin vật lý, Cụm luồng, Phân hệ lõi và Vị trí hiện tại của SCP.",
        timestamp: now,
        mode: "agent_task",
        status: "completed",
        currentBreadcrumb: "Intent -> RBAC Gate -> Kernel Lock -> Ready",
        executionSteps: [
          { id: "s1", name: "1. Khởi động Kernel State Machine", status: "success", detail: "17 trạng thái hợp lệ đã sẵn sàng" },
          { id: "s2", name: "2. Kích hoạt Cổng Quản trị (Governance Gate)", status: "success", detail: "Phân quyền RBAC & Anti-tamper" },
          { id: "s3", name: "3. Sẵn sàng nhận lệnh từ Người vận hành", status: "success", detail: "Chờ người dùng gửi lệnh hoặc câu hỏi" },
        ],
        physicalFiles: [
          { path: "scp/task_kernel_parts/taskkernel.py", action: "VERIFIED" as any, status: "verified", detail: "ACID state engine ready" },
          { path: "data/task_kernel.sqlite3", action: "LOCK", status: "active", detail: "WAL journal initialized" },
        ],
        threadClusters: [
          { id: "cluster-a", name: "Agent Control & CDP Navigator", role: "Trình duyệt & noVNC", port: "9223 / 6080", status: "active" },
          { id: "cluster-b", name: "Task Kernel ACID Engine", role: "Trạng thái tác vụ bền vững", status: "active" },
          { id: "cluster-c", name: "Governance & RBAC Gate", role: "Chặn bypass & kiểm soát quyền", status: "active" },
          { id: "cluster-d", name: "Continuous Operations & AutoFix", role: "Vòng lặp tự sửa chữa", status: "idle" },
        ],
      },
    ],
  }
}
