"""FastAPI + WebSocket web interface for KANDA."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from config import settings
from event_bus import EventBus, Event, EventType, CommandEvent, ResponseEvent
from state_machine import State

if TYPE_CHECKING:
    from main import KandaApp

logger = logging.getLogger(__name__)

WEB_UI_DIR = Path(__file__).parent.parent / "web_ui"


class WebInput:
    """Web server providing UI and WebSocket for real-time control."""

    def __init__(self, bus: EventBus, app: "KandaApp") -> None:
        self._bus = bus
        self._app = app
        self._connections: list = []
        self._history: list[dict] = []

    async def run(self) -> None:
        try:
            from fastapi import FastAPI, WebSocket, WebSocketDisconnect
            from fastapi.responses import HTMLResponse, JSONResponse
            import uvicorn
        except ImportError:
            logger.warning("[web] fastapi/uvicorn not installed — Web UI disabled")
            return

        api = FastAPI(title="KANDA v2")

        @api.get("/")
        async def index():
            html_path = WEB_UI_DIR / "index.html"
            if html_path.exists():
                return HTMLResponse(
                    html_path.read_text(),
                    headers={"Cache-Control": "no-store"},
                )
            return HTMLResponse("<h1>KANDA v2</h1><p>Web UI not found</p>")

        @api.get("/api/status")
        async def status():
            return {
                "state": self._app.state_machine.state.value,
                "sensors": self._app.sensors.as_dict(),
                "connected": self._app.serial.connected,
            }

        @api.post("/api/command")
        async def post_command(body: dict):
            text = body.get("text", "")
            if text:
                await self._bus.publish(CommandEvent(text=text, source="web"))
            return {"ok": True}

        @api.post("/api/stop")
        async def post_stop():
            logger.info("[web] STOP received — direct cancel")
            self._app._cancel.set()
            await self._app.motion.stop()
            await self._app.speaker.interrupt()
            try:
                await self._app.state_machine.transition(State.IDLE)
            except Exception:
                pass
            return {"ok": True}

        @api.post("/api/speed")
        async def post_speed(body: dict):
            new_speed = min(int(body.get("speed", 100)), 255)
            self._app.user_speed = new_speed
            logger.info(f"[web] user_speed set to {new_speed}")
            return {"ok": True, "speed": new_speed}

        @api.get("/api/speed")
        async def get_speed():
            return {"speed": self._app.user_speed}

        @api.post("/api/move")
        async def post_move(body: dict):
            action = body.get("action", "stop")
            duration_ms = int(body.get("duration_ms", 400))
            speed = self._app.user_speed
            logger.info(f"[web] POST /api/move action={action} speed={speed} dur={duration_ms}")
            asyncio.create_task(self._timed_move(action, speed, duration_ms))
            return {"ok": True}

        @api.get("/api/capture")
        async def capture_photo():
            """Take a photo and return base64 JPEG."""
            b64 = await self._app.camera.capture_base64()
            if b64:
                record = {
                    "type": "image",
                    "image": b64,
                    "source": "camera",
                    "timestamp": time.time(),
                }
                self._history.append(record)
                return {"ok": True, "image": b64}
            return JSONResponse({"ok": False, "error": "Camera unavailable"}, status_code=503)

        @api.post("/api/capture_and_ask")
        async def capture_and_ask(body: dict):
            """Take a photo, ask VLM, and return both image + answer."""
            question = body.get("question", "What do you see?")
            b64 = await self._app.camera.capture_base64()
            if not b64:
                return JSONResponse({"ok": False, "error": "Camera unavailable"}, status_code=503)

            # Broadcast the image immediately
            await self._broadcast_image(b64, "camera")

            # Ask VLM
            await self._bus.publish(CommandEvent(text=question, source="web"))
            return {"ok": True, "image": b64}

        @api.websocket("/ws")
        async def websocket_endpoint(ws: WebSocket):
            await ws.accept()
            self._connections.append(ws)
            logger.info(f"[web] client connected ({len(self._connections)} total)")

            try:
                while True:
                    data = await ws.receive_text()
                    msg = json.loads(data)

                    if msg.get("type") == "command":
                        await self._bus.publish(CommandEvent(
                            text=msg.get("text", ""),
                            source="web",
                        ))
                    elif msg.get("type") == "move":
                        action = msg.get("action", "stop")
                        speed = int(msg.get("speed", 200))
                        duration_ms = int(msg.get("duration_ms", 400))
                        asyncio.create_task(self._timed_move(action, speed, duration_ms))
                    elif msg.get("type") == "stop":
                        await self._bus.publish(Event(
                            type=EventType.CANCEL, source="web"
                        ))
                        await self._app.motion.stop()
                    elif msg.get("type") == "capture":
                        asyncio.create_task(self._handle_capture(msg))
            except WebSocketDisconnect:
                pass
            except Exception:
                logger.exception("[web] websocket error")
            finally:
                self._connections.remove(ws)
                logger.info(f"[web] client disconnected ({len(self._connections)} total)")

        @api.get("/api/history")
        async def get_history():
            logger.debug(f"[web] /api/history polled, {len(self._history)} msgs")
            return {"messages": self._history[-50:]}

        @api.post("/api/clear")
        async def clear_history():
            self._history.clear()
            return {"ok": True}

        self._bus.subscribe(EventType.STATE_CHANGE, self._broadcast_event)
        self._bus.subscribe(EventType.SENSOR_UPDATE, self._broadcast_event)
        self._bus.subscribe(EventType.RESPONSE, self._broadcast_response)

        config = uvicorn.Config(
            api,
            host=settings.web_host,
            port=settings.web_port,
            log_level="info",
        )
        server = uvicorn.Server(config)
        logger.info(f"[web] serving on http://{settings.web_host}:{settings.web_port}")
        await server.serve()

    async def _handle_capture(self, msg: dict) -> None:
        """Handle a WebSocket capture request."""
        b64 = await self._app.camera.capture_base64()
        if b64:
            await self._broadcast_image(b64, "camera")
            question = msg.get("question")
            if question:
                await self._bus.publish(CommandEvent(text=question, source="web"))

    async def _broadcast_image(self, image_b64: str, source: str = "camera") -> None:
        """Send an image message to all connected clients."""
        record = {
            "type": "image",
            "image": image_b64[:100] + "...",
            "source": source,
            "timestamp": time.time(),
        }
        self._history.append(record)

        payload = json.dumps({
            "type": "image",
            "data": {"image": image_b64, "source": source},
        })

        dead = []
        for ws in self._connections:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._connections.remove(ws)

    async def _timed_move(self, action: str, speed: int, duration_ms: int) -> None:
        """Execute a short timed movement pulse using user_speed for all."""
        speed = self._app.user_speed
        is_turn = action in ("left", "right", "slight_left", "slight_right")
        if is_turn:
            logger.info(f"[web] _timed_move: {action} speed={speed} → turn_degrees(90)")
            await self._app.motion.turn_degrees(action, 90, speed)
        else:
            duration_ms = min(duration_ms, 2000)
            logger.info(f"[web] _timed_move: {action} speed={speed} dur={duration_ms}ms")
            await self._app.motion.move(action, speed, "acting")
            await asyncio.sleep(duration_ms / 1000.0)
            await self._app.motion.stop()

    async def _broadcast_response(self, event: Event) -> None:
        if not isinstance(event, ResponseEvent):
            return

        logger.info(f"[web] _broadcast_response: text={event.text[:60]!r} image={'yes' if event.image_b64 else 'no'} clients={len(self._connections)}")

        msg_record = {
            "type": "response",
            "text": event.text,
            "source": event.source,
            "timestamp": event.timestamp,
        }
        if event.image_b64:
            msg_record["image"] = event.image_b64
        self._history.append(msg_record)
        if len(self._history) > 100:
            self._history = self._history[-100:]

        data = {"text": event.text, "source": event.source}
        if event.image_b64:
            data["image"] = event.image_b64

        payload = json.dumps({"type": "response", "data": data})

        dead = []
        for ws in self._connections:
            try:
                await ws.send_text(payload)
            except Exception as e:
                logger.warning(f"[web] ws send failed: {e}")
                dead.append(ws)
        for ws in dead:
            self._connections.remove(ws)

    async def _broadcast_event(self, event: Event) -> None:
        if not self._connections:
            return

        payload = json.dumps({
            "type": event.type.value,
            "data": event.data,
        })

        dead = []
        for ws in self._connections:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)

        for ws in dead:
            self._connections.remove(ws)
