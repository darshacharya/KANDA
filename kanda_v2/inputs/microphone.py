"""Microphone input — wake word detection + VAD recording + Whisper ASR."""

from __future__ import annotations

import asyncio
import logging
import struct
import tempfile
import wave
from collections import deque

from config import settings
from event_bus import EventBus, CommandEvent

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1280  # 80ms at 16kHz
RATE_16K = 16000


class MicrophoneInput:
    """Wake word → record → transcribe → publish CommandEvent."""

    def __init__(self, bus: EventBus, speaker, app=None) -> None:
        self._bus = bus
        self._speaker = speaker
        self._app = app
        self._running = False

    async def run(self) -> None:
        self._running = True
        logger.info("[mic] starting wake word listener")

        while self._running:
            try:
                triggered = await asyncio.to_thread(self._wait_for_wake_word)
                if not triggered or not self._running:
                    continue

                if self._speaker.is_speaking:
                    await asyncio.sleep(0.5)
                    continue

                if self._app and hasattr(self._app, 'state_machine'):
                    from state_machine import State
                    current = self._app.state_machine.state
                    if current in (State.SEARCHING, State.ACTING):
                        logger.debug("[mic] ignoring wake word — robot busy")
                        continue

                logger.info("[mic] wake word detected — listening")
                await self._speaker.speak_blocking("Yes?")
                await asyncio.sleep(0.8)
                audio_path = await asyncio.to_thread(self._record_vad)
                if not audio_path:
                    await self._speaker.speak_blocking("I didn't hear anything.")
                    continue

                transcript = await self._transcribe(audio_path)
                if transcript:
                    logger.info(f"[mic] transcript: {transcript!r}")
                    await self._bus.publish(CommandEvent(
                        text=transcript,
                        source="microphone",
                    ))
                else:
                    await self._speaker.speak_blocking("Sorry, I couldn't understand. Try again.")

            except Exception:
                logger.exception("[mic] error in listen loop")
                await asyncio.sleep(2)

    def _wait_for_wake_word(self) -> bool:
        try:
            import pyaudio
            import numpy as np
            from openwakeword.model import Model

            model_name = settings.wake_word_model
            if model_name.endswith(".onnx"):
                model = Model(wakeword_model_paths=[model_name])
                wake_label = model_name.replace(".onnx", "")
            else:
                model = Model()
                wake_label = model_name

            pa = pyaudio.PyAudio()
            stream = None
            actual_rate = RATE_16K

            for rate in (16000, 44100, 48000):
                try:
                    stream = pa.open(
                        format=pyaudio.paInt16,
                        channels=1,
                        rate=rate,
                        input=True,
                        frames_per_buffer=CHUNK_SIZE,
                    )
                    actual_rate = rate
                    break
                except Exception:
                    continue

            if not stream:
                logger.error("[mic] cannot open audio stream")
                return False

            logger.info(f"[mic] wake word active: '{wake_label}' (mic rate: {actual_rate}Hz)")

            try:
                while self._running:
                    raw = stream.read(CHUNK_SIZE, exception_on_overflow=False)
                    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
                    pcm = pcm * 2.0
                    pcm = np.clip(pcm, -32768, 32767).astype(np.int16)

                    if actual_rate != RATE_16K:
                        indices = np.linspace(0, len(pcm) - 1, CHUNK_SIZE).astype(int)
                        pcm = pcm[indices]

                    predictions = model.predict(pcm)
                    score = predictions.get(wake_label, 0.0)
                    if score >= settings.wake_sensitivity:
                        logger.info(f"[mic] wake word detected! score={score:.2f}")
                        return True
            finally:
                stream.stop_stream()
                stream.close()
                pa.terminate()

        except ImportError as e:
            logger.warning(f"[mic] wake word unavailable ({e}), using keyboard fallback")
            import sys
            sys.stdin.readline()
            return True
        except Exception:
            logger.exception("[mic] wake word error")
            return False

    def _record_vad(self) -> str | None:
        try:
            import pyaudio

            pa = pyaudio.PyAudio()
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=RATE_16K,
                input=True,
                frames_per_buffer=CHUNK_SIZE,
            )

            frames: list[bytes] = []
            silence_chunks = 0
            max_chunks = int(settings.vad_max_sec * RATE_16K / CHUNK_SIZE)
            silence_limit = int(settings.vad_silence_sec * RATE_16K / CHUNK_SIZE)
            started = False
            max_rms = 0

            for _ in range(max_chunks):
                data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
                rms = self._rms(data)
                max_rms = max(max_rms, rms)

                if rms > settings.vad_threshold:
                    started = True
                    silence_chunks = 0
                    frames.append(data)
                elif started:
                    silence_chunks += 1
                    frames.append(data)
                    if silence_chunks >= silence_limit:
                        break

            stream.stop_stream()
            stream.close()
            pa.terminate()

            if not frames:
                logger.warning(f"[mic] VAD captured no speech (max_rms={max_rms:.0f}, threshold={settings.vad_threshold})")
                return None

            duration_sec = len(frames) * CHUNK_SIZE / RATE_16K
            logger.info(f"[mic] recorded {duration_sec:.1f}s of speech (max_rms={max_rms:.0f}, frames={len(frames)})")

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                wf = wave.open(f.name, "wb")
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(RATE_16K)
                wf.writeframes(b"".join(frames))
                wf.close()
                return f.name

        except Exception:
            logger.exception("[mic] VAD recording error")
            return None

    async def _transcribe(self, audio_path: str) -> str | None:
        try:
            import httpx
            from pathlib import Path

            audio_data = Path(audio_path).read_bytes()

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                    files={"file": ("audio.wav", audio_data, "audio/wav")},
                    data={"model": "whisper-large-v3-turbo", "language": "en"},
                )
                if resp.status_code == 200:
                    text = resp.json().get("text", "").strip()
                    # Filter out Whisper hallucinations from noisy audio
                    if not text or len(text) < 3 or text in (
                        ".", ",", "Thank you.", "Thanks.", "Thanks for watching.",
                        "You", "I", "Bye.", "Bye", "you", "Thank you",
                        "Subtitles by the Amara.org community",
                    ):
                        logger.warning(f"[mic] filtered hallucination: {text!r}")
                        return None
                    return text
                else:
                    logger.error(f"[mic] Whisper API error: {resp.status_code}")
                    return None
        except Exception:
            logger.exception("[mic] transcription error")
            return None

    @staticmethod
    def _rms(data: bytes) -> float:
        samples = struct.unpack(f"<{len(data)//2}h", data)
        if not samples:
            return 0
        return (sum(s * s for s in samples) / len(samples)) ** 0.5

    @staticmethod
    def _resample(pcm: bytes, from_rate: int, to_rate: int) -> bytes:
        samples = struct.unpack(f"<{len(pcm)//2}h", pcm)
        ratio = to_rate / from_rate
        new_len = int(len(samples) * ratio)
        resampled = []
        for i in range(new_len):
            idx = int(i / ratio)
            idx = min(idx, len(samples) - 1)
            resampled.append(samples[idx])
        return struct.pack(f"<{len(resampled)}h", *resampled)
