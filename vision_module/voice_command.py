"""
KANDA Vision Module — Voice Transcription
Simplified: just transcribes audio from mic into text.
All intent classification and planning is now in task_agent.py.

Flow:
  1. Record audio with VAD (stops when user stops speaking)
  2. Send WAV to Groq Whisper for transcription
  3. Return plain text transcript

Test standalone:
    export GROQ_API_KEY=your_key
    python3 voice_command.py
"""

import json
import logging
import urllib.request
from typing import Optional

import config
from mic import Microphone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


class VoiceTranscriber:
    """Records from earphone mic and transcribes using Groq Whisper."""

    def __init__(self):
        if not config.GROQ_API_KEY:
            raise ValueError("GROQ_API_KEY not set — required for voice transcription")
        self._mic = Microphone()
        logger.info("VoiceTranscriber initialized (Groq Whisper)")

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
        """Transcribe audio using Groq Whisper."""
        result = self._transcribe_groq(wav_bytes)

        if not result or result.upper() == "SILENCE":
            return ""

        logger.info("Transcript: '%s'", result)
        return result

    def _transcribe_groq(self, wav_bytes: bytes) -> Optional[str]:
        """Transcribe using Groq Whisper API."""
        try:
            boundary = "----KandaAudioBoundary"
            body = (
                f"--{boundary}\r\n"
                f"Content-Disposition: form-data; name=\"file\"; filename=\"audio.wav\"\r\n"
                f"Content-Type: audio/wav\r\n\r\n"
            ).encode() + wav_bytes + (
                f"\r\n--{boundary}\r\n"
                f"Content-Disposition: form-data; name=\"model\"\r\n\r\n"
                f"whisper-large-v3-turbo\r\n"
                f"--{boundary}\r\n"
                f"Content-Disposition: form-data; name=\"language\"\r\n\r\n"
                f"en\r\n"
                f"--{boundary}\r\n"
                f"Content-Disposition: form-data; name=\"prompt\"\r\n\r\n"
                f"The speaker gives short English commands to a robot: move forward, turn left, find the bottle, what can you see, stop.\r\n"
                f"--{boundary}--\r\n"
            ).encode()

            req = urllib.request.Request(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                data=body,
                headers={
                    "Authorization": f"Bearer {config.GROQ_API_KEY}",
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "User-Agent": "KANDA/1.0",
                },
            )
            resp = urllib.request.urlopen(req, timeout=10)
            data = json.loads(resp.read())
            text = data.get("text", "").strip()
            if text:
                logger.info("[groq-whisper] transcribed OK")
                return text
            return None
        except Exception as e:
            logger.warning("[groq-whisper] failed: %s", e)
            return None

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
