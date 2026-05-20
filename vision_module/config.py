"""
KANDA Vision Module — Configuration
"""

import os
from enum import Enum, auto


# ── Robot States ──────────────────────────────────────────────────────────────

class State(Enum):
    IDLE      = auto()   # wake word only — zero API calls
    LISTENING = auto()   # VAD recording after wake word
    THINKING  = auto()   # Gemini processing
    ACTING    = auto()   # single ESP32 command executing
    SEARCHING = auto()   # autonomous goal-pursuit / ReAct loop
    SPEAKING  = auto()   # TTS playing
    REPORTING = auto()   # final result, back to IDLE


# ── Gemini API ────────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL   = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
GEMINI_TIMEOUT_SEC = int(os.getenv("KANDA_GEMINI_TIMEOUT", "15"))


# ── USB to ESP32 ──────────────────────────────────────────────────────────────
SERIAL_PORT = os.getenv("KANDA_SERIAL_PORT", "/dev/ttyUSB0")
SERIAL_BAUD = 115200
ESP32_HEARTBEAT_TIMEOUT_SEC = 5   # warn if no telemetry for this long


# ── Camera (Raspberry Pi Camera Module v2.1) ──────────────────────────────────
CAMERA_RESOLUTION  = (640, 480)
CAMERA_JPEG_QUALITY = 80
CAMERA_WARMUP_SEC  = 4.0
CAMERA_MIN_JPEG_BYTES = 5_000   # frames smaller than this are corrupt


# ── VLM ───────────────────────────────────────────────────────────────────────
VLM_INTERVAL_SEC = float(os.getenv("KANDA_VLM_INTERVAL", "10.0"))


# ── Wake Word (openWakeWord — no account or API key needed) ───────────────────
# Built-in models: "hey_jarvis", "alexa", "hey_mycroft"
# Custom model:    path to a .onnx file (e.g. "hey_kanda.onnx")
# Default "hey_jarvis" works out of the box — just say "Hey Jarvis"
WAKE_WORD_MODEL     = os.getenv("KANDA_WAKE_WORD_MODEL", "hey_jarvis")
WAKE_WORD_SENSITIVITY = float(os.getenv("KANDA_WAKE_SENSITIVITY", "0.5"))
# Set to 0 to use keyboard fallback (press Enter) instead of mic wake word
WAKE_WORD_ENABLED   = os.getenv("KANDA_WAKE_WORD", "1") == "1"


# ── Microphone / VAD ─────────────────────────────────────────────────────────
VAD_SILENCE_SEC   = float(os.getenv("KANDA_VAD_SILENCE", "1.5"))  # stop after this much silence
VAD_MAX_SEC       = float(os.getenv("KANDA_VAD_MAX", "8.0"))      # hard cap on recording length
VAD_SPEECH_RMS    = int(os.getenv("KANDA_VAD_THRESHOLD", "500"))   # RMS threshold for speech vs silence


# ── Audio / TTS (Bluetooth speaker) ──────────────────────────────────────────
TTS_ENGINE  = os.getenv("KANDA_TTS_ENGINE", "espeak")
TTS_VOICE   = os.getenv("KANDA_TTS_VOICE", "en")
TTS_RATE    = int(os.getenv("KANDA_TTS_RATE", "150"))
TTS_VOLUME  = float(os.getenv("KANDA_TTS_VOLUME", "0.9"))
TTS_TIMEOUT_SEC = 10    # kill espeak-ng subprocess if it hangs


# ── Task / Search ─────────────────────────────────────────────────────────────
SEARCH_MAX_STEPS           = int(os.getenv("KANDA_SEARCH_MAX_STEPS", "20"))
SEARCH_MEMORY_MAX          = 50      # max entries in semantic memory deque
SEARCH_SIMILARITY_THRESHOLD = 0.7   # difflib ratio — above this = "already visited"
SEARCH_SIMILARITY_MIN      = 0.5    # auto-lowers to this after 5 consecutive skips

# Default motor speeds
SPEED_NORMAL = 120
SPEED_TURN   = 100
SPEED_SLOW   = 80

# Plan executor step limits
PLAN_MAX_STEPS       = 50    # safety cap on Gemini-generated plan length
PLAN_LOOP_MAX_ITER   = 30    # max iterations for loop_while steps
