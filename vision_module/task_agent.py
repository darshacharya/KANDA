"""
KANDA Vision Module — Task Agent
The brain that turns any user instruction into a robot plan and executes it.

Responsibilities:
  1. parse_intent()     — classify transcript into COMMAND / QUESTION / TASK / UNKNOWN
  2. plan_and_execute() — build body context, ask Groq for a JSON plan, run it
  3. run_search()       — ReAct search loop with semantic memory and cancel support
  4. clarify()          — ask follow-up question if goal is too vague

Every LLM/VLM call:
  - Gets full body context (sensors + scene + history + capabilities)
  - Uses Groq Llama 3.3 (text) or NVIDIA NIM Llama 3.2 (vision)
  - Returns None on failure (graceful degradation)
"""

import base64
import difflib
import json
import logging
import re
import threading
import time
import urllib.request
from collections import deque
from typing import Optional, Callable

import config
import telegram_input
from body_context import BodyContext
from plan_executor import PlanExecutor

logger = logging.getLogger(__name__)


# ── Prompt templates ──────────────────────────────────────────────────────────

_INTENT_PROMPT = """{body_context}

Classify the USER INSTRUCTION above into one of these categories:

COMMAND  — a SINGLE movement or stop (e.g. "go forward", "turn left", "stop", "reverse for 3 seconds")
QUESTION — asking about the environment, general knowledge, people, facts, time, date, news, weather, sports scores, or any informational query (e.g. "what do you see", "how far is the wall", "who is virat kohli", "what time is it", "what's the weather")
TASK     — MULTIPLE steps, sequences, compound commands, or goals (e.g. "find my bottle", "go forward then turn right", "move forward 1 sec then turn left and right", "patrol the room", "dance", "go to the door")
UNKNOWN  — cannot understand or completely unintelligible

IMPORTANT: If the instruction contains "then", "and then", multiple actions, or a sequence of movements — classify as TASK, NOT COMMAND.
Examples of TASK (NOT COMMAND):
  - "move forward then turn right" → TASK
  - "go forward for 1 sec then turn left and then right" → TASK
  - "forward, right, forward" → TASK

For COMMAND, extract action, speed, and duration in seconds (default 0.5 if not specified).
  Examples: "forward 2 seconds" → duration=2.0, "reverse 1 second" → action=backward duration=1.0
For TASK, extract the goal description (the full instruction).
For QUESTION, provide a helpful spoken reply answering the question as Kanda (friendly, brief).

Reply with ONLY this JSON:
{{"intent": "COMMAND|QUESTION|TASK|UNKNOWN", "action": "forward|backward|left|right|slight_left|slight_right|stop|null", "speed": 120, "duration": 0.5, "degrees": 90, "goal": "description or null", "reply": "short friendly spoken reply"}}
Notes: For turns, use "degrees" (90=right angle, 180=U-turn). For forward/backward, use "duration" in seconds."""


_PLANNER_PROMPT = """{body_context}

You are the motion planner for KANDA robot.
Generate a JSON plan array to accomplish the USER INSTRUCTION.

Each step must be one of:
  {{"action": "forward|backward", "speed": 0-255, "duration_ms": N}}
  {{"action": "left|right|slight_left|slight_right", "speed": 0-255, "degrees": N}}
  {{"action": "stop", "speed": 0}}
  {{"action": "speak", "text": "..."}}
  {{"action": "capture_check", "query": "yes/no question", "on_yes": "stop_and_speak|continue|repeat|abort", "on_no": "continue|repeat|stop_and_speak|abort", "found_text": "text to speak if found", "max_repeats": N}}
  {{"action": "loop_while", "condition": "front > 25|left > 20|right > 20", "body": [steps...]}}
  {{"action": "wait", "duration_ms": N}}

Rules:
- For turns, ALWAYS use "degrees" (e.g. 90 for a right angle, 180 for U-turn, 360 for full spin)
- For forward/backward, use "duration_ms" (e.g. 1000 = 1 second)
- Always end with {{"action":"stop","speed":0}} if robot might be moving
- Keep plans under 20 steps total
- Use capture_check for visual goals (finding objects)
- Use loop_while for "keep going until" patterns
- For creative requests (dance, spin), make fun movement sequences

Reply with ONLY the JSON array. No explanation. No markdown fences."""


_SEARCH_CHECK_PROMPT = """You are a robot searching for: {goal_description}

Look at this image carefully. Is the EXACT target clearly visible?
- YES ONLY if you can clearly and confidently identify the target in the image
- NO if the target is NOT visible, or if you are unsure, or if something only vaguely resembles it

Be strict — do NOT say YES unless you are highly confident the target is actually in the image.
Reply with EXACTLY one word: YES or NO."""


_SCENE_DESCRIBE_PROMPT = """Describe what you see in this image in exactly 1 sentence (under 15 words). Be specific about objects and location."""

_GROUNDED_KEYWORDS = (
    "news", "weather", "score", "latest", "today", "current", "live",
    "happening", "update", "price", "stock", "match", "election",
    "who won", "who is", "trending", "temperature", "forecast",
)


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
        self._executor = PlanExecutor(
            serial_send_fn=serial_send_fn,
            speaker=speaker,
            camera=camera,
            vlm=vlm,
            body_ctx=body_ctx,
            cancel_event=cancel_event,
        )
        self._search_memory: deque = deque(maxlen=config.SEARCH_MEMORY_MAX)

    def _call_groq(self, prompt: str, temperature: float = 0.1, max_tokens: int = 300) -> Optional[str]:
        """Call Groq API (OpenAI-compatible). Returns text or None."""
        payload = json.dumps({
            "model": config.GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }).encode()
        req = urllib.request.Request(
            config.GROQ_ENDPOINT,
            data=payload,
            headers={
                "Authorization": f"Bearer {config.GROQ_API_KEY}",
                "Content-Type": "application/json",
                "User-Agent": "KANDA/1.0",
            },
        )
        try:
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning("[groq] failed: %s", e)
            return None

    def _call_grounded(self, prompt: str) -> Optional[str]:
        """Answer live/real-time queries using Groq (has recent training data)."""
        return self._call_groq(prompt, temperature=0.3, max_tokens=300)

    def _needs_grounding(self, text: str) -> bool:
        """Check if a query needs real-time web search."""
        lower = text.lower()
        return any(kw in lower for kw in _GROUNDED_KEYWORDS)

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

        raw = self._call_groq(prompt, temperature=0.1, max_tokens=200)

        if not raw:
            return {"intent": "UNKNOWN", "action": None, "speed": 120,
                    "goal": None, "reply": "Sorry, I didn't understand that."}

        return self._parse_json(raw, default={
            "intent": "UNKNOWN", "action": None, "speed": 120,
            "goal": None, "reply": "Sorry, I didn't understand."
        })

    def plan_and_execute(self, transcript: str) -> str:
        """
        Build body context, ask Groq for a plan, execute it.
        Used for TASK and QUESTION intents.
        Returns "done", "found", "cancelled", or "error".
        """
        # Grounded query shortcut — use Google Search for live information
        if self._needs_grounding(transcript):
            ctx_prompt = self._ctx.prompt_block(transcript)
            answer = self._call_grounded(
                ctx_prompt + "\n\nAnswer the user's question in 1-3 concise sentences as Kanda."
            )
            if answer:
                self._spk.speak_blocking(answer)
                self._ctx.add_turn("user", transcript)
                self._ctx.add_turn("kanda", answer)
                telegram_input.notify(f"🌐 Grounded answer: {answer}")
                return "done"
            # Fall through to standard planner if grounding failed

        # Update scene before planning
        b64 = self._cam.capture_base64()
        if b64 and len(b64) > (config.CAMERA_MIN_JPEG_BYTES * 4 // 3):
            scene = self._describe_scene(b64)
            if scene:
                self._ctx.update_scene(scene)

            # Vision question shortcut — if user asks about what's visible, answer directly
            vision_words = ("see", "look", "looking", "front", "view", "camera",
                           "describe", "watching", "visible", "scene", "around")
            if any(w in transcript.lower() for w in vision_words):
                # Ask VLM to answer the specific question about the image
                answer_prompt = (
                    f"You are Kanda, a friendly AI robot. The user asked: \"{transcript}\"\n"
                    f"Based on this camera image, answer their question in 2-3 natural sentences."
                )
                answer = self._vlm.describe_scene(b64, prompt=answer_prompt)
                if answer:
                    self._spk.speak_blocking(answer)
                    self._ctx.add_turn("user", transcript)
                    self._ctx.add_turn("kanda", answer)
                    telegram_input.notify(f"👁️ Vision: {answer}")
                    # Also send the image to Telegram
                    jpeg = self._cam.capture_jpeg()
                    if jpeg:
                        telegram_input.broadcast_photo(jpeg, caption=answer)
                    return "done"

        prompt = _PLANNER_PROMPT.format(
            body_context=self._ctx.prompt_block(transcript)
        )

        plan_raw = self._call_groq(prompt, temperature=0.3, max_tokens=800)

        if not plan_raw:
            self._spk.speak_blocking("I couldn't plan that right now. Please try again.")
            return "error"

        plan = self._parse_plan(plan_raw)
        if not plan:
            self._spk.speak_blocking("I couldn't understand my own plan. Please rephrase.")
            return "error"

        logger.info("[agent] executing plan with %d steps", len(plan))
        return self._executor.execute(plan)

    def run_search(self, goal: str) -> str:
        """
        ReAct search loop: move → check → remember → repeat.
        Uses semantic memory to avoid revisiting the same area.
        Sends every captured image + status to Telegram for live monitoring.

        Args:
            goal: natural language description of what to find

        Returns:
            "found", "not_found", or "cancelled"
        """
        self._search_memory.clear()
        self._search_step_count = 0
        consecutive_skips = 0
        similarity_threshold = config.SEARCH_SIMILARITY_THRESHOLD

        telegram_input.notify(f"🔍 Search started: \"{goal}\" (max {config.SEARCH_MAX_STEPS} steps)")

        for step in range(config.SEARCH_MAX_STEPS):
            if self._cancel.is_set():
                logger.info("[search] cancelled at step %d", step)
                telegram_input.notify(f"🛑 Search cancelled at step {step+1}")
                return "cancelled"

            # 1. STOP motors before capture to avoid blur
            self._send("stop", 0, "searching")
            time.sleep(0.3)

            # 2. Capture and describe scene (single capture, reuse bytes)
            jpeg = self._cam.capture_jpeg()
            if not jpeg or len(jpeg) < config.CAMERA_MIN_JPEG_BYTES:
                logger.warning("[search] corrupt frame at step %d, skipping", step)
                self._move_step(step)
                continue

            b64 = base64.b64encode(jpeg).decode("utf-8")

            # Send captured image to Telegram
            telegram_input.broadcast_photo(
                jpeg, caption=f"Step {step+1}/{config.SEARCH_MAX_STEPS} — looking for: {goal}"
            )

            # Rate-limit VLM calls to stay within free-tier limits
            if not self._vlm.should_update():
                time.sleep(max(0, config.VLM_INTERVAL_SEC - (time.time() - self._vlm._last_time)))

            scene = self._describe_scene(b64)
            if not scene:
                scene = "Unknown area"
            self._ctx.update_scene(scene)
            logger.info("[search] step %d scene: %s", step, scene)

            # 2b. Check if already visited this area
            if self._already_visited(scene, similarity_threshold):
                consecutive_skips += 1
                logger.info("[search] step %d: already visited, skip", step)
                telegram_input.notify(f"Step {step+1}: Already visited — moving on")
                if consecutive_skips >= 5:
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
                found_msg = f"I found it! {scene}"
                self._spk.speak_blocking(found_msg)
                logger.info("[search] FOUND at step %d", step)
                telegram_input.notify(f"✅ FOUND at step {step+1}! {scene}")
                telegram_input.broadcast_photo(jpeg, caption=f"✅ Target found: {goal}")
                return "found"

            # 4. Remember this location and notify
            action = self._choose_move()
            self._search_memory.append({
                "step":   step,
                "scene":  scene,
                "action": action,
            })
            telegram_input.notify(f"Step {step+1}: {scene}\n→ Not found, moving {action}")

            # 5. Move to next area
            self._execute_move(action)

            # 6. Check for stuck condition (all sensors blocked)
            if self._is_stuck():
                logger.info("[search] stuck at step %d — backing up", step)
                self._spk.speak("I'm stuck, backing up.")
                telegram_input.notify(f"⚠️ Step {step+1}: Stuck! Backing up...")
                self._execute_move("backward")
                time.sleep(0.5)

        # Search exhausted
        searched = [m["scene"][:30] for m in list(self._search_memory)[-3:]]
        areas = ", ".join(searched) if searched else "several areas"
        msg = f"I searched {config.SEARCH_MAX_STEPS} areas including {areas}, but couldn't find it. It may be behind something."
        self._send("stop", 0, "reporting_fail")
        self._spk.speak_blocking(msg)
        telegram_input.notify(f"❌ Search failed after {config.SEARCH_MAX_STEPS} steps.\nAreas: {areas}")
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

        result = self._call_groq(prompt, temperature=0.1, max_tokens=50)
        if not result or result.upper().startswith("CLEAR"):
            return None
        return result

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _describe_scene(self, image_b64: str) -> Optional[str]:
        """Get a 1-sentence scene description using the VLM module (NVIDIA NIM primary)."""
        return self._vlm.describe_scene(image_b64, prompt=_SCENE_DESCRIBE_PROMPT)

    def _check_for_goal(self, image_b64: str, goal: str) -> bool:
        """Ask VLM if the target is visible in the frame. Uses NVIDIA NIM."""
        prompt = _SEARCH_CHECK_PROMPT.format(goal_description=goal)
        answer = self._vlm.describe_scene(image_b64, prompt=prompt)
        if answer is None:
            logger.warning("[search] VLM check_for_goal returned None — treating as not found")
            return False
        logger.info("[search] goal check answer: '%s'", answer)
        first_word = answer.strip().split()[0].lower().rstrip(".,!") if answer.strip() else ""
        return first_word == "yes"

    def _already_visited(self, scene: str, threshold: float) -> bool:
        """Check if this scene is similar to a recently visited one."""
        for entry in self._search_memory:
            ratio = difflib.SequenceMatcher(
                None, scene.lower(), entry["scene"].lower()
            ).ratio()
            if ratio > threshold:
                return True
        return False

    _search_step_count = 0

    # Search pattern: scan then move, keeps exploring new ground
    _SEARCH_PATTERN = [
        "right", "right",    # 180° scan right
        "forward",           # advance
        "left", "left",      # 180° scan left
        "forward",           # advance
        "right",             # 90° scan
        "forward",           # advance
        "left",              # 90° scan
        "forward",           # advance
    ]

    def _choose_move(self) -> str:
        """
        Strategic search: scan 360° in place (4 turns), then move forward.
        Repeats. Always respects obstacles.
        """
        sensors = self._ctx.sensors
        f = sensors.get("front", -1)
        l = sensors.get("left",  -1)
        r = sensors.get("right", -1)

        pattern_idx = self._search_step_count % len(self._SEARCH_PATTERN)
        desired = self._SEARCH_PATTERN[pattern_idx]
        self._search_step_count += 1

        # If desired is forward but blocked, turn to clearest side
        if desired == "forward":
            if 0 < f < 25:
                return "left" if (l < 0 or l > r) else "right"
            return "forward"

        # Rotation — always safe (turning in place)
        return desired

    def _execute_move(self, action: str) -> None:
        """Execute a movement with obstacle safety check and keep-alive."""
        sensors = self._ctx.sensors
        f = sensors.get("front", -1)

        if action == "forward" and 0 < f < 18:
            logger.info("[search] blocked forward (%.0fcm), turning instead", f)
            action = self._ctx.best_turn_direction

        if action in ("left", "right"):
            duration_ms = config.TURN_90_MS
            speed = config.SPEED_TURN
        else:
            duration_ms = 2000
            speed = config.SPEED_NORMAL

        self._ctx.log_action(action, speed, duration_ms)
        elapsed = 0
        while elapsed < duration_ms:
            self._send(action, speed, "searching")
            time.sleep(0.5)
            elapsed += 500
        self._send("stop", 0, "searching")

    def _move_step(self, step: int) -> None:
        """Move forward to a new area when current position is already visited."""
        sensors = self._ctx.sensors
        f = sensors.get("front", -1)
        if f < 0 or f > 25:
            self._execute_move("forward")
        else:
            self._execute_move(self._ctx.best_turn_direction)

    def _is_stuck(self) -> bool:
        """True if multiple sensors show close obstacles."""
        s = self._ctx.sensors
        blocked = [v for v in s.values() if 0 < v < 25]
        return len(blocked) >= 2

    def _parse_json(self, text: str, default: dict) -> dict:
        """Extract and parse JSON from LLM response."""
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
        """Extract JSON array plan from LLM response."""
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
