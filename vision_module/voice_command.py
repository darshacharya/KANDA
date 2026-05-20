"""
KANDA Vision Module — Voice Transcription
Simplified: just transcribes audio from mic into text.
All intent classification and planning is now in task_agent.py.

Flow:
  1. Record audio with VAD (stops when user stops speaking)
  2. Send WAV to Gemini for transcription
  3. Return plain text transcript

Test standalone:
    export GEMINI_API_KEY=your_key
    python3 voice_command.py
"""

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from typing import Optional

from google import genai
from google.genai import types

import config
from mic import Microphone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_TRANSCRIBE_PROMPT = """Transcribe exactly what the person said in this audio recording.
Return only the transcription as plain text. If there is no speech or only silence, reply with exactly: SILENCE"""


class VoiceTranscriber:
    """Records from earphone mic and transcribes using Gemini."""

    def __init__(self):
        if not config.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY not set")
        self._client = genai.Client(api_key=config.GEMINI_API_KEY)
        self._mic    = Microphone()
        logger.info("VoiceTranscriber initialized")

    def start(self) -> None:
        self._mic.start()

    def listen(self) -> Optional[str]:
        """
        Record one VAD clip and return the transcript.
        Returns:
          - transcript string if speech detected
          - "" (empty string) if silence
          - None if recording or API failed
        """
        wav_bytes = self._mic.record_vad()
        if wav_bytes is None:
            return None

        if not self._mic.has_speech(wav_bytes):
            return ""

        return self._transcribe(wav_bytes)

    def _transcribe(self, wav_bytes: bytes) -> Optional[str]:
        """Send audio to Gemini, return transcript string."""
        def call():
            resp = self._client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=[
                    _TRANSCRIBE_PROMPT,
                    types.Part.from_bytes(data=wav_bytes, mime_type="audio/wav"),
                ],
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=200,
                ),
            )
            return resp.text.strip()

        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(call)
            try:
                result = fut.result(timeout=config.GEMINI_TIMEOUT_SEC)
            except TimeoutError:
                logger.warning("Transcription timed out")
                return None
            except Exception as exc:
                logger.error("Transcription failed: %s", exc)
                return None

        if not result or result.upper() == "SILENCE":
            return ""

        logger.info("Transcript: '%s'", result)
        return result

    def stop(self) -> None:
        self._mic.stop()
        logger.info("VoiceTranscriber stopped")


if __name__ == "__main__":
    print("=== Voice Transcription Test ===")
    print()

    vt = VoiceTranscriber()
    vt.start()

    for i in range(3):
        print(f"\n--- Round {i+1}/3 ---")
        print("Speak something (recording stops when you pause)...")
        transcript = vt.listen()
        if transcript is None:
            print("  ERROR: recording failed")
        elif transcript == "":
            print("  (silence)")
        else:
            print(f"  Heard: '{transcript}'")

    vt.stop()
    print("\nDone.")
