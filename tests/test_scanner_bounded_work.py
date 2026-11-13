"""Nexus audits bound queued work and stop old-profile requests promptly."""
from __future__ import annotations

import concurrent.futures
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from mo2_update_arsenal.scanner import Cache, MissingAuth, NexusClient, QuotaDepleted, UpdateScanner


def catalog(count=12000):
    return {"game": "skyrimspecialedition", "items": [
        {"id": f"mod-{i}", "nexusId": i + 1, "installedVersion": "1.0"} for i in range(count)]}


class BoundedNexusAuditTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.cache = Cache(Path(temporary.name) / "nexus.json")

    def test_large_audit_queues_only_five_and_cancellation_discards_late_cache_writes(self):
        entered, release, cancel = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        calls, submissions, results, errors = [], [], [], []
        lock = threading.Lock()
        executor = concurrent.futures.ThreadPoolExecutor

        class CountExecutor(executor):
            def submit(self, function, *args, **kwargs):
                submissions.append(args)
                if len(submissions) > 5 and not release.is_set():
                    raise AssertionError("Entire mod catalog submitted ahead of available workers")
                return super().submit(function, *args, **kwargs)

        def fetch(path):
            with lock:
                calls.append(path)
                if len(calls) == 5:
                    entered.set()
            if not release.wait(3):
                raise AssertionError("Blocked provider fixture was not released")
            return {"version": "2.0", "status": "published"}

        self.cache.set("skyrimspecialedition", 1, {"checkedAt": 99, "meta": {"version": "3.0"}})

        def work():
            try:
                results.append(UpdateScanner(self.cache, NexusClient("fixture", fetch)).scan(
                    catalog(), "deep", cancel_event=cancel))
            except Exception as error:
                errors.append(error)

        with patch("mo2_update_arsenal.scanner.concurrent.futures.ThreadPoolExecutor", CountExecutor):
            worker = threading.Thread(target=work)
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(len(submissions), 5)
                cancel.set()
            finally:
                release.set()
                worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(calls), 5)
        self.assertEqual(results[0]["queried"], 5)
        self.assertEqual(results[0]["cachedOrDeferred"], 11995)
        self.assertTrue(results[0]["cancelled"])
        self.assertEqual(len(results[0]["itemRecords"]), 12000)
        self.assertEqual({row["verdict"] for row in results[0]["itemRecords"].values()}, {"unknown"})
        self.assertEqual(self.cache.get("skyrimspecialedition", 1)["meta"]["version"], "3.0")
        self.assertIsNone(self.cache.get("skyrimspecialedition", 2))

    def test_window_refills_and_checks_every_project_once(self):
        calls = []

        def fetch(path):
            calls.append(path)
            return {"version": "2.0", "status": "published"}

        data = catalog(37)
        data["items"].append({**data["items"][0], "id": "second-copy", "installedVersion": "2.0"})
        result = UpdateScanner(self.cache, NexusClient("fixture", fetch)).scan(data, "deep")
        self.assertEqual(len(calls), 37)
        self.assertEqual(len(set(calls)), 37)
        self.assertEqual(result["queried"], 37)
        self.assertEqual(result["itemRecords"]["second-copy"]["verdict"], "current")
        self.assertEqual(result["itemRecords"]["mod-0"]["verdict"], "candidate")
        self.assertFalse(result["errors"])

    def test_quota_or_auth_failure_does_not_dispatch_remaining_projects(self):
        for failure in (QuotaDepleted, MissingAuth):
            with self.subTest(failure=failure.__name__):
                calls = []

                def fetch(path):
                    calls.append(path)
                    raise failure("Fixture provider halted")

                result = UpdateScanner(self.cache, NexusClient("fixture", fetch)).scan(catalog(1000), "deep")
                self.assertGreaterEqual(len(calls), 1)
                self.assertLessEqual(len(calls), 5)
                self.assertEqual(result["queried"], len(calls))
                self.assertEqual(len(result["itemRecords"]), 1000)
                self.assertEqual(result["errors"], ["Fixture provider halted"])
                self.assertEqual({row["verdict"] for row in result["itemRecords"].values()}, {"unknown"})

    def test_already_cancelled_quick_audit_makes_no_activity_or_metadata_request(self):
        cancel = threading.Event()
        cancel.set()

        def fetch(path):
            self.fail("Cancelled audit contacted Nexus")

        result = UpdateScanner(self.cache, NexusClient("fixture", fetch)).scan(
            catalog(2), "quick", cancel_event=cancel)
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["queried"], 0)
        self.assertEqual(len(result["itemRecords"]), 2)

    def test_cancellation_inside_cache_write_preserves_a_newer_result(self):
        entered, release, cancel = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        results = []
        write = self.cache.set_for_scan

        def blocked(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise AssertionError("Cache-write fixture was not released")
            return write(*args, **kwargs)

        scanner = UpdateScanner(self.cache, NexusClient("fixture", lambda _: {"version": "2.0"}))
        with patch.object(self.cache, "set_for_scan", side_effect=blocked):
            worker = threading.Thread(target=lambda: results.append(scanner.scan(
                catalog(1), "deep", cancel_event=cancel)))
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                cancel.set()
                self.cache.set("skyrimspecialedition", 1, {"checkedAt": 999, "meta": {"version": "3.0"}})
            finally:
                release.set()
                worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.cache.get("skyrimspecialedition", 1)["meta"]["version"], "3.0")
        self.assertTrue(results[0]["cancelled"])
        self.assertEqual(results[0]["itemRecords"]["mod-0"]["verdict"], "unknown")

    def test_newer_scan_owns_cache_even_when_older_scan_was_not_explicitly_cancelled(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        results = []

        def older_fetch(_):
            entered.set()
            if not release.wait(3):
                raise AssertionError("Old scan fixture was not released")
            return {"version": "2.0"}

        older = UpdateScanner(self.cache, NexusClient("fixture", older_fetch))
        worker = threading.Thread(target=lambda: results.append(older.scan(catalog(1), "deep")))
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            newer = UpdateScanner(self.cache, NexusClient("fixture", lambda _: {"version": "3.0"}))
            current = newer.scan(catalog(1), "deep")
            self.assertFalse(current["cancelled"])
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertTrue(results[0]["cancelled"])
        self.assertEqual(self.cache.get("skyrimspecialedition", 1)["meta"]["version"], "3.0")
        self.assertEqual(current["itemRecords"]["mod-0"]["latestVersion"], "3.0")


if __name__ == "__main__":
    unittest.main()
