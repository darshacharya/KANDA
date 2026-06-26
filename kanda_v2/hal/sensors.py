"""Sensor fusion — parses ESP32 telemetry and provides obstacle awareness."""

from __future__ import annotations

import logging
import re
import time

from config import settings
from event_bus import EventBus, Event, EventType

logger = logging.getLogger(__name__)

_TELEMETRY_RE = re.compile(
    r"F:([\d.\-]+)\s+L:([\d.\-]+)\s+R:([\d.\-]+)\s*->\s*(\w+)"
)


class SensorFusion:
    """Parses telemetry, maintains sensor state, detects obstacles."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self.front: float = -1
        self.left: float = -1
        self.right: float = -1
        self.esp_action: str = "STOP"
        self._last_update = 0.0
        self._obstacle_reported = False

    @property
    def last_update_age(self) -> float:
        if self._last_update == 0:
            return float("inf")
        return time.time() - self._last_update

    @property
    def front_clear(self) -> bool:
        return self.front < 0 or self.front > settings.obstacle_threshold_cm

    @property
    def best_turn_direction(self) -> str:
        if self.left < 0 and self.right < 0:
            return "right"
        if self.left < 0:
            return "left"
        if self.right < 0:
            return "right"
        return "left" if self.left > self.right else "right"

    @property
    def stuck(self) -> bool:
        threshold = 25.0
        blocked = 0
        if 0 < self.front < threshold:
            blocked += 1
        if 0 < self.left < threshold:
            blocked += 1
        if 0 < self.right < threshold:
            blocked += 1
        return blocked >= 2

    def as_dict(self) -> dict[str, float | str]:
        return {
            "front": round(self.front, 1),
            "left": round(self.left, 1),
            "right": round(self.right, 1),
            "esp_action": self.esp_action,
        }

    async def handle_telemetry_line(self, line: str) -> None:
        m = _TELEMETRY_RE.match(line)
        if not m:
            return

        self.front = float(m.group(1))
        self.left = float(m.group(2))
        self.right = float(m.group(3))
        self.esp_action = m.group(4)
        self._last_update = time.time()

        await self._bus.publish(Event(
            type=EventType.SENSOR_UPDATE,
            data=self.as_dict(),
        ))

        if self.esp_action == "OBSTACLE" and not self._obstacle_reported:
            self._obstacle_reported = True
            await self._bus.publish(Event(type=EventType.OBSTACLE))
            logger.warning(f"[sensors] OBSTACLE detected at {self.front}cm")
        elif self.esp_action != "OBSTACLE":
            self._obstacle_reported = False
