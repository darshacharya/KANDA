"""
KANDA — Full Embodied AI Agent (Phase 4)

Architecture:
  - 7-state machine: IDLE → LISTENING → THINKING → ACTING/SEARCHING → SPEAKING → REPORTING → IDLE
  - Wake word thread (openWakeWord offline, or Enter key fallback)
  - ESP32 heartbeat monitor (warns if telemetry stops)
  - cancel_event: lets "Hey Kanda stop" abort any running task
  - body_context: full sensor + scene + history passed to every LLM call
  - task_agent: handles ALL intents (COMMAND / QUESTION / TASK)
  - plan_executor: walks AI-generated JSON plans (move, speak, capture_check, loop_while)

Cloud APIs:
  - Groq (Llama 3.3 70B) — intent classification, planning, grounded Q&A
  - NVIDIA NIM (Llama 3.2 11B Vision) — scene understanding
  - Groq Whisper — speech transcription

Usage:
    export GROQ_API_KEY=your_key
    export NVIDIA_API_KEY=your_key
    python3 main.py

    # Without ESP32:
    KANDA_NO_UART=1 python3 main.py

Ctrl+C to exit.
"""

import json
import logging
import os
import signal
import sys
import threading
import time
from typing import Optional

from camera import Camera
from vlm import VLM
from speaker import Speaker, check_bluetooth
from voice_command import VoiceTranscriber
from wake_word import WakeWordDetector
from body_context import BodyContext
from task_agent import TaskAgent
import config
from config import State
import telegram_input

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)

NO_UART = os.getenv("KANDA_NO_UART", "0") == "1"


# ── Events and shared state ───────────────────────────────────────────────────

_shutdown_event = threading.Event()
_wake_event     = threading.Event()
_cancel_event   = threading.Event()
_serial_lock    = threading.Lock()

_current_state  = State.IDLE
_state_lock     = threading.Lock()


def set_state(s: State, serial_conn=None) -> None:
    global _current_state
    with _state_lock:
        _current_state = s
    logger.info("[state] → %s", s.name)
    # For ACTING/SEARCHING, skip the OLED-only stop — the movement command
    # that follows carries "state":"acting" and updates the OLED itself.
    if serial_conn and s not in (State.ACTING, State.SEARCHING):
        _send_state_only(serial_conn, s.name.lower())


def _send_state_only(serial_conn, state_str: str) -> None:
    """Update OLED face only (sends stop + state label, no movement)."""
    try:
        with _serial_lock:
            cmd = json.dumps({"action": "stop", "speed": 0, "state": state_str}) + "\n"
            serial_conn.write(cmd.encode())
            serial_conn.flush()
    except Exception as exc:
        logger.warning("[ESP32] state update failed: %s", exc)


# ── ESP32 communication ───────────────────────────────────────────────────────

# Left/right are physically swapped on the chassis — correct here
_DIRECTION_SWAP = {
    "left": "right",
    "right": "left",
    "slight_left": "slight_right",
    "slight_right": "slight_left",
}


def send_to_esp32(serial_conn, action: str, speed: int, state_str: str = "") -> None:
    action = _DIRECTION_SWAP.get(action, action)
    if serial_conn is None:
        logger.info("[ESP32-sim] %s speed=%d", action, speed)
        return
    try:
        with _serial_lock:
            cmd = json.dumps({
                "action": action,
                "speed":  speed,
                "state":  state_str or _current_state.name.lower(),
            }) + "\n"
            serial_conn.write(cmd.encode())
            serial_conn.flush()
        logger.info("[ESP32] → %s speed=%d state=%s", action, speed, state_str)
    except Exception as exc:
        logger.error("[ESP32] send error: %s", exc)


def read_telemetry(serial_conn) -> Optional[dict]:
    if serial_conn is None:
        return None
    try:
        # Check without lock first to avoid blocking senders
        if serial_conn.in_waiting == 0:
            time.sleep(0.1)
        if serial_conn.in_waiting == 0:
            return None

        with _serial_lock:
            raw_bytes = serial_conn.read(serial_conn.in_waiting)

        # Decode and find the last line with telemetry format
        lines = raw_bytes.decode("utf-8", errors="ignore").strip().split("\n")
        for line in reversed(lines):
            line = line.strip().replace("\r", "")
            if "->" in line:
                parts, action_part = line.split("->", 1)
                normalised = parts.replace(": ", ":").replace(":  ", ":")

                def extract(key):
                    for tok in normalised.split():
                        if tok.upper().startswith(key + ":"):
                            try:
                                return float(tok.split(":")[1])
                            except ValueError:
                                pass
                    return -1.0

                return {
                    "front":  extract("F"),
                    "left":   extract("L"),
                    "right":  extract("R"),
                    "action": action_part.strip(),
                }
        return None
    except Exception:
        return None


# ── ESP32 heartbeat monitor ───────────────────────────────────────────────────

def heartbeat_thread(serial_conn, body_ctx: BodyContext) -> None:
    """Read telemetry continuously, update body context, detect ESP32 silence."""
    last_seen      = time.time()
    last_warned_at = 0.0   # rate-limit the warning to once per 10s

    while not _shutdown_event.is_set():
        t = read_telemetry(serial_conn)
        if t:
            last_seen = time.time()
            body_ctx.update_sensors(t["front"], t["left"], t["right"])
            telegram_input.last_telemetry = t   # expose to /esp32 command
            if t["action"] == "OBSTACLE":
                logger.info("[heartbeat] ESP32 OBSTACLE — setting cancel")
                _cancel_event.set()
        else:
            gap = time.time() - last_seen
            if gap > config.ESP32_HEARTBEAT_TIMEOUT_SEC:
                now = time.time()
                if now - last_warned_at >= 10.0:   # warn at most once every 10s
                    logger.warning("[heartbeat] no ESP32 telemetry for %.0fs", gap)
                    last_warned_at = now
        time.sleep(0.1)


# ── Main state machine ────────────────────────────────────────────────────────

def run_state_machine(
    spk: Speaker,
    cam: Camera,
    vlm: VLM,
    transcriber: VoiceTranscriber,
    agent: TaskAgent,
    body_ctx: BodyContext,
    serial_conn,
    wake_detector=None,
) -> None:
    """
    Event-driven 7-state machine.
    Blocks in IDLE until wake_event fires, then drives through the interaction.
    """

    def send(action, speed, state_str=""):
        send_to_esp32(serial_conn, action, speed, state_str)

    set_state(State.IDLE, serial_conn)
    if wake_detector:
        wake_detector.pause()
    spk.speak_blocking("Kanda online. Say Hey Jarvis to begin.")
    time.sleep(0.3)
    if wake_detector:
        wake_detector.resume()
    logger.info("[main] state machine running")

    while not _shutdown_event.is_set():

        # ── IDLE: wait for wake word ───────────────────────────────────────────
        if _current_state == State.IDLE:
            _cancel_event.clear()
            # Make sure wake word listener has the mic
            # Small delay prevents speaker buffer from triggering false wake
            if wake_detector:
                if spk.is_speaking:
                    time.sleep(0.5)
                wake_detector.resume()
            woke = _wake_event.wait(timeout=0.5)
            if not woke:
                continue
            _wake_event.clear()

            if _shutdown_event.is_set():
                break

            # Brief audio confirm (beep via TTS)
            set_state(State.LISTENING, serial_conn)
            spk.speak_blocking("Yes?")

        # ── LISTENING: Telegram queue first, mic fallback ─────────────────────
        if _current_state == State.LISTENING:
            tg = telegram_input.get_command()
            if tg:
                tg_chat_id, transcript = tg
                logger.info("[main] telegram command from %s: '%s'", tg_chat_id, transcript)
                set_state(State.THINKING, serial_conn)
            else:
                tg_chat_id = None
                # Stop any TTS before opening mic (prevents feedback)
                spk.interrupt()
                time.sleep(0.2)

                transcript = transcriber.listen()

                if transcript is None or transcript == "":
                    spk.speak_blocking("I'm here. Tell me what I can do for you — move around, describe what I see, or find something.")
                    time.sleep(0.5)  # allow Bluetooth speaker buffer to drain
                    set_state(State.IDLE, serial_conn)
                    continue

                logger.info("[main] heard: '%s'", transcript)
                set_state(State.THINKING, serial_conn)

        # ── THINKING: parse intent ─────────────────────────────────────────────
        if _current_state == State.THINKING:

            def tg_reply(text: str) -> None:
                """Send a message back to Telegram chat if command came from there."""
                if tg_chat_id:
                    telegram_input.send_message(tg_chat_id, text)

            def say(text: str) -> None:
                """Speak via TTS and mirror the reply to Telegram.
                Pauses wake word detection to prevent self-triggering."""
                if wake_detector:
                    wake_detector.pause()
                spk.speak_blocking(text)
                time.sleep(0.3)  # allow speaker buffer to drain
                if wake_detector:
                    wake_detector.resume()
                tg_reply(text)
                body_ctx.add_turn("kanda", text)

            # Log user turn for conversation memory
            body_ctx.add_turn("user", transcript)

            # Detect "stop" command immediately — cancel anything running
            low = transcript.lower()

            if any(w in low for w in ("stop", "halt", "cancel", "abort")):
                _cancel_event.set()
                send("stop", 0, "idle")
                body_ctx.log_action("stop (cancel)")
                set_state(State.SPEAKING, serial_conn)
                say("Stopping.")
                set_state(State.IDLE, serial_conn)
                continue

            intent = agent.parse_intent(transcript)
            logger.info("[main] intent: %s", intent)

            reply = intent.get("reply", "")

            # ── COMMAND ────────────────────────────────────────────────────────
            if intent["intent"] == "COMMAND":
                action   = intent.get("action") or "stop"
                speed    = int(intent.get("speed", config.SPEED_NORMAL))

                # Use degrees for turns, duration for forward/backward
                degrees  = float(intent.get("degrees", 0))
                if degrees and action in ("left", "right", "slight_left", "slight_right"):
                    duration = degrees * config.TURN_MS_PER_DEG / 1000
                    speed = speed or config.SPEED_TURN
                else:
                    duration = float(intent.get("duration", 2.0))  # 2s default for real movement

                # Start moving immediately, speak acknowledgment in background
                set_state(State.ACTING, serial_conn)
                send(action, speed, "acting")
                body_ctx.log_action(action, speed)

                if reply:
                    spk.speak(reply)
                    tg_reply(reply)
                    body_ctx.add_turn("kanda", reply)
                else:
                    ack = {
                        "forward": "Moving forward.",
                        "backward": "Going back.",
                        "left": "Turning left.",
                        "right": "Turning right.",
                        "slight_left": "Slight left.",
                        "slight_right": "Slight right.",
                        "stop": "Stopping.",
                    }
                    ack_text = ack.get(action, "OK.")
                    spk.speak(ack_text)
                    tg_reply(ack_text)
                    body_ctx.add_turn("kanda", ack_text)

                label = f"{degrees}°" if degrees else f"{duration}s"
                tg_reply(f"⚡ {action} ({label})")

                # Keep-alive: resend command every 0.5s so ESP32 stays moving
                deadline = time.time() + duration
                while time.time() < deadline:
                    if _cancel_event.wait(timeout=0.5):
                        break
                    send(action, speed, "acting")

                send("stop", 0, "idle")
                set_state(State.IDLE, serial_conn)
                continue

            # ── QUESTION ───────────────────────────────────────────────────────
            elif intent["intent"] == "QUESTION":
                # Vision questions need the camera — always use plan_and_execute
                vision_words = ("see", "look", "looking", "front", "around",
                                "view", "camera", "image", "show", "describe",
                                "watching", "visible", "scene")
                needs_vision = any(w in low for w in vision_words)

                if not needs_vision and reply and len(reply) > 5 and reply.lower() not in ("null", "none"):
                    # Non-vision questions: speak Groq's reply directly
                    set_state(State.SPEAKING, serial_conn)
                    say(reply)
                    body_ctx.add_turn("kanda", reply)
                    set_state(State.IDLE, serial_conn)
                    continue

                set_state(State.THINKING, serial_conn)
                if needs_vision:
                    say("Let me look.")
                    # Capture image + describe using VLM directly
                    frame_b64 = cam.capture_base64()
                    if frame_b64:
                        description = vlm.describe_scene(frame_b64, prompt=(
                            f"You are Kanda, an AI robot. The user asked: \"{transcript}\"\n"
                            "Describe what you see in 2-3 conversational sentences."
                        ))
                        if description:
                            set_state(State.SPEAKING, serial_conn)
                            say(description)
                            body_ctx.add_turn("kanda", description)
                            tg_reply(f"👁️ {description}")
                            # Also send the image to Telegram
                            jpeg = cam.capture_jpeg()
                            if jpeg:
                                telegram_input.broadcast_photo(jpeg, caption="What I see right now")
                        else:
                            say("I captured an image but couldn't describe it.")
                            tg_reply("Couldn't describe the scene.")
                    else:
                        say("Camera didn't capture anything.")
                        tg_reply("Camera error.")
                else:
                    say("Let me think.")
                    result = agent.plan_and_execute(transcript)
                    if result and result != "cancelled":
                        tg_reply(str(result))
                    if result == "cancelled":
                        say("Cancelled.")
                set_state(State.IDLE, serial_conn)
                continue

            # ── TASK ───────────────────────────────────────────────────────────
            elif intent["intent"] == "TASK":
                goal = intent.get("goal") or transcript

                # Check if it's a find-task or general task
                find_keywords = ("find", "look for", "search", "locate", "where is", "where's")
                is_find_task  = any(k in transcript.lower() for k in find_keywords)
                dance_keywords = ("dance", "groove", "moves", "boogie")
                is_dance = any(k in transcript.lower() for k in dance_keywords)

                set_state(State.ACTING, serial_conn)
                if is_dance:
                    say("Watch my moves!")
                    tg_reply("💃 Dancing!")
                    dance_moves = [
                        ("right", 0.6), ("left", 0.6), ("right", 0.6), ("left", 0.6),
                        ("forward", 0.5), ("backward", 0.5), ("forward", 0.5), ("backward", 0.5),
                        ("right", 0.8), ("right", 0.8), ("left", 0.8), ("left", 0.8),
                        ("forward", 0.4), ("backward", 0.4), ("right", 0.6), ("left", 0.6),
                    ]
                    for move, dur in dance_moves:
                        if _cancel_event.is_set():
                            break
                        send(move, config.SPEED_TURN, "acting")
                        time.sleep(dur)
                        send("stop", 0, "acting")
                        time.sleep(0.1)
                    send("stop", 0, "idle")
                    say("How was that?")
                    result = "done"
                elif is_find_task:
                    set_state(State.SEARCHING, serial_conn)
                    say(f"Searching for {goal}.")
                    tg_reply(f"🔍 Searching for: {goal}")
                    result = agent.run_search(goal)
                else:
                    say("On it.")
                    result = agent.plan_and_execute(transcript)

                set_state(State.REPORTING, serial_conn)
                if result == "found":
                    send("stop", 0, "reporting_ok")
                    tg_reply("✅ Task done — target found!")
                elif result == "cancelled":
                    say("Task cancelled.")
                    send("stop", 0, "idle")
                    tg_reply("🛑 Task cancelled.")
                elif result == "not_found":
                    send("stop", 0, "reporting_fail")
                    tg_reply("❌ Could not find the target.")
                else:
                    if result:
                        tg_reply(str(result))
                    send("stop", 0, "idle")

                set_state(State.IDLE, serial_conn)
                continue

            # ── UNKNOWN ────────────────────────────────────────────────────────
            else:
                set_state(State.SPEAKING, serial_conn)
                say("Sorry, I didn't understand. Please try again.")
                set_state(State.IDLE, serial_conn)
                continue


# ── Main ──────────────────────────────────────────────────────────────────────

def run_health_check(cam, vlm, spk, mic_ok, serial_conn, bt_ok) -> str:
    """Run system health check and return a status report string."""
    status = []
    status.append("━━━ KANDA System Health ━━━")

    # Camera
    try:
        frame = cam.capture_jpeg()
        if frame and len(frame) > 5000:
            status.append("✓ Camera: OK (640×480)")
        else:
            status.append("✗ Camera: frame too small")
    except Exception as e:
        status.append(f"✗ Camera: {e}")

    # Microphone
    if mic_ok:
        from mic import SAMPLE_RATE
        status.append(f"✓ Microphone: OK ({SAMPLE_RATE} Hz)")
    else:
        status.append("✗ Microphone: failed to init")

    # Speaker / Bluetooth
    if bt_ok:
        status.append("✓ Bluetooth speaker: connected")
    else:
        status.append("⚠ Bluetooth speaker: NOT FOUND")

    # Groq API (primary text/ASR)
    import urllib.request, json as _json
    if config.GROQ_API_KEY:
        try:
            payload = _json.dumps({
                "model": config.GROQ_MODEL,
                "messages": [{"role": "user", "content": "Say OK"}],
                "max_tokens": 5,
            }).encode()
            req = urllib.request.Request(
                config.GROQ_ENDPOINT, data=payload,
                headers={"Authorization": f"Bearer {config.GROQ_API_KEY}",
                         "Content-Type": "application/json", "User-Agent": "KANDA/1.0"})
            resp = urllib.request.urlopen(req, timeout=10)
            status.append(f"✓ Groq: OK ({config.GROQ_MODEL})")
        except Exception as e:
            status.append(f"✗ Groq: {str(e)[:60]}")
    else:
        status.append("⚠ Groq: no API key")

    # NVIDIA NIM (primary vision)
    if config.NVIDIA_API_KEY:
        try:
            payload = _json.dumps({
                "model": config.NVIDIA_VLM_MODEL,
                "messages": [{"role": "user", "content": [{"type": "text", "text": "Say OK"}]}],
                "max_tokens": 5,
            }).encode()
            req = urllib.request.Request(
                config.NVIDIA_ENDPOINT, data=payload,
                headers={"Authorization": f"Bearer {config.NVIDIA_API_KEY}",
                         "Content-Type": "application/json", "User-Agent": "KANDA/1.0"})
            resp = urllib.request.urlopen(req, timeout=10)
            status.append(f"✓ NVIDIA NIM: OK ({config.NVIDIA_VLM_MODEL})")
        except Exception as e:
            status.append(f"⚠ NVIDIA NIM: {str(e)[:60]}")
    else:
        status.append("⚠ NVIDIA NIM: no API key")

    # ESP32
    if NO_UART:
        status.append("⚠ ESP32: disabled (KANDA_NO_UART=1)")
    elif serial_conn:
        status.append(f"✓ ESP32: connected ({serial_conn.port})")
    else:
        status.append("✗ ESP32: not found")

    # Telegram
    if config.TELEGRAM_ENABLED:
        status.append("✓ Telegram: enabled")
    else:
        status.append("✗ Telegram: disabled")

    status.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(status)


def main():
    print("=" * 52)
    print("  KANDA — Embodied AI Agent (Phase 4)")
    print("=" * 52)
    print(f"  LLM     : Groq ({config.GROQ_MODEL})")
    print(f"  VLM     : NVIDIA NIM ({config.NVIDIA_VLM_MODEL})")
    print(f"  Camera  : {config.CAMERA_RESOLUTION[0]}x{config.CAMERA_RESOLUTION[1]}")
    print(f"  TTS     : {config.TTS_ENGINE}")
    print(f"  Wake wd : {'openWakeWord (' + config.WAKE_WORD_MODEL + ')' if config.WAKE_WORD_ENABLED else 'keyboard (Enter)'}")
    print(f"  ESP32   : {'disabled' if NO_UART else config.SERIAL_PORT}")
    print(f"  Telegram: {'enabled' if config.TELEGRAM_ENABLED else 'disabled'}")
    print("=" * 52)
    print()

    # ── Bluetooth check ────────────────────────────────────────────────────────
    bt_ok = check_bluetooth()
    print()

    # ── Shared body context ────────────────────────────────────────────────────
    body_ctx = BodyContext()

    # ── Initialise modules ─────────────────────────────────────────────────────
    print("Starting camera...")
    cam = Camera()
    cam.start()

    print("Initializing VLM...")
    vlm = VLM()

    print("Starting speaker...")
    spk = Speaker()
    spk.start()

    print("Starting microphone / transcriber...")
    mic_ok = False
    transcriber = VoiceTranscriber()
    try:
        transcriber.start()
        mic_ok = True
    except Exception as e:
        logger.warning("Mic init failed: %s — voice input disabled, Telegram still works", e)

    # ── Connect to ESP32 ───────────────────────────────────────────────────────
    serial_conn = None
    if not NO_UART:
        import serial as pyserial
        for port in [config.SERIAL_PORT, "/dev/ttyACM0"]:
            try:
                serial_conn = pyserial.Serial(port, config.SERIAL_BAUD, timeout=0.5)
                time.sleep(1)
                serial_conn.reset_input_buffer()
                # Wait for telemetry to confirm connection
                for _ in range(10):
                    if serial_conn.in_waiting > 0:
                        raw = serial_conn.read(serial_conn.in_waiting).decode("utf-8", errors="ignore")
                        if "->" in raw:
                            print(f"ESP32 connected: {port} — telemetry OK")
                            break
                    time.sleep(0.5)
                else:
                    print(f"ESP32 connected: {port} (no telemetry yet)")
                break
            except Exception as exc:
                logger.warning("Port %s failed: %s", port, exc)
        if serial_conn is None:
            print("ESP32 not found — running without movement")

    # ── Build send function (passed to agent/executor) ─────────────────────────
    def send_fn(action: str, speed: int, state_str: str = "") -> None:
        send_to_esp32(serial_conn, action, speed, state_str)

    # ── Task agent ─────────────────────────────────────────────────────────────
    agent = TaskAgent(
        serial_send_fn=send_fn,
        speaker=spk,
        camera=cam,
        vlm=vlm,
        body_ctx=body_ctx,
        cancel_event=_cancel_event,
    )

    # ── Wake word detector ────────────────────────────────────────────────────
    wake_detector = WakeWordDetector(wake_event=_wake_event)
    wake_detector.start()

    # ── Telegram bot input ────────────────────────────────────────────────────
    telegram_input.start(
        wake_event=_wake_event,
        cancel_event=_cancel_event,
        shutdown_event=_shutdown_event,
        camera=cam,
        body_ctx=body_ctx,
        serial=serial_conn,
        serial_lock=_serial_lock,
        vlm=vlm,
        speaker=spk,
    )

    # ── Health check — test all systems and report via Telegram ──────────────
    health_report = run_health_check(cam, vlm, spk, mic_ok, serial_conn, bt_ok)
    print(health_report)
    telegram_input.register_commands()
    telegram_input.broadcast(health_report)
    telegram_input.send_welcome()
    logger.info("[health] status + welcome sent to Telegram")

    # ── Background threads ────────────────────────────────────────────────────
    if serial_conn:
        # Flush any partial line that accumulated since startup so the first
        # readline() in heartbeat_thread always gets a complete line.
        serial_conn.reset_input_buffer()
        hb = threading.Thread(
            target=heartbeat_thread,
            args=(serial_conn, body_ctx),
            name="heartbeat",
            daemon=True,
        )
        hb.start()

    # ── Graceful shutdown ─────────────────────────────────────────────────────
    def shutdown(sig, frame):
        print("\n\nShutting down...")
        _shutdown_event.set()
        _wake_event.set()       # unblock wake wait
        _cancel_event.set()     # abort any running task
        send_to_esp32(serial_conn, "stop", 0, "idle")
        time.sleep(0.5)
        spk.speak_blocking("Shutting down.")
        time.sleep(2)
        wake_detector.stop()
        transcriber.stop()
        spk.stop()
        cam.stop()
        if serial_conn:
            serial_conn.close()
        sys.exit(0)

    signal.signal(signal.SIGINT,  shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print("\nReady. Say 'Hey Kanda' or send a message via Telegram.")
    print("Commands: 'go forward', 'turn left', 'what do you see', 'find my bottle', ...\n")

    # ── Run state machine with crash recovery ─────────────────────────────────
    MAX_RESTARTS = 5
    restart_count = 0

    while not _shutdown_event.is_set() and restart_count < MAX_RESTARTS:
        try:
            run_state_machine(
                spk=spk,
                cam=cam,
                vlm=vlm,
                transcriber=transcriber,
                agent=agent,
                body_ctx=body_ctx,
                serial_conn=serial_conn,
                wake_detector=wake_detector,
            )
            break  # Normal exit
        except Exception as exc:
            restart_count += 1
            logger.error("[CRASH] State machine exception (restart %d/%d): %s",
                         restart_count, MAX_RESTARTS, exc, exc_info=True)
            send_to_esp32(serial_conn, "stop", 0, "idle")
            telegram_input.broadcast(
                f"⚠️ KANDA crashed (attempt {restart_count}/{MAX_RESTARTS}): {str(exc)[:100]}\nRestarting..."
            )
            _cancel_event.clear()
            time.sleep(2)

    if restart_count >= MAX_RESTARTS:
        logger.critical("[CRASH] Max restarts reached — shutting down")
        telegram_input.broadcast("🛑 KANDA crashed too many times. Manual restart required.")
        send_to_esp32(serial_conn, "stop", 0, "idle")


if __name__ == "__main__":
    main()
