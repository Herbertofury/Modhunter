"""Modhunter updater end-to-end unit/HTTP proof without mocked web browser claims."""
import copy
import tempfile
import threading
import time
import unittest
from pathlib import Path

from mo2_update_arsenal.bridge import ArsenalService
from mo2_update_arsenal.native_nexus import NativeNexusGateway, normalize_native_files
from mo2_update_arsenal.source_registry import (
    SourceScanner, canonical_source_url, discover_local_source, provider_of, release_verdict)
from mo2_update_arsenal.update_plan import PlanError, plan_updates, validate_release_asset


ASSET = {"assetId": 555, "name": "supermod-v2.zip",
         "url": "https://github.com/owner/supermod/releases/download/v2/supermod-v2.zip", "size": 4500}
PROFILE = {
    "profileId": "skyrim:p1", "profileName": "Primary", "game": "skyrimspecialedition",
    "items": [
        {"id": "github-row", "name": "Super Mod", "installedVersion": "1.0",
         "source": {"type": "github", "verified": True, "url": "https://github.com/owner/supermod"},
         "nexusId": 0, "flags": ["DLL"],
         "update": {"verdict": "candidate", "source": "github-release-api",
                    "provider": "github", "latestVersion": "2.0", "releaseAssets": [ASSET]}},
        {"id": "nexus-row", "name": "Nexus Mod", "installedVersion": "1.0",
         "nexusId": 101, "flags": [], "update": {"verdict": "candidate", "latestVersion": "2.0"}},
    ], "groups": [],
}


def source_selection():
    return {"kind": "source", "itemId": "github-row", "assetId": 555,
            "installedVersion": "1.0", "reviewed": True, "riskAcknowledged": True}


class SourceRegistryTests(unittest.TestCase):
    def test_discovery_from_real_mo2_meta_file_and_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            moddir = Path(tmp)
            (moddir / "meta.ini").write_text("[General]\nversion=1.1\nurl=https://github.com/owner/supermod?utm_source=mo2\n", encoding="utf-8")
            info = discover_local_source(moddir)
            self.assertEqual(info["url"], "https://github.com/owner/supermod")
            self.assertEqual(info["type"], "github")
            (moddir / "meta.ini").unlink()
            self.assertEqual(discover_local_source(moddir, "Read https://www.loverslab.com/files/file/1234/")["type"], "loverslab")

    def test_catalog_of_non_nexus_hosts_never_claims_version_check(self):
        providers = {
            "https://www.patreon.com/posts/1234": "patreon",
            "https://www.loverslab.com/files/file/1234": "loverslab",
            "https://www.moddb.com/mods/abc": "moddb",
            "https://www.curseforge.com/skyrim/mods/example": "curseforge",
            "https://creations.bethesda.net/en/skyrim/details/22": "bethesda",
            "https://modding-guild.com/mod/example": "modding-guild",
            "https://dwemermods.com/mod/example": "dwemer",
            "https://gitlab.com/team/thing": "gitlab",
        }
        for url, expected in providers.items():
            with self.subTest(url=url):
                self.assertEqual(provider_of(url), expected)
        self.assertFalse(canonical_source_url("file:///etc/passwd"))
        self.assertFalse(canonical_source_url("http://127.0.0.1/"))
        self.assertEqual(canonical_source_url("https://github.com/foo/bar?token=SECRET"),
                         "https://github.com/foo/bar")

    def test_public_github_and_gitlab_releases_dedup_and_compare(self):
        calls = []
        def fetch(url):
            calls.append(url)
            if "github" in url:
                return {"tag_name": "v2.0", "html_url": "https://github.com/owner/supermod/releases/tag/v2.0",
                        "assets": [
                            {"id": 555, "name": "supermod-v2.zip",
                             "browser_download_url": ASSET["url"], "size": 4500},
                            {"id": 556, "name": "disguised.zip",
                             "browser_download_url": "https://evil.example/mod.zip", "size": 2}]}
            return {"tag_name": "v3.0"}
        snap = copy.deepcopy(PROFILE)
        snap["items"].append({**copy.deepcopy(snap["items"][0]), "id": "github-row-2",
                              "installedVersion": "2.0"})
        snap["items"].append({"id": "gitlab-row", "name": "GitLab Mod", "nexusId": 0,
                              "installedVersion": "1.0", "source": {
                                  "type": "gitlab", "verified": True,
                                  "url": "https://gitlab.com/group/thing"}})
        snap["items"].append({"id": "patreon-row", "name": "Patreon Mod", "nexusId": 0,
                              "installedVersion": "1.0", "source": {
                                  "type": "patreon", "verified": True,
                                  "url": "https://www.patreon.com/posts/example"}})
        result = SourceScanner(fetch_json=fetch).scan(snap)
        self.assertEqual(len(calls), 2)  # Shared GitHub project fetched once.
        self.assertEqual(result["itemRecords"]["github-row"]["verdict"], "candidate")
        self.assertEqual(result["itemRecords"]["github-row-2"]["verdict"], "current")
        self.assertEqual(result["itemRecords"]["gitlab-row"]["verdict"], "candidate")
        self.assertEqual(result["itemRecords"]["patreon-row"]["verdict"], "unverified")
        self.assertEqual(len(result["itemRecords"]["github-row"]["releaseAssets"]), 1)

    def test_network_error_is_unknown_not_current(self):
        def broken(url):
            raise TimeoutError("provider unavailable")
        result = SourceScanner(fetch_json=broken).scan(PROFILE)
        self.assertEqual(result["itemRecords"]["github-row"]["verdict"], "unknown")
        self.assertTrue(result["errors"])

    def test_server_only_asset_validation_and_profile_scoping(self):
        selected = validate_release_asset(PROFILE, source_selection())
        self.assertEqual(selected["url"], ASSET["url"])
        broken = copy.deepcopy(PROFILE)
        broken["items"][0]["update"]["releaseAssets"][0]["url"] = "https://evil.example/malware.zip"
        with self.assertRaises(PlanError):
            validate_release_asset(broken, source_selection())
        changed = copy.deepcopy(PROFILE)
        changed["items"][0]["installedVersion"] = "1.1"
        with self.assertRaises(PlanError):
            validate_release_asset(changed, source_selection())
        plan = plan_updates(PROFILE)
        self.assertEqual(plan["summary"]["candidates"], 2)
        self.assertEqual(plan["items"][0]["provider"], "github")
        self.assertEqual(plan["items"][0]["releaseAssets"][0]["assetId"], 555)


class Signal:
    def __init__(self):
        self.handlers = []
    def connect(self, callback):
        self.handlers.append(callback)
    def emit(self, *args):
        for fn in self.handlers:
            fn(*args)


class FakeNexus:
    def __init__(self):
        self.descriptionAvailable = Signal()
        self.filesAvailable = Signal()
        self.requestFailed = Signal()
        self.calls = []
    def requestDescription(self, game, mod_id, user_data):
        self.calls.append(("metadata", game, mod_id, user_data))
    def requestFiles(self, game, mod_id, user_data):
        self.calls.append(("files", game, mod_id, user_data))


class NativeBridgeTests(unittest.TestCase):
    def test_no_second_login_for_mo2_native_metadata_and_files(self):
        from mo2_update_arsenal.bridge import UIRequest
        native = FakeNexus()
        gateway = NativeNexusGateway(type("Organizer", (), {"createNexusBridge": lambda self: native})())
        q1 = UIRequest("nexus-metadata", {"game": "skyrimspecialedition", "modId": 88})
        gateway.request(q1)
        token = native.calls[-1][-1]
        native.descriptionAvailable.emit("skyrimspecialedition", 88, token,
                                         {"version": "2.0", "status": "published", "private_key": "DONT_LEAK"})
        self.assertTrue(q1.event.is_set())
        self.assertEqual(q1.result["meta"]["version"], "2.0")
        self.assertNotIn("private_key", q1.result["meta"])
        q2 = UIRequest("nexus-files", {"game": "skyrimspecialedition", "modId": 88})
        gateway.request(q2)
        token = native.calls[-1][-1]
        native.filesAvailable.emit("skyrimspecialedition", 88, token,
                                   [{"fileId": 11, "name": "Correct.zip", "category": 1}])
        self.assertEqual(q2.result["files"][0]["fileId"], 11)
        self.assertEqual(len(gateway.pending), 0)

    def test_native_failure_resolves_request(self):
        from mo2_update_arsenal.bridge import UIRequest
        fake = FakeNexus()
        gateway = NativeNexusGateway(type("O", (), {"createNexusBridge": lambda self: fake})())
        q = UIRequest("nexus-metadata", {"game": "skyrimspecialedition", "modId": 88})
        gateway.request(q)
        fake.requestFailed.emit("skyrimspecialedition", 88, 0, fake.calls[-1][-1], 401, "Not connected")
        self.assertTrue(q.event.is_set())
        self.assertFalse(q.result["ok"])


class MixedDownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.commands = []
        def native(req):
            self.commands.append(req.cmd)
            req.finish({"ok": True, "downloadId": 100 + len(self.commands)})
        self.service = ArsenalService("<html></html>", Path(self.tmp.name) / "cache.json",
                                     native, copy.deepcopy(PROFILE))
        self.service.fetch_files = lambda mod_id: {"ok": True, "files": [{"fileId": 12}],
                                                    "modId": mod_id}
    def tearDown(self):
        self.service.stop()
        self.tmp.cleanup()

    def pkt(self, entries):
        return {"profileId": "skyrim:p1", "confirm": "QUEUE_REVIEWED_DOWNLOADS", "entries": entries}

    def test_github_only_uses_native_url_download(self):
        receipt = self.service.queue_reviewed_downloads(self.pkt([source_selection()]))
        self.assertTrue(receipt["ok"], receipt)
        self.assertEqual(self.commands, ["download-url"])
        self.assertEqual(len(receipt["queued"]), 1)
        self.assertEqual(self.service.downloads()[101]["provider"], "github")

    def test_mixed_batch_preflights_nexus_and_release(self):
        nexus = {"itemId": "nexus-row", "modId": 101, "fileId": 12,
                 "installedVersion": "1.0", "reviewed": True}
        receipt = self.service.queue_reviewed_downloads(self.pkt([source_selection(), nexus]))
        self.assertTrue(receipt["ok"], receipt)
        self.assertEqual(len(receipt["queued"]), 2)
        self.assertEqual(self.commands, ["download", "download-url"])
        self.assertEqual(self.service.downloads()[102]["provider"], "github")

    def test_invalid_source_blocks_entire_batch(self):
        nexus = {"itemId": "nexus-row", "modId": 101, "fileId": 12,
                 "installedVersion": "1.0", "reviewed": True}
        invalid = {**source_selection(), "assetId": 999}
        result = self.service.queue_reviewed_downloads(self.pkt([nexus, invalid]))
        self.assertFalse(result["ok"])
        self.assertEqual(self.commands, [])


if __name__ == "__main__":
    unittest.main()
