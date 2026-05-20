"""
KANDA Vision Module — Camera Capture
Captures JPEG frames from Raspberry Pi Camera Module v2.1 via picamera2.

Test standalone:
    python3 camera.py
"""

import base64
import io
import logging
import time
from typing import Optional

from picamera2 import Picamera2
from PIL import Image

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


class Camera:
    def __init__(self):
        self._picam2: Optional[Picamera2] = None
        self._running = False

    def start(self) -> None:
        self._picam2 = Picamera2()
        cam_config = self._picam2.create_still_configuration(
            main={"size": config.CAMERA_RESOLUTION, "format": "RGB888"},
        )
        self._picam2.configure(cam_config)
        self._picam2.start()
        time.sleep(config.CAMERA_WARMUP_SEC)
        self._running = True
        logger.info("Camera started: %dx%d", *config.CAMERA_RESOLUTION)

    def capture_jpeg(self) -> Optional[bytes]:
        """Capture a single frame, return as JPEG bytes."""
        if not self._running:
            return None
        array = self._picam2.capture_array()
        img = Image.fromarray(array)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=config.CAMERA_JPEG_QUALITY)
        return buf.getvalue()

    def capture_base64(self) -> Optional[str]:
        """Capture a frame, return as base64 string for Gemini API."""
        jpeg = self.capture_jpeg()
        if jpeg is None:
            return None
        return base64.b64encode(jpeg).decode("utf-8")

    def stop(self) -> None:
        if self._picam2:
            self._picam2.stop()
            self._picam2.close()
            self._picam2 = None
            self._running = False
            logger.info("Camera stopped")

    @property
    def is_running(self) -> bool:
        return self._running


if __name__ == "__main__":
    print("=== Pi Camera v2.1 Test ===")
    cam = Camera()
    cam.start()

    jpeg = cam.capture_jpeg()
    if jpeg:
        print(f"Captured JPEG: {len(jpeg)} bytes")
        with open("test_capture.jpg", "wb") as f:
            f.write(jpeg)
        print("Saved: test_capture.jpg")

        b64 = base64.b64encode(jpeg).decode()
        print(f"Base64 length: {len(b64)} chars (ready for Gemini)")
    else:
        print("ERROR: capture failed")

    cam.stop()
    print("Done.")
