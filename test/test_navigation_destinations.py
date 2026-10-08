import json
from uuid import UUID
from unittest.mock import patch

import pytest

from x2_operator_panel.navigation_destinations import (
    DestinationConflict, DestinationError, NavigationDestinationStore, validate_destination,
)


def destination(identifier=None):
    value = {"label": "Loading bay", "pose": {"x": 1.0, "y": -2.0, "yaw": 0.5}}
    return dict(value, id=identifier) if identifier is not None else value


def test_catalog_capacity(tmp_path):
    path = tmp_path / "destinations.json"
    path.write_text(json.dumps({"version": 1, "revision": 1, "presets": [
        dict(destination(str(i)), revision=1) for i in range(256)
    ]}))
    store = NavigationDestinationStore(path)
    assert len(store.records()) == 256
    with pytest.raises(DestinationError, match="256"):
        store.save(destination())


def test_empty_start_generated_ids_persistence_order_and_conflicts(tmp_path):
    path = tmp_path / "nested/destinations.json"
    store = NavigationDestinationStore(path)
    assert store.snapshot()["available"]
    assert store.records() == []
    assert not path.exists(), "Reading an absent catalog must not create a configuration file"
    original = store.save(destination())["preset"]
    assert str(UUID(original["id"])) == original["id"]
    first = store.save(dict(original, label="Renamed"))["preset"]
    assert first["id"] == original["id"]
    second = store.save(destination())["preset"]
    assert second["id"] != first["id"]
    restarted = NavigationDestinationStore(path)
    assert restarted.records() == [first, second]
    for stale in (original, dict(first, revision=True)):
        with pytest.raises(DestinationConflict):
            restarted.save(stale)
        with pytest.raises(DestinationConflict):
            restarted.delete(stale["id"], stale["revision"])
    with pytest.raises(DestinationConflict):
        restarted.save(destination(first["id"]))
    restarted.delete(first["id"], first["revision"])
    replacement = restarted.save(destination())["preset"]
    assert replacement["id"] not in (first["id"], second["id"])
    with pytest.raises(DestinationConflict):
        restarted.save(first)
    restarted.delete(replacement["id"], replacement["revision"])
    restarted.delete(second["id"], second["revision"])
    assert NavigationDestinationStore(path).records() == []


def test_existing_named_ids_are_preserved(tmp_path):
    path = tmp_path / "destinations.json"
    record = dict(destination("loading-bay"), revision=7)
    path.write_text(json.dumps({"version": 1, "revision": 7, "presets": [record]}))
    store = NavigationDestinationStore(path)
    updated = store.save(dict(record, label="Renamed bay"))["preset"]
    assert updated["id"] == "loading-bay"
    assert NavigationDestinationStore(path).records() == [updated]
    with pytest.raises(DestinationConflict):
        store.save(destination("client-chosen-id"))


@pytest.mark.parametrize("key,value", [
    ("id", ""), ("id", "bad.id"), ("id", "a" * 129), ("id", []),
    ("label", " "), ("label", "a" * 81), ("pose", None),
])
def test_reject_invalid_destinations(key, value):
    with pytest.raises(DestinationError):
        validate_destination(dict(destination("bay"), **{key: value}))


@pytest.mark.parametrize("number", [None, "1", True, float("nan"), float("inf"), 10 ** 400])
def test_reject_invalid_coordinates(number):
    for key in ("x", "y", "yaw"):
        value = destination("bay")
        value["pose"][key] = number
        with pytest.raises(DestinationError):
            validate_destination(value)


def test_write_failure_preserves_catalog_and_bad_file_is_not_overwritten(tmp_path):
    store = NavigationDestinationStore(tmp_path / "destinations.json")
    first = store.save(destination())["preset"]
    original_bytes = store.path.read_bytes()
    with patch("x2_operator_panel.navigation_destinations.os.replace", side_effect=PermissionError("denied")):
        with pytest.raises(DestinationError, match="denied"):
            store.save(dict(first, label="Changed"))
    assert store.path.read_bytes() == original_bytes
    assert not list(tmp_path.glob(".navigation_destinations_*"))
    snapshot = store.snapshot()
    snapshot["presets"][0]["pose"]["x"] = 99
    assert store.records()[0]["pose"]["x"] == 1
    document = json.loads(original_bytes)
    for corruption in ({}, {**document, "presets": document["presets"] * 2}, {**document, "revision": True}):
        store.path.write_text(json.dumps(corruption))
        assert not store.snapshot()["available"]
        assert store.snapshot()["presets"] == []
        with pytest.raises(DestinationError):
            store.save(destination())
