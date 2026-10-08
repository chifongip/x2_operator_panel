"""Panel-owned navigation destinations. Editing never commands motion."""

from copy import deepcopy
import json
from math import isfinite
import os
from pathlib import Path
import tempfile
import threading
from uuid import uuid4


class DestinationError(ValueError):
    """Invalid destination or unavailable storage."""


class DestinationConflict(DestinationError):
    """An editor is using an outdated destination."""


def validate_destination(value):
    if not isinstance(value, dict):
        raise DestinationError("Destination must be an object")
    identifier, label, pose = value.get("id"), value.get("label"), value.get("pose")
    if (not isinstance(identifier, str) or not 1 <= len(identifier) <= 128
            or not identifier.replace("_", "").replace("-", "").isalnum()):
        raise DestinationError("Destination ID requires letters, digits, _ or - (1–128 characters)")
    if not isinstance(label, str) or not 1 <= len(label.strip()) <= 80:
        raise DestinationError("Destination name must be 1–80 characters")
    if not isinstance(pose, dict):
        raise DestinationError("Destination requires a map-frame pose")
    coordinates = {}
    for key in ("x", "y", "yaw"):
        number = pose.get(key)
        try:
            valid = type(number) in (int, float) and isfinite(number)
        except OverflowError:
            valid = False
        if not valid:
            raise DestinationError(f"Destination {key} must be finite")
        coordinates[key] = float(number)
    return {"id": identifier, "label": label.strip(), "pose": coordinates}


class NavigationDestinationStore:
    def __init__(self, path):
        self.path = Path(path).expanduser()
        if not self.path.is_absolute():
            raise DestinationError("navigation_destinations_file must be an absolute path")
        self._lock = threading.RLock()

    def _read(self):
        try:
            if not self.path.exists():
                return [], 1
            if self.path.stat().st_size > 1024 * 1024:
                raise DestinationError("Destination file is too large")
            document = json.loads(self.path.read_text(encoding="utf-8"))
            if (not isinstance(document, dict) or type(document.get("version")) is not int
                    or document["version"] != 1 or type(document.get("revision")) is not int
                    or document["revision"] < 1 or not isinstance(document.get("presets"), list)
                    or len(document["presets"]) > 256):
                raise DestinationError("Invalid destination file format or version")
            records, ids = [], set()
            for item in document["presets"]:
                record = validate_destination(item)
                revision = item.get("revision")
                if (record["id"] in ids or type(revision) is not int
                        or not 1 <= revision <= document["revision"]):
                    raise DestinationError("Invalid destination ID or revision")
                ids.add(record["id"])
                records.append(dict(record, revision=revision))
            return records, document["revision"]
        except (OSError, ValueError, TypeError) as error:
            raise DestinationError(f"Cannot read destinations: {error}") from error

    def records(self):
        with self._lock:
            return self._read()[0]

    def snapshot(self):
        with self._lock:
            try:
                records, revision = self._read()
                return {"available": True, "presets": records, "revision": revision, "detail": "Ready"}
            except DestinationError as error:
                return {"available": False, "presets": [], "detail": str(error)}

    def _write(self, records, revision):
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=".navigation_destinations_", delete=False) as stream:
                temporary = stream.name
                json.dump({"version": 1, "revision": revision, "presets": records},
                          stream, indent=2, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except OSError as error:
            raise DestinationError(f"Cannot save destinations: {error}") from error
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def save(self, value):
        if not isinstance(value, dict):
            raise DestinationError("Destination must be an object")
        identifier = value.get("id")
        creating = identifier is None or identifier == ""
        record = validate_destination(dict(value, id=str(uuid4()) if creating else identifier))
        with self._lock:
            records, generation = self._read()
            existing = next((item for item in records if item["id"] == record["id"]), None)
            revision = value.get("revision")
            if creating:
                if len(records) >= 256:
                    raise DestinationError("At most 256 destinations can be saved")
            elif (existing is None or type(revision) is not int or revision != existing["revision"]):
                raise DestinationConflict("Destination changed or was deleted; refresh before saving")
            record["revision"] = generation + 1
            if existing:
                records = [record if item["id"] == record["id"] else item for item in records]
            else:
                records.append(record)
            self._write(records, generation + 1)
            return {"preset": deepcopy(record), **self.snapshot()}

    def delete(self, identifier, revision):
        with self._lock:
            records, generation = self._read()
            existing = next((item for item in records if item["id"] == identifier), None)
            if existing is None or type(revision) is not int or revision != existing["revision"]:
                raise DestinationConflict("Destination changed or was deleted; refresh before deleting")
            self._write([item for item in records if item["id"] != identifier], generation + 1)
            return self.snapshot()
