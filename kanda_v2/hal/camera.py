"""Camera abstraction — async capture via picamera2."""

from __future__ import annotations

import asyncio
import base64
import io
import logging

from config import settings

logger = logging.getLogger(__name__)


class Camera:
    """Pi Camera v2 wrapper with async capture."""

    def __init__(self) -> None:
        self._picam = None
        self._ready = False

    async def start(self) -> bool:
        def _init():
            try:
                from picamera2 import Picamera2
                cam = Picamera2()
                w, h = settings.camera_resolution
                config = cam.create_still_configuration(
                    main={"size": (w, h), "format": "RGB888"}
                )
                cam.configure(config)
                cam.start()
                import time
                time.sleep(settings.camera_warmup_sec)
                return cam
            except Exception as e:
                logger.error(f"[camera] init failed: {e}")
                return None

        self._picam = await asyncio.to_thread(_init)
        self._ready = self._picam is not None
        if self._ready:
            logger.info("[camera] ready")
        return self._ready

    async def capture_jpeg(self) -> bytes | None:
        if not self._ready:
            return None

        def _capture():
            try:
                from PIL import Image
                arr = self._picam.capture_array()
                # PiCamera2 RGB888 can sometimes be BGR — swap R and B channels
                arr = arr[:, :, ::-1]
                img = Image.fromarray(arr)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=settings.camera_jpeg_quality)
                data = buf.getvalue()
                if len(data) < 5000:
                    logger.warning("[camera] frame too small, likely corrupt")
                    return None
                return data
            except Exception as e:
                logger.error(f"[camera] capture error: {e}")
                return None

        return await asyncio.to_thread(_capture)

    async def capture_base64(self) -> str | None:
        jpeg = await self.capture_jpeg()
        if jpeg:
            return base64.b64encode(jpeg).decode()
        return None

    async def stop(self) -> None:
        if self._picam:
            try:
                await asyncio.to_thread(self._picam.stop)
            except Exception:
                pass
            self._ready = False
            logger.info("[camera] stopped")
