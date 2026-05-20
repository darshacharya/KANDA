"""
KANDA — Full Embodied AI Agent (Phase 4)

Architecture:
  - 7-state machine: IDLE → LISTENING → THINKING → ACTING/SEARCHING → SPEAKING → REPORTING → IDLE
  - Wake word thread (Porcupine offline, or Enter key fallback)
  - ESP32 heartbeat monitor (warns if telemetry stops)
  - cancel_event: lets "Hey Kanda stop" abort any running task
  - body_context: full sensor + scene + history passed to every Gemini call
  - task_agent: handles ALL intents (COMMAND / QUESTION / TASK)
  - plan_executor: walks Gemini-generated JSON plans (move, speak, capture_check, loop_while)

Usage:
    export GEMINI_API_KEY=your_key
    export PORCUPINE_ACCESS_KEY=your_key   # optional — keyboard fallback if missing
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
    # Notify ESP32 for OLED face update (non-blocking, best-effort)
    if serial_conn:
        _send_state_only(serial_conn, s.name.lower())


def _send_state_only(serial_conn, state_str: str) -> None:
    """Send a stop command that just updates the OLED face without moving."""
    try:
        with _serial_lock:
            cmd = json.dumps({"action": "stop", "speed": 0, "state": state_str}) + "\n"
            serial_conn.write(cmd.encode())
            serial_conn.flush()
    except Exception:
        pass


# ── ESP32 communication ───────────────────────────────────────────────────────

def send_to_esp32(serial_conn, action: str, speed: int, state_str: str = "") -> None:
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
        logger.debug("[ESP32] → %s @%d", action, speed)
    except Exception as exc:
        logger.error("[ESP32] send error: %s", exc)


def read_telemetry(serial_conn) -> Optional[dict]:
    if serial_conn is None:
        return None
    try:
        with _serial_lock:
            raw = serial_conn.readline().decode("utf-8", errors="ignore").strip()
        if not raw or "->" not in raw:
            return None
        parts, action_part = raw.split("->")
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
    except Exception:
        return None


# ── ESP32 heartbeat monitor ───────────────────────────────────────────────────

def heartbeat_thread(serial_conn, body_ctx: BodyContext) -> None:
    """Read telemetry continuously, update body context, detect ESP32 silence."""
    last_seen = time.time()
    while not _shutdown_event.is_set():
        t = read_telemetry(serial_conn)
        if t:
            last_seen = time.time()
            body_ctx.update_sensors(t["front"], t["left"], t["right"])
            # If ESP32 detected obstacle, cancel any running motion
            if t["action"] == "OBSTACLE":
                logger.info("[heartbeat] ESP32 OBSTACLE — setting cancel")
                _cancel_event.set()
        else:
            gap = time.time() - last_seen
            if gap > config.ESP32_HEARTBEAT_TIMEOUT_SEC:
                logger.warning("[heartbeat] no ESP32 telemetry for %.0fs", gap)
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
) -> None:
    """
    Event-driven 7-state machine.
    Blocks in IDLE until wake_event fires, then drives through the interaction.
    """

    def send(action, speed, state_str=""):
        send_to_esp32(serial_conn, action, speed, state_str)

    set_state(State.IDLE, serial_conn)
    spk.speak_blocking("Kanda online. Say Hey Kanda to begin.")
    logger.info("[main] state machine running")

    while not _shutdown_event.is_set():

        # ── IDLE: wait for wake word ───────────────────────────────────────────
        if _current_state == State.IDLE:
            _cancel_event.clear()
            woke = _wake_event.wait(timeout=0.5)
            if not woke:
                continue
            _wake_event.clear()

            if _shutdown_event.is_set():
                break

            # Brief audio confirm (beep via TTS)
            set_state(State.LISTENING, serial_conn)
            spk.speak_blocking("Yes?")

        # ── LISTENING: record voice command with VAD ───────────────────────────
        if _current_state == State.LISTENING:
            # Stop any TTS before opening mic (prevents feedback)
            spk.interrupt()
            time.sleep(0.2)

            transcript = transcriber.listen()

            if transcript is None:
                spk.speak_blocking("I couldn't hear anything.")
                set_state(State.IDLE, serial_conn)
                continue

            if transcript == "":
                # Silence — go back to idle quietly
                set_state(State.IDLE, serial_conn)
                continue

            logger.info("[main] heard: '%s'", transcript)
            set_state(State.THINKING, serial_conn)

        # ── THINKING: parse intent ─────────────────────────────────────────────
        if _current_state == State.THINKING:
            # Detect "stop" command immediately — cancel anything running
            low = transcript.lower()
            if any(w in low for w in ("stop", "halt", "cancel", "abort")):
                _cancel_event.set()
                send("stop", 0, "idle")
                body_ctx.log_action("stop (voice cancel)")
                set_state(State.SPEAKING, serial_conn)
                spk.speak_blocking("Stopping.")
                set_state(State.IDLE, serial_conn)
                continue

            intent = agent.parse_intent(transcript)
            logger.info("[main] intent: %s", intent)

            reply = intent.get("reply", "")

            # ── COMMAND ────────────────────────────────────────────────────────
            if intent["intent"] == "COMMAND":
                action = intent.get("action") or "stop"
                speed  = int(intent.get("speed", config.SPEED_NORMAL))

                set_state(State.SPEAKING, serial_conn)
                if reply:
                    spk.speak_blocking(reply)

                set_state(State.ACTING, serial_conn)
                send(action, speed, "acting")
                body_ctx.log_action(action, speed)
                set_state(State.IDLE, serial_conn)
                continue

            # ── QUESTION ───────────────────────────────────────────────────────
            elif intent["intent"] == "QUESTION":
                set_state(State.THINKING, serial_conn)
                result = agent.plan_and_execute(transcript)

                if result == "cancelled":
                    spk.speak_blocking("Cancelled.")
                set_state(State.IDLE, serial_conn)
                continue

            # ── TASK ───────────────────────────────────────────────────────────
            elif intent["intent"] == "TASK":
                goal = intent.get("goal") or transcript

                # Clarify if goal is vague
                clarification = agent.clarify(transcript)
                if clarification:
                    set_state(State.SPEAKING, serial_conn)
                    spk.speak_blocking(clarification)

                    # Wait for user answer
                    set_state(State.LISTENING, serial_conn)
                    spk.interrupt()
                    time.sleep(0.2)
                    answer = transcriber.listen()
                    if answer:
                        goal = f"{goal} — {answer}"
                        logger.info("[main] refined goal: %s", goal)

                # Check if it's a find-task or general task
                find_keywords = ("find", "look for", "search", "locate", "where is", "where's")
                is_find_task  = any(k in transcript.lower() for k in find_keywords)

                set_state(State.SEARCHING, serial_conn)
                if reply:
                    spk.speak_blocking(reply)

                if is_find_task:
                    result = agent.run_search(goal)
                else:
                    result = agent.plan_and_execute(transcript)

                set_state(State.REPORTING, serial_conn)
                if result == "found":
                    send("stop", 0, "reporting_ok")
                elif result == "cancelled":
                    spk.speak_blocking("Task cancelled.")
                    send("stop", 0, "idle")
                elif result == "not_found":
                    send("stop", 0, "reporting_fail")
                else:
                    send("stop", 0, "idle")

                set_state(State.IDLE, serial_conn)
                continue

            # ── UNKNOWN ────────────────────────────────────────────────────────
            else:
                set_state(State.SPEAKING, serial_conn)
                spk.speak_blocking("Sorry, I didn't understand. Please try again.")
                set_state(State.IDLE, serial_conn)
                continue


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 52)
    print("  KANDA — Embodied AI Agent (Phase 4)")
    print("=" * 52)
    print(f"  Model   : {config.GEMINI_MODEL}")
    print(f"  Camera  : {config.CAMERA_RESOLUTION[0]}x{config.CAMERA_RESOLUTION[1]}")
    print(f"  TTS     : {config.TTS_ENGINE}")
    print(f"  Wake wd : {'Porcupine' if config.WAKE_WORD_ENABLED and config.WAKE_WORD_KEY else 'keyboard (Enter)'}")
    print(f"  ESP32   : {'disabled' if NO_UART else config.SERIAL_PORT}")
    print("=" * 52)
    print()

    # ── Bluetooth check ────────────────────────────────────────────────────────
    check_bluetooth()
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
    transcriber = VoiceTranscriber()
    transcriber.start()

    # ── Connect to ESP32 ───────────────────────────────────────────────────────
    serial_conn = None
    if not NO_UART:
        import serial as pyserial
        for port in [config.SERIAL_PORT, "/dev/ttyACM0"]:
            try:
                serial_conn = pyserial.Serial(port, config.SERIAL_BAUD, timeout=1)
                time.sleep(2)
                serial_conn.reset_input_buffer()
                print(f"ESP32 connected: {port}")
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

    # ── Background threads ────────────────────────────────────────────────────
    if serial_conn:
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

    print("\nReady. Say 'Hey Kanda' to begin (or press Enter if using keyboard mode).")
    print("Commands: 'go forward', 'turn left', 'what do you see', 'find my bottle', ...\n")

    # ── Run state machine (blocks forever) ────────────────────────────────────
    run_state_machine(
        spk=spk,
        cam=cam,
        vlm=vlm,
        transcriber=transcriber,
        agent=agent,
        body_ctx=body_ctx,
        serial_conn=serial_conn,
    )


if __name__ == "__main__":
    main()
