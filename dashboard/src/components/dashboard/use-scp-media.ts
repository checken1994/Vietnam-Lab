"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import {
  JsonRecord,
  SpeechRecognitionConstructor,
  SpeechRecognitionLike,
  firstValue,
} from "./scp-chat-types"

interface UseScpMediaReturn {
  micActive: boolean
  cameraOpen: boolean
  mediaStatus: string
  pendingImage: string | null
  videoRef: React.RefObject<HTMLVideoElement | null>
  clearPendingImage: () => void
  openCamera: () => Promise<void>
  closeCamera: () => void
  captureImage: () => void
  toggleMic: () => Promise<void>
  setMediaStatus: (status: string) => void
}

export function useScpMedia(onTranscript: (text: string) => void): UseScpMediaReturn {
  const [mediaStatus, setMediaStatus] = useState("Mic và camera chỉ hoạt động sau khi bạn bấm nút.")
  const [micActive, setMicActive] = useState(false)
  const [cameraOpen, setCameraOpen] = useState(false)
  const [pendingImage, setPendingImage] = useState<string | null>(null)

  const videoRef = useRef<HTMLVideoElement | null>(null)
  const cameraStreamRef = useRef<MediaStream | null>(null)
  const micStreamRef = useRef<MediaStream | null>(null)
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const recorderChunksRef = useRef<Blob[]>([])

  const clearPendingImage = useCallback(() => {
    setPendingImage(null)
  }, [])

  const closeCamera = useCallback(() => {
    cameraStreamRef.current?.getTracks().forEach((track) => track.stop())
    cameraStreamRef.current = null
    if (videoRef.current) videoRef.current.srcObject = null
    setCameraOpen(false)
  }, [])

  const openCamera = useCallback(async () => {
    if (!navigator.mediaDevices?.getUserMedia) {
      setMediaStatus("Trình duyệt chưa hỗ trợ webcam.")
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false })
      cameraStreamRef.current = stream
      setCameraOpen(true)
      setMediaStatus("Webcam đang mở. Bấm 'Chụp ảnh' để đính kèm.")
      window.setTimeout(() => {
        if (videoRef.current) videoRef.current.srcObject = stream
      }, 50)
    } catch (error) {
      setMediaStatus(`Không mở được webcam: ${error instanceof Error ? error.message : "từ chối"}`)
    }
  }, [])

  const captureImage = useCallback(() => {
    const video = videoRef.current
    if (!video || !cameraStreamRef.current) return
    const canvas = document.createElement("canvas")
    canvas.width = video.videoWidth || 640
    canvas.height = video.videoHeight || 480
    canvas.getContext("2d")?.drawImage(video, 0, 0, canvas.width, canvas.height)
    const dataUrl = canvas.toDataURL("image/jpeg", 0.82)
    setPendingImage(dataUrl)
    setMediaStatus("Đã chụp ảnh. Ảnh sẽ được gửi kèm yêu cầu.")
    closeCamera()
  }, [closeCamera])

  const transcribeMicBlob = useCallback(async (blob: Blob) => {
    setMediaStatus("Đang nhận diện giọng nói...")
    try {
      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader()
        reader.onloadend = () => resolve(String(reader.result || ""))
        reader.onerror = () => reject(new Error("Lỗi đọc audio"))
        reader.readAsDataURL(blob)
      })
      const audioBase64 = dataUrl.split(",", 2)[1] || ""
      const response = await fetch("/api/scp/voice", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ audio_base64: audioBase64 }),
        cache: "no-store",
      })
      const data = (await response.json().catch(() => ({}))) as JsonRecord
      const transcript = firstValue(data, ["text_extracted", "transcript", "text"], "")
      if (transcript) {
        onTranscript(transcript)
        setMediaStatus("Đã nhận giọng nói. Bạn có thể bấm gửi ngay!")
      } else {
        setMediaStatus(firstValue(data, ["error"], "Không nhận diện được giọng nói"))
      }
    } catch (error) {
      setMediaStatus(`Lỗi mic: ${error instanceof Error ? error.message : "không rõ"}`)
    }
  }, [onTranscript])

  const toggleMic = useCallback(async () => {
    if (recognitionRef.current) {
      recognitionRef.current.stop()
      recognitionRef.current = null
      setMicActive(false)
      setMediaStatus("Đã dừng mic.")
      return
    }
    if (recorderRef.current) {
      recorderRef.current.stop()
      setMediaStatus("Đang xử lý âm thanh...")
      return
    }

    const speechWindow = window as Window & {
      SpeechRecognition?: SpeechRecognitionConstructor
      webkitSpeechRecognition?: SpeechRecognitionConstructor
    }
    const SpeechRecognition = speechWindow.SpeechRecognition || speechWindow.webkitSpeechRecognition

    if (SpeechRecognition) {
      const recognition = new SpeechRecognition()
      recognition.lang = "vi-VN"
      recognition.interimResults = false
      recognition.continuous = false
      recognition.onstart = () => {
        setMicActive(true)
        setMediaStatus("Đang nghe bạn nói tiếng Việt... Bấm lại để dừng.")
      }
      recognition.onresult = (event) => {
        const transcript = event.results?.[0]?.[0]?.transcript
        if (transcript) {
          onTranscript(transcript)
          setMediaStatus("Đã nhận giọng nói! Kiểm tra nội dung rồi gửi.")
        }
      }
      recognition.onerror = () => {
        setMicActive(false)
        recognitionRef.current = null
      }
      recognition.onend = () => {
        recognitionRef.current = null
        setMicActive(false)
      }
      recognitionRef.current = recognition
      try {
        recognition.start()
        return
      } catch {
        recognitionRef.current = null
        setMicActive(false)
      }
    }

    if (navigator.mediaDevices?.getUserMedia && typeof MediaRecorder !== "undefined") {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false })
        const supported = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg"].find((t) =>
          MediaRecorder.isTypeSupported(t)
        )
        const recorder = supported ? new MediaRecorder(stream, { mimeType: supported }) : new MediaRecorder(stream)
        micStreamRef.current = stream
        recorderRef.current = recorder
        recorderChunksRef.current = []
        recorder.ondataavailable = (e) => {
          if (e.data.size > 0) recorderChunksRef.current.push(e.data)
        }
        recorder.onstop = () => {
          const audio = new Blob(recorderChunksRef.current, { type: recorder.mimeType || "audio/webm" })
          recorderRef.current = null
          micStreamRef.current?.getTracks().forEach((track) => track.stop())
          micStreamRef.current = null
          recorderChunksRef.current = []
          setMicActive(false)
          void transcribeMicBlob(audio)
        }
        recorder.start()
        setMicActive(true)
        setMediaStatus("Đang ghi âm... Bấm lại để dừng.")
        return
      } catch {
        setMediaStatus("Không thể mở mic.")
        return
      }
    }
    setMediaStatus("Trình duyệt chưa hỗ trợ ghi âm.")
  }, [onTranscript, transcribeMicBlob])

  useEffect(() => {
    return () => {
      recognitionRef.current?.stop()
      recorderRef.current?.stop()
      micStreamRef.current?.getTracks().forEach((track) => track.stop())
      cameraStreamRef.current?.getTracks().forEach((track) => track.stop())
    }
  }, [])

  return {
    micActive,
    cameraOpen,
    mediaStatus,
    pendingImage,
    videoRef,
    clearPendingImage,
    openCamera,
    closeCamera,
    captureImage,
    toggleMic,
    setMediaStatus,
  }
}
