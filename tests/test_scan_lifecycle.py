"""Deterministic scan ownership across profile switches, errors and late progress."""
from __future__ import annotations

import copy
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mo2_update_arsenal.bridge import ArsenalService
from mo2_update_arsenal.scanner import NexusError


PROFILE = {"profileId": "A", "profileName": "A", "game": "skyrimspecialedition", "gameRuntime": "1.5.97",
           "items": [{"id": "live-one", "name": "One", "nexusId": 0, "installedVersion": "1.0",
                      "source": {"url": "https://github.com/owner/project", "verified": True, "kind": "project"}}], "groups": []}


class ScanOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = ArsenalService("<html/>", Path(self.temp.name) / "cache.json", lambda request: None, copy.deepcopy(PROFILE))
        self.addCleanup(self.service.stop)
        self.jobs, self.threads = [], []
        self.lock = threading.Lock()
        actual_thread = threading.Thread
        def tracked_thread(*args, **kwargs):
            worker = actual_thread(*args, **kwargs)
            self.threads.append(worker)
            return worker
        self.thread_patch = patch("mo2_update_arsenal.bridge.threading.Thread", side_effect=tracked_thread)
        self.thread_patch.start()
        self.addCleanup(self.thread_patch.stop)
        self.scanner_patch = patch("mo2_update_arsenal.bridge.SourceScanner", return_value=SimpleNamespace(scan=self.blocked_scan))
        self.scanner_patch.start()
        self.addCleanup(self.scanner_patch.stop)
        self.addCleanup(self.release_all)

    def release_all(self):
        for job in self.jobs:
            job.release.set()
        for worker in self.threads:
            worker.join(2)

    def blocked_scan(self, snapshot, progress):
        with self.lock:
            job = self.jobs[len(self.threads) - 1]
        job.progress = progress
        progress({"phase": "active-" + str(job.index), "checked": job.index, "total": 1})
        job.started.set()
        if not job.release.wait(3):
            raise RuntimeError("Fixture did not release its scan")
        progress({"phase": "late-" + str(job.index), "checked": 999, "total": 1})
        if job.error:
            raise job.error
        return {"records": {}, "itemRecords": {"live-one": {"verdict": "candidate", "latestVersion": str(job.index + 2),
                                                             "source": "github-release-api"}},
                "queried": 1, "errors": [], "mode": "sources"}

    def start_job(self, error=None):
        job = SimpleNamespace(index=len(self.jobs), started=threading.Event(), release=threading.Event(), error=error)
        self.jobs.append(job)
        self.assertTrue(self.service.begin_scan("sources", self.service.profile_id())["ok"])
        self.assertTrue(job.started.wait(1))
        return job

    def finish_job(self, job):
        job.release.set()
        self.threads[job.index].join(2)
        self.assertFalse(self.threads[job.index].is_alive())

    def changed(self, profile):
        changed = copy.deepcopy(PROFILE)
        changed.update(profileId=profile, profileName=profile)
        self.service.set_snapshot(changed)

    def assert_superseded_does_not_touch_new_job(self, error=None):
        old = self.start_job(error)
        self.changed("B")
        current = self.start_job()
        expected_state = self.service.scan_state()
        generation = self.service._generation
        self.finish_job(old)
        self.assertEqual(self.service.scan_state(), expected_state)
        self.assertEqual(self.service._generation, generation)
        self.assertNotIn("update", self.service.snapshot()["items"][0])
        self.finish_job(current)
        self.assertEqual(self.service.scan_state()["state"], "completed")
        self.assertEqual(self.service.snapshot()["items"][0]["update"]["latestVersion"], "3")

    def test_old_completion_and_progress_cannot_overwrite_new_profile_scan(self):
        self.assert_superseded_does_not_touch_new_job()

    def test_old_expected_error_cannot_replace_new_scan_state(self):
        self.assert_superseded_does_not_touch_new_job(NexusError("Old connection failed"))

    def test_old_unexpected_error_cannot_replace_new_scan_state(self):
        self.assert_superseded_does_not_touch_new_job(RuntimeError("Old parser failed"))

    def test_a_to_b_to_a_cannot_accept_original_a_response(self):
        old = self.start_job()
        self.changed("B")
        self.changed("A")
        expected = self.service.scan_state()
        generation = self.service._generation
        self.finish_job(old)
        self.assertEqual(self.service.scan_state(), expected)
        self.assertEqual(self.service._generation, generation)
        self.assertNotIn("update", self.service.snapshot()["items"][0])

    def test_context_changes_invalidate_worker_without_changing_profile_name(self):
        variants = []
        for field, value in (("game", "fallout4"), ("gameRuntime", "1.6.1170")):
            changed = copy.deepcopy(PROFILE)
            changed[field] = value
            variants.append((field, changed))
        changed = copy.deepcopy(PROFILE)
        changed["items"][0]["installedVersion"] = "1.1"
        variants.append(("version", changed))
        changed = copy.deepcopy(PROFILE)
        changed["items"] = []
        variants.append(("removed row", changed))
        changed = copy.deepcopy(PROFILE)
        changed["items"][0]["source"]["url"] = "https://github.com/different/project"
        variants.append(("source", changed))
        for label, changed in variants:
            with self.subTest(change=label):
                self.service.set_snapshot(copy.deepcopy(PROFILE))
                old = self.start_job()
                self.service.set_snapshot(changed)
                expected = self.service.scan_state()
                generation = self.service._generation
                self.finish_job(old)
                self.assertEqual(self.service.scan_state(), expected)
                self.assertEqual(self.service._generation, generation)
                self.assertTrue(all("update" not in item for item in self.service.snapshot()["items"]))

    def test_unchanged_refresh_does_not_discard_valid_active_scan(self):
        job = self.start_job()
        self.service.set_snapshot(copy.deepcopy(PROFILE))
        self.finish_job(job)
        self.assertEqual(self.service.scan_state()["state"], "completed")
        self.assertEqual(self.service.snapshot()["items"][0]["update"]["latestVersion"], "2")

    def test_stop_invalidates_progress_and_completion_and_rejects_new_scans(self):
        old = self.start_job()
        self.service.stop()
        expected = self.service.scan_state()
        self.finish_job(old)
        self.assertEqual(self.service.scan_state(), expected)
        self.assertNotIn("update", self.service.snapshot()["items"][0])
        self.assertFalse(self.service.begin_scan("sources", "A")["ok"])

    def test_authenticated_deep_and_all_scans_reach_the_provider_scanner(self):
        self.service.set_key("fixture-api-key")
        calls = []
        result = {"records": {}, "itemRecords": {"live-one": {"verdict": "candidate", "latestVersion": "2.0"}},
                  "errors": [], "queried": 1, "mode": "deep"}
        def provider(cache, client):
            self.assertEqual(client.api_key, "fixture-api-key")
            def scan(snapshot, mode, progress):
                calls.append(mode)
                progress({"checked": 1, "total": 1})
                return copy.deepcopy(result)
            return SimpleNamespace(scan=scan)
        with patch("mo2_update_arsenal.bridge.UpdateScanner", side_effect=provider), \
             patch("mo2_update_arsenal.bridge.SourceScanner", return_value=SimpleNamespace(scan=lambda *args: {
                 "records": {}, "itemRecords": {}, "errors": [], "queried": 0, "mode": "sources"})):
            for mode in ("deep", "all"):
                index = len(self.threads)
                self.assertTrue(self.service.begin_scan(mode, "A")["ok"])
                self.threads[index].join(2)
                self.assertEqual(self.service.scan_state()["state"], "completed")
        self.assertEqual(calls, ["deep", "deep"])

    def test_native_nexus_deep_scan_reaches_actual_scanner_with_authenticated_callback(self):
        changed = copy.deepcopy(PROFILE)
        changed["items"][0]["nexusId"] = 7
        self.service.set_snapshot(changed)
        self.service.native_nexus_available = True
        calls = []
        def native(request):
            calls.append((request.cmd, request.payload.copy()))
            request.finish({"ok": True, "meta": {"version": "2.0", "status": "published"}})
        self.service.ui_call = native
        index = len(self.threads)
        self.assertTrue(self.service.begin_scan("deep", "A")["ok"])
        self.threads[index].join(2)
        self.assertEqual(self.service.scan_state()["state"], "completed")
        self.assertEqual(calls, [("nexus-metadata", {"game": "skyrimspecialedition", "modId": 7})])
        self.assertEqual(self.service.snapshot()["items"][0]["update"]["latestVersion"], "2.0")


if __name__ == "__main__":
    unittest.main()
