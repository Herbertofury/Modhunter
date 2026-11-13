"""MO2 Update Workshop installation tickets and duplicate-queue regression tests."""
import copy
import tempfile
import threading
import time
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
        self.install_started = threading.Event()
        self.install_gate = threading.Event()
        self.install_gate.set()
        self.install_result = {"ok": True, "verifiedUpdate": True,
                               "installedName": "Test Mod",
                               "backup": {"backupPath": "/backup/example"}}
        def native(req):
            self.calls.append(req.cmd)
            if req.cmd == "download":
                req.finish({"ok": True, "downloadId": 744})
            elif req.cmd == "install":
                self.install_started.set()
                self.install_gate.wait(3)
                req.finish(self.install_result)
            else:
                req.finish({"ok": False, "error": "Unexpected command"})
        self.service = ArsenalService("<html></html>", Path(self.tmp.name) / "cache.json",
                                     native, copy.deepcopy(SNAPSHOT))
        self.service.fetch_files = lambda mid: {"ok": True, "files": [{"fileId": 55}]}

    def tearDown(self):
        self.install_gate.set()
        self.service.stop()
        self.tmp.cleanup()

    def await_ticket(self, state, timeout=2):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if (self.service.downloads().get(744, {}).get("status") == state
                    and not self.service._install_lock.locked()):
                return self.service.downloads()[744]
            time.sleep(0.01)
        self.fail("MO2 ticket never reached " + state)

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
        self.assertEqual(result["state"], "installing")
        self.assertTrue(self.await_ticket("installed")["verifiedUpdate"])
        self.service.set_download_status(744, "complete")
        self.assertEqual(self.service.downloads()[744]["status"], "installed")
        self.assertFalse(self.service.install_reviewed_download(744)["ok"])
        self.assertEqual(self.calls, ["download", "install"])

    def test_cancelled_install_can_retry(self):
        self.assertTrue(self.queue()["ok"])
        self.service.set_download_status(744, "complete")
        self.install_result = {"ok": False, "error": "MO2 installer canceled"}
        self.assertTrue(self.service.install_reviewed_download(744)["ok"])
        self.assertIn("canceled", self.await_ticket("complete")["lastError"])
        self.install_result = {"ok": True, "verifiedUpdate": False,
                               "installedName": "Test Mod"}
        self.assertTrue(self.service.install_reviewed_download(744)["ok"])
        self.assertFalse(self.await_ticket("installed")["verifiedUpdate"])
        self.assertEqual(self.calls, ["download", "install", "install"])

    def test_native_fomod_dialog_can_stay_open_without_blocking_http(self):
        self.assertTrue(self.queue()["ok"])
        self.service.set_download_status(744, "complete")
        self.install_gate.clear()
        began = time.monotonic()
        receipt = self.service.install_reviewed_download(744)
        self.assertTrue(receipt["ok"])
        self.assertLess(time.monotonic() - began, 0.5)
        self.assertTrue(self.install_started.wait(1))
        self.assertEqual(self.service.downloads()[744]["status"], "installing")
        self.assertFalse(self.service.install_reviewed_download(744)["ok"])
        self.assertFalse(self.queue()["ok"])
        self.install_gate.set()
        self.assertEqual(self.await_ticket("installed")["status"], "installed")

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

    def test_completed_install_does_not_block_the_next_release_after_version_refresh(self):
        self.assertTrue(self.queue()["ok"])
        self.service.set_download_status(744, "complete")
        self.assertTrue(self.service.install_reviewed_download(744)["ok"])
        self.await_ticket("installed")
        self.assertFalse(self.queue()["ok"], "Unconfirmed installed version still prevents duplicate execution")
        changed = copy.deepcopy(SNAPSHOT)
        changed["items"][0]["installedVersion"] = "2.0"
        changed["items"][0]["update"]["latestVersion"] = "3.0"
        self.service.set_snapshot(changed)
        self.service.fetch_files = lambda mid: {"ok": True, "files": [{"fileId": 66, "version": "3.0"}]}
        self.service.ui_call = lambda req: req.finish({"ok": True, "downloadId": 745})
        result = self.service.queue_reviewed_downloads({
            "profileId": SNAPSHOT["profileId"], "confirm": "QUEUE_REVIEWED_DOWNLOADS",
            "entries": [{"itemId": "live-test", "modId": 101, "fileId": 66,
                         "installedVersion": "2.0", "targetVersion": "3.0", "reviewed": True}]})
        self.assertEqual(result["queued"][0]["downloadId"], 745)
        self.assertEqual(self.service.downloads()[744]["status"], "installed")
        self.assertEqual(self.service.downloads()[745]["targetVersion"], "3.0")

    def test_changed_release_or_new_warning_during_file_lookup_never_dispatches(self):
        for field, value in (("target", "3.0"), ("risk", ["DLL"]), ("runtime", "1.6.1170")):
            with self.subTest(changed=field):
                self.service._snapshot = copy.deepcopy(SNAPSHOT)
                self.service._items_by_id = {row["id"]: row for row in self.service._snapshot["items"]}

                def files(mid):
                    row = self.service._items_by_id["live-test"]
                    if field == "target":
                        row["update"]["latestVersion"] = value
                    elif field == "risk":
                        row["flags"] = value
                    else:
                        self.service._snapshot["gameRuntime"] = value
                    return {"ok": True, "files": [{"fileId": 55}]}

                self.service.fetch_files = files
                result = self.queue()
                self.assertEqual(result["queued"], [])
                self.assertIn("changed", result["failed"][0]["error"])
                self.assertFalse(self.calls)

    def test_manual_batch_waiting_for_queue_lock_keeps_its_original_epoch(self):
        entered, release = threading.Event(), threading.Event()
        results = []

        class Gate:
            def __enter__(self):
                entered.set()
                if not release.wait(3):
                    raise AssertionError("Queue-lock fixture was not released")

            def __exit__(self, *_):
                return False

        self.service._queue_lock = Gate()
        worker = threading.Thread(target=lambda: results.append(self.queue()))
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            other = copy.deepcopy(SNAPSHOT)
            other["profileId"] = "other-profile"
            self.service.set_snapshot(other)
            self.service.set_snapshot(copy.deepcopy(SNAPSHOT))
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertFalse(results[0]["ok"])
        self.assertFalse(results[0]["queued"])
        self.assertFalse(self.calls)

    def test_manual_batch_rejects_stale_browser_epoch_before_waiting_for_queue_lock(self):
        class ForbiddenLock:
            def __enter__(self):
                raise AssertionError("Stale browser batch waited for the native queue")

            def __exit__(self, *_):
                return False

        old = self.service.selection_snapshot([])["executionEpoch"]
        other = copy.deepcopy(SNAPSHOT)
        other["profileId"] = "other-profile"
        self.service.set_snapshot(other)
        self.service.set_snapshot(copy.deepcopy(SNAPSHOT))
        self.service._queue_lock = ForbiddenLock()
        result = self.service.queue_reviewed_downloads({"profileId": SNAPSHOT["profileId"],
            "executionEpoch": old, "confirm": "QUEUE_REVIEWED_DOWNLOADS", "entries": [
                {"itemId": "live-test", "modId": 101, "fileId": 55,
                 "installedVersion": "1.0", "reviewed": True}]})
        self.assertFalse(result["ok"])
        self.assertFalse(self.calls)
        

if __name__ == "__main__":
    unittest.main()
