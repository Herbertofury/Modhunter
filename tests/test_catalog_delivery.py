"""Immutable full-catalog delivery and exact-profile progress execution."""
from __future__ import annotations

import copy
import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mo2_update_arsenal.bridge import ArsenalService


def catalog(count=2500, profile="large"):
    return {"profileId": profile, "profileName": profile, "game": "skyrimspecialedition",
            "gameRuntime": "1.5.97", "groups": [], "items": [
                {"id": f"live-{i}", "name": f"Mod {i}", "nexusId": i + 1,
                 "installedVersion": "1.0", "flags": [],
                 "source": {"type": "nexus", "verified": True, "kind": "project",
                            "url": f"https://www.nexusmods.com/skyrimspecialedition/mods/{i + 1}"},
                 "update": {"source": "nexus-api", "verdict": "candidate", "latestVersion": "2.0"}}
                for i in range(count)]}


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        # Cache ownership assertions must not straddle a legitimate 15-second
        # freshness rollover; the rollover itself has a dedicated test below.
        clock = patch("mo2_update_arsenal.bridge.time.time", return_value=1_794_500_000.0)
        clock.start()
        self.addCleanup(clock.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = ArsenalService("<html/>", Path(self.temp.name) / "cache.json",
            lambda req: req.finish({"ok": True}), catalog())
        self.service.intelligence.runner = SimpleNamespace(available=False)
        self.addCleanup(self.service.stop)

    def call(self, method, endpoint, data=None):
        if self.service._server is None:
            self.service.start()
        port = self.service._server.server_port
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        headers = {"X-Arsenal-Token": self.service.token, "Origin": f"http://127.0.0.1:{port}"}
        conn.request(method, "/api/" + endpoint, body=json.dumps(data) if data is not None else None,
                     headers=headers)
        response = conn.getresponse()
        result = response.status, response.read()
        conn.close()
        return result

    def progress(self, rows=None, **extras):
        return {"profileId": "large", "rows": rows or {}, "prefs": {}, **extras}

    def test_warm_response_reuses_bytes_and_keeps_every_mod(self):
        with patch.object(self.service, "snapshot_response", wraps=self.service.snapshot_response) as build:
            first = self.service.snapshot_response_bytes()
            second = self.service.snapshot_response_bytes()
        self.assertIs(first, second)
        self.assertEqual(build.call_count, 1)
        response = json.loads(first)
        self.assertEqual(len(response["catalog"]["items"]), 2500)
        self.assertEqual(response["catalog"]["items"][-1]["id"], "live-2499")
        response["catalog"]["items"][0]["name"] = "Caller changed its JSON"
        self.assertEqual(json.loads(second)["catalog"]["items"][0]["name"], "Mod 0")

    def test_concurrent_callers_share_one_builder(self):
        barrier = threading.Barrier(9)
        results = []
        def get():
            barrier.wait(3)
            results.append(self.service.snapshot_response_bytes())
        with patch.object(self.service, "snapshot_response", wraps=self.service.snapshot_response) as build:
            threads = [threading.Thread(target=get) for _ in range(8)]
            for thread in threads:
                thread.start()
            barrier.wait(3)
            for thread in threads:
                thread.join(5)
                self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), 8)
        self.assertTrue(all(value is results[0] for value in results))
        self.assertEqual(build.call_count, 1)

    def test_http_snapshot_uses_cache_without_preparing_or_encoding_again(self):
        status, first = self.call("GET", "snapshot")
        self.assertEqual(status, 200)
        with patch.object(self.service, "snapshot", side_effect=AssertionError("Catalog rebuilt")), \
             patch("mo2_update_arsenal.bridge.json.dumps", side_effect=AssertionError("JSON encoded again")):
            status, second = self.call("GET", "snapshot")
        self.assertEqual((status, second), (200, first))

    def test_profile_switch_invalidates_cache_with_matching_generation(self):
        first = self.service.snapshot_response_bytes()
        self.service.set_snapshot(catalog(3, "replacement"))
        second = self.service.snapshot_response_bytes()
        self.assertIsNot(first, second)
        value = json.loads(second)
        self.assertEqual(value["catalog"]["profileId"], "replacement")
        self.assertEqual(value["generation"], self.service.revision()["generation"])
        self.assertEqual(value["catalog"]["executionEpoch"], self.service._execution_epoch)

    def test_serialization_does_not_hold_execution_lock_or_publish_obsolete_cache(self):
        entered, release, health_done = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        encode = json.dumps
        responses, failures = [], []
        def delayed(obj, *args, **kwargs):
            if isinstance(obj, dict) and "catalog" in obj and obj["catalog"]["profileId"] == "large":
                entered.set()
                if not release.wait(4):
                    raise RuntimeError("Test serialization was not released")
            return encode(obj, *args, **kwargs)
        def get():
            try:
                responses.append(self.service.snapshot_response_bytes())
            except Exception as exc:
                failures.append(exc)
        with patch("mo2_update_arsenal.bridge.json.dumps", side_effect=delayed):
            worker = threading.Thread(target=get)
            worker.start()
            self.assertTrue(entered.wait(3))
            probe = threading.Thread(target=lambda: (self.service.health(), health_done.set()))
            probe.start()
            self.assertTrue(health_done.wait(.5), "JSON serialization blocked the MO2 execution lock")
            self.service.set_snapshot(catalog(2, "replacement"))
            release.set()
            worker.join(3)
            probe.join(3)
        self.assertFalse(failures)
        self.assertFalse(worker.is_alive())
        self.assertEqual(json.loads(responses[0])["catalog"]["profileId"], "large")
        current = json.loads(self.service.snapshot_response_bytes())
        self.assertEqual(current["catalog"]["profileId"], "replacement")
        self.assertEqual(current["generation"], self.service.revision()["generation"])

    def test_metadata_source_and_audit_state_each_invalidate_without_native_refresh(self):
        first = self.service.snapshot_response_bytes()
        self.service.cache.set("skyrimspecialedition", 1,
            {"meta": {"version": "2.0", "status": "published"}, "checkedAt": time.time()})
        second = self.service.snapshot_response_bytes()
        self.assertIsNot(first, second)
        self.service.chronicle.set_progress("large", {"live-0": {"url": "https://github.com/example/mod"}}, {}, "", {"live-0"})
        third = self.service.snapshot_response_bytes()
        self.assertEqual(json.loads(third)["catalog"]["items"][0]["source"]["url"], "https://github.com/example/mod")
        self.assertIsNot(second, third)
        self.service.intelligence.start_audit("large")
        fourth = self.service.snapshot_response_bytes()
        self.assertIsNot(third, fourth)
        self.assertEqual(json.loads(fourth)["catalog"]["pluginAuditState"]["state"], "unavailable")

    def test_time_window_refreshes_cached_health_freshness(self):
        with patch("mo2_update_arsenal.bridge.time.time", return_value=10000):
            first = self.service.snapshot_response_bytes()
        with patch("mo2_update_arsenal.bridge.time.time", return_value=10001):
            self.assertIs(self.service.snapshot_response_bytes(), first)
        with patch("mo2_update_arsenal.bridge.time.time", return_value=10020):
            self.assertIsNot(self.service.snapshot_response_bytes(), first)

    def test_metadata_replacement_with_same_timestamp_cannot_reuse_old_health_summary(self):
        stamp = time.time()
        self.service.cache.set("skyrimspecialedition", 1,
            {"meta": {"version": "2.0", "status": "published"}, "checkedAt": stamp})
        first = json.loads(self.service.snapshot_response_bytes())
        self.assertEqual(first["catalog"]["items"][0]["modHealth"]["page"]["status"], "available")
        self.service.cache.set("skyrimspecialedition", 1,
            {"meta": {"version": "2.0", "status": "hidden"}, "checkedAt": stamp})
        second = json.loads(self.service.snapshot_response_bytes())
        self.assertEqual(second["catalog"]["items"][0]["modHealth"]["page"]["status"], "hidden")

    def test_compact_plugin_projection_does_not_share_nested_mutable_evidence(self):
        self.service.intelligence._result = {"depth": "records", "checkedAt": 123,
            "inventoryComplete": True, "mods": {"live-0": {"status": "analyzed", "plugins": [
                {"name": "Fixture.esp", "winner": True, "header": {"lightEffective": True},
                 "masterIssues": [{"name": "Missing.esm", "status": "missing"}],
                 "records": {"scanComplete": True, "newRecordTypes": {"WEAP": 12}},
                 "errors": [{"message": "Fixture diagnostic"}], "provenance": {"state": "not-configured"}}]}}}
        compact = self.service.snapshot()["items"][0]["localAnalysis"]["plugins"][0]
        self.assertNotIn("records", compact)
        self.assertTrue(compact["recordScanComplete"])
        compact["masterIssues"][0]["name"] = "Caller changed master"
        compact["errors"][0]["message"] = "Caller changed error"
        detailed = self.service.selection_snapshot(["live-0"])["items"][0]["localAnalysis"]["plugins"][0]
        self.assertEqual(detailed["masterIssues"][0]["name"], "Missing.esm")
        self.assertEqual(detailed["errors"][0]["message"], "Fixture diagnostic")
        self.assertEqual(detailed["records"]["newRecordTypes"], {"WEAP": 12})

    def test_oversized_catalog_is_complete_but_not_retained_in_cache(self):
        with patch("mo2_update_arsenal.bridge.MAX_CACHED_SNAPSHOT", 64), \
             patch.object(self.service, "snapshot_response", wraps=self.service.snapshot_response) as build:
            first = self.service.snapshot_response_bytes()
            second = self.service.snapshot_response_bytes()
        self.assertEqual(build.call_count, 2)
        self.assertIsNone(self.service._snapshot_payload_cache)
        self.assertEqual(first, second)
        self.assertEqual(len(json.loads(second)["catalog"]["items"]), 2500)

    def test_encoding_error_releases_builder_for_retry(self):
        with patch("mo2_update_arsenal.bridge.json.dumps", side_effect=ValueError("encoding failed")):
            with self.assertRaisesRegex(ValueError, "encoding failed"):
                self.service.snapshot_response_bytes()
        self.assertIsNone(self.service._snapshot_payload_cache)
        self.assertTrue(json.loads(self.service.snapshot_response_bytes())["ok"])

    def test_profile_only_posts_do_not_prepare_full_catalog(self):
        actions = (("progress", self.progress({"live-0": {"note": "Keep my choice", "favorite": True}})),
                   ("realm/purchase", {"profileId": "large", "sku": "rift-key", "txn": "fixture-purchase-123"}),
                   ("realm/world", {"profileId": "large", "revision": 0, "world": {"v": 1}}),
                   ("siege/state", {"profileId": "large", "revision": 0, "state": {"v": 1}}),
                   ("victory/ack", {"profileId": "large", "victoryId": "not-earned"}),
                   ("siege/boss/defeated", {"profileId": "large", "modId": "live-0", "runId": "fixture-run-123"}))
        with patch.object(self.service, "snapshot", side_effect=AssertionError("Unnecessary catalog preparation")):
            for endpoint, payload in actions:
                with self.subTest(endpoint=endpoint):
                    self.assertEqual(self.call("POST", endpoint, payload)[0], 200)
        self.assertEqual(self.service.chronicle.progress("large")["rows"]["live-0"]["note"], "Keep my choice")

    def test_notes_preserve_execution_epoch_but_source_round_trip_expires_old_intent(self):
        epoch, generation = self.service._execution_epoch, self.service._generation
        notes = self.service.save_progress(self.progress({"live-0": {"note": "My notes", "favorite": True}}))
        self.assertTrue(notes["ok"])
        self.assertFalse(notes["sourceLinksChanged"])
        self.assertEqual((notes["executionEpoch"], notes["generation"]), (epoch, generation))
        original_source = copy.deepcopy(self.service._items_by_id["live-0"]["source"])
        self.service._scan = {"state": "running"}
        self.service._file_cache[("skyrimspecialedition", 1)] = {"ok": True, "files": []}
        old_cancel = self.service._scan_cancel
        change = self.service.save_progress(self.progress({"live-0": {"url": "https://github.com/example/mod"}}))
        self.assertTrue(change["sourceLinksChanged"])
        self.assertEqual(change["executionEpoch"], epoch + 1)
        self.assertTrue(old_cancel.is_set())
        self.assertEqual(self.service.scan_state()["state"], "stale")
        self.assertEqual(self.service._file_cache, {})
        self.assertEqual(self.service._items_by_id["live-0"]["source"], original_source)
        self.assertNotIn("update", self.service.selection_snapshot(["live-0"])["items"][0])
        stale = self.service.save_progress(self.progress({"live-0": {"url": "https://github.com/example/stale"}}, executionEpoch=epoch))
        self.assertFalse(stale["ok"])
        self.assertEqual(self.service._execution_epoch, epoch + 1)
        restored = self.service.save_progress(self.progress({"live-0": {"note": "Native source again"}}))
        self.assertEqual(restored["executionEpoch"], epoch + 2)
        self.assertEqual(self.service.selection_snapshot(["live-0"])["items"][0]["source"], original_source)
        rejected = self.service.quick_update({"profileId": "large", "itemIds": ["live-0"], "executionEpoch": epoch})
        self.assertFalse(rejected["ok"])

    def test_invalid_or_foreign_progress_cannot_change_sources_or_epochs(self):
        first = self.service.snapshot_response_bytes()
        epoch = self.service._execution_epoch
        self.assertFalse(self.service.save_progress(self.progress(rows="invalid"))["ok"])
        status, body = self.call("POST", "progress", self.progress({"live-0": {"url": "https://github.com/example/wrong"}}, profileId="foreign"))
        self.assertEqual(status, 409)
        self.assertFalse(json.loads(body)["ok"])
        self.assertEqual(self.service._execution_epoch, epoch)
        self.assertIs(self.service.snapshot_response_bytes(), first)

    def test_persistence_failure_keeps_changed_source_protected_and_reports_failure(self):
        epoch = self.service._execution_epoch
        with patch.object(self.service.chronicle, "persist", side_effect=OSError("fixture disk failure")):
            result = self.service.save_progress(self.progress({"live-0": {"url": "https://github.com/example/mod"}}))
        self.assertFalse(result["ok"])
        self.assertIn("persist", result["error"])
        self.assertTrue(result["sourceLinksChanged"])
        self.assertEqual(self.service._execution_epoch, epoch + 1)
        self.assertNotIn("update", self.service.selection_snapshot(["live-0"])["items"][0])

    def test_source_scan_keeps_native_identity_and_reverts_cleanly(self):
        native = copy.deepcopy(self.service._items_by_id["live-0"]["source"])
        url = "https://github.com/example/mod"
        self.service.save_progress(self.progress({"live-0": {"url": url}}))
        def scan(snapshot, *args, **kwargs):
            self.assertEqual(snapshot["items"][0]["source"]["url"], url)
            return {"records": {}, "itemRecords": {"live-0": {"verdict": "candidate", "latestVersion": "3.0", "sourceUrl": url}}, "errors": [], "queried": 1}
        with patch("mo2_update_arsenal.bridge.SourceScanner.scan", side_effect=scan):
            self.assertTrue(self.service.begin_scan("sources", "large")["ok"])
            deadline = time.monotonic() + 3
            while self.service.scan_state()["state"] == "running" and time.monotonic() < deadline:
                time.sleep(.01)
        self.assertEqual(self.service.scan_state()["state"], "completed")
        row = self.service.selection_snapshot(["live-0"])["items"][0]
        self.assertEqual(row["update"]["latestVersion"], "3.0")
        self.assertEqual(self.service._items_by_id["live-0"]["source"], native)
        self.service.save_progress(self.progress({"live-0": {"note": "Use native source"}}))
        reverted = self.service.selection_snapshot(["live-0"])["items"][0]
        self.assertEqual(reverted["source"], native)
        self.assertNotIn("update", reverted)

    def test_source_edit_discards_a_scan_already_running(self):
        entered, release = threading.Event(), threading.Event()
        workers = []
        self.addCleanup(release.set)
        def scan(snapshot, *args, **kwargs):
            workers.append(threading.current_thread())
            entered.set()
            release.wait(3)
            return {"records": {}, "itemRecords": {"live-0": {"verdict": "candidate", "latestVersion": "99.0"}}, "errors": [], "queried": 1}
        with patch("mo2_update_arsenal.bridge.SourceScanner.scan", side_effect=scan):
            self.assertTrue(self.service.begin_scan("sources", "large")["ok"])
            self.assertTrue(entered.wait(3))
            result = self.service.save_progress(self.progress({"live-0": {"url": "https://github.com/example/new"}}))
            release.set()
            workers[0].join(3)
            self.assertFalse(workers[0].is_alive())
        self.assertEqual(self.service._generation, result["generation"])
        self.assertEqual(self.service.scan_state()["state"], "stale")
        self.assertNotIn("update", self.service._items_by_id["live-0"])


if __name__ == "__main__":
    unittest.main()
