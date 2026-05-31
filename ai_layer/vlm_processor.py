"""
KANDA AI Layer — Vision-Language Model Processor
Sends camera frames to Gemini's multimodal endpoint for scene understanding.

This module bridges the camera output to Gemini's vision capabilities,
generating natural language descriptions of what the robot sees. These
descriptions can be:
  1. Spoken aloud through the Bluetooth speaker
  2. Fed back into the navigation prompt for vision-informed movement

The VLM operates on a separate cadence from the navigation loop — scene
descriptions are generated every VLM_INTERVAL_SEC (default 5s) to avoid
excessive API calls while still providing situational awareness.

Usage:
    from vlm_processor import VLMProcessor
    vlm = VLMProcessor()
    description = vlm.describe_scene(image_b64)
    navigation_hint = vlm.get_navigation_context(image_b64, telemetry)
"""

import json
import logging
import re
import time
from typing import Optional

import google.generativeai as genai

import config

logger = logging.getLogger(__name__)

_SCENE_DESCRIPTION_PROMPT = """You are the vision system of KANDA, a small indoor companion robot.
Describe what you see in this image in 1-2 short sentences, suitable for speaking aloud.
Focus on: objects, people, obstacles, room layout, or anything notable.
Keep it natural and conversational, as if telling a friend what you see.
Do NOT mention image quality, camera angles, or technical details."""

_NAVIGATION_CONTEXT_PROMPT = """You are the vision system of KANDA, a two-wheeled indoor robot.
Analyze this image for navigation-relevant information.

Current ultrasonic sensor readings:
  front: {front:.1f} cm
  left:  {left:.1f} cm
  right: {right:.1f} cm

Based on what you SEE in the image (not just distance readings), provide a brief
navigation context. Mention:
- Obstacles the ultrasonic sensors might miss (glass, thin legs, overhangs)
- Floor hazards (stairs, edges, cables, wet surfaces)
- People or pets that may move into the path
- Open areas or doorways that are good to navigate toward

Respond in JSON format:
{{"context": "<1-2 sentence visual navigation context>", "hazard_level": "<none|low|medium|high>"}}"""


class VLMProcessor:
    """Processes camera frames through Gemini's multimodal vision endpoint."""

    def __init__(self):
        self._last_description: Optional[str] = None
        self._last_description_time: float = 0
        self._last_nav_context: Optional[dict] = None
        self._initialised = False

    def _ensure_init(self) -> None:
        if not self._initialised:
            if not config.GEMINI_API_KEY:
                raise ValueError("GEMINI_API_KEY not set")
            genai.configure(api_key=config.GEMINI_API_KEY)
            self._initialised = True

    def describe_scene(self, image_b64: str) -> Optional[str]:
        """
        Generate a spoken description of what the camera sees.

        Args:
            image_b64: Base64-encoded JPEG from the camera

        Returns:
            Natural language description suitable for TTS, or None on failure
        """
        self._ensure_init()

        try:
            model = genai.GenerativeModel(config.VLM_MODEL)

            response = model.generate_content(
                [
                    _SCENE_DESCRIPTION_PROMPT,
                    {"mime_type": "image/jpeg", "data": image_b64},
                ],
                generation_config=genai.GenerationConfig(
                    temperature=0.4,
                    max_output_tokens=100,
                ),
            )

            description = response.text.strip()
            self._last_description = description
            self._last_description_time = time.time()

            logger.info("Scene description: %s", description)
            return description

        except Exception as exc:
            logger.error("VLM scene description failed: %s", exc)
            return None

    def get_navigation_context(
        self, image_b64: str, telemetry: dict
    ) -> Optional[dict]:
        """
        Analyze the camera frame for navigation-relevant visual information.

        Args:
            image_b64: Base64-encoded JPEG from the camera
            telemetry: dict with front, left, right distance readings

        Returns:
            dict with 'context' (str) and 'hazard_level' (str), or None on failure
        """
        self._ensure_init()

        prompt = _NAVIGATION_CONTEXT_PROMPT.format(
            front=telemetry.get("front", -1),
            left=telemetry.get("left", -1),
            right=telemetry.get("right", -1),
        )

        try:
            model = genai.GenerativeModel(config.VLM_MODEL)

            response = model.generate_content(
                [
                    prompt,
                    {"mime_type": "image/jpeg", "data": image_b64},
                ],
                generation_config=genai.GenerationConfig(
                    temperature=0.2,
                    max_output_tokens=150,
                ),
            )

            raw = response.text.strip()
            nav_context = self._parse_nav_response(raw)
            self._last_nav_context = nav_context

            logger.info(
                "Navigation context: %s (hazard: %s)",
                nav_context.get("context", ""),
                nav_context.get("hazard_level", "unknown"),
            )
            return nav_context

        except Exception as exc:
            logger.error("VLM navigation context failed: %s", exc)
            return None

    def _parse_nav_response(self, text: str) -> dict:
        """Extract JSON from the navigation context response."""
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if fenced:
            text = fenced.group(1)

        match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        return {"context": text[:200], "hazard_level": "unknown"}

    def should_update(self) -> bool:
        """Check if enough time has passed for a new VLM query."""
        return (time.time() - self._last_description_time) >= config.VLM_INTERVAL_SEC

    @property
    def last_description(self) -> Optional[str]:
        return self._last_description

    @property
    def last_nav_context(self) -> Optional[dict]:
        return self._last_nav_context


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s  %(message)s")
    print("VLM Processor module loaded. Requires a camera frame to test.")
    print(f"  VLM Model: {config.VLM_MODEL}")
    print(f"  Interval: {config.VLM_INTERVAL_SEC}s")
