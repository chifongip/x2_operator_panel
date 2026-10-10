import unittest

from x2_operator_panel.navigation_status import NavigationGoalStatus


PRIMARY, SECONDARY = NavigationGoalStatus.ACTIONS


def connected_status():
    status = NavigationGoalStatus()
    status.observe_server(PRIMARY, frozenset({b"first"}), True)
    return status


class NavigationStatusTest(unittest.TestCase):
    def test_ready_server_does_not_imply_idle_before_first_message(self):
        result = connected_status().snapshot(100.0)
        assert result["available"] is False
        assert result["active"] is None
        assert "waiting for action status" in result["detail"]

    def test_idle_and_active_survive_silence(self):
        for code, active in [(4, False), (2, True), (3, True)]:
            status = connected_status()
            status.receive(PRIMARY, b"first", [code], 100.0)
            result = status.snapshot(1000.0)
            assert result["available"] is True
            assert result["active"] is active
            assert result["actions"][PRIMARY]["age_sec"] == 900.0

    def test_empty_status_array_is_idle_and_unknown_goal_is_unknown(self):
        status = connected_status()
        status.receive(PRIMARY, b"first", [], 100.0)
        assert status.snapshot(101.0)["active"] is False
        status.receive(PRIMARY, b"first", [0], 102.0)
        assert status.snapshot(103.0)["available"] is False

    def test_restart_invalidates_cache_and_rejects_old_queued_samples(self):
        status = connected_status()
        status.receive(PRIMARY, b"first", [4], 100.0)
        status.observe_server(PRIMARY, frozenset({b"replacement"}), True)
        status.receive(PRIMARY, b"first", [4], 101.0)
        assert status.snapshot(102.0)["available"] is False
        status.receive(PRIMARY, b"replacement", [2], 103.0)
        assert status.snapshot(104.0)["active"] is True

    def test_disconnect_and_reconnect_require_a_new_status(self):
        status = connected_status()
        status.receive(PRIMARY, b"first", [4], 100.0)
        status.observe_server(PRIMARY, frozenset({b"first"}), False)
        assert status.snapshot(101.0)["available"] is False
        status.observe_server(PRIMARY, frozenset({b"first"}), True)
        assert status.snapshot(102.0)["available"] is False
        status.receive(PRIMARY, b"first", [4], 103.0)
        status.observe_server(PRIMARY, frozenset(), False)
        assert status.snapshot(104.0)["detail"] == "Single-pose Nav2 action server unavailable"

    def test_secondary_navigation_blocks_idle_and_overrides_unknown_primary(self):
        status = connected_status()
        status.observe_server(SECONDARY, frozenset({b"second"}), True)
        status.receive(SECONDARY, b"second", [2], 100.0)
        assert status.snapshot(1000.0)["active"] is True
        status.receive(SECONDARY, b"second", [4], 1001.0)
        assert status.snapshot(1002.0)["available"] is False
        status.receive(PRIMARY, b"first", [4], 1003.0)
        assert status.snapshot(1004.0)["active"] is False
        status.observe_server(SECONDARY, frozenset(), False)
        assert status.snapshot(1005.0)["available"] is True

    def test_known_active_primary_overrides_unknown_secondary(self):
        status = connected_status()
        status.receive(PRIMARY, b"first", [2], 100.0)
        status.observe_server(SECONDARY, frozenset({b"second"}), True)
        assert status.snapshot(1000.0)["active"] is True

    def test_duplicate_publishers_cannot_supply_idle_status(self):
        status = connected_status()
        status.observe_server(PRIMARY, frozenset({b"first", b"duplicate"}), True)
        status.receive(PRIMARY, b"first", [], 100.0)
        assert status.snapshot(101.0)["available"] is False

    def test_status_before_service_discovery_is_retained_when_server_becomes_ready(self):
        status = NavigationGoalStatus()
        status.observe_server(PRIMARY, frozenset({b"first"}), False)
        status.receive(PRIMARY, b"first", [2], 100.0)
        assert status.snapshot(101.0)["available"] is False
        status.observe_server(PRIMARY, frozenset({b"first"}), True)
        assert status.snapshot(102.0)["active"] is True


    def test_idle_primary_survives_secondary_discovery_silence_and_restart(self):
        status = connected_status()
        status.receive(PRIMARY, b"first", [4], 100.0)
        for publishers, ready in (
            (frozenset({b"second"}), True),
            (frozenset(), False),
            (frozenset({b"replacement"}), True),
            (frozenset({b"replacement", b"duplicate"}), True),
        ):
            with self.subTest(publishers=publishers):
                status.observe_server(SECONDARY, publishers, ready)
                result = status.snapshot(1000.0)
                assert result["available"] is True
                assert result["active"] is False
                assert result["actions"][SECONDARY]["available"] is False
                assert result["actions"][SECONDARY]["age_sec"] is None
                assert "no active navigation reported" in result["detail"]

    def test_secondary_active_states_override_idle_or_unknown_primary(self):
        for primary_idle in (True, False):
            for code in (1, 2, 3):
                with self.subTest(primary_idle=primary_idle, secondary_status=code):
                    status = connected_status()
                    if primary_idle:
                        status.receive(PRIMARY, b"first", [4], 100.0)
                    status.observe_server(SECONDARY, frozenset({b"second"}), True)
                    status.receive(SECONDARY, b"second", [code], 101.0)
                    assert status.snapshot(102.0)["active"] is True
                    for terminal in (4, 5, 6):
                        status.receive(SECONDARY, b"second", [terminal], 103.0)
                        result = status.snapshot(104.0)
                        assert result["available"] is primary_idle
                        assert result["active"] is (False if primary_idle else None)

    def test_secondary_idle_does_not_establish_primary_status(self):
        status = connected_status()
        status.observe_server(SECONDARY, frozenset({b"second"}), True)
        status.receive(SECONDARY, b"second", [], 100.0)
        assert status.snapshot(101.0)["available"] is False
        assert "Single-pose" in status.snapshot(101.0)["detail"]
        status.observe_server(PRIMARY, frozenset(), False)
        result = status.snapshot(102.0)
        assert result["available"] is False
        assert "server unavailable" in result["detail"]
