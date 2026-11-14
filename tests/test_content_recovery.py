"""Content transactions use isolated fixtures, never an installed user's mods."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from mo2_update_arsenal.bridge import ArsenalService
from mo2_update_arsenal.content_recovery import (ContentRecovery, ContentRunner, NativeContent, TERMINAL,
    canonical_json, file_identity, real_path, valid_target_name, verify_acquisition, verify_selected_archive)
from mo2_update_arsenal.duplicate_report import DuplicateRunner


class FixtureRunner:
    """Actual fixture-byte copies, while focused tests inspect transaction order."""
    available = True
    executable = Path("/unused-fixture-helper")
    def __init__(self):
        self.records = {}
        self.calls = []
        self.before_verify = None

    def run(self, request, *, cancel_event=None):
        self.calls.append(deepcopy(request))
        root, vault = Path(request["root"]), Path(request["vault"])
        action = request["action"]
        if action == "snapshot":
            ident = "backup-" + str(len(self.records) + 1)
            destination = vault / ident / "files"
            destination.parent.mkdir(parents=True)
            shutil.copytree(root, destination)
            self.records[ident] = {"id": ident, "state": "prepared", "root": root, "before": self.tree(root), "copy": destination}
            return {"ok": True, "backup": {"id": ident, "state": "prepared", "includesModFiles": True}}
        record = self.records.get(request.get("id"))
        if action == "verifyPrepared":
            if self.before_verify:
                self.before_verify()
            if self.tree(root) != record["before"] or self.tree(record["copy"]) != record["before"]:
                raise RuntimeError("Target or prepared backup changed before installer")
            return {"ok": True}
        if action == "seal":
            record.update(state="sealed", after=self.tree(root))
            return {"ok": True, "backup": {"id": record["id"], "state": "sealed", "undoAvailable": True}}
        if action == "previewRestore":
            if record["state"] != "sealed" or self.tree(root) != record["after"]:
                raise ValueError("Target changed after the retained backup was sealed")
            return {"ok": True, "canRestore": True}
        if action == "restore":
            if self.tree(root) != record["after"]:
                raise ValueError("Target changed before restore")
            shutil.rmtree(root)
            shutil.copytree(record["copy"], root)
            record["state"] = "restored"
            return {"ok": True, "backup": {"id": record["id"], "state": "restored"}}
        if action == "list":
            return {"ok": True, "backups": [{"id": r["id"], "state": r["state"]} for r in self.records.values() if r["root"] == root]}
        raise AssertionError("Unexpected fixture action " + action)

    @staticmethod
    def tree(root):
        return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


class Fixture:
    def __init__(self, directory):
        self.root = Path(directory)
        self.mods = self.root / "mods"
        self.mod = self.mods / "Example"
        self.mod.mkdir(parents=True)
        (self.mod / "payload.txt").write_text("before")
        (self.mod / "meta.ini").write_text("version=1")
        self.profile = self.root / "profiles" / "Current"
        self.profile.mkdir(parents=True)
        (self.profile / "modlist.txt").write_text("-Example\n")
        self.data = self.root / "plugin-data"
        self.data.mkdir()
        self.version = "1.0"
        self.installs = []
        self.callbacks = []
        self.refresh_requested = threading.Event()
        self.defer_refresh = False
        self.fail_refresh = False
        self.install_hook = None
        self.profile_name = "Current"
        self.target = SimpleNamespace(name=lambda: "Example", version=lambda: self.version, absolutePath=lambda: str(self.mod))
        self.mod_list = SimpleNamespace(allMods=lambda: ["Example"], getMod=lambda name: self.target if name == "Example" else None, state=lambda name: 0)
        self.org = SimpleNamespace(profileName=lambda: self.profile_name, profilePath=lambda: str(self.profile),
            pluginDataPath=lambda: str(self.data), modsPath=lambda: str(self.mods), modList=lambda: self.mod_list,
            managedGame=lambda: SimpleNamespace(binaryName=lambda: "FixtureGame.exe", executables=lambda: [], gameNexusName=lambda: self.snapshot()["game"], gameVersion=lambda: "1"),
            installMod=self.install, onNextRefresh=self.on_refresh, refresh=self.refresh)
        self.service = ArsenalService("<html><!--__MO2_BOOT__--></html>", self.data / "cache.json", self.dispatch, self.snapshot())
        self.service.content.runner = FixtureRunner()
        self.service.content.native_enabled = True
        self.service.content.launch_guard_available = True
        self.plugin = SimpleNamespace(org=self.org, server=self.service, _profile_backup=lambda: {"ok": True, "files": ["modlist.txt"]}, _refresh_snapshot=self.refresh_snapshot)
        self.native = NativeContent(self.plugin)
        self.pending = []
        self.defer_install_dispatch = False
        self.install_dispatched = threading.Event()
        self.sequence = 0
        self.acquisitions = self.root / "watchtower-acquisitions"
        self.archive_dir = self.acquisitions / "download-fixture"
        self.archive_dir.mkdir(parents=True)
        self.archive = self.archive_dir / "archive.zip"
        with zipfile.ZipFile(self.archive, "w") as z:
            z.writestr("payload.txt", "after")
        self.identity = file_identity(self.archive)
        self.receipt = {"id": "download-fixture", "state": "ready", "extension": "zip", "bytes": self.identity["bytes"],
            "sha256": self.identity["sha256"], "binding": {"contentIntent": "update", "association": {"profileId": "profile-1", "itemId": "live-1"}}}
        (self.acquisitions / "receipts.json").write_bytes(canonical_json({"schema": 1, "jobs": [self.receipt]}))

    def snapshot(self):
        return {"profileId": "profile-1", "profileName": self.profile_name, "game": "fixture", "gameRuntime": "1",
            "items": [{"id": "live-1", "mo2InternalName": "Example", "name": "Example", "installedVersion": self.version, "enabled": False, "nexusId": 1}]}

    def dispatch(self, request):
        if self.defer_install_dispatch and request.cmd == "content-install":
            self.pending.append(request)
            self.install_dispatched.set()
            return
        self.deliver(request)

    def deliver(self, request):
        if not request.begin():
            return
        try:
            self.native.handle(request, SimpleNamespace(singleShot=lambda milliseconds, callback: None))
        except Exception as exc:
            request.finish({"ok": False, "error": str(exc)})

    def on_refresh(self, callback, immediate):
        if immediate:
            raise AssertionError("Content refresh must wait for an actual native refresh")
        self.callbacks.append(callback)
        return True

    def refresh(self):
        self.refresh_requested.set()
        if self.fail_refresh:
            raise RuntimeError("fixture refresh failed")
        if not self.defer_refresh:
            self.finish_refresh()

    def finish_refresh(self):
        callbacks, self.callbacks = self.callbacks, []
        for callback in callbacks:
            callback()

    def refresh_snapshot(self, **kwargs):
        self.service.set_snapshot(self.snapshot())
        return {"ok": True}

    def install(self, archive, suggested):
        self.installs.append((archive, suggested))
        if self.install_hook:
            return self.install_hook()
        (self.mod / "payload.txt").write_text("after")
        self.version = "2.0"
        return self.target

    def payload(self, *, capture=True):
        context = self.service.content.capture("live-1") if capture else {}
        if capture and not context.get("ok"):
            raise AssertionError(context)
        self.sequence += 1
        return {"reviewId": f"review-123456789-{self.sequence}", "downloadId": "download-fixture", "contextId": context.get("contextId", ""), "confirm": "INSTALL",
                "resolvedArchive": {"archivePath": str(self.archive), "acquisitionRoot": str(self.acquisitions), "receipt": deepcopy(self.receipt)},
                "review": {"archiveIdentity": {"bytes": self.identity["bytes"], "sha256": self.identity["sha256"]}}}

    def wait(self, job_id, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            jobs = self.service.content.jobs()["jobs"]
            job = next(j for j in jobs if j["jobId"] == job_id)
            if job["state"] in TERMINAL:
                return job
            time.sleep(0.005)
        raise AssertionError("Content job did not finish: " + repr(jobs))


class ContentRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = Fixture(self.temp.name)
        self.addCleanup(self.fixture.service.stop)

    def install(self):
        data = self.fixture.payload()
        accepted = self.fixture.service.content.start_install(data)
        self.assertTrue(accepted["ok"], accepted)
        return data, self.fixture.wait(accepted["jobId"])

    def test_actual_bytes_backed_up_before_native_install_and_refresh_ack(self):
        f = self.fixture
        f.defer_refresh = True
        data = f.payload()
        accepted = f.service.content.start_install(data)
        self.assertTrue(f.refresh_requested.wait(3))
        job = f.service.content.jobs()["jobs"][0]
        self.assertEqual(job["state"], "refreshing")
        self.assertFalse(job["stageReleaseAllowed"])
        record = f.service.content.runner.records[job["backupId"]]
        self.assertEqual((record["copy"] / "payload.txt").read_text(), "before")
        self.assertEqual((f.mod / "payload.txt").read_text(), "after")
        self.assertFalse(f.service.content.about_to_run("FixtureGame.exe"))
        f.finish_refresh()
        completed = f.wait(accepted["jobId"])
        self.assertTrue(completed["ok"], completed)
        self.assertEqual(completed["state"], "complete")
        self.assertTrue(completed["backup"]["undoAvailable"])
        self.assertTrue(completed["verifiedUpdate"])
        self.assertTrue(completed["stageReleaseAllowed"])
        self.assertIsNone(f.service.content._lease)
        self.assertTrue(f.service._install_lock.acquire(blocking=False))
        f.service._install_lock.release()
        self.assertEqual([c["action"] for c in f.service.content.runner.calls], ["snapshot", "verifyPrepared", "seal"])

    def test_review_retry_returns_same_job_and_cannot_change_intent(self):
        data, completed = self.install()
        retried = self.fixture.service.content.start_install(data)
        self.assertTrue(retried["ok"], retried)
        self.assertTrue(retried["alreadyAccepted"])
        self.assertEqual(len(self.fixture.installs), 1)
        changed = deepcopy(data)
        changed["resolvedArchive"]["receipt"]["bytes"] += 1
        refused = self.fixture.service.content.start_install(changed)
        self.assertFalse(refused["ok"], refused)
        self.assertEqual(len(self.fixture.installs), 1)

    def test_changed_original_archive_never_opens_installer_or_claims_backup(self):
        f = self.fixture
        data = f.payload()
        f.archive.write_bytes(b"changed")
        accepted = f.service.content.start_install(data)
        job = f.wait(accepted["jobId"])
        self.assertEqual(job["state"], "failed")
        self.assertFalse(f.installs)
        self.assertFalse(f.service.content.runner.records)

    def test_native_target_modified_after_snapshot_stops_before_installer(self):
        f = self.fixture
        f.service.content.runner.before_verify = lambda: (f.mod / "payload.txt").write_text("external edit")
        _, job = self.install()
        self.assertEqual(job["state"], "failed")
        self.assertTrue(job["backupRetained"])
        self.assertFalse(f.installs)
        self.assertEqual(next(iter(f.service.content.runner.records.values()))["state"], "prepared")

    def test_structured_verification_refusal_never_opens_native_installer(self):
        f = self.fixture
        original = f.service.content.runner.run
        with patch.object(f.service.content.runner, "run", side_effect=lambda request, **kwargs:
                {"ok": False, "error": "backup verification refused"} if request["action"] == "verifyPrepared" else original(request, **kwargs)):
            _, job = self.install()
        self.assertEqual(job["state"], "failed", job)
        self.assertFalse(f.installs)
        self.assertTrue(job["backupRetained"])
        self.assertIn("verification refused", job["error"])

    def test_structured_seal_refusal_keeps_installed_files_and_unknown_recovery(self):
        f = self.fixture
        original = f.service.content.runner.run
        with patch.object(f.service.content.runner, "run", side_effect=lambda request, **kwargs:
                {"ok": False, "error": "undo seal refused"} if request["action"] == "seal" else original(request, **kwargs)):
            _, job = self.install()
        self.assertEqual(job["state"], "unknown", job)
        self.assertEqual((f.mod / "payload.txt").read_text(), "after")
        self.assertTrue(job["filesInstalled"])
        self.assertTrue(job["refreshRequired"])
        self.assertFalse(job["backup"].get("undoAvailable", False))
        self.assertEqual(next(iter(f.service.content.runner.records.values()))["state"], "prepared")

    def test_native_review_lease_exposes_profile_round_trip_before_debounced_snapshot(self):
        f = self.fixture
        before = f.service.content.capture("live-1")
        unchanged = f.service.content.capture("live-1")
        self.assertTrue(before["ok"] and unchanged["ok"])
        self.assertEqual(before["nativeLease"], unchanged["nativeLease"])
        self.assertEqual(before["nativeGeneration"], unchanged["nativeGeneration"])
        self.assertNotEqual(before["contextId"], unchanged["contextId"])
        f.profile_name = "Other"
        f.service.content.invalidate()
        f.profile_name = "Current"
        f.service.content.invalidate()
        after = f.service.content.capture("live-1")
        self.assertTrue(after["ok"], after)
        self.assertEqual(before["profileId"], after["profileId"])
        self.assertEqual(before["executionEpoch"], after["executionEpoch"])
        self.assertEqual(before["nativeLease"], after["nativeLease"])
        self.assertEqual(after["nativeGeneration"], before["nativeGeneration"] + 2)
        with self.assertRaisesRegex(ValueError, "Native MO2 state changed"):
            f.service.content._context(before["contextId"])

    def test_native_change_after_qt_capture_cannot_renew_the_captured_generation(self):
        f = self.fixture
        original = f.service._submit_ui
        def changed_after_capture(request):
            original(request)
            if request.cmd == "content-capture":
                f.service.content.invalidate()
        with patch.object(f.service, "_submit_ui", side_effect=changed_after_capture):
            rejected = f.service.content.capture("live-1")
        self.assertFalse(rejected["ok"], rejected)
        self.assertIn("changed during context capture", rejected["error"])

    def test_service_restart_changes_opaque_native_review_lease(self):
        f = self.fixture
        before = f.service.content.capture("live-1")
        f.service.content = ContentRecovery(f.service, f.service.content.ledger)
        after = f.service.content.capture("live-1")
        self.assertTrue(before["ok"] and after["ok"])
        self.assertEqual(before["nativeGeneration"], after["nativeGeneration"])
        self.assertNotEqual(before["nativeLease"], after["nativeLease"])

    def test_cancelled_native_installer_is_unknown_and_backup_stays_prepared(self):
        self.fixture.install_hook = lambda: None
        _, job = self.install()
        self.assertEqual(job["state"], "unknown")
        self.assertTrue(job["backupRetained"])
        self.assertTrue(job["stageReleaseAllowed"])
        self.assertTrue(job["backupVerified"])
        self.assertNotIn("seal", [c["action"] for c in self.fixture.service.content.runner.calls])

    def test_interrupted_stage_requires_positive_owner_exit_and_independent_inputs(self):
        f = self.fixture
        data = f.payload()
        job, _ = f.service.content._new_job(data, "install")
        for stopped, started, verified, expected in ((False, False, False, False), (True, False, False, True),
                                                     (True, True, False, False), (True, True, True, True)):
            with self.subTest(stopped=stopped, started=started, verified=verified):
                job.update(state="installing" if started else "queued", nativeStarted=started,
                    backupVerified=verified, includesModFiles=verified, backupId="backup-fixture" if verified else "",
                    stageReleaseAllowed=False)
                f.service.content._persist()
                with patch("mo2_update_arsenal.content_recovery.previous_owner_stopped", return_value=stopped):
                    recovered = ContentRecovery(f.service, f.service.content.ledger)
                observed = recovered.jobs()["jobs"][0]
                self.assertEqual(observed["state"], "interrupted")
                self.assertEqual(observed["stageReleaseAllowed"], expected)
                if expected:
                    stored = json.loads(f.service.content.ledger.read_bytes())["jobs"][0]
                    self.assertTrue(stored["stageReleaseAllowed"])
        self.assertFalse(f.installs)

    def test_native_installer_other_target_never_seals_automatic_undo(self):
        f = self.fixture
        other = f.mods / "Another Mod"
        other.mkdir()
        f.install_hook = lambda: SimpleNamespace(name=lambda: "Another Mod", absolutePath=lambda: str(other))
        _, job = self.install()
        self.assertEqual(job["state"], "unknown")
        self.assertEqual(job["installedName"], "Another Mod")
        self.assertNotIn("seal", [c["action"] for c in f.service.content.runner.calls])

    def test_pending_qt_installer_cancelled_never_executes_late(self):
        f = self.fixture
        f.defer_install_dispatch = True
        data = f.payload()
        accepted = f.service.content.start_install(data)
        self.assertTrue(f.install_dispatched.wait(3))
        cancel = f.service.content.cancel({"jobId": accepted["jobId"], "reviewId": data["reviewId"], "downloadId": data["downloadId"]})
        self.assertTrue(cancel["ok"], cancel)
        for request in f.pending:
            f.deliver(request)
        result = f.wait(accepted["jobId"])
        self.assertEqual(result["state"], "cancelled")
        self.assertFalse(f.installs)

    def test_profile_native_change_blocks_even_before_snapshot_refresh(self):
        f = self.fixture
        data = f.payload()
        f.profile_name = "Different"
        accepted = f.service.content.start_install(data)
        job = f.wait(accepted["jobId"])
        self.assertEqual(job["state"], "failed")
        self.assertFalse(f.installs)

    def test_running_game_blocks_before_backup(self):
        data = self.fixture.payload()
        with patch("mo2_update_arsenal.content_recovery.running_game_processes", return_value=[{"pid": 42, "name": "FixtureGame.exe"}]):
            accepted = self.fixture.service.content.start_install(data)
            job = self.fixture.wait(accepted["jobId"])
        self.assertEqual(job["state"], "failed")
        self.assertIn("running game", job["error"])
        self.assertFalse(self.fixture.service.content.runner.records)

    def test_restore_bytes_then_refresh_failure_reports_actual_partial_outcome(self):
        _, installed = self.install()
        f = self.fixture
        captured = f.service.content.capture("live-1")
        f.fail_refresh = True
        accepted = f.service.content.start_recovery({"reviewId": "review-123456789-22", "contextId": captured["contextId"],
            "action": "restore", "backupId": installed["backupId"], "confirm": "RESTORE"})
        job = f.wait(accepted["jobId"])
        self.assertFalse(job["ok"])
        self.assertTrue(job["filesRestored"])
        self.assertTrue(job["refreshRequired"])
        self.assertEqual((f.mod / "payload.txt").read_text(), "before")

    def test_later_changes_block_undo_without_erasing_user_bytes(self):
        _, installed = self.install()
        f = self.fixture
        (f.mod / "payload.txt").write_text("later edit")
        captured = f.service.content.capture("live-1")
        accepted = f.service.content.start_recovery({"reviewId": "review-123456789-23", "contextId": captured["contextId"],
            "action": "restore", "backupId": installed["backupId"], "confirm": "RESTORE"})
        job = f.wait(accepted["jobId"])
        self.assertFalse(job["ok"])
        self.assertEqual((f.mod / "payload.txt").read_text(), "later edit")

    def test_restart_does_not_replay_lost_ack_job(self):
        data, _ = self.install()
        old = self.fixture.service.content
        old._jobs[data["reviewId"]]["state"] = "installing"
        old._persist()
        recovered = ContentRecovery(self.fixture.service, old.ledger, runner=FixtureRunner())
        self.assertEqual(recovered.jobs()["jobs"][0]["state"], "interrupted")
        result = recovered.start_install(data)
        self.assertTrue(result["alreadyAccepted"])
        self.assertEqual(result["state"], "interrupted")
        self.assertEqual(len(self.fixture.installs), 1)

    def test_native_context_hides_paths_from_regular_client(self):
        public = self.fixture.service.content.capture("live-1")
        private = self.fixture.service.content.capture("live-1", native=True)
        self.assertNotIn("nativeContext", public)
        self.assertEqual(private["nativeContext"]["modRoot"], str(self.fixture.mod))
        self.assertNotIn(str(self.fixture.root), json.dumps(public))

    def test_new_target_collision_and_windows_names_rejected(self):
        for name in ("../outside", "CON", "folder/name", "Example.", " leading", "", "LPT9.txt"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                valid_target_name(name)
        result = self.fixture.service.content.capture(target_name="eXamPle")
        self.assertFalse(result["ok"], result)
        allowed = self.fixture.service.content.capture(target_name="New Content", native=True)
        self.assertTrue(allowed["ok"], allowed)
        self.assertTrue(allowed["newTarget"])
        self.assertFalse((self.fixture.mods / "New Content").exists())

    def test_exact_download_and_association_ownership(self):
        f = self.fixture
        context = f.service.content._context(f.service.content.capture("live-1")["contextId"])
        data = f.payload()["resolvedArchive"]
        result = verify_acquisition(data, "download-fixture", context, threading.Event())
        self.assertEqual(result, f.identity)
        changed = deepcopy(data)
        changed["receipt"]["binding"]["association"]["itemId"] = "other"
        with self.assertRaisesRegex(ValueError, "receipt"):
            verify_acquisition(changed, "download-fixture", context, threading.Event())
        outside = f.root / "archive.zip"
        shutil.copyfile(f.archive, outside)
        changed = {**data, "archivePath": str(outside)}
        with self.assertRaisesRegex(ValueError, "exact download"):
            verify_acquisition(changed, "download-fixture", context, threading.Event())

    def test_symlink_component_rejected(self):
        link = self.fixture.root / "linked-mod"
        try:
            link.symlink_to(self.fixture.mod, target_is_directory=True)
        except OSError:
            self.skipTest("Host cannot create symlinks")
        with self.assertRaisesRegex(ValueError, "Links"):
            real_path(link / "payload.txt")


@unittest.skipUnless(os.environ.get("MODHUNTER_CONTENT_CLI"), "Set MODHUNTER_CONTENT_CLI to the freshly built matching Rust CLI")
class ActualRustContentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = Fixture(self.temp.name)
        self.addCleanup(self.fixture.service.stop)
        executable = Path(os.environ["MODHUNTER_CONTENT_CLI"]).resolve()
        self.fixture.service.content.runner = ContentRunner(executable, timeout=30)
        self.fixture.service.duplicates.runner = DuplicateRunner(executable, timeout=30)

    def operation(self, action, *, item="live-1", backup=None, **extra):
        f = self.fixture
        captured = f.service.content.capture(item)
        self.assertTrue(captured["ok"], captured)
        f.sequence += 1
        request = {"reviewId": f"review-987654321-{f.sequence}", "contextId": captured["contextId"], "action": action, **extra}
        if backup:
            request["backupId"] = backup
        accepted = f.service.content.start_recovery(request)
        self.assertTrue(accepted["ok"], accepted)
        return f.wait(accepted["jobId"], 10)

    def test_real_helper_install_snapshot_seal_and_guarded_restore(self):
        f = self.fixture
        before = FixtureRunner.tree(f.mod)
        accepted = f.service.content.start_install(f.payload())
        self.assertTrue(accepted["ok"], accepted)
        installed = f.wait(accepted["jobId"], 10)
        self.assertTrue(installed["ok"], installed)
        self.assertTrue(installed["backup"]["undoAvailable"])
        self.assertEqual((f.mod / "payload.txt").read_text(), "after")
        restored = self.operation("restore", backup=installed["backupId"], confirm="RESTORE")
        self.assertTrue(restored["ok"], restored)
        self.assertEqual(FixtureRunner.tree(f.mod), before)
        self.assertFalse(restored["refreshRequired"])

    def test_real_helper_rejects_later_file_changes_and_preserves_backup(self):
        f = self.fixture
        installed = f.wait(f.service.content.start_install(f.payload())["jobId"], 10)
        self.assertTrue(installed["ok"], installed)
        (f.mod / "user-added.txt").write_text("keep this")
        result = self.operation("restore", backup=installed["backupId"], confirm="RESTORE")
        self.assertFalse(result["ok"], result)
        self.assertEqual((f.mod / "user-added.txt").read_text(), "keep this")
        context = f.service.content.capture("live-1")
        listed = f.service.content.backup_list(context["contextId"])
        self.assertTrue(listed["ok"], listed)
        self.assertEqual(listed["backups"][0]["id"], installed["backupId"])

    def test_exact_backup_recovery_releases_interrupted_stage_without_changing_old_outcome(self):
        f = self.fixture
        installed = f.wait(f.service.content.start_install(f.payload())["jobId"], 10)
        self.assertTrue(installed["ok"], installed)
        prior = f.service.content._jobs[installed["jobId"]]
        prior.update(state="interrupted", ok=False, stageReleaseAllowed=False)
        f.service.content._persist()
        restored = self.operation("restore", backup=installed["backupId"], confirm="RESTORE")
        self.assertTrue(restored["ok"], restored)
        acknowledged = next(j for j in f.service.content.jobs()["jobs"] if j["jobId"] == installed["jobId"])
        self.assertEqual(acknowledged["state"], "interrupted")
        self.assertFalse(acknowledged["ok"])
        self.assertTrue(acknowledged["stageReleaseAllowed"])
        self.assertEqual(acknowledged["stageReleaseRecoveryJobId"], restored["jobId"])
        self.assertEqual((f.mod / "payload.txt").read_text(), "before")

    def test_batch_structured_preflight_refusal_changes_no_earlier_target(self):
        f = self.fixture
        shutil.copytree(f.mod, f.mods / "Second")
        (f.profile / "modlist.txt").write_text("-Example\n-Second\n")
        self.dynamic_inventory(["Example", "Second"])
        backups = []
        for name, item in (("Example", "live-1"), ("Second", "live-Second")):
            prepared = self.operation("snapshot", item=item, confirm="BACKUP")
            self.assertTrue(prepared["ok"], prepared)
            (f.mods / name / "payload.txt").write_text("after")
            sealed = self.operation("seal", item=item, backup=prepared["backupId"], confirm="SEAL")
            self.assertTrue(sealed["ok"], sealed)
            backups.append({"itemId": item, "backupId": prepared["backupId"]})
        original = f.service.content.runner.run
        with patch.object(f.service.content.runner, "run", side_effect=lambda request, **kwargs:
                {"ok": False, "error": "second preflight refused"} if request["action"] == "previewRestore" and request["modName"] == "Second" else original(request, **kwargs)):
            accepted = f.service.content.start_batch_restore({"reviewId": "review-161803398-1", "contextId": f.service.content.capture()["contextId"],
                "backups": backups, "confirm": "RESTORE"})
            self.assertTrue(accepted["ok"], accepted)
            result = f.wait(accepted["jobId"], 10)
        self.assertEqual(result["state"], "failed", result)
        self.assertIn("second preflight refused", result["error"])
        self.assertEqual((f.mod / "payload.txt").read_text(), "after")
        self.assertEqual((f.mods / "Second" / "payload.txt").read_text(), "after")

    def dynamic_inventory(self, entries):
        f = self.fixture
        objects = {name: SimpleNamespace(name=lambda n=name: n, version=lambda: "2.0", absolutePath=lambda n=name: str(f.mods / n)) for name in entries}
        f.mod_list.allMods = lambda: [n for n in objects if (f.mods / n).is_dir()]
        f.mod_list.getMod = lambda name: objects.get(name) if (f.mods / name).is_dir() else None
        def snapshot():
            rows = [{"id": "live-1" if name == "Example" else "live-" + name, "mo2InternalName": name, "name": name,
                     "installedVersion": "2.0", "enabled": False, "nexusId": 1} for name in f.mod_list.allMods()]
            return {"profileId": "profile-1", "profileName": f.profile_name, "game": "fixture", "gameRuntime": "1", "items": rows}
        f.snapshot = snapshot
        f.refresh_snapshot()
        return objects

    def test_real_new_target_installs_separately_and_undo_retains_created_bytes(self):
        f = self.fixture
        objects = self.dynamic_inventory(["Example", "New Content"])
        f.receipt["binding"] = {"contentIntent": "newContent", "association": None}
        (f.acquisitions / "receipts.json").write_bytes(canonical_json({"schema": 1, "jobs": [f.receipt]}))
        captured = f.service.content.capture(target_name="New Content")
        self.assertTrue(captured["ok"], captured)
        data = f.payload(capture=False)
        data.update(contextId=captured["contextId"], targetName="New Content")
        def install():
            target = f.mods / "New Content"
            target.mkdir()
            (target / "payload.txt").write_text("separate new content")
            return objects["New Content"]
        f.install_hook = install
        installed = f.wait(f.service.content.start_install(data)["jobId"], 10)
        self.assertTrue(installed["ok"], installed)
        self.assertTrue(installed["backup"]["newTarget"])
        self.assertEqual((f.mod / "payload.txt").read_text(), "before")
        restored = self.operation("restoreNew", item="live-New Content", backup=installed["backupId"], confirm="RESTORE")
        self.assertTrue(restored["ok"], restored)
        self.assertFalse((f.mods / "New Content").exists())
        self.assertTrue(restored["result"]["retainedCreatedFiles"])
        captured = f.service.content.capture(backup_id=installed["backupId"], native=True)
        self.assertTrue(captured["ok"], captured)
        self.assertTrue(captured["missingTarget"])
        retained = Path(captured["nativeContext"]["vaultRoot"]) / installed["backupId"] / "new-target-files" / "payload.txt"
        self.assertEqual(retained.read_text(), "separate new content")
        listed = f.service.content.backup_list(f.service.content.capture()["contextId"])
        self.assertTrue(listed["ok"], listed)
        self.assertEqual([(row["id"], row["itemId"]) for row in listed["backups"]], [(installed["backupId"], "")])
        deleted = f.service.content.start_recovery({"reviewId": "review-987654321-999", "contextId": captured["contextId"],
            "action": "deleteNewBackup", "backupId": installed["backupId"], "confirm": "DELETE BACKUP"})
        self.assertTrue(deleted["ok"], deleted)
        finished = f.wait(deleted["jobId"], 10)
        self.assertTrue(finished["ok"], finished)
        self.assertFalse(retained.exists())
        self.assertFalse((f.mods / "New Content").exists())
        self.assertEqual((f.mod / "payload.txt").read_text(), "before")

    def test_real_retention_preview_and_apply_keeps_newest_backup(self):
        identities = []
        for value in ("first", "second", "third"):
            prepared = self.operation("snapshot", confirm="BACKUP")
            self.assertTrue(prepared["ok"], prepared)
            ident = prepared["backupId"]
            identities.append(ident)
            (self.fixture.mod / "payload.txt").write_text(value)
            sealed = self.operation("seal", backup=ident, confirm="SEAL")
            self.assertTrue(sealed["ok"], sealed)
        preview = self.operation("retentionPreview", retentionCount=1)
        self.assertTrue(preview["ok"], preview)
        plan = preview["result"]
        self.assertEqual(len(plan["eligible"]), 2)
        applied = self.operation("retentionApply", retentionCount=1, confirm="DELETE BACKUPS", expectedDigest=plan["digest"], selected=[r["id"] for r in plan["eligible"]])
        self.assertTrue(applied["ok"], applied)
        listed = self.fixture.service.content.backup_list(self.fixture.service.content.capture("live-1")["contextId"])
        self.assertEqual([row["id"] for row in listed["backups"]], [identities[-1]])

    def duplicate_operation(self, action, **extra):
        f = self.fixture
        captured = f.service.content.capture()
        self.assertTrue(captured["ok"], captured)
        f.sequence += 1
        accepted = f.service.duplicates.start({"action": action, "reviewId": f"review-123123123-{f.sequence}", "contextId": captured["contextId"], **extra})
        self.assertTrue(accepted["ok"], accepted)
        return f.wait(accepted["jobId"], 10)

    def test_real_duplicate_report_quarantine_and_digest_bound_undo(self):
        f = self.fixture
        shutil.copytree(f.mod, f.mods / "Retained")
        (f.mods / "Retained" / "meta.ini").write_text("different metadata")
        (f.profile / "modlist.txt").write_text("-Example\n-Retained\n")
        self.dynamic_inventory(["Example", "Retained"])
        before = FixtureRunner.tree(f.mod)
        scan = self.duplicate_operation("scan")
        self.assertTrue(scan["ok"], scan)
        report = f.service.duplicates.state()["report"]
        self.assertTrue(report["groups"], report)
        self.assertNotIn(str(f.root), json.dumps(report))
        quarantined = self.duplicate_operation("quarantine", modName="Example", retainedModName="Retained", expectedDigest=report["digest"], confirm="QUARANTINE")
        self.assertTrue(quarantined["ok"], quarantined)
        self.assertFalse(f.mod.exists())
        record = quarantined["result"].get("record") or quarantined["result"].get("quarantine")
        self.assertTrue(record, quarantined)
        preview = self.duplicate_operation("previewRestore", id=record["id"])
        self.assertTrue(preview["ok"], preview)
        restored = self.duplicate_operation("restore", id=record["id"], expectedDigest=preview["result"]["digest"], confirm="RESTORE")
        self.assertTrue(restored["ok"], restored)
        self.assertEqual(FixtureRunner.tree(f.mod), before)

    def test_real_quarantine_deletion_requires_current_complete_preview(self):
        f = self.fixture
        shutil.copytree(f.mod, f.mods / "Retained")
        (f.profile / "modlist.txt").write_text("-Example\n-Retained\n")
        self.dynamic_inventory(["Example", "Retained"])
        self.assertTrue(self.duplicate_operation("scan")["ok"])
        report = f.service.duplicates.state()["report"]
        quarantined = self.duplicate_operation("quarantine", modName="Example", retainedModName="Retained", expectedDigest=report["digest"], confirm="QUARANTINE")
        self.assertTrue(quarantined["ok"], quarantined)
        record = quarantined["result"]["record"]
        preview = self.duplicate_operation("previewDrop", id=record["id"])
        self.assertTrue(preview["ok"], preview)
        (f.profile / "modlist.txt").write_text("# Changed after preview\n-Example\n-Retained\n")
        refused = self.duplicate_operation("drop", id=record["id"], expectedDigest=preview["result"]["digest"], confirm="DELETE QUARANTINE")
        self.assertFalse(refused["ok"], refused)
        fresh = self.duplicate_operation("previewDrop", id=record["id"])
        self.assertTrue(fresh["ok"], fresh)
        self.assertNotEqual(fresh["result"]["digest"], preview["result"]["digest"])
        deleted = self.duplicate_operation("drop", id=record["id"], expectedDigest=fresh["result"]["digest"], confirm="DELETE QUARANTINE")
        self.assertTrue(deleted["ok"], deleted)
        self.assertTrue(deleted["quarantineDeleted"])
        self.assertEqual((f.mods / "Retained" / "payload.txt").read_text(), "before")
        self.assertFalse(f.mod.exists())

    def test_real_archive_selected_receipt_rejects_unselected_injected_entry(self):
        from mo2_update_arsenal.content_recovery import ArchiveRunner
        f = self.fixture
        with zipfile.ZipFile(f.archive, "w") as archive:
            archive.writestr("Data/textures/chosen.dds", b"chosen texture")
            archive.writestr("Data/textures/optional.dds", b"optional texture")
        original = file_identity(f.archive)
        runner = ArchiveRunner(f.service.content.runner.executable, timeout=30)
        stage = f.root / "review-stage"
        request = {"action": "inspect", "archive": str(f.archive), "stage": str(stage), "expectedSha256": original["sha256"], "expectedBytes": original["bytes"], "selected": []}
        inspected = runner.run(request)
        request.update(action="prepareSelected", selected=["textures/chosen.dds"])
        prepared = runner.run(request)["preparedArchive"]
        review = {"stage": str(stage), "incomingRoot": inspected["incomingRoot"], "manifestDigest": inspected["manifestDigest"],
                  "archiveIdentity": original, "requiresInstaller": False, "preparedArchive": {k: v for k, v in prepared.items() if k != "entries"}}
        verified = verify_selected_archive(review, original, threading.Event())
        self.assertEqual(verified["sha256"], prepared["sha256"])
        f.identity = original
        f.receipt.update(bytes=original["bytes"], sha256=original["sha256"])
        (f.acquisitions / "receipts.json").write_bytes(canonical_json({"schema": 1, "jobs": [f.receipt]}))
        data = f.payload()
        context = f.service.content._context(data["contextId"])
        compared = f.service.content.runner.run(f.service.content._request(context, "compare", other=inspected["incomingRoot"]))
        self.assertTrue(compared["ok"], compared)
        review["comparisonDigest"] = compared["digest"]
        # The native host nests a compact reviewed receipt under resolvedArchive;
        # Python must independently reverify it and the full compared input tree.
        data.pop("review")
        data["resolvedArchive"]["review"] = review
        before = FixtureRunner.tree(f.mod)
        def install():
            self.assertEqual(Path(f.installs[-1][0]), Path(prepared["path"]))
            with zipfile.ZipFile(f.installs[-1][0]) as archive:
                archive.extractall(f.mod)
            f.version = "2.0"
            return f.target
        f.install_hook = install
        accepted = f.service.content.start_install(data)
        self.assertTrue(accepted["ok"], accepted)
        installed = f.wait(accepted["jobId"], 10)
        self.assertTrue(installed["ok"], installed)
        self.assertEqual((f.mod / "textures/chosen.dds").read_bytes(), b"chosen texture")
        self.assertFalse((f.mod / "textures/optional.dds").exists())
        restored = self.operation("restore", backup=installed["backupId"], confirm="RESTORE")
        self.assertTrue(restored["ok"], restored)
        self.assertEqual(FixtureRunner.tree(f.mod), before)
        with zipfile.ZipFile(prepared["path"], "a") as archive:
            archive.writestr("textures/unselected.dds", b"do not install")
        with self.assertRaisesRegex(ValueError, "Prepared archive bytes changed"):
            verify_selected_archive(review, original, threading.Event())

    def test_native_content_probe_protocol_against_real_cli_and_loopback(self):
        import importlib.util
        import urllib.request
        f = self.fixture
        shutil.rmtree(f.mod)
        for name in ("Smoke Enabled", "Smoke Disabled"):
            folder = f.mods / name
            folder.mkdir()
            (folder / "meta.ini").write_text("version=2")
            (folder / "smoke-fixture.txt").write_text("Harmless fixture content for " + name + ".\n")
        (f.mods / "Smoke Disabled" / "ModhunterSmoke.esp").write_bytes(b"TES4" + bytes(38))
        (f.profile / "modlist.txt").write_text("-Smoke Disabled\n+Smoke Enabled\n")
        (f.root / "modhunter-smoke-fixture.json").write_text(json.dumps({"purpose": "isolated native MO2 CI fixture", "runId": "portable-protocol-test"}))
        objects = self.dynamic_inventory(["Smoke Enabled", "Smoke Disabled"])
        snapshot = f.snapshot
        f.snapshot = lambda: {**snapshot(), "game": "modhuntersmoke"}
        f.refresh_snapshot()
        path = Path(__file__).resolve().parents[1] / "native/scripts/mo2_smoke_plugin/modhunter_native_smoke/content_probe.py"
        spec = importlib.util.spec_from_file_location("content_probe", path)
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)
        address = f.service.start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def api(path, data=None, *, timeout, native=False):
            headers = {"X-Arsenal-Token": f.service.token}
            if native:
                headers["X-Modhunter-Native"] = "content-v1"
            body = None
            if data is not None:
                headers.update({"Origin": address.rstrip("/"), "Content-Type": "application/json"})
                body = json.dumps(data).encode()
            request = urllib.request.Request(address.rstrip("/") + path, data=body, headers=headers)
            with opener.open(request, timeout=timeout) as response:
                return json.load(response)
        rows = {r["mo2InternalName"]: r for r in f.snapshot()["items"]}
        result = probe.exercise_content_recovery(api, f.root, "profile-1", rows, timeout=20)
        self.assertTrue(result["actualModBytesRestored"], result)
        self.assertEqual(result["batchTargets"], 2)
        self.assertTrue(result["lostAckDeduplicated"])
        shutil.rmtree(f.acquisitions)
        intent, dialogs = {}, []
        def arm(value):
            intent.update(value)
        def install():
            job = f.service.content._jobs[intent["reviewId"]]
            self.assertTrue(job["nativeStarted"])
            self.assertTrue(job["backupId"])
            self.assertEqual(f.service.content._lease, intent["reviewId"])
            self.assertEqual(f.installs[-1][1], "Smoke Disabled")
            with zipfile.ZipFile(f.installs[-1][0]) as archive:
                archive.extractall(f.mods / "Smoke Disabled")
            dialogs.extend([{"dialog": "SimpleInstallDialog", "action": "accept exact fixture name"},
                            {"dialog": "QueryOverwriteDialog", "action": "merge exact fixture target"}])
            return objects["Smoke Disabled"]
        f.install_hook = install
        installed = probe.exercise_native_install(api, f.root, "profile-1", rows, arm, lambda: dialogs,
            cli_path=f.service.content.runner.executable, timeout=20)
        self.assertTrue(installed["actualModBytesRestored"], installed)
        self.assertTrue(installed["bundledArchiveReviewVerified"], installed)
        self.assertEqual(installed["originalFiles"], 3)
        self.assertEqual(len(f.installs), 1)
        # The real-Windows proof runs this same probe inside MO2. This portable
        # test substitutes the native installer and its dialog events; it proves
        # the HTTP/CLI contract only and is never published as real Qt evidence.


if __name__ == "__main__":
    unittest.main()
