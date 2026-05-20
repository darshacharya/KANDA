"""
KANDA Vision Module — Wake Word Detector
Uses openWakeWord — fully open source, no account, no API key required.

Wake phrase options (set KANDA_WAKE_WORD_MODEL in env):
  "hey_jarvis"    — say "Hey Jarvis"   (default, works out of the box)
  "alexa"         — say "Alexa"
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
    """

    def __init__(self, wake_event: threading.Event):
        self._wake_event = wake_event
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._use_oww = config.WAKE_WORD_ENABLED

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
            # Load model — built-in or custom .onnx file
            model_path = config.WAKE_WORD_MODEL
            if model_path.endswith(".onnx"):
                # Custom trained model
                oww_model = Model(wakeword_models=[model_path], inference_framework="onnx")
                wake_label = model_path.split("/")[-1].replace(".onnx", "")
            else:
                # Built-in model: "hey_jarvis", "alexa", "hey_mycroft"
                oww_model = Model(wakeword_models=[model_path], inference_framework="onnx")
                wake_label = model_path

            logger.info("openWakeWord loaded: '%s' — say the wake phrase to activate",
                        wake_label.replace("_", " ").title())
            print(f"\n[Wake Word] Say \"{wake_label.replace('_', ' ').title()}\" to wake KANDA\n")

            pa = pyaudio.PyAudio()
            audio_stream = pa.open(
                rate=OWW_SAMPLE_RATE,
                channels=1,
                format=pyaudio.paInt16,
                input=True,
                frames_per_buffer=OWW_CHUNK_FRAMES,
            )

            while not self._stop_event.is_set():
                raw = audio_stream.read(OWW_CHUNK_FRAMES, exception_on_overflow=False)
                # openWakeWord expects numpy int16 array
                pcm = np.frombuffer(raw, dtype=np.int16)

                predictions = oww_model.predict(pcm)

                # predictions is a dict: {model_name: score (0.0–1.0)}
                for name, score in predictions.items():
                    if score >= config.WAKE_WORD_SENSITIVITY:
                        logger.info("Wake word detected! model=%s score=%.2f", name, score)
                        self._wake_event.set()
                        # Brief cooldown — ignore further triggers for 2s
                        self._stop_event.wait(timeout=2.0)
                        if not self._stop_event.is_set():
                            oww_model.reset()   # clear internal state
                        break

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
        """Press Enter in terminal to simulate wake word. No setup needed."""
        print("\n[Wake Word] Keyboard mode — press Enter to wake KANDA\n")
        while not self._stop_event.is_set():
            try:
                input()
                if not self._stop_event.is_set():
                    logger.info("Wake word triggered (keyboard)")
                    self._wake_event.set()
            except EOFError:
                break


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
