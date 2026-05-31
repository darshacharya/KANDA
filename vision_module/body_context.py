"""
KANDA Vision Module — Body Context Assembler
Builds the full robot body context snapshot that is injected into every Gemini call.

This is the "self-awareness" module: it describes what the robot CAN do,
what it currently SEES, what its SENSORS say, and what it has DONE recently.

Every Gemini call (ASR intent, planner, VLM check, ReAct step) gets this context
so the LLM always reasons with full knowledge of the robot's physical state.
"""

from collections import deque
from datetime import datetime
from typing import Optional

import config

# ── Static capability description (never changes) ────────────────────────────
_CAPABILITIES = """ROBOT IDENTITY:
- Name: Kanda (ಕಂದ, means "child" in Kannada)
- Created by: Sudarshan (only mention if asked "who made you" or similar)
- Purpose: Multimodal embodied AI assistant

ROBOT CAPABILITIES:
- Movement: forward, backward, left, right, slight_left, slight_right, stop
- Speed: integer 0-255 (120=normal, 180=fast, 80=slow)
- Duration: any milliseconds (Pi controls timing, ESP32 executes)
- Camera: captures JPEG frames, can describe what it sees
- Ultrasonic sensors: front, left, right distances in cm (-1 = no reading)
- Speaker: says any text via Bluetooth speaker
- Microphone: hears and transcribes voice commands
- Telegram: accepts text, voice notes, and photos as commands
- Presentation mode: can deliver pre-scripted slide presentations

GENERAL KNOWLEDGE:
- Can answer questions about date, time, general knowledge, news, weather, etc.
- Can have conversations, answer follow-up questions using conversation history

ROBOT LIMITATIONS:
- Cannot pick up or manipulate objects
- Cannot go up stairs or large steps
- Cannot navigate more than one room from its start position
- Cannot hear direction of sound (mic is omnidirectional)
- Turns are approximate (no encoders)"""


class BodyContext:
    """
    Maintains robot state history and builds context snapshots.
    One instance lives for the entire session.
    """

    def __init__(self, conversation_maxlen: int = 20):
        self._sensors: dict = {"front": -1.0, "left": -1.0, "right": -1.0}
        self._scene: str = "Unknown — camera not yet used"
        self._state: str = "idle"
        self._action_history: deque = deque(maxlen=10)
        self._conversation: deque = deque(maxlen=conversation_maxlen)

    # ── Updaters (called by main loop) ────────────────────────────────────────

    def update_sensors(self, front: float, left: float, right: float) -> None:
        self._sensors = {"front": round(front, 1), "left": round(left, 1), "right": round(right, 1)}

    def update_scene(self, description: str) -> None:
        if description:
            self._scene = description

    def update_state(self, state: str) -> None:
        self._state = state

    def log_action(self, action: str, speed: int = 0, duration_ms: int = 0) -> None:
        entry = action
        if duration_ms > 0:
            entry += f" {duration_ms}ms"
        if speed > 0:
            entry += f" @{speed}"
        self._action_history.append(entry)

    def add_turn(self, role: str, text: str) -> None:
        """Add a conversation turn. role is 'user' or 'kanda'."""
        self._conversation.append({"role": role, "text": text})

    @property
    def conversation(self) -> list:
        return list(self._conversation)

    # ── Snapshot builder ──────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        """Return a plain dict snapshot of current body state."""
        return {
            "sensors":  dict(self._sensors),
            "scene":    self._scene,
            "state":    self._state,
            "history":  list(self._action_history),
        }

    def prompt_block(self, user_instruction: str = "") -> str:
        """
        Build the full context block to prepend to any Gemini prompt.
        This is what gives Gemini its 'body awareness'.

        Args:
            user_instruction: the user's current voice command (optional)

        Returns:
            Multi-line string ready to be inserted into a Gemini prompt.
        """
        s = self._sensors
        hist = list(self._action_history)
        hist_str = ", ".join(hist[-5:]) if hist else "none"

        # Obstacle warnings inline so LLM can plan around them
        warnings = []
        if 0 < s["front"] < 20:
            warnings.append(f"WARNING: obstacle {s['front']}cm ahead")
        if 0 < s["left"] < 15:
            warnings.append(f"WARNING: obstacle {s['left']}cm left")
        if 0 < s["right"] < 15:
            warnings.append(f"WARNING: obstacle {s['right']}cm right")
        warn_str = " | ".join(warnings) if warnings else "path appears clear"

        now = datetime.now()
        block = f"""{_CAPABILITIES}

CURRENT DATE/TIME: {now.strftime('%A, %B %d, %Y at %I:%M %p')}

CURRENT SENSOR READINGS:
  Front: {s['front']}cm  Left: {s['left']}cm  Right: {s['right']}cm
  Status: {warn_str}

CURRENT CAMERA VIEW:
  {self._scene}

RECENT ACTIONS (last 5):
  {hist_str}

ROBOT STATE: {self._state}"""

        # Include recent conversation for continuity
        if self._conversation:
            recent = list(self._conversation)[-6:]  # last 6 turns
            convo_lines = []
            for turn in recent:
                prefix = "User" if turn["role"] == "user" else "Kanda"
                convo_lines.append(f"  {prefix}: {turn['text']}")
            block += "\n\nRECENT CONVERSATION:\n" + "\n".join(convo_lines)

        if user_instruction:
            block += f"\n\nUSER INSTRUCTION: \"{user_instruction}\""

        return block

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def sensors(self) -> dict:
        return dict(self._sensors)

    @property
    def scene(self) -> str:
        return self._scene

    @property
    def front_clear(self) -> bool:
        f = self._sensors["front"]
        return f < 0 or f > 25

    @property
    def best_turn_direction(self) -> str:
        """Return 'left' or 'right' based on which side has more clearance."""
        l = self._sensors["left"]
        r = self._sensors["right"]
        if l < 0 and r < 0:
            return "left"
        if l < 0:
            return "right"
        if r < 0:
            return "left"
        return "left" if l >= r else "right"
