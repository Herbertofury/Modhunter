"""Batched startup keeps each signed file history and its exact installed rows."""
import io
import json
import re
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from mo2_update_arsenal.bridge import ArsenalService
from mo2_update_arsenal.scanner import (
    Cache, NativeScanCatalog, NexusClient, NexusError, PublicNexusClient, QuotaDepleted,
    PUBLIC_BATCH_SIZE, PUBLIC_BATCH_WORKERS, STARTUP_CACHE_SECONDS, UpdateScanner,
    public_batch_metadata,
)

GAME = "skyrimspecialedition"


def raw_project(mid):
    return {"modId": mid, "game": {"domainName": GAME}, "name": f"Mod {mid}", "version": "1",
            "status": "published", "updatedAt": "2026-04-02T00:00:00Z"}


def project(mid):
    return public_batch_metadata(GAME, mid, raw_project(mid))


def row(mid):
    return {"id": str(mid), "nexusId": mid, "installedVersion": "1", "installedFiles": [[mid, 100 + mid]]}


class BulkStartupTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.cache = Cache(Path(directory.name) / "cache.json")

    def test_alias_errors_hidden_cross_game_and_invalid_projects_fall_back_individually(self):
        ids = list(range(1, 9))
        data = {f"p{i}": raw_project(i) for i in ids}
        data["p2"]["status"] = "hidden"
        data["p3"]["game"] = {"domainName": "othergame"}
        data["p4"]["modId"] = 9
        data["p5"] = None
        data["p6"]["updatedAt"] = "invalid"
        response = {"data": data, "errors": [{"message": "field failed", "path": ["p7", "name"]}]}
        def http(request, timeout):
            body = json.loads(request.data)
            if "domain" in body["variables"]:
                return io.BytesIO(json.dumps({"data": {"game": {"id": 1704, "domainName": GAME}}}).encode())
            self.assertNotIn("description", body["query"])
            self.assertIn("p8:mod", body["query"])
            self.assertNotIn("modFiles", body["query"])
            return io.BytesIO(json.dumps(response).encode())
        with patch("urllib.request.urlopen", http):
            client = PublicNexusClient(GAME)
            self.assertEqual(set(client.get_projects(ids)), {1, 8})
            for invalid in ([1, 1], ["1"], [True], list(range(1, PUBLIC_BATCH_SIZE + 2))):
                with self.assertRaises(ValueError):
                    client.get_projects(invalid)
            response["errors"] = [{"message": "unscoped"}]
            with self.assertRaises(NexusError):
                client.get_projects(ids)
            response.clear()
            response["errors"] = [{"extensions": {"code": "RATE_LIMITED"}}]
            with self.assertRaises(QuotaDepleted):
                client.get_projects(ids)

    def test_published_pages_save_signed_metadata_calls_and_cold_edges_remain_authoritative(self):
        client = Mock()
        client.get_projects.side_effect = lambda ids: {i: project(i) for i in ids if i != 2}
        lookup = NativeScanCatalog(GAME, lambda _: client)
        items = [row(i) for i in range(1, 38)]
        items.append({**row(3), "id": "same-project", "installedVersion": "2"})
        items[3].pop("installedFiles")
        paths = []
        def native(path):
            paths.append(path)
            mid = int(re.search(r"/mods/(\d+)", path)[1])
            if not path.endswith("/files.json"):
                return {"mod_id": mid, "domain_name": GAME, "status": "published", "version": "1"}
            # The public group has exactly one active member. Only V1 exposes
            # its outgoing edge to an absent/withdrawn target.
            edges = [{"old_file_id": 101, "new_file_id": 901}] if mid == 1 else []
            return {"files": [{"fileId": 100 + mid, "version": "1", "category": 1}], "file_updates": edges}
        scanner = UpdateScanner(self.cache, NexusClient("fixture", native), native_catalog=lookup)
        result = scanner.scan({"game": GAME, "items": items}, "startup")
        warm = scanner.scan({"game": GAME, "items": items}, "startup")
        self.assertEqual((result["publicBatches"], result["publicGraphRequests"]), (3, 4))
        self.assertEqual((result["providerProjectRequests"], result["providerFileRequests"]), (1, 36))
        self.assertEqual(result["queried"], 37)
        self.assertEqual(len(paths), 37)
        self.assertFalse(result["errors"])
        self.assertEqual(result["itemRecords"]["1"]["verdict"], "review")
        self.assertEqual(result["itemRecords"]["3"]["targetFileId"], 103)
        self.assertEqual(result["itemRecords"]["same-project"]["verdict"], "review")
        self.assertEqual(result["itemRecords"]["4"]["evidenceScope"], "project-only")
        self.assertEqual(self.cache.get(GAME, 1)["meta"]["fileCatalog"]["file_updates"][0]["new_file_id"], 901)
        self.assertEqual((warm["queried"], warm["publicGraphRequests"]), (0, 0))
        self.assertEqual(warm["itemRecords"], result["itemRecords"])

    def test_stale_prior_file_history_cannot_acquire_a_new_check_time(self):
        source = project(42)
        prior = {**source, "fileCatalog": {"files": [], "file_updates": []}}
        self.cache.set(GAME, 42, {"meta": prior, "checkedAt": time.time() - STARTUP_CACHE_SECONDS - 1})
        client = Mock(get_projects=Mock(return_value={42: source}))
        native = Mock(return_value={"files": [], "file_updates": []})
        lookup = NativeScanCatalog(GAME, lambda _: client)
        scanner = UpdateScanner(self.cache, NexusClient("fixture", native), native_catalog=lookup)
        result = scanner.scan({"game": GAME, "items": [row(42)]}, "startup")
        self.assertEqual(result["fromCache"], 0)
        native.assert_called_once_with(f"/v1/games/{GAME}/mods/42/files.json")
        self.assertFalse(lookup.matches_published(42, source, {"meta": {**prior, "available": False}}))

    def test_dashboard_startup_deep_and_all_reach_batched_pages_and_signed_native_file_callback(self):
        items = [row(i) for i in range(1, 6)]
        snapshot = {"profileId": "test", "profileName": "test", "game": GAME, "items": items}
        def dispatch(req):
            self.assertEqual(req.cmd, "nexus-files")
            mid = req.payload["modId"]
            req.finish({"ok": True, "files": [{"fileId": 100 + mid, "version": "1", "category": 1}], "file_updates": []})
        service = ArsenalService("", self.cache.path, dispatch, snapshot)
        self.addCleanup(service.stop)
        service.native_nexus_available = True
        service._source_scanner = Mock(scan=Mock(return_value={"itemRecords": {}, "errors": []}))
        client = Mock(get_projects=Mock(side_effect=lambda ids: {mid: project(mid) for mid in ids}))
        with patch("mo2_update_arsenal.scanner.NativeScanCatalog", return_value=NativeScanCatalog(GAME, lambda _: client)):
            for mode in ("startup", "deep", "all"):
                response = service.startup_check("test", 0) if mode == "startup" else service.begin_scan(mode, "test")
                self.assertTrue(response["ok"])
                deadline = time.monotonic() + 5
                while service.scan_state()["state"] == "running" and time.monotonic() < deadline:
                    time.sleep(.005)
                self.assertEqual(service.scan_state()["state"], "completed")
                self.assertEqual([r["update"]["targetFileId"] for r in service.snapshot()["items"]], [101, 102, 103, 104, 105])
                client.get_projects.assert_called_with([1, 2, 3, 4, 5])
        self.assertEqual(client.get_projects.call_count, 3)

    def test_other_batches_report_while_one_public_request_waits_and_cancel_discards_backlog(self):
        release = threading.Event()
        slow_started = threading.Event()
        cancel = threading.Event()
        events = []
        def batch(ids):
            if 1 in ids:
                slow_started.set()
                self.assertTrue(release.wait(5))
            else:
                self.assertTrue(slow_started.wait(5))
            return {i: project(i) for i in ids}
        client = Mock(get_projects=Mock(side_effect=batch))
        lookup = NativeScanCatalog(GAME, lambda _: client)
        items = [{"id": str(i), "nexusId": i} for i in range(1, 1001)]
        def progress(event):
            if event["checked"]:
                events.append(event)
                cancel.set()
                release.set()
        try:
            result = UpdateScanner(self.cache, NexusClient("fixture", lambda _: self.fail("native lookup")),
                                   native_catalog=lookup).scan({"game": GAME, "items": items}, "startup", progress, cancel_event=cancel)
        finally:
            release.set()
        self.assertTrue(result["cancelled"])
        self.assertLessEqual(client.get_projects.call_count, PUBLIC_BATCH_WORKERS)
        self.assertNotIn("1", events[0]["itemRecords"])
        self.assertEqual(len(result["itemRecords"]), 1000)

    def test_provider_rate_limit_stops_the_batch_and_signed_backlogs(self):
        client = Mock(get_projects=Mock(side_effect=QuotaDepleted("quota depleted")))
        lookup = NativeScanCatalog(GAME, lambda _: client)
        scanner = UpdateScanner(self.cache, NexusClient("fixture", lambda _: self.fail("signed fallback after 429")),
                                native_catalog=lookup)
        result = scanner.scan({"game": GAME, "items": [row(i) for i in range(1, 1001)]}, "startup")
        self.assertEqual(result["errors"], ["quota depleted"])
        self.assertLessEqual(client.get_projects.call_count, PUBLIC_BATCH_WORKERS)
        self.assertEqual({r["availability"] for r in result["itemRecords"].values()}, {"rate-limited"})
        self.assertTrue(result["rateLimitRetryEstimated"])

    def test_explicit_quota_deadline_survives_restart_and_preserves_signed_results(self):
        snapshot = {"game": GAME, "items": [row(1), row(2)]}
        saved = {**project(1), "fileCatalog": {"files": [{"fileId": 101, "name": "Known file", "version": "1", "category": 1}], "file_updates": []}}
        with patch("mo2_update_arsenal.scanner.time.time", return_value=1000):
            self.cache.set(GAME, 1, {"meta": saved, "checkedAt": 999})
            api = Mock(side_effect=QuotaDepleted("quota", "45"))
            graph = Mock(get_projects=Mock(return_value={2: project(2)}))
            first = UpdateScanner(self.cache, NexusClient("native", api), native_catalog=NativeScanCatalog(GAME, lambda _: graph))
            result = first.scan(snapshot, "startup")
            self.assertEqual(result["itemRecords"]["1"]["targetFileId"], 101)
            self.assertEqual(result["rateLimitRetryAt"], 1045)
            self.assertFalse(result["rateLimitRetryEstimated"])
        with patch("mo2_update_arsenal.scanner.time.time", return_value=1044):
            offline = Mock(side_effect=AssertionError("request during cooldown"))
            restored = UpdateScanner(Cache(self.cache.path), NexusClient("native", offline), native_catalog=NativeScanCatalog(GAME, offline))
            for mode in ("startup", "deep", "quick"):
                result = restored.scan(snapshot, mode)
                self.assertEqual(result["fromCache"], 1)
                self.assertEqual(result["queried"], 0)
                self.assertEqual(result["itemRecords"]["2"]["availability"], "rate-limited")
                self.assertEqual(result["itemRecords"]["2"]["retryAt"], 1045)
            offline.assert_not_called()
        with patch("mo2_update_arsenal.scanner.time.time", return_value=1046):
            api = Mock(return_value={"files": [{"fileId": 102, "version": "1", "category": 1}], "file_updates": []})
            resumed = UpdateScanner(Cache(self.cache.path), NexusClient("native", api), native_catalog=NativeScanCatalog(GAME, lambda _: graph))
            self.assertEqual(resumed.scan(snapshot, "startup")["itemRecords"]["2"]["targetFileId"], 102)
            api.assert_called_once_with(f"/v1/games/{GAME}/mods/2/files.json")

    def test_completed_inflight_files_are_checkpointed_after_another_project_receives_429(self):
        started, limited = threading.Event(), threading.Event()
        def files(path):
            if path.endswith("/1/files.json"):
                started.set()
                self.assertTrue(limited.wait(4))
                return {"files": [{"fileId": 101, "category": 1, "version": "1"}], "file_updates": []}
            self.assertTrue(started.wait(4))
            raise QuotaDepleted("429", "120")
        graph = Mock(get_projects=Mock(return_value={i: project(i) for i in (1, 2)}))
        engine = UpdateScanner(self.cache, NexusClient("fixture", files), native_catalog=NativeScanCatalog(GAME, lambda _: graph))
        checkpoint = self.cache.defer_for_scan
        def persisted(*args, **kwargs):
            checkpoint(*args, **kwargs)
            self.assertGreater(Cache(self.cache.path).quota_deadline(), time.time())
            limited.set()
        with patch.object(self.cache, "defer_for_scan", side_effect=persisted):
            result = engine.scan({"game": GAME, "items": [row(1), row(2)]}, "deep")
        self.assertEqual(result["itemRecords"]["1"]["targetFileId"], 101)
        self.assertEqual(result["itemRecords"]["2"]["availability"], "rate-limited")
        self.assertEqual(Cache(self.cache.path).get(GAME, 1)["meta"]["fileCatalog"]["files"][0]["fileId"], 101)

    def test_old_public_only_cache_needs_signed_file_history_with_or_without_a_route_marker(self):
        for mid, route in ((1, {"fileCatalogRoute": "public-file-groups"}), (2, {})):
            self.cache.set(GAME, mid, {"checkedAt": time.time(), "meta": {**project(mid), **route, "fileCatalog": {
                "providerGroups": True, "files": [{"fileId": 100 + mid, "version": "1", "category": 1, "groupId": mid}]}}})
        graph = Mock(get_projects=Mock(return_value={i: project(i) for i in (1, 2)}))
        api = Mock(return_value={"files": [], "file_updates": []})
        scanner = UpdateScanner(self.cache, NexusClient("fixture", api), native_catalog=NativeScanCatalog(GAME, lambda _: graph))
        result = scanner.scan({"game": GAME, "items": [row(1), row(2)]}, "startup")
        self.assertEqual(result["fromCache"], 0)
        self.assertEqual(api.call_count, 2)
        self.assertEqual({r["verdict"] for r in result["itemRecords"].values()}, {"review"})
