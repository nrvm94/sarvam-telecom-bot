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
  const [callId, setCallId]               = useState(null)
  const [isCallActive, setIsCallActive]   = useState(false)
  const [isRecording, setIsRecording]     = useState(false)
  const [isProcessing, setIsProcessing]   = useState(false)
  const [transcription, setTranscription] = useState('')
  const [botResponse, setBotResponse]     = useState('')
  const [conversation, setConversation]   = useState([])
  const [escalated, setEscalated]         = useState(false)
  const [ticketMessage, setTicketMessage] = useState('')
  const [callDuration, setCallDuration]   = useState(0)
  const [error, setError]                 = useState('')
  const [statusMsg, setStatusMsg]         = useState('Click "Start Call" to begin')
  const [customerPhone, setCustomerPhone] = useState('9876543210')
  const [customerName, setCustomerName]   = useState('')
  const [customerFound, setCustomerFound] = useState(false)
  const [lookedUpPhone, setLookedUpPhone] = useState('')
  // Language is auto-detected per turn by the backend; always start with hi-IN STT
  const language = 'hi'

  // Refs (not causing re-renders)
  const audioChunksRef    = useRef([])
  const mediaRecorderRef  = useRef(null)
  const callTimerRef      = useRef(null)
  const conversationEndRef = useRef(null)
  const errorTimerRef     = useRef(null)
  // VAD / audio refs
  const audioContextRef   = useRef(null)
  const analyserRef       = useRef(null)
  const vadIntervalRef    = useRef(null)
  const silenceStartRef   = useRef(null)
  // Continuous listening refs
  const playingAudioRef   = useRef(null)   // current bot audio { source, ctx }
  const micStreamRef      = useRef(null)   // persistent mic MediaStream
  const isRecordingRef    = useRef(false)  // recording in progress (ref, not state)
  const isProcessingRef   = useRef(false)  // waiting for API response
  const speechStartRef    = useRef(null)   // voice onset debounce timestamp
  const callIdRef         = useRef(null)   // mirror of callId state for closures
  const languageRef       = useRef('hi')   // mirrors selectedLang; switches per-turn from detected language

  // Sync state → refs
  useEffect(() => { callIdRef.current = callId }, [callId])

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
      clearInterval(vadIntervalRef.current)
      if (audioContextRef.current) audioContextRef.current.close()
      if (micStreamRef.current) micStreamRef.current.getTracks().forEach(t => t.stop())
    }
  }, [])

  // Derived status for UI
  const uiStatus = isRecording ? 'recording' : isProcessing ? 'processing' : isCallActive ? 'active' : 'idle'

  // ---- processAudio — stable (uses refs, no state deps) -------------------

  const processAudio = useCallback(async () => {
    const chunks = audioChunksRef.current
    if (!chunks.length) {
      isProcessingRef.current = false
      setIsProcessing(false)
      setStatusMsg('Listening...')
      return
    }

    const blob = new Blob(chunks, { type: chunks[0]?.type || 'audio/webm' })
    const reader = new FileReader()
    reader.readAsDataURL(blob)
    reader.onloadend = async () => {
      const b64 = reader.result.split(',')[1]
      try {
        const res = await fetch('/voice/transcribe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            audio_base64: b64,
            call_id: callIdRef.current,
            language: languageRef.current,
          }),
        })

        if (!res.ok) {
          const errBody = await res.json().catch(() => ({}))
          throw new Error(errBody.detail || `Server error ${res.status}`)
        }

        const data = await res.json()

        setTranscription(data.transcription)
        setBotResponse(data.response)
        setIsProcessing(false)
        isProcessingRef.current = false
        setStatusMsg('Listening...')
        audioChunksRef.current = []

        // Track detected language so next turn uses correct STT language
        if (data.language) languageRef.current = data.language

        const ts = nowISO()
        setConversation((prev) => [
          ...prev,
          { role: 'user', text: data.transcription, timestamp: ts },
          { role: 'bot',  text: data.response,      timestamp: ts },
        ])

        if (data.audio_base64) {
          playAudio(data.audio_base64)
        }

        if (data.escalate) {
          setEscalated(true)
          const routing = data.routing ? data.routing.replace('_', ' ') : 'support team'
          const ticket = data.ticket_id || ''
          setTicketMessage(
            `Escalated to ${routing}${ticket ? ` | Ticket: ${ticket}` : ''} | Agent will call within 2 hours`
          )
        }
      } catch (err) {
        setError(`Processing failed: ${err.message}`)
        setStatusMsg('Listening...')
        setIsProcessing(false)
        isProcessingRef.current = false
        audioChunksRef.current = []
      }
    }
  }, [])  // stable — callId and language accessed via refs

  // ---- playAudio — play bot response audio --------------------------------

  const playAudio = useCallback(async (b64Audio) => {
    try {
      const arrayBuffer = base64ToArrayBuffer(b64Audio)
      const ctx = new (window.AudioContext || window.webkitAudioContext)()
      const decoded = await ctx.decodeAudioData(arrayBuffer)
      const source = ctx.createBufferSource()
      source.buffer = decoded
      source.connect(ctx.destination)
      playingAudioRef.current = { source, ctx }
      setStatusMsg('Bot is speaking...')
      source.start(0)
      source.onended = () => {
        playingAudioRef.current = null
        ctx.close()
        setStatusMsg('Listening...')
      }
    } catch (err) {
      console.warn('Audio playback error:', err)
      playingAudioRef.current = null
    }
  }, [])

  // ---- startListening — continuous VAD loop --------------------------------

  const startListening = useCallback(async () => {
    // 1. Request mic once
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { sampleRate: 16000, channelCount: 1, echoCancellation: true }
    })
    micStreamRef.current = stream

    // 2. Set up AudioContext + AnalyserNode for VAD
    const vadCtx = new (window.AudioContext || window.webkitAudioContext)()
    const analyser = vadCtx.createAnalyser()
    analyser.fftSize = 512
    vadCtx.createMediaStreamSource(stream).connect(analyser)
    audioContextRef.current = vadCtx
    analyserRef.current = analyser

    const data = new Uint8Array(analyser.frequencyBinCount)
    const SPEECH_THRESHOLD = 5   // RMS > 5 → voice detected
    const SILENCE_THRESHOLD = 3  // RMS < 3 → silence

    // 3. VAD interval — runs every 150ms throughout entire call
    vadIntervalRef.current = setInterval(() => {
      if (!analyserRef.current) return
      analyserRef.current.getByteTimeDomainData(data)
      let sum = 0
      for (let i = 0; i < data.length; i++) {
        const v = (data[i] - 128) / 128
        sum += v * v
      }
      const rms = Math.sqrt(sum / data.length) * 100

      // --- Interrupt bot audio if user speaks ---
      if (playingAudioRef.current && rms > SPEECH_THRESHOLD) {
        if (!speechStartRef.current) {
          speechStartRef.current = Date.now()
        } else if (Date.now() - speechStartRef.current > 200) {
          // Confirmed speech during bot playback → interrupt
          try { playingAudioRef.current.source.stop() } catch (_) {}
          try { playingAudioRef.current.ctx.close() } catch (_) {}
          playingAudioRef.current = null
          speechStartRef.current = null
          setStatusMsg('Listening...')
        }
        return  // VAD will pick up speech on next cycle
      }

      // --- LISTENING state: detect onset of speech ---
      if (!isRecordingRef.current && !isProcessingRef.current && !playingAudioRef.current) {
        if (rms > SPEECH_THRESHOLD) {
          if (!speechStartRef.current) {
            speechStartRef.current = Date.now()
          } else if (Date.now() - speechStartRef.current > 200) {
            // Confirmed speech → start recording
            speechStartRef.current = null
            silenceStartRef.current = null
            audioChunksRef.current = []

            const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
              ? 'audio/webm;codecs=opus'
              : MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : ''
            const recorder = new MediaRecorder(micStreamRef.current, mimeType ? { mimeType } : {})
            mediaRecorderRef.current = recorder
            recorder.ondataavailable = (e) => {
              if (e.data.size > 0) audioChunksRef.current.push(e.data)
            }
            recorder.onstop = () => processAudio()
            recorder.start(100)
            isRecordingRef.current = true
            setIsRecording(true)
            setStatusMsg('Recording...')
          }
        } else {
          speechStartRef.current = null
        }
      }

      // --- RECORDING state: detect end of speech (1.5s silence) ---
      if (isRecordingRef.current) {
        if (rms < SILENCE_THRESHOLD) {
          if (!silenceStartRef.current) {
            silenceStartRef.current = Date.now()
          } else if (Date.now() - silenceStartRef.current > 1500) {
            // Silence confirmed → stop recording
            silenceStartRef.current = null
            isRecordingRef.current = false
            isProcessingRef.current = true
            setIsRecording(false)
            setIsProcessing(true)
            setStatusMsg('Processing...')
            if (mediaRecorderRef.current?.state === 'recording') {
              mediaRecorderRef.current.stop()
            }
          }
        } else {
          silenceStartRef.current = null
        }
      }
    }, 150)
  }, [processAudio])

  // ---- stopListening — tear down mic + VAD --------------------------------

  const stopListening = useCallback(() => {
    clearInterval(vadIntervalRef.current)
    vadIntervalRef.current = null
    analyserRef.current = null
    speechStartRef.current = null
    silenceStartRef.current = null
    if (mediaRecorderRef.current?.state === 'recording') {
      mediaRecorderRef.current.stop()
    }
    if (audioContextRef.current) {
      audioContextRef.current.close()
      audioContextRef.current = null
    }
    if (micStreamRef.current) {
      micStreamRef.current.getTracks().forEach(t => t.stop())
      micStreamRef.current = null
    }
    isRecordingRef.current = false
    isProcessingRef.current = false
    setIsRecording(false)
    setIsProcessing(false)
  }, [])

  // ---- startCall ----------------------------------------------------------

  const startCall = useCallback(async () => {
    setError('')
    setEscalated(false)
    setTicketMessage('')
    setConversation([])
    setTranscription('')
    setBotResponse('')
    setCallDuration(0)
    setCustomerName('')
    setCustomerFound(false)

    try {
      setStatusMsg('Connecting...')
      const res = await fetch('/voice/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          customer_name: '',
          customer_phone: customerPhone,
          language: language,
        }),
      })
      if (!res.ok) throw new Error(`Server error ${res.status}`)
      const data = await res.json()

      setCallId(data.call_id)
      setIsCallActive(true)
      setCustomerName(data.customer_name || '')
      setCustomerFound(!!data.customer_found)
      setLookedUpPhone(data.looked_up_phone || '')

      // Play greeting audio (non-blocking)
      if (data.greeting_audio_base64) {
        playAudio(data.greeting_audio_base64)
      }

      // Start timer
      clearInterval(callTimerRef.current)
      let elapsed = 0
      callTimerRef.current = setInterval(() => {
        elapsed += 1
        setCallDuration(elapsed)
      }, 1000)

      // Start continuous VAD listening
      await startListening()
      setStatusMsg('Listening...')
    } catch (err) {
      setError(`Failed to start call: ${err.message}`)
      setStatusMsg('Click "Start Call" to begin')
    }
  }, [customerPhone, playAudio, startListening])

  // ---- endCall ------------------------------------------------------------

  const endCall = useCallback(async () => {
    if (!callId) return

    // Stop bot audio immediately
    if (playingAudioRef.current) {
      try { playingAudioRef.current.source.stop() } catch (_) {}
      try { playingAudioRef.current.ctx.close() } catch (_) {}
      playingAudioRef.current = null
    }

    // Stop mic + VAD
    stopListening()

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
    languageRef.current = 'hi'
    setConversation([])
    setLookedUpPhone('')
    setCustomerPhone('9876543210')
    setError('')
    setCallId(null)
    setIsCallActive(false)
    setIsRecording(false)
    setIsProcessing(false)
    setTranscription('')
    setBotResponse('')
    setEscalated(false)
    setTicketMessage('')
    setCallDuration(0)
    setCustomerName('')
    setCustomerFound(false)
    setStatusMsg('Call ended — click "Start Call" to begin a new call')
  }, [callId, callDuration, stopListening])

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
        <p className="text-gray-400 text-sm">Hindi · English · मराठी</p>
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

        {/* Status indicator (replaces push-to-talk mic button) */}
        <div className="w-20 h-20 rounded-full flex items-center justify-center bg-gray-700 shadow-lg">
          <span className="text-3xl">
            {isProcessing ? '⏳' : isRecording ? '🎙️' : isCallActive ? '👂' : '🎤'}
          </span>
        </div>

        {/* Instruction / status text */}
        <p className="text-gray-400 text-sm text-center min-h-5">
          {statusMsg}
        </p>

        {/* Pre-call: phone input + language selector. In-call: customer badge */}
        {!isCallActive ? (
          <div className="w-full max-w-xs">
            <input
              type="tel"
              value={customerPhone}
              onChange={(e) => setCustomerPhone(e.target.value.replace(/\D/g, '').slice(0, 10))}
              placeholder="Airtel number (e.g. 9876543210)"
              className="w-full bg-gray-700 border border-gray-600 text-gray-100 placeholder-gray-500 rounded-xl px-4 py-2 text-sm focus:outline-none focus:border-green-500"
            />
            <p className="text-xs text-gray-500 mt-1.5 text-center">
              Demo accounts: 9876543210, 9123456789, 9988776655
            </p>
          </div>
        ) : (
          <div
            className={`text-sm font-semibold px-4 py-1.5 rounded-full border ${
              customerFound
                ? 'bg-green-900/40 border-green-600 text-green-300'
                : 'bg-amber-900/40 border-amber-600 text-amber-300'
            }`}
          >
            {customerFound
              ? `✓ Identified: ${customerName}${lookedUpPhone ? ` (${lookedUpPhone})` : ''}`
              : '⚠ Guest — account questions need a registered number'}
          </div>
        )}

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
              className="bg-red-700 hover:bg-red-800 text-white font-semibold px-8 py-3 rounded-full transition-colors shadow-md"
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
            <p>1. Enter your Airtel number</p>
            <p>2. Click <strong className="text-green-400">Start Call</strong></p>
            <p>3. Speak naturally in Hindi, English, or Marathi — the bot listens automatically</p>
            <p>4. Pause for 1.5 seconds — the bot will respond in your language</p>
            <p>5. Click <strong className="text-red-400">End Call</strong> when done</p>
          </div>
        </div>
      )}
    </div>
  )
}
