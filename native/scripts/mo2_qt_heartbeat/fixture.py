"""Inert owned inputs and evidence helpers; uses no Qt or MO2."""
from __future__ import annotations
from collections import Counter
import hashlib
import json
from copy import deepcopy
import math
from pathlib import Path
import threading

SIZES = (40, 11207)
PERIOD_MS = 10
ACTIVE = 2
PROFILE = "Modhunter Qt Heartbeat Fixture"
CALLBACKS = {
    "onProfileChanged", "onAboutToRun", "onFinishedRun", "onUserInterfaceInitialized",
    "onModInstalled", "onModRemoved", "onModMoved", "onModStateChanged",
    "onRefreshed", "onPluginMoved", "onPluginStateChanged",
    "onDownloadComplete", "onDownloadFailed", "onDownloadPaused", "onDownloadRemoved",
}


class Organizer:
    """Python boundary substitute; all fixture paths are created beneath private root."""
    def __init__(self, root: Path, count: int):
        if count not in SIZES:
            raise ValueError("Unsupported fixture size")
        self.root, self.count, self.owner = root, count, threading.get_ident()
        self.callbacks, self.calls = {}, Counter()
        self.mods, self.plugins = {}, {}
        self.fixture_sha = hashlib.sha256()
        (root / "profile").mkdir()
        (root / "mods").mkdir()
        for i in range(count):
            mod = f"QtFixture-{i:05}"
            path = root / "mods" / mod
            path.mkdir()
            meta = f"[General]\nnotes=Owned timing fixture {i}\n".encode()
            self.write_fixture(path / "meta.ini", meta)
            if i % 6 == 0:
                directory = path / "SKSE" / "Plugins"
                directory.mkdir(parents=True)
                self.write_fixture(directory / "readme.txt", b"Package evidence only")
                if i % 18 == 0:
                    # Mark a suffix; never interpreted as a DLL or executed.
                    self.write_fixture(directory / "fixture.dll", b"INERT NOT AN EXECUTABLE")
            self.mods[mod] = Mod(mod, path, i)
            if i % 12 == 0:
                plug = f"QtFixture-{i:05}.esp"
                # Name/path in the simulated native catalog only; no plugin payload.
                self.plugins[plug] = (mod, i, str(path / plug))
        self.expected_plugins = len(self.plugins)

    def write_fixture(self, path, data):
        path.write_bytes(data)
        self.fixture_sha.update(path.relative_to(self.root).as_posix().encode() + b"\0" + hashlib.sha256(data).digest())

    def count_call(self, name):
        if threading.get_ident() != self.owner:
            raise AssertionError("Native proxy simulation called off Qt owner")
        self.calls[name] += 1

    def __getattr__(self, name):
        if name not in CALLBACKS:
            raise AttributeError(name)
        def register(fn):
            self.count_call(name)
            if name in self.callbacks:
                raise ValueError("Duplicate callback")
            self.callbacks[name] = fn
            return True
        return register

    def createNexusBridge(self): return None
    def persistent(self, _plugin, _key, default): return default
    def profileName(self): return PROFILE
    def profilePath(self): return str(self.root / "profile")
    def basePath(self): return str(self.root)
    def pluginDataPath(self): return str(self.root / "plugin-data")
    def pluginSetting(self, *_): return ""
    def managedGame(self): return self
    def gameNexusName(self): return "skyrimspecialedition"
    def gameVersion(self): return "1.5.97"
    def modList(self): return self
    def pluginList(self): return self
    def downloadManager(self): return self
    def profile(self): return self
    def allModsByProfilePriority(self, obj):
        self.count_call("allModsByProfilePriority")
        if obj is not self:
            raise AssertionError("Wrong profile")
        return self.mods
    def getMod(self, name):
        self.count_call("getMod")
        return self.mods[name]
    def priority(self, name): return self.mods[name].index
    def pluginNames(self):
        self.count_call("pluginNames")
        return self.plugins
    def state(self, name): return 1 if name in self.mods and self.mods[name].index % 4 == 3 else ACTIVE
    def loadOrder(self, name): return self.plugins[name][1] // 12
    def origin(self, name): return self.plugins[name][0]
    def resolvePath(self, name):
        self.count_call("resolvePath")
        return self.plugins[name][2]
    def isLightFlagged(self, name): return False
    def hasLightExtension(self, name): return False
    def masters(self, name): return []


class Mod:
    def __init__(self, name, path, index):
        self.name, self.absolutePath, self.index = name, str(path), index
        self.version = "1.0.0"
        self.notes = "Owned fixture"
        self.nexusId = 0


def semantic_witness(snapshot, fixture):
    """Check every ordered mod, activation and projected plugin origin; no raw paths in report."""
    rows = snapshot["items"]
    if snapshot["profileName"] != PROFILE or len(rows) != fixture.count:
        raise ValueError("Wrong profile or incomplete catalog")
    if snapshot["pluginAnalysis"]["activationSource"] != "mo2-native":
        raise ValueError("Simulated plugin states did not reach the normal capture")
    h = hashlib.sha256()
    plugins = 0
    for (name, mod), row in zip(fixture.mods.items(), rows, strict=True):
        enabled = mod.index % 4 != 3
        if (row["mo2InternalName"], row["priority"], row["enabled"], row["installedVersion"]) != (
                name, mod.index, enabled, "1.0.0"):
            raise ValueError("Missing or altered mod representation")
        if row["flags"] != (["DLL"] if mod.index % 18 == 0 else []):
            raise ValueError("Local DLL marker classification changed")
        owned = row.get("localAnalysis", {}).get("plugins", [])
        if mod.index % 12 == 0:
            if len(owned) != 1 or owned[0]["name"] != name + ".esp" or owned[0]["ownerModName"] != name:
                raise ValueError("Missing or altered plugin origin")
            plugins += 1
        elif owned:
            raise ValueError("Unexpected fixture plugin")
        h.update(f'{name}|{mod.index}|{int(enabled)}|{"DLL" in row["flags"]}\n'.encode())
    if plugins != fixture.expected_plugins:
        raise ValueError("Incomplete plugin count")
    exact = deepcopy(snapshot)
    exact.pop("generatedAt", None)
    for source in exact.get("sources", []):
        source.pop("timestamp", None)
    for row in exact["items"]:
        row.get("localAnalysis", {}).pop("checkedAt", None)
    exact_sha = hashlib.sha256(json.dumps(exact, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return {"fullCatalogExceptTimestampsSha256": exact_sha, "mods": len(rows), "pluginOrigins": plugins, "portableWitnessSha256": h.hexdigest(),
            "catalogContentSha256": snapshot["build"]["contentHash"], "profileId": snapshot["profileId"],
            "sameProcessPluginStateSha256": snapshot["pluginStateHash"]}


def interval_metrics(ticks, start_ns, end_ns):
    """Raw gap must truly overlap the active callback. Require both boundary ticks."""
    if start_ns >= end_ns or len(ticks) < 2 or any(a >= b for a, b in zip(ticks, ticks[1:])):
        raise ValueError("Invalid timestamps")
    if ticks[0] > start_ns or ticks[-1] < end_ns:
        raise ValueError("Heartbeat did not bracket the actual operation")
    spans = [round((b-a)/1e6, 3) for a,b in zip(ticks,ticks[1:]) if a < end_ns and b > start_ns]
    if not spans:
        raise ValueError("No heartbeat gap overlapped operation")
    ordered=sorted(spans)
    p=lambda q: ordered[math.ceil(q*len(ordered))-1]
    return {"callbackMs": round((end_ns-start_ns)/1e6, 3), "intersectingGapsMs": spans,
            "worstGapMs": max(spans), "p95GapMs": p(.95), "over50ms": sum(s >= 50 for s in spans),
            "over100ms": sum(s >= 100 for s in spans)}
