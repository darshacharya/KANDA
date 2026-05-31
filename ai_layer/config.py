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

# VLM model for vision tasks (scene description, navigation context)
# Uses the same model by default; override for a vision-specific model
VLM_MODEL = os.getenv("KANDA_VLM_MODEL", GEMINI_MODEL)

# ── Camera (Raspberry Pi Camera Module v2.1 — Sony IMX219) ───────────────────
CAMERA_RESOLUTION  = (640, 480)     # width x height — balance quality vs API cost
CAMERA_FRAMERATE   = 15             # fps (we capture single frames, not video)
CAMERA_JPEG_QUALITY = 75            # JPEG compression (lower = smaller payload)
CAMERA_WARMUP_SEC  = 2.0            # sensor stabilization time after start
CAMERA_ENABLED     = os.getenv("KANDA_CAMERA_ENABLED", "1") == "1"

# VLM cadence — scene descriptions are expensive; don't run every nav cycle
VLM_INTERVAL_SEC = float(os.getenv("KANDA_VLM_INTERVAL", "5.0"))
VLM_NAV_ENABLED  = os.getenv("KANDA_VLM_NAV", "1") == "1"

# ── Audio / TTS (Bluetooth speaker) ──────────────────────────────────────────
# Engine options: 'espeak' (default, offline), 'pyttsx3', 'gtts' (needs internet)
TTS_ENGINE  = os.getenv("KANDA_TTS_ENGINE", "espeak")
TTS_VOICE   = os.getenv("KANDA_TTS_VOICE", "en")   # espeak-ng voice
TTS_RATE    = int(os.getenv("KANDA_TTS_RATE", "150"))  # words per minute
TTS_VOLUME  = float(os.getenv("KANDA_TTS_VOLUME", "0.9"))  # 0.0 to 1.0
AUDIO_ENABLED = os.getenv("KANDA_AUDIO_ENABLED", "1") == "1"

# When to speak: 'scene' (describe what robot sees), 'hazard' (warnings only), 'all'
SPEAK_MODE = os.getenv("KANDA_SPEAK_MODE", "scene")

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
LOOP_INTERVAL_SEC = 2.0    # seconds between navigation LLM query cycles
LLM_TIMEOUT_SEC   = 8.0    # max wait for Gemini response before fallback
