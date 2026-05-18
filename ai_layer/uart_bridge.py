"""
KANDA AI Layer — UART Bridge
Handles all serial communication between the Raspberry Pi and the ESP32.

Standalone test:
    python3 uart_bridge.py
    → opens serial port, sends {"action":"forward","speed":120}, reads one line back
"""

import json
import logging
import serial
import time

import config

logger = logging.getLogger(__name__)


class UARTBridge:
    def __init__(self, port: str = config.SERIAL_PORT, baud: int = config.BAUD_RATE):
        self._port = port
        self._baud = baud
        self._ser: serial.Serial | None = None

    def connect(self) -> None:
        self._ser = serial.Serial(
            self._port,
            self._baud,
            timeout=config.SERIAL_TIMEOUT,
        )
        time.sleep(2.0)   # let ESP32 finish reset after serial open
        logger.info("UART connected on %s @ %d baud", self._port, self._baud)

    def disconnect(self) -> None:
        if self._ser and self._ser.is_open:
            self._ser.close()
            logger.info("UART disconnected")

    def send_command(self, action: str, speed: int = config.DEFAULT_SPEED) -> None:
        """Serialise command to JSON and write one line to the ESP32."""
        if self._ser is None or not self._ser.is_open:
            raise RuntimeError("Serial port not open — call connect() first")

        cmd = json.dumps({"action": action, "speed": speed})
        self._ser.write((cmd + "\n").encode("utf-8"))
        self._ser.flush()
        logger.debug("TX → %s", cmd)

    def read_telemetry(self) -> dict | None:
        """
        Read one line from the ESP32 and parse it.
        ESP32 sends CSV: F:<val> L:<val> R:<val> -> <action>
        Returns dict with keys: front, left, right, action
        Returns None on timeout or parse failure.
        """
        if self._ser is None or not self._ser.is_open:
            raise RuntimeError("Serial port not open — call connect() first")

        try:
            raw = self._ser.readline().decode("utf-8", errors="ignore").strip()
        except serial.SerialException as exc:
            logger.warning("Serial read error: %s", exc)
            return None

        if not raw:
            return None

        logger.debug("RX ← %s", raw)
        return _parse_telemetry(raw)

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_):
        self.disconnect()


def _parse_telemetry(line: str) -> dict | None:
    """
    Parse ESP32 telemetry line.
    Expected format:  F:45.20 L:30.10 R:80.50 -> FORWARD
    Returns:          {"front": 45.2, "left": 30.1, "right": 80.5, "action": "FORWARD"}
    """
    try:
        parts, action_part = line.split("->")
        def extract(key):
            for tok in parts.split():
                if tok.startswith(key + ":"):
                    return float(tok.split(":")[1])
            return -1.0

        return {
            "front":  extract("F"),
            "left":   extract("L"),
            "right":  extract("R"),
            "action": action_part.strip(),
        }
    except Exception:
        logger.debug("Could not parse telemetry line: %r", line)
        return None


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s  %(message)s")

    print(f"Connecting to ESP32 on {config.SERIAL_PORT}...")
    with UARTBridge() as bridge:
        print("Sending: forward @ speed 120")
        bridge.send_command("forward", 120)
        time.sleep(0.5)

        print("Reading telemetry...")
        for _ in range(5):
            data = bridge.read_telemetry()
            if data:
                print(f"  Telemetry: {data}")
            time.sleep(0.2)

        print("Sending: stop")
        bridge.send_command("stop", 0)
        print("Done.")
