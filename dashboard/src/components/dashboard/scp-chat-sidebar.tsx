"use client"

import React, { useState } from "react"
import {
  Clock,
  Edit2,
  MessageCircle,
  Plus,
  Search,
  Trash2,
  Zap,
} from "lucide-react"
import { ChatThread } from "./scp-chat-types"

interface ScpChatSidebarProps {
  threads: ChatThread[]
  activeThreadId: string
  onSelectThread: (threadId: string) => void
  onCreateThread: () => void
  onDeleteThread: (threadId: string, e: React.MouseEvent) => void
  onSaveRename: (threadId: string, newTitle: string) => void
}

export function ScpChatSidebar({
  threads,
  activeThreadId,
  onSelectThread,
  onCreateThread,
  onDeleteThread,
  onSaveRename,
}: ScpChatSidebarProps) {
  const [searchTerm, setSearchTerm] = useState("")
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editingTitle, setEditingTitle] = useState("")

  const filteredThreads = threads.filter((t) =>
    t.title.toLowerCase().includes(searchTerm.toLowerCase())
  )

  const handleStartRename = (thread: ChatThread, e: React.MouseEvent) => {
    e.stopPropagation()
    setEditingId(thread.id)
    setEditingTitle(thread.title)
  }

  const handleCommitRename = (threadId: string) => {
    if (editingTitle.trim()) {
      onSaveRename(threadId, editingTitle.trim())
    }
    setEditingId(null)
  }

  return (
    <aside className="border-b border-white/[0.08] bg-black/30 p-3 sm:p-4 lg:border-b-0 lg:border-r">
      {/* Top Header & New Thread */}
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-slate-400">
          <Clock className="h-3.5 w-3.5 text-cyan-300" />
          <span>Lịch sử ({threads.length})</span>
        </div>
        <button
          type="button"
          onClick={onCreateThread}
          className="inline-flex items-center gap-1 rounded-lg border border-cyan-400/30 bg-cyan-400/10 px-2.5 py-1 text-xs font-semibold text-cyan-200 transition hover:bg-cyan-400/20"
          title="Tạo cuộc trò chuyện mới"
        >
          <Plus className="h-3.5 w-3.5" />
          <span>Mới</span>
        </button>
      </div>

      {/* Search Input if threads > 3 */}
      {threads.length > 3 && (
        <div className="relative mb-3">
          <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-slate-500" />
          <input
            type="text"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder="Tìm chủ đề..."
            className="w-full rounded-xl border border-white/10 bg-black/40 py-1.5 pl-8 pr-3 text-xs text-white outline-none placeholder:text-slate-500 focus:border-cyan-400/40"
          />
        </div>
      )}

      {/* Thread List */}
      <div className="max-h-[200px] space-y-1.5 overflow-y-auto pr-1 lg:max-h-[580px]">
        {filteredThreads.length === 0 ? (
          <div className="py-4 text-center text-xs text-slate-500">
            {searchTerm ? "Không tìm thấy chủ đề phù hợp" : "Chưa có chủ đề nào"}
          </div>
        ) : (
          filteredThreads.map((thread) => {
            const isActive = thread.id === activeThreadId
            const isEditing = editingId === thread.id

            return (
              <div
                key={thread.id}
                onClick={() => onSelectThread(thread.id)}
                className={`group relative flex cursor-pointer items-center justify-between rounded-xl px-3 py-2 text-xs transition ${
                  isActive
                    ? "border border-cyan-400/40 bg-cyan-400/15 text-cyan-100 shadow-sm"
                    : "border border-transparent bg-white/[0.025] text-slate-300 hover:border-white/10 hover:bg-white/[0.06] hover:text-white"
                }`}
              >
                <div className="min-w-0 flex-1 pr-2">
                  {isEditing ? (
                    <input
                      type="text"
                      value={editingTitle}
                      onChange={(e) => setEditingTitle(e.target.value)}
                      onBlur={() => handleCommitRename(thread.id)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") handleCommitRename(thread.id)
                        if (e.key === "Escape") setEditingId(null)
                      }}
                      autoFocus
                      className="w-full rounded border border-cyan-300/50 bg-black/80 px-1.5 py-0.5 text-xs text-white outline-none"
                    />
                  ) : (
                    <>
                      <div className="flex items-center gap-1.5 font-medium">
                        {thread.mode === "agent_task" ? (
                          <Zap className="h-3 w-3 shrink-0 text-amber-300" />
                        ) : (
                          <MessageCircle className="h-3 w-3 shrink-0 text-cyan-300" />
                        )}
                        <span className="truncate">{thread.title}</span>
                      </div>
                      <div className="mt-0.5 flex items-center gap-1 text-[10px] text-slate-500">
                        <span>{thread.messages?.length ?? 0} tin nhắn</span>
                        <span>·</span>
                        <span>
                          {new Date(thread.updatedAt).toLocaleTimeString("vi-VN", {
                            hour: "2-digit",
                            minute: "2-digit",
                          })}
                        </span>
                      </div>
                    </>
                  )}
                </div>

                {!isEditing && (
                  <div className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                    <button
                      type="button"
                      onClick={(e) => handleStartRename(thread, e)}
                      className="rounded p-1 text-slate-400 hover:bg-white/10 hover:text-cyan-200"
                      title="Đổi tên chủ đề"
                    >
                      <Edit2 className="h-3 w-3" />
                    </button>
                    <button
                      type="button"
                      onClick={(e) => onDeleteThread(thread.id, e)}
                      className="rounded p-1 text-slate-400 hover:bg-rose-500/20 hover:text-rose-300"
                      title="Xóa chủ đề này"
                    >
                      <Trash2 className="h-3 w-3" />
                    </button>
                  </div>
                )}
              </div>
            )
          })
        )}
      </div>
    </aside>
  )
}
