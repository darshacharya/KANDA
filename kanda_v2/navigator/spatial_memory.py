"""Grid-based spatial memory for tracking visited locations."""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class Cell:
    x: int
    y: int
    visited: bool = False
    scene: str = ""
    checked: bool = False  # VLM checked for target


@dataclass
class RobotPose:
    """Dead-reckoned position and heading."""
    x: float = 0.0  # cm
    y: float = 0.0  # cm
    heading: float = 0.0  # degrees, 0 = forward/north

    def advance(self, distance_cm: float) -> None:
        rad = math.radians(self.heading)
        self.x += distance_cm * math.sin(rad)
        self.y += distance_cm * math.cos(rad)

    def rotate(self, degrees: float) -> None:
        self.heading = (self.heading + degrees) % 360


class SpatialMemory:
    """Grid-based map for frontier exploration."""

    def __init__(self, cell_size_cm: float = 30.0) -> None:
        self._cell_size = cell_size_cm
        self._grid: dict[tuple[int, int], Cell] = {}
        self.pose = RobotPose()

    @property
    def current_cell_key(self) -> tuple[int, int]:
        cx = int(round(self.pose.x / self._cell_size))
        cy = int(round(self.pose.y / self._cell_size))
        return (cx, cy)

    def mark_visited(self, scene: str = "") -> None:
        key = self.current_cell_key
        if key not in self._grid:
            self._grid[key] = Cell(x=key[0], y=key[1])
        self._grid[key].visited = True
        self._grid[key].scene = scene

    def mark_checked(self) -> None:
        key = self.current_cell_key
        if key in self._grid:
            self._grid[key].checked = True

    def is_current_visited(self) -> bool:
        key = self.current_cell_key
        return key in self._grid and self._grid[key].visited

    @property
    def visited_count(self) -> int:
        return sum(1 for c in self._grid.values() if c.visited)

    def get_frontier_direction(self) -> str | None:
        """Find direction to nearest unvisited cell.

        Returns 'forward', 'left', 'right', or None if surrounded.
        """
        cx, cy = self.current_cell_key
        heading = self.pose.heading

        # Check the 4 cardinal neighbors
        neighbors = [
            (0, 1, 0),    # forward (north)
            (0, -1, 180), # backward (south)
            (-1, 0, 270), # left (west)
            (1, 0, 90),   # right (east)
        ]

        best_dir = None
        best_score = float("inf")

        for dx, dy, world_angle in neighbors:
            nkey = (cx + dx, cy + dy)
            if nkey in self._grid and self._grid[nkey].visited:
                continue

            # Angle difference between where we're facing and where the frontier is
            relative = (world_angle - heading) % 360
            if relative > 180:
                relative = 360 - relative

            if relative < best_score:
                best_score = relative
                if relative < 45:
                    best_dir = "forward"
                elif (world_angle - heading) % 360 < 180:
                    best_dir = "right"
                else:
                    best_dir = "left"

        return best_dir

    def record_movement(self, action: str, duration_s: float, speed: int) -> None:
        """Update pose based on movement command (dead reckoning)."""
        # Approximate: at speed 255, ~30cm/s forward. Scale linearly.
        speed_factor = speed / 255.0
        distance_cm = 30.0 * speed_factor * duration_s

        if action == "forward":
            self.pose.advance(distance_cm)
        elif action == "backward":
            self.pose.advance(-distance_cm)
        elif action == "left":
            self.pose.rotate(-90 * (duration_s / 0.5))
        elif action == "right":
            self.pose.rotate(90 * (duration_s / 0.5))

    def reset(self) -> None:
        self._grid.clear()
        self.pose = RobotPose()
