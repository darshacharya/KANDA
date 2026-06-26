"""KANDA v2 — Entry point. Starts all subsystems on the asyncio event loop."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import settings, State
from event_bus import EventBus, Event, EventType, CommandEvent, ResponseEvent
from state_machine import StateMachine
from hal.serial_conn import SerialConnection
from hal.sensors import SensorFusion
from hal.motion import MotionController
from hal.speaker import Speaker
from hal.camera import Camera

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


class KandaApp:
    """Top-level application wiring all subsystems together."""

    def __init__(self) -> None:
        self.bus = EventBus()
        self.state_machine = StateMachine(self.bus)
        self.serial = SerialConnection()
        self.sensors = SensorFusion(self.bus)
        self.motion = MotionController(self.serial, self.sensors)
        self.speaker = Speaker()
        self.camera = Camera()
        self.user_speed: int = settings.speed_normal  # slider-controlled speed
        self._cancel = asyncio.Event()
        self._shutdown = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        logger.info("=" * 50)
        logger.info("KANDA v2 starting")
        logger.info("=" * 50)

        # Connect hardware
        await self.serial.connect()
        self.serial.on_line_received(self.sensors.handle_telemetry_line)

        await self.camera.start()

        # Subscribe bus events
        self.bus.subscribe(EventType.COMMAND, self._handle_command)
        self.bus.subscribe(EventType.OBSTACLE, self._handle_obstacle)
        self.bus.subscribe(EventType.CANCEL, self._handle_cancel)
        self.bus.subscribe(EventType.SHUTDOWN, self._handle_shutdown)

        # Start background tasks
        self._tasks = [
            asyncio.create_task(self.bus.run(), name="bus"),
            asyncio.create_task(self.speaker.run(), name="speaker"),
        ]

        if self.serial.connected:
            self._tasks.append(
                asyncio.create_task(self.serial.read_loop(), name="serial_read")
            )

        # Start input sources
        await self._start_inputs()

        logger.info("[main] all systems online — IDLE")
        await self.speaker.speak("Kanda online.")

        # Send Telegram startup notification
        if settings.telegram_enabled and settings.telegram_owner_id:
            await asyncio.sleep(3)  # Let telegram bot initialize
            try:
                from inputs.telegram import TelegramInput
                for task in self._tasks:
                    if task.get_name() == "telegram":
                        # Find the telegram input instance and send welcome
                        break
                import httpx
                async with httpx.AsyncClient() as client:
                    await client.post(
                        f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                        json={"chat_id": settings.telegram_owner_id, "text": "🤖 KANDA v2 online!\n\nSystems:\n✅ Camera\n✅ ESP32\n✅ Web UI (port 8080)\n✅ Wake Word\n✅ Telegram"},
                    )
            except Exception as e:
                logger.warning(f"[main] telegram welcome failed: {e}")

        # Wait for shutdown
        await self._shutdown.wait()
        await self._cleanup()

    async def _start_inputs(self) -> None:
        # Telegram
        if settings.telegram_enabled and settings.telegram_bot_token:
            from inputs.telegram import TelegramInput
            tg = TelegramInput(self.bus)
            self._tasks.append(
                asyncio.create_task(tg.run(), name="telegram")
            )

        # Web UI
        from inputs.web import WebInput
        web = WebInput(self.bus, self)
        self._web_ref = web
        self._tasks.append(
            asyncio.create_task(web.run(), name="web")
        )

        # Microphone
        if settings.wake_word_enabled:
            from inputs.microphone import MicrophoneInput
            mic = MicrophoneInput(self.bus, self.speaker, app=self)
            self._tasks.append(
                asyncio.create_task(mic.run(), name="microphone")
            )

    def _try_keyword_intent(self, text: str) -> dict | None:
        """Fast keyword matching — bypasses LLM for common commands."""
        t = text.lower().strip()

        # If compound command with "and"/"then", let LLM handle sequencing
        if " and " in t or " then " in t:
            return None

        # Direct movement commands
        move_map = {
            "forward": "forward", "go forward": "forward", "move forward": "forward",
            "backward": "backward", "go back": "backward", "move back": "backward",
            "left": "left", "turn left": "left", "go left": "left",
            "right": "right", "turn right": "right", "go right": "right",
            "stop": "CANCEL", "halt": "CANCEL",
        }
        for phrase, action in move_map.items():
            if t == phrase or t.startswith(phrase + " "):
                if action == "CANCEL":
                    return {"intent": "CANCEL"}
                return {"intent": "COMMAND", "action": action, "speed": self.user_speed, "duration": 2.0, "degrees": 0}
        # Rotate = full 360° turn
        if t in ("rotate", "spin", "turn around"):
            return {"intent": "COMMAND", "action": "right", "speed": self.user_speed, "duration": 2.0, "degrees": 360}
        # Dance / Sing
        if any(k in t for k in ("dance", "groove", "boogie")):
            return {"intent": "TASK", "goal": "dance"}
        # Fun responses
        if any(k in t for k in ("sing", "song")):
            return {"intent": "FUN", "type": "sing"}
        if any(k in t for k in ("joke", "funny", "make me laugh")):
            return {"intent": "FUN", "type": "joke"}
        if any(k in t for k in ("news", "headlines")):
            return {"intent": "FUN", "type": "news"}
        if any(k in t for k in ("what time", "time now", "current time")):
            return {"intent": "FUN", "type": "time"}
        if any(k in t for k in ("what date", "today's date", "what day")):
            return {"intent": "FUN", "type": "date"}
        # Memory - store
        if t.startswith("remember ") or t.startswith("my name is ") or t.startswith("i am "):
            return {"intent": "MEMORY", "action": "store", "text": t}
        # Memory - remove reminder
        if any(k in t for k in ("remove reminder", "delete reminder", "clear reminder", "remove that reminder", "done with reminder", "remove the reminder")):
            return {"intent": "MEMORY", "action": "remove_reminder", "text": t}
        if ("done" in t or "remove" in t or "delete" in t or "clear" in t) and "reminder" in t:
            return {"intent": "MEMORY", "action": "remove_reminder", "text": t}
        # Memory - recall (personal info only)
        if "my name" in t or "reminder" in t or "what do you know" in t or "who am i" in t or "what is kanda" in t or "about me" in t:
            return {"intent": "MEMORY", "action": "recall", "text": t}
        # "who is X" — check memory first, fall through to LLM if not found
        if "who is" in t:
            return {"intent": "MEMORY", "action": "who_is", "text": t}
        if t.startswith("remind me "):
            return {"intent": "MEMORY", "action": "remind", "text": t[10:]}
        # Find/search
        find_prefixes = ("find", "look for", "search for", "search", "seach", "serach", "locate", "where is")
        for prefix in find_prefixes:
            if t.startswith(prefix):
                return {"intent": "TASK", "goal": t}
        # Questions
        if any(t.startswith(q) for q in ("what", "who", "how", "why", "where", "when", "describe", "tell me")):
            return {"intent": "QUESTION"}
        return None

    async def _handle_command(self, event: Event) -> None:
        """Central command dispatch."""
        if not isinstance(event, CommandEvent):
            return
        self._cancel.clear()

        text = event.text.strip()
        if not text:
            return

        logger.info(f"[main] command from {event.source}: {text!r}")
        await self.speaker.interrupt()
        await self.state_machine.transition(State.THINKING)

        # Handle compound commands ("X and Y", "X then Y")
        t_lower = text.lower()
        if " and " in t_lower or " then " in t_lower:
            parts = t_lower.replace(" then ", " and ").split(" and ")
            parts = [p.strip() for p in parts if p.strip()]
            if len(parts) > 1:
                intents = []
                for part in parts:
                    sub_intent = self._try_keyword_intent(part)
                    if sub_intent and sub_intent["intent"] == "COMMAND":
                        intents.append(sub_intent)
                if intents:
                    ack_map = {"forward": "forward", "backward": "back", "left": "left", "right": "right", "stop": "stop"}
                    actions_str = " then ".join(ack_map.get(i["action"], i["action"]) for i in intents)
                    await self.speaker.speak_blocking(f"Okay, {actions_str}.")
                    for sub_intent in intents:
                        await self._execute_command_silent(sub_intent)
                        await asyncio.sleep(0.3)
                    await self.speaker.speak("Done.")
                    await self.bus.publish(ResponseEvent(text=f"Done: {actions_str}", source="command"))
                    await self.state_machine.transition(State.IDLE)
                    return

        # Try fast keyword match first — works even when LLM rate-limited
        intent = self._try_keyword_intent(text)
        if not intent:
            from brain.intent import classify_intent
            intent = await classify_intent(text, self.sensors, self.motion)

        if intent["intent"] == "CANCEL":
            await self.bus.publish(Event(type=EventType.CANCEL, source="user"))
            return
        elif intent["intent"] == "COMMAND":
            await self._execute_command(intent)
        elif intent["intent"] == "TASK":
            await self._execute_task(intent, text, event)
        elif intent["intent"] == "QUESTION":
            await self._execute_question(intent, text, event)
        elif intent["intent"] == "FUN":
            await self._execute_fun(intent)
        elif intent["intent"] == "MEMORY":
            await self._execute_memory(intent)
        else:
            reply = intent.get("reply", "I didn't understand that.")
            await self.speaker.speak(reply)
            await self.bus.publish(ResponseEvent(text=reply, source="brain"))
            await self.state_machine.transition(State.IDLE)

    async def _execute_fun(self, intent: dict) -> None:
        """Handle fun commands: sing, joke, news, time, date."""
        import random
        from datetime import datetime
        fun_type = intent.get("type", "joke")

        if fun_type == "sing":
            songs = [
                "La la la, I'm a robot, beep boop boop! I roll around and help you out, that's what I'm about!",
                "Twinkle twinkle little star, how I wonder what you are! Up above the world so high, like a camera in the sky!",
                "I'm a little robot, short and stout. Here is my camera, here is my wheel. When I get a command, hear me shout. I'll move around with so much zeal!",
            ]
            reply = random.choice(songs)
        elif fun_type == "joke":
            jokes = [
                "Why do robots never get scared? Because they have nerves of steel!",
                "What do you call a robot that takes the long way around? R2 Detour!",
                "Why was the robot so tired? Because it had a hard drive!",
                "I told my robot a joke about UDP. I'm not sure if it got it.",
                "What's a robot's favorite type of music? Heavy metal!",
            ]
            reply = random.choice(jokes)
        elif fun_type == "news":
            reply = "I don't have internet access for live news, but I can tell you that today is a great day to explore and learn new things!"
        elif fun_type == "time":
            now = datetime.now()
            reply = f"It's {now.strftime('%I:%M %p')}."
        elif fun_type == "date":
            now = datetime.now()
            reply = f"Today is {now.strftime('%A, %B %d, %Y')}."
        else:
            reply = "I'm not sure what you want me to do, but I'm here to help!"

        await self.speaker.speak(reply)
        await self.bus.publish(ResponseEvent(text=reply, source="fun"))
        await self.state_machine.transition(State.IDLE)

    async def _execute_memory(self, intent: dict) -> None:
        """Handle memory store/recall/remind."""
        from memory import get_user_name, set_user_info, store_fact, get_reminders, add_reminder, get_context_for_llm, remove_reminder

        action = intent.get("action", "recall")
        text = intent.get("text", "")

        if action == "store":
            if "my name is " in text:
                name = text.split("my name is ")[-1].strip().title()
                set_user_info("name", name)
                reply = f"Got it! I'll remember your name is {name}."
            elif "i am " in text:
                info = text.split("i am ")[-1].strip()
                parts = info.split(" ", 1)
                first_word = parts[0].title()
                if len(parts) > 1 and first_word[0].isupper():
                    set_user_info("name", first_word)
                    set_user_info("about", parts[1].strip())
                    reply = f"Got it! You're {first_word}, and {parts[1].strip()}."
                else:
                    set_user_info("about", info)
                    reply = f"Noted! You are {info}."
            elif text.startswith("remember "):
                fact = text[9:].strip()
                store_fact(fact, "true")
                reply = f"I'll remember that: {fact}."
            else:
                reply = "What would you like me to remember?"
        elif action == "remind":
            reminder_text = text.strip()
            if reminder_text:
                add_reminder(reminder_text)
                reply = f"Reminder set: {reminder_text}."
            else:
                reply = "What should I remind you about?"
        elif action == "remove_reminder":
            import re
            words = text.lower()
            # Try to extract a keyword from the text (strip common filler)
            for strip in ("remove", "delete", "clear", "done", "with", "that", "the", "reminder", "reminders", "can you", "please", "exam done", "you can"):
                words = words.replace(strip, "")
            keyword = words.strip()
            if not keyword:
                # If no keyword extracted, try to find domain words
                reminders = get_reminders()
                for r in reminders:
                    for w in r.lower().split():
                        if w in text.lower() and len(w) > 3:
                            keyword = w
                            break
                    if keyword:
                        break
            if keyword:
                removed = remove_reminder(keyword)
                if removed:
                    reply = f"Removed reminder: {removed}"
                else:
                    reply = f"No reminder found matching '{keyword}'."
            else:
                reminders = get_reminders()
                if reminders and len(reminders) == 1:
                    removed = remove_reminder(reminders[0])
                    reply = f"Removed reminder: {removed}"
                elif reminders:
                    reply = "Which reminder should I remove? " + ", ".join(f'"{r}"' for r in reminders)
                else:
                    reply = "You have no reminders to remove."
        elif action == "who_is":
            from memory import get_fact, _load
            query = text.split("who is")[-1].strip().lower()
            data = _load()
            user = data.get("user", {})
            found = False
            if query and query in (user.get("name", "").lower(), user.get("nickname", "").lower()):
                reply = f"{user.get('name', query)} is {user.get('about', 'someone I know')}."
                found = True
            elif query:
                fact = get_fact(query)
                if fact:
                    reply = fact
                    found = True
            if not found:
                await self._execute_question({"intent": "QUESTION"}, text)
                return
            await self.speaker.speak(reply)
            await self.bus.publish(ResponseEvent(text=reply, source="memory"))
            await self.state_machine.transition(State.IDLE)
            return
        elif action == "recall":
            if "my name" in text or "who am i" in text:
                name = get_user_name()
                reply = f"Your name is {name}."
            elif "reminder" in text:
                reminders = get_reminders()
                if reminders:
                    reply = "Your reminders:\n" + "\n".join(f"- {r}" for r in reminders)
                else:
                    reply = "You have no reminders."
            elif "who is" in text:
                from memory import get_fact, _load
                query = text.split("who is")[-1].strip().lower()
                data = _load()
                user = data.get("user", {})
                if query in (user.get("name", "").lower(), user.get("nickname", "").lower()):
                    reply = f"{user.get('name', query)} is {user.get('role', 'someone I know')}. {user.get('about', '')}"
                else:
                    fact = get_fact(query)
                    if fact:
                        reply = fact
                    else:
                        reply = f"I don't know who {query} is."
            elif "what is kanda" in text or "who are you" in text:
                from memory import get_fact
                reply = get_fact("kanda") or get_fact("who am i") or "I am KANDA, your AI robot assistant."
            else:
                context = get_context_for_llm()
                if context:
                    reply = f"Here's what I know: {context}"
                else:
                    reply = "I don't have any stored information yet. Tell me things to remember!"
        else:
            reply = "I'm not sure what to do with that."

        await self.speaker.speak(reply)
        await self.bus.publish(ResponseEvent(text=reply, source="memory"))
        await self.state_machine.transition(State.IDLE)

    async def _execute_command_silent(self, intent: dict) -> None:
        """Execute a movement command without speaking (used in compound commands)."""
        action = intent.get("action", "stop")
        speed = int(intent.get("speed", self.user_speed))
        duration = float(intent.get("duration", 2.0))
        degrees = float(intent.get("degrees", 0))

        await self.state_machine.transition(State.ACTING)

        if action in ("left", "right", "slight_left", "slight_right"):
            turn_deg = degrees if degrees else 90
            await self.motion.turn_degrees(action, turn_deg)
        else:
            await self.motion.move_timed(action, duration, speed)

        await self.state_machine.transition(State.IDLE)

    async def _execute_command(self, intent: dict) -> None:
        action = intent.get("action", "stop")
        speed = int(intent.get("speed", self.user_speed))
        duration = float(intent.get("duration", 2.0))
        degrees = float(intent.get("degrees", 0))

        valid_actions = ("forward", "backward", "left", "right", "slight_left", "slight_right", "stop")
        if action not in valid_actions:
            reply = f"Sorry, I can't do that. I can move forward, backward, turn left/right, dance, or search for things."
            await self.speaker.speak(reply)
            await self.bus.publish(ResponseEvent(text=reply, source="command"))
            await self.state_machine.transition(State.IDLE)
            return

        await self.state_machine.transition(State.ACTING)

        ack = {
            "forward": "Moving forward.",
            "backward": "Going back.",
            "left": "Turning left.",
            "right": "Turning right.",
            "stop": "Stopping.",
        }
        reply = ack.get(action, "Done.")
        await self.bus.publish(ResponseEvent(text=reply, source="command"))
        await self.speaker.speak(reply)

        if action in ("left", "right", "slight_left", "slight_right"):
            turn_deg = degrees if degrees else 90
            await self.motion.turn_degrees(action, turn_deg)
        else:
            await self.motion.move_timed(action, duration, speed)

        await self.state_machine.transition(State.IDLE)

    async def _execute_task(self, intent: dict, text: str, event: Event = None) -> None:
        goal = intent.get("goal", text)

        dance_keywords = ("dance", "groove", "moves", "boogie")
        if any(k in text.lower() for k in dance_keywords):
            await self.state_machine.transition(State.ACTING)
            reply = "Watch my moves!"
            await self.speaker.speak(reply)
            await self.bus.publish(ResponseEvent(text=reply, source="task"))
            await self.motion.dance(self._cancel)
            reply = "How was that?"
            await self.speaker.speak(reply)
            await self.bus.publish(ResponseEvent(text=reply, source="task"))
            await self.state_machine.transition(State.IDLE)
            return

        find_keywords = ("find", "look for", "search", "seach", "serach", "locate", "where is")
        if any(k in text.lower() for k in find_keywords):
            import re
            search_goal = goal
            for prefix in ("find", "look for", "search for", "search", "seach", "serach", "locate", "where is", "where's"):
                search_goal = re.sub(rf"^{prefix}\s+", "", search_goal, flags=re.IGNORECASE)
            search_goal = re.sub(r"^(a|an|the)\s+", "", search_goal, flags=re.IGNORECASE).strip()
            if not search_goal:
                search_goal = goal
            await self.state_machine.transition(State.SEARCHING)
            # Write directly to web history for immediate delivery
            search_msg = f"Searching for: {search_goal}"
            if hasattr(self, '_web_ref') and self._web_ref:
                import json as _json, time as _time
                self._web_ref._history.append({"type": "response", "text": search_msg, "source": "search", "timestamp": _time.time()})
            await self.bus.publish(ResponseEvent(text=search_msg, source="search"))

            import base64
            import json as json_mod

            # Frame callback — sends progress photos to both Telegram and Web
            telegram_chat_id = None
            if event and hasattr(event, "chat_id") and event.chat_id and event.source == "telegram":
                telegram_chat_id = event.chat_id

            async def on_frame(photo_bytes, caption):
                img_b64 = base64.b64encode(photo_bytes).decode()
                # Write directly to web history (bypass bus queue for real-time delivery)
                from inputs.web import WebInput
                for task in self._tasks:
                    if task.get_name() == "web":
                        break
                web_inst = getattr(self, '_web_ref', None)
                if web_inst:
                    record = {"type": "response", "text": caption, "source": "search", "image": img_b64, "timestamp": __import__('time').time()}
                    web_inst._history.append(record)
                    if len(web_inst._history) > 100:
                        web_inst._history = web_inst._history[-100:]
                    payload = json_mod.dumps({"type": "response", "data": {"text": caption, "source": "search", "image": img_b64}})
                    dead = []
                    for ws in web_inst._connections:
                        try:
                            await ws.send_text(payload)
                        except Exception:
                            dead.append(ws)
                    for ws in dead:
                        web_inst._connections.remove(ws)
                if telegram_chat_id:
                    await self._send_telegram_response(telegram_chat_id, caption, photo_bytes)

            from navigator.search import SearchNavigator
            nav = SearchNavigator(
                motion=self.motion,
                camera=self.camera,
                sensors=self.sensors,
                speaker=self.speaker,
                cancel=self._cancel,
                on_frame=on_frame,
            )
            result = await nav.search(search_goal)
            self._cancel.clear()
            if result == "cancelled":
                await self.motion.stop()
                reply = "Search stopped."
                await self.speaker.speak(reply)
                await self.bus.publish(ResponseEvent(text=reply, source="search"))
                await self.state_machine.transition(State.IDLE)
                return
            reply = "I found it!" if result == "found" else f"I couldn't find {search_goal}."
            photo_bytes = await self.camera.capture_jpeg()
            img_b64 = base64.b64encode(photo_bytes).decode() if photo_bytes else ""
            await self.bus.publish(ResponseEvent(text=reply, source="search", image_b64=img_b64))
            if telegram_chat_id:
                await self._send_telegram_response(telegram_chat_id, reply, photo_bytes)
            await self.speaker.speak(reply)
            await self.state_machine.transition(State.IDLE)
            return

        from brain.planner import plan_and_execute
        await self.state_machine.transition(State.ACTING)
        await self.bus.publish(ResponseEvent(text=f"Working on: {goal}", source="planner"))
        result = await plan_and_execute(
            goal, self.motion, self.camera, self.sensors, self.speaker, self._cancel
        )
        if result:
            await self.bus.publish(ResponseEvent(text=str(result), source="planner"))
        else:
            await self.bus.publish(ResponseEvent(text="Done.", source="planner"))
        await self.state_machine.transition(State.IDLE)

    async def _execute_question(self, intent: dict, text: str, event: Event = None) -> None:
        await self.state_machine.transition(State.THINKING)
        from brain.intent import answer_question
        import base64

        vision_keywords = ("see", "look", "show", "what is", "describe", "in front", "around")
        is_vision = any(k in text.lower() for k in vision_keywords)
        if is_vision:
            await self.speaker.speak("Let me take a look.")
        else:
            await self.speaker.speak("Let me think.")

        photo_bytes = None
        img_b64 = ""
        if is_vision:
            photo_bytes = await self.camera.capture_jpeg()
            if photo_bytes:
                img_b64 = base64.b64encode(photo_bytes).decode()
                await self.bus.publish(ResponseEvent(text="Captured. Analyzing...", source="camera", image_b64=img_b64))

        answer = await answer_question(text, self.camera, self.sensors)
        if not answer or answer == "Unable to describe the scene.":
            answer = "I captured the image but couldn't get a description right now."

        await self.state_machine.transition(State.SPEAKING)
        await self.bus.publish(ResponseEvent(text=answer, source="brain", image_b64=img_b64))

        if event and hasattr(event, "chat_id") and event.chat_id and event.source == "telegram":
            await self._send_telegram_response(event.chat_id, answer, photo_bytes)

        spoken = answer.strip().strip('"')
        sentences = spoken.split(". ")
        spoken = ". ".join(sentences[:2])
        if len(spoken) > 150:
            spoken = spoken[:147] + "..."
        await self.speaker.speak(spoken)
        await asyncio.sleep(0.5)
        await self.state_machine.transition(State.IDLE)

    async def _send_telegram_response(self, chat_id: int, text: str, photo_bytes: bytes | None = None) -> None:
        """Send response back to Telegram with optional photo."""
        for task in self._tasks:
            if task.get_name() == "telegram":
                break
        # Use direct API call since we need the bot instance
        try:
            import httpx
            if photo_bytes:
                async with httpx.AsyncClient(timeout=30) as client:
                    await client.post(
                        f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendPhoto",
                        data={"chat_id": chat_id, "caption": text[:1024]},
                        files={"photo": ("capture.jpg", photo_bytes, "image/jpeg")},
                    )
            else:
                async with httpx.AsyncClient(timeout=30) as client:
                    await client.post(
                        f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                        json={"chat_id": chat_id, "text": text},
                    )
        except Exception as e:
            logger.warning(f"[main] telegram response failed: {e}")

    async def _handle_obstacle(self, event: Event) -> None:
        logger.warning("[main] obstacle — emergency stop")
        await self.motion.stop()

    async def _handle_cancel(self, event: Event) -> None:
        logger.info("[main] STOP/CANCEL received — halting all")
        self._cancel.set()
        await self.motion.stop()
        await self.speaker.interrupt()
        try:
            await self.state_machine.transition(State.IDLE)
        except Exception:
            pass

    async def _handle_shutdown(self, event: Event) -> None:
        self._shutdown.set()

    async def _cleanup(self) -> None:
        logger.info("[main] shutting down...")
        try:
            async with asyncio.timeout(3):
                await self.motion.stop()
                await self.speaker.stop()
                await self.camera.stop()
                await self.serial.disconnect()
                await self.bus.stop()
        except (asyncio.TimeoutError, Exception):
            logger.warning("[main] cleanup timed out — forcing exit")

        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        logger.info("[main] shutdown complete")
        import os
        os._exit(0)
        logger.info("[main] goodbye")


def main() -> None:
    app = KandaApp()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: app._shutdown.set())

    try:
        loop.run_until_complete(app.start())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    main()
