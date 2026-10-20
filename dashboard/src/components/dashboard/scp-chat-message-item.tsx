"use client"

import React from "react"
import {
  Check,
  Copy,
  ShieldCheck,
  StopCircle,
  User,
  Volume2,
  Zap,
} from "lucide-react"
import { ChatMessage } from "./scp-chat-types"
import { ScpExecutionVisualizer } from "./scp-execution-visualizer"

interface ScpChatMessageItemProps {
  msg: ChatMessage
  isSpeaking: boolean
  speakingMessageId: string | null
  copiedId: string | null
  onSpeak: (id: string, text: string) => void
  onCopy: (id: string, text: string) => void
  onTriggerControlledFix?: () => Promise<void>
  onRefreshSystem?: () => Promise<void>
}

export function ScpChatMessageItem({
  msg,
  isSpeaking,
  speakingMessageId,
  copiedId,
  onSpeak,
  onCopy,
  onTriggerControlledFix,
  onRefreshSystem,
}: ScpChatMessageItemProps) {
  const isUser = msg.role === "user"
  const isCurrentSpeaking = isSpeaking && speakingMessageId === msg.id

  return (
    <div className={`flex gap-3 ${isUser ? "justify-end" : "justify-start"}`}>
      {!isUser && (
        <div className="grid h-8 w-8 shrink-0 place-items-center rounded-xl border border-cyan-400/30 bg-cyan-400/15 text-cyan-300">
          {msg.mode === "agent_task" ? (
            <Zap className="h-4 w-4 text-amber-300" />
          ) : (
            <ShieldCheck className="h-4 w-4" />
          )}
        </div>
      )}

      <div className="max-w-[88%] sm:max-w-[80%] space-y-2">
        <div
          className={`rounded-2xl p-4 text-sm leading-6 ${
            isUser
              ? "border border-cyan-400/30 bg-cyan-400/15 text-white"
              : "border border-white/10 bg-black/40 text-slate-200 shadow-xl"
          }`}
        >
          {!isUser && (
            <div className="mb-2 flex items-center justify-between border-b border-white/[0.06] pb-2 text-[11px]">
              <div className="flex items-center gap-2 font-mono">
                <span
                  className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${
                    msg.mode === "agent_task"
                      ? "bg-amber-400/20 text-amber-200"
                      : "bg-cyan-400/20 text-cyan-200"
                  }`}
                >
                  {msg.mode === "agent_task" ? "⚡ Agent Task" : "💬 Q&A"}
                </span>
                {msg.status && (
                  <span
                    className={`rounded px-1.5 py-0.5 text-[10px] font-bold uppercase ${
                      msg.status === "completed"
                        ? "bg-emerald-400/20 text-emerald-300"
                        : msg.status === "failed"
                        ? "bg-rose-400/20 text-rose-300"
                        : "bg-cyan-400/20 text-cyan-300 animate-pulse"
                    }`}
                  >
                    {msg.status}
                  </span>
                )}
                {msg.data?.elapsed_ms != null && (
                  <span className="text-slate-500">· {String(msg.data.elapsed_ms)}ms</span>
                )}
              </div>

              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => onSpeak(msg.id, msg.content)}
                  className="inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-medium bg-white/[0.06] text-slate-300 hover:bg-white/[0.12]"
                >
                  {isCurrentSpeaking ? (
                    <StopCircle className="h-3 w-3 text-rose-400" />
                  ) : (
                    <Volume2 className="h-3 w-3 text-cyan-300" />
                  )}
                  <span>{isCurrentSpeaking ? "Dừng" : "Đọc to"}</span>
                </button>
                <button
                  type="button"
                  onClick={() => onCopy(msg.id, msg.content)}
                  className="rounded-md p-1 text-slate-400 hover:bg-white/[0.08]"
                  title="Sao chép"
                >
                  {copiedId === msg.id ? (
                    <Check className="h-3 w-3 text-emerald-400" />
                  ) : (
                    <Copy className="h-3 w-3" />
                  )}
                </button>
              </div>
            </div>
          )}

          {msg.imageData && (
            <div className="mb-3 overflow-hidden rounded-xl border border-white/10">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={msg.imageData} alt="Snapshot" className="max-h-48 w-auto rounded-lg object-contain" />
            </div>
          )}

          <div className="whitespace-pre-wrap">{msg.content}</div>

          {/* Execution Visualizer for Agent Task or messages with execution data */}
          {(msg.mode === "agent_task" || msg.executionSteps?.length || msg.errorDetail) && (
            <ScpExecutionVisualizer
              message={msg}
              onTriggerControlledFix={onTriggerControlledFix}
              onRefreshSystem={onRefreshSystem}
            />
          )}
        </div>

        <div className={`text-[10px] text-slate-500 ${isUser ? "text-right" : "text-left"}`}>
          {new Date(msg.timestamp).toLocaleTimeString("vi-VN", {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
          })}
        </div>
      </div>

      {isUser && (
        <div className="grid h-8 w-8 shrink-0 place-items-center rounded-xl border border-white/10 bg-white/[0.08] text-slate-300">
          <User className="h-4 w-4" />
        </div>
      )}
    </div>
  )
}
