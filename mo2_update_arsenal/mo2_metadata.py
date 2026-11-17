"""Small read-only MO2 metadata fallbacks, never recursive mod-content scans."""
from __future__ import annotations

import configparser
import re
import struct
from pathlib import Path

from .nexus_files import archive_name, positive_id

META_LIMIT = 256_000


def qsettings_color(value: str) -> str | None:
    """Decode Qt's QSettings Qt_4_0 QVariant<QColor> RGB representation.

    QColor's type is 67; QDataStream writes spec, alpha, red, green, blue,
    padding as one byte plus five big-endian 16-bit values. Unknown color
    spaces stay absent rather than inventing a palette approximation.
    """
    raw = str(value or "").strip().strip('"')
    if not raw.startswith("@Variant(") or not raw.endswith(")"):
        return None
    data, index, raw = bytearray(), 0, raw[9:-1]
    escapes = {"0": 0, "a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9,
               "v": 11, "\\": 92, '"': 34, "'": 39, "?": 63}
    while index < len(raw):
        char = raw[index]
        index += 1
        if char == "\\":
            if index >= len(raw):
                return None
            char = raw[index]
            index += 1
            if char == "x":
                match = re.match(r"[0-9a-fA-F]{1,4}", raw[index:])
                if not match:
                    return None
                number = int(match[0], 16)
                index += len(match[0])
            elif char in escapes:
                number = escapes[char]
            else:
                return None
        else:
            number = ord(char)
        if number > 255 or len(data) >= 15:
            return None
        data.append(number)
    if len(data) != 15 or data[:5] != b"\0\0\0\x43\x01":
        return None
    alpha, red, green, blue, _ = struct.unpack(">5H", data[5:])
    return "#" + "".join(f"{(value + 128) // 257:02x}" for value in (red, green, blue, alpha))


def read_metadata(path: Path) -> dict:
    try:
        meta = path / "meta.ini"
        if meta.is_symlink() or meta.stat().st_size > META_LIMIT:
            return {}
        with meta.open("rb") as stream:
            raw = stream.read(META_LIMIT + 1)
        if len(raw) > META_LIMIT:
            return {}
        text = raw.decode("utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig")
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.read_string(text)
        general = next((parser[section] for section in parser.sections() if section.casefold() == "general"), {})
        installed = next((parser[section] for section in parser.sections() if section.casefold() == "installedfiles"), {})
        files = []
        for key, value in installed.items():
            if match := re.fullmatch(r"([1-9][0-9]*)\\modid", key):
                mid, fid = positive_id(value), positive_id(installed.get(match[1] + "\\fileid"))
                if mid and fid:
                    files.append({"modId": mid, "fileId": fid})
        return {"installedFiles": files, "installationFile": archive_name(general.get("installationfile", "").strip('"')),
                "ignoredVersion": general.get("ignoredversion", "").strip('"'),
                "separatorColor": qsettings_color(general.get("color", ""))}
    except (OSError, UnicodeError, ValueError, configparser.Error):
        return {}


def native_files(mod) -> list[dict] | None:
    try:
        values = mod.installedFiles()
        result = []
        for value in values:
            mid, fid = value
            mid, fid = positive_id(mid), positive_id(fid)
            if mid and fid:
                result.append({"modId": mid, "fileId": fid})
        return sorted(result, key=lambda row: (row["modId"], row["fileId"]))
    except (AttributeError, TypeError, ValueError, RuntimeError):
        return None


def native_color(mod) -> tuple[bool, str | None]:
    try:
        color = mod.color()
        if not color.isValid():
            return True, None
        values = tuple(color.getRgb())
        if len(values) == 4 and all(type(value) is int and 0 <= value <= 255 for value in values):
            return True, "#" + "".join(f"{value:02x}" for value in values)
    except (AttributeError, TypeError, ValueError, RuntimeError):
        pass
    return False, None
