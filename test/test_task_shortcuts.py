from copy import deepcopy
import json
from unittest.mock import patch

import pytest

from x2_operator_panel.task_shortcuts import (
    ShortcutConflict,
    ShortcutError,
    TaskShortcutStore,
    validate_shortcut,
)


def pick_shortcut():
    return {
        "name": "Pick grey box",
        "action": "pick",
        "box": {"profile_id": "grey_box", "instance_id": "tag:180"},
        "dock": {"enabled": True, "profile_id": "grey_box_dock"},
        "posture": {"enabled": True, "height": 0.48, "waist_yaw": 0.2},
        "return_posture": {"enabled": True, "height": 0.64, "waist_yaw": 0.0},
        "undock": {"enabled": True, "profile_id": "grey_box_retreat"},
        "place": None,
    }


def test_create_restart_update_and_delete(tmp_path):
    path = tmp_path / "nested" / "shortcuts.json"
    store = TaskShortcutStore(path)
    assert store.snapshot()["shortcuts"] == []
    assert not path.exists()
    saved = store.save(pick_shortcut())["shortcut"]
    assert saved["revision"] == 1
    restarted = TaskShortcutStore(path)
    assert restarted.snapshot()["shortcuts"] == [saved]
    edited = dict(saved, name="Pick Box B", box={"profile_id": "box_b", "instance_id": "tag:183"})
    updated = restarted.save(edited)["shortcut"]
    assert updated["id"] == saved["id"]
    assert updated["revision"] == 2
    assert store.snapshot()["shortcuts"] == [updated]
    assert store.delete(updated["id"], updated["revision"])["shortcuts"] == []
    assert json.loads(path.read_text())["version"] == 1


@pytest.mark.parametrize("instance", [None, "", "missing"])
def test_profile_target_can_be_saved_without_a_fixed_tag(tmp_path, instance):
    value = pick_shortcut()
    value["box"] = {"profile_id": "grey_box"}
    if instance != "missing":
        value["box"]["instance_id"] = instance
    store = TaskShortcutStore(tmp_path / "shortcuts.json")
    saved = store.save(value)["shortcut"]
    assert saved["box"] == {"profile_id": "grey_box", "instance_id": None}
    assert TaskShortcutStore(store.path).snapshot()["shortcuts"] == [saved]
    fixed = store.save(dict(saved, box={"profile_id": "grey_box", "instance_id": "tag:181"}))["shortcut"]
    assert fixed["box"]["instance_id"] == "tag:181"
    assert store.save(dict(fixed, box={"profile_id": "grey_box"}))["shortcut"]["box"]["instance_id"] is None


def test_stale_edits_and_deletes_do_not_overwrite(tmp_path):
    store = TaskShortcutStore(tmp_path / "shortcuts.json")
    original = store.save(pick_shortcut())["shortcut"]
    updated = store.save(dict(original, name="Edited elsewhere"))["shortcut"]
    with pytest.raises(ShortcutConflict):
        store.save(dict(original, name="Stale edit"))
    with pytest.raises(ShortcutConflict):
        store.delete(original["id"], original["revision"])
    assert store.snapshot()["shortcuts"] == [updated]
    store.delete(updated["id"], updated["revision"])
    with pytest.raises(ShortcutConflict):
        store.save(updated)


@pytest.mark.parametrize("name", ["box-a", "Box B", "箱A", ":manual", " box ", "x" * 129])
def test_server_box_profile_ids_round_trip_without_normalization(tmp_path, name):
    store = TaskShortcutStore(tmp_path / "shortcuts.json")
    value = pick_shortcut()
    value["box"] = {"profile_id": name, "instance_id": None}
    saved = store.save(value)["shortcut"]
    assert saved["box"]["profile_id"] == name
    assert TaskShortcutStore(store.path).snapshot()["shortcuts"] == [saved]


@pytest.mark.parametrize("name", [None, 9, "", "nested.box", ".manual"])
def test_box_profile_requires_one_nonempty_parameter_component(name):
    value = pick_shortcut()
    value["box"]["profile_id"] = name
    with pytest.raises(ShortcutError, match="Box requires a named profile"):
        validate_shortcut(value)


@pytest.mark.parametrize("field", ["dock", "undock", "table"])
def test_box_naming_support_does_not_change_other_profile_rules(field):
    value = pick_shortcut()
    if field == "table":
        value.update(action="place", place={"mode": "automatic", "table_profile_id": "table-a"})
    else:
        value[field]["profile_id"] = "dock-a"
    with pytest.raises(ShortcutError, match="requires a named profile"):
        validate_shortcut(value)


@pytest.mark.parametrize("contents", ["{broken", '{"version":2,"shortcuts":[]}',
                                       '{"version":1,"shortcuts":[{}]}'])
def test_corrupt_storage_is_visible_and_preserved(tmp_path, contents):
    path = tmp_path / "shortcuts.json"
    path.write_text(contents)
    store = TaskShortcutStore(path)
    assert not store.snapshot()["available"]
    with pytest.raises(ShortcutError):
        store.save(pick_shortcut())
    assert path.read_text() == contents


def test_storage_existence_permission_error_is_reported_and_preserves_file(tmp_path):
    store = TaskShortcutStore(tmp_path / "shortcuts.json")
    saved = store.save(pick_shortcut())["shortcut"]
    before = store.path.read_bytes()
    with patch("x2_operator_panel.task_shortcuts.Path.exists", side_effect=PermissionError("Access denied")):
        snapshot = store.snapshot()
        assert not snapshot["available"]
        assert "Access denied" in snapshot["detail"]
        with pytest.raises(ShortcutError, match="Access denied"):
            store.save(saved)
        with pytest.raises(ShortcutError, match="Access denied"):
            store.delete(saved["id"], saved["revision"])
    assert store.path.read_bytes() == before


def test_atomic_write_failure_preserves_previous_file(tmp_path):
    path = tmp_path / "shortcuts.json"
    store = TaskShortcutStore(path)
    saved = store.save(pick_shortcut())["shortcut"]
    before = path.read_bytes()
    with patch("x2_operator_panel.task_shortcuts.os.replace", side_effect=OSError("Disk error")):
        with pytest.raises(ShortcutError, match="Disk error"):
            store.save(dict(saved, name="Lost update"))
    assert path.read_bytes() == before
    assert sorted(item.name for item in tmp_path.iterdir()) == ["shortcuts.json"]


@pytest.mark.parametrize("field,value", [
    ("action", "automatic"), ("name", " "), ("box", None),
    ("box", {"instance_id": None}),
    ("box", {"profile_id": "grey_box", "instance_id": False}),
    ("box", {"profile_id": "grey_box", "instance_id": "tag:0180"}),
    ("box", {"profile_id": "grey_box", "instance_id": "tag:2147483648"}),
    ("dock", {"enabled": True, "profile_id": ""}),
    ("dock", {"enabled": 1, "profile_id": "dock"}),
    ("dock", {"enabled": True, "profile_id": "../dock"}),
    ("posture", {"enabled": True, "height": float("nan"), "waist_yaw": 0}),
    ("posture", {"enabled": True, "height": True, "waist_yaw": 0}),
    ("posture", {"enabled": True, "height": 0.65, "waist_yaw": 0}),
    ("posture", {"enabled": True, "height": 0.48, "waist_yaw": 1.6}),
])
def test_invalid_shortcuts_are_rejected(field, value):
    with pytest.raises(ShortcutError):
        validate_shortcut(dict(pick_shortcut(), **{field: value}))


def test_place_targets_and_optional_stages_can_be_saved_without_live_ros(tmp_path):
    store = TaskShortcutStore(tmp_path / "shortcuts.json")
    value = pick_shortcut()
    value.update(action="place", box=None,
                 place={"mode": "automatic", "table_profile_id": "independent_table"})
    for stage in ("dock", "undock"):
        value[stage] = {"enabled": False, "profile_id": ""}
    saved = store.save(value)["shortcut"]
    assert saved["place"]["table_profile_id"] == "independent_table"
    manual = deepcopy(value)
    manual["place"] = {"mode": "manual", "pose": {
        "frame_id": "map", "x": 1.2, "y": -0.5, "z": 0.2, "yaw": 0.7}}
    assert store.save(manual)["shortcut"]["place"] == manual["place"]
    for invalid in (float("inf"), 10 ** 1000):
        manual["place"]["pose"]["x"] = invalid
        with pytest.raises(ShortcutError):
            store.save(manual)


def test_storage_requires_absolute_path():
    with pytest.raises(ShortcutError, match="absolute"):
        TaskShortcutStore("shortcuts.json")


def test_navigation_and_carry_options_persist_and_old_files_remain_compatible(tmp_path):
    path = tmp_path / "shortcuts.json"
    legacy = dict(pick_shortcut(), id="legacy", revision=4)
    path.write_text(json.dumps({"version": 1, "shortcuts": [legacy]}))
    store = TaskShortcutStore(path)
    loaded = store.snapshot()["shortcuts"][0]
    for stage in ("navigate_start", "navigate_end", "carry_start", "carry_end"):
        assert not loaded[stage]["enabled"]
    assert loaded["revision"] == 4
    loaded["navigate_start"] = {"enabled": True, "preset_id": "loading-bay"}
    loaded["navigate_end"] = {"enabled": True, "preset_id": "dropoff"}
    loaded["carry_end"] = {"enabled": True, "pose": "b"}
    updated = store.save(loaded)["shortcut"]
    assert TaskShortcutStore(path).snapshot()["shortcuts"] == [updated]


@pytest.mark.parametrize("stage,settings", [
    ("navigate_start", {"enabled": True, "preset_id": ""}),
    ("navigate_end", {"enabled": True, "preset_id": "../bad"}),
    ("navigate_start", {"enabled": 1, "preset_id": "dock"}),
    ("carry_end", {"enabled": True, "pose": "c"}),
    ("carry_end", {"enabled": "yes", "pose": "a"}),
    ("carry_start", {"enabled": True, "pose": "a"}),
])
def test_invalid_navigation_and_carry_options_are_rejected(stage, settings):
    with pytest.raises(ShortcutError):
        validate_shortcut(dict(pick_shortcut(), **{stage: settings}))


def test_carry_before_place_is_allowed_but_after_place_is_rejected():
    value = dict(pick_shortcut(), action="place", box=None,
                 place={"mode": "automatic", "table_profile_id": "table"},
                 carry_start={"enabled": True, "pose": "b"})
    assert validate_shortcut(value)["carry_start"]["enabled"]
    value["carry_end"] = {"enabled": True, "pose": "a"}
    with pytest.raises(ShortcutError, match="held box"):
        validate_shortcut(value)
