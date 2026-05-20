"""
KANDA Vision Module — Vision-Language Model
Sends camera frames to Gemini for scene understanding and speaks the result.

Uses the new google-genai SDK (replaces deprecated google.generativeai).

Test standalone:
    export GEMINI_API_KEY=your_key
    python3 vlm.py
"""

import base64
import logging
import time
from typing import Optional

from google import genai
from google.genai import types

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_SCENE_PROMPT = """Describe this image in 1 short sentence (under 15 words). Just name the main thing you see. Example: "A person sitting at a desk with a laptop." Now describe:"""


class VLM:
    def __init__(self):
        if not config.GEMINI_API_KEY:
            raise ValueError(
                "GEMINI_API_KEY not set. Run: export GEMINI_API_KEY=your_key"
            )
        self._client = genai.Client(api_key=config.GEMINI_API_KEY)
        self._last_description: Optional[str] = None
        self._last_time: float = 0
        logger.info("VLM initialized with model: %s", config.GEMINI_MODEL)

    def describe_scene(self, image_b64: str) -> Optional[str]:
        """
        Send image to Gemini, get a spoken description of the scene.

        Args:
            image_b64: base64-encoded JPEG from camera

        Returns:
            Natural language description string, or None on failure
        """
        try:
            image_bytes = base64.b64decode(image_b64)

            response = self._client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=[
                    _SCENE_PROMPT,
                    types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                ],
                config=types.GenerateContentConfig(
                    temperature=0.7,
                    max_output_tokens=300,
                ),
            )

            description = response.text.strip()
            self._last_description = description
            self._last_time = time.time()
            logger.info("Scene: %s", description)
            return description

        except Exception as exc:
            logger.error("VLM failed: %s", exc)
            return None

    def should_update(self) -> bool:
        """Check if enough time has passed since last VLM call."""
        return (time.time() - self._last_time) >= config.VLM_INTERVAL_SEC

    @property
    def last_description(self) -> Optional[str]:
        return self._last_description


if __name__ == "__main__":
    from camera import Camera

    print("=== VLM Test: Capture + Describe ===")
    print()

    cam = Camera()
    cam.start()

    b64 = cam.capture_base64()
    cam.stop()

    if not b64:
        print("ERROR: No frame captured")
        exit(1)

    print(f"Frame captured ({len(b64)} chars base64)")
    print("Sending to Gemini...")
    print()

    vlm = VLM()
    description = vlm.describe_scene(b64)

    if description:
        print(f"Robot sees: {description}")
    else:
        print("ERROR: VLM returned nothing")
