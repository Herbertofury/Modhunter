"""Operational update-history regression tests: durability without automatic resume."""
from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from pathlib import Path

from mo2_update_arsenal.operation_journal import OperationJournal
from mo2_update_arsenal.bridge import ArsenalService


def ticket(profile="p1", operation="op1"):
    return {
        "operationId": operation, "profileId": profile, "profileName": "My MO2 Profile",
        "game": "skyrimspecialedition", "gameRuntime": "1.5.97",
        "itemId": "live-1", "modName": "Animations", "installedVersion": "1.0",
        "targetVersion": "2.0", "provider": "nexus", "modId": 15, "fileId": 48,
        "fileName": "Animations-2.0.7z", "reviewed": True,
        "status": "downloading",
        "apiKey": "SECRET_TOKEN_MUST_NEVER_SAVE",
        "url": "https://private.example/download?token=NO_SAVE",
        "backupPath": "C:/Users/private/SecretBackups",
    }


SNAPSHOT = {
    "profileId": "p1", "profileName": "My MO2 Profile",
    "game": "skyrimspecialedition", "gameRuntime": "1.5.97",
    "groups": [], "items": [{
        "id": "live-1", "name": "Animations", "mo2InternalName": "Animations",
        "installedVersion": "1.0", "nexusId": 15, "flags": [],
        "update": {"verdict": "candidate", "latestVersion": "2.0"},
    }],
}


class OperationJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "operations.jsonl"

    def tearDown(self):
        self.temp.cleanup()

    def test_restart_shows_history_but_no_actionable_or_auto_resumed_downloads(self):
        j1 = OperationJournal(self.path, "first-session")
        self.assertTrue(j1.record(ticket(), 11))
        self.assertTrue(j1.record({**ticket(), "status": "complete"}, 11))
        active = j1.profile("p1")["records"][0]
        self.assertTrue(active["sessionActive"])
        self.assertFalse(active["actionable"])
        self.assertEqual([x["status"] for x in active["timeline"]],
                         ["downloading", "complete"])
        j2 = OperationJournal(self.path, "next-session")
        result = j2.profile("p1")
        self.assertEqual(result["count"], 1)
        previous = result["records"][0]
        self.assertFalse(previous["sessionActive"])
        self.assertTrue(previous["needsReview"])
        self.assertFalse(previous["actionable"])
        self.assertEqual(previous["fileName"], "Animations-2.0.7z")

    def test_no_secrets_paths_or_duplicate_callbacks_and_profile_isolation(self):
        j = OperationJournal(self.path, "session")
        t = ticket()
        self.assertTrue(j.record(t, 11))
        self.assertTrue(j.record(t, 11))
        self.assertTrue(j.record(ticket("p2", "op2"), 12))
        text = self.path.read_text("utf-8")
        for secret in ("SECRET_TOKEN", "private.example", "SecretBackups"):
            self.assertNotIn(secret, text)
        self.assertEqual(len(text.splitlines()), 2)
        self.assertEqual(j.profile("p1")["count"], 1)
        self.assertEqual(j.profile("p2")["count"], 1)
        self.assertEqual(j.profile("p3")["count"], 0)

    def test_cancel_install_and_verified_receipt_are_distinct(self):
        journal = OperationJournal(self.path, "session")
        original = ticket()
        for state in ("downloading", "complete", "installing", "complete", "installing", "installed"):
            value = {**original, "status": state,
                     "lastError": "MO2 installer canceled" if state == "complete" else ""}
            if state == "installed":
                value.update({"verifiedUpdate": False, "backupPath": "C:/hidden/backup"})
            self.assertTrue(journal.record(value, 1))
        result = journal.profile("p1")["records"][0]
        self.assertEqual(result["status"], "installed")
        self.assertFalse(result["verifiedUpdate"])
        self.assertTrue(result["hasProfileBackup"])
        self.assertIn("installing", [x["status"] for x in result["timeline"]])
        self.assertNotIn("C:/hidden", self.path.read_text("utf-8"))
        after_restart = OperationJournal(self.path, "another").profile("p1")["records"][0]
        self.assertFalse(after_restart["needsReview"])  # Already installed, not automatically actionable.

    def test_repair_torn_last_line_without_losing_valid_receipts(self):
        j = OperationJournal(self.path, "old")
        j.record(ticket(), 5)
        with self.path.open("a", encoding="utf-8") as f:
            f.write('{"broken":')
        reboot = OperationJournal(self.path, "new")
        self.assertEqual(reboot.profile("p1")["count"], 1)
        self.assertTrue(reboot.profile("p1")["records"][0]["needsReview"])
        reboot.record(ticket(operation="next"), 6)
        restored = OperationJournal(self.path, "third")
        self.assertEqual(restored.profile("p1")["count"], 2)

    def test_identical_callback_retries_failed_durable_write(self):
        journal = OperationJournal(self.path, "session")
        with patch.object(Path, "open", side_effect=OSError("fixture write unavailable")):
            self.assertFalse(journal.record(ticket(), 11))
        self.assertFalse(journal.profile("p1")["durable"])
        self.assertTrue(journal.record(ticket(), 11))
        self.assertTrue(journal.profile("p1")["durable"])
        restored = OperationJournal(self.path, "next").profile("p1")["records"]
        self.assertEqual(len(restored), 1)
        self.assertEqual(len(restored[0]["timeline"]), 1)
        self.assertTrue(restored[0]["hasProfileBackup"])
        self.assertFalse(restored[0]["actionable"])

    def test_next_transition_preserves_failed_prior_transition(self):
        journal = OperationJournal(self.path, "session")
        with patch.object(Path, "open", side_effect=OSError("fixture write unavailable")):
            self.assertFalse(journal.record(ticket(), 11))
        self.assertTrue(journal.record({**ticket(), "status": "complete"}, 11))
        restored = OperationJournal(self.path, "next").profile("p1")["records"][0]
        self.assertEqual([event["status"] for event in restored["timeline"]],
                         ["downloading", "complete"])

    def test_uncertain_fsync_retry_does_not_duplicate_replayed_transitions(self):
        journal = OperationJournal(self.path, "session")
        with patch("mo2_update_arsenal.operation_journal.os.fsync", side_effect=OSError("fixture fsync failure")):
            self.assertFalse(journal.record(ticket(), 11))
        self.assertTrue(journal.record(ticket(), 11))
        self.assertTrue(journal.profile("p1")["durable"])
        restored = OperationJournal(self.path, "next").profile("p1")["records"][0]
        self.assertEqual(len(restored["timeline"]), 1)
        self.assertTrue(restored["hasProfileBackup"])
        text = self.path.read_text("utf-8")
        for secret in ("SECRET_TOKEN", "private.example", "SecretBackups"):
            self.assertNotIn(secret, text)

    def test_legacy_receipts_without_event_ids_keep_backup_fact(self):
        journal = OperationJournal(self.path, "session")
        self.assertTrue(journal.record(ticket(), 11))
        event = json.loads(self.path.read_text("utf-8"))
        event.pop("eventId", None)
        self.path.write_text(json.dumps(event) + "\n", "utf-8")
        restored = OperationJournal(self.path, "next").profile("p1")["records"][0]
        self.assertTrue(restored["hasProfileBackup"])
        self.assertFalse(restored["actionable"])
        self.assertTrue(restored["needsReview"])

    def test_full_history_has_no_result_cap(self):
        j = OperationJournal(self.path, "session")
        for i in range(120):
            j.record(ticket(operation="op" + str(i)), i + 1)
        self.assertEqual(j.profile("p1")["count"], 120)


class JournalBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dispatches = []
        def native(req):
            self.dispatches.append(req.cmd)
            if req.cmd == "install":
                req.finish({"ok": True, "verifiedUpdate": False,
                            "backup": {"backupPath": "/private/backups"}})
            else:
                req.finish({"ok": False, "error": "Not supported in this test"})
        self.path = Path(self.temp.name) / "cache.json"
        self.service = ArsenalService("<html></html>", self.path, native, SNAPSHOT)

    def tearDown(self):
        self.service.stop()
        self.temp.cleanup()

    def test_native_state_progress_is_logged_and_restart_never_installs_history(self):
        self.service._downloads[9] = ticket()
        self.assertTrue(self.service.journal.record(self.service._downloads[9], 9))
        self.service.set_download_status(9, "complete")
        self.assertEqual(self.service.activity()["records"][0]["status"], "complete")
        self.assertTrue(self.service.install_reviewed_download(9)["ok"])
        deadline = time.monotonic() + 2
        while (not self.service.activity()["records"]
               or self.service.activity()["records"][0]["status"] != "installed"):
            if time.monotonic() >= deadline:
                self.fail("Native MO2 completion was not journaled")
            time.sleep(.01)
        history = self.service.activity()["records"]
        self.assertEqual(history[0]["status"], "installed")
        self.assertFalse(history[0]["verifiedUpdate"])
        self.assertEqual(self.dispatches, ["install"])
        self.assertNotIn("/private/backups", self.service.journal.path.read_text("utf-8"))
        second = ArsenalService("<html></html>", self.path, lambda req: None, SNAPSHOT)
        try:
            self.assertEqual(second.downloads(), {})
            self.assertEqual(second.activity()["count"], 1)
            self.assertFalse(second.activity()["records"][0]["actionable"])
            self.assertFalse(second.install_reviewed_download(9)["ok"])
        finally:
            second.stop()

    def test_native_download_status_is_not_confused_by_profile_switch(self):
        self.service._downloads[9] = ticket()
        self.service.set_download_status(9, "paused")
        self.assertEqual(self.service.activity()["records"][0]["status"], "paused")
        changed = {**SNAPSHOT, "profileId": "p2", "profileName": "Other"}
        self.service.set_snapshot(changed)
        self.assertEqual(self.service.activity()["count"], 0)


if __name__ == "__main__":
    unittest.main()
