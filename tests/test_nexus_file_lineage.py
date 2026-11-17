from __future__ import annotations
import copy
import tempfile
import threading
import time
import unittest
from pathlib import Path

from mo2_update_arsenal.nexus_files import FileLineage, normalize_file_payload
from mo2_update_arsenal.scanner import Cache, NexusClient, UpdateScanner
from mo2_update_arsenal.quick_update import choose_nexus_file
from nexus_fixture import GAME, files_payload, file_item


def index(payload):
    return FileLineage(normalize_file_payload(payload, GAME, 1), GAME, 1)


class ExactFileTests(unittest.TestCase):
    def optional(self):
        payload = files_payload("1.5.1")
        payload["files"].append({"file_id": 700, "name": "Fishing Integration Patch",
            "file_name": "Fishing-Integration-Patch-1-0-1.7z", "version": "1.0.1", "category_id": 3})
        item = file_item(version="1.0.1")
        item["installedFiles"] = [{"modId": 1, "fileId": 700}]
        return payload, item

    def test_fishing_optional_patch_is_current_even_when_main_project_is_newer(self):
        payload, item = self.optional()
        result = index(payload).evaluate(item)
        self.assertEqual((result["verdict"], result["latestVersion"]), ("current", "1.0.1"))
        self.assertEqual(result["fileEvidence"]["targetFileId"], 700)
        self.assertEqual(result["fileEvidence"]["installedFileName"], "Fishing-Integration-Patch-1-0-1.7z")
        self.assertEqual(result["fileEvidence"]["targetFileName"], "Fishing-Integration-Patch-1-0-1.7z")
        self.assertFalse(result["fileEvidence"]["updated"])

    def test_only_explicit_optional_replacement_is_suggested_including_same_version_reuploads(self):
        payload, item = self.optional()
        payload["files"].append({"file_id": 701, "name": "Fishing replacement", "file_name": "renamed-patch.7z",
                                 "version": "1.0.1", "category_id": 3})
        payload["file_updates"].append({"old_file_id": 700, "new_file_id": 701})
        lineage = index(payload)
        item["update"] = {**lineage.evaluate(item), "source": "nexus-api"}
        self.assertEqual(item["update"]["verdict"], "candidate")
        chosen, _ = choose_nexus_file(item, lineage.payload["files"])
        self.assertEqual(chosen["fileId"], 701)

    def test_archived_id_and_exact_old_archive_name_follow_the_same_chain(self):
        payload = files_payload()
        payload["files"] = [payload["files"][1]]
        for item in (file_item(), {"nexusId": 1, "installedVersion": "1.0", "installationFile": r"D:\Downloads\installed-1.7z"}):
            result = index(payload).evaluate(item)
            self.assertEqual(result["verdict"], "candidate")
            self.assertEqual(result["fileEvidence"]["targetFileId"], 201)

    def test_no_project_version_similar_name_or_largest_file_id_fallback(self):
        payload, item = self.optional()
        for evidence in ({}, {"installationFile": "Fishing-Integration-Patch.7z"}, {"installedFiles": [{"modId": 1, "fileId": 99999}]}):
            row = {"nexusId": 1, "installedVersion": "1.0.1", "name": "Fishing Integration Patch", **evidence}
            result = index(payload).evaluate(row)
            self.assertEqual(result["verdict"], "review")
            self.assertEqual(result["latestVersion"], "")

    def test_branches_cycles_conflicting_records_merged_options_and_missing_history_require_review(self):
        base = files_payload()
        branch = copy.deepcopy(base); branch["file_updates"].append({"old_file_id": 101, "new_file_id": 202})
        cycle = copy.deepcopy(base); cycle["file_updates"].append({"old_file_id": 201, "new_file_id": 101})
        conflict = copy.deepcopy(base); conflict["files"].append({**conflict["files"][-1], "version": "99"})
        missing = copy.deepcopy(base); del missing["file_updates"]
        for payload in (branch, cycle, conflict, missing):
            self.assertEqual(index(payload).evaluate(file_item())["verdict"], "review")
        merged = file_item(); merged["installedFiles"].append({"modId": 1, "fileId": 999})
        self.assertEqual(index(base).evaluate(merged)["fileEvidence"]["status"], "ambiguous")

    def test_old_archived_removed_unknown_and_missing_targets_never_report_current(self):
        for category in (0, 4, 6, 7, 1000):
            payload = files_payload(); payload["files"][-1]["category_id"] = category
            self.assertEqual(index(payload).evaluate(file_item())["verdict"], "review")
        payload = files_payload(); payload["files"] = []
        self.assertEqual(index(payload).evaluate(file_item())["verdict"], "review")

    def test_ignored_target_and_lower_version_replacement_require_explicit_review(self):
        item = file_item(); item["ignoredVersion"] = "2.0"
        self.assertTrue(index(files_payload()).evaluate(item)["ignored"])
        self.assertEqual(index(files_payload()).evaluate(file_item(version="3.0"))["verdict"], "review")

    def test_file_list_is_not_silently_capped_and_names_are_not_truncated(self):
        payload, item = self.optional()
        payload["files"] = [{"file_id": 1000 + number, "version": "1", "category_id": 1} for number in range(21000)] + payload["files"]
        name = "Long optional archive " + "a" * 220 + ".7z"
        payload["files"][-1]["file_name"] = name
        item.pop("installedFiles"); item["installationFile"] = name
        result = index(payload).evaluate(item)
        self.assertEqual(result["verdict"], "current")
        self.assertEqual(result["fileEvidence"]["installedFileName"], name)

    def test_game_project_and_malformed_response_identity_are_rejected(self):
        for fields in ({"mod_id": 2}, {"mod_id": True}, {"domain_name": "fallout4"}):
            with self.assertRaises(ValueError):
                index({**files_payload(), **fields})

    def test_malformed_cache_and_response_issues_never_crash_or_become_verified(self):
        valid = normalize_file_payload(files_payload(), GAME, 1)
        for change in ({"files": [{}]}, {"fileUpdates": [{"oldFileId": 101}]},
                       {"files": [None]}, {"game": "fallout4"}, {"modId": True}, {"schema": True},
                       {"fileUpdates": [*valid["fileUpdates"], {**valid["fileUpdates"][0], "newFileName": "foreign.7z"}]}):
            result = FileLineage({**valid, **change}, GAME, 1).evaluate(file_item())
            self.assertEqual(result["verdict"], "review")
            self.assertEqual(result["fileEvidence"]["status"], "unavailable")
        for issues in (None, "not a list", [False]):
            result = index({**files_payload(), "issues": issues}).evaluate(file_item())
            self.assertEqual(result["verdict"], "review")
        duplicate = files_payload()
        duplicate["file_updates"].append({**duplicate["file_updates"][0], "new_file_name": "Conflicting.7z"})
        self.assertEqual(index(duplicate).evaluate(file_item())["verdict"], "review")
        merged = file_item(); merged["installedFiles"].append({"modId": 999, "fileId": 101})
        self.assertEqual(index(files_payload()).evaluate(merged)["fileEvidence"]["status"], "ambiguous")


class SharedProjectTests(unittest.TestCase):
    def test_one_request_for_main_and_optional_rows_and_zero_for_a_fresh_recheck(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Cache(Path(directory) / "cache.json")
            payload, optional = ExactFileTests().optional()
            optional["id"] = "patch"
            snapshot = {"game": GAME, "items": [file_item(ident="main"), optional]}
            calls = []
            scanner = UpdateScanner(cache, NexusClient("fixture", lambda path: calls.append(path) or payload))
            first = scanner.scan(snapshot, "startup")
            second = scanner.scan(snapshot, "startup")
            self.assertEqual(len(calls), 1)
            self.assertTrue(calls[0].endswith("/mods/1/files.json"))
            self.assertEqual(first["itemRecords"]["main"]["verdict"], "candidate")
            self.assertEqual(first["itemRecords"]["patch"]["verdict"], "current")
            self.assertEqual(second["fromCache"], 1)
            self.assertEqual(second["queried"], 0)
            scanner.scan(snapshot, "deep")
            self.assertEqual(len(calls), 2)

    def test_old_project_version_cache_is_never_an_installed_file_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Cache(Path(directory) / "cache.json")
            cache.set(GAME, 1, {"meta": {"version": "90"}, "checkedAt": time.time()})
            calls = []
            result = UpdateScanner(cache, NexusClient("fixture", lambda path: calls.append(path) or files_payload())).scan(
                {"game": GAME, "items": [file_item()]}, "startup")
            self.assertEqual(len(calls), 1)
            self.assertEqual(result["itemRecords"]["1"]["latestVersion"], "2.0")

    def test_cached_other_game_same_project_id_is_refetched_and_never_borrowed(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Cache(Path(directory) / "cache.json")
            foreign = normalize_file_payload(files_payload(), GAME, 1)
            foreign["game"] = "fallout4"
            cache.set(GAME, 1, {"files": foreign, "checkedAt": time.time()})
            calls = []
            result = UpdateScanner(cache, NexusClient("fixture", lambda path: calls.append(path) or files_payload())).scan(
                {"game": GAME, "items": [file_item()]}, "startup")
            self.assertEqual(len(calls), 1)
            self.assertEqual(result["fromCache"], 0)

    def test_same_version_reinstall_invalidates_old_scan_ownership_and_target(self):
        from mo2_update_arsenal.bridge import ArsenalService
        with tempfile.TemporaryDirectory() as directory:
            row = file_item()
            row["update"] = {**index(files_payload()).evaluate(row), "source": "nexus-api"}
            snapshot = {"profileId": "A", "game": GAME, "items": [row], "groups": []}
            service = ArsenalService("", Path(directory) / "cache.json", lambda request: None, snapshot)
            self.addCleanup(service.stop)
            epoch = service._execution_epoch
            replacement = copy.deepcopy(snapshot)
            replacement["items"][0]["installedFiles"] = [{"modId": 1, "fileId": 999}]
            replacement["items"][0].pop("update")
            service.set_snapshot(replacement)
            self.assertGreater(service._execution_epoch, epoch)
            self.assertNotIn("update", service.snapshot()["items"][0])
            self.assertEqual(service.scan_state()["state"], "stale")


if __name__ == "__main__":
    unittest.main()
