"""Discover immutable timed-rotation limits using bounded parameter requests."""

from math import isfinite

from x2_operator_panel.docking_profiles import DockingProfileMonitor


class RotationLimitsMonitor(DockingProfileMonitor):
    catalog_label = "Rotation"
    names_parameter = "rotate_max_angular_speed"
    default_parameter = "rotate_max_duration"

    def _request_profiles(self, token, values):
        if any(type(value) not in (int, float) or not isfinite(value) or value <= 0
               for value in values):
            raise ValueError("Rotation limits must be finite and positive")
        self._catalog = {"available": True, "max_angular_speed": float(values[0]),
                         "max_duration": float(values[1]), "detail": "Ready"}
        self._token = None
