"""Operational update-history regression tests: durability without automatic resume."""
from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
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

    def test_retry_clears_current_error_even_without_a_status_change(self):
        journal = OperationJournal(self.path, "current")
        ready = {**ticket(), "status": "complete"}
        canceled = {**ready, "lastError": "MO2 installer canceled in C:/fixture/private"}
        self.assertTrue(journal.record(canceled, 11))
        cancellation_bytes = self.path.read_bytes()
        self.assertTrue(journal.record(canceled, 11))
        self.assertEqual(self.path.read_bytes(), cancellation_bytes)
        self.assertIn("lastError", journal.profile("p1")["records"][0])
        self.assertTrue(journal.record(ready, 11))
        self.assertTrue(journal.record(ready, 11))  # Duplicate callback stays deduplicated.
        timeline = journal.profile("p1")["records"][0]["timeline"]
        self.assertEqual([e["note"] for e in timeline], ["MO2 installer canceled; review before retrying", ""])
        self.assertNotIn("lastError", journal.profile("p1")["records"][0])
        before = self.path.read_bytes()
        self.assertEqual(len(before.splitlines()), 2)
        self.assertTrue(before.startswith(cancellation_bytes), "Retry is appended, not a rewrite")
        self.assertNotIn(b"fixture/private", before)
        reopened = OperationJournal(self.path, "new").profile("p1")["records"][0]
        self.assertFalse(reopened["actionable"])
        self.assertTrue(reopened["needsReview"])
        self.assertEqual(reopened["timeline"], timeline)
        self.assertNotIn("lastError", reopened)
        self.assertEqual(self.path.read_bytes(), before)

    def test_legacy_success_event_clears_current_error_but_retains_historical_note(self):
        archive = {**ticket(), "status": "complete", "lastError": "MO2 installer canceled"}
        journal = OperationJournal(self.path, "legacy")
        self.assertTrue(journal.record(archive, 11))
        # Legacy JSONL did not have explicit nulls for absent errors; preserve
        # both original events, including the previous cancel note, on read.
        event = json.loads(self.path.read_text("utf-8"))
        previous = {**event, "at": "2026-11-24T12:03:00+00:00"}
        previous.pop("eventId")
        new_ticket = {**event["ticket"], "status": "installed", "verifiedUpdate": True}
        new_ticket.pop("lastError")
        installed = {**previous, "ticket": new_ticket, "at": "2026-11-24T12:04:00+00:00"}
        original = ("\n".join(json.dumps(e) for e in [previous, installed])+"\n").encode()
        self.path.write_bytes(original)
        recovered = OperationJournal(self.path, "new").profile("p1")["records"][0]
        self.assertNotIn("lastError", recovered)
        self.assertEqual([e["status"] for e in recovered["timeline"]], ["complete", "installed"])
        self.assertEqual([e["note"] for e in recovered["timeline"]], ["MO2 installer canceled; review before retrying", ""])
        self.assertEqual((recovered["status"], recovered["verifiedUpdate"], recovered["fileId"]), ("installed", True, 48))
        self.assertFalse(recovered["needsReview"])
        self.assertFalse(recovered["actionable"])
        self.assertEqual(self.path.read_bytes(), original)

    def test_archive_identity_does_not_admit_fomod_looking_metadata_on_record_or_reload(self):
        journal = OperationJournal(self.path, "first-session")
        original = {**ticket(), "status": "installed", "verifiedUpdate": True,
                    "fileName": "Option-Sounding-Name.7z"}
        unbound = {"fomodChoices": {"state": "captured", "steps": ["Claim without a bound run"]},
                   "installerMetadata": {"FOMOD Plus": {"selected": ["Historical selection"]}}}
        self.assertTrue(journal.record({**original, **unbound}, 11))
        event = json.loads(self.path.read_text("utf-8"))
        self.assertTrue(set(unbound).isdisjoint(event["ticket"]))
        # Synthetic legacy/foreign fields on disk: project none as authoritative
        # choice evidence, but never rewrite the old receipt just by reading it.
        event["ticket"].update(unbound)
        line = json.dumps(event) + "\n"
        self.path.write_text(line, encoding="utf-8")
        reopened = OperationJournal(self.path, "next-session")
        record = reopened.profile("p1")["records"][0]
        self.assertTrue(set(unbound).isdisjoint(record))
        self.assertEqual((record["fileName"], record["downloadId"], record["modId"], record["fileId"]),
                         ("Option-Sounding-Name.7z", 11, 15, 48))
        self.assertEqual(reopened.nexus_archive_for_review("p1", "skyrimspecialedition", "1.5.97",
                                                            "live-1", 15, "2.0"), 48)
        self.assertIsNone(reopened.nexus_archive_for_review("other-profile", "skyrimspecialedition",
                                                            "1.5.97", "live-1", 15, "2.0"))
        self.assertFalse(record["actionable"])
        self.assertEqual(self.path.read_text("utf-8"), line)

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

    def test_torn_utf8_tail_preserves_receipts_and_allows_future_append(self):
        journal = OperationJournal(self.path, "old")
        original = {**ticket(), "modName": "Étoile 雪"}
        self.assertTrue(journal.record(original, 5))
        with self.path.open("ab") as stream:
            stream.write(b'{"ticket":{"modName":"\xe9\x9b')
        reboot = OperationJournal(self.path, "new")
        prior = reboot.profile("p1")["records"]
        self.assertEqual([row["modName"] for row in prior], ["Étoile 雪"])
        self.assertFalse(prior[0]["actionable"])
        self.assertTrue(reboot.record(ticket(operation="next"), 6))
        restored = OperationJournal(self.path, "third").profile("p1")
        self.assertEqual(restored["count"], 2)
        self.assertTrue(all(not row["actionable"] for row in restored["records"]))

    def test_invalid_utf8_middle_line_does_not_hide_later_valid_receipts(self):
        journal = OperationJournal(self.path, "old")
        self.assertTrue(journal.record(ticket(), 5))
        with self.path.open("ab") as stream:
            stream.write(b'{"invalid":"\xff"}\n')
        self.assertTrue(journal.record(ticket("p2", "second"), 6))
        restored = OperationJournal(self.path, "new")
        self.assertEqual(restored.profile("p1")["count"], 1)
        self.assertEqual(restored.profile("p2")["count"], 1)

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

    def test_activity_does_not_export_old_cancellation_after_real_retry_controller(self):
        results = iter([
            {"ok": False, "error": "MO2 installer canceled at C:/fixture/FOMOD?token=PRIVATE_TEST",
             "backup": {"backupPath": "C:/fixture/backup"}},
            {"ok": True, "verifiedUpdate": True, "installedName": "Annotations",
             "backup": {"backupPath": "C:/fixture/backup"}},
        ])
        def dispatch(request):
            if request.cmd != "install":
                request.finish({"ok": False, "error": "Not supported in synthetic fixture"})
                return
            self.dispatches.append(request.cmd)
            request.finish(next(results))

        self.service._submit_ui = dispatch
        self.service._downloads[11] = ticket()
        self.assertTrue(self.service.journal.record(self.service._downloads[11], 11))
        self.service.set_download_status(11, "complete")
        self.service.start()
        def activity():
            connection = HTTPConnection("127.0.0.1", self.service._server.server_port, timeout=5)
            try:
                connection.request("GET", "/api/activity", headers={"X-Arsenal-Token": self.service.token})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                return json.loads(response.read())
            finally:
                connection.close()
        def await_record(condition):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                result = activity()
                self.assertEqual(result["profileId"], "p1")
                receipt = result["records"][0]
                if condition(receipt):
                    return receipt
                time.sleep(.01)
            self.fail("Synthetic install result was not written to Update Timeline")
        self.assertTrue(self.service.install_reviewed_download(11)["ok"])
        canceled = await_record(lambda record: record["status"] == "complete" and bool(record.get("lastError")))
        self.assertEqual(canceled["lastError"], "MO2 installer canceled; review before retrying")
        self.assertTrue(self.service.install_reviewed_download(11)["ok"])
        final = await_record(lambda record: record["status"] == "installed")
        self.assertEqual(self.dispatches, ["install", "install"])
        self.assertNotIn("lastError", final)
        self.assertEqual((final["verifiedUpdate"], final["fileId"], final["hasProfileBackup"]), (True, 48, True))
        self.assertFalse(final["actionable"])
        self.assertEqual([e["note"] for e in final["timeline"] if e["note"]], [canceled["lastError"]])
        data = self.service.journal.path.read_bytes()
        for value in (b"PRIVATE_TEST", b"C:/fixture/backup", b"C:/fixture/FOMOD"):
            self.assertNotIn(value, data)
            self.assertNotIn(value.decode(), json.dumps(final))

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
