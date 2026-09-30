"""Bounded, passive timing for the current manipulation task."""

from __future__ import annotations


ACTIVE_TASK_STATUSES = {"running", "retrying", "paused"}
TERMINAL_TASK_STATUSES = {"completed", "failed", "canceled", "interrupted"}


class ManipulationTiming:
    """Time received task and controller status transitions with a monotonic clock."""

    def __init__(self) -> None:
        self.task_id = ""
        self.task_start: float | None = None
        self.task_end: float | None = None
        self.partial = False
        self.controller_active: dict[str, float] = {}
        self.controller_seconds = 0.0
        self.controller_goals = 0

    def observe_task(self, task, now: float) -> None:
        task_id = task.get("task_id", "")
        status = task.get("status", "")
        if not task_id:
            return
        if task_id != self.task_id:
            if status not in ACTIVE_TASK_STATUSES:
                return  # A latched or historical terminal status has no start.
            self.__init__()
            self.task_id = task_id
            self.task_start = now
            self.partial = not (
                status == "running" and not task.get("phase")
                and task.get("attempt") == 0
            )
        elif status in TERMINAL_TASK_STATUSES and self.task_end is None:
            self.task_end = now

    def observe_controller(self, goal_id: str, status: int, now: float,
                           executing: int, terminal: set[int]) -> None:
        if not self.task_id or self.task_start is None:
            return
        if status == executing:
            if self.task_end is None and goal_id not in self.controller_active:
                self.controller_active[goal_id] = now
                self.controller_goals += 1
        elif status in terminal and goal_id in self.controller_active:
            started = self.controller_active.pop(goal_id)
            stopped = self.task_end if self.task_end is not None else now
            self.controller_seconds += max(0.0, min(now, stopped) - started)

    def snapshot(self, now: float) -> dict:
        if self.task_start is None:
            return {
                "task_elapsed_sec": None,
                "controller_execution_sec": None,
                "controller_goal_count": 0,
                "timing_partial": False,
            }
        stopped = self.task_end if self.task_end is not None else now
        controller_seconds = self.controller_seconds + sum(
            max(0.0, stopped - started)
            for started in self.controller_active.values()
        )
        return {
            "task_elapsed_sec": max(0.0, stopped - self.task_start),
            "controller_execution_sec": controller_seconds,
            "controller_goal_count": self.controller_goals,
            "timing_partial": self.partial,
        }
