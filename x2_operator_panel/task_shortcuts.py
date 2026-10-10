"""Validated, persistent task shortcuts. Saving never commands motion."""

from copy import deepcopy
import json
from math import isfinite
import os
from pathlib import Path
import re
import tempfile
import threading
from uuid import uuid4

from x2_operator_panel.box_profile_ids import is_box_profile_id


class ShortcutError(ValueError):
    """Invalid shortcut or unavailable storage."""


class ShortcutConflict(ShortcutError):
    """Another editor changed or deleted this shortcut."""


def _mapping(value, label):
    if not isinstance(value, dict):
        raise ShortcutError(f"{label} must be an object")
    return value


def _profile(value, label, required=True):
    if not required and value in (None, ""):
        return ""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,128}", value):
        raise ShortcutError(f"{label} requires a named profile")
    return value


def _number(value, label, lower=None, upper=None):
    try:
        finite = type(value) in (int, float) and isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ShortcutError(f"{label} must be finite")
    if (lower is not None and value < lower) or (upper is not None and value > upper):
        raise ShortcutError(f"{label} is outside the supported range")
    return float(value)


def validate_shortcut(value):
    value = _mapping(value, "Shortcut")
    name = value.get("name")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise ShortcutError("Shortcut name must be 1–80 characters")
    action = value.get("action")
    if action not in ("pick", "place"):
        raise ShortcutError("Choose Pick or Place")
    result = {"name": name.strip(), "action": action}
    for stage in ("navigate_start", "navigate_end", "carry_start", "carry_end"):
        settings = _mapping(value.get(stage, {"enabled": False}), stage)
        enabled = settings.get("enabled")
        if type(enabled) is not bool:
            raise ShortcutError(f"{stage} enabled must be boolean")
        if stage.startswith("navigate"):
            identifier = settings.get("preset_id", "")
            if (not isinstance(identifier, str) or len(identifier) > 128 or (enabled and not identifier)
                    or (identifier and not identifier.replace("_", "").replace("-", "").isalnum())):
                raise ShortcutError(f"{stage} requires a valid navigation preset ID")
            result[stage] = {"enabled": enabled, "preset_id": identifier}
        else:
            pose = settings.get("pose", "a")
            if pose not in ("a", "b"):
                raise ShortcutError(f"{stage} pose must be Carry A or Carry B")
            result[stage] = {"enabled": enabled, "pose": pose}
    if ((action == "pick" and result["carry_start"]["enabled"])
            or (action == "place" and result["carry_end"]["enabled"])):
        raise ShortcutError("Carry poses require a held box: enable before Place or after Pick")
    for stage in ("rotate_start", "rotate_end"):
        settings = _mapping(value.get(stage, {"enabled": False}), stage)
        enabled = settings.get("enabled")
        if type(enabled) is not bool:
            raise ShortcutError(f"{stage} enabled must be boolean")
        speed = _number(settings.get("angular_speed", 0.2), stage + " angular speed")
        duration = _number(settings.get("duration", 1.0), stage + " duration")
        if speed == 0 or duration <= 0:
            raise ShortcutError(f"{stage} requires nonzero speed and positive duration")
        result[stage] = {"enabled": enabled, "angular_speed": speed, "duration": duration}
    box = value.get("box")
    if box is not None:
        box = _mapping(box, "Box")
        instance = box.get("instance_id")
        if instance == "":
            instance = None
        if instance is not None and (
                not isinstance(instance, str) or not re.fullmatch(r"tag:(0|[1-9][0-9]{0,9})", instance)
                or int(instance[4:]) > 2147483647):
            raise ShortcutError("Box instance must be tag:<nonnegative ID>")
        profile_id = box.get("profile_id")
        if not is_box_profile_id(profile_id):
            raise ShortcutError("Box requires a named profile")
        box = {"profile_id": profile_id, "instance_id": instance}
    if action == "pick" and box is None:
        raise ShortcutError("Pick requires a box profile")
    result["box"] = box
    for stage in ("dock", "posture", "return_posture", "undock"):
        settings = _mapping(value.get(stage), stage)
        enabled = settings.get("enabled")
        if type(enabled) is not bool:
            raise ShortcutError(f"{stage} enabled must be boolean")
        if stage in ("dock", "undock"):
            result[stage] = {"enabled": enabled, "profile_id": _profile(
                settings.get("profile_id"), stage, required=enabled)}
        else:
            result[stage] = {"enabled": enabled,
                             "height": _number(settings.get("height", 0.64), stage + " height", 0.30, 0.64),
                             "waist_yaw": _number(settings.get("waist_yaw", 0.0), stage + " yaw", -1.5708, 1.5708)}
    result["place"] = None
    if action == "place":
        place = _mapping(value.get("place"), "Place target")
        if place.get("mode") == "automatic":
            result["place"] = {"mode": "automatic", "table_profile_id": _profile(
                place.get("table_profile_id"), "Table")}
        elif place.get("mode") == "manual":
            pose = _mapping(place.get("pose"), "Manual pose")
            if pose.get("frame_id") not in ("base_link", "map"):
                raise ShortcutError("Manual pose frame must be base_link or map")
            result["place"] = {"mode": "manual", "pose": {
                "frame_id": pose["frame_id"],
                **{key: _number(pose.get(key), "Place " + key) for key in ("x", "y", "z", "yaw")}}}
        else:
            raise ShortcutError("Choose automatic or manual placement")
    return result


class TaskShortcutStore:
    def __init__(self, path):
        self.path = Path(path).expanduser()
        if not self.path.is_absolute():
            raise ShortcutError("task_shortcuts_file must be an absolute path")
        self._lock = threading.RLock()

    def _read(self):
        try:
            if not self.path.exists():
                return []
            if self.path.stat().st_size > 1024 * 1024:
                raise ShortcutError("Shortcut file is too large")
            document = json.loads(self.path.read_text(encoding="utf-8"))
            if (not isinstance(document, dict) or type(document.get("version")) is not int
                    or document["version"] != 1 or not isinstance(document.get("shortcuts"), list)
                    or len(document["shortcuts"]) > 256):
                raise ShortcutError("Invalid shortcut file format or version")
            records, ids = [], set()
            for record in document["shortcuts"]:
                normalized = validate_shortcut(record)
                identifier, revision = record.get("id"), record.get("revision")
                if (not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", identifier)
                        or identifier in ids or type(revision) is not int or revision < 1):
                    raise ShortcutError("Invalid shortcut ID or revision")
                ids.add(identifier)
                records.append(dict(normalized, id=identifier, revision=revision))
            return records
        except (OSError, ValueError, TypeError) as error:
            raise ShortcutError(f"Cannot read shortcuts: {error}") from error

    def snapshot(self):
        with self._lock:
            try:
                return {"available": True, "version": 1, "shortcuts": deepcopy(self._read()), "detail": "Ready"}
            except ShortcutError as error:
                return {"available": False, "version": 1, "shortcuts": [], "detail": str(error)}

    def _write(self, records):
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=".task_shortcuts_", delete=False) as stream:
                temporary = stream.name
                json.dump({"version": 1, "shortcuts": records}, stream, indent=2, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except OSError as error:
            raise ShortcutError(f"Cannot save shortcuts: {error}") from error
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def save(self, value):
        normalized = validate_shortcut(value)
        with self._lock:
            records = self._read()
            identifier = value.get("id")
            if identifier:
                existing = next((item for item in records if item["id"] == identifier), None)
                if (existing is None or type(value.get("revision")) is not int
                        or value["revision"] != existing["revision"]):
                    raise ShortcutConflict("Shortcut changed or was deleted; refresh before saving")
                revision = existing["revision"] + 1
            else:
                if len(records) >= 256:
                    raise ShortcutError("At most 256 shortcuts can be saved")
                identifier, revision = str(uuid4()), 1
            record = dict(normalized, id=identifier, revision=revision)
            records = [record if item["id"] == identifier else item for item in records]
            if revision == 1:
                records.append(record)
            self._write(records)
            return {"shortcut": deepcopy(record), **self.snapshot()}

    def delete(self, identifier, revision):
        with self._lock:
            records = self._read()
            existing = next((item for item in records if item["id"] == identifier), None)
            if existing is None or type(revision) is not int or revision != existing["revision"]:
                raise ShortcutConflict("Shortcut changed or was deleted; refresh before deleting")
            self._write([item for item in records if item["id"] != identifier])
            return self.snapshot()
