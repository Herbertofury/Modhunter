"""MO2 Update Workshop installation tickets and duplicate-queue regression tests."""
import copy
import tempfile
import unittest
from pathlib import Path

from mo2_update_arsenal.bridge import ArsenalService


SNAPSHOT = {
    "profileId": "mo2:skyrim:profile1", "profileName": "Profile 1",
    "game": "skyrimspecialedition", "items": [
        {"id": "live-test", "name": "Test Mod", "mo2InternalName": "Test Mod",
         "nexusId": 101, "installedVersion": "1.0",
         "update": {"verdict": "candidate", "latestVersion": "2.0"}, "flags": []},
    ]
}


class TicketLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.calls = []
        self.install_result = {"ok": True, "verifiedUpdate": True,
                               "installedName": "Test Mod",
                               "backup": {"backupPath": "/backup/example"}}
        def native(req):
            self.calls.append(req.cmd)
            if req.cmd == "download":
                req.finish({"ok": True, "downloadId": 744})
            elif req.cmd == "install":
                req.finish(self.install_result)
            else:
                req.finish({"ok": False, "error": "Unexpected command"})
        self.service = ArsenalService("<html></html>", Path(self.tmp.name) / "cache.json",
                                     native, copy.deepcopy(SNAPSHOT))
        self.service.fetch_files = lambda mid: {"ok": True, "files": [{"fileId": 55}]}

    def tearDown(self):
        self.service.stop()
        self.tmp.cleanup()

    def queue(self):
        return self.service.queue_reviewed_downloads({
            "profileId": SNAPSHOT["profileId"], "confirm": "QUEUE_REVIEWED_DOWNLOADS",
            "entries": [{"itemId": "live-test", "modId": 101, "fileId": 55,
                         "installedVersion": "1.0", "reviewed": True}]})

    def test_queue_tracks_exact_row_and_rejects_duplicate_downloads(self):
        self.assertTrue(self.queue()["ok"])
        ticket = self.service.downloads()[744]
        self.assertEqual((ticket["itemId"], ticket["installedVersion"]),
                         ("live-test", "1.0"))
        self.assertEqual(ticket["provider"], "nexus")
        self.assertFalse(self.queue()["ok"])
        self.assertEqual(self.calls, ["download"])

    def test_install_requires_completion_and_cannot_install_twice(self):
        self.assertTrue(self.queue()["ok"])
        self.assertFalse(self.service.install_reviewed_download(744)["ok"])
        self.service.set_download_status(744, "complete")
        result = self.service.install_reviewed_download(744)
        self.assertTrue(result["ok"])
        self.assertTrue(self.service.downloads()[744]["verifiedUpdate"])
        self.assertEqual(self.service.downloads()[744]["status"], "installed")
        self.service.set_download_status(744, "complete")
        self.assertEqual(self.service.downloads()[744]["status"], "installed")
        self.assertFalse(self.service.install_reviewed_download(744)["ok"])
        self.assertEqual(self.calls, ["download", "install"])

    def test_cancelled_install_can_retry(self):
        self.assertTrue(self.queue()["ok"])
        self.service.set_download_status(744, "complete")
        self.install_result = {"ok": False, "error": "MO2 installer canceled"}
        self.assertFalse(self.service.install_reviewed_download(744)["ok"])
        self.assertEqual(self.service.downloads()[744]["status"], "complete")
        self.install_result = {"ok": True, "verifiedUpdate": False,
                               "installedName": "Test Mod"}
        self.assertTrue(self.service.install_reviewed_download(744)["ok"])
        self.assertFalse(self.service.downloads()[744]["verifiedUpdate"])
        self.assertEqual(self.calls, ["download", "install", "install"])

    def test_profile_or_version_change_blocks_stale_install(self):
        self.assertTrue(self.queue()["ok"])
        self.service.set_download_status(744, "complete")
        changed = copy.deepcopy(SNAPSHOT)
        changed["items"][0]["installedVersion"] = "1.5"
        self.service.set_snapshot(changed)
        self.assertFalse(self.service.install_reviewed_download(744)["ok"])
        changed["profileId"] = "mo2:skyrim:profile2"
        self.service.set_snapshot(changed)
        self.assertNotIn(744, self.service.downloads())
        self.assertFalse(self.service.install_reviewed_download(744)["ok"])
        self.assertNotIn("install", self.calls)

    def test_removed_or_failed_download_is_not_installable(self):
        self.assertTrue(self.queue()["ok"])
        self.service.set_download_status(744, "removed")
        self.assertFalse(self.service.install_reviewed_download(744)["ok"])
        self.assertTrue(self.queue()["ok"])  # User can queue a replacement.
        

if __name__ == "__main__":
    unittest.main()
