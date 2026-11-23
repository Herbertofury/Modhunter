"""Live MO2 snapshot handoff: organizer capture, then host-free projection."""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, get_ident
from time import monotonic
from typing import Any

from .model import COLORS, LocalMetadataCache, call_str, identity, inspect_local_flags, source_url
from .source_registry import discover_local_source, source_record


@dataclass(frozen=True, slots=True)
class _ModInput:
    name: str
    label: str
    priority: int
    separator: bool
    nexus_id: int = 0
    version: str = ""
    latest: str = ""
    enabled: bool = False
    path: str = field(default="", repr=False)
    notes: str = field(default="", repr=False)
    native_url: str = field(default="", repr=False)
    repository: str = ""


@dataclass(frozen=True, slots=True)
class SnapshotInput:
    """Only Python values cross the worker boundary; paths and notes stay local."""

    game: str
    runtime: str
    profile: str
    profile_id: str
    mods: tuple[_ModInput, ...] = field(repr=False)


class SnapshotCancelled(Exception):
    """Stopped work must not publish a partial snapshot."""


class SnapshotCapture:
    """Owner-thread state for a single capture. Never hand it to a worker.

    Schedule another owner-thread step only after a step returns None. Close
    the handle on the owner if that work is superseded without a next step.
    """

    def __init__(self, mod_list: Any, names: list[str], identity_parts: tuple[str, str, str, str],
                 active_flag: int, is_current: Callable[[], bool]) -> None:
        self._owner = get_ident()
        self._mod_list = mod_list
        self._names = names
        self._identity_parts = identity_parts
        self._active_flag = active_flag
        self._is_current = is_current
        self._mods: list[_ModInput] = []
        self._item_count = self._cursor = 0
        self._closed = self._stepping = False

    def _require_owner(self) -> None:
        if get_ident() != self._owner:
            raise RuntimeError("MO2 snapshot capture must stay on its owner thread")

    def close(self) -> None:
        self._require_owner()
        self._closed = True
        self._mod_list = None
        self._names = []
        self._mods = []
        self._is_current = None

    def _require_current(self) -> None:
        check = self._is_current
        if check is None or not check() or self._closed:
            raise SnapshotCancelled()

    def _capture_row(self, ml: Any, native_name: str) -> None:
        mod = ml.getMod(native_name)
        if mod is None:
            return
        name = str(native_name)
        label = call_str(mod, "name", name)
        separator = call_str(mod, "isSeparator", "False").lower() == "true"
        try:
            priority = int(ml.priority(native_name))
        except (ValueError, TypeError):
            priority = self._item_count
        if separator:
            self._mods.append(_ModInput(name, label, priority, True))
            return
        try:
            nexus_id = max(0, int(call_str(mod, "nexusId", "0")))
        except ValueError:
            nexus_id = 0
        version = call_str(mod, "version")
        latest = call_str(mod, "newestVersion")
        try:
            raw_state = ml.state(native_name)
            try:
                enabled = bool(int(raw_state) & self._active_flag)
            except (ValueError, TypeError):
                enabled = "ACTIVE" in str(raw_state).upper()
        except Exception:
            enabled = False
        self._mods.append(_ModInput(
            name, label, priority, False, nexus_id, version, latest, enabled,
            call_str(mod, "absolutePath"), call_str(mod, "notes"),
            call_str(mod, "url"), call_str(mod, "repository"),
        ))
        self._item_count += 1

    def step(self, *, max_rows: int = 64, max_seconds: float | None = 0.006) -> SnapshotInput | None:
        """Run at most max_rows lookups, or stop between rows after max_seconds.

        A single native call/row can overrun the time target; there is no
        interruptible Mobase API. None is not a publishable partial snapshot.
        """
        self._require_owner()
        if self._stepping:
            raise RuntimeError("MO2 snapshot capture cannot step reentrantly")
        if type(max_rows) is not int or max_rows < 1:
            raise ValueError("max_rows must be a positive integer")
        if max_seconds is not None and (not math.isfinite(max_seconds) or max_seconds <= 0):
            raise ValueError("max_seconds must be positive, finite or None")
        self._stepping = True
        try:
            self._require_current()
            deadline = monotonic() + max_seconds if max_seconds is not None else None
            stop = min(len(self._names), self._cursor + max_rows)
            while self._cursor < stop:
                self._capture_row(self._mod_list, self._names[self._cursor])
                self._cursor += 1
                if self._closed or (deadline is not None and monotonic() >= deadline):
                    break
            if self._cursor == len(self._names):
                result = SnapshotInput(*self._identity_parts, tuple(self._mods))
                self._require_current()
                self.close()
                return result
            self._require_current()
            return None
        except Exception:
            self.close()
            raise
        finally:
            self._stepping = False


def begin_snapshot_capture(organizer: Any, target_runtime: str = "unknown", *,
                           is_current: Callable[[], bool]) -> SnapshotCapture:
    """Bind profile and detach the name iterable once on the organizer thread.

    The native name enumeration/copy (and legacy priority sort) is one
    synchronous operation. Only subsequent per-row calls are sliced.
    is_current should be a cheap owner-thread check of the original request
    generation, not just the visible profile name (A to B to A is stale).
    """
    if not is_current():
        raise SnapshotCancelled()
    game_obj = organizer.managedGame()
    game = call_str(game_obj, "gameNexusName") or call_str(game_obj, "gameShortName", "skyrimspecialedition")
    game = game.lower().replace(" ", "")
    if not game.replace("-", "").replace("_", "").isalnum():
        game = "unknown"
    runtime = (str(target_runtime) if target_runtime and target_runtime != "unknown"
               else call_str(game_obj, "gameVersion", "unknown"))
    if game == "skyrimspecialedition" and runtime == "1.5.97.0":
        runtime = "1.5.97"
    profile = call_str(organizer, "profileName", "Default")
    profile_id = identity(call_str(organizer, "basePath") + "|" + game + "|" + profile)
    ml = organizer.modList()
    try:
        names = list(ml.allModsByProfilePriority(organizer.profile()))
    except (AttributeError, TypeError):
        names = list(ml.allMods())
        names.sort(key=lambda n: ml.priority(n))

    try:
        import mobase
        active_flag = int(mobase.ModState.ACTIVE)
    except (ImportError, AttributeError, TypeError, ValueError):
        active_flag = 0x2
    if not is_current():
        raise SnapshotCancelled()
    return SnapshotCapture(ml, names, (game, runtime, profile, profile_id), active_flag, is_current)


def capture_snapshot(organizer: Any, target_runtime: str = "unknown") -> SnapshotInput:
    """Synchronously capture all rows on the organizer thread for legacy use."""
    capture = begin_snapshot_capture(organizer, target_runtime, is_current=lambda: True)
    result = capture.step(max_rows=max(1, len(capture._names)), max_seconds=None)
    assert result is not None  # No deadline and all existing names fit.
    return result


def project_snapshot(captured: SnapshotInput, *,
                     metadata_cache: LocalMetadataCache | None = None,
                     force_metadata: bool = False,
                     cancel_event: Event | None = None) -> dict[str, Any]:
    """Read local metadata and build a complete catalog without importing mobase.

    A supplied cache belongs to one serialized projection worker. Cancellation
    can leave individually valid observations cached but never prunes the
    cache using a partial inventory. The caller must independently fence result
    publication against the active profile, generation and request identity.
    """
    def check_cancelled() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise SnapshotCancelled()

    groups: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    group: dict[str, Any] | None = None
    present: set[str] = set()
    active_paths: set[str] = set()
    for mod in captured.mods:
        check_cancelled()
        if mod.separator:
            group = {"id": "g-" + identity(captured.profile_id + "|" + mod.name),
                     "name": mod.label, "startPriority": mod.priority,
                     "color": COLORS[len(groups) % len(COLORS)], "count": 0}
            groups.append(group)
            continue
        if group is None:
            group = {"id": "g-" + identity(captured.profile_id + "|UNSORTED"),
                     "name": "Unsorted / Essentials", "startPriority": -1, "color": "gold", "count": 0}
            groups.append(group)
        group["count"] += 1
        if mod.path and metadata_cache is not None:
            active_paths.add(mod.path)
            flags, detected_source = metadata_cache.inspect(Path(mod.path), mod.notes, force=force_metadata)
        else:
            flags = inspect_local_flags(Path(mod.path)) if mod.path else []
            detected_source = discover_local_source(Path(mod.path), mod.notes) if mod.path else {}
        check_cancelled()
        custom_enabled = detected_source.pop("customUrlEnabled", None)
        native_url = source_record(mod.native_url if custom_enabled is not False else "", "MO2 official mod URL")
        nexus_source = source_url(captured.game, mod.nexus_id)
        actual_source = (native_url if native_url.get("verified") else detected_source if detected_source.get("verified")
                         else nexus_source if nexus_source.get("verified") else detected_source if detected_source.get("url")
                         else nexus_source)
        hint = "Contains native DLL files. Select a build for the exact game runtime." if flags else ""
        mod_id = "live-" + identity(captured.profile_id + "|" + mod.name)
        if mod_id in present:
            raise ValueError("Duplicate MO2 mod ID: " + mod_id)
        present.add(mod_id)
        items.append({
            "id": mod_id,
            "mo2InternalName": mod.name,
            "priority": mod.priority,
            "name": mod.label,
            "installedVersion": mod.version if mod.version not in ("Invalid", "None", "0", "0.0", "") else "?",
            "latestKnownVersion": mod.latest if mod.latest not in ("Invalid", "None", "0", "0.0", "") else "",
            "enabled": mod.enabled,
            "nexusId": mod.nexus_id,
            "screenshotNote": mod.notes,
            "author": "",
            "flags": flags,
            "groupId": group["id"],
            "group": group["name"],
            "audit": {"verdict": "check", "note": hint, "evidence": "MO2 local metadata only"} if flags else {},
            "source": actual_source,
            "repository": mod.repository,
            "screenshot": 0,
        })
    verified = sum(bool(i["source"]["verified"]) for i in items)
    content_hash = hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()
    check_cancelled()
    if metadata_cache is not None:
        metadata_cache.retain(active_paths)
    stamp = datetime.now(timezone.utc).isoformat()
    return {
        "schemaVersion": 2,
        "catalogId": "mo2-live-" + captured.profile_id,
        "title": f"{captured.profile} - MO2 Live Load Order",
        "generatedAt": stamp,
        "game": captured.game,
        "profileName": captured.profile,
        "profileId": captured.profile_id,
        "gameRuntime": captured.runtime,
        "skseRuntime": "2.0.20" if captured.runtime == "1.5.97" and captured.game == "skyrimspecialedition" else "",
        "groups": groups,
        "items": items,
        "build": {"count": len(items), "groups": len(groups), "verifiedLinks": verified,
                  "unresolvedLinks": len(items) - verified, "contentHash": content_hash},
        "sources": [{"kind": "live-mobase", "name": "Mod Organizer 2", "timestamp": stamp}],
    }
