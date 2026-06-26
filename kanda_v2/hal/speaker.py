"""TTS engine with async queue — never blocks the event loop."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import tempfile
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)


class Speaker:
    """Queued text-to-speech with gTTS primary and espeak-ng fallback."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._running = False
        self._proc: subprocess.Popen | None = None
        self._speaking = False

    @property
    def is_speaking(self) -> bool:
        return self._speaking

    async def speak(self, text: str) -> None:
        await self._queue.put(text)

    async def speak_blocking(self, text: str) -> None:
        await asyncio.to_thread(self._synthesize_and_play, text)

    async def interrupt(self) -> None:
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        if self._proc:
            try:
                self._proc.kill()
            except ProcessLookupError:
                pass

    async def run(self) -> None:
        """Worker loop — processes speech queue."""
        self._running = True
        logger.info("[speaker] worker started")
        while self._running:
            try:
                text = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            self._speaking = True
            await asyncio.to_thread(self._synthesize_and_play, text)
            self._speaking = False
            self._queue.task_done()

    async def stop(self) -> None:
        self._running = False
        await self.interrupt()

    def _synthesize_and_play(self, text: str) -> None:
        if not text.strip():
            return

        logger.info(f"[speaker] saying: {text[:60]}")
        if settings.tts_engine in ("gtts", "google"):
            self._gtts_speak(text)
        else:
            self._espeak_speak(text)

    def _gtts_speak(self, text: str) -> None:
        try:
            from gtts import gTTS

            tts_text = text.replace("Kanda", "\u0C95\u0C82\u0CA6")
            tts = gTTS(text=tts_text, lang=settings.gtts_lang, tld=settings.gtts_tld)

            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                tts.save(f.name)
                mp3_path = f.name

            if settings.gtts_speed != 1.0:
                sped_path = mp3_path.replace(".mp3", "_fast.mp3")
                result = subprocess.run(
                    ["sox", mp3_path, sped_path, "tempo", str(settings.gtts_speed)],
                    capture_output=True,
                )
                if result.returncode == 0:
                    mp3_path = sped_path

            self._play_audio(mp3_path)
            Path(mp3_path).unlink(missing_ok=True)
            if mp3_path.endswith("_fast.mp3"):
                Path(mp3_path.replace("_fast.mp3", ".mp3")).unlink(missing_ok=True)

        except Exception as e:
            logger.warning(f"[speaker] gTTS failed: {type(e).__name__}: {e}")
            self._espeak_speak(text)

    def _espeak_speak(self, text: str) -> None:
        try:
            clean_text = text.replace('"', '').replace("'", "").replace('\n', ' ')
            if len(clean_text) > 300:
                clean_text = clean_text[:300]
            subprocess.run(
                [
                    "espeak-ng", "-v", settings.espeak_voice,
                    "-s", str(settings.espeak_rate),
                    clean_text,
                ],
                timeout=30,
                capture_output=True,
            )
        except Exception as e:
            logger.error(f"[speaker] espeak failed: {e}")

    def _play_audio(self, filepath: str) -> None:
        import time
        time.sleep(0.15)

        players = [
            ["mpv", "--no-video", "--really-quiet", "--audio-buffer=0.5", filepath],
            ["play", "-q", filepath],
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", filepath],
            ["aplay", filepath],
        ]

        for cmd in players:
            try:
                self._proc = subprocess.Popen(
                    cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                self._proc.wait(timeout=30)
                self._proc = None
                return
            except FileNotFoundError:
                continue
            except subprocess.TimeoutExpired:
                if self._proc:
                    self._proc.kill()
                return

        logger.error("[speaker] no audio player found")
