"""Provider evidence, lazy transport, and exact-archive queue regressions."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch

from mo2_update_arsenal.bridge import ArsenalService
from mo2_update_arsenal.install_brief import build_install_brief
from mo2_update_arsenal.quick_update import choose_release_asset
from mo2_update_arsenal.source_adapters import (
    RustSourceRunner, adapter_capabilities, asset_matches_source, asset_proof,
    compact_source_update, normalize_adapter_record,
)
from mo2_update_arsenal.source_registry import SourceScanner, canonical_source_url, provider_of
from mo2_update_arsenal.update_plan import PlanError, plan_updates, validate_release_asset


def fixture(provider="gitlab"):
    identities = {
        "github": ("https://github.com/Owner/Project", "owner/project", "92", "v2.0", "https://github.com/Owner/Project/releases/download/v2.0/mod.zip"),
        "gitlab": ("https://gitlab.com/group/sub/project/-/releases", "group/sub/project", "v2.0", "v2.0", "https://gitlab.com/group/sub/project/-/uploads/abc/mod.zip"),
        "modrinth": ("https://modrinth.com/mod/example", "AbCdEfGh", "IjKlMnOp", "2.0.0", "https://cdn.modrinth.com/data/AbCdEfGh/versions/IjKlMnOp/mod.zip"),
        "thunderstore": ("https://thunderstore.io/c/valheim/p/Team/Package/", "Team/Package", "2.0.0", "2.0.0", "https://thunderstore.io/package/download/Team/Package/2.0.0/"),
    }
    source_url, project, release, version, url = identities[provider]
    item = {"id": "live-source", "name": "Fixture Mod", "nexusId": 0, "installedVersion": "1.0",
            "source": {"type": provider, "url": source_url, "verified": True, "kind": "project"}, "flags": []}
    raw = {"provider": provider, "source": provider + "-release-api", "sourceUrl": source_url,
           "projectId": project, "releaseId": release, "latestVersion": version, "releaseUrl": source_url,
           "outcome": "release", "availability": "available", "freshness": "fresh", "checkedAt": time.time(), "expiresAt": time.time() + 900,
           "compatibility": {"status": "matched"}, "summary": "Release summary", "changelog": "Changes\n" * 1000,
           "dependencies": [{"projectId": "Dependency", "kind": "required"}],
           "publisher": {"name": "Release Publisher", "url": "https://github.com/publisher", "role": "release publisher"},
           "releaseAssets": [{"assetKey": provider + ":exact-file", "assetId": 42, "provider": provider,
                              "projectId": project, "releaseId": release, "name": "mod.zip", "url": url,
                              "size": 1024, "hashes": {"sha256": "a" * 64}, "installable": True}]}
    item["update"] = normalize_adapter_record(item, raw)
    return item, raw


def snapshot(item):
    return {"profileId": "p", "profileName": "Profile", "game": "skyrimspecialedition", "gameRuntime": "1.6.1170",
            "items": [item], "groups": []}


def selection(item):
    asset = item["update"]["releaseAssets"][0]
    return {"kind": "source", "itemId": item["id"], "assetKey": asset["assetKey"], "assetProof": asset["assetProof"],
            "installedVersion": item["installedVersion"], "targetVersion": item["update"]["latestVersion"],
            "reviewed": True, "riskAcknowledged": True}


class SourceEvidenceTests(unittest.TestCase):
    def test_community_moderation_is_visible_without_rewriting_release_facts(self):
        item, raw = fixture("thunderstore")
        raw["moderationStatus"] = "unreviewed"
        item["update"] = normalize_adapter_record(item, raw)
        self.assertEqual(item["update"]["moderationStatus"], "unreviewed")
        self.assertEqual(item["update"]["verdict"], "candidate")
        self.assertEqual(compact_source_update(item["update"])["moderationStatus"], "unreviewed")

    def test_supported_api_assets_bind_exact_owned_project_and_release(self):
        for provider in ("github", "gitlab", "modrinth", "thunderstore"):
            with self.subTest(provider=provider):
                item, _ = fixture(provider)
                result = validate_release_asset(snapshot(item), selection(item))
                self.assertEqual(result["provider"], provider)
                self.assertEqual(result["assetKey"], provider + ":exact-file")
                self.assertTrue(item["update"]["releaseAssets"][0]["installable"])
                self.assertIsNotNone(choose_release_asset(item)[0])
                self.assertNotIn("author", item["update"])
                self.assertEqual(item["update"]["publisher"]["role"], "release publisher")

    def test_unknown_stale_future_and_wrong_identity_never_become_current(self):
        item, raw = fixture("github")
        for changed in ({"freshness": "stale"}, {"expiresAt": time.time() - 1}, {"checkedAt": time.time() + 900},
                        {"sourceUrl": "https://github.com/someone/else"}, {"provider": "gitlab"},
                        {"compatibility": {"status": "game-mismatch"}}, {"outcome": "review"}):
            with self.subTest(changed=changed):
                update = normalize_adapter_record(item, {**raw, **changed})
                self.assertNotIn(update["verdict"], ("candidate", "current", "newer-installed"))

    def test_hash_or_size_change_after_review_invalidates_same_asset_id(self):
        item, _ = fixture()
        request = selection(item)
        for field, value in (("size", 2048), ("hashes", {"sha256": "b" * 64}), ("name", "replacement.zip")):
            changed = copy.deepcopy(item)
            changed["update"]["releaseAssets"][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(PlanError, "archive changed"):
                validate_release_asset(snapshot(changed), request)

    def test_cross_project_redirect_wrapper_and_encoded_traversal_are_rejected(self):
        item, _ = fixture("gitlab")
        bad = ("https://evil.example/mod.zip", "https://gitlab.com/group/other/-/uploads/abc/mod.zip",
               "https://gitlab.com/group/sub/project/-/uploads/%2e%2e/else.zip",
               "https://gitlab.com/group/sub/project/-/uploads/abc/mod.zip?private_token=secret")
        for url in bad:
            altered = copy.deepcopy(item)
            altered["update"]["releaseAssets"][0]["url"] = url
            with self.subTest(url=url), self.assertRaises(PlanError):
                validate_release_asset(snapshot(altered), selection(altered))
        altered = copy.deepcopy(item)
        altered["update"]["releaseAssets"][0].update(url="https://gitlab.com/group/sub/project/-/releases/v2.0/downloads/mod.zip", originUrl="https://evil.example/mod.zip")
        self.assertFalse(asset_matches_source(altered["source"], altered["update"], altered["update"]["releaseAssets"][0]))

    def test_other_github_tag_and_modrinth_version_cannot_reuse_an_asset_identity(self):
        for provider, old, replacement in (("github", "/v2.0/", "/v1.0/"), ("modrinth", "/IjKlMnOp/", "/QrStUvWx/")):
            item, _ = fixture(provider)
            item["update"]["releaseAssets"][0]["url"] = item["update"]["releaseAssets"][0]["url"].replace(old, replacement)
            with self.subTest(provider=provider), self.assertRaises(PlanError):
                validate_release_asset(snapshot(item), selection(item))

    def test_modrinth_non_mo2_file_remains_visible_but_cannot_queue(self):
        item, raw = fixture("modrinth")
        raw["releaseAssets"][0]["name"] = "mod.jar"
        raw["releaseAssets"][0]["url"] = raw["releaseAssets"][0]["url"].replace("mod.zip", "mod.jar")
        raw["releaseAssets"][0]["installable"] = False
        item["update"] = normalize_adapter_record(item, raw)
        self.assertEqual(item["update"]["releaseAssets"][0]["name"], "mod.jar")
        self.assertIsNone(choose_release_asset(item)[0])
        with self.assertRaises(PlanError):
            validate_release_asset(snapshot(item), selection(item))

    def test_manual_source_capabilities_preserve_unverified_and_strip_credentials(self):
        providers = {"https://creator.itch.io/mod": "itch", "https://creator.tumblr.com/post/123": "tumblr",
                     "https://www.subscribestar.adult/creator": "subscribestar", "https://afdian.com/a/creator": "afdian",
                     "https://modcollective.gg/mods/123": "modcollective", "https://simsfinds.com/downloads/123": "simsfinds",
                     "https://valheim.thunderstore.io/package/Team/Mod": "thunderstore"}
        for url, provider in providers.items():
            self.assertEqual(provider_of(url), provider)
        self.assertEqual(provider_of("https://patreon.com.evil.example/posts/1"), "website")
        cap = adapter_capabilities("curseforge")
        self.assertFalse(cap["versionCheck"])
        self.assertEqual(cap["authentication"], "api-key")
        url = canonical_source_url("https://gitlab.com/group/mod?private_token=secret&auth_token=secret&tab=files")
        self.assertEqual(url, "https://gitlab.com/group/mod?tab=files")
        item = {"source": {"url": "https://www.patreon.com/posts/123", "verified": True}}
        update = normalize_adapter_record(item, {"provider": "patreon", "sourceUrl": item["source"]["url"], "outcome": "manual", "reason": "Use original page"})
        self.assertEqual(update["verdict"], "unverified")
        self.assertFalse(update["capabilities"]["reviewedDownload"])

    def test_advisory_dependency_link_is_not_a_project_match(self):
        item, raw = fixture()
        item["source"].update(verified=False, kind="advisory")
        raw.update(outcome="manual", checkedAt=None)
        update = normalize_adapter_record(item, raw)
        self.assertEqual(update["verdict"], "unverified")
        self.assertFalse(update["capabilities"]["versionCheck"])

    def test_nested_gitlab_project_lookup_keeps_entire_namespace(self):
        calls = []
        item, _ = fixture()
        item["source"]["url"] = "https://gitlab.com/group/sub/project"
        result = SourceScanner(lambda url: calls.append(url) or {"tag_name": "v2.0"}).scan(snapshot(item))
        self.assertEqual(len(calls), 1)
        self.assertIn("group%2Fsub%2Fproject", calls[0])
        self.assertEqual(result["itemRecords"][item["id"]]["verdict"], "candidate")

    def test_compact_record_preserves_actions_without_full_payload(self):
        item, _ = fixture()
        compact = compact_source_update(item["update"])
        self.assertNotIn("changelog", compact)
        self.assertNotIn("releaseAssets", compact)
        self.assertNotIn("dependencies", compact)
        self.assertEqual(compact["releaseAssetCount"], 1)
        self.assertEqual(compact["dependencyCount"], 1)
        self.assertTrue(compact["detailsAvailable"])


class RustAdapterBridgeTests(unittest.TestCase):
    def test_rust_request_cache_persistence_and_cancellation_are_owned(self):
        item, raw = fixture()
        request_seen = []
        class Runner:
            available = True
            def run(self, request, *, cancel_event=None):
                request_seen.append(request)
                return {"schemaVersion": 1, "profileId": "p", "itemRecords": {item["id"]: raw},
                        "cache": {"entries": []}, "stats": {"sourceProjects": 1, "queried": 1}, "errors": []}
        with tempfile.TemporaryDirectory() as tmp:
            scanner = SourceScanner(runner=Runner(), cache_path=Path(tmp) / "cache.json")
            result = scanner.scan(snapshot(item))
            self.assertEqual(result["sourceEngine"], "rust")
            self.assertEqual(result["itemRecords"][item["id"]]["verdict"], "candidate")
            self.assertEqual(request_seen[0]["workers"], 4)
            self.assertEqual(request_seen[0]["budgetMs"], 45000)
            self.assertEqual(request_seen[0]["items"][0]["source"]["url"], item["source"]["url"])
            self.assertEqual(json.loads((Path(tmp)/"cache.json").read_text()), {"entries": []})
            cancel = threading.Event(); cancel.set()
            self.assertTrue(scanner.scan(snapshot(item), cancel_event=cancel)["cancelled"])

    def test_protocol_rejects_wrong_profile_unrequested_ids_and_schema(self):
        runner = RustSourceRunner()
        request = {"profileId": "p", "items": [{"id": "known"}]}
        for result in ({"schemaVersion": 2, "profileId": "p", "itemRecords": {}},
                       {"schemaVersion": 1, "profileId": "other", "itemRecords": {}},
                       {"schemaVersion": 1, "profileId": "p", "itemRecords": {"unknown": {}}}):
            with self.subTest(result=result), self.assertRaises(RuntimeError):
                runner._validate_result(result, request)

    def test_failed_rust_engine_does_not_silently_retry_via_another_transport(self):
        item, _ = fixture()
        class Runner:
            available = True
            def run(self, *args, **kwargs):
                raise RuntimeError("Provider cooldown or helper unavailable")
        scanner = SourceScanner(runner=Runner())
        scanner.fetch_json = lambda *_: self.fail("Unexpected fallback bypasses bounded Rust failure")
        result = scanner.scan(snapshot(item))
        self.assertEqual(result["itemRecords"][item["id"]]["verdict"], "unknown")
        self.assertTrue(result["errors"])


class SourceServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        item, _ = fixture()
        self.item = item
        self.calls = []
        def native(request):
            self.calls.append(request)
            request.finish({"ok": True, "downloadId": 501})
        self.service = ArsenalService("<html></html>", Path(self.temp.name)/"cache.json", native, snapshot(item))
        self.addCleanup(self.service.stop)

    def test_source_details_are_indexed_scoped_and_catalog_plan_polling_stay_compact(self):
        catalog = self.service.snapshot()
        self.assertNotIn("changelog", catalog["items"][0]["update"])
        self.assertEqual(catalog["items"][0]["update"]["releaseAssetCount"], 1)
        details = self.service.source_details(self.item["id"], "p")
        self.assertTrue(details["ok"])
        self.assertIn("Changes", details["update"]["changelog"])
        self.assertFalse(self.service.source_details(self.item["id"], "other")["ok"])
        self.assertFalse(self.service.source_details("missing", "p")["ok"])
        self.assertEqual(plan_updates(catalog)["items"][0]["releaseAssetCount"], 1)
        self.assertIsNone(plan_updates(catalog)["items"][0]["sourceEvidence"])
        self.assertIsNotNone(plan_updates(self.service.snapshot(source_details=True))["items"][0]["sourceEvidence"])
        self.service._scan = {"state": "completed", "itemRecords": {"x": {"changelog": "LARGE"}}, "queried": 1}
        self.assertNotIn("itemRecords", self.service.scan_state())

    def test_generalized_download_and_install_proof_reject_changed_source_bytes_metadata(self):
        queued = self.service.queue_reviewed_downloads({"profileId": "p", "confirm": "QUEUE_REVIEWED_DOWNLOADS", "entries": [selection(self.item)]})
        self.assertEqual(queued["queued"][0]["provider"], "gitlab")
        self.assertEqual(len(self.calls), 1)
        native = self.calls[0]
        self.assertEqual(native.cmd, "download-url")
        self.assertEqual(native.payload["assetProof"], selection(self.item)["assetProof"])
        self.service.set_download_status(501, "complete")
        self.assertTrue(self.service.preview_install(501)["canInstall"])
        self.service._items_by_id[self.item["id"]]["update"]["releaseAssets"][0]["hashes"] = {"sha256": "b" * 64}
        brief = self.service.preview_install(501)
        self.assertFalse(brief["canInstall"])
        self.assertTrue(any("archive" in message for message in brief["errors"]))
        result = self.service.install_reviewed_download(501)
        self.assertFalse(result["ok"])
        self.assertEqual(len(self.calls), 1)

    def test_same_archive_for_two_rows_is_queued_once_and_cannot_be_queued_again(self):
        second = copy.deepcopy(self.item)
        second["id"] = "second-row"
        updated = snapshot(self.item)
        updated["items"].append(second)
        self.service.set_snapshot(updated)
        queued = self.service.queue_reviewed_downloads({"profileId": "p", "confirm": "QUEUE_REVIEWED_DOWNLOADS",
                                                       "entries": [selection(self.item), selection(second)]})
        self.assertEqual(len(queued["queued"]), 1)
        self.assertEqual(queued["skipped"][0]["itemId"], second["id"])
        self.assertEqual(len(self.calls), 1)
        again = self.service.queue_reviewed_downloads({"profileId": "p", "confirm": "QUEUE_REVIEWED_DOWNLOADS",
                                                      "entries": [selection(second)]})
        self.assertFalse(again["ok"])
        self.assertEqual(len(self.calls), 1)

    def test_profile_change_sets_active_source_cancellation(self):
        started = threading.Event()
        stopped = threading.Event()
        class Scanner:
            def scan(self, snapshot, progress, *, cancel_event=None):
                started.set()
                if cancel_event.wait(2):
                    stopped.set()
                return {"records": {}, "itemRecords": {}, "errors": [], "queried": 0}
        self.service._source_scanner = Scanner()
        self.assertTrue(self.service.begin_scan("sources", "p")["ok"])
        self.assertTrue(started.wait(1))
        changed = snapshot(copy.deepcopy(self.item)); changed["profileId"] = "other"
        self.service.set_snapshot(changed)
        self.assertTrue(stopped.wait(1))
        self.assertEqual(self.service.scan_state()["state"], "idle")

    def test_authenticated_http_details_and_explicit_full_export(self):
        base = self.service.start()
        def get(path, token=True):
            request = Request(base.rstrip("/") + path, headers={"X-Arsenal-Token": self.service.token} if token else {})
            try:
                with urlopen(request, timeout=2) as response:
                    return response.status, json.load(response)
            except HTTPError as exc:
                return exc.code, json.load(exc)
        self.assertEqual(get("/api/source-details?profileId=p&itemId=live-source", token=False)[0], 403)
        code, details = get("/api/source-details?profileId=p&itemId=live-source")
        self.assertEqual(code, 200)
        self.assertTrue(details["ok"])
        self.assertIn("releaseAssets", details["update"])
        self.assertIsNone(get("/api/update-plan")[1]["items"][0]["sourceEvidence"])
        self.assertIn("changelog", get("/api/update-plan?details=full")[1]["items"][0]["sourceEvidence"])


if __name__ == "__main__":
    unittest.main()
