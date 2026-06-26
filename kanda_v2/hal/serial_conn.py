"""Async wrapper around pyserial for ESP32 communication."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable, Coroutine, Any

import serial

from config import settings

logger = logging.getLogger(__name__)

TelemetryCallback = Callable[[str], Coroutine[Any, Any, None]]


class SerialConnection:
    """Non-blocking serial I/O with ESP32."""

    def __init__(self) -> None:
        self._conn: serial.Serial | None = None
        self._running = False
        self._on_line: list[TelemetryCallback] = []

    @property
    def connected(self) -> bool:
        return self._conn is not None and self._conn.is_open

    def on_line_received(self, callback: TelemetryCallback) -> None:
        self._on_line.append(callback)

    async def connect(self) -> bool:
        if settings.no_uart:
            logger.info("[serial] UART disabled — running without ESP32")
            return False

        def _open() -> serial.Serial | None:
            import glob
            # Try configured port first, then scan for USB serial devices
            ports_to_try = [settings.serial_port]
            ports_to_try += sorted(glob.glob("/dev/ttyUSB*"))
            ports_to_try += sorted(glob.glob("/dev/ttyACM*"))
            # Deduplicate while preserving order
            seen = set()
            unique_ports = []
            for p in ports_to_try:
                if p not in seen:
                    seen.add(p)
                    unique_ports.append(p)

            for port in unique_ports:
                try:
                    conn = serial.Serial(
                        port=port,
                        baudrate=settings.serial_baud,
                        timeout=0.1,
                    )
                    logger.info(f"[serial] connected to {port}")
                    return conn
                except (serial.SerialException, OSError):
                    continue

            logger.error("[serial] no ESP32 found on any port")
            return None

        self._conn = await asyncio.to_thread(_open)
        return self._conn is not None

    async def send(self, action: str, speed: int = 0, state: str = "idle") -> None:
        if not self.connected:
            return

        payload = json.dumps({
            "action": action,
            "speed": max(0, min(255, speed)),
            "state": state,
        }) + "\n"

        def _write():
            try:
                self._conn.write(payload.encode())
            except (serial.SerialException, OSError) as e:
                logger.error(f"[serial] write error: {e}")

        await asyncio.to_thread(_write)

    async def read_loop(self) -> None:
        """Continuously read telemetry lines from ESP32 with auto-reconnect."""
        self._running = True
        logger.info("[serial] read loop started")
        no_data_count = 0

        while self._running:
            line = await asyncio.to_thread(self._read_line)
            if line:
                no_data_count = 0
                for cb in self._on_line:
                    try:
                        await cb(line)
                    except Exception:
                        logger.exception("[serial] telemetry callback error")
            else:
                no_data_count += 1
                await asyncio.sleep(0.01)
                # ESP32 sends at 20Hz — if no data for 5s, try reconnect
                if no_data_count > 500:
                    no_data_count = 0
                    logger.warning("[serial] no telemetry for 5s — reconnecting")
                    await self._reconnect()

    async def _reconnect(self) -> None:
        """Close and re-open serial to recover from ESP32 reset."""
        try:
            if self._conn and self._conn.is_open:
                self._conn.close()
        except Exception:
            pass
        self._conn = None
        await asyncio.sleep(2)
        await self.connect()

    def _read_line(self) -> str | None:
        if not self.connected:
            return None
        try:
            if self._conn.in_waiting:
                raw = self._conn.readline().decode("utf-8", errors="replace").strip()
                return raw if raw else None
        except (serial.SerialException, OSError):
            self._conn = None
        return None

    async def disconnect(self) -> None:
        self._running = False
        if self._conn and self._conn.is_open:
            await asyncio.to_thread(self._conn.close)
            logger.info("[serial] disconnected")
