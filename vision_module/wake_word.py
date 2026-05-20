"""
KANDA Vision Module — Wake Word Detector
Listens continuously for "Hey Kanda" using Porcupine (offline, ~5% CPU on Pi 4).
When detected, sets a threading.Event so the main loop can transition to LISTENING.

Fallback mode (KANDA_WAKE_WORD=0): pressing Enter triggers the wake event instead
of Porcupine — useful for testing without a Picovoice access key.

Setup:
    pip install pvporcupine
    Get free access key at: https://console.picovoice.ai/
    export PORCUPINE_ACCESS_KEY=your_key

Test standalone:
    export PORCUPINE_ACCESS_KEY=your_key
    python3 wake_word.py
"""

import logging
import struct
import threading
from typing import Optional

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


class WakeWordDetector:
    """
    Runs Porcupine in a background thread.
    Fires wake_event whenever "Hey Kanda" (or fallback Enter key) is heard.
    State machine checks wake_event.is_set() and calls clear() after consuming it.
    """

    def __init__(self, wake_event: threading.Event):
        self._wake_event = wake_event
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._use_porcupine = config.WAKE_WORD_ENABLED and bool(config.WAKE_WORD_KEY)

    def start(self) -> None:
        if self._use_porcupine:
            self._thread = threading.Thread(
                target=self._porcupine_loop,
                name="wake-word",
                daemon=True,
            )
            logger.info("Wake word: Porcupine active (sensitivity=%.1f)", config.WAKE_WORD_SENSITIVITY)
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

    # ── Porcupine listener ─────────────────────────────────────────────────────

    def _porcupine_loop(self) -> None:
        try:
            import pvporcupine
            import pyaudio
        except ImportError as e:
            logger.error("Missing library: %s — falling back to keyboard", e)
            self._keyboard_fallback_loop()
            return

        porcupine = None
        audio_stream = None
        pa = None

        try:
            # NOTE: "hey siri" is the closest built-in Porcupine keyword.
            # To use a custom "Hey Kanda" keyword:
            #   1. Train one free at https://console.picovoice.ai/ppn
            #   2. Download the .ppn file for Raspberry Pi
            #   3. Replace keywords= with:
            #        keyword_paths=["hey-kanda_raspberry-pi.ppn"]
            #      and remove the keywords= line.
            print("[Wake Word] NOTICE: using 'Hey Siri' as wake phrase "
                  "(train 'Hey Kanda' at console.picovoice.ai for custom wake word)")
            porcupine = pvporcupine.create(
                access_key=config.WAKE_WORD_KEY,
                keywords=["hey siri"],
                sensitivities=[config.WAKE_WORD_SENSITIVITY],
            )

            pa = pyaudio.PyAudio()
            audio_stream = pa.open(
                rate=porcupine.sample_rate,
                channels=1,
                format=pyaudio.paInt16,
                input=True,
                frames_per_buffer=porcupine.frame_length,
            )

            logger.info("Porcupine listening on mic (frame_len=%d)", porcupine.frame_length)

            while not self._stop_event.is_set():
                pcm = audio_stream.read(porcupine.frame_length, exception_on_overflow=False)
                pcm = struct.unpack_from("h" * porcupine.frame_length, pcm)

                keyword_index = porcupine.process(pcm)
                if keyword_index >= 0:
                    logger.info("Wake word detected!")
                    self._wake_event.set()

        except Exception as exc:
            logger.error("Porcupine error: %s — falling back to keyboard", exc)
            self._keyboard_fallback_loop()
        finally:
            if audio_stream:
                audio_stream.stop_stream()
                audio_stream.close()
            if pa:
                pa.terminate()
            if porcupine:
                porcupine.delete()

    # ── Keyboard fallback ──────────────────────────────────────────────────────

    def _keyboard_fallback_loop(self) -> None:
        """Press Enter in terminal to simulate wake word. Useful for testing."""
        print("\n[Wake Word] Keyboard fallback active — press Enter to wake KANDA\n")
        while not self._stop_event.is_set():
            try:
                input()   # blocks until Enter
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

    print("Waiting for wake word (or Enter key)...")
    for _ in range(60):
        if event.wait(timeout=1.0):
            print("WAKE EVENT FIRED!")
            event.clear()
            print("Cleared. Waiting again...")

    detector.stop()
    print("Done.")
