"""MO2 download handles are zero-based; Nexus project/file IDs are not.

Never coerce a missing, boolean, fractional or malformed handle to zero: zero
identifies a real download. Keep this boundary separate from provider IDs.
"""
from __future__ import annotations
import re


def download_id(value: object) -> int | None:
    if type(value) is int:
        number = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]{1,10}", value):
        number = int(value)
    else:
        return None
    return number if 0 <= number <= 2_147_483_647 else None
