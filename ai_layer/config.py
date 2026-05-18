"""
KANDA AI Layer — Central Configuration
Adjust these values to match your Raspberry Pi setup.
"""

import os

# ── Serial / UART ─────────────────────────────────────────────────────────────
# On Raspberry Pi: /dev/ttyS0 (GPIO UART) or /dev/ttyUSB0 (USB-serial adapter)
SERIAL_PORT = os.getenv("KANDA_SERIAL_PORT", "/dev/ttyS0")
BAUD_RATE   = 115200
SERIAL_TIMEOUT = 1.0      # seconds to wait for a line from ESP32

# ── Gemini API ────────────────────────────────────────────────────────────────
GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY", "")   # set in environment
# Default: Gemini 2.5 Flash — strong price/latency trade-off for closed-loop robotics.
# Override: export GEMINI_MODEL=gemini-2.5-flash (or another supported ID)
GEMINI_MODEL    = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# ── Safety Limits ─────────────────────────────────────────────────────────────
VALID_ACTIONS = {
    "forward",
    "backward",
    "left",
    "right",
    "slight_left",
    "slight_right",
    "stop",
}
SPEED_MIN = 0
SPEED_MAX = 255
DEFAULT_SPEED = 120    # used when LLM omits speed field

# ── Orchestration ─────────────────────────────────────────────────────────────
LOOP_INTERVAL_SEC = 2.0    # seconds between LLM query cycles
LLM_TIMEOUT_SEC   = 8.0    # max wait for Gemini response before fallback
