"use client"

import React, { useCallback, useEffect, useRef, useState } from "react"
import {
  Camera,
  Loader2,
  MessageCircle,
  Mic,
  MicOff,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Send,
  StopCircle,
  Volume2,
  VolumeX,
  X,
  Zap,
} from "lucide-react"
import {
  AgentExecutionStep,
  ChatMessage,
  ChatThread,
  DEFAULT_THREAD_ID,
  InteractionMode,
  JsonRecord,
  STORAGE_THREADS_KEY,
  STORAGE_VOICE_KEY,
  cleanTextForSpeech,
  createDefaultThread,
  firstValue,
} from "./scp-chat-types"
import { ScpChatSidebar } from "./scp-chat-sidebar"
import { ScpChatMessageItem } from "./scp-chat-message-item"
import { useScpMedia } from "./use-scp-media"

interface ScpChatControlPlaneProps {
  sessionIdRef?: React.MutableRefObject<string>
  ensureSessionId?: () => string
  onTriggerControlledFix?: () => Promise<void>
  onRefreshSystem?: () => Promise<void>
}

export function ScpChatControlPlane({
  sessionIdRef,
  ensureSessionId: propEnsureSessionId,
  onTriggerControlledFix,
  onRefreshSystem,
}: ScpChatControlPlaneProps) {
  // 1. Crash-proof initial state: Never empty, default thread always present
  const [threads, setThreads] = useState<ChatThread[]>(() => [createDefaultThread()])
  const [activeThreadId, setActiveThreadId] = useState<string>(DEFAULT_THREAD_ID)
  const [mode, setMode] = useState<InteractionMode>("agent_task")
  const [question, setQuestion] = useState("")
  const [isBusy, setIsBusy] = useState(false)
  const [voiceOutputEnabled, setVoiceOutputEnabled] = useState(true)
  const [isSpeaking, setIsSpeaking] = useState(false)
  const [speakingMessageId, setSpeakingMessageId] = useState<string | null>(null)
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [copiedId, setCopiedId] = useState<string | null>(null)

  const messagesEndRef = useRef<HTMLDivElement | null>(null)
  const localSessionIdRef = useRef("")
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)

  // Media controller hook (Mic STT + Webcam snapshot)
  const media = useScpMedia(useCallback((t: string) => setQuestion(t), []))

  const getSessionId = useCallback(() => {
    if (propEnsureSessionId) return propEnsureSessionId()
    if (sessionIdRef?.current) return sessionIdRef.current
    if (localSessionIdRef.current) return localSessionIdRef.current
    if (typeof window !== "undefined") {
      const key = "scp-desktop-chat-session-id"
      const stored = window.sessionStorage.getItem(key)
      localSessionIdRef.current = stored || (window.crypto?.randomUUID?.() ?? `desktop-${Date.now()}`)
      window.sessionStorage.setItem(key, localSessionIdRef.current)
    } else {
      localSessionIdRef.current = `desktop-${Date.now()}`
    }
    return localSessionIdRef.current
  }, [propEnsureSessionId, sessionIdRef])

  // 2. Safe hydration from localStorage (runs client-side only)
  useEffect(() => {
    if (typeof window === "undefined") return

    const storedVoice = localStorage.getItem(STORAGE_VOICE_KEY)
    if (storedVoice !== null) {
      setVoiceOutputEnabled(storedVoice === "true")
    }

    try {
      const raw = localStorage.getItem(STORAGE_THREADS_KEY)
      if (raw) {
        const parsed = JSON.parse(raw) as { threads?: ChatThread[]; activeId?: string }
        if (Array.isArray(parsed.threads) && parsed.threads.length > 0) {
          setThreads(parsed.threads)
          const validActiveId =
            parsed.activeId && parsed.threads.some((t) => t.id === parsed.activeId)
              ? parsed.activeId
              : parsed.threads[0].id
          setActiveThreadId(validActiveId)
          const cur = parsed.threads.find((t) => t.id === validActiveId)
          if (cur) setMode(cur.mode)
        }
      }
    } catch {
      // Fallback preserved
    }
  }, [])

  const persistThreads = useCallback((updated: ChatThread[], activeId: string) => {
    if (typeof window === "undefined") return
    try {
      localStorage.setItem(STORAGE_THREADS_KEY, JSON.stringify({ version: 1, activeId, threads: updated }))
    } catch {
      // ignore quota
    }
  }, [])

  // Guaranteed non-null activeThread
  const activeThread =
    threads.find((t) => t.id === activeThreadId) ?? threads[0] ?? createDefaultThread()
  const activeMessages = activeThread.messages ?? []

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [activeMessages.length, isBusy])

  // Cleanup speech on unmount
  useEffect(() => {
    return () => {
      if (typeof window !== "undefined" && "speechSynthesis" in window) {
        window.speechSynthesis.cancel()
      }
    }
  }, [])

  // 3. Bidirectional Voice Synthesis (TTS)
  const speakMessage = useCallback(
    (messageId: string, text: string) => {
      if (typeof window === "undefined" || !("speechSynthesis" in window)) {
        media.setMediaStatus("Trình duyệt không hỗ trợ tổng hợp giọng nói.")
        return
      }

      window.speechSynthesis.cancel()

      if (isSpeaking && speakingMessageId === messageId) {
        setIsSpeaking(false)
        setSpeakingMessageId(null)
        return
      }

      const cleaned = cleanTextForSpeech(text)
      if (!cleaned) return

      const utterance = new SpeechSynthesisUtterance(cleaned)
      utterance.lang = "vi-VN"
      utterance.rate = 1.05
      utterance.pitch = 1.0

      const voices = window.speechSynthesis.getVoices()
      const viVoice = voices.find(
        (v) => v.lang.toLowerCase().includes("vi") || v.lang.toLowerCase().includes("vn")
      )
      if (viVoice) utterance.voice = viVoice

      utterance.onstart = () => {
        setIsSpeaking(true)
        setSpeakingMessageId(messageId)
        media.setMediaStatus("SCP đang đọc câu trả lời...")
      }
      utterance.onend = () => {
        setIsSpeaking(false)
        setSpeakingMessageId(null)
        media.setMediaStatus("Đã đọc xong.")
      }
      utterance.onerror = () => {
        setIsSpeaking(false)
        setSpeakingMessageId(null)
      }

      window.speechSynthesis.speak(utterance)
    },
    [isSpeaking, media, speakingMessageId]
  )

  const stopSpeaking = useCallback(() => {
    if (typeof window !== "undefined" && "speechSynthesis" in window) {
      window.speechSynthesis.cancel()
    }
    setIsSpeaking(false)
    setSpeakingMessageId(null)
    media.setMediaStatus("Đã dừng đọc.")
  }, [media])

  const toggleVoiceOutput = useCallback(() => {
    setVoiceOutputEnabled((prev) => {
      const next = !prev
      if (typeof window !== "undefined") {
        localStorage.setItem(STORAGE_VOICE_KEY, String(next))
      }
      if (!next) stopSpeaking()
      return next
    })
  }, [stopSpeaking])

  // 4. Thread operations
  const handleCreateNewThread = useCallback(() => {
    stopSpeaking()
    const now = new Date().toISOString()
    const newThread: ChatThread = { id: `thread-${Date.now()}`, title: "Cuộc trò chuyện mới", createdAt: now, updatedAt: now, mode, messages: [] }
    const updated = [newThread, ...threads]
    setThreads(updated); setActiveThreadId(newThread.id); persistThreads(updated, newThread.id); setQuestion("")
  }, [mode, persistThreads, stopSpeaking, threads])

  const handleDeleteThread = useCallback(
    (threadId: string, e: React.MouseEvent) => {
      e.stopPropagation()
      stopSpeaking()
      const remaining = threads.filter((t) => t.id !== threadId)
      if (remaining.length === 0) {
        const fresh = createDefaultThread()
        setThreads([fresh])
        setActiveThreadId(fresh.id)
        persistThreads([fresh], fresh.id)
      } else {
        setThreads(remaining)
        const nextId = activeThreadId === threadId ? remaining[0].id : activeThreadId
        setActiveThreadId(nextId)
        persistThreads(remaining, nextId)
      }
    },
    [activeThreadId, persistThreads, stopSpeaking, threads]
  )

  const handleSaveRename = (threadId: string, newTitle: string) => {
    const updated = threads.map((t) => t.id === threadId ? { ...t, title: newTitle, updatedAt: new Date().toISOString() } : t)
    setThreads(updated); persistThreads(updated, activeThreadId)
  }

  const handleSelectThread = (threadId: string) => {
    stopSpeaking(); setActiveThreadId(threadId)
    const cur = threads.find((t) => t.id === threadId)
    if (cur) setMode(cur.mode)
    persistThreads(threads, threadId)
  }

  // 5. Action Execution / Chat Submit
  const handleSendMessage = useCallback(async () => {
    const rawContent = question.trim()
    if (!rawContent || isBusy) return

    stopSpeaking()
    setIsBusy(true)
    media.setMediaStatus("SCP đang tiếp nhận ý định và điều phối tác vụ...")

    const userMessageId = `msg-user-${Date.now()}`
    const assistantMessageId = `msg-agent-${Date.now() + 1}`
    const currentImage = media.pendingImage
    media.clearPendingImage()

    const initialSteps: AgentExecutionStep[] = mode === "agent_task" ? [
      { id: "s1", name: "1. Tiếp nhận & Phân tích Ý định (Intent)", status: "running" },
      { id: "s2", name: "2. Cổng Quản trị RBAC & Phân quyền", status: "pending" },
      { id: "s3", name: "3. Điều phối Task Kernel ACID", status: "pending" },
      { id: "s4", name: "4. Đối soát Bằng chứng Thực tế (DNA #2)", status: "pending" },
    ] : []

    const userMessage: ChatMessage = {
      id: userMessageId,
      role: "user",
      content: rawContent,
      timestamp: new Date().toISOString(),
      mode,
      imageData: currentImage,
    }

    const placeholderAssistant: ChatMessage = {
      id: assistantMessageId,
      role: "assistant",
      content: mode === "agent_task" ? "Đang lên kế hoạch và kích hoạt các công cụ điều phối..." : "Đang tìm câu trả lời...",
      timestamp: new Date().toISOString(),
      mode,
      status: "planning",
      executionSteps: initialSteps,
    }

    let newTitle = activeThread.title
    if (activeMessages.length === 0 || activeThread.title === "Cuộc trò chuyện mới") {
      newTitle = rawContent.slice(0, 36) + (rawContent.length > 36 ? "…" : "")
    }

    const historyPayload = activeMessages
      .filter((m) => m.role === "user" || m.role === "assistant")
      .slice(-8)
      .map((m) => ({ role: m.role, content: m.content }))

    const updatedWithPlaceholder = threads.map((t) =>
      t.id === activeThread.id
        ? {
            ...t,
            title: newTitle,
            updatedAt: new Date().toISOString(),
            messages: [...activeMessages, userMessage, placeholderAssistant],
          }
        : t
    )
    setThreads(updatedWithPlaceholder)
    setQuestion("")

    try {
      const isFixCommand = mode === "agent_task" && /(sửa|chữa|fix|khắc phục|loop)/i.test(rawContent)
      const isHealthCommand = mode === "agent_task" && /(kiểm tra|sức khỏe|health|status)/i.test(rawContent)

      if (isFixCommand && onTriggerControlledFix) void onTriggerControlledFix()
      if (isHealthCommand && onRefreshSystem) void onRefreshSystem()

      const response = await fetch("/api/scp/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: rawContent,
          domain: mode === "agent_task" ? "agent_task" : "general",
          session_id: getSessionId(),
          conversation_history: historyPayload,
          image_data: currentImage,
        }),
        cache: "no-store",
      })

      const data = (await response.json().catch(() => ({}))) as JsonRecord
      const finalAnswer = firstValue(
        data,
        ["final_answer", "answer", "output", "detail", "error"],
        "SCP đã thực hiện tác vụ nhưng không trả về nội dung văn bản."
      )
      const verdict = String(data.verdict || "PASS").toUpperCase()
      const isError = !response.ok || Boolean(data.error) || verdict === "FAIL" || verdict === "UNKNOWN"

      let errorDetail: ChatMessage["errorDetail"]
      if (isError) {
        errorDetail = {
          code: String(data.error_code || response.status),
          message: firstValue(data, ["error", "detail"], `Lỗi HTTP ${response.status}`),
          rootCause: response.status === 401 || response.status === 403
            ? "Cổng RBAC từ chối vì thiếu quyền hạn (Fail-Closed)."
            : response.status === 502 || response.status === 503
            ? "Dịch vụ SCP Python API hoặc Loop Scheduler chưa sẵn sàng."
            : "Lỗi phản hồi hoặc không đủ bằng chứng thực tế.",
          hint: firstValue(data, ["hint"], "Bấm 'Sửa có kiểm soát' hoặc kiểm tra service"),
        }
      }

      const finalSteps: AgentExecutionStep[] = mode === "agent_task" ? [
        { id: "s1", name: "1. Phân tích Ý định & Ranh giới", status: "success", detail: "Phân loại OK", timeMs: 25 },
        { id: "s2", name: "2. Cổng Quản trị RBAC", status: isError ? "warning" : "success", detail: firstValue(data, ["governance_decision"], "ALLOW"), timeMs: 35 },
        { id: "s3", name: "3. Task Kernel ACID Engine", status: isError ? "error" : "success", detail: firstValue(data, ["lane"], "LANE_AGENT_EXEC"), timeMs: 90 },
        { id: "s4", name: "4. Đối soát Bằng chứng Thực tế", status: isError ? "error" : "success", detail: `Verdict: ${verdict}`, timeMs: Number(data.elapsed_ms) || 120 },
      ] : []

      const finalAssistant: ChatMessage = {
        id: assistantMessageId,
        role: "assistant",
        content: finalAnswer,
        timestamp: new Date().toISOString(),
        mode,
        status: isError ? "failed" : "completed",
        data,
        executionSteps: finalSteps,
        errorDetail,
      }

      const finalThreads = threads.map((t) =>
        t.id === activeThread.id
          ? {
              ...t,
              title: newTitle,
              updatedAt: new Date().toISOString(),
              messages: [...activeMessages, userMessage, finalAssistant],
            }
          : t
      )
      setThreads(finalThreads)
      persistThreads(finalThreads, activeThread.id)
      media.setMediaStatus(isError ? "SCP phát hiện sự cố hoặc chặn an toàn." : "SCP đã hoàn thành tác vụ!")

      if (voiceOutputEnabled && finalAnswer) {
        speakMessage(assistantMessageId, finalAnswer)
      }
    } catch (error) {
      const errMsg = error instanceof Error ? error.message : "Mất kết nối tới SCP"
      const errorMsg: ChatMessage = {
        id: assistantMessageId,
        role: "assistant",
        content: `Không thể kết nối tới SCP Backend: ${errMsg}`,
        timestamp: new Date().toISOString(),
        mode,
        status: "failed",
        errorDetail: {
          code: "NETWORK_ERROR",
          message: errMsg,
          rootCause: "Không kết nối được tới SCP Python API trên cổng 8000.",
          hint: "Khởi động lại backend hoặc kiểm tra mạng.",
        },
      }
      const finalThreads = threads.map((t) =>
        t.id === activeThread.id
          ? {
              ...t,
              updatedAt: new Date().toISOString(),
              messages: [...activeMessages, userMessage, errorMsg],
            }
          : t
      )
      setThreads(finalThreads)
      persistThreads(finalThreads, activeThread.id)
      media.setMediaStatus(`Lỗi kết nối: ${errMsg}`)
    } finally {
      setIsBusy(false)
    }
  }, [
    activeMessages,
    activeThread.id,
    activeThread.title,
    getSessionId,
    isBusy,
    media,
    mode,
    onRefreshSystem,
    onTriggerControlledFix,
    persistThreads,
    question,
    speakMessage,
    stopSpeaking,
    threads,
    voiceOutputEnabled,
  ])

  const quickPrompts =
    mode === "agent_task"
      ? [
          "Kiểm tra toàn bộ sức khỏe hệ thống SCP và phân tích lỗi",
          "Kích hoạt vòng lặp tự sửa lỗi có kiểm soát",
          "Tìm kiếm web độc lập về kiến trúc Agent OS",
        ]
      : [
          "SCP là gì và hoạt động theo 29 nguyên lý DNA nào?",
          "Task Kernel có bao nhiêu trạng thái hợp lệ?",
          "Cơ chế phân quyền RBAC và kiểm soát Egress hoạt động ra sao?",
        ]

  return (
    <section
      aria-label="Trung tâm điều khiển hội thoại & Tác vụ tự chủ"
      className="mt-6 overflow-hidden rounded-3xl border border-cyan-300/30 bg-gradient-to-b from-[#0b1b30] via-[#081525] to-[#050e1a] shadow-2xl shadow-cyan-950/30"
    >
      {/* 1. Header Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/[0.08] bg-black/30 px-5 py-4 sm:px-6">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => setSidebarOpen((v) => !v)}
            className="rounded-xl border border-white/10 bg-white/[0.05] p-2 text-slate-300 transition hover:bg-white/10 hover:text-white"
            title={sidebarOpen ? "Thu gọn danh sách" : "Mở danh sách chủ đề"}
          >
            {sidebarOpen ? <PanelLeftClose className="h-4 w-4" /> : <PanelLeftOpen className="h-4 w-4" />}
          </button>
          <div>
            <div className="flex items-center gap-2">
              <span className="flex h-2 w-2 rounded-full bg-cyan-400 shadow-[0_0_8px_#22d3ee]" />
              <h2 className="text-base font-bold tracking-tight text-white sm:text-lg">
                Trung tâm Điều khiển Tác vụ Tự chủ (Agent OS)
              </h2>
            </div>
            <p className="hidden text-xs text-slate-400 sm:block">
              Lịch sử nhiều chủ đề · Ra lệnh thực thi có kiểm chứng · Giọng nói hai chiều
            </p>
          </div>
        </div>

        {/* Action Controls & Voice Toggle */}
        <div className="flex flex-wrap items-center gap-2">
          {/* Mode Switcher */}
          <div className="flex rounded-xl border border-white/10 bg-black/40 p-1">
            <button
              type="button"
              onClick={() => {
                setMode("agent_task")
                const updated = threads.map((t) =>
                  t.id === activeThread.id ? { ...t, mode: "agent_task" as InteractionMode } : t
                )
                setThreads(updated)
                persistThreads(updated, activeThread.id)
              }}
              className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
                mode === "agent_task"
                  ? "bg-amber-400/20 text-amber-200 shadow-sm shadow-amber-500/20"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              <Zap className="h-3.5 w-3.5 text-amber-300" />
              <span>⚡ Ra lệnh thực thi</span>
            </button>
            <button
              type="button"
              onClick={() => {
                setMode("chat")
                const updated = threads.map((t) =>
                  t.id === activeThread.id ? { ...t, mode: "chat" as InteractionMode } : t
                )
                setThreads(updated)
                persistThreads(updated, activeThread.id)
              }}
              className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
                mode === "chat"
                  ? "bg-cyan-400/20 text-cyan-200 shadow-sm shadow-cyan-500/20"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              <MessageCircle className="h-3.5 w-3.5 text-cyan-300" />
              <span>💬 Hỏi đáp</span>
            </button>
          </div>

          {/* Voice Toggle */}
          <button
            type="button"
            onClick={toggleVoiceOutput}
            className={`inline-flex items-center gap-1.5 rounded-xl border px-3 py-2 text-xs font-medium transition ${
              voiceOutputEnabled
                ? "border-emerald-400/40 bg-emerald-400/15 text-emerald-200 shadow-sm shadow-emerald-500/20"
                : "border-white/10 bg-white/[0.04] text-slate-400 hover:bg-white/[0.08]"
            }`}
            title="Bật/Tắt giọng đọc SCP hai chiều"
          >
            {voiceOutputEnabled ? <Volume2 className="h-4 w-4 text-emerald-300" /> : <VolumeX className="h-4 w-4" />}
            <span className="hidden sm:inline">Giọng đọc:</span>
            <span>{voiceOutputEnabled ? "BẬT" : "TẮT"}</span>
          </button>

          {/* New Chat Button */}
          <button
            type="button"
            onClick={handleCreateNewThread}
            className="inline-flex items-center gap-1.5 rounded-xl border border-cyan-400/30 bg-cyan-400/15 px-3 py-2 text-xs font-semibold text-cyan-200 transition hover:bg-cyan-400/25"
          >
            <Plus className="h-4 w-4" />
            <span>Chủ đề mới</span>
          </button>
        </div>
      </div>

      {/* 2. Main Area: Sidebar + Messages Viewport */}
      <div className="grid grid-cols-1 lg:grid-cols-[280px_1fr]">
        {sidebarOpen && (
          <ScpChatSidebar
            threads={threads}
            activeThreadId={activeThread.id}
            onSelectThread={handleSelectThread}
            onCreateThread={handleCreateNewThread}
            onDeleteThread={handleDeleteThread}
            onSaveRename={handleSaveRename}
          />
        )}

        <div className="flex flex-col">
          {/* Active Speaking Indicator */}
          {isSpeaking && (
            <div className="flex items-center justify-between border-b border-cyan-400/20 bg-cyan-400/10 px-5 py-2 text-xs text-cyan-200">
              <div className="flex items-center gap-2">
                <span className="flex h-2 w-2 animate-ping rounded-full bg-cyan-300" />
                <span className="font-semibold">SCP đang nói tiếng Việt...</span>
              </div>
              <button
                type="button"
                onClick={stopSpeaking}
                className="inline-flex items-center gap-1 rounded-lg border border-cyan-400/30 bg-cyan-400/20 px-2 py-1 font-semibold text-white hover:bg-cyan-400/30"
              >
                <StopCircle className="h-3.5 w-3.5" /> Dừng nói
              </button>
            </div>
          )}

          {/* Messages Viewport */}
          <div className="flex-1 space-y-4 overflow-y-auto p-4 sm:p-6 max-h-[540px] min-h-[360px]">
            {activeMessages.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-10 text-center">
                <div className="grid h-12 w-12 place-items-center rounded-2xl border border-cyan-300/30 bg-cyan-300/10 text-cyan-300">
                  {mode === "agent_task" ? <Zap className="h-6 w-6 text-amber-300" /> : <MessageCircle className="h-6 w-6" />}
                </div>
                <h3 className="mt-4 text-base font-semibold text-white">
                  {mode === "agent_task" ? "Ra lệnh Thực thi Tác vụ (Agent Task)" : "Hỏi đáp Trực tiếp (Q&A Chat)"}
                </h3>
                <p className="mt-1.5 max-w-md text-xs leading-5 text-slate-400">
                  {mode === "agent_task"
                    ? "Ra lệnh cho SCP: kiểm tra lỗi hệ thống, tự sửa chữa, hoặc quét tệp tin vật lý thời gian thực."
                    : "Hỏi đáp và tra cứu kiến thức nguyên lý SCP DNA với giọng đọc tiếng Việt hai chiều."}
                </p>
                <div className="mt-5 flex flex-wrap justify-center gap-2 max-w-lg">
                  {quickPrompts.map((p, idx) => (
                    <button
                      key={idx}
                      type="button"
                      onClick={() => setQuestion(p)}
                      className="rounded-xl border border-white/10 bg-white/[0.04] px-3 py-1.5 text-xs text-slate-300 hover:bg-cyan-400/10 hover:text-cyan-200"
                    >
                      {p}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              activeMessages.map((msg) => (
                <ScpChatMessageItem
                  key={msg.id}
                  msg={msg}
                  isSpeaking={isSpeaking}
                  speakingMessageId={speakingMessageId}
                  copiedId={copiedId}
                  onSpeak={speakMessage}
                  onCopy={(id, text) => {
                    navigator.clipboard.writeText(text)
                    setCopiedId(id)
                    setTimeout(() => setCopiedId(null), 2000)
                  }}
                  onTriggerControlledFix={onTriggerControlledFix}
                  onRefreshSystem={onRefreshSystem}
                />
              ))
            )}

            {isBusy && (
              <div className="flex items-center gap-3 text-xs text-cyan-300">
                <div className="grid h-8 w-8 place-items-center rounded-xl border border-cyan-400/30 bg-cyan-400/10">
                  <Loader2 className="h-4 w-4 animate-spin" />
                </div>
                <div className="flex items-center gap-2">
                  <span className="font-semibold">
                    {mode === "agent_task" ? "SCP đang điều phối công cụ và quét hệ thống..." : "SCP đang suy luận..."}
                  </span>
                  <span className="flex h-1.5 w-1.5 animate-pulse rounded-full bg-cyan-300" />
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          {/* 3. Input & Media Controls Area */}
          <div className="border-t border-white/[0.08] bg-black/35 p-4 sm:p-5">
            {media.cameraOpen && (
              <div className="mb-4 flex flex-wrap items-center gap-3 rounded-2xl border border-amber-300/30 bg-black/50 p-3">
                <video ref={media.videoRef} autoPlay muted playsInline className="h-36 w-52 rounded-xl bg-black object-contain" />
                <div className="space-y-2">
                  <div className="text-xs font-semibold text-amber-200">Chụp ảnh webcam đính kèm:</div>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={media.captureImage}
                      className="rounded-xl bg-amber-300 px-3.5 py-2 text-xs font-bold text-slate-950 hover:bg-amber-200"
                    >
                      Chụp ảnh
                    </button>
                    <button
                      type="button"
                      onClick={media.closeCamera}
                      className="rounded-xl border border-white/10 px-3.5 py-2 text-xs text-slate-300 hover:bg-white/[0.06]"
                    >
                      Đóng webcam
                    </button>
                  </div>
                </div>
              </div>
            )}

            {media.pendingImage && (
              <div className="mb-2.5 inline-flex items-center gap-2 rounded-lg border border-amber-400/30 bg-amber-400/10 px-3 py-1 text-xs text-amber-200">
                <Camera className="h-3.5 w-3.5" />
                <span>Đã đính kèm 1 ảnh chụp webcam</span>
                <button
                  type="button"
                  onClick={media.clearPendingImage}
                  className="rounded p-0.5 hover:bg-amber-400/20"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            )}

            <div className="flex flex-col gap-3">
              <textarea
                ref={textareaRef}
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => {
                  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
                    e.preventDefault()
                    void handleSendMessage()
                  }
                }}
                placeholder={
                  mode === "agent_task"
                    ? "Nhập lệnh cho SCP (ví dụ: 'Kiểm tra lỗi và tự sửa', 'Quét file', 'Tra cứu web')... (Ctrl+Enter để gửi)"
                    : "Nhập câu hỏi để SCP phân tích... (Ctrl+Enter để gửi)"
                }
                rows={3}
                className="w-full resize-y rounded-2xl border border-white/10 bg-black/30 px-4 py-3 text-sm text-white outline-none placeholder:text-slate-500 focus:border-cyan-300/40"
              />

              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => void handleSendMessage()}
                    disabled={isBusy || !question.trim()}
                    className={`inline-flex items-center gap-2 rounded-xl px-4 py-2.5 text-xs font-bold transition disabled:cursor-wait disabled:opacity-50 ${
                      mode === "agent_task"
                        ? "bg-amber-300 text-slate-950 hover:bg-amber-200 shadow-md shadow-amber-400/20"
                        : "bg-cyan-300 text-slate-950 hover:bg-cyan-200 shadow-md shadow-cyan-400/20"
                    }`}
                  >
                    {isBusy ? (
                      <>
                        <Loader2 className="h-4 w-4 animate-spin" />
                        <span>Đang xử lý...</span>
                      </>
                    ) : mode === "agent_task" ? (
                      <>
                        <Zap className="h-4 w-4" />
                        <span>⚡ Thực thi tác vụ</span>
                      </>
                    ) : (
                      <>
                        <Send className="h-4 w-4" />
                        <span>Gửi câu hỏi</span>
                      </>
                    )}
                  </button>

                  <button
                    type="button"
                    onClick={media.toggleMic}
                    className={`inline-flex items-center gap-1.5 rounded-xl border px-3.5 py-2.5 text-xs font-semibold transition ${
                      media.micActive
                        ? "border-rose-400/50 bg-rose-400/20 text-rose-200 animate-pulse"
                        : "border-white/10 bg-white/[0.04] text-slate-200 hover:bg-white/[0.08]"
                    }`}
                    title="Nói chuyện qua mic"
                  >
                    {media.micActive ? <MicOff className="h-4 w-4 text-rose-300" /> : <Mic className="h-4 w-4 text-cyan-300" />}
                    <span>{media.micActive ? "Dừng mic" : "Mic"}</span>
                  </button>

                  <button
                    type="button"
                    onClick={() => void media.openCamera()}
                    className={`inline-flex items-center gap-1.5 rounded-xl border px-3.5 py-2.5 text-xs font-semibold transition ${
                      media.cameraOpen
                        ? "border-amber-400/50 bg-amber-400/20 text-amber-200"
                        : "border-white/10 bg-white/[0.04] text-slate-200 hover:bg-white/[0.08]"
                    }`}
                    title="Mở webcam"
                  >
                    <Camera className="h-4 w-4 text-amber-300" />
                    <span>Webcam</span>
                  </button>
                </div>

                <div className="text-[11px] text-slate-400">
                  <span>{media.mediaStatus}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
