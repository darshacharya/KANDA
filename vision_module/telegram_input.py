"""
KANDA — Telegram Bot Input
Polls the Telegram Bot API for messages and feeds them into the state machine
as if the user had spoken them.  No mic required.

Commands
--------
  /photo   — capture and send back the current camera image + VLM description
  /stop    — emergency stop (sets cancel_event)
  /status  — reply with last known sensor readings + state
  /esp32   — send a test ping to ESP32 and show last telemetry
  /help    — list all commands
  <text>   — treated as a voice command (routed through Gemini)
  <voice>  — transcribed via Gemini, then processed as text
  <photo>  — object described via VLM, then robot searches for it
"""

import logging
import queue
import threading
import time
import urllib.request
import urllib.parse
import json
import io
from typing import Optional

import config

# Vision keywords — these questions are handled directly here without going
# through the full state machine so the photo + description arrive together.
_VISION_KEYWORDS = (
    "what do you see", "what can you see", "what's in front",
    "what is in front", "describe what", "look around",
    "what are you looking at", "show me", "take a photo",
    "capture", "send photo", "send image",
)

logger = logging.getLogger(__name__)

# ── Shared queue and references (set by start()) ──────────────────────────────
_tg_queue: queue.Queue = queue.Queue()   # (chat_id, text) tuples

_wake_event:   Optional[threading.Event] = None
_cancel_event: Optional[threading.Event] = None
_shutdown_event: Optional[threading.Event] = None

_camera_ref    = None   # Camera instance for /photo
_body_ctx_ref  = None   # BodyContext for /status
_serial_ref    = None   # serial.Serial for /esp32 test
_vlm_ref       = None   # VLM instance for vision questions
_presenter_ref = None   # Presentation instance for slide mode
_speaker_ref   = None   # Speaker instance for TTS during presentation

# Last telemetry dict received by heartbeat thread (updated externally)
last_telemetry: Optional[dict] = None

# Stored chat_id for broadcast messages (health check, search logs, etc.)
_owner_chat_id: Optional[int] = config.TELEGRAM_OWNER_CHAT_ID


# ── Telegram HTTP helpers (pure stdlib, no dependency) ────────────────────────

_BASE = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"


def _tg(method: str, **params) -> dict:
    """Call a Telegram Bot API method; returns parsed JSON response."""
    url = f"{_BASE}/{method}"
    data = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(url, data=data.encode() if data else None)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def send_message(chat_id: int, text: str) -> None:
    """Send a plain-text reply to a Telegram chat."""
    try:
        _tg("sendMessage", chat_id=chat_id, text=text)
    except Exception as exc:
        logger.warning("[telegram] sendMessage failed: %s", exc)


def send_photo(chat_id: int, jpeg_bytes: bytes, caption: str = "") -> None:
    """Send a JPEG image to a Telegram chat using multipart upload."""
    try:
        import urllib.request as urlreq
        boundary = "KandaBoundary"
        body  = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
            f"{chat_id}\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="caption"\r\n\r\n'
            f"{caption}\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="photo"; filename="kanda.jpg"\r\n'
            f"Content-Type: image/jpeg\r\n\r\n"
        ).encode() + jpeg_bytes + f"\r\n--{boundary}--\r\n".encode()

        req = urlreq.Request(
            f"{_BASE}/sendPhoto",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urlreq.urlopen(req, timeout=15):
            pass
    except Exception as exc:
        logger.warning("[telegram] sendPhoto failed: %s", exc)


def _ensure_owner_chat_id() -> Optional[int]:
    """Detect the owner's chat_id from the last Telegram update."""
    global _owner_chat_id
    if _owner_chat_id:
        return _owner_chat_id
    try:
        url = f"{_BASE}/getUpdates?offset=-1&limit=1"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
        if data.get("result"):
            _owner_chat_id = data["result"][-1]["message"]["chat"]["id"]
    except Exception:
        pass
    return _owner_chat_id


def broadcast(text: str) -> None:
    """Send a message to the owner (last known chat_id). Used for boot status, logs."""
    cid = _ensure_owner_chat_id()
    if cid:
        send_message(cid, text)


def send_welcome() -> None:
    """Send a well-designed welcome message with clickable commands on boot."""
    cid = _ensure_owner_chat_id()
    if not cid:
        return

    welcome = (
        "🤖 *KANDA is online!*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "🎤 *Voice:* Say \"Hey Kanda\" + your command\n"
        "💬 *Text:* Type anything below\n"
        "📸 *Photo:* Send image to search for it\n"
        "🎙 *Voice note:* Send audio message\n"
        "\n"
        "━━━ Quick Commands ━━━\n"
        "\n"
        "🔭 /photo — What am I seeing now?\n"
        "🛑 /stop — Emergency stop\n"
        "📊 /status — Sensors & state\n"
        "🔌 /esp32 — Hardware check\n"
        "📽 /present — Start presentation\n"
        "➡️ /next — Next slide\n"
        "⬅️ /prev — Previous slide\n"
        "❓ /help — All commands\n"
        "\n"
        "━━━ Try saying ━━━\n"
        "\n"
        "• \"Move forward\"\n"
        "• \"Turn right 2 seconds\"\n"
        "• \"What can you see?\"\n"
        "• \"Find the red bottle\"\n"
        "• \"Who is Virat Kohli?\"\n"
        "• \"What time is it?\"\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )

    try:
        payload = urllib.parse.urlencode({
            "chat_id": cid,
            "text": welcome,
            "parse_mode": "Markdown",
        })
        req = urllib.request.Request(
            f"{_BASE}/sendMessage",
            data=payload.encode(),
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as exc:
        logger.warning("[telegram] welcome message failed: %s — sending plain", exc)
        send_message(cid, welcome)


def register_commands() -> None:
    """Register bot slash commands menu with Telegram (shows in input field)."""
    commands = [
        {"command": "photo", "description": "📸 Camera snapshot + description"},
        {"command": "stop", "description": "🛑 Emergency stop"},
        {"command": "status", "description": "📊 Sensor readings & state"},
        {"command": "esp32", "description": "🔌 ESP32 hardware check"},
        {"command": "present", "description": "📽 Start presentation mode"},
        {"command": "next", "description": "➡️ Next slide"},
        {"command": "prev", "description": "⬅️ Previous slide"},
        {"command": "speed", "description": "⚡ Set motor speed (0-255)"},
        {"command": "auto", "description": "🚗 Switch to self-driving mode"},
        {"command": "ai", "description": "🤖 Switch to AI command mode"},
        {"command": "help", "description": "❓ List all commands"},
    ]
    try:
        payload = json.dumps({"commands": commands}).encode()
        req = urllib.request.Request(
            f"{_BASE}/setMyCommands",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
        logger.info("[telegram] slash commands registered")
    except Exception as exc:
        logger.warning("[telegram] setMyCommands failed: %s", exc)


def broadcast_photo(jpeg_bytes: bytes, caption: str = "") -> None:
    """Send a photo to the owner. Used for search step images."""
    cid = _ensure_owner_chat_id()
    if cid:
        send_photo(cid, jpeg_bytes, caption=caption)


def notify(text: str) -> None:
    """Lightweight alias for broadcast — use for real-time event logs."""
    broadcast(text)


# ── File download + transcription helpers ─────────────────────────────────────

def _download_file(file_id: str) -> Optional[bytes]:
    """Download a file from Telegram servers by file_id."""
    try:
        result = _tg("getFile", file_id=file_id)
        file_path = result.get("result", {}).get("file_path")
        if not file_path:
            return None
        url = f"https://api.telegram.org/file/bot{config.TELEGRAM_BOT_TOKEN}/{file_path}"
        with urllib.request.urlopen(url, timeout=30) as resp:
            return resp.read()
    except Exception as exc:
        logger.warning("[telegram] file download failed: %s", exc)
        return None


def _transcribe_voice(audio_bytes: bytes, mime_type: str = "audio/ogg") -> Optional[str]:
    """Transcribe audio bytes using Groq Whisper. Returns text or None."""
    if not config.GROQ_API_KEY:
        return None
    try:
        import tempfile
        suffix = ".ogg" if "ogg" in mime_type else ".wav"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(audio_bytes)
            tmp_path = f.name

        boundary = "----KandaVoice"
        body = (
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"file\"; filename=\"voice{suffix}\"\r\n"
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode() + audio_bytes + (
            f"\r\n--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"model\"\r\n\r\n"
            f"whisper-large-v3-turbo\r\n"
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"language\"\r\n\r\n"
            f"en\r\n"
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"prompt\"\r\n\r\n"
            f"Short English commands to a robot: move forward, turn left, find the bottle, what do you see, stop.\r\n"
            f"--{boundary}--\r\n"
        ).encode()

        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            data=body,
            headers={
                "Authorization": f"Bearer {config.GROQ_API_KEY}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "KANDA/1.0",
            },
        )
        resp = urllib.request.urlopen(req, timeout=15)
        result = json.loads(resp.read())
        text = result.get("text", "").strip()
        import os
        os.unlink(tmp_path)
        return text if text else None
    except Exception as exc:
        logger.warning("[telegram] voice transcription failed: %s", exc)
        return None


def _describe_target_image(image_bytes: bytes, query: str = "") -> Optional[str]:
    """Ask NVIDIA NIM VLM to describe or answer a question about an image."""
    if _vlm_ref is None:
        return None
    try:
        import base64 as _b64
        b64 = _b64.b64encode(image_bytes).decode()
        if query:
            prompt = f"You are Kanda, an AI robot. User sent this image with the question: \"{query}\"\nAnswer in 2-3 sentences."
        else:
            prompt = (
                "Describe the main object in this image in 5-10 words. "
                "Be specific about color, shape, and type. "
                "Example: 'red water bottle with white cap' or 'black laptop charger'. "
                "Return ONLY the description."
            )
        desc = _vlm_ref.describe_scene(b64, prompt=prompt)
        return desc if desc else None
    except Exception as exc:
        logger.warning("[telegram] image description failed: %s", exc)
        return None


# ── Polling thread ────────────────────────────────────────────────────────────

def _poll_loop() -> None:
    offset = None
    allowed = set(config.TELEGRAM_ALLOWED_IDS)

    logger.info("[telegram] bot polling started (@bot token …%s)", config.TELEGRAM_BOT_TOKEN[-6:])

    while not _shutdown_event.is_set():
        try:
            params = {"timeout": 20, "allowed_updates": ["message"]}
            if offset is not None:
                params["offset"] = offset

            url = f"{_BASE}/getUpdates?" + urllib.parse.urlencode(params)
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = json.loads(resp.read())

            for update in data.get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message")
                if not msg:
                    continue

                chat_id = msg["chat"]["id"]

                # Remember owner chat_id for broadcast messages
                global _owner_chat_id
                if _owner_chat_id is None:
                    _owner_chat_id = chat_id

                # Access control — check early before processing any content
                if allowed and chat_id not in allowed:
                    send_message(chat_id, "Unauthorised.")
                    logger.warning("[telegram] blocked chat_id=%s", chat_id)
                    continue

                username = msg.get("from", {}).get("username", "?")

                # ── Voice note handling ──────────────────────────────────────
                voice = msg.get("voice") or msg.get("audio")
                if voice and config.TELEGRAM_VOICE_ENABLED:
                    file_id = voice.get("file_id")
                    logger.info("[telegram] voice from @%s (%s)", username, chat_id)
                    send_message(chat_id, "Transcribing...")
                    audio_data = _download_file(file_id)
                    if not audio_data:
                        send_message(chat_id, "Failed to download voice note.")
                        continue
                    mime = voice.get("mime_type", "audio/ogg")
                    transcript = _transcribe_voice(audio_data, mime)
                    if not transcript:
                        send_message(chat_id, "Couldn't understand the voice note.")
                        continue
                    send_message(chat_id, f"Heard: '{transcript}'")
                    # Feed transcript as a text command
                    _tg_queue.put((chat_id, transcript))
                    if _wake_event:
                        _wake_event.set()
                    continue

                # ── Photo handling (with optional caption query) ──────────────
                photos = msg.get("photo")
                if photos:
                    caption = (msg.get("caption") or "").strip()
                    largest = photos[-1]
                    file_id = largest.get("file_id")
                    logger.info("[telegram] photo from @%s (%s) caption='%s'", username, chat_id, caption)
                    send_message(chat_id, "Analyzing image..." + (f" ({caption})" if caption else ""))
                    image_data = _download_file(file_id)
                    if not image_data:
                        send_message(chat_id, "Failed to download image.")
                        continue

                    if caption:
                        # User asked a question about the image — answer it directly
                        description = _describe_target_image(image_data, query=caption)
                        if description:
                            send_message(chat_id, f"👁️ {description}")
                            _tg_queue.put((chat_id, description))
                            if _wake_event:
                                _wake_event.set()
                        else:
                            send_message(chat_id, "Couldn't analyze the image.")
                    else:
                        # No caption — describe and search
                        description = _describe_target_image(image_data)
                        if not description:
                            send_message(chat_id, "Couldn't identify the object. Try a clearer photo.")
                            continue
                        search_cmd = f"find {description}"
                        send_message(chat_id, f"Looking for: {description}\nSearching...")
                        _tg_queue.put((chat_id, search_cmd))
                        if _wake_event:
                            _wake_event.set()
                    continue

                # ── Text handling ────────────────────────────────────────────
                text = (msg.get("text") or "").strip()
                if not text:
                    send_message(chat_id, "Send a text, voice note, or photo.")
                    continue

                logger.info("[telegram] from @%s (%s): '%s'", username, chat_id, text)

                # ── Built-in commands ──────────────────────────────────────────
                tl = text.lower()
                if tl == "/photo":
                    _handle_photo(chat_id)
                elif tl == "/stop":
                    _handle_stop(chat_id)
                elif tl == "/status":
                    _handle_status(chat_id)
                elif tl == "/esp32":
                    _handle_esp32(chat_id)
                elif tl == "/present":
                    _handle_present_start(chat_id)
                elif tl in ("/next", "next", "next slide"):
                    _handle_present_next(chat_id)
                elif tl in ("/prev", "previous", "prev slide", "previous slide"):
                    _handle_present_prev(chat_id)
                elif tl == "/endpresent":
                    _handle_present_stop(chat_id)
                elif tl == "/auto":
                    _handle_mode_switch(chat_id, auto=True)
                elif tl == "/ai":
                    _handle_mode_switch(chat_id, auto=False)
                elif tl.startswith("/speed"):
                    _handle_speed(chat_id, tl)
                elif tl in ("/start", "/help"):
                    _send_help_menu(chat_id)
                elif any(k in tl for k in _VISION_KEYWORDS):
                    _handle_vision_question(chat_id)
                else:
                    _tg_queue.put((chat_id, text))
                    if _wake_event:
                        _wake_event.set()

        except Exception as exc:
            logger.warning("[telegram] poll error: %s", exc)
            time.sleep(5)   # back-off on network error


def _send_help_menu(chat_id: int) -> None:
    """Send a nicely formatted help message with all slash commands."""
    help_text = (
        "🤖 *KANDA — Command Menu*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "*🎯 Movement*\n"
        "  /stop — 🛑 Emergency stop\n"
        "  /speed 120 — ⚡ Set motor speed\n"
        "  • Move forward / backward\n"
        "  • Turn left / right [N seconds]\n"
        "\n"
        "*👁 Vision*\n"
        "  /photo — 📸 Camera snapshot\n"
        "  • What can you see?\n"
        "  • Send photo + caption to ask\n"
        "\n"
        "*🔍 Search*\n"
        "  • Find the red bottle\n"
        "  • Send photo of target object\n"
        "\n"
        "*📊 Status & Mode*\n"
        "  /status — Sensor readings\n"
        "  /esp32 — Hardware check\n"
        "  /auto — 🚗 Self-driving mode\n"
        "  /ai — 🤖 AI command mode\n"
        "\n"
        "*📽 Presentation*\n"
        "  /present — Start slides\n"
        "  /next — Next slide\n"
        "  /prev — Previous slide\n"
        "\n"
        "*❓ Questions*\n"
        "  • Who is Virat Kohli?\n"
        "  • What time is it?\n"
        "  • What's today's date?\n"
        "\n"
        "*🎙 Voice*\n"
        "  • Send voice note for any command\n"
        "  • Say 'Hey Kanda' + speak\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    try:
        payload = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": help_text,
            "parse_mode": "Markdown",
        })
        req = urllib.request.Request(
            f"{_BASE}/sendMessage",
            data=payload.encode(),
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        send_message(chat_id, help_text)


def _handle_photo(chat_id: int) -> None:
    if _camera_ref is None:
        send_message(chat_id, "📸 Camera not available.")
        return
    try:
        frame = _camera_ref.capture_jpeg()
        if not frame:
            send_message(chat_id, "Camera capture failed.")
            return
        # Get scene description via VLM
        caption = "📸 Current view"
        if _vlm_ref:
            import base64 as _b64
            b64 = _b64.b64encode(frame).decode()
            desc = _vlm_ref.describe_scene(b64)
            if desc:
                caption = f"👁️ {desc}"
        send_photo(chat_id, frame, caption=caption)
    except Exception as exc:
        send_message(chat_id, f"Camera error: {exc}")


def _handle_speed(chat_id: int, text: str) -> None:
    """Set motor speed: /speed 80, /speed 150, etc. Range 0-255."""
    parts = text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        send_message(chat_id, "Usage: /speed [0-255]\nExamples:\n  /speed 80 (slow)\n  /speed 120 (normal)\n  /speed 200 (fast)")
        return
    speed = int(parts[1])
    speed = max(0, min(255, speed))
    if _serial_ref is None:
        send_message(chat_id, "⚠️ ESP32 not connected.")
        return
    try:
        cmd = json.dumps({"action": "stop", "speed": speed, "state": "idle"}) + "\n"
        _serial_ref.write(cmd.encode())
        _serial_ref.flush()
        send_message(chat_id, f"⚡ Speed set to {speed}/255\n{'🐢 Slow' if speed < 100 else '🚶 Normal' if speed < 160 else '🏎 Fast'}")
    except Exception as exc:
        send_message(chat_id, f"Speed set failed: {exc}")


def _handle_mode_switch(chat_id: int, auto: bool) -> None:
    """Switch ESP32 between AI mode (Pi-controlled) and AUTO mode (self-driving)."""
    if _serial_ref is None:
        send_message(chat_id, "⚠️ ESP32 not connected.")
        return
    try:
        mode = "auto" if auto else "ai"
        cmd = json.dumps({"action": "mode", "mode": mode}) + "\n"
        _serial_ref.write(cmd.encode())
        _serial_ref.flush()
        if auto:
            send_message(chat_id, "🚗 AUTO mode — robot drives on its own, avoiding obstacles.")
        else:
            send_message(chat_id, "🤖 AI mode — robot waits for your commands.")
        logger.info("[telegram] mode switch to %s", mode)
    except Exception as exc:
        send_message(chat_id, f"Mode switch failed: {exc}")


def _handle_stop(chat_id: int) -> None:
    if _cancel_event:
        _cancel_event.set()
    if _serial_ref is not None:
        try:
            cmd = json.dumps({"action": "stop", "speed": 0}) + "\n"
            _serial_ref.write(cmd.encode())
            _serial_ref.flush()
        except Exception:
            pass
    send_message(chat_id, "🛑 Emergency stop! All movement halted.")
    logger.info("[telegram] /stop from chat_id=%s", chat_id)


def _handle_status(chat_id: int) -> None:
    if _body_ctx_ref is None:
        send_message(chat_id, "Status not available.")
        return
    try:
        block = _body_ctx_ref.prompt_block()
        lines = [l for l in block.splitlines() if l.strip()]
        snippet = "\n".join(lines[:12])
        send_message(chat_id, snippet or "No status yet.")
    except Exception as exc:
        send_message(chat_id, f"Status error: {exc}")


def _handle_esp32(chat_id: int) -> None:
    """Send a stop ping to ESP32 and report the last received telemetry."""
    lines = []

    # Show last telemetry
    if last_telemetry:
        t = last_telemetry
        lines.append(
            f"Last telemetry:\n"
            f"  Front: {t.get('front', '?')} cm\n"
            f"  Left:  {t.get('left', '?')} cm\n"
            f"  Right: {t.get('right', '?')} cm\n"
            f"  Action: {t.get('action', '?')}"
        )
    else:
        lines.append("No telemetry received yet from ESP32.")

    # Try sending a ping
    if _serial_ref is not None:
        try:
            import json as _json
            cmd = _json.dumps({"action": "stop", "speed": 0, "state": "idle"}) + "\n"
            _serial_ref.write(cmd.encode())
            _serial_ref.flush()
            lines.append("\nPing sent to ESP32.")
        except Exception as exc:
            lines.append(f"\nFailed to ping ESP32: {exc}")
    else:
        lines.append("\nESP32 serial not connected.")

    send_message(chat_id, "\n".join(lines))


def _handle_present_start(chat_id: int) -> None:
    """Start presentation mode."""
    if _presenter_ref is None:
        send_message(chat_id, "Presentation module not loaded.")
        return
    speech = _presenter_ref.start()
    if speech is None:
        send_message(chat_id, "No slides loaded. Put slides in slides.json.")
        return
    title = _presenter_ref.current_title()
    send_message(chat_id, f"Presentation started.\n{title}\n\nSpeaking...")
    if _speaker_ref:
        _speaker_ref.speak_blocking(speech)
    send_message(chat_id, f"[{_presenter_ref.index + 1}/{_presenter_ref.total}] Done. /next to advance.")


def _handle_present_next(chat_id: int) -> None:
    """Advance to next slide and speak it."""
    if _presenter_ref is None or not _presenter_ref.active:
        send_message(chat_id, "No presentation active. Use /present to start.")
        return
    speech = _presenter_ref.advance()
    if speech is None:
        send_message(chat_id, "End of presentation. Use /endpresent to exit.")
        return
    title = _presenter_ref.current_title()
    send_message(chat_id, f"[{_presenter_ref.index + 1}/{_presenter_ref.total}] {title}\nSpeaking...")
    if _speaker_ref:
        _speaker_ref.speak_blocking(speech)
    send_message(chat_id, "Done. /next or /prev")


def _handle_present_prev(chat_id: int) -> None:
    """Go back to previous slide."""
    if _presenter_ref is None or not _presenter_ref.active:
        send_message(chat_id, "No presentation active. Use /present to start.")
        return
    speech = _presenter_ref.previous()
    if speech is None:
        send_message(chat_id, "Already at the first slide.")
        return
    title = _presenter_ref.current_title()
    send_message(chat_id, f"[{_presenter_ref.index + 1}/{_presenter_ref.total}] {title}\nSpeaking...")
    if _speaker_ref:
        _speaker_ref.speak_blocking(speech)
    send_message(chat_id, "Done. /next or /prev")


def _handle_present_stop(chat_id: int) -> None:
    """Stop presentation mode."""
    if _presenter_ref is None or not _presenter_ref.active:
        send_message(chat_id, "No presentation active.")
        return
    _presenter_ref.stop()
    send_message(chat_id, "Presentation ended.")


def _handle_vision_question(chat_id: int) -> None:
    """Capture camera frame, describe it with VLM, send photo + description."""
    if _camera_ref is None:
        send_message(chat_id, "Camera not available.")
        return

    send_message(chat_id, "Looking...")
    try:
        jpeg = _camera_ref.capture_jpeg()
        if not jpeg:
            send_message(chat_id, "Camera capture failed.")
            return

        # Get VLM description if available
        caption = "Current camera view"
        if _vlm_ref is not None:
            try:
                import base64 as _b64
                b64 = _b64.b64encode(jpeg).decode()
                description = _vlm_ref.describe_scene(b64)
                if description:
                    caption = description
            except Exception as exc:
                logger.warning("[telegram] VLM description failed: %s", exc)

        send_photo(chat_id, jpeg, caption=caption)
        send_message(chat_id, f"I see: {caption}")

    except Exception as exc:
        send_message(chat_id, f"Vision error: {exc}")


# ── Public API ────────────────────────────────────────────────────────────────

def start(
    wake_event: threading.Event,
    cancel_event: threading.Event,
    shutdown_event: threading.Event,
    camera=None,
    body_ctx=None,
    serial=None,
    vlm=None,
    presenter=None,
    speaker=None,
) -> Optional[threading.Thread]:
    """
    Start the Telegram polling thread.  Returns the Thread object or None if
    Telegram is disabled or the token is missing.
    """
    global _wake_event, _cancel_event, _shutdown_event
    global _camera_ref, _body_ctx_ref, _serial_ref, _vlm_ref
    global _presenter_ref, _speaker_ref

    if not config.TELEGRAM_ENABLED:
        logger.info("[telegram] disabled (TELEGRAM_ENABLED=0)")
        return None

    if not config.TELEGRAM_BOT_TOKEN:
        logger.warning("[telegram] no BOT_TOKEN — Telegram input disabled")
        return None

    _wake_event     = wake_event
    _cancel_event   = cancel_event
    _shutdown_event = shutdown_event
    _camera_ref     = camera
    _body_ctx_ref   = body_ctx
    _serial_ref     = serial
    _vlm_ref        = vlm
    _presenter_ref  = presenter
    _speaker_ref    = speaker

    t = threading.Thread(target=_poll_loop, name="telegram", daemon=True)
    t.start()
    return t


def get_command() -> Optional[tuple]:
    """
    Non-blocking. Returns (chat_id, text) if a Telegram message is waiting,
    else None.  Called by the state machine instead of the mic when available.
    """
    try:
        return _tg_queue.get_nowait()
    except queue.Empty:
        return None
