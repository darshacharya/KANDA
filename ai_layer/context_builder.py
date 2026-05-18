"""
KANDA AI Layer — Context Builder
Constructs the hardware description prompt sent to the Gemini LLM.

The prompt tells the model exactly what the robot can do and what it currently
sees, so it can generate a grounded, hardware-safe JSON command.

Stub slots for image_b64 and user_speech are already wired in — they are None
now and will be filled when camera and microphone hardware arrive (Phase 4).

Standalone test:
    python3 context_builder.py
    → prints a sample prompt to console
"""

import logging
from typing import Optional

import config

logger = logging.getLogger(__name__)

# Hardware description — static portion sent with every request
_HARDWARE_DESC = """You are the reasoning brain of a two-wheeled indoor companion robot named KANDA.

Hardware available:
  - 3 ultrasonic distance sensors (front, left, right) measuring in centimetres
  - Differential drive motors (two wheels)
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
  - When unsure: use stop

Response format — ONLY output valid JSON, nothing else:
  {"action": "<command>", "speed": <integer>}"""


def build_prompt(
    telemetry: dict,
    user_speech: Optional[str] = None,
    image_b64: Optional[str] = None,   # Phase 4 — camera frame
) -> str:
    """
    Compose the full prompt string from hardware description + live sensor data.

    Args:
        telemetry:   dict with keys front, left, right (floats, cm), action (str)
        user_speech: transcribed text from mic (None until Phase 4)
        image_b64:   base64-encoded camera frame (None until Phase 4)

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

    # Optional voice input (Phase 4)
    speech_section = ""
    if user_speech:
        speech_section = f"\nUser said: \"{user_speech}\""

    # Optional visual input (Phase 4 — image_b64 handled in llm_client.py)
    # We note its presence here for prompt clarity
    vision_note = ""
    if image_b64:
        vision_note = "\nA camera image of the robot's view is also attached."

    prompt = (
        _HARDWARE_DESC
        + sensor_section
        + speech_section
        + vision_note
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
    print("SAMPLE PROMPT (with user speech):")
    print("=" * 60)
    print(build_prompt(sample_telemetry, user_speech="Go to the kitchen"))

    print()
    print("=" * 60)
    print("SAMPLE PROMPT (obstacle scenario):")
    print("=" * 60)
    obstacle = {"front": 12.0, "left": 60.0, "right": 25.0, "action": "STOP"}
    print(build_prompt(obstacle))
