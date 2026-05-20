"""
KANDA Vision Module — Task Agent
The brain that turns any user instruction into a robot plan and executes it.

Responsibilities:
  1. parse_intent()     — classify transcript into COMMAND / QUESTION / TASK / UNKNOWN
  2. plan_and_execute() — build body context, ask Gemini for a JSON plan, run it
  3. run_search()       — ReAct search loop with semantic memory and cancel support
  4. clarify()          — ask follow-up question if goal is too vague

Every Gemini call:
  - Gets full body context (sensors + scene + history + capabilities)
  - Is wrapped in gemini_call_with_timeout()
  - Returns None on failure (graceful degradation)
"""

import base64
import difflib
import json
import logging
import re
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from typing import Optional, Callable

from google import genai
from google.genai import types

import config
from body_context import BodyContext
from plan_executor import PlanExecutor

logger = logging.getLogger(__name__)


# ── Prompt templates ──────────────────────────────────────────────────────────

_INTENT_PROMPT = """{body_context}

Classify the USER INSTRUCTION above into one of these categories:

COMMAND  — simple movement or stop (e.g. "go forward", "turn left", "stop")
QUESTION — asking about the environment (e.g. "what do you see", "how far is the wall")
TASK     — goal to pursue that requires multiple steps (e.g. "find my bottle", "go to the door", "patrol the room", "dance")
UNKNOWN  — cannot understand

For COMMAND, also extract action and speed.
For TASK, extract the goal description.

Reply with ONLY this JSON:
{{"intent": "COMMAND|QUESTION|TASK|UNKNOWN", "action": "forward|backward|left|right|slight_left|slight_right|stop|null", "speed": 120, "goal": "description or null", "reply": "short spoken reply to user"}}"""


_PLANNER_PROMPT = """{body_context}

You are the motion planner for KANDA robot.
Generate a JSON plan array to accomplish the USER INSTRUCTION.

Each step must be one of:
  {{"action": "forward|backward|left|right|slight_left|slight_right|stop", "speed": 0-255, "duration_ms": N}}
  {{"action": "speak", "text": "..."}}
  {{"action": "capture_check", "query": "yes/no question", "on_yes": "stop_and_speak|continue|repeat|abort", "on_no": "continue|repeat|stop_and_speak|abort", "found_text": "text to speak if found", "max_repeats": N}}
  {{"action": "loop_while", "condition": "front > 25|left > 20|right > 20", "body": [steps...]}}
  {{"action": "wait", "duration_ms": N}}

Rules:
- Always end with {{"action":"stop","speed":0,"duration_ms":0}} if robot might be moving
- Keep plans under 20 steps total
- Use capture_check for visual goals (finding objects)
- Use loop_while for "keep going until" patterns
- For creative requests (dance, spin), make fun movement sequences

Reply with ONLY the JSON array. No explanation. No markdown fences."""


_SEARCH_CHECK_PROMPT = """Look at this image carefully.
Goal: find {goal_description}
Question: Is the target object clearly visible in this image?
Reply with ONLY "yes" or "no"."""


_SCENE_DESCRIBE_PROMPT = """Describe what you see in this image in exactly 1 sentence (under 15 words). Be specific about objects and location."""


class TaskAgent:
    """Full embodied AI agent for KANDA robot."""

    def __init__(
        self,
        serial_send_fn: Callable,
        speaker,
        camera,
        vlm,
        body_ctx: BodyContext,
        cancel_event: threading.Event,
    ):
        self._send    = serial_send_fn
        self._spk     = speaker
        self._cam     = camera
        self._vlm     = vlm
        self._ctx     = body_ctx
        self._cancel  = cancel_event
        self._client  = genai.Client(api_key=config.GEMINI_API_KEY)
        self._executor = PlanExecutor(
            serial_send_fn=serial_send_fn,
            speaker=speaker,
            camera=camera,
            vlm=vlm,
            body_ctx=body_ctx,
            cancel_event=cancel_event,
        )
        # Semantic search memory
        self._search_memory: deque = deque(maxlen=config.SEARCH_MEMORY_MAX)

    # ── Public API ────────────────────────────────────────────────────────────

    def parse_intent(self, transcript: str) -> dict:
        """
        Classify transcript. Returns:
          {"intent": "COMMAND|QUESTION|TASK|UNKNOWN", "action": ..., "speed": ...,
           "goal": ..., "reply": ...}
        """
        if not transcript.strip():
            return {"intent": "UNKNOWN", "action": None, "speed": 120,
                    "goal": None, "reply": ""}

        prompt = _INTENT_PROMPT.format(
            body_context=self._ctx.prompt_block(transcript)
        )

        def call():
            resp = self._client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=[prompt],
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=200,
                ),
            )
            return resp.text.strip()

        raw = self._gemini_timeout(call)
        if not raw:
            return {"intent": "UNKNOWN", "action": None, "speed": 120,
                    "goal": None, "reply": "Sorry, I didn't understand that."}

        return self._parse_json(raw, default={
            "intent": "UNKNOWN", "action": None, "speed": 120,
            "goal": None, "reply": "Sorry, I didn't understand."
        })

    def plan_and_execute(self, transcript: str) -> str:
        """
        Build body context, ask Gemini for a plan, execute it.
        Used for TASK and QUESTION intents.
        Returns "done", "found", "cancelled", or "error".
        """
        # Update scene before planning
        b64 = self._cam.capture_base64()
        if b64 and len(b64) > (config.CAMERA_MIN_JPEG_BYTES * 4 // 3):
            scene = self._describe_scene(b64)
            if scene:
                self._ctx.update_scene(scene)

        prompt = _PLANNER_PROMPT.format(
            body_context=self._ctx.prompt_block(transcript)
        )

        def call():
            resp = self._client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=[prompt],
                config=types.GenerateContentConfig(
                    temperature=0.3,
                    max_output_tokens=800,
                ),
            )
            return resp.text.strip()

        raw = self._gemini_timeout(call)
        if not raw:
            self._spk.speak_blocking("I couldn't plan that right now. Please try again.")
            return "error"

        plan = self._parse_plan(raw)
        if not plan:
            self._spk.speak_blocking("I couldn't understand my own plan. Please rephrase.")
            return "error"

        logger.info("[agent] executing plan with %d steps", len(plan))
        return self._executor.execute(plan)

    def run_search(self, goal: str) -> str:
        """
        ReAct search loop: move → check → remember → repeat.
        Uses semantic memory to avoid revisiting the same area.

        Args:
            goal: natural language description of what to find

        Returns:
            "found", "not_found", or "cancelled"
        """
        self._search_memory.clear()
        consecutive_skips = 0
        similarity_threshold = config.SEARCH_SIMILARITY_THRESHOLD

        for step in range(config.SEARCH_MAX_STEPS):
            if self._cancel.is_set():
                logger.info("[search] cancelled at step %d", step)
                return "cancelled"

            # 1. Capture and describe scene
            b64 = self._cam.capture_base64()
            if not b64 or len(b64) < (config.CAMERA_MIN_JPEG_BYTES * 4 // 3):
                logger.warning("[search] corrupt frame at step %d, skipping", step)
                self._move_step(step)
                continue

            scene = self._describe_scene(b64)
            if not scene:
                scene = "Unknown area"
            self._ctx.update_scene(scene)
            logger.info("[search] step %d scene: %s", step, scene)

            # 2. Check if already visited this area
            if self._already_visited(scene, similarity_threshold):
                consecutive_skips += 1
                logger.info("[search] step %d: already visited, skip", step)
                if consecutive_skips >= 5:
                    # Looping — lower threshold to allow rescanning
                    similarity_threshold = config.SEARCH_SIMILARITY_MIN
                    consecutive_skips = 0
                    logger.info("[search] lowering similarity threshold to %.1f",
                                similarity_threshold)
                self._move_step(step)
                continue

            consecutive_skips = 0

            # 3. Check if goal is visible
            if self._check_for_goal(b64, goal):
                self._send("stop", 0, "reporting_ok")
                self._ctx.log_action("stop — found target")
                found_msg = f"I found it. {scene}"
                self._spk.speak_blocking(found_msg)
                logger.info("[search] FOUND at step %d", step)
                return "found"

            # 4. Remember this location
            action = self._choose_move()
            self._search_memory.append({
                "step":   step,
                "scene":  scene,
                "action": action,
            })

            # 5. Move to next area
            self._execute_move(action)

            # 6. Check for stuck condition (all sensors blocked)
            if self._is_stuck():
                logger.info("[search] stuck at step %d — backing up", step)
                self._spk.speak_blocking("I'm stuck, backing up.")
                self._execute_move("backward")
                time.sleep(0.5)

        # Search exhausted
        searched = [m["scene"][:30] for m in list(self._search_memory)[-3:]]
        areas = ", ".join(searched) if searched else "several areas"
        msg = f"I searched {config.SEARCH_MAX_STEPS} areas including {areas}, but couldn't find it. It may be behind something."
        self._send("stop", 0, "reporting_fail")
        self._spk.speak_blocking(msg)
        return "not_found"

    def clarify(self, transcript: str) -> Optional[str]:
        """
        Ask a follow-up question if the goal is too vague.
        Returns the clarification question to speak, or None if goal is clear.
        """
        prompt = f"""{self._ctx.prompt_block(transcript)}

Is the USER INSTRUCTION above specific enough for a robot to act on, or does it need clarification?

If it needs clarification, reply with a short question (under 10 words) to ask the user.
If it's clear enough, reply with exactly: CLEAR

Reply only the question or CLEAR."""

        def call():
            resp = self._client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=[prompt],
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=50,
                ),
            )
            return resp.text.strip()

        result = self._gemini_timeout(call)
        if not result or result.upper().startswith("CLEAR"):
            return None
        return result

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _describe_scene(self, image_b64: str) -> Optional[str]:
        """Get a 1-sentence scene description from Gemini."""
        def call():
            img_bytes = base64.b64decode(image_b64)
            resp = self._client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=[
                    _SCENE_DESCRIBE_PROMPT,
                    types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
                ],
                config=types.GenerateContentConfig(
                    temperature=0.5,
                    max_output_tokens=60,
                ),
            )
            return resp.text.strip()

        return self._gemini_timeout(call)

    def _check_for_goal(self, image_b64: str, goal: str) -> bool:
        """Ask Gemini if the target is visible in the frame."""
        prompt = _SEARCH_CHECK_PROMPT.format(goal_description=goal)

        def call():
            img_bytes = base64.b64decode(image_b64)
            resp = self._client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=[
                    prompt,
                    types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
                ],
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=10,
                ),
            )
            return resp.text.strip().lower()

        answer = self._gemini_timeout(call)
        if answer is None:
            return False
        return answer.startswith("yes")

    def _already_visited(self, scene: str, threshold: float) -> bool:
        """Check if this scene is similar to a recently visited one."""
        for entry in self._search_memory:
            ratio = difflib.SequenceMatcher(
                None, scene.lower(), entry["scene"].lower()
            ).ratio()
            if ratio > threshold:
                return True
        return False

    def _choose_move(self) -> str:
        """Pick the next move based on sensor readings."""
        sensors = self._ctx.sensors
        f = sensors.get("front", -1)
        l = sensors.get("left",  -1)
        r = sensors.get("right", -1)

        # Forward if clear
        if f < 0 or f > 40:
            return "forward"

        # Turn toward clearer side
        return self._ctx.best_turn_direction

    def _execute_move(self, action: str) -> None:
        duration_ms = 1000 if action == "forward" else 700
        speed       = config.SPEED_NORMAL if action == "forward" else config.SPEED_TURN
        self._send(action, speed, "searching")
        self._ctx.log_action(action, speed, duration_ms)
        time.sleep(duration_ms / 1000)
        self._send("stop", 0, "searching")

    def _move_step(self, step: int) -> None:
        """Move one step during a search when we need to reposition."""
        action = self._choose_move()
        self._execute_move(action)

    def _is_stuck(self) -> bool:
        """True if all three sensors show close obstacles."""
        s = self._ctx.sensors
        blocked = [v for v in s.values() if 0 < v < 20]
        return len(blocked) >= 2

    def _gemini_timeout(self, fn: Callable) -> Optional[str]:
        """Run Gemini call with timeout. Returns None on timeout/error."""
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(fn)
            try:
                return fut.result(timeout=config.GEMINI_TIMEOUT_SEC)
            except TimeoutError:
                logger.warning("[agent] Gemini timed out")
                return None
            except Exception as exc:
                # Handle 429 rate limit
                if "429" in str(exc) or "quota" in str(exc).lower():
                    logger.warning("[agent] rate limited, sleeping 5s")
                    time.sleep(5)
                    try:
                        return fut.result(timeout=config.GEMINI_TIMEOUT_SEC)
                    except Exception:
                        pass
                else:
                    logger.error("[agent] Gemini error: %s", exc)
                return None

    def _parse_json(self, text: str, default: dict) -> dict:
        """Extract and parse JSON from Gemini response."""
        # Strip markdown fences
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if fenced:
            text = fenced.group(1)

        match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return default

    def _parse_plan(self, text: str) -> Optional[list]:
        """Extract JSON array plan from Gemini response."""
        # Strip markdown fences
        clean = re.sub(r"```(?:json)?", "", text).replace("```", "").strip()

        # Find array
        match = re.search(r"\[.*\]", clean, re.DOTALL)
        if match:
            try:
                plan = json.loads(match.group())
                if isinstance(plan, list):
                    return plan
            except json.JSONDecodeError as e:
                logger.error("[agent] plan JSON parse error: %s", e)

        logger.error("[agent] could not extract plan from: %s", text[:200])
        return None
