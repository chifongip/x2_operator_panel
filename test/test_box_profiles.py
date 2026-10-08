"""Read active box profiles without relying on visible detections."""

from concurrent.futures import Future
from types import SimpleNamespace

import pytest

from test.test_ros_gateway import _new_panel_node
from x2_operator_panel.box_profiles import BoxProfileMonitor


class LateFuture(Future):
    def cancel(self):
        return False


class CatalogClient:
    def __init__(self, deferred=False):
        self.ready = True
        self.deferred = deferred
        self.response = SimpleNamespace(
            profile_ids=["grey_box", "box_a", "box_b"], profile_version=0
        )
        self.requests = []
        self.removed = []

    def service_is_ready(self):
        return self.ready

    def call_async(self, request):
        future = LateFuture()
        self.requests.append((request, future))
        if not self.deferred:
            future.set_result(self.response)
        return future

    def remove_pending_request(self, future):
        self.removed.append(future)


def test_catalog_lists_unseen_boxes_and_refreshes_after_reload():
    client = CatalogClient()
    now = [0.0]
    monitor = BoxProfileMonitor(client, 1.0, clock=lambda: now[0])
    monitor.poll()
    snapshot = monitor.snapshot()
    assert snapshot["available"]
    assert [profile["id"] for profile in snapshot["profiles"]] == ["box_a", "box_b", "grey_box"]
    snapshot["profiles"].clear()
    assert len(monitor.snapshot()["profiles"]) == 3
    monitor.poll()
    assert len(client.requests) == 1
    client.response = SimpleNamespace(profile_ids=["new_box"], profile_version=1)
    now[0] = 5.1
    monitor.poll()
    assert monitor.snapshot()["profiles"] == [{"id": "new_box"}]
    assert monitor.snapshot()["profile_version"] == 1
    node = _new_panel_node()
    assert not node._box_catalog()["available"]
    node._box_profile_monitor = monitor
    assert node._box_catalog() == monitor.snapshot()


def test_empty_legacy_catalog_is_available_without_an_invented_default():
    client = CatalogClient()
    client.response.profile_ids = []
    monitor = BoxProfileMonitor(client, 1.0)
    monitor.poll()
    assert monitor.snapshot()["available"]
    assert monitor.snapshot()["profiles"] == []
    assert monitor.snapshot()["default_profile"] is None


@pytest.mark.parametrize("name", ["box-a", "Box B", "箱A", ":manual", " box ", "x" * 129])
def test_server_profile_names_do_not_hide_other_configured_boxes(name):
    client = CatalogClient()
    client.response.profile_ids = [name, "grey_box"]
    monitor = BoxProfileMonitor(client, 1.0)
    monitor.poll()
    snapshot = monitor.snapshot()
    assert snapshot["available"], snapshot["detail"]
    assert {profile["id"] for profile in snapshot["profiles"]} == {name, "grey_box"}


def test_catalog_does_not_impose_a_profile_count_limit_absent_from_the_server():
    client = CatalogClient()
    client.response.profile_ids = [f"box_{index}" for index in range(257)]
    monitor = BoxProfileMonitor(client, 1.0)
    monitor.poll()
    assert monitor.snapshot()["available"]
    assert len(monitor.snapshot()["profiles"]) == 257


def test_timeout_discards_late_response_and_bounds_requests():
    client = CatalogClient(deferred=True)
    now = [0.0]
    monitor = BoxProfileMonitor(client, 1.0, clock=lambda: now[0])
    monitor.poll()
    old_future = client.requests[0][1]
    monitor.poll()
    assert len(client.requests) == 1
    now[0] = 1.1
    monitor.poll()
    assert "timed out" in monitor.snapshot()["detail"]
    assert client.removed == [old_future]
    now[0] = 5.1
    monitor.poll()
    old_future.set_result(client.response)
    assert not monitor.snapshot()["available"]
    assert len(client.requests) == 2
    client.requests[-1][1].set_result(SimpleNamespace(profile_ids=["fresh"], profile_version=2))
    assert monitor.snapshot()["profiles"] == [{"id": "fresh"}]


def test_disconnect_clears_catalog_and_restart_discovers_new_names():
    client = CatalogClient()
    monitor = BoxProfileMonitor(client, 1.0)
    monitor.poll()
    client.ready = False
    monitor.poll()
    assert not monitor.snapshot()["available"]
    assert monitor.snapshot()["profiles"] == []
    client.response = SimpleNamespace(profile_ids=["restarted"], profile_version=0)
    client.ready = True
    monitor.poll()
    assert monitor.snapshot()["profiles"] == [{"id": "restarted"}]


@pytest.mark.parametrize("names,version", [
    (["duplicate", "duplicate"], 0), (["nested.box"], 0), ([""], 0),
    ([None], 0), (["box"], -1), (["box"], True),
])
def test_invalid_response_clears_catalog(names, version):
    client = CatalogClient()
    client.response = SimpleNamespace(profile_ids=names, profile_version=version)
    monitor = BoxProfileMonitor(client, 1.0)
    monitor.poll()
    assert not monitor.snapshot()["available"]
    assert monitor.snapshot()["profiles"] == []


def test_service_errors_do_not_escape_the_timer_or_keep_stale_catalog():
    client = CatalogClient(deferred=True)
    monitor = BoxProfileMonitor(client, 1.0)
    monitor.poll()
    client.requests[-1][1].set_exception(RuntimeError("service failed"))
    assert not monitor.snapshot()["available"]
    assert "service failed" in monitor.snapshot()["detail"]
