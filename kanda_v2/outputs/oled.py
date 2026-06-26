"""OLED state display — sends state to ESP32 which renders on SSD1306."""

from __future__ import annotations

import logging

from event_bus import EventBus, Event, EventType

logger = logging.getLogger(__name__)


class OledDisplay:
    """Syncs robot state to the ESP32's OLED display via the serial state field."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._current_state = "idle"
        bus.subscribe(EventType.STATE_CHANGE, self._on_state_change)

    async def _on_state_change(self, event: Event) -> None:
        new_state = event.data.get("new", "idle")
        self._current_state = new_state
        # The OLED is updated by including "state" in every serial command
        # sent to ESP32 — the firmware renders it. No separate command needed.
        logger.debug(f"[oled] state → {new_state}")
