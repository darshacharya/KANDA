"""
KANDA Vision Module — Audio Output (Google TTS + espeak-ng fallback)

Primary:  gTTS (Google Text-to-Speech) — natural female voice, free, no API key
Fallback: espeak-ng — offline, robotic but works without internet

Bluetooth speaker must be paired and connected before running.

Test standalone:
    python3 speaker.py
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


class Speaker:
    def __init__(self):
        self._queue: queue.Queue = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._speaking = threading.Event()
        self._proc: Optional[subprocess.Popen] = None
        self._proc_lock = threading.Lock()
        self._use_gtts = False

    def start(self) -> None:
        self._running = True

        if config.TTS_ENGINE in ("gtts", "google", "elevenlabs"):
            try:
                from gtts import gTTS  # noqa: F401
                self._use_gtts = True
                logger.info("Speaker started (Google TTS, lang=%s, tld=%s)",
                            config.GTTS_LANG, config.GTTS_TLD)
            except ImportError:
                logger.warning("gTTS not installed — falling back to espeak-ng")
                logger.warning("  Install: pip install gTTS")
                self._use_gtts = False
        else:
            logger.info("Speaker started (espeak-ng, voice=%s)", config.TTS_VOICE)

        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def speak(self, text: str) -> None:
        """Queue text for speech. Returns immediately (non-blocking)."""
        if not self._running or not text:
            return
        self._queue.put(text.strip())

    def speak_blocking(self, text: str) -> None:
        """Speak and block until finished. Use before opening mic."""
        if not self._running or not text:
            return
        self._say(text.strip())

    def interrupt(self) -> None:
        """Kill current speech immediately."""
        with self._proc_lock:
            if self._proc and self._proc.poll() is None:
                try:
                    self._proc.kill()
                    logger.debug("Speech interrupted")
                except Exception:
                    pass
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    @property
    def is_speaking(self) -> bool:
        return self._speaking.is_set()

    def wait_done(self, timeout: float = 10.0) -> None:
        """Block until current speech finishes."""
        self._speaking.wait(timeout=timeout)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _worker(self) -> None:
        while self._running:
            try:
                text = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            self._say(text)

    def _say(self, text: str) -> None:
        """Route to gTTS or espeak-ng."""
        if self._use_gtts:
            self._say_gtts(text)
        else:
            self._say_espeak(text)

    def _say_gtts(self, text: str) -> None:
        """Generate speech via Google TTS. Uses English (Indian) voice with Kanda in Kannada."""
        self._speaking.set()
        tmp_path = None
        sped_path = None
        try:
            from gtts import gTTS
            import re

            # Replace "Kanda" with Kannada script for correct pronunciation
            processed = re.sub(r'(?i)\bkanda\b', 'ಕಂದ', text)

            # Use English (Indian accent) so numbers/text are spoken in English
            # Only the word ಕಂದ will get Kannada pronunciation naturally
            tts = gTTS(text=processed, lang="en", tld="co.in", slow=False)
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                tts.save(f.name)
                tmp_path = f.name

            # Speed up audio if configured (using sox)
            speed = getattr(config, 'GTTS_SPEED', 1.0)
            if speed != 1.0:
                sped_path = tmp_path + ".fast.mp3"
                result = subprocess.run(
                    ["sox", tmp_path, sped_path, "tempo", str(speed)],
                    capture_output=True
                )
                if result.returncode == 0:
                    os.unlink(tmp_path)
                    tmp_path = sped_path
                else:
                    sped_path = None

            self._play_audio(tmp_path)

        except Exception as exc:
            logger.warning("gTTS failed: %s — falling back to espeak", exc)
            self._say_espeak(text)
        finally:
            self._speaking.clear()
            with self._proc_lock:
                self._proc = None
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def _play_audio(self, filepath: str) -> None:
        """Play an audio file using available system player."""
        players = [
            ["play", "-q", filepath],
            ["mpv", "--no-video", "--really-quiet", filepath],
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", filepath],
            ["aplay", filepath],
        ]

        for cmd in players:
            try:
                with self._proc_lock:
                    self._proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                self._proc.wait(timeout=30)
                return
            except FileNotFoundError:
                continue
            except subprocess.TimeoutExpired:
                logger.warning("Audio playback timed out")
                with self._proc_lock:
                    if self._proc:
                        self._proc.kill()
                return

        logger.error("No audio player found. Install one: sudo apt install mpv")

    def _say_espeak(self, text: str) -> None:
        """Fallback: espeak-ng subprocess (offline, robotic)."""
        self._speaking.set()
        try:
            cmd = [
                "espeak-ng",
                "-v", config.TTS_VOICE,
                "-s", str(config.TTS_RATE),
                "-a", str(int(config.TTS_VOLUME * 100)),
                text,
            ]

            with self._proc_lock:
                self._proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

            try:
                self._proc.wait(timeout=config.TTS_TIMEOUT_SEC)
            except subprocess.TimeoutExpired:
                logger.warning("espeak-ng timed out — killing")
                with self._proc_lock:
                    if self._proc:
                        self._proc.kill()

        except FileNotFoundError:
            logger.error("espeak-ng not found. Install: sudo apt install espeak-ng")
        except Exception as exc:
            logger.error("Speech error: %s", exc)
        finally:
            self._speaking.clear()
            with self._proc_lock:
                self._proc = None

    def stop(self) -> None:
        self.interrupt()
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        logger.info("Speaker stopped")

    @property
    def is_running(self) -> bool:
        return self._running


def check_bluetooth() -> bool:
    """Check if a Bluetooth audio sink is connected."""
    try:
        result = subprocess.run(
            ["pactl", "list", "sinks", "short"],
            capture_output=True, text=True, timeout=5,
        )
        if "bluez" in result.stdout.lower():
            print("Bluetooth speaker: CONNECTED")
            return True
        else:
            print("Bluetooth speaker: NOT FOUND")
            print("  Pair with: bluetoothctl → connect XX:XX:XX:XX:XX:XX")
            return False
    except FileNotFoundError:
        print("pactl not available — cannot check Bluetooth")
        return False


if __name__ == "__main__":
    print("=== Speaker Test ===")
    print(f"Engine: {config.TTS_ENGINE}")
    print()

    check_bluetooth()
    print()

    speaker = Speaker()
    speaker.start()

    phrases = [
        "Hello, I am Kanda, your multimodal AI assistant.",
        "I can see through my camera and understand what's around me.",
        "Ask me anything, or send me on a mission.",
    ]

    for p in phrases:
        print(f"  Speaking: {p}")
        speaker.speak_blocking(p)
        time.sleep(0.3)

    # Test interrupt
    print("  Testing interrupt (will cut off after 0.5s)...")
    speaker.speak("This sentence should be cut short immediately by the interrupt function.")
    time.sleep(0.5)
    speaker.interrupt()
    print("  Interrupted.")

    speaker.stop()
    print("\nDone.")
