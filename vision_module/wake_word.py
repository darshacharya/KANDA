"""
KANDA Vision Module — Wake Word Detector
Uses openWakeWord — fully open source, no account, no API key required.

Wake phrase options (set KANDA_WAKE_WORD_MODEL in env):
  "hey_jarvis"    — say "Hey Jarvis"   (default, works out of the box)
  "alexa"         — say "Alexa"
  "hey_marvin"    — say "Hey Marvin"
  "hey_mycroft"   — say "Hey Mycroft"
  custom          — record ~5 clips and train (see below)

Fallback (KANDA_WAKE_WORD=0): press Enter in terminal — no setup needed.

Setup:
    pip install openwakeword pyaudio

Train a custom "Hey Kanda" model (optional, ~10 min):
    pip install openwakeword[train]
    oww-train --phrase "hey kanda" --output hey_kanda.onnx
    export KANDA_WAKE_WORD_MODEL=hey_kanda.onnx

Test standalone:
    python3 wake_word.py
"""

import logging
import struct
import threading
import time
import numpy as np
from typing import Optional

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# openWakeWord needs 16kHz mono, 80ms chunks (1280 samples)
OWW_SAMPLE_RATE  = 16000
OWW_CHUNK_FRAMES = 1280


class WakeWordDetector:
    """
    Listens continuously for a wake word using openWakeWord (offline, ~8% CPU on Pi 4).
    Fires wake_event when wake word is heard.
    Falls back to keyboard Enter if openWakeWord is not available or disabled.
    Pauses mic when wake word fires so VAD can use it.
    """

    def __init__(self, wake_event: threading.Event):
        self._wake_event = wake_event
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()  # Set = paused, Clear = running
        self._use_oww = config.WAKE_WORD_ENABLED

    def pause(self) -> None:
        """Pause wake word detection (release mic for VAD)."""
        self._pause_event.set()

    def resume(self) -> None:
        """Resume wake word detection after VAD is done."""
        self._pause_event.clear()

    def start(self) -> None:
        if self._use_oww:
            self._thread = threading.Thread(
                target=self._oww_loop,
                name="wake-word",
                daemon=True,
            )
            logger.info("Wake word: openWakeWord active (model=%s)", config.WAKE_WORD_MODEL)
        else:
            self._thread = threading.Thread(
                target=self._keyboard_fallback_loop,
                name="wake-word-kb",
                daemon=True,
            )
            logger.info("Wake word: keyboard fallback (press Enter to wake)")

        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)

    # ── openWakeWord listener ─────────────────────────────────────────────────

    def _oww_loop(self) -> None:
        try:
            from openwakeword.model import Model
            import pyaudio
        except ImportError as e:
            logger.error("openWakeWord not installed: %s", e)
            logger.error("Run: pip install openwakeword pyaudio")
            logger.info("Falling back to keyboard mode")
            self._keyboard_fallback_loop()
            return

        pa            = None
        audio_stream  = None
        oww_model     = None

        try:
            # Load model — custom .onnx or fall back to built-in
            model_path = config.WAKE_WORD_MODEL
            wake_label = model_path.split("/")[-1].replace(".onnx", "") if model_path.endswith(".onnx") else model_path

            oww_model = None
            # v0.4.0 API: wakeword_model_paths=[]
            import os
            if model_path.endswith(".onnx") and os.path.isfile(model_path):
                try:
                    oww_model = Model(wakeword_model_paths=[model_path])
                except Exception as e:
                    logger.warning("Custom model '%s' failed: %s", model_path, e)

            # Fall back to loading all default models (includes hey_jarvis, alexa, hey_marvin, hey_mycroft)
            if oww_model is None:
                oww_model = Model()
                builtin_models = {k for k in oww_model.models.keys()}
                # Match configured model to available built-in models
                matched = False
                for name in builtin_models:
                    if model_path in name or name.startswith(model_path):
                        wake_label = name
                        matched = True
                        break
                if not matched:
                    wake_label = "hey_jarvis"
                    logger.warning("Model '%s' not found in built-ins %s, falling back to hey_jarvis", model_path, builtin_models)
                logger.info("Using built-in wake word: '%s' (say '%s')", wake_label, wake_label.replace("_", " ").title())

            logger.info("openWakeWord loaded: '%s' — say the wake phrase to activate",
                        wake_label.replace("_", " ").title())
            print(f"\n[Wake Word] Say \"{wake_label.replace('_', ' ').title()}\" to wake KANDA\n")

            pa = pyaudio.PyAudio()
            # Find a sample rate the mic supports
            mic_rate = OWW_SAMPLE_RATE
            for try_rate in [16000, 44100, 48000]:
                try:
                    test = pa.open(rate=try_rate, channels=1, format=pyaudio.paInt16,
                                   input=True, frames_per_buffer=OWW_CHUNK_FRAMES)
                    test.close()
                    mic_rate = try_rate
                    break
                except Exception:
                    continue
            logger.info("Wake word mic rate: %d Hz (OWW needs %d Hz)", mic_rate, OWW_SAMPLE_RATE)

            # Calculate how many frames to read per iteration to get ~80ms
            read_frames = int(mic_rate * OWW_CHUNK_FRAMES / OWW_SAMPLE_RATE)
            audio_stream = pa.open(
                rate=mic_rate,
                channels=1,
                format=pyaudio.paInt16,
                input=True,
                frames_per_buffer=read_frames,
            )

            while not self._stop_event.is_set():
                # If paused (mic needed by VAD), close stream and wait
                if self._pause_event.is_set():
                    audio_stream.stop_stream()
                    audio_stream.close()
                    logger.info("[wake] mic released for VAD")
                    while self._pause_event.is_set() and not self._stop_event.is_set():
                        time.sleep(0.1)
                    if self._stop_event.is_set():
                        break
                    # Reopen stream after VAD is done
                    audio_stream = pa.open(
                        rate=mic_rate, channels=1, format=pyaudio.paInt16,
                        input=True, frames_per_buffer=read_frames)
                    oww_model.reset()
                    logger.info("[wake] mic reclaimed, listening again")
                    continue

                raw = audio_stream.read(read_frames, exception_on_overflow=False)
                pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32)

                # Boost gain — helps with low-sensitivity USB mics
                pcm = pcm * 3.0
                pcm = np.clip(pcm, -32768, 32767).astype(np.int16)

                # Resample to 16kHz if mic runs at a different rate
                if mic_rate != OWW_SAMPLE_RATE:
                    indices = np.linspace(0, len(pcm) - 1, OWW_CHUNK_FRAMES).astype(int)
                    pcm = pcm[indices]

                predictions = oww_model.predict(pcm)

                # Only trigger on the chosen wake label (ignore timer/weather/etc.)
                score = predictions.get(wake_label, 0.0)
                # Periodic debug: log score if above 0.05 (hearing something)
                if score > 0.05:
                    logger.debug("[wake] score=%.3f (threshold=%.2f)", score, config.WAKE_WORD_SENSITIVITY)
                if score >= config.WAKE_WORD_SENSITIVITY:
                    logger.info("Wake word detected! model=%s score=%.2f", wake_label, score)
                    # Release mic BEFORE firing wake event so VAD can use it
                    audio_stream.stop_stream()
                    audio_stream.close()
                    logger.info("[wake] mic released for listening")
                    self._wake_event.set()
                    # Wait until state machine is done with the mic
                    self._pause_event.set()
                    while self._pause_event.is_set() and not self._stop_event.is_set():
                        time.sleep(0.1)
                    if self._stop_event.is_set():
                        break
                    # Reopen stream
                    audio_stream = pa.open(
                        rate=mic_rate, channels=1, format=pyaudio.paInt16,
                        input=True, frames_per_buffer=read_frames)
                    oww_model.reset()
                    logger.info("[wake] mic reclaimed, listening again")


        except Exception as exc:
            logger.error("openWakeWord error: %s — falling back to keyboard", exc)
            self._keyboard_fallback_loop()
        finally:
            if audio_stream:
                try:
                    audio_stream.stop_stream()
                    audio_stream.close()
                except Exception:
                    pass
            if pa:
                try:
                    pa.terminate()
                except Exception:
                    pass

    # ── Keyboard fallback ─────────────────────────────────────────────────────

    def _keyboard_fallback_loop(self) -> None:
        """Fallback: idle and let Telegram trigger. Also accepts Enter if terminal is attached."""
        import sys, select
        print("\n[Wake Word] Headless mode — use Telegram to interact (or Enter if terminal attached)\n")
        while not self._stop_event.is_set():
            try:
                # Non-blocking check for stdin (works if terminal attached, doesn't block if headless)
                if sys.stdin.isatty():
                    ready, _, _ = select.select([sys.stdin], [], [], 1.0)
                    if ready:
                        sys.stdin.readline()
                        if not self._stop_event.is_set():
                            logger.info("Wake word triggered (keyboard)")
                            self._wake_event.set()
                else:
                    # No terminal — just sleep and let Telegram handle wake
                    self._stop_event.wait(timeout=1.0)
            except (EOFError, OSError):
                self._stop_event.wait(timeout=1.0)


if __name__ == "__main__":
    import time

    print("=== Wake Word Test ===")
    print()

    event = threading.Event()
    detector = WakeWordDetector(wake_event=event)
    detector.start()

    print("Waiting for wake word (or Enter key if openWakeWord not installed)...")
    for _ in range(120):
        if event.wait(timeout=1.0):
            print("WAKE EVENT FIRED!")
            event.clear()
            print("Cleared. Listening again...")

    detector.stop()
    print("Done.")
