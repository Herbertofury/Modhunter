"""Owner-queue replay for already-reviewed content lease admission."""
from queue import Empty, Queue
from tempfile import TemporaryDirectory
from time import monotonic
from unittest import TestCase

from mo2_update_arsenal.bridge import UIRequest
from mo2_update_arsenal.refresh_work import RefreshWork
from test_content_recovery import Fixture
from test_plugin_autostart import plugin_type


class ContentLeaseRefreshSettlementTests(TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.f = Fixture(tmp.name)
        self.addCleanup(self.f.service.stop)
        self.events, self.timers = Queue(), []
        self.plugin = plugin_type(self.timers)()
        p, f = self.plugin, self.f
        p.org, p.server = f.org, f.service
        p._content_native = f.native
        f.native.plugin = p
        p._profile_backup = lambda: {"ok": True, "files": ["modlist.txt"]}
        p._refresh_snapshot = f.refresh_snapshot
        p._publish_diagnostics = lambda: None
        # Native next-refresh acknowledgement precedes the plugin-list event
        # inside the same owner turn, as OrganizerCore does.
        def finish_then_mark_dirty():
            f.refresh()
            p._on_plugins_refreshed()
        f.org.refresh = finish_then_mark_dirty
        f.service.ui_call = lambda req: self.events.put(lambda: p._dispatch(req))
        self.publications = []
        p._background_refresh = RefreshWork(
            context=lambda: (f.profile_name,), capture=f.snapshot,
            project=lambda captured, _force, _cancel: captured,
            publish=self.publish,
            deliver=lambda complete: self.events.put(lambda: p._on_background_refresh_completed(complete)),
        )
        self.addCleanup(p._background_refresh.close)

    def publish(self, snapshot, valid):
        self.assertTrue(valid())
        self.f.service.set_snapshot(snapshot)
        self.plugin._background_dirty = False
        self.publications.append(snapshot)
        return {"ok": True}

    def fire_debounce(self):
        timers = [fn for delay, fn in self.timers if delay == 150]
        self.timers[:] = [(delay, fn) for delay, fn in self.timers if delay != 150]
        for fn in timers:
            fn()

    def pump(self, predicate, *, fire_timers=False):
        deadline = monotonic() + 4
        while not predicate():
            if monotonic() > deadline:
                self.fail("owner queue did not reach condition")
            if fire_timers:
                self.fire_debounce()
            try:
                self.events.get(timeout=0.01)()
            except Empty:
                pass

    def terminal(self, job_id):
        return next((row for row in self.f.service.content.jobs()["jobs"]
                     if row["jobId"] == job_id and row["state"] in {"complete", "failed", "cancelled", "unknown"}), None)

    def capture_install(self):
        f = self.f
        saved = f.service.ui_call
        f.service.ui_call = self.plugin._dispatch  # capture is an owned read on this thread
        try:
            return f.payload()
        finally:
            f.service.ui_call = saved

    def capture_recovery(self):
        f = self.f
        saved = f.service.ui_call
        f.service.ui_call = self.plugin._dispatch
        try:
            captured = f.service.content.capture("live-1")
            self.assertTrue(captured["ok"], captured)
            return captured["contextId"]
        finally:
            f.service.ui_call = saved

    def wait_for_queued_lease(self):
        self.pump(lambda: bool(self.plugin._deferred_content_leases))
        request, = self.plugin._deferred_content_leases
        self.assertEqual(request.state, "queued")
        self.assertIsNone(self.f.service.content._lease)
        return request

    def test_install_and_duplicate_review_restore_neutral_refresh_only(self):
        f, p = self.f, self.plugin
        data = self.capture_install()
        p._on_plugins_refreshed()
        accepted = f.service.content.start_install(data)
        self.assertTrue(accepted["ok"], accepted)
        pending = self.wait_for_queued_lease()
        p._dispatch(pending)  # duplicate native queued event, not a second lease
        self.assertEqual(len(p._background_refresh._settle_waiters), 1)
        self.assertEqual(len(f.installs), 0)
        self.assertFalse(f.service._install_lock.acquire(blocking=False))
        self.assertEqual(f.service.content.start_install(data)["jobId"], accepted["jobId"])
        self.pump(pending.event.is_set, fire_timers=True)
        self.pump(lambda: self.terminal(accepted["jobId"]))
        installed = self.terminal(accepted["jobId"])
        self.assertTrue(installed["ok"], installed)
        self.assertEqual(len(f.installs), 1)
        self.assertEqual((f.mod/"payload.txt").read_text(), "after")
        # Do not artificially settle a scheduled native follow-up before
        # review/submission. This is the native restore race.
        self.assertTrue(p._background_dirty)
        rid = {"reviewId": "review-123456789-200", "contextId": self.capture_recovery(),
               "action": "restore", "backupId": installed["backupId"], "confirm": "RESTORE"}
        accepted = f.service.content.start_recovery(rid)
        self.assertTrue(accepted["ok"], accepted)
        pending = self.wait_for_queued_lease()
        repeated = f.service.content.start_recovery(rid)
        self.assertEqual(repeated["jobId"], accepted["jobId"])
        self.pump(pending.event.is_set, fire_timers=True)
        self.pump(lambda: self.terminal(accepted["jobId"]))
        restored = self.terminal(accepted["jobId"])
        self.assertTrue(restored["ok"], restored)
        self.assertEqual((f.mod/"payload.txt").read_text(), "before")
        self.assertEqual(f.service.content.start_recovery(rid)["jobId"], accepted["jobId"])
        self.assertEqual(sum(c["action"] == "restore" for c in f.service.content.runner.calls), 1)
        self.assertEqual(len(f.installs), 1)
        self.assertIsNone(f.service.content._lease)
        self.assertTrue(f.service._install_lock.acquire(blocking=False))
        f.service._install_lock.release()

    def test_profile_round_trip_after_waiting_never_executes_old_review(self):
        f, p = self.f, self.plugin
        data = self.capture_install()
        p._on_plugins_refreshed()
        accepted = f.service.content.start_install(data)
        request = self.wait_for_queued_lease()
        for profile in ("Other", "Current"):
            previous, f.profile_name = f.profile_name, profile
            p._on_profile_changed(previous, profile)
        self.assertEqual(request.state, "cancelled")
        self.pump(lambda: self.terminal(accepted["jobId"]))
        self.assertEqual(f.installs, [])
        self.assertEqual((f.mod/"payload.txt").read_text(), "before")

    def test_shutdown_closes_waiting_request_without_claiming(self):
        f, p = self.f, self.plugin
        data = self.capture_install()
        p._on_plugins_refreshed()
        accepted = f.service.content.start_install(data)
        request = self.wait_for_queued_lease()
        p._shutdown()
        self.assertTrue(request.event.is_set())
        self.assertFalse(request.result["ok"])
        self.assertEqual(f.installs, [])
        self.pump(lambda: self.terminal(accepted["jobId"]))

    def test_expired_cancelled_and_conflicting_lease_fail_before_execution(self):
        f, p = self.f, self.plugin
        ctx = f.native.capture({"itemId": "live-1"})
        p._on_plugins_refreshed()
        expired = UIRequest("content-lease", {"jobId": "expired", "context": ctx}, timeout=0)
        p._dispatch(expired)
        self.assertEqual(expired.state, "cancelled")
        cancelled = UIRequest("content-lease", {"jobId": "cancelled", "context": ctx})
        p._dispatch(cancelled)
        self.assertTrue(cancelled.cancel_if_pending())
        f.service.content._lease = "other-owned"
        rejected = UIRequest("content-lease", {"jobId": "new", "context": ctx})
        p._dispatch(rejected)
        # Existing lease checks and guarded refreshes coexist with dirty state.
        check = UIRequest("content-check", {"jobId": "other-owned", "context": ctx})
        p._dispatch(check)
        self.assertTrue(check.result["ok"], check.result)
        self.pump(rejected.event.is_set, fire_timers=True)
        self.assertIn("holds the mutation lease", rejected.result["error"])
        self.assertEqual(f.service.content._lease, "other-owned")
        self.assertEqual(cancelled.state, "cancelled")
        self.assertEqual(f.installs, [])

    def test_stale_native_context_revalidated_even_with_matching_snapshot_profile(self):
        f, p = self.f, self.plugin
        ctx = f.native.capture({"itemId": "live-1"})
        p._on_plugins_refreshed()
        request = UIRequest("content-lease", {"jobId": "reviewed", "context": ctx})
        p._dispatch(request)
        f.service.content.invalidate(reason="profile")
        self.pump(request.event.is_set, fire_timers=True)
        self.assertIn("execution lease changed", request.result["error"])
        self.assertIsNone(f.service.content._lease)

    def test_superseded_in_flight_snapshot_waits_for_new_debounce_without_extra_capture(self):
        f, p = self.f, self.plugin
        p._on_plugins_refreshed()
        self.fire_debounce()  # first capture queued, completion not delivered yet
        ctx = f.native.capture({"itemId": "live-1"})
        request = UIRequest("content-lease", {"jobId": "reviewed", "context": ctx})
        p._dispatch(request)
        p._on_plugins_refreshed()
        self.events.get(timeout=2)()  # stale capture completes before second debounce
        self.assertEqual(request.state, "queued")
        self.assertEqual(self.publications, [])
        self.pump(request.event.is_set, fire_timers=True)
        self.assertTrue(request.result["ok"], request.result)
        self.assertEqual(len(self.publications), 1)
        self.assertEqual(f.service.content._lease, "reviewed")

    def test_settle_waiters_are_bounded_and_expire_before_claim(self):
        f, p = self.f, self.plugin
        p._on_plugins_refreshed()
        ctx = f.native.capture({"itemId": "live-1"})
        requests = [UIRequest("content-lease", {"jobId": str(i), "context": ctx})
                    for i in range(p._background_refresh.MAX_WAITERS+1)]
        for request in requests:
            p._dispatch(request)
        self.assertIn("waiting for profile refresh", requests[-1].result["error"])
        self.assertEqual(len(p._deferred_content_leases), p._background_refresh.MAX_WAITERS)
        for request in requests[:-1]:
            request.deadline = monotonic() - 1
        self.pump(lambda: all(r.event.is_set() for r in requests), fire_timers=True)
        self.assertTrue(all(r.state == "cancelled" for r in requests[:-1]))
        self.assertFalse(p._deferred_content_leases)
        self.assertIsNone(f.service.content._lease)

    def test_sync_refresh_can_subsume_debounce_but_cannot_strand_waiter(self):
        f, p = self.f, self.plugin
        ctx = f.native.capture({"itemId": "live-1"})
        p._on_plugins_refreshed()
        request = UIRequest("content-lease", {"jobId": "reviewed", "context": ctx})
        p._dispatch(request)
        self.assertIsNone(p._pending_refresh)
        self.assertTrue(p._background_refresh.busy)
        # Reproduce the exact synchronous path's ordering: subsume timer,
        # invalidate any worker, publish, then clear dirty. The stale worker
        # can neither admit this lease nor strand it behind a dead timer.
        p._background_refresh.invalidate()
        f.refresh_snapshot()
        p._background_dirty = False
        self.pump(request.event.is_set)
        self.assertFalse(request.result["ok"])
        self.assertFalse(p._deferred_content_leases)
        self.assertIsNone(f.service.content._lease)
        self.assertEqual(self.publications, [])
