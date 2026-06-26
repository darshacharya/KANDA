"""Typed event system with async queue hub."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    COMMAND = "command"
    RESPONSE = "response"
    SENSOR_UPDATE = "sensor_update"
    STATE_CHANGE = "state_change"
    SPEECH_REQUEST = "speech_request"
    NOTIFICATION = "notification"
    OBSTACLE = "obstacle"
    CANCEL = "cancel"
    SHUTDOWN = "shutdown"


@dataclass
class Event:
    type: EventType
    data: dict[str, Any] = field(default_factory=dict)
    source: str = "system"
    timestamp: float = field(default_factory=time.time)


@dataclass
class CommandEvent(Event):
    """A user command from any input source."""
    type: EventType = EventType.COMMAND
    text: str = ""
    chat_id: int | None = None


@dataclass
class ResponseEvent(Event):
    """An AI response to broadcast to clients."""
    type: EventType = EventType.RESPONSE
    text: str = ""
    image_b64: str = ""


Handler = Callable[[Event], Coroutine[Any, Any, None]]


class EventBus:
    """Central async event dispatcher."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers: dict[EventType, list[Handler]] = {}
        self._running = False

    def subscribe(self, event_type: EventType, handler: Handler) -> None:
        self._subscribers.setdefault(event_type, []).append(handler)

    async def publish(self, event: Event) -> None:
        await self._queue.put(event)

    def publish_nowait(self, event: Event) -> None:
        self._queue.put_nowait(event)

    async def run(self) -> None:
        """Main dispatch loop — run as a task."""
        self._running = True
        logger.info("[bus] event loop started")
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            handlers = self._subscribers.get(event.type, [])
            for handler in handlers:
                try:
                    await handler(event)
                except Exception:
                    logger.exception(f"[bus] handler error for {event.type}")
            self._queue.task_done()

    async def stop(self) -> None:
        self._running = False
        logger.info("[bus] stopped")
