"""Fast update acceptance: zero extra modals while retaining MO2 identity guards."""
from __future__ import annotations

import copy
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from mo2_update_arsenal.bridge import ArsenalService
from mo2_update_arsenal.quick_update import choose_nexus_file, choose_github_asset, choose_release_asset


def mod(mid, project, *, flags=None, source="nexus-api"):
    result = {"id": mid, "name": mid, "mo2InternalName": mid, "nexusId": project,
              "installedVersion": "1.0", "flags": flags or [],
              "update": {"verdict": "candidate", "latestVersion": "2.0", "source": source}}
    if source == "github-release-api":
        result["source"] = {"type": "github", "url": "https://github.com/org/my-mod", "verified": True}
        result["update"]["releaseAssets"] = [{"assetId": 17, "name": "archive.zip",
            "url": "https://github.com/org/my-mod/releases/download/v2/archive.zip"}]
    return result


SNAP = {"profileId": "quick-profile", "profileName": "Quick profile", "game": "skyrimspecialedition",
        "gameRuntime": "1.5.97", "items": [mod("live-a", 101), mod("live-b", 102),
            mod("live-risky", 103, flags=["DLL"]), mod("live-github", 0, source="github-release-api")]}


def files(mid):
    return [{"fileId": 200 + mid, "name": "Latest main", "fileName": "main-2.0.7z",
             "version": "2.0", "category": 1, "sizeInKB": 1024},
            {"fileId": 100 + mid, "name": "Old main", "fileName": "main-1.0.7z",
             "version": "1.0", "category": 1}]


class QuickSelectionTests(unittest.TestCase):
    def test_unique_version_main_is_selected_without_hiding_alternates(self):
        selected, reason = choose_nexus_file(mod("live-a", 101), files(101))
        self.assertEqual(selected["fileId"], 301)
        self.assertIn("unique", reason)
        self.assertEqual(len(files(101)), 2)

    def test_ambiguous_editions_and_risky_dll_get_a_choice_not_an_auto_queue(self):
        for candidates in (files(101) + [dict(files(101)[0], fileId=777)], files(101)[:1]):
            with self.subTest(candidates=candidates):
                chosen, _ = choose_nexus_file(mod("live-a", 101), candidates)
                self.assertEqual(bool(chosen), len(candidates) == 1)
        picked, reason = choose_nexus_file(mod("live-risky", 103, flags=["DLL"]), files(103))
        self.assertIsNone(picked)
        self.assertIn("Compatibility", reason)

    def test_generic_review_note_does_not_block_a_unique_main_release(self):
        generic = mod("live-a", 101)
        generic["audit"] = {"verdict": "check"}
        self.assertEqual(choose_nexus_file(generic, files(101))[0]["fileId"], 301)
        generic["audit"] = {"verdict": "hold"}
        self.assertIsNone(choose_nexus_file(generic, files(101))[0])

    def test_github_suggests_only_single_installable_release_asset(self):
        g = mod("live-github", 0, source="github-release-api")
        self.assertEqual(choose_github_asset(g)[0]["assetId"], 17)
        g["update"]["releaseAssets"].append({"assetId": 19, "name": "patch.zip"})
        self.assertIsNone(choose_github_asset(g)[0])
        g["update"]["releaseAssets"] = [{"assetId": 19, "name": "README.txt"}]
        self.assertIsNone(choose_github_asset(g)[0])


class QuickServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.downloads_started = []
        self.installs_started = []
        self.gate = threading.Event()
        self.gate.set()

        def native(req):
            if req.cmd == "download":
                self.downloads_started.append((req.payload["modId"], req.payload["fileId"]))
                req.finish({"ok": True, "downloadId": 501 + len(self.downloads_started)})
            elif req.cmd == "download-url":
                self.downloads_started.append(("github", req.payload["url"]))
                req.finish({"ok": True, "downloadId": 501 + len(self.downloads_started)})
            elif req.cmd == "install":
                self.installs_started.append(req.payload["downloadId"])
                self.gate.wait(3)
                req.finish({"ok": True, "verifiedUpdate": False, "installedName": "example",
                            "backup": {"backupPath": "/backups/profile.txt"}})
            else:
                req.finish({"ok": False, "error": "unexpected"})

        self.service = ArsenalService("<html></html>", Path(self.tmp.name) / "cache.json",
                                     native, copy.deepcopy(SNAP))
        self.service.fetch_files = lambda mid: {"ok": True, "files": files(mid)}

    def tearDown(self):
        self.gate.set()
        deadline = time.monotonic() + 2
        while self.service._install_lock.locked() and time.monotonic() < deadline:
            time.sleep(.005)
        self.service.stop()
        self.tmp.cleanup()

    def await_condition(self, predicate, timeout=2):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            if predicate():
                return
            time.sleep(.005)
        self.fail("Expected quick update state did not occur")

    def test_single_click_auto_queues_and_opens_native_installer_after_download(self):
        reply = self.service.quick_update({"profileId": SNAP["profileId"], "itemIds": ["live-a"],
                                           "autoInstall": True})
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(len(reply["queued"]), 1)
        self.assertEqual(self.downloads_started, [(101, 301)])
        self.assertFalse(self.installs_started)
        did = reply["queued"][0]["downloadId"]
        self.assertTrue(self.service.downloads()[did]["autoInstallRequested"])
        self.gate.clear()
        self.service.set_download_status(did, "complete")
        self.await_condition(lambda: self.installs_started == [did])
        self.assertEqual(self.service.downloads()[did]["status"], "installing")
        self.assertFalse(self.service.quick_update({"profileId": SNAP["profileId"],
                                                   "itemIds": ["live-a"]})["queued"])
        self.gate.set()
        self.await_condition(lambda: self.service.downloads()[did]["status"] == "installed")
        self.assertFalse(self.service.downloads()[did]["verifiedUpdate"])
        self.assertEqual(len(self.installs_started), 1)

    def test_manual_risky_choice_still_updates_with_one_selection_no_extra_confirm(self):
        reply = self.service.quick_update({"profileId": SNAP["profileId"],
                                           "itemIds": ["live-risky"], "autoInstall": True})
        self.assertFalse(reply["queued"])
        self.assertIn("Compatibility", reply["skipped"][0]["reason"])
        manual = self.service.queue_reviewed_downloads({
            "profileId": SNAP["profileId"], "confirm": "QUEUE_REVIEWED_DOWNLOADS",
            "entries": [{"itemId": "live-risky", "modId": 103, "fileId": 303,
                         "installedVersion": "1.0", "targetVersion": "2.0", "reviewed": True,
                         "riskAcknowledged": True, "autoInstall": True}]})
        self.assertTrue(manual["ok"], manual)
        did = manual["queued"][0]["downloadId"]
        self.assertTrue(self.service.downloads()[did]["autoInstallRequested"])
        self.service.set_download_status(did, "complete")
        self.await_condition(lambda: did in self.installs_started)

    def test_bulk_is_nonblocking_and_keeps_ambiguous_mods_available(self):
        response = self.service.quick_update({"profileId": SNAP["profileId"], "all": True})
        self.assertEqual(response["state"], "running")
        self.await_condition(lambda: self.service.quick_state()["state"] == "completed")
        status = self.service.quick_state()
        self.assertEqual(status["total"], 4)
        self.assertEqual(status["completed"], 4)
        self.assertEqual(len(status["queued"]), 3)
        self.assertEqual(len(status["skipped"]), 1)
        self.assertIn("live-risky", str(status["skipped"]))
        for did in self.service.downloads():
            self.service.set_download_status(did, "complete")
        self.assertFalse(self.installs_started)  # Bulk download, not surprise FOMOD dialogs.

    def test_profile_recheck_prevents_wrong_game_or_profile_installs(self):
        reply = self.service.quick_update({"profileId": SNAP["profileId"], "itemIds": ["live-a"],
                                           "autoInstall": True})
        did = reply["queued"][0]["downloadId"]
        changed = copy.deepcopy(SNAP)
        changed["gameRuntime"] = "1.6.1170"
        self.service.set_snapshot(changed)
        self.service.set_download_status(did, "complete")
        self.assertFalse(self.installs_started)
        self.assertIn("runtime", self.service.downloads()[did].get("lastError", ""))
        other = copy.deepcopy(SNAP)
        other["profileId"] = "other"
        self.service.set_snapshot(other)
        self.assertFalse(self.service.quick_update({"profileId": SNAP["profileId"],
                                                   "itemIds": ["live-a"]})["ok"])
        self.assertEqual(self.service.downloads(), {})

    def test_ambiguous_nexus_files_never_auto_select(self):
        self.service.fetch_files = lambda mid: {"ok": True, "files": files(mid) + [
            dict(files(mid)[0], fileId=999)]}
        result = self.service.quick_update({"profileId": SNAP["profileId"], "itemIds": ["live-a"]})
        self.assertFalse(result["queued"])
        self.assertTrue(result["skipped"])
        self.assertFalse(self.downloads_started)

    def test_bulk_queue_waiting_for_native_handoff_cannot_resume_after_profile_round_trip(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        seen = []
        def blocked(profile, item_id, auto_install, *, execution_epoch=None):
            seen.append(item_id)
            entered.set()
            if not release.wait(2):
                raise AssertionError("Fixture queue handoff did not release")
            return {"skipped": [{"itemId": item_id, "reason": "fixture"}]}

        worker = self.service._quick_worker

        def observed(*args):
            try:
                return worker(*args)
            finally:
                finished.set()

        with patch.object(self.service, "_quick_one", side_effect=blocked), \
             patch.object(self.service, "_quick_worker", side_effect=observed):
            self.service.quick_update({"profileId": SNAP["profileId"], "all": True})
            try:
                self.assertTrue(entered.wait(1))
                changed = copy.deepcopy(SNAP)
                changed["profileId"] = "different-profile"
                self.service.set_snapshot(changed)
                self.service.set_snapshot(copy.deepcopy(SNAP))
                self.assertEqual(self.service.quick_state()["state"], "idle")
            finally:
                release.set()
                self.assertTrue(finished.wait(2))
        self.assertEqual(seen, ["live-a"])
        self.assertEqual(self.service.quick_state()["state"], "idle")
        self.assertFalse(self.downloads_started)

    def test_quick_source_selection_keeps_original_epoch_through_profile_round_trip(self):
        def choose(item):
            result = choose_release_asset(item)
            changed = copy.deepcopy(SNAP)
            changed["profileId"] = "different-profile"
            self.service.set_snapshot(changed)
            self.service.set_snapshot(copy.deepcopy(SNAP))
            return result

        with patch("mo2_update_arsenal.bridge.choose_release_asset", side_effect=choose):
            result = self.service.quick_update({"profileId": SNAP["profileId"], "itemIds": ["live-github"]})
        self.assertFalse(result["queued"])
        self.assertTrue(result["failed"])
        self.assertFalse(self.downloads_started)

    def test_delayed_browser_quick_update_cannot_reuse_previous_profile_epoch(self):
        old = self.service.selection_snapshot([])["executionEpoch"]
        changed = copy.deepcopy(SNAP)
        changed["profileId"] = "different-profile"
        self.service.set_snapshot(changed)
        self.service.set_snapshot(copy.deepcopy(SNAP))
        with patch.object(self.service, "_quick_one", side_effect=AssertionError("Stale browser intent was accepted")):
            result = self.service.quick_update({"profileId": SNAP["profileId"],
                "executionEpoch": old, "itemIds": ["live-a"]})
        self.assertFalse(result["ok"])
        self.assertFalse(self.downloads_started)


if __name__ == "__main__":
    unittest.main()
