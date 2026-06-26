"""State machine with explicit transition table."""

from __future__ import annotations

import logging
from typing import Callable, Coroutine, Any

from config import State
from event_bus import EventBus, Event, EventType

logger = logging.getLogger(__name__)

TRANSITIONS: dict[State, set[State]] = {
    State.IDLE: {State.LISTENING, State.THINKING, State.ACTING, State.SEARCHING, State.SPEAKING},
    State.LISTENING: {State.THINKING, State.IDLE},
    State.THINKING: {State.ACTING, State.SEARCHING, State.SPEAKING, State.IDLE},
    State.ACTING: {State.IDLE, State.SPEAKING, State.REPORTING},
    State.SEARCHING: {State.IDLE, State.REPORTING, State.SPEAKING},
    State.SPEAKING: {State.IDLE, State.ACTING, State.LISTENING},
    State.REPORTING: {State.IDLE, State.SPEAKING},
}

StateCallback = Callable[[State, State], Coroutine[Any, Any, None]]


class StateMachine:
    """Manages robot state transitions and broadcasts changes."""

    def __init__(self, bus: EventBus) -> None:
        self._state = State.IDLE
        self._bus = bus
        self._on_enter: dict[State, list[StateCallback]] = {}

    @property
    def state(self) -> State:
        return self._state

    def on_enter(self, state: State, callback: StateCallback) -> None:
        self._on_enter.setdefault(state, []).append(callback)

    async def transition(self, new_state: State) -> bool:
        if new_state == self._state:
            return True

        allowed = TRANSITIONS.get(self._state, set())
        if new_state not in allowed:
            logger.warning(
                f"[state] invalid transition {self._state.value} → {new_state.value}"
            )
            return False

        old = self._state
        self._state = new_state
        logger.info(f"[state] {old.value} → {new_state.value}")

        await self._bus.publish(Event(
            type=EventType.STATE_CHANGE,
            data={"old": old.value, "new": new_state.value},
        ))

        for cb in self._on_enter.get(new_state, []):
            try:
                await cb(old, new_state)
            except Exception:
                logger.exception(f"[state] on_enter callback error for {new_state}")

        return True
