"""Discover immutable physical tables and match docking identities."""

from math import isfinite

from x2_operator_panel.docking_profiles import DockingProfileMonitor


class TableProfileMonitor(DockingProfileMonitor):
    catalog_label = "Table"
    names_parameter = "table_profile_names"
    default_parameter = "default_table_profile"
    profile_prefix = "table_profiles"
    profile_fields = (
        "tag_id", "tag_frame", "tabletop_center", "dimensions",
        "place_offset", "place_yaw", "collision_id",
    )
    legacy_fields = {
        "tag_id": "table_tag_id", "tag_frame": "table_tag_frame",
        "tabletop_center": "table_tag_to_tabletop_center", "dimensions": "table_dimensions",
        "place_offset": "table_tag_place_offset", "place_yaw": "table_tag_to_box_yaw",
        "collision_id": "table_collision_id",
    }

    def parameter_name(self, name, field):
        return self.legacy_fields[field] if name == "default" else f"{self.profile_prefix}.{name}.{field}"

    def validate_profile(self, profile):
        valid = (type(profile["tag_id"]) is int and profile["tag_id"] >= 0
                 and isinstance(profile["tag_frame"], str) and bool(profile["tag_frame"])
                 and isinstance(profile["collision_id"], str) and bool(profile["collision_id"])
                 and type(profile["place_yaw"]) is float and isfinite(profile["place_yaw"]))
        for field, size in (("tabletop_center", 3), ("dimensions", 3), ("place_offset", 2)):
            values = profile[field]
            valid = valid and isinstance(values, list) and len(values) == size and all(
                type(value) is float and isfinite(value) for value in values
            )
        if (not valid or profile["tabletop_center"][1] > 0
                or any(value <= 0 for value in profile["dimensions"])):
            raise ValueError(f"Invalid table profile: {profile['id']}")

    def _store_profiles(self, names, default, values):
        # Validate uniqueness before the shared monitor publishes the catalog.
        identities, collision_ids = set(), set()
        for index in range(len(names)):
            fields = values[index * len(self.profile_fields):(index + 1) * len(self.profile_fields)]
            profile = dict(zip(self.profile_fields, fields), id=names[index])
            self.validate_profile(profile)
            identity = (profile["tag_id"], profile["tag_frame"])
            if identity in identities or profile["collision_id"] in collision_ids:
                raise ValueError("Duplicate table identity or collision ID")
            identities.add(identity)
            collision_ids.add(profile["collision_id"])
        super()._store_profiles(names, default, values)


def matching_table(docking, tables):
    """Require one physical table with exactly the same detection identity."""
    if not tables.get("available"):
        raise ValueError("Table profile configuration is unavailable")
    matches = [table for table in tables["profiles"]
               if (table["tag_id"], table["tag_frame"]) == (docking["tag_id"], docking["tag_frame"])]
    if len(matches) != 1:
        raise ValueError("Docking profile must match exactly one table tag ID and frame")
    return matches[0]
