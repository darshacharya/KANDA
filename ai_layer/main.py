"""
KANDA AI Layer — Main Orchestrator (Phase 4)
Full sense → see → reason → validate → act → speak loop.

Usage:
    export GEMINI_API_KEY=your_key_here
    python3 main.py

The loop runs every LOOP_INTERVAL_SEC seconds:
  1. Read sensor telemetry from ESP32 via UART
  2. Capture camera frame (Pi Camera v2.1)
  3. [Periodic] Run VLM for scene description → speak via Bluetooth
  4. Build hardware-aware prompt with sensor + vision context
  5. Query Gemini (multimodal if image available) for a JSON movement command
  6. Validate the command through the safety layer
  7. Send the validated command back to ESP32

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


def run_cycle(
    bridge: UARTBridge,
    cycle: int,
    camera=None,
    vlm=None,
    speaker=None,
) -> None:
    """Execute one full sense → see → reason → validate → act → speak cycle."""
    logger.info("─── Cycle %d ───────────────────────────────", cycle)

    # ── Step 1: Read telemetry from ESP32 ─────────────────────────────────────
    telemetry = bridge.read_telemetry()
    if telemetry is None:
        logger.warning("No telemetry received — skipping cycle, sending stop")
        _fallback_stop(bridge)
        return

    logger.info(
        "Sensors  front=%.1fcm  left=%.1fcm  right=%.1fcm  state=%s",
        telemetry["front"], telemetry["left"], telemetry["right"], telemetry["action"],
    )

    # ── Step 2: Capture camera frame ──────────────────────────────────────────
    image_b64: Optional[str] = None
    if camera and camera.is_running:
        image_b64 = camera.capture_base64()
        if image_b64:
            logger.debug("Camera frame captured (%d chars b64)", len(image_b64))
        else:
            logger.debug("Camera capture returned None this cycle")

    # ── Step 3: Vision-Language processing (periodic) ─────────────────────────
    vision_context: Optional[dict] = None
    if vlm and image_b64 and vlm.should_update():
        # Scene description → spoken aloud
        description = vlm.describe_scene(image_b64)
        if description and speaker and speaker.is_running:
            if config.SPEAK_MODE in ("scene", "all"):
                speaker.speak(description)

        # Navigation context → fed into movement prompt
        if config.VLM_NAV_ENABLED:
            vision_context = vlm.get_navigation_context(image_b64, telemetry)

            if vision_context and vision_context.get("hazard_level") in ("medium", "high"):
                hazard_msg = f"Warning: {vision_context.get('context', 'hazard detected')}"
                if speaker and speaker.is_running:
                    speaker.speak(hazard_msg, priority=True)
    else:
        # Use cached navigation context between VLM updates
        if vlm:
            vision_context = vlm.last_nav_context

    # ── Step 4: Build prompt (sensors + vision + speech) ──────────────────────
    prompt = context_builder.build_prompt(
        telemetry,
        user_speech=None,
        image_b64=image_b64,
        vision_context=vision_context,
    )

    # ── Step 5: Query Gemini (multimodal if image available) ──────────────────
    raw_cmd: Optional[dict] = None
    try:
        raw_cmd = llm_client.query(prompt, image_b64=image_b64)
        logger.info("Gemini raw → %s", raw_cmd)
    except ValueError as exc:
        logger.warning("Gemini parse error: %s — sending stop", exc)
    except Exception as exc:
        logger.error("Gemini API error: %s — sending stop", exc)

    if raw_cmd is None:
        _fallback_stop(bridge)
        return

    # ── Step 6: Safety validation ─────────────────────────────────────────────
    safe_cmd = safety_validator.validate(raw_cmd)
    logger.info("Validated → action=%s  speed=%d", safe_cmd["action"], safe_cmd["speed"])

    # ── Step 7: Send to ESP32 ─────────────────────────────────────────────────
    bridge.send_command(safe_cmd["action"], safe_cmd["speed"])
    logger.info("Sent to ESP32 ✓")


def main() -> None:
    logger.info("KANDA AI Layer starting (Phase 4 — Vision + Audio)...")
    logger.info("Serial port : %s @ %d baud", config.SERIAL_PORT, config.BAUD_RATE)
    logger.info("LLM model   : %s", config.GEMINI_MODEL)
    logger.info("VLM model   : %s", config.VLM_MODEL)
    logger.info("Camera      : %s", "enabled" if config.CAMERA_ENABLED else "disabled")
    logger.info("Audio       : %s (engine=%s)", "enabled" if config.AUDIO_ENABLED else "disabled", config.TTS_ENGINE)
    logger.info("Loop interval: %.1f s | VLM interval: %.1f s", config.LOOP_INTERVAL_SEC, config.VLM_INTERVAL_SEC)

    # ── Initialize UART bridge ────────────────────────────────────────────────
    bridge = UARTBridge()

    # ── Initialize camera (Pi Camera v2.1) ────────────────────────────────────
    camera = None
    if config.CAMERA_ENABLED:
        try:
            from vision import Camera
            camera = Camera()
            camera.start()
        except Exception as exc:
            logger.warning("Camera init failed — running without vision: %s", exc)
            camera = None

    # ── Initialize VLM processor ──────────────────────────────────────────────
    vlm = None
    if camera:
        try:
            from vlm_processor import VLMProcessor
            vlm = VLMProcessor()
            logger.info("VLM processor ready")
        except Exception as exc:
            logger.warning("VLM init failed: %s", exc)

    # ── Initialize speaker (Bluetooth) ────────────────────────────────────────
    speaker = None
    if config.AUDIO_ENABLED:
        try:
            from audio_output import Speaker, check_bluetooth_audio
            bt_ok = check_bluetooth_audio()
            if not bt_ok:
                logger.warning("No Bluetooth speaker detected — audio may not play")
            speaker = Speaker()
            speaker.start()
            speaker.speak("Kanda online. Vision and audio systems active.")
        except Exception as exc:
            logger.warning("Speaker init failed — running without audio: %s", exc)
            speaker = None

    # ── Signal handlers ───────────────────────────────────────────────────────
    def _shutdown(sig, frame):
        logger.info("Shutdown signal received — stopping robot")
        _fallback_stop(bridge)
        if speaker:
            speaker.speak("Shutting down. Goodbye.")
            time.sleep(2)
            speaker.stop()
        if camera:
            camera.stop()
        bridge.disconnect()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # ── Connect to ESP32 ──────────────────────────────────────────────────────
    try:
        bridge.connect()
    except Exception as exc:
        logger.critical("Failed to open serial port: %s", exc)
        logger.critical("Check KANDA_SERIAL_PORT env var (currently: %s)", config.SERIAL_PORT)
        if camera:
            camera.stop()
        if speaker:
            speaker.stop()
        sys.exit(1)

    # ── Main loop ─────────────────────────────────────────────────────────────
    cycle = 0
    try:
        while True:
            cycle += 1
            try:
                run_cycle(bridge, cycle, camera=camera, vlm=vlm, speaker=speaker)
            except Exception as exc:
                logger.error("Unexpected error in cycle %d: %s", cycle, exc)
                _fallback_stop(bridge)

            time.sleep(config.LOOP_INTERVAL_SEC)

    finally:
        logger.info("Sending final stop and closing all resources")
        _fallback_stop(bridge)
        if speaker:
            speaker.stop()
        if camera:
            camera.stop()
        bridge.disconnect()


if __name__ == "__main__":
    main()
