"""Scan-Move-Scan search with lawnmower coverage pattern."""

from __future__ import annotations

import asyncio
import logging
import random

from config import settings

logger = logging.getLogger(__name__)

MOVE_DURATION = 1.5  # seconds per forward move between scan positions


class SearchNavigator:
    """
    Search strategy (scan → move → scan with lawnmower coverage):
    1. At each position: rotate 360° in 4 steps (front/right/back/left), check VLM
    2. If found: announce and stop
    3. If not found: move to next position using lawnmower pattern
    4. Pattern: forward → forward → turn 90° → forward → turn 90° (opposite) → repeat
       This creates a zigzag/lawnmower coverage of the room.
    """

    def __init__(self, motion, camera, sensors, speaker, cancel: asyncio.Event, on_frame=None) -> None:
        self._motion = motion
        self._camera = camera
        self._sensors = sensors
        self._speaker = speaker
        self._cancel = cancel
        self._on_frame = on_frame  # async callback(photo_bytes, caption)

    async def search(self, goal: str) -> str:
        """Run search. Returns 'found', 'not_found', or 'cancelled'."""
        max_positions = 5
        moves_per_row = 2
        turn_direction = "right"

        logger.info(f"[search] starting search for: {goal}")
        await self._speaker.speak(f"Looking for {goal}. Scanning the area.")

        for pos in range(max_positions):
            if self._cancel.is_set():
                await self._motion.stop()
                return "cancelled"

            logger.info(f"[search] position {pos+1}/{max_positions}")

            # Scan 360° at current position
            found = await self._scan_360(goal)
            if found:
                return "found"

            if self._cancel.is_set():
                await self._motion.stop()
                return "cancelled"

            # Move to next position (lawnmower pattern)
            if pos < max_positions - 1:
                if pos > 0 and pos % moves_per_row == 0:
                    logger.info(f"[search] lawnmower U-turn ({turn_direction})")
                    await self._turn_safe(turn_direction, 90)
                    if self._cancel.is_set():
                        await self._motion.stop()
                        return "cancelled"
                    await self._move_forward()
                    if self._cancel.is_set():
                        await self._motion.stop()
                        return "cancelled"
                    await self._turn_safe(turn_direction, 90)
                    turn_direction = "left" if turn_direction == "right" else "right"
                else:
                    await self._move_forward()

        await self._motion.stop()
        logger.info(f"[search] exhausted {max_positions} positions without finding {goal}")
        return "not_found"

    async def _scan_360(self, goal: str) -> bool:
        """Rotate 360° in 4 steps, check VLM at each."""
        from brain.intent import describe_scene

        directions = ["front", "right", "back", "left"]

        for i, dir_name in enumerate(directions):
            if self._cancel.is_set():
                logger.info("[search] cancelled during scan")
                return False

            await self._motion.stop()
            await asyncio.sleep(0.3)
            if self._cancel.is_set():
                return False

            logger.info(f"[search] scanning {dir_name} ({i+1}/4)")

            photo_bytes = await self._camera.capture_jpeg()
            if self._cancel.is_set():
                return False
            if self._on_frame and photo_bytes:
                await self._on_frame(photo_bytes, f"[{dir_name}] scanning...")

            scene = await describe_scene(self._camera)
            if self._cancel.is_set():
                return False
            logger.info(f"[search] scene: {scene}")

            if self._on_frame and photo_bytes and scene != "Unable to describe the scene.":
                await self._on_frame(photo_bytes, f"[{dir_name}] {scene}")

            if scene and scene != "Unable to describe the scene.":
                scene_lower = scene.lower()
                goal_lower = goal.lower()
                goal_words = goal_lower.split()
                noun = goal_words[-1] if goal_words else goal_lower

                synonym_groups = [
                    ["person", "man", "woman", "someone", "people", "boy", "girl", "human", "legs", "sitting"],
                    ["bottle", "water bottle", "flask"],
                    ["cylinder", "gas cylinder", "tank"],
                    ["cup", "mug", "glass"],
                    ["phone", "mobile", "smartphone", "cellphone"],
                    ["chair", "seat", "stool"],
                    ["door", "doorway", "entrance"],
                    ["table", "desk"],
                ]
                check_words = [noun]
                for group in synonym_groups:
                    if noun in group:
                        check_words = group
                        break

                import re
                scene_words = set(re.findall(r'\b\w+\b', scene_lower))
                found_match = (goal_lower in scene_lower) or any(
                    w in scene_words for w in check_words if len(w) > 3
                )
                if found_match:
                    logger.info(f"[search] FOUND '{goal}' in scene description!")
                    await self._speaker.speak_blocking(f"I found {goal}! It's in front of me.")
                    return True

            if self._cancel.is_set():
                return False
            if i < 3:
                await self._motion.turn_degrees("right", 90)
                await asyncio.sleep(0.3)

        return False

    async def _move_forward(self) -> None:
        """Move forward one step, handling obstacles."""
        if self._cancel.is_set():
            return
        if self._sensors.front_clear:
            logger.info("[search] moving forward")
            await self._motion.move("forward", settings.speed_normal, "searching")
            await asyncio.sleep(MOVE_DURATION)
            await self._motion.stop()
        else:
            logger.info("[search] blocked — finding alternate path")
            directions = ["right", "left"]
            random.shuffle(directions)
            for turn_dir in directions:
                if self._cancel.is_set():
                    return
                await self._motion.turn_degrees(turn_dir, 90)
                await asyncio.sleep(0.3)
                if self._sensors.front_clear:
                    await self._motion.move("forward", settings.speed_normal, "searching")
                    await asyncio.sleep(MOVE_DURATION)
                    await self._motion.stop()
                    return
            logger.info("[search] stuck — skipping move")

    async def _turn_safe(self, direction: str, degrees: float) -> None:
        """Turn with a brief pause after."""
        await self._motion.turn_degrees(direction, degrees)
        await asyncio.sleep(0.3)
