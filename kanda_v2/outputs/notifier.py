"""Fan-out notification system — sends status updates to all output channels."""

from __future__ import annotations

import logging

from config import settings
from event_bus import EventBus, Event, EventType

logger = logging.getLogger(__name__)


class Notifier:
    """Broadcasts notifications to Telegram and Web clients."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._telegram = None
        self._web_connections: list = []

    def set_telegram(self, telegram_input) -> None:
        self._telegram = telegram_input

    async def notify(self, message: str) -> None:
        logger.info(f"[notify] {message}")

        if self._telegram and settings.telegram_owner_id:
            await self._telegram.broadcast(message)

        # Web clients get updates via the bus → WebSocket broadcast
        await self._bus.publish(Event(
            type=EventType.NOTIFICATION,
            data={"message": message},
        ))

    async def notify_photo(self, photo_bytes: bytes, caption: str = "") -> None:
        if self._telegram and settings.telegram_owner_id:
            await self._telegram.send_photo(
                settings.telegram_owner_id, photo_bytes, caption
            )
