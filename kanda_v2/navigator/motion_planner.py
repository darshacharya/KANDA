"""Motion planning utilities for obstacle-aware navigation."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def plan_path_around_obstacle(sensors, target_direction: str) -> list[tuple[str, float]]:
    """Generate a sequence of moves to navigate around an obstacle.

    Returns list of (action, duration_seconds) tuples.
    """
    front = sensors.front
    left = sensors.left
    right = sensors.right

    if front < 0 or front > 25:
        # No obstacle ahead — go straight
        return [("forward", 0.8)]

    # Obstacle ahead — find the best way around
    if left > right and (left < 0 or left > 30):
        # More room on left
        return [
            ("left", 0.5),
            ("forward", 0.6),
            ("right", 0.3),
        ]
    elif right < 0 or right > 30:
        # More room on right
        return [
            ("right", 0.5),
            ("forward", 0.6),
            ("left", 0.3),
        ]
    else:
        # Tight space — back up and try different angle
        return [
            ("backward", 0.5),
            ("right", 0.8),
            ("forward", 0.6),
        ]


def should_backup(sensors) -> bool:
    """Check if robot is in a tight corner and should reverse."""
    threshold = 20.0
    blocked = 0
    if 0 < sensors.front < threshold:
        blocked += 1
    if 0 < sensors.left < threshold:
        blocked += 1
    if 0 < sensors.right < threshold:
        blocked += 1
    return blocked >= 2
