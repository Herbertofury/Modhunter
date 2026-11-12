"""Normalized read-only MO2 profiles. No changes to MO2 or game files."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COLORS = ("mint", "violet", "sky", "pink", "ochre", "green", "slate", "rose", "gold", "cyan", "blue")


def identity(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def call_str(obj: Any, method: str, fallback: str = "") -> str:
    try:
        func = getattr(obj, method)
        out = func() if callable(func) else func
        return str(out) if out is not None else fallback
    except (AttributeError, ValueError, TypeError, RuntimeError):
        return fallback


def source_url(game: str, mod_id: int) -> dict[str, Any]:
    if mod_id > 0 and game and game.replace("-", "").isalnum():
        return {"type": "nexus", "verified": True, "kind": "project", "label": "Nexus / Files", "url": f"https://www.nexusmods.com/{game}/mods/{mod_id}?tab=files"}
    return {"type": "unknown", "verified": False, "kind": "unresolved", "label": "Source unlinked", "url": ""}


def inspect_local_flags(mod_directory: Path) -> list[str]:
    """Lightweight direct checks only; never recursively walk huge mod content."""
    flags: list[str] = []
    try:
        for key in ("SKSE/Plugins", "skse/plugins"):
            plugins = mod_directory / key
            if plugins.is_dir() and any(x.suffix.lower() == ".dll" for x in plugins.iterdir() if x.is_file()):
                flags.append("DLL")
                break
    except (OSError, PermissionError):
        pass
    return flags


def take_snapshot(organizer: Any, target_runtime: str = "unknown") -> dict[str, Any]:
    """Call only from the main MO2 Qt thread. Return JSON-safe immutable primitives."""
    game_obj = organizer.managedGame()
    game = call_str(game_obj, "gameNexusName") or call_str(game_obj, "gameShortName", "skyrimspecialedition")
    game = game.lower().replace(" ", "")
    if not game.replace("-", "").replace("_", "").isalnum():
        game = "unknown"  # Invalid destinations remain unlinked rather than guessed.
    if not target_runtime or target_runtime == "unknown":
        target_runtime = call_str(game_obj, "gameVersion", "unknown")
    # Windows exe file versions are sometimes reported with a fourth .0 component.
    if game == "skyrimspecialedition" and target_runtime == "1.5.97.0":
        target_runtime = "1.5.97"
    profile = call_str(organizer, "profileName", "Default")
    base = call_str(organizer, "basePath")
    profile_id = identity(base + "|" + game + "|" + profile)
    ml = organizer.modList()
    try:
        names = list(ml.allModsByProfilePriority(organizer.profile()))
    except (AttributeError, TypeError):
        names = list(ml.allMods())
        names.sort(key=lambda n: ml.priority(n))
    groups: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    group: dict[str, Any] | None = None
    present: set[str] = set()
    for name in names:
        mod = ml.getMod(name)
        if mod is None:
            continue
        label = call_str(mod, "name", name)
        sep = call_str(mod, "isSeparator", "False").lower() == "true"
        try:
            priority = int(ml.priority(name))
        except (ValueError, TypeError):
            priority = len(items)
        if sep:
            group = {"id": "g-" + identity(profile_id + "|" + name), "name": label, "startPriority": priority, "color": COLORS[len(groups) % len(COLORS)], "count": 0}
            groups.append(group)
            continue
        if group is None:
            group = {"id": "g-" + identity(profile_id + "|UNSORTED"), "name": "Unsorted / Essentials", "startPriority": -1, "color": "gold", "count": 0}
            groups.append(group)
        group["count"] += 1
        raw_id = call_str(mod, "nexusId", "0")
        try:
            nexus_id = max(0, int(raw_id))
        except ValueError:
            nexus_id = 0
        version = call_str(mod, "version")
        latest = call_str(mod, "newestVersion")
        if version in ("Invalid", "None", "0", "0.0", ""):
            version = "?"
        if latest in ("Invalid", "None", "0", "0.0", ""):
            latest = ""
        try:
            raw_state = ml.state(name)
            try:
                import mobase  # Native ModState enum provides the authoritative ACTIVE bit.
                active_flag = int(mobase.ModState.ACTIVE)
            except (ImportError, AttributeError, TypeError, ValueError):
                active_flag = 0x2  # Fallback for isolated fixtures; native install uses the enum.
            try:
                enabled = bool(int(raw_state) & active_flag)
            except (ValueError, TypeError):
                enabled = "ACTIVE" in str(raw_state).upper()
        except Exception:
            enabled = False
        flags = inspect_local_flags(Path(call_str(mod, "absolutePath", ""))) if call_str(mod, "absolutePath") else []
        hint = "Contains native DLL files. Select a build for the exact game runtime." if flags else ""
        mod_id = "live-" + identity(profile_id + "|" + name)
        if mod_id in present:
            raise ValueError("Duplicate MO2 mod ID: " + mod_id)
        present.add(mod_id)
        items.append({
            "id": mod_id,
            "mo2InternalName": name,
            "priority": priority,
            "name": label,
            "installedVersion": version,
            "latestKnownVersion": latest,
            "enabled": enabled,
            "nexusId": nexus_id,
            "screenshotNote": call_str(mod, "notes"),
            "author": "",
            "flags": flags,
            "groupId": group["id"],
            "group": group["name"],
            "audit": {"verdict": "check", "note": hint, "evidence": "MO2 local metadata only"} if flags else {},
            "source": source_url(game, nexus_id),
            "repository": call_str(mod, "repository"),
            "screenshot": 0,
        })
    stamp = datetime.now(timezone.utc).isoformat()
    return {
        "schemaVersion": 2,
        "catalogId": "mo2-live-" + profile_id,
        "title": f"{profile} - MO2 Live Load Order",
        "generatedAt": stamp,
        "game": game,
        "profileName": profile,
        "profileId": profile_id,
        "gameRuntime": target_runtime,
        "skseRuntime": "2.0.20" if target_runtime == "1.5.97" and game == "skyrimspecialedition" else "",
        "groups": groups,
        "items": items,
        "build": {"count": len(items), "groups": len(groups), "verifiedLinks": sum(bool(i["source"]["verified"]) for i in items), "unresolvedLinks": sum(not bool(i["source"]["verified"]) for i in items), "contentHash": hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()},
        "sources": [{"kind": "live-mobase", "name": "Mod Organizer 2", "timestamp": stamp}],
    }


def apply_scan_results(snapshot: dict, records: dict) -> dict:
    """Annotate an existing snapshot without altering installed-version metadata."""
    out = json.loads(json.dumps(snapshot))
    for item in out.get("items", []):
        key = str(item.get("nexusId") or 0)
        result = records.get(key)
        if result:
            item["update"] = result
    return out
