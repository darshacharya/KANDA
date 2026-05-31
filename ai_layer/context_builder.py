"""
KANDA AI Layer — Context Builder
Constructs the hardware description prompt sent to the Gemini LLM.

The prompt tells the model exactly what the robot can do and what it currently
sees, so it can generate a grounded, hardware-safe JSON command.

Phase 4 additions:
  - Vision context from camera is injected into the navigation prompt
  - User speech commands can influence robot behaviour
  - Camera image_b64 is passed to llm_client for multimodal queries

Standalone test:
    python3 context_builder.py
    → prints a sample prompt to console
"""

import logging
from typing import Optional

import config

logger = logging.getLogger(__name__)

_HARDWARE_DESC = """You are the reasoning brain of a two-wheeled indoor companion robot named KANDA.

Hardware available:
  - 3 ultrasonic distance sensors (front, left, right) measuring in centimetres
  - Camera (Pi Camera v2.1) providing visual scene understanding
  - Differential drive motors (two wheels)
  - Bluetooth speaker for spoken feedback
  - OLED display for status feedback

Movement commands you can issue (choose exactly one):
  forward      — move straight ahead
  backward     — reverse
  left         — pivot turn left
  right        — pivot turn right
  slight_left  — gentle left correction while moving
  slight_right — gentle right correction while moving
  stop         — halt all motors

Speed: integer from 0 (stopped) to 255 (full speed). Recommended cruise: 100-150.

Safety rules you MUST follow:
  - If front sensor < 20 cm: do NOT use forward
  - If left sensor < 15 cm: prefer slight_right or right
  - If right sensor < 15 cm: prefer slight_left or left
  - If vision reports a hazard (glass, stairs, pets): use stop or avoid
  - When unsure: use stop

Response format — ONLY output valid JSON, nothing else:
  {"action": "<command>", "speed": <integer>}"""


def build_prompt(
    telemetry: dict,
    user_speech: Optional[str] = None,
    image_b64: Optional[str] = None,
    vision_context: Optional[dict] = None,
) -> str:
    """
    Compose the full prompt string from hardware description + live sensor data
    + optional vision context and user speech.

    Args:
        telemetry:      dict with keys front, left, right (floats, cm), action (str)
        user_speech:    transcribed text from mic (None if no speech input)
        image_b64:      base64-encoded camera frame (passed to llm_client separately)
        vision_context: dict from VLMProcessor.get_navigation_context() with
                        'context' (str) and 'hazard_level' (str)

    Returns:
        Full prompt string ready to send to Gemini.
    """
    front = telemetry.get("front", -1)
    left  = telemetry.get("left",  -1)
    right = telemetry.get("right", -1)

    def fmt(v):
        return f"{v:.1f} cm" if v > 0 else "no reading"

    sensor_section = (
        f"\nCurrent sensor readings:\n"
        f"  front : {fmt(front)}\n"
        f"  left  : {fmt(left)}\n"
        f"  right : {fmt(right)}"
    )

    # Vision context from VLM (camera-based scene understanding)
    vision_section = ""
    if vision_context:
        ctx_text = vision_context.get("context", "")
        hazard = vision_context.get("hazard_level", "none")
        if ctx_text:
            vision_section = (
                f"\nVisual context from camera:\n"
                f"  {ctx_text}\n"
                f"  Hazard level: {hazard}"
            )

    # Note presence of attached camera image for multimodal query
    vision_note = ""
    if image_b64:
        vision_note = "\nA live camera image of the robot's forward view is also attached."

    speech_section = ""
    if user_speech:
        speech_section = f"\nUser said: \"{user_speech}\""

    prompt = (
        _HARDWARE_DESC
        + sensor_section
        + vision_section
        + vision_note
        + speech_section
        + "\n\nWhat should the robot do next? Respond with JSON only."
    )

    logger.debug("Prompt built (%d chars)", len(prompt))
    return prompt


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s  %(message)s")

    sample_telemetry = {"front": 45.2, "left": 30.1, "right": 80.5, "action": "FORWARD"}

    print("=" * 60)
    print("SAMPLE PROMPT (sensor data only):")
    print("=" * 60)
    print(build_prompt(sample_telemetry))

    print()
    print("=" * 60)
    print("SAMPLE PROMPT (with vision context):")
    print("=" * 60)
    nav_ctx = {"context": "A glass door is visible 2m ahead, ultrasonic may not detect it.", "hazard_level": "medium"}
    print(build_prompt(sample_telemetry, vision_context=nav_ctx))

    print()
    print("=" * 60)
    print("SAMPLE PROMPT (with speech + vision):")
    print("=" * 60)
    print(build_prompt(sample_telemetry, user_speech="Go to the kitchen", vision_context=nav_ctx))

    print()
    print("=" * 60)
    print("SAMPLE PROMPT (obstacle scenario):")
    print("=" * 60)
    obstacle = {"front": 12.0, "left": 60.0, "right": 25.0, "action": "STOP"}
    print(build_prompt(obstacle))
