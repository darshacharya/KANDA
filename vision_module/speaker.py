"""
KANDA Vision Module — Audio Output
Speaks text through Bluetooth speaker using espeak-ng.

Changes from v1:
  - speak_blocking(): blocks until speech is done (needed before opening mic)
  - interrupt(): kills current espeak process immediately
  - 10s subprocess timeout (kills hung espeak)
  - is_speaking property for state machine checks

Bluetooth speaker must be paired and connected before running.

Test standalone:
    python3 speaker.py
"""

import logging
import queue
import subprocess
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
        self._speaking = threading.Event()   # set while TTS subprocess running
        self._proc: Optional[subprocess.Popen] = None
        self._proc_lock = threading.Lock()

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()
        logger.info("Speaker started (engine=%s, voice=%s)", config.TTS_ENGINE, config.TTS_VOICE)

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
        """Kill current speech immediately (e.g. when new command arrives)."""
        with self._proc_lock:
            if self._proc and self._proc.poll() is None:
                try:
                    self._proc.kill()
                    logger.debug("Speech interrupted")
                except Exception:
                    pass
        # Drain the queue so queued phrases don't play after interrupt
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
        """Run espeak-ng subprocess with hard timeout."""
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
    print()

    check_bluetooth()
    print()

    speaker = Speaker()
    speaker.start()

    phrases = [
        "Hello, I am Kanda.",
        "Vision language module is active.",
        "Testing interrupt...",
    ]

    for p in phrases:
        print(f"  Speaking: {p}")
        speaker.speak_blocking(p)
        time.sleep(0.5)

    # Test interrupt
    print("  Testing interrupt (will cut off after 0.5s)...")
    speaker.speak("This sentence should be cut short immediately.")
    time.sleep(0.5)
    speaker.interrupt()
    print("  Interrupted.")

    speaker.stop()
    print("\nDone.")
