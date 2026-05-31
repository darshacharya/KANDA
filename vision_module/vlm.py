"""
KANDA Vision Module — Vision-Language Model
Sends camera frames to NVIDIA NIM for scene understanding.

Provider: NVIDIA NIM (40 req/min free, LLaMA 3.2 Vision)

Test standalone:
    python3 vlm.py
"""

import json
import logging
import time
import urllib.request
from typing import Optional

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_SCENE_PROMPT = """Describe this image in 1 short sentence (under 15 words). Just name the main thing you see. Example: "A person sitting at a desk with a laptop." Now describe:"""


class VLM:
    def __init__(self):
        if not config.NVIDIA_API_KEY:
            raise ValueError("NVIDIA_API_KEY not set — required for VLM")
        self._last_description: Optional[str] = None
        self._last_time: float = 0
        logger.info("VLM initialized: NVIDIA NIM (%s)", config.NVIDIA_VLM_MODEL)

    def describe_scene(self, image_b64: str, prompt: str = None) -> Optional[str]:
        """
        Send image for description via NVIDIA NIM.

        Args:
            image_b64: base64-encoded JPEG from camera
            prompt: optional custom prompt (defaults to scene description)

        Returns:
            Natural language description string, or None on failure
        """
        text_prompt = prompt or _SCENE_PROMPT
        description = self._call_nvidia(image_b64, text_prompt)

        if description:
            self._last_description = description
            self._last_time = time.time()
            logger.info("Scene: %s", description)

        return description

    def _call_nvidia(self, image_b64: str, prompt: str) -> Optional[str]:
        """Call NVIDIA NIM Qwen VLM with base64 image."""
        try:
            payload = json.dumps({
                "model": config.NVIDIA_VLM_MODEL,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/jpeg;base64,{image_b64}"
                        }},
                    ],
                }],
                "max_tokens": 300,
                "temperature": 0.7,
            }).encode()

            req = urllib.request.Request(
                config.NVIDIA_ENDPOINT,
                data=payload,
                headers={
                    "Authorization": f"Bearer {config.NVIDIA_API_KEY}",
                    "Content-Type": "application/json",
                    "User-Agent": "KANDA/1.0",
                },
            )
            resp = urllib.request.urlopen(req, timeout=30)
            data = json.loads(resp.read())
            text = data["choices"][0]["message"]["content"].strip()
            logger.info("[nvidia-vlm] OK")
            return text
        except Exception as e:
            logger.error("[nvidia-vlm] failed: %s", e)
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
    print("Sending to NVIDIA NIM...")
    print()

    vlm = VLM()
    description = vlm.describe_scene(b64)

    if description:
        print(f"Robot sees: {description}")
    else:
        print("ERROR: VLM returned nothing")
