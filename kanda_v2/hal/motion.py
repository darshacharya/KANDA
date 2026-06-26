"""High-level motion controller built on serial connection."""

from __future__ import annotations

import asyncio
import logging

from config import settings, State
from hal.serial_conn import SerialConnection
from hal.sensors import SensorFusion

logger = logging.getLogger(__name__)

# Pi wiring is reversed — swap left/right before sending to ESP32
_DIRECTION_SWAP = {
    "left": "right",
    "right": "left",
    "slight_left": "slight_right",
    "slight_right": "slight_left",
}


class MotionController:
    """Controls robot movement with safety checks."""

    def __init__(self, serial: SerialConnection, sensors: SensorFusion) -> None:
        self._serial = serial
        self._sensors = sensors
        self._current_action = "stop"

    @property
    def current_action(self) -> str:
        return self._current_action

    async def move(
        self,
        action: str,
        speed: int | None = None,
        state: str = "acting",
    ) -> None:
        if speed is None:
            speed = settings.speed_normal
        speed = min(max(speed, 0), 255)

        if action == "forward" and not self._sensors.front_clear:
            logger.warning("[motion] blocked — obstacle ahead, stopping")
            await self.stop()
            return

        wire_action = _DIRECTION_SWAP.get(action, action)
        self._current_action = action
        await self._serial.send(wire_action, speed, state)
        logger.info(f"[motion] {action} speed={speed}")

    async def stop(self, state: str = "idle") -> None:
        self._current_action = "stop"
        await self._serial.send("stop", 0, state)

    async def turn_degrees(self, direction: str, degrees: float, speed: int | None = None) -> None:
        turn_speed = speed if speed is not None else settings.speed_turn
        duration = degrees * settings.turn_ms_per_deg / 1000.0
        await self.move(direction, turn_speed, "acting")
        await asyncio.sleep(duration)
        await self.stop()

    async def move_timed(
        self,
        action: str,
        duration: float,
        speed: int | None = None,
    ) -> None:
        await self.move(action, speed, "acting")
        await asyncio.sleep(duration)
        await self.stop()

    async def dance(self, cancel_event: asyncio.Event | None = None, speed: int | None = None) -> None:
        """Execute a dance sequence at high speed."""
        dance_speed = speed if speed is not None else 255
        moves = [
            ("right", 0.3), ("left", 0.3), ("right", 0.3), ("left", 0.3),
            ("forward", 0.3), ("backward", 0.3), ("forward", 0.3), ("backward", 0.3),
            ("right", 0.4), ("right", 0.4), ("left", 0.4), ("left", 0.4),
            ("forward", 0.2), ("backward", 0.2), ("right", 0.3), ("left", 0.3),
        ]
        for action, dur in moves:
            if cancel_event and cancel_event.is_set():
                break
            await self.move(action, dance_speed, "acting")
            await asyncio.sleep(dur)
        await self.stop()

    async def backup(self) -> None:
        """Reverse out of a tight spot."""
        await self.move_timed("backward", 1.0, settings.speed_slow)
        await self.turn_degrees(self._sensors.best_turn_direction, 90)
