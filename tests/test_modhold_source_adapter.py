"""ModHold metadata integration: exact author pins and no download capability."""
from __future__ import annotations

import copy
import time
import unittest

from mo2_update_arsenal.source_adapters import (
    adapter_capabilities, asset_matches_source, compact_source_update, normalize_adapter_record,
)
from mo2_update_arsenal.source_registry import SourceScanner, provider_of

PAGE = "https://modholdmods.com/g/skyrim-se/mods/11/fixture-estate"


def fixture():
    now = time.time()
    item = {"id": "estate", "nexusId": 196696, "installedVersion": "0.1.20",
            "source": {"url": PAGE, "type": "modhold", "verified": True,
                       "kind": "project", "expectedAuthor": "SethSaidThat"}}
    raw = {"provider": "modhold", "source": "modhold-release-api", "sourceUrl": PAGE,
           "outcome": "release", "availability": "available", "freshness": "fresh",
           "checkedAt": now, "expiresAt": now + 900, "projectId": "11", "releaseId": "541",
           "releaseIdentityKind": "main-file", "latestVersion": "0.1.21", "releaseUrl": PAGE,
           "publisherId": "SethSaidThat", "author": {"name": "SethSaidThat", "handle": "SethSaidThat",
                   "url": "https://modholdmods.com/u/SethSaidThat", "role": "mod author"},
           "access": "free", "providerTimestampZone": "not-declared", "updatedAt": "2026-11-14 18:10:48",
           "projectPublishedAt": "2026-10-25 03:00:53", "publishedAt": "2026-11-12 18:40:37",
           "compatibility": {"status": "not-declared", "game": "skyrim-se"},
           "releaseAssets": [{"assetKey": "modhold:541", "assetId": 541, "provider": "modhold", "projectId": "11",
                   "releaseId": "541", "releaseVersion": "0.1.21", "fileKind": "main", "name": "Estate 0.1.21.zip",
                   "url": PAGE + "?tab=files", "size": 100, "hashes": {"sha256": "a" * 64},
                   "installable": False, "downloadMode": "provider-sign-in"}]}
    return item, raw


class ModholdSourceAdapterTests(unittest.TestCase):
    def test_provider_host_is_exact(self):
        self.assertEqual(provider_of(PAGE), "modhold")
        self.assertEqual(provider_of(PAGE.replace("modholdmods.com", "www.modholdmods.com")), "modhold")
        for host in ("modholdmods.com.evil.test", "evil.modholdmods.com", "modhold.com"):
            self.assertEqual(provider_of(PAGE.replace("modholdmods.com", host)), "website")

    def test_capabilities_are_metadata_only_and_require_helper(self):
        supported = adapter_capabilities("modhold")
        self.assertTrue(supported["versionCheck"])
        self.assertFalse(supported["reviewedDownload"])
        self.assertEqual(supported["authentication"], "none")
        missing = adapter_capabilities("modhold", helper_available=False)
        self.assertFalse(missing["versionCheck"])
        self.assertFalse(missing["reviewedDownload"])

    def test_normalized_update_keeps_author_access_and_main_file_identity(self):
        item, raw = fixture()
        update = normalize_adapter_record(item, raw)
        self.assertEqual(update["verdict"], "candidate")
        self.assertEqual(update["releaseIdentityKind"], "main-file")
        self.assertEqual(update["author"]["handle"], "SethSaidThat")
        self.assertEqual(update["access"], "free")
        self.assertEqual(update["updatedAt"], "2026-11-14 18:10:48")
        self.assertFalse(update["releaseAssets"][0]["installable"])
        compact = compact_source_update(update)
        self.assertEqual(compact["access"], "free")
        self.assertEqual(compact["author"]["handle"], "SethSaidThat")

    def test_changed_or_missing_author_pin_never_becomes_current(self):
        item, raw = fixture()
        for author in ("OtherAuthor", "", None):
            changed = {**raw, "publisherId": author}
            update = normalize_adapter_record(item, changed)
            self.assertEqual(update["verdict"], "unknown")
            self.assertEqual(update["outcome"], "unavailable")
        bad = copy.deepcopy(item)
        bad["source"]["expectedAuthor"] = "../../SethSaidThat"
        self.assertEqual(normalize_adapter_record(bad, raw)["verdict"], "unknown")

    def test_no_client_override_can_turn_metadata_into_download(self):
        item, raw = fixture()
        raw["releaseAssets"][0]["installable"] = True
        raw["releaseAssets"][0]["url"] = "https://modholdmods.com/api/v1/files/541/download"
        update = normalize_adapter_record(item, raw)
        self.assertFalse(update["releaseAssets"][0]["installable"])
        self.assertFalse(asset_matches_source(item["source"], update, update["releaseAssets"][0]))

    def test_migrated_nexus_mod_is_sent_to_native_scanner_with_author_pin(self):
        item, raw = fixture()
        class Runner:
            available = True
            def __init__(self):
                self.request = None
            def run(self, request, **_):
                self.request = request
                return {"schemaVersion": 1, "profileId": request["profileId"],
                        "itemRecords": {"estate": raw}, "cache": {"entries": []}, "stats": {"queried": 1}}
        runner = Runner()
        scanner = SourceScanner(runner=runner)
        result = scanner.scan({"profileId": "p", "game": "skyrimspecialedition", "items": [item]})
        self.assertEqual(runner.request["items"][0]["source"]["expectedAuthor"], "SethSaidThat")
        self.assertEqual(result["itemRecords"]["estate"]["verdict"], "candidate")

    def test_failure_and_review_never_promote_stale_versions(self):
        item, raw = fixture()
        for changes in ({"outcome": "unavailable", "availability": "access-denied-or-challenged"},
                        {"outcome": "review", "channel": "prerelease"},
                        {"expiresAt": time.time() - 10}):
            update = normalize_adapter_record(item, {**raw, **changes})
            self.assertNotIn(update["verdict"], ("candidate", "current", "newer-installed"))
            self.assertNotIn("authorStatus", update)


if __name__ == "__main__":
    unittest.main()
