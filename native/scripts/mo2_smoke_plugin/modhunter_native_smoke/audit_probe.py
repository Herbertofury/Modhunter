"""Bounded HTTP evidence checks for the real MO2 Rust-audit smoke.

This module has no Qt imports so its protocol checks can be self-tested without
launching a GUI. A self-test is not evidence that native Windows MO2 passed.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import struct
import time
import urllib.parse


FIXTURE_PLUGIN_NAME = "ModhunterSmoke.esp"
FIXTURE_PLUGIN_OWNER = "Smoke Disabled"
# An inert modern TES4 header and HEDR, with no masters or content records.
# It resides in the disabled disposable mod, never a user's game directory.
FIXTURE_PLUGIN_BYTES = (struct.pack("<4sIIIIHH", b"TES4", 18, 0, 0, 0, 44, 0)
                        + b"HEDR" + struct.pack("<HfII", 12, 1.7, 0, 0x800))
FIXTURE_PLUGIN_SHA256 = hashlib.sha256(FIXTURE_PLUGIN_BYTES).hexdigest()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def helper_hash(path: Path) -> str:
    maximum = 128 * 1024 * 1024
    initial = path.stat()
    require(path.is_file() and 0 < initial.st_size <= maximum, "Rust helper is missing, empty or exceeds the smoke hash bound")
    digest, consumed = hashlib.sha256(), 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            consumed += len(chunk)
            require(consumed <= maximum, "Rust helper exceeded its smoke hash bound while reading")
            digest.update(chunk)
    final = path.stat()
    require((initial.st_size, initial.st_mtime_ns) == (final.st_size, final.st_mtime_ns)
            and consumed == initial.st_size, "Rust helper changed during smoke verification")
    return digest.hexdigest()


def exercise_plugin_audit(api, profile_id: str, fixture_rows: dict, installed_cli: Path,
                          supplied_cli: Path, *, timeout=35, clock=time.monotonic,
                          pause=time.sleep) -> dict:
    """Run on the HTTP worker, leaving MO2's Qt event loop unblocked.

    ``api`` uses only the harness's validated loopback address. It must accept
    ``timeout=`` so every HTTP call fits within this audit's overall deadline.
    """
    require(isinstance(profile_id, str) and bool(profile_id), "Rust audit requires the observed native profile")
    require(0 < timeout <= 60, "Rust audit smoke timeout must be bounded to at most 60 seconds")
    digest = helper_hash(installed_cli)
    require(digest == helper_hash(supplied_cli), "Installed MO2 helper differs from the supplied compiled CLI")
    started = clock()
    deadline = started + timeout

    def call(path, data=None):
        remaining = deadline - clock()
        require(remaining > 0, "Native Rust plugin audit exceeded its smoke deadline")
        result = api(path, data, timeout=min(5.0, remaining))
        require(clock() <= deadline, "Native Rust plugin audit exceeded its smoke deadline")
        return result

    state = call("/api/plugin-audit", {"profileId": profile_id, "deep": True})
    polls = 0
    while True:
        require(isinstance(state, dict) and state.get("ok") is True,
                "Native Rust plugin audit request failed: " + str(state.get("error", "invalid response") if isinstance(state, dict) else "invalid response")[:400])
        require(state.get("profileId") == profile_id, "Native Rust plugin audit changed profiles")
        require(state.get("state") not in {"failed", "unavailable", "stale"},
                "Native Rust plugin audit failed: " + str(state.get("error") or state.get("reason") or state.get("state"))[:400])
        if state.get("state") == "completed" and state.get("depth") == "records":
            break
        require(state.get("state") in {"queued", "running", "completed"}, "Unexpected native Rust audit state")
        remaining = deadline - clock()
        require(remaining > 0, "Native Rust plugin audit exceeded its smoke deadline")
        pause(min(0.1, remaining))
        state = call("/api/plugin-audit")
        polls += 1
    analysis = state.get("analysis") or {}
    require(analysis.get("schemaVersion") == 1 and analysis.get("profileId") == profile_id
            and analysis.get("depth") == "records" and bool(analysis.get("checkedAt")),
            "Native Rust plugin audit did not return schema 1 records for the active profile")
    require((analysis.get("stats") or {}).get("deepScanned", 0) >= 1,
            "Native Rust plugin audit did not hash the synthetic plugin")
    observed = {}
    fixture_plugin = None
    for name in ("Smoke Enabled", "Smoke Disabled"):
        row = fixture_rows.get(name) or {}
        require(isinstance(row.get("id"), str) and bool(row["id"]), "Native fixture row was missing from the profile")
        query = urllib.parse.urlencode({"itemId": row["id"], "profileId": profile_id})
        selected = call("/api/mod-health?" + query)
        local = selected.get("localAnalysis") or {}
        current = selected.get("pluginAuditState") or {}
        require(selected.get("ok") is True and selected.get("profileId") == profile_id
                and selected.get("itemId") == row["id"] and local.get("depth") == "records"
                and local.get("checkedAt") == analysis["checkedAt"]
                and current.get("state") == "completed" and current.get("depth") == "records"
                and isinstance(local.get("plugins"), list),
                "Native Rust audit did not decorate the exact fixture mod: " + name)
        observed[name] = {"itemId": row["id"], "status": local.get("status"), "pluginCount": len(local["plugins"])}
        if name == FIXTURE_PLUGIN_OWNER:
            matches = [plugin for plugin in local["plugins"] if plugin.get("name") == FIXTURE_PLUGIN_NAME]
            require(len(matches) == 1, "Native Rust audit did not inspect the disabled fixture plugin")
            plugin = matches[0]
            header, fingerprint = plugin.get("header") or {}, plugin.get("fingerprint") or {}
            require(not plugin.get("errors") and plugin.get("enabled") is False
                    and header.get("masters") == [] and header.get("declaredRecordAndGroupCount") == 0
                    and fingerprint.get("size") == len(FIXTURE_PLUGIN_BYTES)
                    and fingerprint.get("sha256") == FIXTURE_PLUGIN_SHA256,
                    "Native Rust fixture plugin header or fresh SHA256 differs from its generated bytes")
            fixture_plugin = {"name": FIXTURE_PLUGIN_NAME, "owner": FIXTURE_PLUGIN_OWNER,
                              "bytes": len(FIXTURE_PLUGIN_BYTES), "sha256": fingerprint["sha256"],
                              "declaredRecords": 0, "verified": True}
    require(helper_hash(installed_cli) == digest, "Installed helper changed during the native audit")
    return {"schemaVersion": 1, "profileId": profile_id, "state": "completed", "depth": "records",
            "helperSha256": digest, "fixtureMods": observed, "syntheticPlugin": fixture_plugin,
            "polls": polls, "elapsedMs": round((clock() - started) * 1000, 2),
            "scope": "Real Qt inventory capture and shipped Rust helper; synthetic unsupported game, no game eligibility or conversion-safety claim"}


def self_test():
    """Exercise gate protocol using synthetic receipts, not native Windows proof."""
    import tempfile
    import unittest

    class ProbeTests(unittest.TestCase):
        def setUp(self):
            self.temp = tempfile.TemporaryDirectory()
            self.addCleanup(self.temp.cleanup)
            self.helper = Path(self.temp.name) / "synthetic-helper"
            self.helper.write_bytes(b"synthetic helper bytes; never executed")
            self.now = 0.0
            self.calls = []
            self.pending = 1
            self.alter = None
            self.rows = {"Smoke Enabled": {"id": "enabled"}, "Smoke Disabled": {"id": "disabled"}}

        def pause(self, duration):
            self.now += duration

        def api(self, path, data=None, *, timeout):
            self.calls.append((path, data, timeout))
            state = {"ok": True, "profileId": "fixture", "state": "completed", "depth": "records",
                     "analysis": {"schemaVersion": 1, "profileId": "fixture", "depth": "records", "checkedAt": "observed",
                                  "stats": {"deepScanned": 1}}}
            if path == "/api/plugin-audit":
                if data is not None or self.pending:
                    self.pending = max(0, self.pending - 1)
                    state["state"] = "queued" if data else "running"
                if self.alter:
                    state = self.alter(state)
                return state
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
            item = query["itemId"][0]
            plugins = [] if item == "enabled" else [{"name": FIXTURE_PLUGIN_NAME, "enabled": False,
                "errors": [], "header": {"masters": [], "declaredRecordAndGroupCount": 0},
                "fingerprint": {"size": 42, "sha256": FIXTURE_PLUGIN_SHA256}}]
            return {"ok": True, "profileId": "fixture", "itemId": item, "pluginAuditState": state,
                    "localAnalysis": {"depth": "records", "checkedAt": "observed", "plugins": plugins, "status": "fixture-only"}}

        def run_probe(self, **kwargs):
            return exercise_plugin_audit(self.api, "fixture", self.rows, self.helper, self.helper,
                                         clock=lambda: self.now, pause=self.pause, **kwargs)

        def test_completed_deep_job_and_both_rows_are_required(self):
            result = self.run_probe()
            self.assertEqual(result["depth"], "records")
            self.assertEqual(set(result["fixtureMods"]), set(self.rows))
            self.assertEqual(result["syntheticPlugin"]["sha256"], FIXTURE_PLUGIN_SHA256)
            self.assertTrue(all(path.startswith(("/api/plugin-audit", "/api/mod-health?")) for path, _, _ in self.calls))
            self.assertTrue(all(timeout <= 5 for _, _, timeout in self.calls))

        def test_header_only_completion_cannot_pass_deep_gate(self):
            self.alter = lambda state: {**state, "state": "completed", "depth": "header"}
            with self.assertRaisesRegex(AssertionError, "deadline"):
                self.run_probe(timeout=0.2)
            self.assertLessEqual(self.now, 0.2)

        def test_failed_wrong_profile_and_wrong_schema_are_rejected(self):
            for alteration, error in (({"state": "failed"}, "failed"), ({"profileId": "other"}, "profiles"),
                                      ({"analysis": {"schemaVersion": 2}}, "schema 1")):
                with self.subTest(alteration=alteration):
                    self.pending = 0
                    self.alter = lambda state, value=alteration: {**state, **value}
                    with self.assertRaisesRegex(AssertionError, error):
                        self.run_probe()

        def test_missing_fixture_row_and_different_installed_binary_fail(self):
            del self.rows["Smoke Disabled"]
            with self.assertRaisesRegex(AssertionError, "fixture row"):
                self.run_probe()
            other = self.helper.with_name("different-helper")
            other.write_bytes(b"different synthetic bytes")
            with self.assertRaisesRegex(AssertionError, "differs"):
                exercise_plugin_audit(self.api, "fixture", self.rows, self.helper, other)

        def test_fixture_has_exact_inert_tes4_header(self):
            self.assertEqual(len(FIXTURE_PLUGIN_BYTES), 42)
            self.assertEqual(struct.unpack("<4sIIIIHH", FIXTURE_PLUGIN_BYTES[:24]), (b"TES4", 18, 0, 0, 0, 44, 0))
            self.assertEqual(FIXTURE_PLUGIN_BYTES[24:28], b"HEDR")
            self.assertEqual(struct.unpack("<HfII", FIXTURE_PLUGIN_BYTES[28:])[0::2], (12, 0))

    outcome = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ProbeTests))
    if not outcome.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    import sys
    if sys.argv[1:] != ["--self-test"]:
        raise SystemExit("Use --self-test for synthetic gate tests; run the PowerShell harness for native Windows evidence.")
    self_test()
