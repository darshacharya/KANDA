"""
KANDA Vision Module — Plan Executor
Walks a Gemini-generated JSON plan array step by step.

Plan format (Gemini must output this):
[
  {"action": "forward",       "speed": 120, "duration_ms": 2000},
  {"action": "left",          "speed": 100, "duration_ms": 5000},
  {"action": "speak",         "text": "Turning left now"},
  {"action": "capture_check", "query": "Is there a blue bottle?",
                               "on_yes": "stop_and_speak", "on_no": "continue",
                               "found_text": "Found it!", "max_repeats": 10},
  {"action": "loop_while",    "condition": "front > 25",
                               "body": [{"action":"forward","speed":120,"duration_ms":500}]},
  {"action": "wait",          "duration_ms": 1000},
  {"action": "stop"}
]

Step types:
  move     : forward / backward / left / right / slight_left / slight_right / stop
  speak    : say text via TTS
  capture_check : take photo, ask VLM yes/no, branch
  loop_while    : repeat body while sensor condition is true
  wait          : pause N ms
"""

import json
import logging
import re
import time
import threading
from typing import Optional, Callable

import config
from body_context import BodyContext

logger = logging.getLogger(__name__)

_MOVE_ACTIONS = {
    "forward", "backward", "left", "right",
    "slight_left", "slight_right", "stop",
}




class PlanExecutor:
    """
    Executes a JSON plan array produced by the AI planner (Groq).
    Handles move, speak, capture_check, loop_while, wait steps.
    Respects cancel_event to abort mid-plan.
    """

    def __init__(
        self,
        serial_send_fn: Callable,    # fn(action, speed, state_str) → None
        speaker,                      # Speaker instance
        camera,                       # Camera instance
        vlm,                          # VLM instance
        body_ctx: BodyContext,
        cancel_event: threading.Event,
    ):
        self._send   = serial_send_fn
        self._spk    = speaker
        self._cam    = camera
        self._vlm    = vlm
        self._ctx    = body_ctx
        self._cancel = cancel_event

    # ── Public entry point ────────────────────────────────────────────────────

    def execute(self, plan: list[dict]) -> str:
        """
        Execute plan steps. Returns "done", "cancelled", or "error".
        """
        if not plan or not isinstance(plan, list):
            return "error"

        plan = plan[:config.PLAN_MAX_STEPS]   # safety cap

        for i, step in enumerate(plan):
            if self._cancel.is_set():
                logger.info("[executor] cancelled at step %d", i)
                return "cancelled"

            result = self._execute_step(step, plan, i)
            if result == "cancelled":
                return "cancelled"
            if result == "found":
                return "found"
            if result == "error":
                logger.warning("[executor] step %d error, continuing", i)

        return "done"

    # ── Step dispatcher ───────────────────────────────────────────────────────

    def _execute_step(self, step: dict, plan: list, step_idx: int) -> str:
        action = step.get("action", "stop")

        if action in _MOVE_ACTIONS:
            return self._step_move(step)
        elif action == "speak":
            return self._step_speak(step)
        elif action == "capture_check":
            return self._step_capture_check(step, plan, step_idx)
        elif action == "loop_while":
            return self._step_loop_while(step)
        elif action == "wait":
            return self._step_wait(step)
        else:
            logger.warning("[executor] unknown action: %s", action)
            return "ok"

    # ── Step: move ────────────────────────────────────────────────────────────

    def _step_move(self, step: dict) -> str:
        action      = step.get("action", "stop")
        speed       = int(step.get("speed", config.SPEED_NORMAL))
        duration_ms = int(step.get("duration_ms", 0))

        self._send(action, speed, "acting")
        self._ctx.log_action(action, speed, duration_ms)

        if duration_ms > 0:
            deadline = time.time() + duration_ms / 1000
            while time.time() < deadline:
                if self._cancel.is_set():
                    self._send("stop", 0, "idle")
                    return "cancelled"
                time.sleep(0.05)
            self._send("stop", 0, "acting")

        return "ok"

    # ── Step: speak ───────────────────────────────────────────────────────────

    def _step_speak(self, step: dict) -> str:
        text = step.get("text", "")
        if text:
            self._spk.speak_blocking(text)
            self._ctx.log_action(f"speak: {text[:30]}")
        return "ok"

    # ── Step: capture_check ───────────────────────────────────────────────────

    def _step_capture_check(self, step: dict, plan: list, step_idx: int) -> str:
        """
        Capture a camera frame, ask Gemini a yes/no question about it.
        on_yes / on_no can be:
          "continue"       — move to next step
          "stop_and_speak" — stop, speak found_text, return "found"
          "repeat"         — repeat this step from beginning (up to max_repeats)
          "abort"          — stop everything
        """
        query        = step.get("query", "Is there anything notable here?")
        on_yes       = step.get("on_yes", "continue")
        on_no        = step.get("on_no", "continue")
        found_text   = step.get("found_text", "I found it.")
        max_repeats  = int(step.get("max_repeats", 10))

        for repeat in range(max_repeats + 1):
            if self._cancel.is_set():
                return "cancelled"

            b64 = self._cam.capture_base64()
            if not b64 or len(b64) < (config.CAMERA_MIN_JPEG_BYTES * 4 // 3):
                logger.warning("[executor] corrupt frame, skipping check")
                return "ok"

            self._ctx.update_scene(self._vlm.last_description or "")

            # Ask Gemini the yes/no question
            answer = self._ask_yes_no(b64, query)
            logger.info("[executor] capture_check: %s → %s", query[:50], answer)

            branch = on_yes if answer else on_no

            if branch == "stop_and_speak":
                self._send("stop", 0, "reporting")
                self._ctx.log_action("stop (found)")
                self._spk.speak_blocking(found_text)
                return "found"
            elif branch == "abort":
                self._send("stop", 0, "idle")
                return "cancelled"
            elif branch == "repeat":
                if repeat >= max_repeats:
                    logger.info("[executor] capture_check max_repeats reached")
                    return "ok"
                # Take a small move step before repeating
                self._step_move({"action": "forward", "speed": config.SPEED_SLOW,
                                  "duration_ms": 500})
                continue
            else:
                return "ok"

        return "ok"

    # ── Step: loop_while ──────────────────────────────────────────────────────

    def _step_loop_while(self, step: dict) -> str:
        """
        Repeat body steps while a sensor condition is true.
        Conditions: "front > N", "left > N", "right > N"
        (N in cm)
        """
        condition = step.get("condition", "")
        body      = step.get("body", [])
        max_iter  = config.PLAN_LOOP_MAX_ITER

        for _ in range(max_iter):
            if self._cancel.is_set():
                return "cancelled"

            if not self._eval_condition(condition):
                break

            for sub_step in body:
                if self._cancel.is_set():
                    return "cancelled"
                result = self._execute_step(sub_step, [], 0)
                if result in ("cancelled", "found"):
                    return result

        return "ok"

    # ── Step: wait ────────────────────────────────────────────────────────────

    def _step_wait(self, step: dict) -> str:
        duration_ms = int(step.get("duration_ms", 500))
        deadline = time.time() + duration_ms / 1000
        while time.time() < deadline:
            if self._cancel.is_set():
                return "cancelled"
            time.sleep(0.05)
        return "ok"

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _ask_yes_no(self, image_b64: str, query: str) -> bool:
        """Ask VLM (NVIDIA NIM) a yes/no question about the camera frame."""
        prompt = (
            f"Look at this image carefully.\n"
            f"Question: {query}\n"
            f"Reply with ONLY the word 'yes' or 'no'. Nothing else."
        )
        answer = self._vlm.describe_scene(image_b64, prompt=prompt)
        if answer is None:
            return False
        lower = answer.lower().strip()
        return lower.startswith("yes") or "yes" in lower.split()[:3]

    # Allowed pattern: "<sensor> <op> <number>"  e.g. "front > 25", "left < 15.5"
    _CONDITION_RE = re.compile(
        r"^\s*(front|left|right)\s*(>|<|>=|<=|==)\s*(-?\d+(?:\.\d+)?)\s*$",
        re.IGNORECASE,
    )

    def _eval_condition(self, condition: str) -> bool:
        """
        Evaluate a sensor condition string safely (no eval()).
        Supported: "front > N", "front < N", "left >= N", "right <= N", etc.
        Returns False for any unrecognised or malformed condition.
        """
        m = self._CONDITION_RE.match(condition)
        if not m:
            logger.warning("[executor] unrecognised condition (ignored): %s", condition)
            return False

        sensor_name = m.group(1).lower()
        operator    = m.group(2)
        threshold   = float(m.group(3))
        value       = self._ctx.sensors.get(sensor_name, -1.0)

        ops = {
            ">":  lambda a, b: a > b,
            "<":  lambda a, b: a < b,
            ">=": lambda a, b: a >= b,
            "<=": lambda a, b: a <= b,
            "==": lambda a, b: a == b,
        }
        return ops[operator](value, threshold)
