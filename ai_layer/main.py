"""
KANDA AI Layer — Main Orchestrator
Full sense → reason → validate → act loop.

Usage:
    export GEMINI_API_KEY=your_key_here
    python3 main.py

The loop runs every LOOP_INTERVAL_SEC seconds:
  1. Read sensor telemetry from ESP32 via UART
  2. Build hardware description prompt with live sensor data
  3. Query Gemini for a JSON movement command
  4. Validate the command through the safety layer
  5. Send the validated command back to ESP32

If Gemini fails or times out, the robot is commanded to stop safely.
Ctrl+C to exit — robot is sent 'stop' on shutdown.
"""

import logging
import signal
import sys
import time
from typing import Optional

import config
import context_builder
import llm_client
import safety_validator
from uart_bridge import UARTBridge

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _fallback_stop(bridge: UARTBridge) -> None:
    """Send a safe stop command — used on any error or shutdown."""
    try:
        bridge.send_command("stop", 0)
    except Exception as exc:
        logger.error("Could not send stop command: %s", exc)


def run_cycle(bridge: UARTBridge, cycle: int) -> None:
    """Execute one full sense → reason → validate → act cycle."""
    logger.info("─── Cycle %d ───────────────────────────────", cycle)

    # ── Step 1: Read telemetry ────────────────────────────────────────────────
    telemetry = bridge.read_telemetry()
    if telemetry is None:
        logger.warning("No telemetry received — skipping cycle, sending stop")
        _fallback_stop(bridge)
        return

    logger.info(
        "Sensors  front=%.1fcm  left=%.1fcm  right=%.1fcm  state=%s",
        telemetry["front"], telemetry["left"], telemetry["right"], telemetry["action"],
    )

    # ── Step 2: Build prompt ──────────────────────────────────────────────────
    # user_speech and image_b64 are None until Phase 4 hardware arrives
    prompt = context_builder.build_prompt(
        telemetry,
        user_speech=None,
        image_b64=None,
    )

    # ── Step 3: Query Gemini ──────────────────────────────────────────────────
    raw_cmd: Optional[dict] = None
    try:
        raw_cmd = llm_client.query(prompt)
        logger.info("Gemini raw → %s", raw_cmd)
    except ValueError as exc:
        logger.warning("Gemini parse error: %s — sending stop", exc)
    except Exception as exc:
        logger.error("Gemini API error: %s — sending stop", exc)

    if raw_cmd is None:
        _fallback_stop(bridge)
        return

    # ── Step 4: Safety validation ─────────────────────────────────────────────
    safe_cmd = safety_validator.validate(raw_cmd)
    logger.info("Validated → action=%s  speed=%d", safe_cmd["action"], safe_cmd["speed"])

    # ── Step 5: Send to ESP32 ─────────────────────────────────────────────────
    bridge.send_command(safe_cmd["action"], safe_cmd["speed"])
    logger.info("Sent to ESP32 ✓")


def main() -> None:
    logger.info("KANDA AI Layer starting...")
    logger.info("Serial port : %s @ %d baud", config.SERIAL_PORT, config.BAUD_RATE)
    logger.info("LLM model   : %s", config.GEMINI_MODEL)
    logger.info("Loop interval: %.1f s", config.LOOP_INTERVAL_SEC)

    bridge = UARTBridge()

    def _shutdown(sig, frame):
        logger.info("Shutdown signal received — stopping robot")
        _fallback_stop(bridge)
        bridge.disconnect()
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        bridge.connect()
    except Exception as exc:
        logger.critical("Failed to open serial port: %s", exc)
        logger.critical("Check KANDA_SERIAL_PORT env var (currently: %s)", config.SERIAL_PORT)
        sys.exit(1)

    cycle = 0
    try:
        while True:
            cycle += 1
            try:
                run_cycle(bridge, cycle)
            except Exception as exc:
                logger.error("Unexpected error in cycle %d: %s", cycle, exc)
                _fallback_stop(bridge)

            time.sleep(config.LOOP_INTERVAL_SEC)

    finally:
        logger.info("Sending final stop and closing serial port")
        _fallback_stop(bridge)
        bridge.disconnect()


if __name__ == "__main__":
    main()
