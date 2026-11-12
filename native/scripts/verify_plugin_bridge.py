#!/usr/bin/env python3
"""Exercise the production Python-to-Rust plugin-analysis JSON boundary.

This is a disposable synthetic-organizer test, not a native MO2, GUI, Nexus,
download, install, game, conversion, or save-safety test. It imports the actual
build_audit_request and RustAuditRunner, and invokes an actual compiled CLI.

Usage:
  python3 native/scripts/verify_plugin_bridge.py \
    --cli native/target/release/modhunter-cli \
    --output native/evidence/plugin-bridge-protocol.json

The debug CLI built by `cargo test -p modhunter-core` is also accepted. Source
and executable hashes identify the tested inputs. No network access is used.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import struct
import subprocess
import tempfile
import time


ROOT = Path(__file__).resolve().parents[2]
SOURCE_FILES = (
    "native/scripts/verify_plugin_bridge.py",
    "mo2_update_arsenal/plugin_intelligence.py",
    "native/crates/modhunter-core/src/lib.rs",
    "native/crates/modhunter-core/src/importer.rs",
    "native/crates/modhunter-core/src/plugin_analysis/mod.rs",
    "native/crates/modhunter-core/src/plugin_analysis/records.rs",
    "native/crates/modhunter-core/src/plugin_analysis/provenance.rs",
    "native/crates/modhunter-core/src/bin/modhunter-cli.rs",
    "native/Cargo.lock",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def plugin(*, light=False, object_id=0x800, empty=False) -> bytes:
    """A valid TES4/HEDR followed by one framed MISC record, or no records."""
    hedr = b"HEDR" + struct.pack("<HfII", 12, 1.7, 0 if empty else 2,
                                 0x800 if empty else object_id + 1)

    def record(tag, flags, form_id, payload):
        return struct.pack("<4sIIIIHH", tag, len(payload), flags, form_id, 0, 44, 0) + payload

    header = record(b"TES4", 0x200 if light else 0, 0, hedr)
    if empty:
        return header
    def field(tag, value):
        return tag + struct.pack("<H", len(value)) + value

    payload = (field(b"EDID", b"ModhunterProtocolFixture\0")
               + field(b"OBND", bytes(12)) + field(b"DATA", struct.pack("<If", 0, 0.0)))
    body = record(b"MISC", 0, object_id, payload)
    group = struct.pack("<4sI4sIII", b"GRUP", 24 + len(body), b"MISC", 0, 0, 0)
    return header + group + body


class Directory:
    def __init__(self, path: Path):
        self.path = path

    def absolutePath(self):
        return str(self.path)


class ModList:
    def __init__(self, paths: dict[str, Path]):
        self.paths = paths

    def getMod(self, name):
        return Directory(self.paths[name]) if name in self.paths else None


class ManagedGame:
    def __init__(self, data: Path):
        self.data = data

    def dataDirectory(self):
        return Directory(self.data)


class PluginList:
    def __init__(self, active: bool):
        self.active = active

    def pluginNames(self):
        return ["Example.esp"] if self.active else []

    def state(self, _name):
        return 2  # Documented MO2 ACTIVE enum; this organizer is synthetic.

    def loadOrder(self, _name):
        return 0

    def origin(self, _name):
        return "ESLifier Output"

    def isLightFlagged(self, _name):
        return True

    def hasLightExtension(self, _name):
        return False

    def masters(self, _name):
        return []


class Organizer:
    def __init__(self, root: Path, paths: dict[str, Path], *, active=True):
        self.root = root
        self.paths = paths
        self.active = active

    def profileName(self):
        return "Protocol Fixture"

    def profilePath(self):
        return str(self.root / "profiles" / self.profileName())

    def modList(self):
        return ModList(self.paths)

    def pluginList(self):
        return PluginList(self.active)

    def managedGame(self):
        return ManagedGame(self.root / "Data")

    def overwritePath(self):
        return str(self.root / "overwrite")

    def resolvePath(self, name):
        return str(self.paths["ESLifier Output"] / name) if self.active else ""

    def pluginSetting(self, plugin_name, key):
        if plugin_name == "ESLifier" and key == "ESLifier Folder" and self.active:
            return str(self.root / "tools" / "ESLifier")
        return ""


def load_production_module():
    # Import this real standalone module without importing the package's MO2/Qt
    # entry point, which cannot execute in a non-MO2 Linux test process.
    spec = importlib.util.spec_from_file_location(
        "modhunter_protocol_production", ROOT / "mo2_update_arsenal/plugin_intelligence.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Production plugin intelligence module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def verify(cli: Path) -> dict:
    if not cli.is_file():
        raise RuntimeError("--cli must identify an existing built Modhunter CLI")
    source_hashes = {name: sha256(ROOT / name) for name in SOURCE_FILES}
    executable_hash = sha256(cli)
    cli_version = subprocess.run(
        [str(cli), "--version"], check=True, capture_output=True, text=True, timeout=10
    ).stdout.strip()
    module = load_production_module()
    runner = module.RustAuditRunner(cli, timeout=30)
    checks = []

    def check(name: str, condition: bool) -> None:
        if not condition:
            raise RuntimeError("Protocol verification failed: " + name)
        checks.append({"name": name, "passed": True})

    def result_contract(result: dict, request: dict, stage: str) -> None:
        check(stage + "_schema_profile_depth", result.get("schemaVersion") == 1
              and result.get("profileId") == request["profileId"]
              and result.get("depth") == "records"
              and isinstance(result.get("mods"), dict)
              and bool(result.get("checkedAt")))

    with tempfile.TemporaryDirectory(prefix="modhunter-python-rust-protocol-") as temporary:
        root = Path(temporary)
        source_dir = root / "mods" / "Source Mod"
        output_dir = root / "mods" / "ESLifier Output"
        records = root / "tools" / "ESLifier" / "ESLifier_Data"
        for directory in (source_dir, output_dir, records, root / "Data", root / "overwrite",
                          root / "profiles" / "Protocol Fixture"):
            directory.mkdir(parents=True, exist_ok=True)
        source_path, output_path = source_dir / "Example.esp", output_dir / "Example.esp"
        original_bytes, output_bytes = plugin(), plugin(light=True)
        source_path.write_bytes(original_bytes)
        output_path.write_bytes(output_bytes)
        original_hash, output_hash = sha256(source_path), sha256(output_path)
        write_json(records / "settings.json", {
            "mod_manager_mode": 2, "mo2_profile": "Protocol Fixture",
            "output_folder_path": str(root / "mods"), "output_folder_name": "ESLifier Output",
        })
        write_json(records / "original_files.json", {"example.esp": [str(source_path), original_hash]})
        write_json(records / "new_file_hashes.json", {"example.esp": [output_hash, False]})
        write_json(records / "esl_flagged.json", ["Example.esp"])
        write_json(records / "compacted_and_patched.json", {})
        record_hashes = {p.name: sha256(p) for p in records.iterdir()}
        organizer = Organizer(root, {"Source Mod": source_dir, "ESLifier Output": output_dir})
        snapshot = {
            "profileId": "synthetic-protocol-profile", "profileName": organizer.profileName(),
            "game": "skyrimspecialedition", "gameRuntime": "1.6.1170",
            "items": [
                {"id": "source", "mo2InternalName": "Source Mod", "name": "Source Mod",
                 "enabled": True, "priority": 0, "installedVersion": "1.0"},
                {"id": "output", "mo2InternalName": "ESLifier Output", "name": "ESLifier Output",
                 "enabled": True, "priority": 1, "installedVersion": "1.0"},
            ],
        }
        request = module.build_audit_request(organizer, snapshot, deep=True)
        check("production_request_captures_owned_paths", len(request["mods"]) == 2
              and request["mods"][0]["path"] == str(source_dir)
              and request["pluginStates"][0]["path"] == str(output_path)
              and request["pluginStates"][0]["active"] is True)
        check("production_request_discovers_real_eslifier_setting", request["eslifierDataPaths"] == [str(records)])
        started = time.perf_counter()
        first = runner.run(request)
        first_elapsed = (time.perf_counter() - started) * 1000
        result_contract(first, request, "initial")
        first_source = first["mods"]["source"]["plugins"][0]
        first_output = first["mods"]["output"]["plugins"][0]
        check("native_winner_path_crosses_json_boundary", first_output["winner"] is True
              and first_output["winnerBy"] == "mo2-native"
              and first_source["winnerModId"] == "output")
        check("original_observes_light_override", first_source["lightOverride"] is True
              and first_source["active"] is True and first_source["winner"] is False)
        check("current_matches_recorded_original_and_output", first_source["provenance"]["state"] == "current"
              and first_output["provenance"]["state"] == "current"
              and first_source["fingerprint"]["sha256"] == original_hash
              and first_output["fingerprint"]["sha256"] == output_hash)
        check("valid_record_counts_and_conditional_eligibility", first_source["records"]["scanComplete"] is True
              and first_source["records"]["newRecords"] == 1
              and first_source["eligibility"]["status"] == "flag-only-candidate"
              and first_source["eligibility"]["saveSafety"] == "not-assessed")
        check("initial_audit_does_not_modify_mod_files", source_path.read_bytes() == original_bytes
              and output_path.read_bytes() == output_bytes)

        changed_bytes = plugin(object_id=0x801)
        source_path.write_bytes(changed_bytes)
        changed_hash = sha256(source_path)
        second_request = module.build_audit_request(organizer, snapshot, deep=True)
        check("staleness_test_preserves_installed_version", second_request["mods"][0]["installedVersion"] == "1.0")
        started = time.perf_counter()
        second = runner.run(second_request)
        second_elapsed = (time.perf_counter() - started) * 1000
        result_contract(second, second_request, "changed_source")
        second_source = second["mods"]["source"]["plugins"][0]
        second_output = second["mods"]["output"]["plugins"][0]
        check("updated_original_uses_fresh_sha256", changed_hash != original_hash
              and second_source["fingerprint"]["sha256"] == changed_hash
              and second_source["records"]["scanComplete"] is True)
        check("changed_source_is_detected_from_recorded_hashes", second_source["provenance"]["state"] == "source-changed"
              and second_output["provenance"]["state"] == "source-changed"
              and second["mods"]["source"]["status"] == "conversion-stale"
              and second_output["fingerprint"]["sha256"] == output_hash)
        check("audit_preserves_updated_source_output_and_tool_records", source_path.read_bytes() == changed_bytes
              and output_path.read_bytes() == output_bytes
              and {p.name: sha256(p) for p in records.iterdir()} == record_hashes)

        disabled_dir = root / "mods" / "Smoke Disabled"
        disabled_dir.mkdir()
        inert_path = disabled_dir / "ModhunterSmoke.esl"
        inert_bytes = plugin(empty=True)
        inert_path.write_bytes(inert_bytes)
        unknown_organizer = Organizer(root, {"Smoke Disabled": disabled_dir}, active=False)
        unknown_snapshot = {
            "profileId": "synthetic-unknown-game", "profileName": unknown_organizer.profileName(),
            "game": "unknown", "gameRuntime": "unknown", "items": [
                {"id": "disabled", "mo2InternalName": "Smoke Disabled", "name": "Smoke Disabled",
                 "enabled": False, "priority": 0, "installedVersion": "1.0"},
            ],
        }
        unknown_request = module.build_audit_request(unknown_organizer, unknown_snapshot, deep=True)
        unknown = runner.run(unknown_request)
        result_contract(unknown, unknown_request, "unknown_game")
        inert = unknown["mods"]["disabled"]["plugins"][0]
        check("unknown_game_inert_header_and_fingerprint", len(inert_bytes) == 42
              and inert["header"]["declaredRecordAndGroupCount"] == 0
              and inert["fingerprint"]["size"] == 42
              and inert["fingerprint"]["sha256"] == sha256(inert_path)
              and unknown["stats"]["deepScanned"] >= 1)
        check("unknown_game_does_not_gain_eligibility_or_light_claim", inert["eligibility"]["status"] == "unsupported"
              and inert["eligibility"]["saveSafety"] == "not-assessed"
              and inert["header"]["lightEffective"] is False and inert["winner"] is False)

    check("source_files_and_executable_stayed_unchanged", source_hashes == {name: sha256(ROOT / name) for name in SOURCE_FILES}
          and executable_hash == sha256(cli))
    return {
        "schemaVersion": 1, "ok": True,
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "scope": {"kind": "synthetic-python-rust-json-protocol", "realMO2": False,
                  "nativeGUI": False, "network": False, "gameOrModInstallation": False,
                  "conversionOrSaveSafetyAssessed": False, "disposableFixtures": True},
        "environment": {"system": platform.system(), "architecture": platform.machine(),
                        "python": platform.python_version(), "rustCliVersion": cli_version},
        "sourceHashes": source_hashes, "rustCliSha256": executable_hash,
        "checks": checks,
        "observed": {"originalCopies": 2, "initialLightOverride": True,
                     "initialRecordedOutputState": "current", "changedSourceState": "source-changed",
                     "installedVersionUnchanged": True, "unknownGameHeaderBytes": 42,
                     "initialRunnerElapsedMs": round(first_elapsed, 3),
                     "changedSourceRunnerElapsedMs": round(second_elapsed, 3)},
        "limitations": "Actual Python request capture and subprocess runner invoke the actual Rust parser on synthetic files. Organizer methods are fixture doubles. No native MO2, Windows GUI, Nexus, download, installation, game, conversion or save-safety claim is made. Timings are single fixture runs, not benchmarks.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = verify(args.cli.resolve())
    encoded = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=args.output.parent,
                                             prefix=".plugin-bridge-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(encoded)
            os.replace(temporary, args.output)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    print(encoded, end="")


if __name__ == "__main__":
    main()
