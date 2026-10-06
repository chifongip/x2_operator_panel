"""Discover the navigation server's immutable docking profiles asynchronously."""

from copy import deepcopy
from math import isfinite
import re
import threading
import time

from rcl_interfaces.srv import GetParameters
from rclpy.parameter import parameter_value_to_python


PROFILE_FIELDS = ("tag_id", "tag_frame", "standoff", "lateral_offset", "yaw_offset")


def unavailable_catalog(detail="Waiting for docking profile configuration"):
    return {"available": False, "default_profile": None, "profiles": [], "detail": detail}


class DockingProfileMonitor:
    """Use one bounded parameter request at a time; ignore superseded responses."""

    catalog_label = "Docking"
    names_parameter = "docking_profile_names"
    default_parameter = "default_docking_profile"
    profile_prefix = "docking_profiles"
    profile_fields = PROFILE_FIELDS

    def __init__(self, client, timeout, clock=time.monotonic):
        self.client = client
        self.timeout = timeout
        self.clock = clock
        self._lock = threading.RLock()
        self._catalog = unavailable_catalog(f"Waiting for {self.catalog_label.lower()} profile configuration")
        self._token = None
        self._future = None
        self._deadline = 0.0
        self._next_refresh = 0.0

    def snapshot(self):
        with self._lock:
            return deepcopy(self._catalog)

    def _invalidate(self, detail):
        self._catalog = unavailable_catalog(detail)
        self._token = None
        if self._future is not None:
            if hasattr(self.client, "remove_pending_request"):
                self.client.remove_pending_request(self._future)
            self._future.cancel()
            self._future = None

    def poll(self):
        with self._lock:
            now = self.clock()
            if not self.client.service_is_ready():
                self._invalidate(f"{self.catalog_label} profile service unavailable")
                self._next_refresh = 0.0
                return
            if self._token is not None:
                if now < self._deadline:
                    return
                self._invalidate(f"{self.catalog_label} profile request timed out; retrying")
            if now < self._next_refresh:
                return
            self._next_refresh = now + 5.0
            token = object()
            self._token = token
            self._request(
                token, [self.names_parameter, self.default_parameter],
                lambda values: self._request_profiles(token, values),
            )

    def _request(self, token, names, callback):
        request = GetParameters.Request()
        request.names = names
        self._deadline = self.clock() + self.timeout
        try:
            self._future = self.client.call_async(request)
            self._future.add_done_callback(
                lambda completed: self._on_response(token, names, callback, completed)
            )
        except Exception as error:
            self._invalidate(f"Cannot read {self.catalog_label.lower()} profiles: {error}")

    def _on_response(self, token, names, callback, completed):
        with self._lock:
            if self._token is not token:
                return
            if self.clock() >= self._deadline:
                self._invalidate(f"{self.catalog_label} profile request timed out; retrying")
                return
            if not self.client.service_is_ready():
                self._invalidate(f"{self.catalog_label} profile service unavailable")
                return
            self._future = None
            try:
                values = completed.result().values
                if len(values) != len(names):
                    raise ValueError(f"Incomplete {self.catalog_label.lower()} parameter response")
                callback([parameter_value_to_python(value) for value in values])
            except Exception as error:
                self._invalidate(f"Cannot read {self.catalog_label.lower()} profiles: {error}")

    def _request_profiles(self, token, values):
        additional, default = values
        if not isinstance(additional, list) or not isinstance(default, str):
            raise ValueError(f"{self.catalog_label} profile configuration is unavailable")
        names = ["default", *additional]
        if (len(names) > 256 or any(
                not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_]+", name)
                for name in names) or len(set(names)) != len(names) or default not in names):
            raise ValueError(f"Invalid {self.catalog_label.lower()} profile names or default")
        parameters = [
            self.parameter_name(name, field)
            for name in names for field in self.profile_fields
        ]
        self._request(token, parameters, lambda fields: self._store_profiles(names, default, fields))

    def _store_profiles(self, names, default, values):
        profiles = []
        for index, name in enumerate(names):
            fields = values[index * len(self.profile_fields):(index + 1) * len(self.profile_fields)]
            profile = dict(zip(self.profile_fields, fields), id=name)
            self.validate_profile(profile)
            profiles.append(profile)
        self._catalog = {
            "available": True, "default_profile": default, "profiles": profiles, "detail": "Ready",
        }
        self._token = None

    def parameter_name(self, name, field):
        return field if name == "default" else f"{self.profile_prefix}.{name}.{field}"

    def validate_profile(self, profile):
        if (type(profile["tag_id"]) is not int or profile["tag_id"] < 0
                or not isinstance(profile["tag_frame"], str) or not profile["tag_frame"]
                or any(type(profile[field]) is not float or not isfinite(profile[field])
                       for field in PROFILE_FIELDS[2:]) or profile["standoff"] <= 0):
            raise ValueError(f"Invalid docking profile: {profile['id']}")
