"""Same-version archive approvals stay bound to their installed Nexus file.

These are service/queue fixtures, not claims of native MO2 execution.
"""
from __future__ import annotations

import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from mo2_update_arsenal.bridge import ArsenalService
from mo2_update_arsenal.install_brief import build_install_brief
from mo2_update_arsenal.nexus_files import FileLineage, installed_file_context, normalize_file_payload
from mo2_update_arsenal.scanner import summary_from_metadata
from nexus_fixture import GAME


def profile(installed=101, target=201, *, version="1.0.1", latest="1.0.1"):
    row = {"id": "live-patch", "name": "Optional patch", "mo2InternalName": "Optional patch",
           "nexusId": 1, "installedVersion": version,
           "installedFiles": [{"modId": 1, "fileId": installed}],
           "source": {"type": "nexus", "verified": True,
                      "url": f"https://www.nexusmods.com/{GAME}/mods/1"}}
    files = {"ok": True, "checkedAt": time.time(), **normalize_file_payload({
        "files": [
            {"file_id": installed, "file_name": f"patch-{installed}.7z", "version": version, "category_id": 4},
            {"file_id": target, "file_name": f"patch-{target}.7z", "version": latest, "category_id": 3},
        ], "file_updates": [{"old_file_id": installed, "new_file_id": target}],
    }, GAME, 1)}
    row["update"] = summary_from_metadata(row, {}, files["checkedAt"], FileLineage(files, GAME, 1))
    return {"profileId": "optional", "profileName": "Optional", "game": GAME,
            "gameRuntime": "1.5.97", "groups": [], "items": [row]}, files


def ticket(snapshot, *, status="complete"):
    row = snapshot["items"][0]
    return {"provider": "nexus", "profileId": snapshot["profileId"], "profileName": snapshot["profileName"],
            "game": snapshot["game"], "gameRuntime": snapshot["gameRuntime"],
            "itemId": row["id"], "modId": row["nexusId"], "status": status, "reviewed": True,
            "fileId": row["update"]["fileEvidence"]["targetFileId"],
            "installedVersion": row["installedVersion"], "targetVersion": row["update"]["latestVersion"],
            "installedFileIdentity": installed_file_context(row), "riskReasons": []}


class NexusInstallIdentityTests(unittest.TestCase):
    def test_identity_is_json_stable_and_independent_of_duplicate_record_order(self):
        row = {"installedFiles": [{"modId": 1, "fileId": 102}, {"modId": 1, "fileId": 101},
                                  {"modId": 1, "fileId": 102}],
               "installationFile": r"D:\Downloads\patch.7z", "ignoredVersion": "2.0"}
        identity = installed_file_context(row)
        self.assertEqual(identity, json.loads(json.dumps(identity)))
        self.assertEqual(identity, installed_file_context({**row, "installedFiles": list(reversed(row["installedFiles"]))}))
        self.assertEqual(identity["files"], [{"modId": 1, "fileId": 101}, {"modId": 1, "fileId": 102}])
        self.assertEqual(identity["archiveName"], "patch.7z")

    def test_same_version_different_installed_file_rejects_old_archive_approval(self):
        before, _ = profile()
        after, _ = profile(301, 302)
        approved = ticket(before)
        self.assertTrue(build_install_brief(before, approved, 7)["canInstall"])
        refused = build_install_brief(after, approved, 7)
        self.assertFalse(refused["canInstall"])
        self.assertTrue(any("file identity changed" in error for error in refused["errors"]))

    def test_archive_name_fallback_and_ignored_choice_are_pinned(self):
        before, _ = profile()
        row = before["items"][0]
        row["installedFiles"] = []
        row["installationFile"] = "selected-patch.7z"
        approved = ticket(before)
        for changes in ({"installationFile": "different-patch.7z"}, {"ignoredVersion": "1.0.1"}):
            with self.subTest(changes=changes):
                after = copy.deepcopy(before)
                after["items"][0].update(changes)
                self.assertFalse(build_install_brief(after, approved, 7)["canInstall"])

    def test_missing_or_mutated_receipt_does_not_borrow_current_file_identity(self):
        snapshot, _ = profile()
        approved = ticket(snapshot)
        for value in (None, {}, {"files": [{"modId": 1, "fileId": 999}], "archiveName": "", "ignoredVersion": ""}):
            with self.subTest(value=value):
                changed = {**approved, "installedFileIdentity": value}
                self.assertFalse(build_install_brief(snapshot, changed, 7)["canInstall"])

    def test_completed_same_version_replacement_does_not_block_next_file_lineage(self):
        before, _ = profile()
        after, _ = profile(201, 202, latest="1.0.2")
        completed = ticket(before, status="installed")
        self.assertTrue(ArsenalService._ticket_blocks_update(completed, before["items"][0]))
        self.assertFalse(ArsenalService._ticket_blocks_update(completed, after["items"][0]))
        self.assertEqual(completed["status"], "installed")
        self.assertEqual(completed["fileId"], 201)


class NexusInstallHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.calls = []
        self.snapshot, self.files = profile()
        self.before_reply = lambda request: None

        def native(request):
            self.calls.append(request.cmd)
            self.before_reply(request)
            request.finish({"ok": True, "downloadId": 7} if request.cmd == "download" else
                           {"ok": False, "error": "An install must not reach this fixture"})

        self.service = ArsenalService("", Path(self.temp.name) / "cache.json", native, copy.deepcopy(self.snapshot))
        self.service.fetch_files = lambda mid: copy.deepcopy(self.files)

    def tearDown(self):
        self.service.stop()
        deadline = time.monotonic() + 2
        while self.service._install_lock.locked() and time.monotonic() < deadline:
            time.sleep(.005)
        self.temp.cleanup()

    def download(self):
        context = self.service._download_context(self.service.selection_snapshot(["live-patch"]), self.snapshot["items"][0])
        return self.service.request_download(1, 201, "DOWNLOAD", "live-patch", "1.0.1", reviewed_context=context)

    def test_native_handoff_pins_identity_before_a_synchronous_inventory_change(self):
        original = installed_file_context(self.snapshot["items"][0])
        after, _ = profile(301, 302)
        self.before_reply = lambda request: self.service.set_snapshot(copy.deepcopy(after))
        result = self.download()
        self.assertTrue(result["ok"], result)
        recorded = self.service.downloads()[7]
        self.assertEqual(recorded["installedFileIdentity"], original)
        self.service.set_download_status(7, "complete")
        self.assertFalse(self.service.preview_install(7)["canInstall"])
        self.assertEqual(self.calls, ["download"])

    def test_same_version_target_change_invalidates_delayed_native_review_context(self):
        live = self.service.selection_snapshot(["live-patch"])
        reviewed = self.service._download_context(live, live["items"][0])
        newer, _ = profile(101, 202)
        with self.service._guard:
            self.service._items_by_id["live-patch"]["update"] = newer["items"][0]["update"]
        self.assertFalse(self.service.download_context_matches(reviewed))
        result = self.service._start_native_download("download", {"reviewedContext": reviewed}, ticket(self.snapshot))
        self.assertFalse(result["ok"])
        self.assertFalse(self.calls)

    def test_unchanged_nexus_evidence_cannot_cross_expiry_while_waiting_for_qt(self):
        live = self.service.selection_snapshot(["live-patch"])
        reviewed = self.service._download_context(live, live["items"][0])
        self.assertTrue(self.service.download_context_matches(reviewed))
        expires = live["items"][0]["update"]["expiresAt"]
        with patch("mo2_update_arsenal.update_plan.time.time", return_value=expires):
            self.assertFalse(self.service.download_context_matches(reviewed))
            result = self.service._start_native_download("download", {"reviewedContext": reviewed}, ticket(self.snapshot))
        self.assertFalse(result["ok"])
        self.assertFalse(self.calls)

    def test_waiting_installer_cannot_borrow_new_same_version_installed_file(self):
        self.assertTrue(self.download()["ok"])
        with self.service._guard:
            self.service._downloads[7].update(targetVersion="1.0.1", reviewed=True)
            self.service._install_mode = "paused"
        self.service.set_download_status(7, "complete")
        self.assertTrue(self.service.install_reviewed_download(7)["ok"])
        after, _ = profile(301, 302)
        self.service.set_snapshot(after)
        with self.service._guard:
            self.service._install_mode = "running"
        self.service._drain_install_queue()
        deadline = time.monotonic() + 2
        while not self.service.install_queue()["history"] and time.monotonic() < deadline:
            time.sleep(.005)
        history = self.service.install_queue()["history"]
        self.assertEqual(len(history), 1, self.service.install_queue())
        self.assertFalse(history[0]["ok"])
        self.assertTrue(history[0]["needsReview"])
        self.assertEqual(self.calls, ["download"])


if __name__ == "__main__":
    unittest.main()
