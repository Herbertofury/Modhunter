"""Bounded full-field version comparisons; never extract versions from page prose.

Inspired by GameSync's multiple version strategies, independently implemented
for the MO2 updater. Dates, explicit build numbers and prerelease labels remain
separate families. A comparison is metadata evidence, never install permission.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
import re

MAX_VERSION_LENGTH = 96
_RELEASE = re.compile(r"v?([0-9]{1,10}(?:\.[0-9]{1,10}){0,5})", re.I)
_SEMVER = re.compile(r"v?([0-9]{1,10}\.[0-9]{1,10}\.[0-9]{1,10})(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?", re.I)
_SHORT_PRE = re.compile(r"v?([0-9]{1,10}\.[0-9]{1,10}(?:\.[0-9]{1,10})?)[ .-]?(alpha|beta|rc|a|b)[ .-]?([0-9]{1,10})", re.I)
_DATE = re.compile(r"(20[0-9]{2})([-.])([0-9]{1,2})\2([0-9]{1,2})")
_BUILD = re.compile(r"(?:build|revision|rev)[ :#-]*([0-9]{1,12})", re.I)
_CHANNELS = {"alpha", "beta", "rc", "pre", "preview", "dev", "nightly"}


@dataclass(frozen=True)
class Version:
    family: str
    numbers: tuple[int, ...]
    prerelease: tuple[str, ...] = ()
    metadata: str = ""


def parse_version(value: object) -> Version | None:
    if not isinstance(value, str) or not value or len(value) > MAX_VERSION_LENGTH:
        return None
    value = value.strip()
    match = _DATE.fullmatch(value)
    if match:
        parts = int(match[1]), int(match[3]), int(match[4])
        try:
            date(*parts)
        except ValueError:
            return None
        return Version("calendar-date", parts)
    # A date-shaped invalid value must not fall through to a numeric release.
    if re.match(r"20[0-9]{2}[-.][0-9]+[-.]", value):
        return None
    match = _BUILD.fullmatch(value)
    if match:
        return Version("build", (int(match[1]),))
    match = _SHORT_PRE.fullmatch(value)
    if match:
        if len(match[3]) > 1 and match[3][0] == "0":
            return None
        channel = {"a": "alpha", "b": "beta"}.get(match[2].lower(), match[2].lower())
        return Version("release", tuple(map(int, match[1].split("."))), (channel, str(int(match[3]))))
    match = _RELEASE.fullmatch(value)
    if match:
        return Version("release", tuple(map(int, match[1].split("."))))
    match = _SEMVER.fullmatch(value)
    if not match:
        return None
    pre = tuple((match[2] or "").split(".")) if match[2] else ()
    if any(len(part) > 24 or (part.isdecimal() and len(part) > 1 and part[0] == "0") for part in pre):
        return None
    return Version("release", tuple(map(int, match[1].split("."))), pre, match[3] or "")


def _numbers(a: tuple[int, ...], b: tuple[int, ...]) -> int:
    width = max(len(a), len(b))
    left, right = a + (0,) * (width - len(a)), b + (0,) * (width - len(b))
    return (left > right) - (left < right)


def _prerelease(a: tuple[str, ...], b: tuple[str, ...]) -> int:
    if not a or not b:
        return int(not a) - int(not b)
    for left, right in zip(a, b):
        if left == right:
            continue
        if left.isdecimal() and right.isdecimal():
            return (int(left) > int(right)) - (int(left) < int(right))
        if left.isdecimal() != right.isdecimal():
            return -1 if left.isdecimal() else 1
        return (left > right) - (left < right)
    return (len(a) > len(b)) - (len(a) < len(b))


def compare_versions(installed: object, latest: object) -> dict[str, str]:
    """Return an explainable, conservative project-metadata verdict.

    A newer unstable release is not an automatic candidate for a stable install.
    Existing prerelease installs can compare within their named channel. Unknown
    or mixed strategies stay review, rather than an invented current/no-update.
    """
    if latest is None or latest == "" or (isinstance(latest, str) and not latest.strip()):
        return {"verdict": "unknown", "strategy": "unknown", "reason": "No available version was reported."}
    left, right = parse_version(installed), parse_version(latest)
    if left is None or right is None:
        return {"verdict": "review", "strategy": "unknown", "reason": "Version labels are missing or ambiguous; review the exact release and file."}
    if left.family != right.family:
        return {"verdict": "review", "strategy": "mixed", "reason": "Installed and available versions use different numbering systems."}
    result = _numbers(right.numbers, left.numbers)
    strategy = left.family
    if strategy == "release":
        if left.prerelease and left.prerelease[0] not in _CHANNELS:
            return {"verdict": "review", "strategy": "prerelease-channel", "reason": "The installed prerelease channel is not recognized; review its provenance before replacing it."}
        if right.prerelease:
            channel = right.prerelease[0]
            if not left.prerelease or channel not in _CHANNELS or left.prerelease[0] != channel:
                return {"verdict": "review", "strategy": "prerelease-channel", "reason": "The available prerelease is outside the installed release channel; choose it explicitly."}
        if result == 0:
            result = _prerelease(right.prerelease, left.prerelease)
            if result == 0 and left.metadata != right.metadata:
                return {"verdict": "review", "strategy": "build-metadata", "reason": "Equal version precedence but different build metadata; inspect the exact file."}
        if left.prerelease or right.prerelease:
            strategy = "prerelease"
    verdict = "candidate" if result > 0 else "current" if result == 0 else "newer-installed"
    return {"verdict": verdict, "strategy": strategy,
            "reason": "Compared complete " + strategy + " labels; project versions alone do not verify file choice or Skyrim compatibility."}
