"""Evidence-based file review. No false compatibility claims or missing files."""
import copy
import tempfile
import time
import unittest
from pathlib import Path

from mo2_update_arsenal.file_advisor import advise_nexus_files
from mo2_update_arsenal.bridge import ArsenalService
from mo2_update_arsenal.update_plan import PlanError, validate_batch_selection, validate_release_asset, nexus_file_proof
from nexus_fixture import review_payload


def row(ident="primary", source="nexus-api", version="2.0"):
    return {"id": ident, "nexusId": 44, "installedVersion": "1.0", "flags": ["DLL"],
            "installedFiles": [{"modId": 44, "fileId": 7}],
            "update": {"verdict": "candidate", "source": source, "latestVersion": version,
                       "checkedAt": time.time(), "expiresAt": time.time() + 14390, "freshness": "fresh",
                       "fileEvidence": {"status": "matched", "updated": True, "targetFileId": 9,
                                        "installedFileIds": [7]}}}


FILES = [
    {"fileId": 8, "name": "SE old archive", "version": "1.5", "category": 1},
    {"fileId": 9, "name": "Latest core", "version": "v2.0.0", "category": 1},
    {"fileId": 10, "name": "Optional graphics", "version": "2", "category": 2},
    {"fileId": 11, "name": "Unsupported variant", "version": "2.0-AE", "category": 1},
]


class AdvisorTest(unittest.TestCase):
    def test_exact_replacement_is_visibly_reviewable_not_compatibility_approved(self):
        original = copy.deepcopy(FILES)
        result = advise_nexus_files(row(), FILES)
        self.assertEqual(FILES, original)
        self.assertEqual(len(result["files"]), len(FILES))
        self.assertEqual(result["files"][0]["fileId"], 9)
        self.assertTrue(result["files"][0]["suggested"])
        self.assertIn("verify runtime", result["files"][0]["reviewHint"].lower())
        self.assertEqual(result["reviewAdvice"]["matchingMainFiles"], 1)
        self.assertFalse(result["files"][1]["suggested"])
        self.assertEqual({f["fileId"] for f in result["files"]}, {8, 9, 10, 11})

    def test_equal_project_versions_cannot_replace_missing_lineage_evidence(self):
        files = FILES + [{"fileId": 12, "name": "AE", "version": "2.0", "category": 1}]
        item = row(); item['update'].pop('fileEvidence')
        result = advise_nexus_files(item, files)
        self.assertEqual(result["reviewAdvice"]["matchingMainFiles"], 0)
        self.assertFalse(result["reviewAdvice"]["hasUniqueMainMatch"])
        self.assertEqual(sum(bool(f["suggested"]) for f in result["files"]), 0)
        self.assertEqual(len(result["files"]), 5)

    def test_exact_target_stays_distinct_from_an_unrelated_equal_version(self):
        files = FILES + [{"fileId": 12, "name": "AE", "version": "2.0", "category": 1}]
        result = advise_nexus_files(row(), files)
        self.assertEqual([file['fileId'] for file in result['files'] if file['suggested']], [9])
        self.assertEqual(len(result['files']), 5)

    def test_unknown_or_untrusted_project_version_cannot_suggest_file(self):
        for item in (row(source="github-release-api"), row(source="mo2-cached-metadata"),
                     row(version="2.0-beta"), row(source="nexus-api", version="unknown")):
            with self.subTest(item=item):
                result = advise_nexus_files(item, FILES)
                self.assertFalse(any(x["suggested"] for x in result["files"]))
                self.assertFalse(any(x["releaseVersionMatch"] for x in result["files"]))

    def test_malformed_rows_are_never_implicitly_approved(self):
        result = advise_nexus_files(row(), [None, {}, {"fileId": 99, "category": "?", "version": True}])
        self.assertEqual(len(result["files"]), 2)
        self.assertFalse(any(f["suggested"] for f in result["files"]))

    def test_same_nexus_project_has_per_mod_review_hints(self):
        snap = {"profileId": "profile-A", "game": "skyrimspecialedition", "items": [
            row("nexus-project-row"), row("external-release-row", "github-release-api")]}
        with tempfile.TemporaryDirectory() as d:
            service = ArsenalService("<html/>", Path(d) / "cache.json", lambda r: r.finish({"ok": False}), snap)
            service.fetch_files = lambda mod_id: review_payload(mod_id, copy.deepcopy(FILES), [(7, 9)])
            a = service.review_files(44, "nexus-project-row")
            b = service.review_files(44, "external-release-row")
            self.assertTrue(a["reviewAdvice"]["hasUniqueMainMatch"])
            self.assertFalse(b["reviewAdvice"]["hasUniqueMainMatch"])
            self.assertFalse(service.review_files(45, "nexus-project-row")["ok"])
            self.assertFalse(service.review_files(44, "imaginary-row")["ok"])
            service.stop()

    def test_stale_latest_release_invalidates_only_explicit_target_version(self):
        snap = {"profileId": "profile-A", "items": [row()]}
        selection = {"profileId": "profile-A", "confirm": "QUEUE_REVIEWED_DOWNLOADS", "entries": [
            {"itemId": "primary", "modId": 44, "fileId": 9, "installedVersion": "1.0",
             "targetVersion": "2.0", "reviewed": True, "riskAcknowledged": True,
             "fileProof": nexus_file_proof(snap, snap["items"][0])}]}
        self.assertEqual(len(validate_batch_selection(snap, selection)), 1)
        changed = copy.deepcopy(snap)
        changed["items"][0]["update"]["latestVersion"] = "2.5"
        with self.assertRaisesRegex(PlanError, "Published target version changed"):
            validate_batch_selection(changed, selection)

    def test_stale_github_release_selection_rejected(self):
        item = row("github", "github-release-api")
        item["nexusId"] = 0
        item["source"] = {"verified": True, "url": "https://github.com/owner/project"}
        item["update"]["releaseAssets"] = [{"assetId": 1, "name": "project-2.0.zip",
              "url": "https://github.com/owner/project/releases/download/v2/project-2.0.zip"}]
        snap = {"profileId": "profile-A", "items": [item]}
        choice = {"kind": "source", "itemId": "github", "assetId": 1,
                  "installedVersion": "1.0", "targetVersion": "2.0", "reviewed": True,
                  "riskAcknowledged": True}
        self.assertEqual(validate_release_asset(snap, choice)["provider"], "github")
        snap["items"][0]["update"]["latestVersion"] = "2.1"
        with self.assertRaisesRegex(PlanError, "Published source release changed"):
            validate_release_asset(snap, choice)


if __name__ == "__main__":
    unittest.main()
