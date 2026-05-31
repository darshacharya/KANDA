"""
KANDA Vision Module — Configuration
"""

import os
from enum import Enum, auto
from pathlib import Path

# Load .env file if present (no external dependency needed)
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _val = _line.split("=", 1)
                os.environ.setdefault(_key.strip(), _val.strip())


# ── Robot States ──────────────────────────────────────────────────────────────

class State(Enum):
    IDLE      = auto()   # wake word only — zero API calls
    LISTENING = auto()   # VAD recording after wake word
    THINKING  = auto()   # AI processing (Groq / NVIDIA NIM)
    ACTING    = auto()   # single ESP32 command executing
    SEARCHING = auto()   # autonomous goal-pursuit / ReAct loop
    SPEAKING  = auto()   # TTS playing
    REPORTING = auto()   # final result, back to IDLE


# ── Groq API (primary text model — 30 req/min free) ─────────────────────────
GROQ_API_KEY   = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL     = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_ENDPOINT  = "https://api.groq.com/openai/v1/chat/completions"

# ── NVIDIA NIM API (primary vision model — 40 req/min free) ──────────────────
NVIDIA_API_KEY    = os.getenv("NVIDIA_API_KEY", "")
NVIDIA_VLM_MODEL  = os.getenv("NVIDIA_VLM_MODEL", "meta/llama-3.2-11b-vision-instruct")
NVIDIA_ENDPOINT   = "https://integrate.api.nvidia.com/v1/chat/completions"


# ── USB to ESP32 ──────────────────────────────────────────────────────────────
SERIAL_PORT = os.getenv("KANDA_SERIAL_PORT", "/dev/ttyUSB0")
SERIAL_BAUD = 115200
ESP32_HEARTBEAT_TIMEOUT_SEC = 5   # warn if no telemetry for this long


# ── Camera (Raspberry Pi Camera Module v2.1) ──────────────────────────────────
CAMERA_RESOLUTION  = (640, 480)
CAMERA_JPEG_QUALITY = 60  # Lower = faster upload to VLM API
CAMERA_WARMUP_SEC  = 4.0
CAMERA_MIN_JPEG_BYTES = 5_000   # frames smaller than this are corrupt


# ── VLM ───────────────────────────────────────────────────────────────────────
VLM_INTERVAL_SEC = float(os.getenv("KANDA_VLM_INTERVAL", "10.0"))


# ── Wake Word (openWakeWord — no account or API key needed) ───────────────────
# Built-in models: "hey_jarvis", "alexa", "hey_mycroft"
# Production wake phrase: "Hey Kanda" — set KANDA_WAKE_WORD_MODEL=hey_kanda.onnx
#   (train: oww-train --phrase "hey kanda" --output hey_kanda.onnx)
WAKE_WORD_MODEL     = os.getenv("KANDA_WAKE_WORD_MODEL", "hey_jarvis")
WAKE_WORD_SENSITIVITY = float(os.getenv("KANDA_WAKE_SENSITIVITY", "0.1"))
# Set to 0 to use keyboard fallback (press Enter) instead of mic wake word
WAKE_WORD_ENABLED   = os.getenv("KANDA_WAKE_WORD", "1") == "1"


# ── Microphone / VAD ─────────────────────────────────────────────────────────
VAD_SILENCE_SEC   = float(os.getenv("KANDA_VAD_SILENCE", "2.0"))  # stop after this much silence
VAD_MAX_SEC       = float(os.getenv("KANDA_VAD_MAX", "15.0"))     # hard cap — allows longer sentences
VAD_SPEECH_RMS    = int(os.getenv("KANDA_VAD_THRESHOLD", "500"))   # RMS threshold for speech vs silence


# ── Audio / TTS ──────────────────────────────────────────────────────────────
# Engine: "gtts" (Google TTS, natural female, free) or "espeak" (offline fallback)
TTS_ENGINE  = os.getenv("KANDA_TTS_ENGINE", "gtts")

# Google TTS settings (free, no API key, requires internet)
GTTS_LANG = os.getenv("KANDA_GTTS_LANG", "en")        # Language: en with co.in TLD for Indian accent
GTTS_TLD  = os.getenv("KANDA_GTTS_TLD", "co.in")      # TLD for regional voice
GTTS_SPEED = float(os.getenv("KANDA_GTTS_SPEED", "1.2"))  # Playback speed (1.0=normal, 1.2=slightly fast)

# espeak-ng fallback settings (offline, robotic)
TTS_VOICE   = os.getenv("KANDA_TTS_VOICE", "en")
TTS_RATE    = int(os.getenv("KANDA_TTS_RATE", "150"))
TTS_VOLUME  = float(os.getenv("KANDA_TTS_VOLUME", "0.9"))
TTS_TIMEOUT_SEC = 10


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
PLAN_MAX_STEPS       = 50    # safety cap on AI-generated plan length
PLAN_LOOP_MAX_ITER   = 30    # max iterations for loop_while steps


# ── Telegram Bot Input (microphone alternative) ───────────────────────────────
# Set TELEGRAM_BOT_TOKEN env var or paste token here.
# Leave TELEGRAM_ALLOWED_IDS empty to allow any user, or add chat IDs like:
#   KANDA_TG_ALLOWED=123456789,987654321
TELEGRAM_ENABLED    = os.getenv("TELEGRAM_ENABLED", "1") == "1"
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "8679310303:AAH7PRXjeXXNTuFH6i68ASmy6o6yfK8g3nU")
TELEGRAM_VOICE_ENABLED = os.getenv("TELEGRAM_VOICE_ENABLED", "1") == "1"
TELEGRAM_ALLOWED_IDS = [
    int(x) for x in os.getenv("KANDA_TG_ALLOWED", "").split(",") if x.strip().isdigit()
]
TELEGRAM_OWNER_CHAT_ID = int(os.getenv("KANDA_TG_OWNER", "0")) or None
