"""Centralized configuration with Pydantic validation."""

from __future__ import annotations

import enum
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class State(str, enum.Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    ACTING = "acting"
    SEARCHING = "searching"
    SPEAKING = "speaking"
    REPORTING = "reporting"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).parent / ".env",
        env_prefix="KANDA_",
        extra="ignore",
    )

    # --- LLM / Vision ---
    groq_api_key: str = ""
    groq_model: str = "llama-3.1-8b-instant"
    groq_endpoint: str = "https://api.groq.com/openai/v1/chat/completions"
    nvidia_api_key: str = ""
    nvidia_vlm_model: str = "meta/llama-3.2-11b-vision-instruct"
    nvidia_endpoint: str = "https://ai.api.nvidia.com/v1/gr/meta/llama-3.2-11b-vision-instruct/chat/completions"

    # --- Serial / Hardware ---
    serial_port: str = "/dev/ttyUSB0"
    serial_baud: int = 115200
    no_uart: bool = False

    # --- Motor ---
    speed_normal: int = 200
    speed_turn: int = 200
    speed_slow: int = 80
    turn_ms_per_deg: float = 10.0

    # --- Camera ---
    camera_resolution: tuple[int, int] = (640, 480)
    camera_jpeg_quality: int = 60
    camera_warmup_sec: float = 4.0

    # --- Wake Word ---
    wake_word_enabled: bool = True
    wake_word_model: str = "hey_jarvis"
    wake_sensitivity: float = 0.5

    # --- VAD / Mic ---
    vad_silence_sec: float = 1.5
    vad_max_sec: float = 10.0
    vad_threshold: int = 100

    # --- TTS ---
    tts_engine: str = "gtts"
    gtts_lang: str = "en"
    gtts_tld: str = "com"
    gtts_speed: float = 1.1
    espeak_voice: str = "en"
    espeak_rate: int = 150

    # --- Search ---
    search_max_steps: int = 20
    search_cell_size_cm: float = 30.0
    search_confidence_required: int = 1
    vlm_interval_sec: float = 10.0

    # --- Telegram ---
    telegram_enabled: bool = True
    telegram_bot_token: str = ""
    telegram_allowed_ids: str = ""
    telegram_owner_id: int = 0

    # --- Web ---
    web_host: str = "0.0.0.0"
    web_port: int = 8080

    # --- Safety ---
    obstacle_threshold_cm: float = 15.0
    esp32_heartbeat_timeout_sec: float = 5.0


settings = Settings()
