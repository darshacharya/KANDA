"""Async Telegram bot input using aiogram."""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

from config import settings
from event_bus import EventBus, Event, EventType, CommandEvent

logger = logging.getLogger(__name__)


class TelegramInput:
    """Telegram bot that converts messages into CommandEvents."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._bot = None
        self._dp = None
        self._allowed_ids: set[int] = set()
        if settings.telegram_allowed_ids:
            self._allowed_ids = {
                int(x.strip()) for x in settings.telegram_allowed_ids.split(",") if x.strip()
            }

    async def run(self) -> None:
        try:
            from aiogram import Bot, Dispatcher, types
            from aiogram.filters import Command
        except ImportError:
            logger.warning("[telegram] aiogram not installed — Telegram disabled")
            return

        self._bot = Bot(token=settings.telegram_bot_token)
        self._dp = Dispatcher()
        bot = self._bot

        @self._dp.message(Command("stop"))
        async def handle_stop(message: types.Message):
            if not self._is_allowed(message.chat.id):
                return
            await self._bus.publish(Event(type=EventType.CANCEL, source="telegram"))
            await message.reply("Stopped.")

        @self._dp.message(Command("status"))
        async def handle_status(message: types.Message):
            if not self._is_allowed(message.chat.id):
                return
            await message.reply("KANDA v2 online.")

        @self._dp.message(Command("photo"))
        async def handle_photo(message: types.Message):
            if not self._is_allowed(message.chat.id):
                return
            await message.reply("Capturing...")
            try:
                from hal.camera import Camera
                from brain.intent import describe_scene
                # Use the app's camera via event
                await self._bus.publish(CommandEvent(
                    text="what do you see",
                    source="telegram",
                    chat_id=message.chat.id,
                ))
            except Exception as e:
                await message.reply(f"Error: {e}")

        @self._dp.message(Command("dance"))
        async def handle_dance(message: types.Message):
            if not self._is_allowed(message.chat.id):
                return
            await self._bus.publish(CommandEvent(text="dance", source="telegram", chat_id=message.chat.id))
            await message.reply("Dancing!")

        @self._dp.message(Command("find"))
        async def handle_find(message: types.Message):
            if not self._is_allowed(message.chat.id):
                return
            args = message.text.replace("/find", "").strip()
            if not args:
                await message.reply("Usage: /find <object>")
                return
            await self._bus.publish(CommandEvent(text=f"find {args}", source="telegram", chat_id=message.chat.id))
            await message.reply(f"Searching for: {args}")

        @self._dp.message()
        async def handle_message(message: types.Message):
            if not self._is_allowed(message.chat.id):
                return

            text = ""

            if message.voice:
                text = await self._transcribe_voice(message)
            elif message.text:
                text = message.text
            elif message.caption:
                text = message.caption

            if text:
                await self._bus.publish(CommandEvent(
                    text=text,
                    source="telegram",
                    chat_id=message.chat.id,
                ))
                await message.reply(f"Got it: {text}")

        logger.info("[telegram] bot starting")
        try:
            await self._dp.start_polling(bot)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("[telegram] polling error")
        finally:
            if bot:
                await bot.session.close()

    async def send_message(self, chat_id: int, text: str) -> None:
        if self._bot:
            try:
                await self._bot.send_message(chat_id, text)
            except Exception as e:
                logger.error(f"[telegram] send error: {e}")

    async def broadcast(self, text: str) -> None:
        if settings.telegram_owner_id and self._bot:
            await self.send_message(settings.telegram_owner_id, text)

    async def send_photo(self, chat_id: int, photo_bytes: bytes, caption: str = "") -> None:
        if self._bot:
            try:
                from aiogram.types import BufferedInputFile
                photo = BufferedInputFile(photo_bytes, filename="capture.jpg")
                await self._bot.send_photo(chat_id, photo, caption=caption)
            except Exception as e:
                logger.error(f"[telegram] photo send error: {e}")

    def _is_allowed(self, chat_id: int) -> bool:
        if not self._allowed_ids:
            return True
        return chat_id in self._allowed_ids

    async def _transcribe_voice(self, message) -> str:
        try:
            import httpx

            file_info = await self._bot.get_file(message.voice.file_id)
            file_bytes = await self._bot.download_file(file_info.file_path)
            audio_data = file_bytes.read()

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                    files={"file": ("voice.ogg", audio_data, "audio/ogg")},
                    data={"model": "whisper-large-v3-turbo", "language": "en"},
                )
                if resp.status_code == 200:
                    return resp.json().get("text", "").strip()
        except Exception:
            logger.exception("[telegram] voice transcription error")
        return ""
