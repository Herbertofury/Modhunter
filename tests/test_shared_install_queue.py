"""Approved page/API work shares real content transactions and one native lease."""
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
import zipfile

from mo2_update_arsenal.content_recovery import ArchiveRunner, ContentRunner, canonical_json, file_identity
from mo2_update_arsenal.operation_journal import OperationJournal
from test_content_recovery import Fixture


class MixedFixture(Fixture):
    def __init__(self, directory):
        self.extra_versions = {}
        super().__init__(directory)
        self.targets = {"Example": self.target}
        for name in ("Second", "Third"):
            folder = self.mods / name
            folder.mkdir()
            (folder / "payload.txt").write_text("before " + name)
            (folder / "meta.ini").write_text("version=1")
            self.extra_versions[name] = "1.0"
            self.targets[name] = SimpleNamespace(name=lambda n=name: n,
                version=lambda n=name: self.extra_versions[n], absolutePath=lambda p=folder: str(p))
        self.mod_list.allMods = lambda: list(self.targets)
        self.mod_list.getMod = self.targets.get
        self.org.downloadManager = lambda: SimpleNamespace(downloadPath=lambda did: str(self.archive))
        self.org.downloadsPath = lambda: str(self.acquisitions)
        self.org.installMod = self.install
        self.entered = threading.Event()
        self.gate = threading.Event()
        self.gate.set()
        self.concurrent = 0
        self.max_concurrent = 0
        self.fail_names = set()
        self.service.set_snapshot(self.snapshot())

    def snapshot(self):
        value = super().snapshot()
        value["items"] += [{"id": "live-" + name, "mo2InternalName": name, "name": name,
            "installedVersion": version, "enabled": False, "nexusId": index + 2}
            for index, (name, version) in enumerate(self.extra_versions.items())]
        return value

    def install(self, archive, suggested):
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            self.installs.append((archive, suggested))
            if suggested == "Example":
                self.entered.set()
                if not self.gate.wait(5):
                    raise AssertionError("Owned native dialog fixture was not released")
            if suggested in self.fail_names:
                return None
            (self.mods / suggested / "payload.txt").write_text("after " + suggested)
            if suggested == "Example":
                self.version = "2.0"
            else:
                self.extra_versions[suggested] = "2.0"
            self.service.content.invalidate(reason="target", mod_name=suggested)
            return self.targets[suggested]
        finally:
            self.concurrent -= 1

    def ticket(self, did, name, *, armed=False):
        row = next(row for row in self.snapshot()["items"] if row["name"] == name)
        self.service._downloads[did] = {"profileId": "profile-1", "profileName": "Current", "game": "fixture",
            "gameRuntime": "1", "itemId": row["id"], "name": name, "modName": name, "provider": "nexus",
            "modId": row["nexusId"], "fileId": did, "fileName": "owned-fixture.zip", "reviewed": True,
            "installedVersion": row["installedVersion"], "status": "complete", "riskReasons": [],
            "autoInstallRequested": armed, "installContextEpoch": self.service._install_intent_epoch}

    def page(self, name, *, review_content=False):
        self.sequence += 1
        did = "download-queue-" + str(self.sequence)
        directory = self.acquisitions / did
        directory.mkdir()
        archive = directory / "archive.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("payload.txt", "after " + name)
        identity = file_identity(archive)
        item = "live-1" if name == "Example" else "live-" + name
        receipt = {"id": did, "state": "ready", "extension": "zip", "bytes": identity["bytes"], "sha256": identity["sha256"],
            "binding": {"contentIntent": "update", "association": {"profileId": "profile-1", "itemId": item}}}
        import json
        receipts = json.loads((self.acquisitions / "receipts.json").read_text())
        receipts["jobs"].append(receipt)
        (self.acquisitions / "receipts.json").write_bytes(canonical_json(receipts))
        captured = self.service.content.capture(item)
        if not captured.get("ok"):
            raise AssertionError(captured)
        review = {"archiveIdentity": {"bytes": identity["bytes"], "sha256": identity["sha256"]}}
        if review_content:
            stage = self.root / ("stage-" + str(self.sequence))
            inspected = ArchiveRunner(self.service.content.runner.executable).run({"action": "inspect", "archive": str(archive),
                "stage": str(stage), "expectedSha256": identity["sha256"], "expectedBytes": identity["bytes"], "selected": []})
            context = self.service.content._context(captured["contextId"])
            compared = self.service.content.runner.run(self.service.content._request(context, "compare", other=inspected["incomingRoot"]))
            review.update(stage=str(stage), incomingRoot=inspected["incomingRoot"], manifestDigest=inspected["manifestDigest"],
                          comparisonDigest=compared["digest"], requiresInstaller=inspected["requiresInstaller"])
        return {"reviewId": "review-3456789-" + str(self.sequence), "downloadId": did, "contextId": captured["contextId"],
            "confirm": "INSTALL", "resolvedArchive": {"archivePath": str(archive), "acquisitionRoot": str(self.acquisitions),
                "receipt": receipt, "review": review}}


class SharedQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.f = MixedFixture(self.temp.name)
        self.addCleanup(self.f.service.stop)
        self.addCleanup(self.f.gate.set)

    def control(self, action):
        snapshot = self.f.service.selection_snapshot([])
        result = self.f.service.install_queue_control({"action": action, "profileId": snapshot["profileId"],
                                                      "executionEpoch": snapshot["executionEpoch"]})
        self.assertTrue(result["ok"], result)
        return result

    def until(self, check, seconds=5):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if check():
                return
            time.sleep(0.005)
        self.fail("Shared queue did not reach the expected state: " + repr(self.f.service.install_queue()))

    def actual_cli(self):
        executable = os.environ.get("MODHUNTER_CONTENT_CLI")
        if not executable or not Path(executable).is_file():
            self.skipTest("Set MODHUNTER_CONTENT_CLI to exercise the actual content/archive helper")
        self.f.service.content.runner = ContentRunner(Path(executable))

    def test_mixed_api_page_api_waits_and_uses_same_real_content_engine(self):
        self.actual_cli()
        f = self.f
        page = f.page("Second", review_content=True)
        f.ticket(901, "Example")
        f.ticket(902, "Third")
        f.gate.clear()
        self.assertTrue(f.service.install_reviewed_download(901)["ok"])
        self.assertTrue(f.entered.wait(2))
        accepted = f.service.content.start_install(page)
        self.assertEqual(accepted["state"], "queued")
        self.assertTrue(f.service.install_reviewed_download(902)["ok"])
        self.assertEqual([entry["kind"] for entry in f.service.install_queue()["pending"]], ["page", "native"])
        self.assertEqual(len(f.installs), 1)
        f.gate.set()
        completed = f.wait(accepted["jobId"])
        self.assertTrue(completed["ok"], completed)
        self.assertTrue(completed["queueRevalidatedAfter"])
        self.until(lambda: f.service.downloads()[902]["status"] == "installed")
        self.assertEqual([name for _, name in f.installs], ["Example", "Second", "Third"])
        self.assertEqual(f.max_concurrent, 1)
        jobs = f.service.content.jobs()["jobs"]
        self.assertEqual(len(jobs), 3)
        self.assertTrue(all(job["backupVerified"] and job["backup"]["undoAvailable"] for job in jobs))
        replay = f.service.content.start_install(page)
        self.assertTrue(replay["alreadyAccepted"])
        self.assertEqual(len(f.installs), 3)

    def test_stop_after_current_preserves_approved_page_until_explicit_resume(self):
        self.actual_cli()
        f = self.f
        page = f.page("Second", review_content=True)
        f.ticket(901, "Example")
        f.gate.clear()
        f.service.install_reviewed_download(901)
        self.assertTrue(f.entered.wait(2))
        accepted = f.service.content.start_install(page)
        stopped = self.control("stopAfterCurrent")
        self.assertEqual(stopped["mode"], "paused")
        f.gate.set()
        self.until(lambda: f.service.downloads()[901]["status"] == "installed" and not f.service.install_queue()["active"])
        self.assertEqual(len(f.installs), 1)
        self.assertEqual(f.service.content._jobs[accepted["jobId"]]["state"], "queued")
        self.assertTrue(Path(page["resolvedArchive"]["archivePath"]).exists())
        self.control("resume")
        self.assertTrue(f.wait(accepted["jobId"])["ok"])
        self.assertEqual([name for _, name in f.installs], ["Example", "Second"])

    def test_cancel_preparation_disarms_both_queues_and_pending_native_callback(self):
        f = self.f
        f.defer_install_dispatch = True
        first, second = f.page("Example"), f.page("Second")
        active = f.service.content.start_install(first)
        self.assertTrue(f.install_dispatched.wait(2))
        pending = f.service.content.start_install(second)
        f.ticket(901, "Third")
        f.service.install_reviewed_download(901)
        stopped = self.control("cancelPreparation")
        self.assertEqual(stopped["pending"], [])
        self.assertEqual(f.wait(active["jobId"])["state"], "cancelled")
        cancelled = f.wait(pending["jobId"])
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertTrue(cancelled["stageReleaseAllowed"])
        for request in f.pending:
            f.deliver(request)
        self.control("resume")
        self.assertFalse(f.installs)
        self.assertFalse(f.service.downloads()[901]["autoInstallRequested"])
        self.assertTrue(f.service.downloads()[901]["installDisarmed"])
        self.assertTrue(f.service.content.start_install(second)["alreadyAccepted"])
        self.assertFalse(f.installs)

    def test_cancel_preparation_does_not_interrupt_already_running_native_installer(self):
        f = self.f
        f.ticket(901, "Example")
        f.gate.clear()
        f.service.install_reviewed_download(901)
        self.assertTrue(f.entered.wait(2))
        result = self.control("cancelPreparation")
        self.assertTrue(result["active"]["nativeStarted"])
        self.assertTrue(f.service._install_lock.locked())
        self.assertFalse(f.service.content.about_to_run("FixtureGame.exe"))
        f.gate.set()
        self.until(lambda: f.service.downloads()[901]["status"] == "installed")
        job = f.service.content.jobs()["jobs"][0]
        self.assertEqual(job["state"], "complete")
        self.assertTrue(job["backup"]["undoAvailable"])
        self.assertEqual(f.service.install_queue()["mode"], "paused")

    def test_profile_round_trip_blocks_old_entry_but_not_fresh_eligible_entry(self):
        f = self.f
        self.control("stopAfterCurrent")
        stale = f.page("Example")
        first = f.service.content.start_install(stale)
        f.service.content.invalidate(reason="profile")
        f.service.content.invalidate(reason="profile")
        fresh = f.page("Second")
        second = f.service.content.start_install(fresh)
        self.control("resume")
        blocked = f.wait(first["jobId"])
        self.assertFalse(blocked["ok"])
        self.assertTrue(blocked["needsReview"])
        self.assertTrue(f.wait(second["jobId"])["ok"])
        self.assertEqual([name for _, name in f.installs], ["Second"])
        self.assertTrue(f.service.content.start_install(stale)["alreadyAccepted"])

    def test_recovery_lease_makes_page_wait_instead_of_failing_busy(self):
        f = self.f
        self.assertTrue(f.service._install_lock.acquire(blocking=False))
        try:
            accepted = f.service.content.start_install(f.page("Example"))
            self.assertTrue(accepted["ok"])
            self.assertEqual(accepted["state"], "queued")
            self.assertFalse(f.service.content.runner.calls)
        finally:
            f.service._install_lock.release()
        f.service._drain_install_queue()
        self.assertTrue(f.wait(accepted["jobId"])["ok"])

    def test_stale_control_does_not_pause_or_cancel_fresh_work(self):
        snapshot = self.f.service.selection_snapshot([])
        result = self.f.service.install_queue_control({"action": "cancelPreparation", "profileId": snapshot["profileId"],
                                                       "executionEpoch": snapshot["executionEpoch"] - 1})
        self.assertFalse(result["ok"])
        self.assertEqual(self.f.service.install_queue()["mode"], "running")

    def test_ambiguous_native_result_is_not_replayed_and_later_api_target_can_progress(self):
        f = self.f
        self.control("stopAfterCurrent")
        f.fail_names.add("Example")
        intent = f.page("Example")
        first = f.service.content.start_install(intent)
        f.ticket(902, "Second")
        f.service.install_reviewed_download(902)
        self.control("resume")
        uncertain = f.wait(first["jobId"])
        self.assertEqual(uncertain["state"], "unknown")
        self.assertTrue(uncertain["backupRetained"])
        self.until(lambda: f.service.downloads()[902]["status"] == "installed")
        self.assertTrue(f.service.content.start_install(intent)["alreadyAccepted"])
        self.assertEqual([name for _, name in f.installs], ["Example", "Second"])
        self.assertEqual(f.max_concurrent, 1)

    def test_late_download_callback_cannot_rearm_uncertain_native_install(self):
        f = self.f
        f.fail_names.add("Example")
        f.ticket(901, "Example")
        self.assertTrue(f.service.install_reviewed_download(901)["ok"])
        self.until(lambda: f.service.downloads()[901]["status"] == "needs-review")
        ticket = f.service.downloads()[901]
        self.assertEqual(f.wait(ticket["contentJobId"])["state"], "unknown")
        f.service.set_download_status(901, "complete")
        self.assertEqual(f.service.downloads()[901]["status"], "needs-review")
        f.service.install_ready_queue()
        self.assertFalse(f.service.install_queue()["pending"])
        self.assertEqual([name for _, name in f.installs], ["Example"])
        self.assertEqual(f.service.downloads()[901]["contentJobId"], ticket["contentJobId"])
        current = f.service.journal.profile("profile-1")["records"][0]
        self.assertEqual(current["status"], "needs-review")
        self.assertTrue(current["needsReview"])
        reopened = OperationJournal(f.service.journal.path, session_id="fresh-session")
        recovered = reopened.profile("profile-1")["records"][0]
        self.assertEqual(recovered["status"], "needs-review")
        self.assertTrue(recovered["needsReview"])
        self.assertFalse(recovered["actionable"])

    def test_own_completion_cannot_renew_a_changed_reviewed_target(self):
        self.actual_cli()
        f = self.f
        page = f.page("Second", review_content=True)
        f.ticket(901, "Example")
        f.gate.clear()
        f.service.install_reviewed_download(901)
        self.assertTrue(f.entered.wait(2))
        second = f.service.content.start_install(page)
        # Byte-only outside edit: no generation callback may be assumed.
        (f.mods / "Second" / "payload.txt").write_text("later user bytes")
        f.gate.set()
        refused = f.wait(second["jobId"])
        self.assertFalse(refused["ok"])
        self.assertTrue(refused["needsReview"])
        self.assertIn("changed", refused["error"])
        self.assertEqual([name for _, name in f.installs], ["Example"])
        self.assertEqual((f.mods / "Second" / "payload.txt").read_text(), "later user bytes")

    def test_external_event_during_owned_install_cannot_be_hidden_by_same_final_profile(self):
        self.actual_cli()
        f = self.f
        page = f.page("Second", review_content=True)
        f.ticket(901, "Example")
        f.gate.clear()
        f.service.install_reviewed_download(901)
        self.assertTrue(f.entered.wait(2))
        second = f.service.content.start_install(page)
        f.service.content.invalidate(reason="profile")
        f.service.content.invalidate(reason="profile")
        f.gate.set()
        refused = f.wait(second["jobId"])
        self.assertFalse(refused["ok"])
        self.assertTrue(refused["needsReview"])
        self.assertEqual([name for _, name in f.installs], ["Example"])

    def test_shutdown_releases_only_unused_queued_stages_without_replay(self):
        self.control("stopAfterCurrent")
        payload = self.f.page("Example")
        accepted = self.f.service.content.start_install(payload)
        self.f.service.stop()
        job = self.f.wait(accepted["jobId"])
        self.assertEqual(job["state"], "cancelled")
        self.assertTrue(job["stageReleaseAllowed"])
        self.assertTrue(Path(payload["resolvedArchive"]["archivePath"]).exists())
        self.assertFalse(self.f.installs)

    def test_cancel_between_durable_acceptance_and_fifo_admission_cannot_rearm_job(self):
        from unittest.mock import patch
        f = self.f
        entered, released = threading.Event(), threading.Event()
        queued = f.service.queue_content_install
        accepted = []
        def delayed(*args):
            entered.set()
            if not released.wait(3):
                raise AssertionError("Queue admission fixture was not released")
            return queued(*args)
        payload = f.page("Example")
        with patch.object(f.service, "queue_content_install", side_effect=delayed):
            worker = threading.Thread(target=lambda: accepted.append(f.service.content.start_install(payload)))
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                self.control("cancelPreparation")
            finally:
                released.set()
                worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertTrue(accepted[0]["ok"])
        job = f.wait(accepted[0]["jobId"])
        self.assertEqual(job["state"], "cancelled")
        self.assertTrue(job["stageReleaseAllowed"])
        self.control("resume")
        self.assertFalse(f.installs)
        self.assertEqual(f.service.install_queue()["pending"], [])


if __name__ == "__main__":
    unittest.main()
