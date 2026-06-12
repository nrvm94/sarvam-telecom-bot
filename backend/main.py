"""
Sarvam Telecom Bot — FastAPI Backend
Orchestrates: STT → RAG → LLM → TTS → Supabase → n8n escalation
"""

import asyncio
import base64
import logging
import os
import time
import uuid
from datetime import datetime, timezone

import re

import aiohttp
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# ---- Load environment first, before importing local modules ---------------
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

from conversation import ConversationManager        # noqa: E402
from rag_engine import AirtelKnowledgeBase         # noqa: E402
from sarvam_client import SarvamClient             # noqa: E402
from supabase_client import SupabaseClient         # noqa: E402

# Marathi-specific Devanagari words (not shared with Hindi)
_MARATHI_DEVANAGARI_WORDS = frozenset([
    # Original set
    "आहे", "आहेत", "माझा", "माझी", "माझे", "आणि", "मला",
    "कुठे", "आम्ही", "हवे", "तुमचा", "तुमची", "नाही", "काय",
    "कसे", "कधी", "कोण", "सांगा", "करा", "द्या", "घ्या",
    # Additional uniquely Marathi words
    "खूप",      # very/much   (Hindi uses बहुत)
    "पण",       # but         (Hindi uses लेकिन/पर)
    "म्हणजे",   # means/i.e.  (uniquely Marathi)
    "म्हणून",   # therefore   (uniquely Marathi)
    "नको",      # don't want  (uniquely Marathi)
    "आता",      # now         (Hindi uses अभी)
    "वेळ",      # time        (Hindi uses समय/वक्त)
    "जास्त",    # more/too much (Hindi uses ज्यादा)
    "किती",     # how much    (Hindi uses कितना/कितनी)
    "पाहिजे",   # need/want   (uniquely Marathi)
    "कशाला",    # for what    (uniquely Marathi)
    "आपण",      # we/you formal (Marathi 1st-person; Hindi आप is 2nd-person)
    "बघा",      # look/see    (Hindi uses देखिए)
    "ऐका",      # listen      (Hindi uses सुनिए)
    "सगळे",     # all/everyone (Hindi uses सब/सारे)
    "झाले",     # happened/done (Marathi past; Hindi uses हुआ)
    "झाली",
    "झाला",
    "मिळाले",   # got         (Hindi uses मिला)
    "मिळेल",    # will get    (Hindi uses मिलेगा)
    "माझ्या",   # my (oblique) (Hindi uses मेरे/मेरी)
    "तुमच्या",  # your (oblique)
    "आहात",     # you are     (Marathi 2nd-person; Hindi uses हैं)
])

# Postpositions that appear in _HINDI_DEVANAGARI_WORDS.
# These are "weak" signals: STT routinely appends them to transliterated English
# (e.g. "में करंट प्लान" for "in current plan").  A detection must include at
# least one NON-postposition ("strong") match to classify as Hindi.
_HINDI_POSTPOSITIONS = frozenset([
    "का", "की", "के", "को", "से", "में", "पर", "ने", "तक",
])

# Common Hindi Devanagari words used to distinguish real Hindi from
# STT-generated Devanagari transliterations of English speech.
# If NONE of these appear in Devanagari text, it is likely a transliteration
# artifact (e.g. "व्हाट इज माय करंट प्लान") rather than actual Hindi.
_HINDI_DEVANAGARI_WORDS = frozenset([
    # Pronouns
    "मैं", "मेरा", "मेरी", "मेरे", "मुझे", "मुझको",
    "आप", "आपका", "आपकी", "आपके",
    "हम", "हमारा", "हमारी", "हमारे",
    "तुम", "तुम्हारा", "वो", "वह", "उसका", "उसकी",
    "यह", "इसका", "इसकी",
    # Question words
    "क्या", "कैसे", "कैसा", "कैसी", "क्यों", "क्यूँ",
    "कब", "कहाँ", "कहां", "कितना", "कितनी", "कितने", "कौन",
    # Verb forms
    "है", "हैं", "था", "थी", "थे", "होगा", "होगी", "होंगे",
    "करना", "करें", "करो", "करता", "करती", "किया",
    "बताओ", "बताना", "बता", "बताइए",
    "चाहिए", "चाहता", "चाहती", "चाहते",
    # Negation / affirmation
    "नहीं", "नही", "मत", "हाँ",
    # Conjunctions / particles
    "और", "या", "लेकिन", "परंतु", "तो", "भी", "ही", "सिर्फ",
    "बहुत", "थोड़ा", "थोड़ी",
    # Postpositions
    "का", "की", "के", "को", "से", "में", "पर", "ने", "तक",
    # Common words
    "अभी", "पहले", "बाद", "आज", "कल",
    "अच्छा", "ठीक", "जी", "नमस्ते", "काफी", "कुछ", "कोई",
])

# Marathi-specific Roman-script words (not common in Hindi Romanization)
_MARATHI_ROMAN_WORDS = frozenset([
    "ahe", "aahe", "ahet", "maza", "mazi", "mala",
    "aani", "amhi", "kuthe", "tumcha", "tumchi",
    "sanga", "kara", "kadhi", "kon",
    # Additional Roman Marathi
    "kiti", "khup", "nako", "mhanje", "mhanun",
    "pahije", "kashala", "zale", "zali", "zala",
    "milale", "milel", "aata", "sagale", "vel",
])

_HINGLISH_WORDS = frozenset([
    "mera", "meri", "mujhe", "hamara", "hamare", "tumhara",
    "kya", "kaise", "kaisa", "kyun", "kab", "kahan", "kitna", "kitni",
    "hai", "hain", "tha", "thi", "hoga", "hogi",
    "nahi", "haan",
    "chahiye", "chahte",
    "kar", "karo", "karna", "karein", "karta", "karti",
    "bata", "batao", "batana",
    "aap", "tum", "hum", "woh", "yeh",
    "aur", "lekin", "toh", "bhi", "sirf", "bahut", "thoda",
    "abhi", "pehle", "baad",
    "accha", "theek", "bilkul", "shukriya",
    "ji",
    "mere", "tera", "teri", "uska", "uski",
    # NOTE: "main" (English: primary/chief) and "ya" (English: yeah) removed
    # NOTE: "problem" and "issue" already removed (common English words)
])


def _detect_language(text: str, user_pref: str = "hi") -> str:
    """Detect language per turn: hi, mr, or en.

    Uses vocabulary matching to distinguish real Hindi/Marathi from
    Devanagari transliterations that some STT models produce for English
    speech (e.g. "व्हाट इज माय करंट प्लान" for "what is my current plan").
    If Devanagari is present but contains no known Hindi/Marathi vocabulary
    words, we treat it as an STT artifact and return "en".
    """
    # Strip Devanagari dandas (। ॥) so they don't fuse with the preceding word
    # (e.g. "है।" → "है" so it can be found in the vocabulary sets).
    text = text.replace('\u0964', '').replace('\u0965', '')
    devanagari_words = re.findall(r'[\u0900-\u097F]+', text)
    devanagari = sum(len(w) for w in devanagari_words)
    alpha = len(re.findall(r'[a-zA-Z\u0900-\u097F]', text))
    if alpha == 0:
        return user_pref if user_pref in ("hi", "mr") else "hi"
    if (devanagari / alpha) > 0.3:
        # Devanagari present — check vocabulary to confirm it is real Hindi/Marathi
        deva_set = set(devanagari_words)
        if deva_set & _MARATHI_DEVANAGARI_WORDS:
            return "mr"
        hindi_matches = deva_set & _HINDI_DEVANAGARI_WORDS
        if hindi_matches:
            # "Strong" matches are non-postposition Hindi words (pronouns, verbs,
            # conjunctions).  Postpositions alone are not reliable — STT appends
            # them to transliterated English ("में करंट प्लान", "प्लान का").
            strong_matches = hindi_matches - _HINDI_POSTPOSITIONS
            if len(deva_set) <= 3:
                # Short utterances: require ≥1 strong (non-postposition) match.
                if strong_matches:
                    return "hi"
            else:
                # Longer utterances (4+ Devanagari words): require ≥2 total
                # matches, ≥1 strong (non-postposition) match, AND ≥40% of all
                # Devanagari words must be Hindi vocab.  The density filter rejects
                # STT transliterations where the model semantically substituted
                # just 2-3 function words ("is"→"है", "in"→"में") in an otherwise
                # English sentence.
                hindi_density = len(hindi_matches) / len(deva_set)
                if len(hindi_matches) >= 2 and strong_matches and hindi_density >= 0.40:
                    return "hi"
        # Devanagari with no recognised vocabulary (or low Hindi ratio) →
        # STT transliteration of English speech.
        return "en"
    # Roman script — check for Marathi then Hinglish
    words = set(text.lower().split())
    if words & _MARATHI_ROMAN_WORDS:
        return "mr"
    hinglish_hits = words & _HINGLISH_WORDS
    if hinglish_hits:
        # For short queries (≤3 words), even a single Hinglish word is strong enough signal.
        # For longer queries (4+ words), require ≥2 Hinglish words to avoid classifying
        # English sentences as Hindi when STT appends a single Hindi particle (e.g. "hai").
        if len(words) <= 3 or len(hinglish_hits) >= 2:
            return "hi"
    return "en"


# ---- Logging setup --------------------------------------------------------
log_level = os.getenv("LOG_LEVEL", "DEBUG").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.DEBUG),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---- FastAPI app ----------------------------------------------------------
app = FastAPI(
    title="Sarvam Telecom Bot API",
    description="AI-powered voice support bot for Airtel using Sarvam AI",
    version="1.0.0",
)

# ---- CORS ----------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- Request logging middleware ------------------------------------------
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    logger.info("→ %s %s", request.method, request.url.path)
    response = await call_next(request)
    duration_ms = (time.time() - start) * 1000
    logger.info(
        "← %s %s | %d | %.1fms",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response


# ---- Service clients (lazy, initialised in startup) ----------------------
sarvam_client: SarvamClient = None        # type: ignore[assignment]
rag_engine: AirtelKnowledgeBase = None    # type: ignore[assignment]
conversation_manager: ConversationManager = None  # type: ignore[assignment]
supabase_client: SupabaseClient = None    # type: ignore[assignment]
orchestrator = None                        # type: ignore[assignment]

# Kept for safety; orchestrator now owns per-call history
call_history: dict = {}


# ---- Startup event -------------------------------------------------------
@app.on_event("startup")
async def startup_event():
    global sarvam_client, rag_engine, conversation_manager, supabase_client, orchestrator  # noqa: PLW0603

    # Log config (mask secrets)
    logger.info("=" * 60)
    logger.info("Sarvam Telecom Bot starting up ...")
    logger.info("ENVIRONMENT   : %s", os.getenv("ENVIRONMENT", "development"))
    logger.info("LOG_LEVEL     : %s", os.getenv("LOG_LEVEL", "DEBUG"))
    logger.info("BACKEND_URL   : %s", os.getenv("BACKEND_URL"))
    logger.info("FRONTEND_URL  : %s", os.getenv("FRONTEND_URL"))
    logger.info("N8N_WEBHOOK   : %s", os.getenv("N8N_WEBHOOK_URL"))
    api_key = os.getenv("SARVAM_API_KEY", "")
    logger.info("SARVAM_KEY    : %s***", api_key[:8] if api_key else "MISSING")
    logger.info("SUPABASE_URL  : %s", os.getenv("SUPABASE_URL", "NOT SET"))
    logger.info("DEMO_DEFAULT  : %s", os.getenv("DEMO_DEFAULT_PHONE", "NOT SET"))
    logger.info("=" * 60)

    # Initialise clients
    sarvam_client = SarvamClient(
        api_key=os.getenv("SARVAM_API_KEY", ""),
        base_url=os.getenv("SARVAM_API_BASE", "https://api.sarvam.ai"),
    )

    rag_engine = AirtelKnowledgeBase()

    conversation_manager = ConversationManager()

    supabase_client = SupabaseClient(
        url=os.getenv("SUPABASE_URL", ""),
        key=os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""),
    )

    from orchestrator import VoiceOrchestrator
    orchestrator = VoiceOrchestrator(sarvam_client, rag_engine, supabase_client)

    logger.info("Sarvam Telecom Bot started successfully ✓")


# ---- Pydantic request/response models ------------------------------------

class StartCallRequest(BaseModel):
    customer_phone: str = Field(default="", description="Customer phone number (optional)")
    customer_name: str = Field(default="", description="Customer name (optional)")
    language: str = Field(default="hi", description="Language: 'hi' for Hindi, 'en' for English, 'mr' for Marathi")


class TranscribeRequest(BaseModel):
    audio_base64: str = Field(..., description="Base64-encoded audio (WebM or WAV)")
    call_id: str = Field(..., description="Active call identifier")
    language: str = Field(default="hi", description="Language preference: hi, en, or mr")


class EndCallRequest(BaseModel):
    call_id: str = Field(..., description="Active call identifier")
    duration_seconds: int = Field(default=0, description="Total call duration in seconds")


# ---- Routes --------------------------------------------------------------

@app.get("/health")
async def health_check():
    """Health check endpoint — returns service status."""
    return {
        "status": "ok",
        "service": "Sarvam Telecom Bot",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/debug/test")
async def debug_test_apis():
    """
    Tests each Sarvam API independently.
    Visit http://localhost:8000/debug/test in browser to diagnose failures.
    Returns pass/fail + error details for each service.
    """
    results = {}

    # 1. LLM test
    try:
        resp = await sarvam_client.generate_response(
            "What is Airtel balance check code?", "", "en"
        )
        results["llm"] = {"status": "ok", "response_preview": resp[:80]}
    except Exception as e:
        results["llm"] = {"status": "error", "error": str(e)}

    # 2. TTS test
    try:
        audio = await sarvam_client.synthesize_speech(
            "Hello this is a test", "en-IN", "female_1"
        )
        results["tts"] = {"status": "ok", "audio_bytes": len(audio)}
    except Exception as e:
        results["tts"] = {"status": "error", "error": str(e)}

    # 3. STT test (with synthetic silent WAV)
    try:
        import wave, io
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(b"\x00" * 32000)  # 1 second silence
        transcript = await sarvam_client.transcribe_audio(buf.getvalue(), "hi-IN")
        results["stt"] = {"status": "ok", "transcript": repr(transcript)}
    except Exception as e:
        results["stt"] = {"status": "error", "error": str(e)}

    # 4. Config info (proves which code version is running)
    results["config"] = {
        "stt_model": "saarika:v2.5",
        "llm_model": "sarvam-30b",
        "tts_model": "bulbul:v3",
        "tts_speaker": "anushka",
        "sarvam_base": os.getenv("SARVAM_API_BASE", ""),
        "key_prefix": os.getenv("SARVAM_API_KEY", "")[:8] + "***",
    }

    all_ok = all(v.get("status") == "ok" for k, v in results.items() if k != "config")
    return {"overall": "ok" if all_ok else "degraded", "tests": results}


@app.get("/debug/customer/{phone}")
async def debug_customer(phone: str):
    """Verify the customer DB lookup in isolation (no voice/STT).
    Visit http://localhost:8000/debug/customer/9876543210 in a browser."""
    from agents import CustomerProfileAgent
    c = CustomerProfileAgent().load_customer(phone)
    found = c.get("segment") != "unknown"
    return {
        "phone": phone,
        "found": found,
        "name": c.get("name"),
        "segment": c.get("segment"),
    }


@app.post("/voice/start")
async def start_call(req: StartCallRequest):
    """
    Initiate a new voice call session.
    Loads customer profile, generates personalised greeting, returns greeting audio.
    """
    call_id = "call_" + uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()

    logger.info(
        "New call started | call_id=%s | language=%s | phone=%s",
        call_id,
        req.language,
        req.customer_phone or "unknown",
    )

    # Persist call record to Supabase
    call_data = {
        "call_id": call_id,
        "customer_phone": req.customer_phone,
        "customer_name": req.customer_name,
        "language": req.language,
        "status": "active",
        "started_at": now,
        "conversation": [],
    }
    await supabase_client.insert_call(call_data)

    # Orchestrator: load customer profile and generate personalised greeting
    start_result = await orchestrator.start_call(call_id, req.customer_phone, req.language)

    # TTS the greeting — Marathi greeting is spoken in Hindi (bridge language)
    lang_code = {"hi": "hi-IN", "mr": "hi-IN"}.get(req.language, "en-IN")
    try:
        greeting_audio = await sarvam_client.synthesize_speech(
            text=start_result["greeting"],
            language=lang_code,
            voice=os.getenv("DEFAULT_TTS_VOICE", "female_1"),
        )
        greeting_audio_b64 = base64.b64encode(greeting_audio).decode("utf-8")
    except Exception as e:
        logger.warning("Greeting TTS failed: %s", e)
        greeting_audio_b64 = ""

    return {
        "call_id": call_id,
        "greeting": start_result["greeting"],
        "greeting_audio_base64": greeting_audio_b64,
        "customer_name": start_result["customer_name"],
        "customer_found": start_result["customer_found"],
        "customer_segment": start_result["customer_segment"],
        "looked_up_phone": start_result.get("looked_up_phone", ""),
        "status": "initiated",
        "timestamp": now,
    }


@app.post("/voice/transcribe")
async def transcribe_and_respond(req: TranscribeRequest):
    """
    Full voice pipeline:
      audio → STT → RAG → LLM → TTS → (escalation check) → response
    """
    logger.info(
        "Transcribe request | call_id=%s | language=%s | audio_b64_len=%d",
        req.call_id,
        req.language,
        len(req.audio_base64),
    )

    # Track which pipeline step we're on for precise error reporting
    step = "initialising"

    try:
        # Step 1 — Decode audio
        step = "Step 1: decode audio"
        audio_bytes = base64.b64decode(req.audio_base64)
        logger.debug("Step 1 — Audio decoded | bytes=%d", len(audio_bytes))

        # Step 2 — STT with parallel language detection
        # For hi/mr preference: run native-language and en-IN STT in parallel.
        # English speech through en-IN → clean Roman text, no Hindi/Hinglish words.
        # Hindi/Marathi speech through en-IN → Hinglish Roman (caught by _HINGLISH_WORDS)
        # or garbled phonetics — both indicate non-English speech.
        # This approach is reliable even when saarika:v2.5 semantically translates
        # English into Hindi (e.g. "what is my plan" → "मेरा प्लान क्या है"), which
        # makes vocabulary-density detection impossible.
        step = "Step 2: STT (Sarvam Saarika)"
        stt_lang = {"hi": "hi-IN", "mr": "mr-IN"}.get(req.language, "en-IN")

        if stt_lang != "en-IN":
            nat_tr, en_tr = await asyncio.gather(
                sarvam_client.transcribe_audio(audio_bytes, stt_lang),
                sarvam_client.transcribe_audio(audio_bytes, "en-IN"),
            )
            nat_tr = (nat_tr or "").strip()
            en_tr  = (en_tr  or "").strip()
            logger.info(
                "Step 2 — Dual STT | %s=%r | en-IN=%r",
                stt_lang, nat_tr[:80], en_tr[:80],
            )

            en_words             = set(en_tr.lower().split())
            en_roman             = len(re.findall(r'[a-zA-Z]', en_tr))
            en_all_alpha         = len(re.findall(r'[a-zA-Z\u0900-\u097F]', en_tr))
            en_roman_ratio       = en_roman / max(1, en_all_alpha)
            # Strip trailing punctuation before Hinglish check so "hai." matches "hai"
            en_words_no_punct    = {re.sub(r'[^a-z]', '', w) for w in en_words} - {''}
            has_hinglish         = bool(en_words_no_punct & _HINGLISH_WORDS)
            has_devanagari_in_en = bool(re.search(r'[\u0900-\u097F]', en_tr))

            # Treat as English only when en-IN produces clean Roman output:
            # > 80 % Roman chars + no Hinglish words + no Devanagari + ≥ 2 words.
            if (en_tr
                    and en_roman_ratio > 0.8
                    and not has_hinglish
                    and not has_devanagari_in_en
                    and len(en_words) >= 2):
                transcription = en_tr
                detected_lang  = "en"
                logger.info("Step 2 — Language: en (clean en-IN output)")
            else:
                transcription = nat_tr or en_tr
                detected_lang  = (
                    _detect_language(transcription, user_pref=req.language)
                    if transcription else req.language
                )
                logger.info(
                    "Step 2 — Language: %s (en-IN not clean | hinglish=%s | roman_ratio=%.2f)",
                    detected_lang, has_hinglish, en_roman_ratio,
                )
        else:
            # User preference is English — use en-IN STT
            transcription = await sarvam_client.transcribe_audio(audio_bytes, stt_lang)
            transcription = (transcription or "").strip()

            # Even with an English preference the user may have switched to Hindi/
            # Marathi mid-conversation.  en-IN STT on Hindi speech returns Hinglish
            # Roman words (e.g. "mera balance kya hai") rather than Devanagari.
            # Detect this via _HINGLISH_WORDS and re-transcribe with hi-IN so we
            # get a clean Devanagari transcript and the right detected_lang.
            tr_words        = set(transcription.lower().split())
            tr_words_clean  = {re.sub(r'[^a-z]', '', w) for w in tr_words} - {''}
            has_hinglish    = bool(tr_words_clean & _HINGLISH_WORDS)
            has_devanagari  = bool(re.search(r'[\u0900-\u097F]', transcription))

            if has_hinglish or has_devanagari:
                hi_tr = await sarvam_client.transcribe_audio(audio_bytes, "hi-IN")
                hi_tr = (hi_tr or "").strip()
                transcription = hi_tr or transcription
                detected_lang = _detect_language(transcription, user_pref="hi")
                logger.info(
                    "Step 2 — Language switch en→hi detected | detected=%s | hinglish=%s",
                    detected_lang, has_hinglish,
                )
            else:
                detected_lang = "en"
            logger.info("Step 2 — STT done | transcription=%r", transcription[:100])

        logger.info(
            "Language detected | detected_lang=%s | user_pref=%s | transcription=%r",
            detected_lang, req.language, transcription[:100],
        )

        # Empty transcript guard — silence or background noise
        if not transcription or not transcription.strip():
            logger.info("Empty transcript — returning early without LLM/TTS call")
            silence_msg = {
                "hi": "माफ़ करें, मुझे समझ नहीं आया। क्या आप दोबारा बोल सकते हैं?",
                "mr": "माफ करा, मला समजले नाही. कृपया पुन्हा सांगा.",
                "en": "Sorry, I couldn't hear you clearly. Could you please repeat that?",
            }.get(req.language, "माफ़ करें, मुझे समझ नहीं आया। क्या आप दोबारा बोल सकते हैं?")
            return {
                "transcription": "",
                "response": silence_msg,
                "audio_base64": "",
                "language": req.language,
                "escalate": False,
                "issue_type": "general_query",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        # Steps 3-6 — Orchestrator (RAG + LLM + escalation + classification)
        step = "Step 3-6: orchestrator process_turn"
        result = await orchestrator.process_turn(req.call_id, transcription, detected_lang)
        bot_response = result["response"]
        escalate = result["escalated"]
        issue_type = result["issue_type"]
        logger.info("Orchestrator done | escalated=%s | issue=%s | response=%r", escalate, issue_type, bot_response[:80])

        # Step 7 — TTS: use detected language for response
        step = "Step 7: TTS (Sarvam Bulbul)"
        tts_lang = {"hi": "hi-IN", "mr": "mr-IN"}.get(detected_lang, "en-IN")

        # Prepare text for TTS: strip markdown and trim to ≤3 spoken sentences.
        # The LLM occasionally emits chain-of-thought with markdown formatting
        # (e.g. "**Analyze:**\n* ...") which sounds bad when spoken aloud and
        # can push the text past the 500-char TTS limit.
        from agents import _voice_clean
        tts_text = _voice_clean(bot_response)
        # If still very long, keep only the first 3 sentence-ending segments.
        if len(tts_text) > 450:
            parts = re.split(r'(?<=[।.!?])\s+', tts_text)
            truncated, acc = [], 0
            for part in parts:
                if acc + len(part) > 450:
                    break
                truncated.append(part)
                acc += len(part) + 1
            tts_text = " ".join(truncated) if truncated else tts_text[:450]
        logger.info("Step 7 — TTS input | len=%d | text=%r", len(tts_text), tts_text[:80])

        audio_out_bytes = await sarvam_client.synthesize_speech(
            text=tts_text,
            language=tts_lang,
            voice=os.getenv("DEFAULT_TTS_VOICE", "female_1"),
        )
        logger.info("Step 7 — TTS done | audio_bytes=%d", len(audio_out_bytes))

        # Step 8 — Encode audio for JSON transport
        step = "Step 8: encode audio"
        response_audio_b64 = base64.b64encode(audio_out_bytes).decode("utf-8")

        # Step 9 — Log to Supabase
        step = "Step 9: log to Supabase"
        await supabase_client.log_conversation_turn(
            req.call_id, transcription, bot_response, detected_lang
        )
        logger.debug("Step 9 — Conversation logged to Supabase")

        return {
            "transcription": transcription,
            "response": bot_response,
            "audio_base64": response_audio_b64,
            "language": detected_lang,
            "escalate": escalate,
            "issue_type": issue_type,
            "routing": result.get("routing"),
            "priority": result.get("priority"),
            "ticket_id": result.get("ticket_id"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as exc:  # pylint: disable=broad-except
        logger.error(
            "Pipeline failed at [%s]: %s", step, exc, exc_info=True
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": f"[{step}] {str(exc)}",
                "detail": f"Pipeline failed at: {step}",
                "step": step,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )


@app.post("/voice/end")
async def end_call(req: EndCallRequest):
    """End an active call session and record duration in Supabase."""
    logger.info(
        "End call | call_id=%s | duration=%ds", req.call_id, req.duration_seconds
    )

    end_result = await orchestrator.end_call(req.call_id, req.duration_seconds)
    call_history.pop(req.call_id, None)  # Safety cleanup of legacy dict

    return {
        "call_id": req.call_id,
        "status": "completed",
        "turns": end_result.get("turns", 0),
        "duration_seconds": req.duration_seconds,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/n8n/webhook")
async def n8n_callback(request: Request):
    """
    Receives callback from n8n workflow after escalation processing.
    Updates Supabase with ticket details.
    """
    body = await request.json()
    logger.info("n8n callback received | body=%s", body)

    call_id = body.get("call_id", "")
    ticket_id = body.get("ticket_id", "")
    status = body.get("status", "escalated")

    if call_id:
        await supabase_client.update_escalation(call_id, ticket_id, status)

    return {
        "status": "received",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---- Helper: n8n escalation trigger --------------------------------------

async def trigger_n8n_escalation(
    call_id: str, issue_type: str, query: str, response: str
) -> None:
    """
    Fire-and-forget POST to n8n webhook to trigger the escalation workflow.
    Failure does not propagate to the main pipeline.
    """
    webhook_url = os.getenv("N8N_WEBHOOK_URL", "")
    if not webhook_url:
        logger.warning("N8N_WEBHOOK_URL not set — skipping escalation trigger.")
        return

    payload = {
        "call_id": call_id,
        "issue_type": issue_type,
        "user_query": query,
        "bot_response": response,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    logger.info(
        "Triggering n8n escalation | call_id=%s | issue=%s | url=%s",
        call_id,
        issue_type,
        webhook_url,
    )

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                webhook_url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                logger.info(
                    "n8n escalation response | status=%d", resp.status
                )
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning(
            "n8n escalation trigger failed (non-fatal): %s", exc
        )


# ---- Entrypoint ----------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level=log_level.lower(),
    )
