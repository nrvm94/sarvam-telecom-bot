"""
Sarvam AI API Client
Handles all interactions with Sarvam AI services:
  - Speech-to-Text  → POST https://api.sarvam.ai/speech-to-text  (multipart/form-data)
  - Chat Completions → POST https://api.sarvam.ai/v1/chat/completions (OpenAI-compat)
  - Text-to-Speech  → POST https://api.sarvam.ai/text-to-speech  (JSON)

Auth:
  - /v1/chat/completions  uses  Authorization: Bearer <key>
  - /speech-to-text and /text-to-speech  use  api-subscription-key: <key>
"""

import base64
import logging
import os

import aiohttp

logger = logging.getLogger(__name__)

# Map generic voice names → real Sarvam Bulbul v2 speaker IDs
# bulbul:v2 voices: anushka, manisha, vidya, arya, abhilash, karun, hitesh
# bulbul:v3 voices include: ritu, anushka, rahul, priya, ...
VOICE_MAP = {
    "female_1": "anushka",
    "female_2": "manisha",
    "female_3": "vidya",
    "male_1": "abhilash",
    "male_2": "karun",
    "male_3": "hitesh",
    # Pass-through any real speaker name as-is
}


class SarvamClient:
    """
    Async client for Sarvam AI APIs (STT, LLM, TTS).
    Uses aiohttp for non-blocking HTTP requests.
    """

    def __init__(self, api_key: str, base_url: str = "https://api.sarvam.ai"):
        self.api_key = api_key

        # Strip /v1 suffix — we build paths explicitly per endpoint
        clean = base_url.rstrip("/")
        if clean.endswith("/v1"):
            clean = clean[:-3]
        self.base_url = clean  # e.g. "https://api.sarvam.ai"

        logger.info(
            "SarvamClient initialised | base_url=%s | key=%s***",
            self.base_url,
            api_key[:8] if api_key else "MISSING",
        )

    # ------------------------------------------------------------------
    # Auth header helpers
    # ------------------------------------------------------------------

    def _bearer_headers(self) -> dict:
        """For OpenAI-compatible /v1/ endpoints."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _subscription_headers(self) -> dict:
        """For Sarvam-native endpoints (STT, TTS)."""
        return {
            "api-subscription-key": self.api_key,
        }

    # ------------------------------------------------------------------
    # Method 1 — Speech-to-Text (Saaras)
    # ------------------------------------------------------------------

    async def transcribe_audio(
        self, audio_bytes: bytes, language: str = "hi-IN"
    ) -> str:
        """
        Transcribe raw audio bytes to text using Sarvam Saaras STT.

        The API expects multipart/form-data — NOT base64 JSON.

        Args:
            audio_bytes: Raw WebM/Opus audio captured from browser microphone.
            language:    BCP-47 code, e.g. "hi-IN" or "en-IN".

        Returns:
            Transcribed text string.
        """
        logger.debug(
            "STT | language=%s | audio_bytes=%d", language, len(audio_bytes)
        )

        # Build multipart form — field 'file' is required by Sarvam STT
        # Model: saarika = transcription in original language (what we need)
        #        saaras  = speech-to-English translation (NOT what we need)
        data = aiohttp.FormData()
        data.add_field(
            "file",
            audio_bytes,
            filename="audio.webm",
            content_type="audio/webm",
        )
        data.add_field("language_code", language)
        data.add_field("model", "saarika:v2.5")

        url = f"{self.base_url}/speech-to-text"
        logger.debug("STT | POST %s", url)

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                data=data,
                headers=self._subscription_headers(),  # api-subscription-key
            ) as resp:
                body = await resp.json(content_type=None)
                logger.debug("STT | status=%d | body=%s", resp.status, str(body)[:300])

                if resp.status != 200:
                    raise RuntimeError(
                        f"Sarvam STT failed: HTTP {resp.status} | {body}"
                    )

                # Response: {"transcript": "...", ...}
                transcript = (
                    body.get("transcript")
                    or body.get("text")
                    or body.get("transcription")
                    or ""
                )
                logger.info(
                    "STT done | language=%s | transcript=%r",
                    language,
                    transcript[:120],
                )
                return transcript

    # ------------------------------------------------------------------
    # Method 2 — Chat Completions (LLM)
    # ------------------------------------------------------------------

    async def generate_response(
        self, query: str, context: str, language: str = "hi"
    ) -> str:
        """
        Generate an Airtel customer-support reply using Sarvam LLM.

        Uses /v1/chat/completions (OpenAI-compatible) with Authorization: Bearer.

        Args:
            query:    Customer's transcription / question.
            context:  RAG context from the Airtel knowledge base.
            language: "hi" for Hindi, "en" for English.

        Returns:
            Bot response text.
        """
        if language.startswith("hi"):
            system_content = (
                "Aap ek helpful Airtel customer support agent hain. "
                "Sirf Hindi mein jawab dein. "
                "2-3 sentences mein concise aur clear jawab dein."
            )
        else:
            system_content = (
                "You are a helpful Airtel customer support agent. "
                "Answer only in English. "
                "Keep your response concise, 2-3 sentences maximum."
            )

        user_content = query
        if context and context.strip():
            user_content += f"\n\nRelevant Information:\n{context}"

        payload = {
            "model": "sarvam-30b",
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.3,
            # Do NOT set max_tokens — sarvam-30b and sarvam-105b are reasoning models
            # that spend ~1500-2000 tokens on internal chain-of-thought before writing
            # the actual response. Any hard cap will truncate during reasoning, leaving
            # content=None. Let the model finish naturally (finish_reason='stop').
        }

        url = f"{self.base_url}/v1/chat/completions"
        logger.debug(
            "LLM | POST %s | query=%r | context_len=%d",
            url,
            query[:80],
            len(context),
        )

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                headers=self._bearer_headers(),  # Authorization: Bearer
            ) as resp:
                body = await resp.json(content_type=None)
                logger.debug(
                    "LLM | status=%d | body=%s", resp.status, str(body)[:400]
                )

                if resp.status != 200:
                    raise RuntimeError(
                        f"Sarvam LLM failed: HTTP {resp.status} | {body}"
                    )

                choices = body.get("choices") or [{}]
                message = choices[0].get("message") or {}

                # sarvam-105b may return content=None with reasoning_content populated
                # when token budget runs out. Fall back to reasoning_content in that case.
                response_text = (
                    message.get("content")
                    or message.get("reasoning_content")
                    or ""
                )

                logger.info(
                    "LLM done | query=%r | response=%r",
                    query[:60],
                    str(response_text)[:120],
                )
                return response_text

    # ------------------------------------------------------------------
    # Method 3 — Text-to-Speech (Bulbul)
    # ------------------------------------------------------------------

    async def synthesize_speech(
        self,
        text: str,
        language: str = "hi-IN",
        voice: str = "female_1",
    ) -> bytes:
        """
        Convert text to speech using Sarvam Bulbul TTS.

        Args:
            text:     Text to synthesise.
            language: BCP-47 code, e.g. "hi-IN".
            voice:    Generic voice alias ("female_1") or real speaker name ("anushka").

        Returns:
            Raw WAV audio bytes.
        """
        # Map generic alias → real speaker name; pass-through if already real
        speaker = VOICE_MAP.get(voice, voice)
        # Ensure language has region suffix
        lang_code = language if "-" in language else f"{language}-IN"

        payload = {
            "text": text,
            "target_language_code": lang_code,
            "speaker": speaker,
            "model": "bulbul:v2",
            "pace": 1.0,
        }

        url = f"{self.base_url}/text-to-speech"
        logger.debug(
            "TTS | POST %s | text_len=%d | language=%s | speaker=%s",
            url,
            len(text),
            lang_code,
            speaker,
        )

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                headers={
                    **self._subscription_headers(),  # api-subscription-key
                    "Content-Type": "application/json",
                },
            ) as resp:
                body = await resp.json(content_type=None)
                logger.debug(
                    "TTS | status=%d | body_keys=%s",
                    resp.status,
                    list(body.keys()) if isinstance(body, dict) else "non-dict",
                )

                if resp.status != 200:
                    raise RuntimeError(
                        f"Sarvam TTS failed: HTTP {resp.status} | {body}"
                    )

                # Response: {"audios": ["<base64_wav>"]}
                audios = body.get("audios") or []
                if not audios:
                    raise RuntimeError(
                        f"Sarvam TTS returned empty audios list | body={body}"
                    )

                audio_bytes = base64.b64decode(audios[0])
                logger.info(
                    "TTS done | text_len=%d | language=%s | speaker=%s | output_bytes=%d",
                    len(text),
                    lang_code,
                    speaker,
                    len(audio_bytes),
                )
                return audio_bytes
