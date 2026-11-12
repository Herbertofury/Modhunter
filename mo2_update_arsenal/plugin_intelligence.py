"""Qt-owned MO2 facts and an isolated, read-only Rust plugin analyzer.

Only ``native_plugin_facts`` / ``build_audit_request`` touch mobase. Call them
on MO2's UI thread, then pass primitive owned data to background workers.
Neither browser parameters nor ESLifier JSON may choose an executable to run.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time


def _value(obj, method, *args, default=None):
    try:
        return getattr(obj, method)(*args)
    except (AttributeError, RuntimeError, TypeError, ValueError, OSError):
        return default


def discover_eslifier_paths(organizer, game: str, configured=()) -> list[str]:
    """Use ESLifier's documented MO2 setting, never guess a data directory."""
    paths = [str(p) for p in configured if p]
    folder = _value(organizer, "pluginSetting", "ESLifier", "ESLifier Folder", default="")
    if (isinstance(folder, str) and folder.strip()
            and game.lower() in ("skyrimspecialedition", "enderalspecialedition", "skyrimvr", "fallout4", "fallout4vr")):
        suffix = "ESLifier_FO4_Data" if game.lower() in ("fallout4", "fallout4vr") else "ESLifier_Data"
        paths.append(str(Path(folder) / suffix))
    return list(dict.fromkeys(paths))[:4]


def native_plugin_facts(organizer, snapshot: dict) -> tuple[list[dict] | None, dict, list[str]]:
    """Read actual winning origins and activation without walking plugin bytes."""
    plugin_list = _value(organizer, "pluginList")
    names = _value(plugin_list, "pluginNames")
    if names is None:
        return None, {}, ["MO2's native plugin inventory is unavailable."]
    owner_ids = {str(row.get("mo2InternalName")): str(row["id"]) for row in snapshot.get("items", [])}
    owner_ids.update({"overwrite": "__overwrite__", "data": "__game_data__"})
    native, rows, warnings = [], defaultdict(list), []
    checked = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    all_names = list(names)
    if len(all_names) > 20_000:
        return None, {}, ["Native plugin inventory exceeds the supported 20,000-plugin bound."]
    complete = True
    states = {}
    for name in all_names:
        name = str(name)
        state = _value(plugin_list, "state", name)
        try:
            state = int(getattr(state, "value", state))
        except (ValueError, TypeError):
            state = None
        order = _value(plugin_list, "loadOrder", name)
        try:
            order = int(order) if order is not None and int(order) >= 0 else None
        except (ValueError, TypeError):
            order = None
        origin = str(_value(plugin_list, "origin", name, default="") or "")
        resolved = str(_value(organizer, "resolvePath", name, default="") or "")
        active = state == 2 if state in (0, 1, 2) else None
        if active is None:
            complete = False
        else:
            native.append({"name": name, "active": active, "loadOrder": order,
                           "origin": origin or None, "path": resolved or None})
        flagged = _value(plugin_list, "isLightFlagged", name)
        extension = _value(plugin_list, "hasLightExtension", name)
        light = bool(flagged or extension) if flagged is not None and extension is not None else None
        masters = _value(plugin_list, "masters", name, default=None)
        master_names = [str(m) for m in masters] if masters is not None else []
        owner = owner_ids.get(origin, "")
        copy = {"name": name, "ownerModId": owner, "ownerModName": origin,
                "winner": bool(owner), "winnerKnown": bool(owner), "winnerBy": "mo2-native",
                "winnerModId": owner or None, "winnerLight": light, "lightOverride": False,
                "active": active, "loadOrder": order, "enabled": active is not False,
                "missing": state == 0,
                "header": {"lightFlag": flagged, "lightByExtension": extension,
                           "lightEffective": light, "masters": master_names},
                "eligibility": {"status": "already-light" if light else "unknown",
                    "reasonCodes": ["native-state-only"], "saveSafety": "not-assessed",
                    "notes": ["MO2 reports the winning plugin state. Run Plugin audit to examine records and conversion history."]},
                "runtimeSupport": "unknown", "records": None, "fingerprint": None,
                "provenance": {"state": "unknown", "status": "unknown"}, "masterIssues": [],
                "errors": []}
        states[name.casefold()] = copy
        if owner:
            rows[owner].append(copy)
    for plugin in states.values():
        if plugin["active"] is not True:
            continue
        for master in plugin["header"]["masters"]:
            found = states.get(master.casefold())
            issue = "missing" if found is None or found.get("missing") else "inactive" if found["active"] is False else (
                "loads-after" if found["active"] is True and found["loadOrder"] is not None
                and plugin["loadOrder"] is not None and found["loadOrder"] >= plugin["loadOrder"] else None)
            if issue:
                plugin["masterIssues"].append({"name": master, "status": issue})
    if not complete:
        warnings.append("Some native plugin activation states were unavailable; file-based activation needs review.")
    result = {owner: {"status": "native-headers", "depth": "native", "checkedAt": checked,
                     "plugins": copies, "warnings": [], "inventoryComplete": False}
              for owner, copies in rows.items()}
    return native if complete else None, result, warnings


def attach_native_plugin_state(organizer, snapshot: dict) -> dict:
    states, rows, warnings = native_plugin_facts(organizer, snapshot)
    for item in snapshot.get("items", []):
        if str(item["id"]) in rows:
            item["localAnalysis"] = rows[str(item["id"])]
    # No claim that a mod without a winning file has no plugin: it may be overridden.
    snapshot["pluginAnalysis"] = {"schemaVersion": 1, "depth": "native", "warnings": warnings,
        "inventoryComplete": False, "activationSource": "mo2-native" if states is not None else "unknown",
        "eslifierConfigured": bool(discover_eslifier_paths(organizer, str(snapshot.get("game", ""))))}
    stable = [{"id": owner, "plugins": data["plugins"]} for owner, data in sorted(rows.items())]
    snapshot["pluginStateHash"] = hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()
    return snapshot


def build_audit_request(organizer, snapshot: dict, *, deep=False, configured=()) -> dict:
    """Capture owned paths on Qt, including MO2's real winning physical paths."""
    if str(_value(organizer, "profileName", default="")) != str(snapshot.get("profileName") or ""):
        raise ValueError("Active MO2 profile changed before plugin analysis; refresh and retry")
    mod_list = organizer.modList()
    mods, warnings = [], []
    for row in snapshot.get("items", []):
        internal = row.get("mo2InternalName")
        if not internal:
            continue
        mod = _value(mod_list, "getMod", internal)
        path = _value(mod, "absolutePath", default="")
        if not path:
            if row.get("enabled"):
                raise ValueError("Enabled mod folder unavailable during capture: " + str(row.get("name") or internal))
            warnings.append("Mod folder unavailable: " + str(row.get("name") or internal))
            continue
        mods.append({"id": str(row["id"]), "name": str(internal), "path": str(path),
                     "enabled": bool(row.get("enabled")), "priority": int(row.get("priority") or 0),
                     "installedVersion": str(row.get("installedVersion") or "")})
    states, _, native_warnings = native_plugin_facts(organizer, snapshot)
    profile_path = str(_value(organizer, "profilePath", default="") or "")
    game_dir = _value(_value(organizer, "managedGame"), "dataDirectory")
    game_data = _value(game_dir, "absolutePath", default=None)
    result = {"game": str(snapshot.get("game") or "unknown"),
              "gameRuntime": str(snapshot.get("gameRuntime") or "unknown"),
              "profileId": snapshot.get("profileId"), "profileName": snapshot.get("profileName"),
              "mods": mods, "pluginStates": states,
              "pluginsPath": str(Path(profile_path) / "plugins.txt") if profile_path else None,
              "loadorderPath": str(Path(profile_path) / "loadorder.txt") if profile_path else None,
              "overwritePath": _value(organizer, "overwritePath", default=None) or None,
              "gameDataPath": str(game_data) if game_data else None,
              "eslifierDataPaths": discover_eslifier_paths(organizer, str(snapshot.get("game") or ""), configured),
              "deep": bool(deep), "nativeWarnings": warnings + native_warnings}
    return result


class RustAuditRunner:
    """Run only the installed matching helper; reads/writes use bounded temporary IO."""
    def __init__(self, executable: Path | None = None, *, timeout=180):
        self.executable = executable or Path(__file__).parent / "bin" / ("modhunter-cli.exe" if os.name == "nt" else "modhunter-cli")
        self.timeout = timeout

    @property
    def available(self):
        return self.executable.is_file()

    def run_cancellable(self, request: dict, cancel_event: threading.Event) -> dict:
        return self.run(request, cancel_event=cancel_event)

    def run(self, request: dict, *, cancel_event=None) -> dict:
        if not self.available:
            raise RuntimeError("Rust plugin analyzer is not installed. Install the matching Modhunter MO2 package; native MO2 plugin states remain available.")
        payload = json.dumps(request, ensure_ascii=False).encode("utf-8")
        if len(payload) > 32_000_000:
            raise ValueError("Plugin inventory exceeds the analyzer request bound")
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("Plugin analysis was superseded before it started")
        # File-backed stdin cannot block Qt/background shutdown when a helper
        # stops reading. Dedicated readers enforce bounds while it is running.
        with tempfile.TemporaryFile() as incoming:
            incoming.write(payload)
            incoming.seek(0)
            process = subprocess.Popen([str(self.executable), "audit-plugins", "--request", "-"],
                stdin=incoming, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            output, errors = bytearray(), bytearray()
            exceeded = threading.Event()
            def collect(stream, target, limit):
                try:
                    while True:
                        chunk = stream.read1(min(65_536, limit - len(target) + 1))
                        if not chunk:
                            break
                        if len(target) + len(chunk) > limit:
                            exceeded.set()
                            process.kill()
                            break
                        target.extend(chunk)
                except (OSError, ValueError):
                    exceeded.set()
                finally:
                    stream.close()
            readers = [threading.Thread(target=collect, args=(process.stdout, output, 32_000_000), daemon=True),
                       threading.Thread(target=collect, args=(process.stderr, errors, 65_536), daemon=True)]
            for reader in readers:
                reader.start()
            timed_out = False
            cancelled = False
            deadline = time.monotonic() + self.timeout
            try:
                while True:
                    cancelled = cancel_event is not None and cancel_event.is_set()
                    remaining = deadline - time.monotonic()
                    if cancelled or remaining <= 0:
                        timed_out = not cancelled
                        process.kill()
                        process.wait(timeout=5)
                        break
                    try:
                        process.wait(timeout=min(0.2, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                for reader in readers:
                    reader.join(timeout=2)
            if timed_out:
                raise RuntimeError("Plugin analysis exceeded its time limit. No mod files were changed; narrow the profile and retry.")
            if cancelled:
                raise RuntimeError("Plugin analysis was superseded by a profile or path change")
            if exceeded.is_set() or any(reader.is_alive() for reader in readers):
                raise RuntimeError("Plugin analysis response exceeds its supported bound or its output stream did not close")
            raw = bytes(output)
            detail = bytes(errors[:4096]).decode("utf-8", errors="replace").strip()
            if process.returncode:
                raise RuntimeError("Rust plugin analysis failed: " + (detail or f"exit {process.returncode}"))
            if len(raw) > 32_000_000:
                raise RuntimeError("Plugin analysis response exceeds its supported bound")
            result = json.loads(raw)
        if not isinstance(result, dict) or result.get("schemaVersion") != 1 or not isinstance(result.get("mods"), dict):
            raise RuntimeError("Rust plugin analysis returned an unsupported schema")
        if result.get("profileId") != request.get("profileId"):
            raise RuntimeError("Rust plugin analysis returned a different profile")
        result.setdefault("warnings", []).extend(request.get("nativeWarnings", []))
        return result
