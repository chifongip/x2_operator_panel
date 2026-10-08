"""Box IDs are single components of box_profiles.<id>.<field> parameters."""


def is_box_profile_id(value):
    """Match the component extracted by BoxProfileRegistry without normalizing it."""
    return isinstance(value, str) and bool(value) and "." not in value
