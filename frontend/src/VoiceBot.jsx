import React, { useState, useRef, useEffect, useCallback } from 'react'

// ---- Helpers ----------------------------------------------------------------

function formatTime(seconds) {
  const m = Math.floor(seconds / 60).toString().padStart(2, '0')
  const s = (seconds % 60).toString().padStart(2, '0')
  return `${m}:${s}`
}

function nowISO() {
  return new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

// Convert base64 to ArrayBuffer for Web Audio API
function base64ToArrayBuffer(b64) {
  const binary = atob(b64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
  return bytes.buffer
}

// ---- Status dot -------------------------------------------------------------

function StatusDot({ status }) {
  const config = {
    idle:       { color: 'bg-gray-400',   label: 'Idle',       pulse: false },
    active:     { color: 'bg-green-400',  label: 'Active',     pulse: false },
    recording:  { color: 'bg-red-500',    label: 'Recording',  pulse: true  },
    processing: { color: 'bg-yellow-400', label: 'Processing', pulse: false },
  }
  const { color, label, pulse } = config[status] || config.idle
  return (
    <div className="flex items-center gap-2">
      <span className={`inline-block w-3 h-3 rounded-full ${color} ${pulse ? 'animate-pulse' : ''}`} />
      <span className="text-gray-300 text-sm font-medium">{label}</span>
    </div>
  )
}

// ---- Main component ---------------------------------------------------------

export default function VoiceBot() {
  // State
  const [callId, setCallId]           = useState(null)
  const [isCallActive, setIsCallActive] = useState(false)
  const [isRecording, setIsRecording] = useState(false)
  const [isProcessing, setIsProcessing] = useState(false)
  const [transcription, setTranscription] = useState('')
  const [botResponse, setBotResponse] = useState('')
  const [conversation, setConversation] = useState([])
  const [language, setLanguage]       = useState('hi')
  const [escalated, setEscalated]     = useState(false)
  const [ticketMessage, setTicketMessage] = useState('')
  const [callDuration, setCallDuration] = useState(0)
  const [error, setError]             = useState('')
  const [statusMsg, setStatusMsg]     = useState('Click "Start Call" to begin')

  // Refs (not causing re-renders)
  const audioChunksRef    = useRef([])
  const mediaRecorderRef  = useRef(null)
  const callTimerRef      = useRef(null)
  const conversationEndRef = useRef(null)
  const errorTimerRef     = useRef(null)

  // Auto-scroll conversation
  useEffect(() => {
    conversationEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [conversation])

  // Auto-dismiss errors after 6 seconds
  useEffect(() => {
    if (error) {
      clearTimeout(errorTimerRef.current)
      errorTimerRef.current = setTimeout(() => setError(''), 6000)
    }
  }, [error])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      clearInterval(callTimerRef.current)
      clearTimeout(errorTimerRef.current)
    }
  }, [])

  // Derived status for UI
  const uiStatus = isRecording ? 'recording' : isProcessing ? 'processing' : isCallActive ? 'active' : 'idle'

  // ---- API calls ------------------------------------------------------------

  const startCall = useCallback(async () => {
    setError('')
    setEscalated(false)
    setTicketMessage('')
    setConversation([])
    setTranscription('')
    setBotResponse('')
    setCallDuration(0)

    try {
      setStatusMsg('Connecting...')
      const res = await fetch('/voice/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ language, customer_name: '', customer_phone: '' }),
      })
      if (!res.ok) throw new Error(`Server error ${res.status}`)
      const data = await res.json()

      setCallId(data.call_id)
      setIsCallActive(true)
      setStatusMsg('Call active — click the mic to speak')

      // Start timer
      clearInterval(callTimerRef.current)
      let elapsed = 0
      callTimerRef.current = setInterval(() => {
        elapsed += 1
        setCallDuration(elapsed)
      }, 1000)
    } catch (err) {
      setError(`Failed to start call: ${err.message}`)
      setStatusMsg('Click "Start Call" to begin')
    }
  }, [language])

  const startRecording = useCallback(async () => {
    if (!callId) {
      setError('Please start a call first')
      return
    }
    if (isRecording || isProcessing) return

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { sampleRate: 16000, channelCount: 1, echoCancellation: true },
      })

      audioChunksRef.current = []

      // Pick best supported MIME type
      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : MediaRecorder.isTypeSupported('audio/webm')
        ? 'audio/webm'
        : ''

      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : {})
      mediaRecorderRef.current = recorder

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) audioChunksRef.current.push(e.data)
      }

      recorder.onstop = () => {
        // Stop microphone tracks
        stream.getTracks().forEach((t) => t.stop())
        processAudio()
      }

      recorder.start(100) // Collect chunks every 100ms
      setIsRecording(true)
      setStatusMsg('Recording... click mic again to stop')
    } catch (err) {
      setError(`Microphone error: ${err.message}. Please allow microphone access.`)
    }
  }, [callId, isRecording, isProcessing])

  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current && isRecording) {
      mediaRecorderRef.current.stop()
      setIsRecording(false)
      setIsProcessing(true)
      setStatusMsg('Processing your voice...')
    }
  }, [isRecording])

  const processAudio = useCallback(async () => {
    const chunks = audioChunksRef.current
    if (!chunks.length) {
      setIsProcessing(false)
      setStatusMsg('No audio captured — try again')
      return
    }

    // Combine chunks into a single Blob
    const blob = new Blob(chunks, { type: chunks[0]?.type || 'audio/webm' })

    // Convert Blob → base64
    const reader = new FileReader()
    reader.readAsDataURL(blob)
    reader.onloadend = async () => {
      // Strip the "data:audio/webm;base64," prefix
      const b64 = reader.result.split(',')[1]

      try {
        const res = await fetch('/voice/transcribe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            audio_base64: b64,
            call_id: callId,
            language,
          }),
        })

        if (!res.ok) {
          const errBody = await res.json().catch(() => ({}))
          throw new Error(errBody.detail || `Server error ${res.status}`)
        }

        const data = await res.json()

        setTranscription(data.transcription)
        setBotResponse(data.response)

        const ts = nowISO()
        setConversation((prev) => [
          ...prev,
          { role: 'user', text: data.transcription, timestamp: ts },
          { role: 'bot',  text: data.response,      timestamp: ts },
        ])

        if (data.audio_base64) {
          await playAudio(data.audio_base64)
        }

        if (data.escalate) {
          setEscalated(true)
          setTicketMessage('Your issue has been escalated. Our agent will contact you within 2 hours.')
        }

        setStatusMsg('Click the mic to speak again')
      } catch (err) {
        setError(`Processing failed: ${err.message}`)
        setStatusMsg('Error — please try again')
      } finally {
        setIsProcessing(false)
        audioChunksRef.current = []
      }
    }
  }, [callId, language])

  const playAudio = useCallback(async (b64Audio) => {
    try {
      const arrayBuffer = base64ToArrayBuffer(b64Audio)
      // AudioContext needs user gesture — already satisfied by mic button click
      const ctx = new (window.AudioContext || window.webkitAudioContext)()
      const decoded = await ctx.decodeAudioData(arrayBuffer)
      const source = ctx.createBufferSource()
      source.buffer = decoded
      source.connect(ctx.destination)
      source.start(0)
      source.onended = () => ctx.close()
    } catch (err) {
      console.warn('Audio playback error:', err)
      // Non-fatal — user can still read the text response
    }
  }, [])

  const endCall = useCallback(async () => {
    if (!callId) return
    clearInterval(callTimerRef.current)

    try {
      await fetch('/voice/end', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ call_id: callId, duration_seconds: callDuration }),
      })
    } catch (err) {
      console.warn('End call API error:', err)
    }

    // Reset all state
    setCallId(null)
    setIsCallActive(false)
    setIsRecording(false)
    setIsProcessing(false)
    setTranscription('')
    setBotResponse('')
    setEscalated(false)
    setTicketMessage('')
    setCallDuration(0)
    setStatusMsg('Call ended — click "Start Call" to begin a new call')
  }, [callId, callDuration])

  // ---- Mic button handler ---------------------------------------------------
  const handleMicClick = () => {
    if (isProcessing) return
    if (isRecording) stopRecording()
    else startRecording()
  }

  // ---- Mic button styling ---------------------------------------------------
  const micButtonClass = [
    'relative w-20 h-20 rounded-full flex items-center justify-center',
    'text-white text-3xl transition-all duration-200 focus:outline-none',
    'shadow-lg',
    isProcessing
      ? 'bg-yellow-500 cursor-not-allowed opacity-70'
      : isRecording
      ? 'bg-red-600 hover:bg-red-700 cursor-pointer'
      : isCallActive
      ? 'bg-gray-600 hover:bg-gray-500 cursor-pointer'
      : 'bg-gray-700 cursor-not-allowed opacity-50',
  ].join(' ')

  // ---- Render ---------------------------------------------------------------
  return (
    <div className="bg-gray-800 rounded-2xl shadow-2xl overflow-hidden">

      {/* ── Header ─────────────────────────────────────────────────────── */}
      <div className="bg-gray-900 px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold" style={{ color: '#E40000' }}>
            airtel
          </h1>
          <p className="text-gray-400 text-sm">Customer Support</p>
        </div>
        {/* Language toggle */}
        <div className="flex gap-2">
          {['hi', 'en'].map((lang) => (
            <button
              key={lang}
              onClick={() => !isCallActive && setLanguage(lang)}
              disabled={isCallActive}
              className={[
                'px-4 py-1.5 rounded-full text-sm font-medium transition-colors',
                language === lang
                  ? 'bg-red-600 text-white'
                  : 'bg-gray-700 text-gray-300 hover:bg-gray-600',
                isCallActive ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer',
              ].join(' ')}
            >
              {lang === 'hi' ? 'हिंदी' : 'English'}
            </button>
          ))}
        </div>
      </div>

      {/* ── Error banner ────────────────────────────────────────────────── */}
      {error && (
        <div className="bg-red-900 border-l-4 border-red-500 px-4 py-3 flex items-start gap-2">
          <span className="text-red-400 text-lg mt-0.5">⚠</span>
          <p className="text-red-200 text-sm">{error}</p>
        </div>
      )}

      {/* ── Status bar ──────────────────────────────────────────────────── */}
      <div className="bg-gray-750 border-b border-gray-700 px-6 py-3 flex items-center justify-between">
        <StatusDot status={uiStatus} />
        <div className="flex items-center gap-4">
          {isCallActive && (
            <span className="text-green-400 font-mono text-sm">
              {formatTime(callDuration)}
            </span>
          )}
          {isProcessing && (
            <span className="inline-block w-4 h-4 border-2 border-yellow-400 border-t-transparent rounded-full animate-spin-slow" />
          )}
        </div>
      </div>

      {/* ── Main control area ───────────────────────────────────────────── */}
      <div className="px-6 py-8 flex flex-col items-center gap-6">

        {/* Mic button */}
        <button
          onClick={handleMicClick}
          disabled={!isCallActive || isProcessing}
          className={micButtonClass}
          title={isRecording ? 'Stop recording' : 'Start recording'}
        >
          {isRecording && (
            <span className="pulse-ring" />
          )}
          {isProcessing ? '⏳' : isRecording ? '⏹' : '🎤'}
        </button>

        {/* Instruction text */}
        <p className="text-gray-400 text-sm text-center min-h-5">
          {isProcessing
            ? 'Processing your voice...'
            : isRecording
            ? 'Recording — click to stop'
            : isCallActive
            ? 'Click the mic to speak'
            : statusMsg}
        </p>

        {/* Start / End call buttons */}
        <div className="flex gap-4">
          {!isCallActive ? (
            <button
              onClick={startCall}
              className="bg-green-600 hover:bg-green-700 text-white font-semibold px-8 py-3 rounded-full transition-colors shadow-md"
            >
              📞 Start Call
            </button>
          ) : (
            <button
              onClick={endCall}
              disabled={isRecording}
              className="bg-red-700 hover:bg-red-800 text-white font-semibold px-8 py-3 rounded-full transition-colors shadow-md disabled:opacity-50 disabled:cursor-not-allowed"
            >
              📵 End Call
            </button>
          )}
        </div>
      </div>

      {/* ── Escalation banner ───────────────────────────────────────────── */}
      {escalated && (
        <div className="mx-6 mb-4 bg-yellow-900 border border-yellow-600 rounded-xl px-4 py-3 flex items-start gap-3">
          <span className="text-yellow-400 text-xl">⚠️</span>
          <div>
            <p className="text-yellow-200 font-semibold text-sm">Issue Escalated</p>
            <p className="text-yellow-300 text-sm mt-0.5">{ticketMessage}</p>
          </div>
        </div>
      )}

      {/* ── Live transcription / response preview ───────────────────────── */}
      {(transcription || botResponse) && (
        <div className="mx-6 mb-4 grid grid-cols-2 gap-3 text-xs">
          {transcription && (
            <div className="bg-blue-900 bg-opacity-40 border border-blue-700 rounded-lg px-3 py-2">
              <p className="text-blue-400 font-semibold mb-1">You said</p>
              <p className="text-blue-100">{transcription}</p>
            </div>
          )}
          {botResponse && (
            <div className="bg-gray-700 border border-gray-600 rounded-lg px-3 py-2">
              <p className="text-green-400 font-semibold mb-1">Airtel Bot</p>
              <p className="text-gray-100">{botResponse}</p>
            </div>
          )}
        </div>
      )}

      {/* ── Conversation history ────────────────────────────────────────── */}
      {conversation.length > 0 && (
        <div className="px-6 pb-6">
          <p className="text-gray-500 text-xs font-semibold uppercase tracking-wider mb-3">
            Conversation History
          </p>
          <div className="max-h-64 overflow-y-auto space-y-2 pr-1 scrollbar-thin">
            {conversation.map((turn, idx) => (
              <div
                key={idx}
                className={`flex ${turn.role === 'user' ? 'justify-end' : 'justify-start'}`}
              >
                <div
                  className={[
                    'max-w-xs rounded-2xl px-4 py-2 text-sm shadow',
                    turn.role === 'user'
                      ? 'bg-blue-600 text-white rounded-br-sm'
                      : 'bg-gray-700 text-gray-100 rounded-bl-sm',
                  ].join(' ')}
                >
                  <p className={`text-xs font-semibold mb-1 ${turn.role === 'user' ? 'text-blue-200' : 'text-green-400'}`}>
                    {turn.role === 'user' ? 'You' : 'Airtel Bot'}
                  </p>
                  <p>{turn.text}</p>
                  <p className={`text-xs mt-1 ${turn.role === 'user' ? 'text-blue-200' : 'text-gray-500'}`}>
                    {turn.timestamp}
                  </p>
                </div>
              </div>
            ))}
            <div ref={conversationEndRef} />
          </div>
        </div>
      )}

      {/* ── How to use ──────────────────────────────────────────────────── */}
      {!isCallActive && conversation.length === 0 && (
        <div className="px-6 pb-6">
          <div className="bg-gray-700 bg-opacity-50 rounded-xl p-4 text-xs text-gray-400 space-y-1">
            <p className="font-semibold text-gray-300 mb-2">How to use:</p>
            <p>1. Select language (Hindi / English) above</p>
            <p>2. Click <strong className="text-green-400">Start Call</strong></p>
            <p>3. Click the <strong className="text-red-400">🎤 microphone</strong> and speak your question</p>
            <p>4. Click mic again to stop — the bot will respond</p>
            <p>5. Click <strong className="text-red-400">End Call</strong> when done</p>
          </div>
        </div>
      )}
    </div>
  )
}
