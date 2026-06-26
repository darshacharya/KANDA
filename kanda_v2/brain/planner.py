"""Multi-step task planner and executor."""

from __future__ import annotations

import asyncio
import json
import logging

import httpx

from config import settings
from brain.prompts import SYSTEM_IDENTITY, PLANNER_PROMPT
from brain.intent import _call_groq

logger = logging.getLogger(__name__)


async def plan_and_execute(
    goal: str,
    motion,
    camera,
    sensors,
    speaker,
    cancel: asyncio.Event,
) -> str:
    """Generate a plan from LLM and execute it step by step."""
    prompt = PLANNER_PROMPT.format(
        identity=SYSTEM_IDENTITY,
        front=sensors.front,
        left=sensors.left,
        right=sensors.right,
        goal=goal,
    )

    result = await _call_groq([{"role": "user", "content": prompt}])
    if not result:
        await speaker.speak("I couldn't come up with a plan.")
        return "error"

    try:
        cleaned = result.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
        plan = json.loads(cleaned)
        if not isinstance(plan, list):
            raise ValueError("Plan is not a list")
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"[planner] invalid plan JSON: {e}")
        await speaker.speak("I couldn't form a valid plan.")
        return "error"

    logger.info(f"[planner] executing {len(plan)} steps for: {goal}")

    for i, step in enumerate(plan):
        if cancel.is_set():
            await motion.stop()
            return "cancelled"

        step_type = step.get("type", "")
        logger.info(f"[planner] step {i+1}/{len(plan)}: {step_type}")

        try:
            if step_type == "move":
                action = step.get("action", "forward")
                duration_ms = min(step.get("duration_ms", 1000), 5000)
                if action == "forward" and not sensors.front_clear:
                    logger.warning("[planner] skipping forward — obstacle")
                    continue
                await motion.move(action, settings.speed_normal, "acting")
                await asyncio.sleep(duration_ms / 1000)
                await motion.stop()

            elif step_type == "turn":
                direction = step.get("direction", "right")
                degrees = min(step.get("degrees", 90), 360)
                await motion.turn_degrees(direction, degrees)

            elif step_type == "speak":
                text = step.get("text", "")
                if text:
                    await speaker.speak(text)

            elif step_type == "wait":
                duration_ms = min(step.get("duration_ms", 1000), 10000)
                await asyncio.sleep(duration_ms / 1000)

            elif step_type == "capture_check":
                from brain.intent import check_for_goal
                question = step.get("question", "")
                result = await check_for_goal(camera, question)
                if result is True:
                    await speaker.speak("Yes, I can confirm that.")
                elif result is False:
                    await speaker.speak("No, I don't see that.")

        except Exception:
            logger.exception(f"[planner] step {i+1} failed")

    return "done"
