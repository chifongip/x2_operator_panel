"""Table discovery, docking identity binding, and action forwarding."""

from unittest.mock import Mock

from agibot_x2_manipulation_msgs.action import Pick, Place, PickPlace

from test import test_docking_profiles as docking_tests
from test.test_docking_profiles import ParameterClient
from x2_operator_panel.ros_gateway import PanelCommandError
from x2_operator_panel.table_profiles import TableProfileMonitor, matching_table
import pytest


def table_client():
    client = ParameterClient()
    client.parameters = {
        "table_profile_names": ["second"], "default_table_profile": "default",
        "table_tag_id": 9, "table_tag_frame": "tag9",
        "table_tag_to_tabletop_center": [0.0, -0.55, 0.15],
        "table_dimensions": [0.6, 0.4, 0.6], "table_tag_place_offset": [0.0, 0.05],
        "table_tag_to_box_yaw": 0.0, "table_collision_id": "work_table",
        "table_profiles.second.tag_id": 10, "table_profiles.second.tag_frame": "tag10",
        "table_profiles.second.tabletop_center": [0.1, -0.4, 0.2],
        "table_profiles.second.dimensions": [0.8, 0.5, 0.7],
        "table_profiles.second.place_offset": [0.0, -0.1],
        "table_profiles.second.place_yaw": 0.2,
        "table_profiles.second.collision_id": "second_work_table",
    }
    return client


def test_discovers_legacy_calibration_and_distinct_named_table():
    monitor = TableProfileMonitor(table_client(), 1.0)
    monitor.poll()
    catalog = monitor.snapshot()
    assert catalog["available"]
    assert catalog["profiles"][0]["tabletop_center"] == [0.0, -0.55, 0.15]
    assert catalog["profiles"][1]["dimensions"] == [0.8, 0.5, 0.7]
    assert matching_table({"tag_id": 10, "tag_frame": "tag10"}, catalog)["id"] == "second"
    with pytest.raises(ValueError, match="exactly one"):
        matching_table({"tag_id": 10, "tag_frame": "other_frame"}, catalog)
    monitor.client.ready = False
    monitor.poll()
    assert not monitor.snapshot()["available"]
    assert monitor.snapshot()["profiles"] == []


@pytest.mark.parametrize("changes", [
    {"table_profiles.second.tag_id": 9, "table_profiles.second.tag_frame": "tag9"},
    {"table_profiles.second.collision_id": "work_table"},
    {"table_profiles.second.dimensions": [0.0, 0.5, 0.7]},
    {"table_profiles.second.tabletop_center": [0.0, 0.5, 0.2]},
    {"table_profiles.second.place_offset": [0.1]},
    {"table_profiles.second.place_yaw": float("nan")},
    {"table_profiles.second.tag_frame": None},
])
def test_invalid_catalog_is_unavailable(changes):
    client = table_client()
    client.parameters.update(changes)
    monitor = TableProfileMonitor(client, 1.0)
    monitor.poll()
    assert not monitor.snapshot()["available"]


def panel():
    node = docking_tests.DockingProfileGatewayTest().panel()
    node._table_profile_monitor = TableProfileMonitor(table_client(), 1.0)
    node._table_profile_monitor.poll()
    node._selected_visible_box_id = Mock(return_value="tag:0")
    node._action_clients.update({kind: Mock() for kind in ("pick", "place", "pick_place")})
    return node


@pytest.mark.parametrize("kind,action", [("pick", Pick), ("place", Place), ("pick_place", PickPlace)])
def test_combo_and_standalone_forward_table_and_serialize_result(kind, action):
    node = panel()
    operation = node._submit_manipulation(kind, {
        "plan_only": True, "table_profile_id": "second", "docking_profile_id": "offset",
    })
    goal = node._action_clients[kind].send_goal_async.call_args.args[0]
    assert goal.table_profile_id == "second"
    assert operation.as_dict()["table_profile_id"] == "second"
    result = action.Result()
    result.table_profile_id = "second"
    assert node._result_as_dict(result)["table_profile_id"] == "second"


@pytest.mark.parametrize("payload", [
    {"table_profile_id": "missing"}, {"table_profile_id": 9},
    {"table_profile_id": "", "docking_profile_id": "offset"},
    {"table_profile_id": "second", "docking_profile_id": "missing"},
])
def test_invalid_selection_is_rejected_before_unlock_or_submission(payload):
    node = panel()
    before = node._execution_unlocked_until
    with pytest.raises(PanelCommandError):
        node._submit_manipulation("place", {**payload, "plan_only": False, "confirmed": True})
    assert node._execution_unlocked_until == before
    node._action_clients["place"].send_goal_async.assert_not_called()


def test_manual_pose_keeps_matching_table_and_saved_execution_selection():
    node = panel()
    pose = {"frame_id": "base_link", "x": 0.35, "y": 0.0, "z": 0.17, "yaw": 0.0}
    node._submit_manipulation("place", {
        "plan_only": True, "table_profile_id": "second", "place_pose": pose,
    })
    goal = node._action_clients["place"].send_goal_async.call_args.args[0]
    assert goal.table_profile_id == "second"
    assert goal.place_pose.header.frame_id == "base_link"
    assert goal.place_pose.pose.position.x == 0.35
    node = panel()
    node._submit_manipulation("place", {
        "plan_only": False, "confirmed": True, "plan_id": "saved",
        "table_profile_id": "second",
    })
    goal = node._action_clients["place"].send_goal_async.call_args.args[0]
    assert goal.plan_id == "saved"
    assert goal.table_profile_id == "second"


def test_explicit_table_can_differ_from_docking_tag():
    node = panel()
    node._submit_manipulation("place", {
        "plan_only": True, "table_profile_id": "default", "docking_profile_id": "offset",
    })
    goal = node._action_clients["place"].send_goal_async.call_args.args[0]
    assert goal.table_profile_id == "default"


def test_box_only_pick_and_manual_place_skip_docking_table_match():
    node = panel()
    assert node._table_profile_id({"kind": "pick", "docking_profile_id": "offset"}) == ""
    pose = {"frame_id": "base_link", "x": 0.35, "y": 0.0, "z": 0.17, "yaw": 0.0}
    node._submit_manipulation("place", {
        "plan_only": True, "docking_profile_id": "offset", "place_pose": pose,
    })
    goal = node._action_clients["place"].send_goal_async.call_args.args[0]
    assert goal.place_pose.header.frame_id == "base_link"
    assert goal.table_profile_id == ""


def test_pick_reuses_successful_box_docking_profile_and_rejects_another_instance():
    node = panel()
    node._last_box_dock = {"profile_id": "box", "instance_id": "tag:17"}
    node._fresh_visible_boxes_locked = Mock(return_value=[{
        "instance_id": "tag:17", "docking_profile_ids": ["box"],
        "default_docking_profile": "box",
    }])
    node._selected_visible_box_id.return_value = "tag:17"
    operation = node._submit_manipulation("pick", {"plan_only": True, "instance_id": "tag:17"})
    assert operation.docking_profile_id == "box"
    assert operation.instance_id == "tag:17"
    goal = node._action_clients["pick"].send_goal_async.call_args.args[0]
    assert goal.instance_id == "tag:17"
    node._selected_visible_box_id.return_value = "tag:42"
    before = node._execution_unlocked_until
    with pytest.raises(PanelCommandError, match="box instance"):
        node._submit_manipulation("pick", {"plan_only": False, "confirmed": True,
            "instance_id": "tag:42", "docking_profile_id": "box"})
    assert node._execution_unlocked_until == before
