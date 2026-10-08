"""Discover the manipulation server's active box catalog without detections."""

from copy import deepcopy
import threading
import time

from agibot_x2_manipulation_msgs.srv import GetBoxProfiles

from x2_operator_panel.docking_profiles import unavailable_catalog
from x2_operator_panel.box_profile_ids import is_box_profile_id


class BoxProfileMonitor:
    """Bound discovery requests and discard responses from superseded requests."""

    def __init__(self, client, timeout, clock=time.monotonic):
        self.client = client
        self.timeout = timeout
        self.clock = clock
        self._lock = threading.RLock()
        self._catalog = unavailable_catalog("Waiting for box profile configuration")
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
                self._invalidate("Box profile service unavailable")
                self._next_refresh = 0.0
                return
            if self._token is not None:
                if now < self._deadline:
                    return
                self._invalidate("Box profile request timed out; retrying")
            if now < self._next_refresh:
                return
            self._next_refresh = now + 5.0
            token = object()
            self._token = token
            self._deadline = now + self.timeout
            try:
                self._future = self.client.call_async(GetBoxProfiles.Request())
                self._future.add_done_callback(
                    lambda completed: self._on_response(token, completed)
                )
            except Exception as error:
                self._invalidate(f"Cannot read box profiles: {error}")

    def _on_response(self, token, completed):
        with self._lock:
            if self._token is not token:
                return
            if self.clock() >= self._deadline:
                self._invalidate("Box profile request timed out; retrying")
                return
            if not self.client.service_is_ready():
                self._invalidate("Box profile service unavailable")
                return
            self._future = None
            try:
                response = completed.result()
                names = list(response.profile_ids)
                version = response.profile_version
                if (any(not is_box_profile_id(name) for name in names)
                        or len(set(names)) != len(names)
                        or type(version) is not int or not 0 <= version <= 18446744073709551615):
                    raise ValueError("Invalid box profile catalog")
                self._catalog = {
                    "available": True, "default_profile": None,
                    "profiles": [{"id": name} for name in sorted(names)],
                    "profile_version": version, "detail": "Ready",
                }
                self._token = None
            except Exception as error:
                self._invalidate(f"Cannot read box profiles: {error}")
