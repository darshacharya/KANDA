"""
KANDA Vision Module — Microphone Input with VAD
Records audio from wired earphone mic (3.5mm jack).

Improvements over v1:
  - record_vad(): stops automatically when user stops speaking (no fixed clip length)
  - Hard cap (VAD_MAX_SEC) prevents infinite recording
  - has_speech() RMS check still available for quick silence rejection

Setup:
    sudo apt install python3-pyaudio portaudio19-dev
    # or: pip install --break-system-packages pyaudio

Test standalone:
    python3 mic.py
"""

import base64
import io
import logging
import struct
import time
import wave
from collections import deque
from typing import Optional

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SAMPLE_RATE  = 16000
SAMPLE_RATES_TO_TRY = [16000, 44100, 48000, 8000]
CHANNELS     = 1
CHUNK        = 1024        # ~64ms per chunk at 16kHz
FORMAT_WIDTH = 2           # 16-bit


class Microphone:
    """Records audio from earphone mic via 3.5mm jack."""

    def __init__(self):
        self._pyaudio      = None
        self._device_index: Optional[int] = None

    def start(self) -> None:
        global SAMPLE_RATE
        import pyaudio
        self._pyaudio = pyaudio.PyAudio()
        self._device_index = self._find_input_device()
        if self._device_index is not None:
            logger.info("Mic ready: device index %d", self._device_index)
        else:
            logger.info("Mic ready: using default input device")
        # Find a working sample rate for this device
        for rate in SAMPLE_RATES_TO_TRY:
            try:
                test_stream = self._pyaudio.open(
                    format=pyaudio.paInt16, channels=CHANNELS,
                    rate=rate, input=True,
                    input_device_index=self._device_index,
                    frames_per_buffer=CHUNK,
                )
                test_stream.close()
                SAMPLE_RATE = rate
                logger.info("Mic sample rate: %d Hz", rate)
                return
            except Exception:
                continue
        logger.warning("Mic: no supported sample rate found, defaulting to %d", SAMPLE_RATE)

    # ── Device selection ───────────────────────────────────────────────────────

    def _find_input_device(self) -> Optional[int]:
        """Find a USB or 3.5mm audio input device on Pi."""
        candidates = []
        for i in range(self._pyaudio.get_device_count()):
            info = self._pyaudio.get_device_info_by_index(i)
            if info["maxInputChannels"] > 0:
                name = info["name"].lower()
                # Prefer USB mic, then 3.5mm jack, then any input
                if "usb" in name or "pnp" in name:
                    logger.info("Found input device: [%d] %s", i, info["name"])
                    return i
                if "bcm" in name or "headphone" in name or "audio" in name:
                    candidates.append((i, info["name"]))
        if candidates:
            idx, name = candidates[0]
            logger.info("Found input device: [%d] %s", idx, name)
            return idx
        return None

    # ── VAD recording (preferred) ──────────────────────────────────────────────

    def record_vad(self) -> Optional[bytes]:
        """
        Record until user stops speaking.

        Uses a rolling window to detect silence. Stops when
        VAD_SILENCE_SEC of consecutive silence is detected, or
        VAD_MAX_SEC total recording time reached.

        Returns WAV bytes, or None on failure.
        """
        import pyaudio

        try:
            stream = self._pyaudio.open(
                format=pyaudio.paInt16,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                input=True,
                input_device_index=self._device_index,
                frames_per_buffer=CHUNK,
            )

            frames: list[bytes] = []
            silence_chunks = 0
            speech_started = False
            started_at = time.time()

            # How many consecutive silent chunks = VAD_SILENCE_SEC
            silence_limit = int(config.VAD_SILENCE_SEC * SAMPLE_RATE / CHUNK)
            # Max total chunks
            max_chunks = int(config.VAD_MAX_SEC * SAMPLE_RATE / CHUNK)

            logger.info("Listening (VAD, max=%.0fs)...", config.VAD_MAX_SEC)

            for _ in range(max_chunks):
                data = stream.read(CHUNK, exception_on_overflow=False)
                frames.append(data)

                rms = self._chunk_rms(data)

                if rms > config.VAD_SPEECH_RMS:
                    speech_started = True
                    silence_chunks = 0
                else:
                    if speech_started:
                        silence_chunks += 1
                        if silence_chunks >= silence_limit:
                            logger.info("VAD: speech ended (%.1fs)", time.time() - started_at)
                            break

            stream.stop_stream()
            stream.close()

            if not frames:
                return None

            wav_bytes = self._frames_to_wav(frames)
            has_voice = self.has_speech(wav_bytes)
            logger.info("Recorded: %d bytes WAV (%s)",
                        len(wav_bytes), "speech" if has_voice else "silence")
            return wav_bytes

        except Exception as exc:
            logger.error("VAD recording failed: %s", exc)
            return None

    # ── Fixed-duration recording (fallback) ───────────────────────────────────

    def record(self, duration_sec: float = 4.0) -> Optional[bytes]:
        """Record for a fixed duration. Use record_vad() for better results."""
        import pyaudio

        try:
            stream = self._pyaudio.open(
                format=pyaudio.paInt16,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                input=True,
                input_device_index=self._device_index,
                frames_per_buffer=CHUNK,
            )

            logger.info("Recording for %.1fs...", duration_sec)
            frames = []
            for _ in range(int(SAMPLE_RATE / CHUNK * duration_sec)):
                data = stream.read(CHUNK, exception_on_overflow=False)
                frames.append(data)

            stream.stop_stream()
            stream.close()

            wav_bytes = self._frames_to_wav(frames)
            logger.info("Recorded: %d bytes WAV", len(wav_bytes))
            return wav_bytes

        except Exception as exc:
            logger.error("Recording failed: %s", exc)
            return None

    # ── Utilities ─────────────────────────────────────────────────────────────

    def _chunk_rms(self, data: bytes) -> float:
        """Calculate RMS amplitude of a raw PCM chunk."""
        count = len(data) // 2
        if count == 0:
            return 0.0
        samples = struct.unpack(f"<{count}h", data)
        return (sum(s * s for s in samples) / count) ** 0.5

    def _frames_to_wav(self, frames: list[bytes]) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(FORMAT_WIDTH)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(b"".join(frames))
        return buf.getvalue()

    def has_speech(self, wav_bytes: bytes, threshold: int = None) -> bool:
        """Quick check if WAV contains speech (not pure silence)."""
        t = threshold if threshold is not None else config.VAD_SPEECH_RMS
        raw = wav_bytes[44:]   # skip WAV header
        if len(raw) < 100:
            return False
        count = len(raw) // 2
        samples = struct.unpack(f"<{count}h", raw)
        rms = (sum(s * s for s in samples) / count) ** 0.5
        return rms > t

    def record_base64(self, duration_sec: float = 4.0) -> Optional[str]:
        wav = self.record(duration_sec)
        if wav is None:
            return None
        return base64.b64encode(wav).decode("utf-8")

    def record_vad_base64(self) -> Optional[str]:
        wav = self.record_vad()
        if wav is None:
            return None
        return base64.b64encode(wav).decode("utf-8")

    def stop(self) -> None:
        if self._pyaudio:
            self._pyaudio.terminate()
            self._pyaudio = None
        logger.info("Mic stopped")


def list_audio_devices():
    import pyaudio
    pa = pyaudio.PyAudio()
    print("Audio input devices:")
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if info["maxInputChannels"] > 0:
            print(f"  [{i}] {info['name']} (channels={info['maxInputChannels']})")
    pa.terminate()


if __name__ == "__main__":
    print("=== Microphone VAD Test ===")
    print()

    list_audio_devices()
    print()

    mic = Microphone()
    mic.start()

    print("Speak into your earphone mic — recording stops automatically when you pause...")
    wav = mic.record_vad()

    if wav:
        print(f"Recorded: {len(wav)} bytes")
        print(f"Speech detected: {mic.has_speech(wav)}")
        with open("test_vad.wav", "wb") as f:
            f.write(wav)
        print("Saved: test_vad.wav")
    else:
        print("ERROR: Recording failed")

    mic.stop()
    print("\nDone.")
