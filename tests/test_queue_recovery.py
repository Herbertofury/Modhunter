"""MO2 queue recovery and low-friction installer regression tests."""
from __future__ import annotations

import copy
import tempfile
import threading
import time
import unittest
from pathlib import Path

from mo2_update_arsenal.bridge import ArsenalService


SNAP = {
    "profileId": "recovery-profile", "profileName": "Main profile",
    "game": "skyrimspecialedition", "gameRuntime": "1.5.97",
    "items": [
        {"id": "alpha", "name": "Alpha", "mo2InternalName": "Alpha",
         "installedVersion": "1.0", "nexusId": 101, "flags": [],
         "update": {"source": "nexus-api", "verdict": "candidate", "latestVersion": "2.0"}},
        {"id": "beta", "name": "Beta", "mo2InternalName": "Beta",
         "installedVersion": "1.0", "nexusId": 102, "flags": [],
         "update": {"source": "nexus-api", "verdict": "candidate", "latestVersion": "2.0"}},
        {"id": "github", "name": "GitHub Mod", "mo2InternalName": "GitHub Mod",
         "installedVersion": "1.0", "nexusId": 0, "flags": [],
         "update": {"source": "github-release-api", "verdict": "candidate", "latestVersion": "2.0",
                    "releaseAssets": [{"assetId": 17, "name": "github-mod.zip",
                                      "url": "https://github.com/project/repo/releases/download/v2/github-mod.zip"}]}},
    ],
}


class RecoveryQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.download_calls = []
        self.install_calls = []
        self.gates = {}
        self.emit_early = ""
        self.cancel_install = False
        self.service = None

        def native(req):
            if req.cmd in ("download", "download-url"):
                did = 900 + len(self.download_calls) + 1
                self.download_calls.append((did, req.cmd))
                if self.emit_early:
                    self.service.set_download_status(did, self.emit_early)
                req.finish({"ok": True, "downloadId": did})
            elif req.cmd == "install":
                did = req.payload["downloadId"]
                self.install_calls.append(did)
                self.gates.setdefault(did, threading.Event()).wait(3)
                req.finish({"ok": not self.cancel_install, "verifiedUpdate": False,
                            "installedName": "Updated Mod",
                            "backup": {"backupPath": "/private/profile-backup"},
                            "error": "MO2 installer canceled" if self.cancel_install else ""})
            else:
                req.finish({"ok": False, "error": "Unexpected native call"})

        self.service = ArsenalService("<html></html>", Path(self.tmp.name) / "cache.json",
                                     native, copy.deepcopy(SNAP))
        self.service.fetch_files = lambda mid: {
            "ok": True, "files": [{"fileId": 100 + mid, "name": "Latest main",
                                   "fileName": "mod-2.0.7z", "version": "2.0", "category": 1}],
        }

    def tearDown(self):
        for gate in self.gates.values():
            gate.set()
        limit = time.monotonic() + 2
        while self.service._install_lock.locked() and time.monotonic() < limit:
            time.sleep(.005)
        self.service.stop()
        self.tmp.cleanup()

    def queue(self, items=("alpha",)):
        entries = [{"itemId": name, "modId": 101 if name == "alpha" else 102,
                    "fileId": 201 if name == "alpha" else 202,
                    "installedVersion": "1.0", "targetVersion": "2.0",
                    "reviewed": True, "riskAcknowledged": True} for name in items]
        return self.service.queue_reviewed_downloads({
            "profileId": SNAP["profileId"], "confirm": "QUEUE_REVIEWED_DOWNLOADS",
            "entries": entries,
        })

    def until(self, condition, seconds=2):
        expires = time.monotonic() + seconds
        while time.monotonic() < expires:
            if condition():
                return
            time.sleep(.01)
        self.fail("Timed out waiting for MO2 queue transition")

    def test_instant_completion_callback_is_not_lost_before_download_id_returns(self):
        self.emit_early = "complete"
        result = self.queue()
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.service.downloads()[901]["status"], "complete")
        self.assertEqual(self.service.activity()["records"][0]["status"], "complete")
        self.assertFalse(self.install_calls)
        self.assertEqual(self.service._early_download_events, {})

    def test_instant_failed_callback_survives_for_retry(self):
        self.emit_early = "failed"
        self.assertTrue(self.queue()["ok"])
        self.assertEqual(self.service.downloads()[901]["status"], "failed")
        self.emit_early = ""
        result = self.service.retry_failed_download(901)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["queued"][0]["downloadId"], 902)
        self.assertEqual(self.service.downloads()[901]["retriedBy"], 902)
        self.assertEqual(self.service.downloads()[902]["status"], "downloading")
        self.assertFalse(self.service.retry_failed_download(901)["ok"])  # Already requeued.
        self.assertEqual(len(self.download_calls), 2)

    def test_retry_requires_same_profile_version_and_exact_reviewed_file(self):
        self.assertTrue(self.queue()["ok"])
        self.service.set_download_status(901, "failed")
        stale = copy.deepcopy(SNAP)
        stale["items"][0]["installedVersion"] = "1.5"
        self.service.set_snapshot(stale)
        rejected = self.service.retry_failed_download(901)
        self.assertFalse(rejected["ok"])
        self.assertIn("changed", rejected["error"])
        self.assertEqual(len(self.download_calls), 1)
        stale["profileId"] = "other-profile"
        self.service.set_snapshot(stale)
        self.assertFalse(self.service.retry_failed_download(901)["ok"])

    def test_early_native_callback_for_github_download_is_replayed(self):
        self.emit_early = "complete"
        reply = self.service.queue_reviewed_downloads({
            "profileId": SNAP["profileId"], "confirm": "QUEUE_REVIEWED_DOWNLOADS",
            "entries": [{"kind": "source", "itemId": "github", "assetId": 17,
                         "installedVersion": "1.0", "targetVersion": "2.0",
                         "reviewed": True, "riskAcknowledged": True}],
        })
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(self.service.downloads()[901]["status"], "complete")
        self.assertEqual(self.service.downloads()[901]["provider"], "github")

    def test_explicit_install_all_ready_runs_one_native_dialog_at_a_time(self):
        self.assertTrue(self.queue(("alpha", "beta"))["ok"])
        self.service.set_download_status(901, "complete")
        self.service.set_download_status(902, "complete")
        result = self.service.install_ready_queue()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["armed"], 2)
        self.until(lambda: self.install_calls == [901])
        self.assertEqual(self.service.downloads()[902]["status"], "complete")
        self.gates[901].set()
        self.until(lambda: self.install_calls == [901, 902])
        self.assertEqual(self.service.downloads()[902]["status"], "installing")
        self.gates[902].set()
        self.until(lambda: self.service.downloads()[902]["status"] == "installed")
        self.assertEqual(self.install_calls, [901, 902])
        self.assertFalse(self.service.install_ready_queue()["armed"])

    def test_install_all_skips_changed_runtime_without_blocking_manual_review(self):
        self.assertTrue(self.queue(("alpha", "beta"))["ok"])
        for did in (901, 902):
            self.service.set_download_status(did, "complete")
        changed = copy.deepcopy(SNAP)
        changed["gameRuntime"] = "1.6.1170"
        self.service.set_snapshot(changed)
        reply = self.service.install_ready_queue()
        self.assertEqual(reply["armed"], 0)
        self.assertEqual(len(reply["blocked"]), 2)
        self.assertFalse(self.install_calls)
        self.assertEqual(self.service.downloads()[901]["status"], "complete")

    def test_no_bogus_signals_are_remembered_when_no_native_dispatch_exists(self):
        self.service.set_download_status(99999, "complete")
        self.assertFalse(self.service._early_download_events)
        self.assertTrue(self.queue()["ok"])
        self.assertEqual(self.service.downloads()[901]["status"], "downloading")

    def test_hundreds_of_invalid_ready_tickets_do_not_recurse(self):
        self.service.journal.record = lambda *args: True
        self.service._downloads = {
            did: {"profileId": SNAP["profileId"], "itemId": "alpha",
                  "status": "complete", "autoInstallRequested": True,
                  "installedVersion": "1.0", "targetVersion": "2.0",
                  "gameRuntime": "different-runtime"} for did in range(1, 1101)
        }
        self.service._maybe_autoinstall()
        self.assertFalse(self.install_calls)
        self.assertFalse(any(t["autoInstallRequested"] for t in self.service._downloads.values()))


if __name__ == "__main__":
    unittest.main()
