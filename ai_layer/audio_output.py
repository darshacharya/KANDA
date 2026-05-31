"""
KANDA AI Layer — Audio Output Module
Text-to-speech output via Bluetooth speaker connection.

Handles TTS synthesis and playback through the Pi's Bluetooth audio sink.
The Bluetooth speaker must be paired and connected before running KANDA
(see setup instructions below).

Bluetooth Setup (one-time):
    1. sudo bluetoothctl
    2. power on
    3. agent on
    4. scan on
    5. pair XX:XX:XX:XX:XX:XX
    6. trust XX:XX:XX:XX:XX:XX
    7. connect XX:XX:XX:XX:XX:XX
    8. exit

    Verify: pactl list sinks short (should show bluez_sink.XX_XX_...)

This module uses pyttsx3 (offline, no internet needed) by default for low
latency. Optionally falls back to gTTS + pygame if pyttsx3 is unavailable.

Usage:
    from audio_output import Speaker
    speaker = Speaker()
    speaker.speak("I can see a table ahead with a cup on it.")
"""

import logging
import os
import queue
import subprocess
import tempfile
import threading
import time
from typing import Optional

import config

logger = logging.getLogger(__name__)


class Speaker:
    """Text-to-speech output routed to Bluetooth speaker."""

    def __init__(self, engine: str = None):
        """
        Args:
            engine: TTS engine to use — 'pyttsx3', 'gtts', or None (auto-detect)
        """
        self._engine_name = engine or config.TTS_ENGINE
        self._engine = None
        self._speech_queue: queue.Queue = queue.Queue()
        self._worker_thread: Optional[threading.Thread] = None
        self._running = False
        self._volume = config.TTS_VOLUME
        self._rate = config.TTS_RATE

    def start(self) -> None:
        """Initialize the TTS engine and start the background speech worker."""
        self._running = True
        self._worker_thread = threading.Thread(
            target=self._speech_worker, daemon=True, name="kanda-tts"
        )
        self._worker_thread.start()
        logger.info(
            "Speaker started (engine=%s, volume=%.1f, rate=%d)",
            self._engine_name,
            self._volume,
            self._rate,
        )

    def speak(self, text: str, priority: bool = False) -> None:
        """
        Queue text for speech synthesis and playback.

        Args:
            text: The text to speak
            priority: If True, clear the queue and speak this immediately
        """
        if not self._running:
            logger.warning("Speaker not started — call .start() first")
            return

        if not text or not text.strip():
            return

        if priority:
            self._clear_queue()

        self._speech_queue.put(text.strip())
        logger.debug("Queued for speech: %s", text[:60])

    def _speech_worker(self) -> None:
        """Background thread that processes the speech queue."""
        while self._running:
            try:
                text = self._speech_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            try:
                self._synthesize_and_play(text)
            except Exception as exc:
                logger.error("TTS playback failed: %s", exc)

    def _synthesize_and_play(self, text: str) -> None:
        """Synthesize speech and play through the audio output."""
        if self._engine_name == "pyttsx3":
            self._play_pyttsx3(text)
        elif self._engine_name == "gtts":
            self._play_gtts(text)
        elif self._engine_name == "espeak":
            self._play_espeak(text)
        else:
            self._play_espeak(text)

    def _play_pyttsx3(self, text: str) -> None:
        """Use pyttsx3 for offline TTS (uses espeak backend on Linux)."""
        try:
            import pyttsx3

            engine = pyttsx3.init()
            engine.setProperty("rate", self._rate)
            engine.setProperty("volume", self._volume)
            engine.say(text)
            engine.runAndWait()
            engine.stop()
        except Exception as exc:
            logger.warning("pyttsx3 failed (%s), falling back to espeak", exc)
            self._play_espeak(text)

    def _play_gtts(self, text: str) -> None:
        """Use Google TTS (requires internet) with pygame playback."""
        try:
            from gtts import gTTS
            import pygame

            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                tts = gTTS(text=text, lang="en", slow=False)
                tts.save(f.name)
                tmp_path = f.name

            if not pygame.mixer.get_init():
                pygame.mixer.init()

            pygame.mixer.music.load(tmp_path)
            pygame.mixer.music.set_volume(self._volume)
            pygame.mixer.music.play()

            while pygame.mixer.music.get_busy():
                time.sleep(0.1)

            os.unlink(tmp_path)
        except Exception as exc:
            logger.warning("gTTS failed (%s), falling back to espeak", exc)
            self._play_espeak(text)

    def _play_espeak(self, text: str) -> None:
        """
        Use espeak-ng directly via subprocess. Most reliable on Raspberry Pi OS.
        Audio routes through PulseAudio/PipeWire to the Bluetooth sink.
        """
        try:
            cmd = [
                "espeak-ng",
                "-v", config.TTS_VOICE,
                "-s", str(self._rate),
                "-a", str(int(self._volume * 100)),
                text,
            ]
            result = subprocess.run(
                cmd, capture_output=True, timeout=30
            )
            if result.returncode != 0:
                logger.warning(
                    "espeak-ng returned %d: %s",
                    result.returncode,
                    result.stderr.decode()[:200],
                )
        except FileNotFoundError:
            logger.error(
                "espeak-ng not found. Install: sudo apt install espeak-ng"
            )
        except subprocess.TimeoutExpired:
            logger.warning("espeak-ng timed out for text: %s", text[:40])

    def _clear_queue(self) -> None:
        """Drain the speech queue."""
        while not self._speech_queue.empty():
            try:
                self._speech_queue.get_nowait()
            except queue.Empty:
                break

    def stop(self) -> None:
        """Stop the speech worker thread."""
        self._running = False
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=3.0)
        logger.info("Speaker stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def queue_size(self) -> int:
        return self._speech_queue.qsize()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()


def check_bluetooth_audio() -> bool:
    """Verify a Bluetooth audio sink is connected."""
    try:
        result = subprocess.run(
            ["pactl", "list", "sinks", "short"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        sinks = result.stdout
        if "bluez" in sinks.lower():
            logger.info("Bluetooth audio sink detected")
            return True
        else:
            logger.warning(
                "No Bluetooth audio sink found. Pair your speaker first."
            )
            return False
    except FileNotFoundError:
        logger.warning("pactl not found — cannot verify Bluetooth audio")
        return False
    except Exception as exc:
        logger.warning("Bluetooth check failed: %s", exc)
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s  %(message)s")

    print("=== KANDA Audio Output Test ===")
    print()

    bt_ok = check_bluetooth_audio()
    print(f"Bluetooth audio available: {bt_ok}")
    print()

    speaker = Speaker()
    speaker.start()

    test_phrases = [
        "Hello! I am Kanda, your companion robot.",
        "I can see a clear path ahead.",
        "There seems to be an obstacle on my left side.",
    ]

    for phrase in test_phrases:
        print(f"Speaking: {phrase}")
        speaker.speak(phrase)
        time.sleep(4)

    speaker.stop()
    print("\nDone.")
