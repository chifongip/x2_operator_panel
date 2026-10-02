"""Cache event-driven action state for the lifetime of its DDS publisher."""

from typing import Any

from action_msgs.msg import GoalStatus


ACTIVE_STATUSES = {
    GoalStatus.STATUS_ACCEPTED, GoalStatus.STATUS_EXECUTING, GoalStatus.STATUS_CANCELING,
}
TERMINAL_STATUSES = {
    GoalStatus.STATUS_SUCCEEDED, GoalStatus.STATUS_CANCELED, GoalStatus.STATUS_ABORTED,
}


class ActionGoalStatus:
    def __init__(self, action_names: tuple[str, ...]) -> None:
        self.actions: dict[str, dict[str, Any]] = {
            name: {
                "publishers": frozenset(), "ready": False, "seen": False,
                "active": None, "received": None, "generation": 0,
            }
            for name in action_names
        }

    def observe_server(self, name: str, publishers: frozenset, ready: bool) -> None:
        state = self.actions[name]
        if publishers != state["publishers"]:
            state["generation"] += 1
        if publishers != state["publishers"] or (state["ready"] and not ready):
            state.update(active=None, received=None)
        state.update(publishers=publishers, ready=ready)
        state["seen"] = state["seen"] or bool(publishers) or ready

    def receive(self, name: str, publisher: bytes, statuses: list[int], now: float) -> None:
        state = self.actions[name]
        # Do not accept queued samples from a replaced server, or mix servers
        # advertising the same action. DDS graph discovery may precede services.
        if state["publishers"] != frozenset({publisher}):
            return
        active = any(status in ACTIVE_STATUSES for status in statuses)
        unknown = any(status not in TERMINAL_STATUSES for status in statuses)
        state.update(
            active=True if active else (None if unknown else False),
            received=now,
        )

    def snapshot(self, now: float) -> dict[str, Any]:
        actions = {}
        for name, state in self.actions.items():
            connected = state["ready"] and len(state["publishers"]) == 1
            active = state["active"] if connected else None
            actions[name] = {
                "server_ready": state["ready"], "connected": connected,
                "available": active is not None, "active": active,
                "age_sec": None if state["received"] is None else now - state["received"],
            }
        return actions


class NavigationGoalStatus(ActionGoalStatus):
    ACTIONS = ("navigate_to_pose", "navigate_through_poses")

    def __init__(self) -> None:
        super().__init__(self.ACTIONS)

    def snapshot(self, now: float) -> dict[str, Any]:
        actions = super().snapshot(now)
        # The primary action is required; the second action is optional until
        # discovered. A known active goal always overrides incomplete state.
        required = [
            actions[name] for name in self.ACTIONS
            if name == self.ACTIONS[0] or self.actions[name]["seen"]
        ]
        active = any(state["active"] is True for state in required)
        available = active or all(state["available"] for state in required)
        connected = any(state["connected"] for state in required)
        return {
            "available": available, "active": active if available else None,
            "detail": ("Active navigation goal" if active else "Nav2 is idle") if available else (
                "Navigation goal state unknown; waiting for action status" if connected
                else "Nav2 action server unavailable"
            ),
            "actions": actions,
        }
