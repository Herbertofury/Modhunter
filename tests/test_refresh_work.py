from queue import Queue
from threading import Event, get_ident
import unittest
from mo2_update_arsenal.refresh_work import RefreshWork


class RefreshWorkTests(unittest.TestCase):
    def setUp(self):
        self.actor = get_ident()
        self.profile = ("A",)
        self.go = Event()
        self.projected = Queue()
        self.delivery = Queue()
        self.published, self.results = [], []
        self.capture_count = 0
        self.work = RefreshWork(context=lambda: self.profile, capture=self.capture,
                                project=self.project, publish=self.publish, deliver=self.delivery.put)
        self.addCleanup(self.work.close)
        self.addCleanup(self.go.set)

    def capture(self):
        self.assertEqual(get_ident(), self.actor)
        self.capture_count += 1
        return self.profile

    def project(self, captured, forced, cancel):
        self.assertNotEqual(get_ident(), self.actor)
        self.projected.put((captured, forced, cancel))
        self.go.wait(2)
        return captured

    def publish(self, captured, valid):
        self.assertEqual(get_ident(), self.actor)
        if not valid():
            return {"ok": False, "error": "stale"}
        self.published.append(captured)
        return {"ok": True, "catalog": captured}

    def complete(self):
        self.work.complete(self.delivery.get(timeout=2))

    def test_ordinary_then_forced_followup_is_queued_and_not_lost(self):
        self.work.enqueue()
        self.assertEqual(self.projected.get(timeout=2)[:2], (("A",), False))
        self.work.enqueue(force=True, done=self.results.append)
        self.work.enqueue()  # Simultaneous event merges without dropping forced mode.
        self.assertEqual(self.capture_count, 1)
        self.go.set()
        self.complete()
        self.assertEqual(self.projected.get(timeout=2)[:2], (("A",), True))
        self.complete()
        self.assertEqual(self.published, [("A",), ("A",)])
        self.assertEqual(self.results, [{"ok": True, "catalog": ("A",)}])

    def test_profile_aba_rejects_late_old_result(self):
        self.work.enqueue(force=True, done=self.results.append)
        _, _, old_cancel = self.projected.get(timeout=2)
        for profile in "BA":
            self.profile = (profile,)
            self.work.invalidate()
            self.work.enqueue()
        self.assertTrue(old_cancel.is_set())
        self.go.set()
        self.complete()
        self.assertFalse(self.results[0]["ok"])
        self.complete()
        self.assertEqual(self.published, [("A",)])
        self.assertEqual(self.capture_count, 2)

    def test_close_finishes_waiters_once_and_late_worker_cannot_publish(self):
        self.work.enqueue(force=True, done=self.results.append)
        _, _, cancel = self.projected.get(timeout=2)
        self.work.enqueue(force=True, done=self.results.append)
        self.work.close()
        self.assertTrue(cancel.is_set())
        self.assertEqual(self.results, [{"ok": False, "error": "MO2 is closing"}] * 2)
        self.go.set()
        self.complete()
        self.assertEqual(len(self.results), 2)
        self.assertEqual(self.published, [])

    def test_pending_waiters_bounded(self):
        self.work.enqueue()
        self.projected.get(timeout=2)
        for _ in range(self.work.MAX_WAITERS + 1):
            self.work.enqueue(force=True, done=self.results.append)
        self.assertEqual(self.results, [{"ok": False, "error": "Another profile refresh is pending"}])
        self.go.set()
        self.complete()
        self.complete()
        self.assertEqual(len(self.results), self.work.MAX_WAITERS + 1)
        self.assertEqual(self.capture_count, 2)

    def test_identity_rechecked_after_owned_native_collection(self):
        def collection(captured, valid):
            self.work.invalidate() # synchronous organizer callback during completed attach
            return self.publish(captured, valid)
        self.work._publish = collection
        self.work.enqueue(force=True, done=self.results.append)
        self.go.set()
        self.complete()
        self.assertFalse(self.results[0]["ok"])
        self.assertEqual(self.published, [])


class RefreshMetadataCacheTests(unittest.TestCase):
    def test_forced_late_replacement_retries_and_does_not_return_intermediary(self):
        from pathlib import Path
        from unittest.mock import patch
        from mo2_update_arsenal import model
        from mo2_update_arsenal.refresh_work import RefreshMetadataCache, UnstableSnapshotMetadata
        path = Path("/synthetic/mod")
        cache = RefreshMetadataCache()
        with (patch.object(model.LocalMetadataCache, "_signature", side_effect=[(1,), (2,), (2,), (2,)]),
              patch.object(model, "inspect_local_flags", return_value=[]),
              patch.object(model, "discover_local_source", side_effect=[{"url": "old"}, {"url": "new"}]) as source):
            self.assertEqual(cache.inspect(path, "", force=True), ([], {"url": "new"}))
            self.assertEqual(source.call_count, 2)
        with (patch.object(model.LocalMetadataCache, "_signature", side_effect=[(1,), (2,), (3,), (4,)]),
              patch.object(model, "inspect_local_flags", return_value=[]),
              patch.object(model, "discover_local_source", return_value={"url": "racing"})):
            with self.assertRaises(UnstableSnapshotMetadata):
                cache.inspect(path, "", force=True)
        self.assertEqual(cache._entries, {})
