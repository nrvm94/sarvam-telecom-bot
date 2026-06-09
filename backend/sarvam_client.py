"""
Sarvam AI API Client
Handles all interactions with Sarvam AI services:
  - Speech-to-Text (Saaras STT)
  - Chat Completions (LLM)
  - Text-to-Speech (Bulbul TTS)
"""

import base64
import logging
import os
import aiohttp

logger = logging.getLogger(__name__)


class SarvamClient:
    """
    Async client for Sarvam AI APIs.
    Uses aiohttp for non-blocking HTTP requests.
    """

    def __init__(self, api_key: str, base_url: str = "https://api.sarvam.ai"):
        self.api_key = api_key
        # Normalize base_url — strip trailing /v1 or / so we can build paths cleanly
        self.base_url = base_url.rstrip("/")
        if self.base_url.endswith("/v1"):
            self.base_url = self.base_url[:-3]
        logger.info(
            "SarvamClient initialised | base_url=%s | key=%s***",
            self.base_url,
            api_key[:8] if api_key else "MISSING",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Method 1 — Speech-to-Text (Saaras)
    # ------------------------------------------------------------------

    async def transcribe_audio(
        self, audio_bytes: bytes, language: str = "hi-IN"
    ) -> str:
        """
        Transcribe raw audio bytes to text using Sarvam Saaras STT.

        Args:
            audio_bytes: Raw WAV/WebM audio bytes from the browser microphone.
            language: BCP-47 language code, e.g. "hi-IN" or "en-IN".

        Returns:
            Transcribed text string.
        """
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        logger.debug(
            "STT | language=%s | audio_size=%d bytes | b64_len=%d",
            language,
            len(audio_bytes),
            len(audio_b64),
        )

        payload = {
            "audio": audio_b64,
            "language_code": language,
            "model": "saaras:v2",
            "with_timestamps": False,
        }

        url = f"{self.base_url}/v1/speech-to-text"
        logger.debug("STT | POST %s", url)

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=payload, headers=self._headers()
            ) as resp:
                body = await resp.json(content_type=None)
                logger.debug("STT | status=%d | body=%s", resp.status, body)

                if resp.status != 200:
                    raise RuntimeError(
                        f"Sarvam STT failed: HTTP {resp.status} | {body}"
                    )

                # Sarvam STT returns: {"transcript": "...", ...}
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

        Args:
            query: Customer's question / transcription.
            context: Retrieved RAG context from knowledge base.
            language: "hi" for Hindi, "en" for English.

        Returns:
            Bot response text.
        """
        # Build language-appropriate system prompt
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
            "model": "sarvam-m",
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.3,
            "max_tokens": 150,
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
                url, json=payload, headers=self._headers()
            ) as resp:
                body = await resp.json(content_type=None)
                logger.debug("LLM | status=%d | body=%s", resp.status, str(body)[:400])

                if resp.status != 200:
                    raise RuntimeError(
                        f"Sarvam LLM failed: HTTP {resp.status} | {body}"
                    )

                response_text = (
                    body.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )
                logger.info(
                    "LLM done | query=%r | response=%r",
                    query[:60],
                    response_text[:120],
                )
                return response_text

    # ------------------------------------------------------------------
    # Method 3 — Text-to-Speech (Bulbul TTS)
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
            text: Text to synthesise.
            language: BCP-47 language code.
            voice: Voice ID, e.g. "female_1", "male_1".

        Returns:
            Raw WAV audio bytes.
        """
        # Normalise language code for TTS
        lang_code = language if "-" in language else f"{language}-IN"

        payload = {
            "inputs": [text],
            "target_language_code": lang_code,
            "speaker": voice,
            "model": "bulbul:v1",
        }

        url = f"{self.base_url}/v1/text-to-speech"
        logger.debug(
            "TTS | POST %s | text_len=%d | language=%s | voice=%s",
            url,
            len(text),
            lang_code,
            voice,
        )

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=payload, headers=self._headers()
            ) as resp:
                body = await resp.json(content_type=None)
                logger.debug("TTS | status=%d | body_keys=%s", resp.status, list(body.keys()) if isinstance(body, dict) else "non-dict")

                if resp.status != 200:
                    raise RuntimeError(
                        f"Sarvam TTS failed: HTTP {resp.status} | {body}"
                    )

                # Sarvam TTS returns: {"audios": ["<base64>", ...]}
                audios = body.get("audios") or []
                if not audios:
                    raise RuntimeError("Sarvam TTS returned empty audios list")

                audio_bytes = base64.b64decode(audios[0])
                logger.info(
                    "TTS done | text_len=%d | language=%s | voice=%s | audio_bytes=%d",
                    len(text),
                    lang_code,
                    voice,
                    len(audio_bytes),
                )
                return audio_bytes
