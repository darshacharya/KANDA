"""
KANDA AI Layer — Vision Module
Captures frames from Raspberry Pi Camera Module v2.1 via picamera2.

The Pi Camera v2.1 uses a Sony IMX219 8MP sensor connected via the CSI ribbon.
This module provides frame capture in JPEG format suitable for sending to
Gemini's multimodal endpoint.

Usage:
    from vision import Camera
    cam = Camera()
    cam.start()
    frame_b64 = cam.capture_base64()  # returns base64-encoded JPEG
    cam.stop()
"""

import base64
import io
import logging
import time
from typing import Optional

import config

logger = logging.getLogger(__name__)


class Camera:
    """Interface to the Raspberry Pi Camera Module v2.1 via picamera2."""

    def __init__(
        self,
        resolution: tuple = None,
        framerate: int = None,
        jpeg_quality: int = None,
    ):
        self._resolution = resolution or config.CAMERA_RESOLUTION
        self._framerate = framerate or config.CAMERA_FRAMERATE
        self._jpeg_quality = jpeg_quality or config.CAMERA_JPEG_QUALITY
        self._picam2 = None
        self._running = False

    def start(self) -> None:
        """Initialize and start the camera preview (no display, capture-only)."""
        try:
            from picamera2 import Picamera2

            self._picam2 = Picamera2()

            cam_config = self._picam2.create_still_configuration(
                main={"size": self._resolution, "format": "RGB888"},
            )
            self._picam2.configure(cam_config)
            self._picam2.start()
            time.sleep(config.CAMERA_WARMUP_SEC)
            self._running = True
            logger.info(
                "Camera started: %dx%d @ %dfps, JPEG quality=%d",
                self._resolution[0],
                self._resolution[1],
                self._framerate,
                self._jpeg_quality,
            )
        except ImportError:
            logger.error(
                "picamera2 not installed. Install with: sudo apt install python3-picamera2"
            )
            raise
        except Exception as exc:
            logger.error("Failed to initialize camera: %s", exc)
            raise

    def capture_frame(self) -> Optional[bytes]:
        """Capture a single frame and return as JPEG bytes."""
        if not self._running or self._picam2 is None:
            logger.warning("Camera not running — cannot capture")
            return None

        try:
            from PIL import Image

            array = self._picam2.capture_array()
            img = Image.fromarray(array)

            if img.size != self._resolution:
                img = img.resize(self._resolution, Image.LANCZOS)

            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=self._jpeg_quality)
            jpeg_bytes = buffer.getvalue()

            logger.debug("Captured frame: %d bytes JPEG", len(jpeg_bytes))
            return jpeg_bytes

        except Exception as exc:
            logger.error("Frame capture failed: %s", exc)
            return None

    def capture_base64(self) -> Optional[str]:
        """Capture a frame and return as base64-encoded string for LLM API."""
        jpeg_bytes = self.capture_frame()
        if jpeg_bytes is None:
            return None
        return base64.b64encode(jpeg_bytes).decode("utf-8")

    def stop(self) -> None:
        """Stop the camera and release resources."""
        if self._picam2 is not None:
            try:
                self._picam2.stop()
                self._picam2.close()
                logger.info("Camera stopped and released")
            except Exception as exc:
                logger.warning("Error stopping camera: %s", exc)
            finally:
                self._picam2 = None
                self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s  %(message)s")

    print("Testing Pi Camera v2.1 capture...")
    cam = Camera()
    try:
        cam.start()
        for i in range(3):
            b64 = cam.capture_base64()
            if b64:
                print(f"  Frame {i+1}: {len(b64)} chars base64 ({len(b64)*3//4} bytes JPEG)")
            else:
                print(f"  Frame {i+1}: capture failed")
            time.sleep(1)
    finally:
        cam.stop()
    print("Done.")
