"""Deterministic pre-install briefing, safe rejection, and HTTP auth tests.

These checks exercise provenance/state validation but not native Windows MO2.
"""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from mo2_update_arsenal.bridge import ArsenalService
from mo2_update_arsenal.install_brief import build_install_brief


SNAP = {
    "profileId": "skyrim:main", "profileName": "Primary", "game": "skyrimspecialedition",
    "gameRuntime": "1.5.97", "items": [{
        "id": "live-test", "name": "Test Animations", "mo2InternalName": "Test Animations",
        "nexusId": 45, "installedVersion": "1.1", "flags": ["DLL"],
        "audit": {"verdict": "check"},
        "update": {"verdict": "candidate", "latestVersion": "2.4", "source": "nexus-api"},
    }],
}
TICKET = {
    "profileId": "skyrim:main", "profileName": "Primary",
    "game": "skyrimspecialedition", "gameRuntime": "1.5.97", "status": "complete",
    "itemId": "live-test", "installedVersion": "1.1", "targetVersion": "2.4",
    "provider": "nexus", "fileId": 48, "fileName": "Animations-SE-2.4.7z",
    "fileVersion": "2.4", "reviewed": True,
    "riskReasons": ["Native/configuration flag: DLL", "Existing compatibility advisory: check"],
}


class InstallBriefTests(unittest.TestCase):
    def test_exact_evidence_and_honest_backup_scope(self):
        preview = build_install_brief(copy.deepcopy(SNAP), copy.deepcopy(TICKET), 711)
        self.assertTrue(preview["canInstall"], preview)
        self.assertEqual(preview["modName"], "Test Animations")
        self.assertEqual(preview["archiveName"], "Animations-SE-2.4.7z")
        self.assertEqual(preview["fileId"], 48)
        self.assertIn("Native/configuration flag: DLL", preview["warnings"])
        self.assertFalse(preview["backup"]["includesModFiles"])
        self.assertEqual(len(preview["backup"]["files"]), 5)

    def test_changed_profile_game_runtime_version_and_release_are_blocking(self):
        for path, new_value in [
            (("profileId",), "skyrim:other"),
            (("profileName",), "Different profile"),
            (("game",), "fallout4"),
            (("gameRuntime",), "1.6.1170"),
            (("items", 0, "installedVersion"), "1.8"),
            (("items", 0, "update", "latestVersion"), "3.0"),
        ]:
            with self.subTest(path=path):
                snap = copy.deepcopy(SNAP)
                target = snap
                for part in path[:-1]:
                    target = target[part]
                target[path[-1]] = new_value
                result = build_install_brief(snap, copy.deepcopy(TICKET), 711)
                self.assertFalse(result["canInstall"], result)
                self.assertTrue(result["errors"])

    def test_newly_detected_compatibility_risks_fail_closed(self):
        snap = copy.deepcopy(SNAP)
        snap["items"][0]["flags"] = ["DLL", "NEMESIS"]
        result = build_install_brief(snap, TICKET, 9)
        self.assertFalse(result["canInstall"])
        self.assertIn("New compatibility warnings", result["errors"][0])

    def test_other_download_states_or_missing_mod_cannot_install(self):
        for state in ("downloading", "paused", "failed", "removed", "installing", "installed"):
            with self.subTest(state=state):
                ticket = {**TICKET, "status": state}
                self.assertFalse(build_install_brief(SNAP, ticket, 8)["canInstall"])
        snap = {**SNAP, "items": []}
        self.assertFalse(build_install_brief(snap, TICKET, 8)["canInstall"])
        self.assertFalse(build_install_brief(SNAP, TICKET, True)["canInstall"])
        self.assertFalse(build_install_brief(SNAP, None, 1)["canInstall"])

    def test_unverified_runtime_and_nonmatching_exact_file_are_just_warnings(self):
        snap, ticket = copy.deepcopy(SNAP), copy.deepcopy(TICKET)
        snap["gameRuntime"] = ticket["gameRuntime"] = "unknown"
        ticket["fileVersion"] = "2.2"
        result = build_install_brief(snap, ticket, 8)
        self.assertTrue(result["canInstall"])
        self.assertGreaterEqual(len(result["warnings"]), 3)


class InstallBriefServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.calls = []

        def ui(req):
            self.calls.append(req.cmd)
            if req.cmd == "download":
                req.finish({"ok": True, "downloadId": 711})
            elif req.cmd == "install":
                req.finish({"ok": False, "error": "MO2 user canceled"})
            else:
                req.finish({"ok": False})

        self.service = ArsenalService("<html></html>", Path(self.temp.name) / "cache.json", ui,
                                     copy.deepcopy(SNAP))
        self.service.fetch_files = lambda mid: {"ok": True, "files": [{
            "fileId": 48, "fileName": "Animations-SE-2.4.7z", "name": "Main file",
            "version": "2.4", "category": 1,
        }]}
        self.queue_result = self.service.queue_reviewed_downloads({
            "profileId": SNAP["profileId"], "confirm": "QUEUE_REVIEWED_DOWNLOADS",
            "entries": [{"itemId": "live-test", "modId": 45, "fileId": 48,
                         "installedVersion": "1.1", "targetVersion": "2.4",
                         "riskAcknowledged": True, "reviewed": True}],
        })
        self.assertTrue(self.queue_result["ok"])
        self.service.set_download_status(711, "complete")

    def tearDown(self):
        self.service.stop()
        self.temp.cleanup()

    def test_queued_archive_keeps_exact_file_provenance(self):
        ticket = self.service.downloads()[711]
        self.assertEqual(ticket["fileName"], "Animations-SE-2.4.7z")
        self.assertEqual(ticket["fileVersion"], "2.4")
        self.assertTrue(ticket["reviewed"])
        self.assertEqual(ticket["riskReasons"], TICKET["riskReasons"])
        self.assertEqual(self.service.preview_install(711)["archiveName"], ticket["fileName"])

    def test_server_rechecks_before_install_dispatch(self):
        changed = copy.deepcopy(SNAP)
        changed["gameRuntime"] = "1.6.1170"
        self.service.set_snapshot(changed)
        result = self.service.install_reviewed_download(711)
        self.assertFalse(result["ok"])
        self.assertIn("runtime changed", result["error"])
        self.assertEqual(self.calls, ["download"])

    def test_install_preview_endpoint_is_session_authorized(self):
        root = self.service.start()
        url = root + "api/install-preview?downloadId=711"
        with self.assertRaises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(url, timeout=3)
        self.assertEqual(exc.exception.code, 403)
        req = urllib.request.Request(url, headers={"X-Arsenal-Token": self.service.token})
        with urllib.request.urlopen(req, timeout=3) as response:
            body = json.load(response)
        self.assertTrue(body["canInstall"])
        self.assertEqual(body["archiveName"], "Animations-SE-2.4.7z")
        self.assertEqual(body["profileName"], "Primary")


if __name__ == "__main__":
    unittest.main()
