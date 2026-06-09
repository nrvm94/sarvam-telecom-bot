"""
Sarvam Telecom Bot — FastAPI Backend
Orchestrates: STT → RAG → LLM → TTS → Supabase → n8n escalation
"""

import base64
import logging
import os
import time
import uuid
from datetime import datetime, timezone

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
        "*",
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


# ---- Startup event -------------------------------------------------------
@app.on_event("startup")
async def startup_event():
    global sarvam_client, rag_engine, conversation_manager, supabase_client  # noqa: PLW0603

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

    logger.info("Sarvam Telecom Bot started successfully ✓")


# ---- Pydantic request/response models ------------------------------------

class StartCallRequest(BaseModel):
    customer_phone: str = Field(default="", description="Customer phone number (optional)")
    customer_name: str = Field(default="", description="Customer name (optional)")
    language: str = Field(default="hi", description="Language: 'hi' for Hindi, 'en' for English")


class TranscribeRequest(BaseModel):
    audio_base64: str = Field(..., description="Base64-encoded audio (WebM or WAV)")
    call_id: str = Field(..., description="Active call identifier")
    language: str = Field(default="hi", description="Language for STT and TTS")


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


@app.post("/voice/start")
async def start_call(req: StartCallRequest):
    """
    Initiate a new voice call session.
    Creates a unique call_id and persists initial metadata to Supabase.
    """
    call_id = "call_" + uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()

    call_data = {
        "call_id": call_id,
        "customer_phone": req.customer_phone,
        "customer_name": req.customer_name,
        "language": req.language,
        "status": "active",
        "started_at": now,
        "conversation": [],
    }

    logger.info(
        "New call started | call_id=%s | language=%s | customer=%s",
        call_id,
        req.language,
        req.customer_name or req.customer_phone or "anonymous",
    )

    await supabase_client.insert_call(call_data)

    return {
        "call_id": call_id,
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

    # Map short language codes to BCP-47 for Sarvam APIs
    lang_map = {"hi": "hi-IN", "en": "en-IN"}
    sarvam_lang = lang_map.get(req.language, req.language)

    try:
        # Step 1 — Decode audio
        audio_bytes = base64.b64decode(req.audio_base64)
        logger.debug("Step 1 — Audio decoded | bytes=%d", len(audio_bytes))

        # Step 2 — STT
        transcription = await sarvam_client.transcribe_audio(audio_bytes, sarvam_lang)
        logger.info("Step 2 — STT done | transcription=%r", transcription[:100])

        # Step 3 — RAG
        context = await rag_engine.query(transcription)
        logger.info("Step 3 — RAG done | context_len=%d", len(context))

        # Step 4 — LLM response
        bot_response = await sarvam_client.generate_response(
            query=transcription,
            context=context,
            language=req.language,
        )
        logger.info("Step 4 — LLM done | response=%r", bot_response[:100])

        # Step 5 — Escalation detection
        escalate = await conversation_manager.detect_escalation(
            transcription, bot_response
        )
        logger.info("Step 5 — Escalation=%s", escalate)

        # Step 6 — Issue classification
        issue_type = await conversation_manager.classify_issue(transcription)
        logger.info("Step 6 — Issue type=%s", issue_type)

        # Step 7 — TTS
        audio_out_bytes = await sarvam_client.synthesize_speech(
            text=bot_response,
            language=sarvam_lang,
            voice=os.getenv("DEFAULT_TTS_VOICE", "female_1"),
        )
        logger.info("Step 7 — TTS done | audio_bytes=%d", len(audio_out_bytes))

        # Step 8 — Encode audio for JSON transport
        response_audio_b64 = base64.b64encode(audio_out_bytes).decode("utf-8")

        # Step 9 — Log to Supabase
        await supabase_client.log_conversation_turn(
            req.call_id, transcription, bot_response, req.language
        )
        logger.debug("Step 9 — Conversation logged to Supabase")

        # Step 10 — Trigger n8n escalation (non-blocking)
        if escalate:
            await trigger_n8n_escalation(
                req.call_id, issue_type, transcription, bot_response
            )

        return {
            "transcription": transcription,
            "response": bot_response,
            "audio_base64": response_audio_b64,
            "language": req.language,
            "escalate": escalate,
            "issue_type": issue_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Transcribe pipeline failed: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "error": str(exc),
                "detail": "Voice pipeline failed. Please try again.",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )


@app.post("/voice/end")
async def end_call(req: EndCallRequest):
    """End an active call session and record duration in Supabase."""
    logger.info(
        "End call | call_id=%s | duration=%ds", req.call_id, req.duration_seconds
    )

    await supabase_client.end_call(req.call_id, req.duration_seconds)

    return {
        "call_id": req.call_id,
        "status": "completed",
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
