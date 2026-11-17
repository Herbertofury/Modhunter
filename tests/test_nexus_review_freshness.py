"""Fresh file history, retained last-good data and exact open-review ownership."""
from __future__ import annotations

import copy
import tempfile
import time
import unittest
from pathlib import Path

from mo2_update_arsenal.bridge import ArsenalService
from mo2_update_arsenal.nexus_files import FileLineage, normalize_file_payload
from mo2_update_arsenal.scanner import Cache, NexusClient, UpdateScanner, summary_from_metadata
from mo2_update_arsenal.update_plan import nexus_file_proof, validate_batch_selection, PlanError
from nexus_fixture import GAME, file_item, files_payload


class FreshProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Cache(Path(self.tmp.name) / "cache.json")
        self.snapshot = {"profileId": "review", "game": GAME, "items": [file_item()]}
        self.raw = files_payload()
        self.metadata = self.raw.pop("meta")
        self.metadata.update(picture_url="https://staticdelivery.nexusmods.com/mods/1704/images/1-2.png",
                             author="Actual author", summary="Complete project details", status="published")
        self.calls = []

    def provider(self, path):
        self.calls.append(path)
        return copy.deepcopy(self.raw if path.endswith("/files.json") else self.metadata)

    def scanner(self):
        return UpdateScanner(self.cache, NexusClient("fixture", self.provider))

    def test_cold_requests_include_project_artwork_and_warm_recheck_preserves_it(self):
        cold = self.scanner().scan(self.snapshot, "startup")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual({path.rsplit("/", 1)[-1] for path in self.calls}, {"files.json", "1.json"})
        self.calls.clear()
        warm = self.scanner().scan(self.snapshot, "startup")
        self.assertFalse(self.calls)
        for result in (cold, warm):
            self.assertEqual(result["itemRecords"]["1"]["author"]["name"], "Actual author")
            self.assertEqual(result["itemRecords"]["1"]["thumbnailUrl"], self.metadata["picture_url"])
        self.assertEqual(self.cache.get(GAME, 1)["meta"]["summary"], "Complete project details")
        self.scanner().scan(self.snapshot, "deep")
        self.assertEqual(len(self.calls), 2)

    def test_only_expired_metadata_is_refetched_without_renewing_file_evidence(self):
        self.scanner().scan(self.snapshot, "startup")
        entry = self.cache.get(GAME, 1)
        old_file_time = entry["checkedAt"]
        entry["metaCheckedAt"] = time.time() - 5 * 3600
        self.cache.set(GAME, 1, entry)
        self.calls.clear()
        result = self.scanner().scan(self.snapshot, "startup")
        self.assertEqual(self.calls, [f"/v1/games/{GAME}/mods/1.json"])
        self.assertEqual(result["itemRecords"]["1"]["checkedAt"], old_file_time)

    def test_published_project_without_project_version_retains_artwork_and_exact_file_verdict(self):
        self.metadata.pop("version")
        result = self.scanner().scan(self.snapshot, "startup")
        self.assertFalse(result["errors"])
        self.assertEqual(result["itemRecords"]["1"]["verdict"], "candidate")
        self.assertEqual(result["itemRecords"]["1"]["thumbnailUrl"], self.metadata["picture_url"])

    def test_unavailable_project_metadata_does_not_authorize_cached_active_file(self):
        for status in ({"status": "hidden"}, {"available": False}):
            self.metadata["status"] = "published"
            self.metadata.pop("available", None)
            self.metadata.update(status)
            result = self.scanner().scan(self.snapshot, "deep")
            self.assertEqual(result["itemRecords"]["1"]["verdict"], "review")

    def test_incomplete_refresh_retains_good_cache_but_forces_next_check_to_retry(self):
        self.scanner().scan(self.snapshot, "startup")
        previous = copy.deepcopy(self.cache.get(GAME, 1))
        self.raw["files"] = [None]
        failed = self.scanner().scan(self.snapshot, "deep")
        retained = self.cache.get(GAME, 1)
        self.assertTrue(failed["errors"])
        self.assertEqual(failed["itemRecords"]["1"]["availability"], "unavailable")
        self.assertEqual(failed["itemRecords"]["1"]["thumbnailUrl"], self.metadata["picture_url"])
        for key in ("files", "meta", "checkedAt", "metaCheckedAt"):
            self.assertEqual(retained[key], previous[key])
        self.assertTrue(retained["lastFailure"])
        self.calls.clear()
        failed_again = self.scanner().scan(self.snapshot, "startup")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(failed_again["itemRecords"]["1"]["verdict"], "unknown")
        self.raw = files_payload(); self.raw.pop("meta")
        self.scanner().scan(self.snapshot, "startup")
        self.assertNotIn("lastFailure", self.cache.get(GAME, 1))


class FreshReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.payload = normalize_file_payload(files_payload(), GAME, 1)
        self.row = file_item(ident="live-one")
        self.row["update"] = summary_from_metadata(self.row, {"version": "2.0"}, time.time(),
                                                   FileLineage(self.payload, GAME, 1))
        self.snapshot = {"profileId": "review", "game": GAME, "items": [self.row], "groups": []}
        self.downloads = []

        def native(request):
            if request.cmd == "nexus-files":
                request.finish({"ok": True, **self.payload})
            elif request.cmd == "download":
                self.downloads.append(request.payload["fileId"])
                request.finish({"ok": True, "downloadId": 9})
            else:
                request.finish({"ok": False, "error": "Unexpected fixture command"})

        self.service = ArsenalService("", Path(self.tmp.name) / "cache.json", native, self.snapshot)
        self.service.native_nexus_available = self.service.native_nexus_files_available = True
        self.addCleanup(self.service.stop)

    def changed_target(self):
        raw = files_payload()
        raw["files"].append({**raw["files"][-1], "file_id": 202, "file_name": "replacement-new.7z"})
        raw["file_updates"] = [{"old_file_id": 101, "new_file_id": 202}]
        self.payload = normalize_file_payload(raw, GAME, 1)

    def choice(self, snapshot=None, file_id=201):
        snapshot = snapshot or self.service.selection_snapshot(["live-one"])
        return {"itemId": "live-one", "modId": 1, "fileId": file_id,
                "installedVersion": "1.0", "targetVersion": "2.0", "reviewed": True,
                "riskAcknowledged": True, "fileProof": nexus_file_proof(snapshot, snapshot["items"][0])}

    def packet(self, selection):
        return {"profileId": "review", "confirm": "QUEUE_REVIEWED_DOWNLOADS", "entries": [selection]}

    def test_quick_update_uses_new_exact_target_after_old_evidence_expires(self):
        self.changed_target()
        self.service._items_by_id["live-one"]["update"]["checkedAt"] -= 5 * 3600
        self.service._items_by_id["live-one"]["update"]["expiresAt"] -= 5 * 3600
        result = self.service.quick_update({"profileId": "review", "itemIds": ["live-one"]})
        self.assertEqual(self.downloads, [202], result)
        self.assertEqual(self.service.snapshot()["items"][0]["update"]["fileEvidence"]["targetFileId"], 202)

    def test_batch_rejects_changed_target_even_if_old_file_remains_active_and_same_version(self):
        selection = self.choice()
        self.changed_target()
        result = self.service.queue_reviewed_downloads(self.packet(selection))
        self.assertFalse(result["ok"], result)
        self.assertFalse(self.downloads)
        self.assertIn("replacement history changed", result["failed"][0]["error"])

    def test_expired_missing_and_nonfinite_timestamps_never_authorize_batch(self):
        for changes in ({"checkedAt": time.time() - 5 * 3600}, {"expiresAt": time.time() - 1},
                        {"checkedAt": float("nan")}, {"expiresAt": float("inf")},
                        {"checkedAt": None}, {"freshness": "stale"}):
            snapshot = copy.deepcopy(self.snapshot)
            snapshot["items"][0]["update"].update(changes)
            with self.subTest(changes=changes), self.assertRaises(PlanError):
                validate_batch_selection(snapshot, self.packet(self.choice(snapshot)))

    def test_open_manual_review_does_not_survive_same_version_installed_archive_change(self):
        selection = self.choice()
        snapshot = copy.deepcopy(self.snapshot)
        snapshot["items"][0]["installedFiles"] = [{"modId": 1, "fileId": 999}]
        with self.assertRaisesRegex(PlanError, "reviewed installed archive"):
            validate_batch_selection(snapshot, self.packet(selection))

    def test_explicit_manual_alternate_keeps_complete_current_project_ownership(self):
        self.payload["files"].append({**self.payload["files"][-1], "fileId": 303,
                                      "name": "Alternate runtime", "fileName": "alternate.7z"})
        reviewed = self.service.review_files(1, "live-one")
        result = self.service.queue_reviewed_downloads(self.packet({**self.choice(file_id=303),
                                                                   "fileProof": reviewed["fileProof"]}))
        self.assertEqual(self.downloads, [303], result)

    def test_failed_download_retry_cannot_borrow_a_changed_same_version_target(self):
        queued = self.service.quick_update({"profileId": "review", "itemIds": ["live-one"]})
        self.assertTrue(queued["queued"], queued)
        self.service.set_download_status(9, "failed")
        self.changed_target()
        self.service._file_cache.clear()
        self.assertTrue(self.service.review_files(1, "live-one")["ok"])
        rejected = self.service.retry_failed_download(9)
        self.assertFalse(rejected["ok"], rejected)
        self.assertIn("identity changed", rejected["error"])
        self.assertEqual(self.downloads, [201])


if __name__ == "__main__":
    unittest.main()
