"""
KANDA AI Layer — Safety Validator
Validates every LLM-generated command before it is transmitted to the ESP32.

Rules:
  1. 'action' must be one of the defined valid action strings
  2. 'speed' must be an integer in [0, 255]
  3. Any violation → fallback safe command {"action": "stop", "speed": 0}

Standalone test:
    python3 safety_validator.py
"""

import logging
from typing import Any

import config

logger = logging.getLogger(__name__)

_SAFE_FALLBACK = {"action": "stop", "speed": 0}


def validate(cmd: Any) -> dict:
    """
    Validate an LLM-generated command dict.

    Returns the original command if valid, or the safe fallback if not.
    Never raises — always returns a safe, transmittable dict.
    """
    if not isinstance(cmd, dict):
        logger.warning("Validator: command is not a dict (%r) — stopping", cmd)
        return _SAFE_FALLBACK

    action = cmd.get("action")
    speed  = cmd.get("speed", config.DEFAULT_SPEED)

    # ── Action check ──────────────────────────────────────────────────────────
    if not isinstance(action, str) or action not in config.VALID_ACTIONS:
        logger.warning(
            "Validator: invalid action %r (allowed: %s) — stopping",
            action, config.VALID_ACTIONS,
        )
        return _SAFE_FALLBACK

    # ── Speed check ───────────────────────────────────────────────────────────
    try:
        speed = int(speed)
    except (TypeError, ValueError):
        logger.warning("Validator: speed %r is not numeric — stopping", speed)
        return _SAFE_FALLBACK

    if not (config.SPEED_MIN <= speed <= config.SPEED_MAX):
        # Clamp instead of full rejection — keeps direction intent
        clamped = max(config.SPEED_MIN, min(config.SPEED_MAX, speed))
        logger.warning(
            "Validator: speed %d out of range [%d, %d] — clamped to %d",
            speed, config.SPEED_MIN, config.SPEED_MAX, clamped,
        )
        speed = clamped

    safe_cmd = {"action": action, "speed": speed}
    logger.debug("Validator: approved %s", safe_cmd)
    return safe_cmd


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s  %(message)s")

    cases = [
        ({"action": "forward",  "speed": 150},  "valid — should pass"),
        ({"action": "forward",  "speed": 300},  "speed too high — should clamp to 255"),
        ({"action": "forward",  "speed": -10},  "speed negative — should clamp to 0"),
        ({"action": "fly",      "speed": 100},  "invalid action — should stop"),
        ({"action": "stop"},                     "no speed key — should use default"),
        ("not a dict",                           "wrong type — should stop"),
        ({},                                     "empty dict — should stop"),
    ]

    print(f"{'Input':<45} {'Result':<35} Note")
    print("-" * 100)
    for cmd, note in cases:
        result = validate(cmd)
        print(f"{str(cmd):<45} {str(result):<35} {note}")
