"""Published provider checksums flow into the existing off-GUI content worker."""
import hashlib
import os
import threading
import unittest
from unittest.mock import patch

from mo2_update_arsenal.content_recovery import file_identity
from mo2_update_arsenal.source_adapters import asset_proof
import test_shared_install_queue as shared
from test_site_adapters import fixture


class ProviderArchiveTests(unittest.TestCase):
    setUp = shared.SharedQueueTests.setUp
    until = shared.SharedQueueTests.until
    control = shared.SharedQueueTests.control
    def source_ticket(self, hashes=None, size=None, provider="modrinth"):
        f = self.f
        item, _ = fixture(provider)
        row = f.snapshot()["items"][0]
        row.update(source=item["source"], update=item["update"], nexusId=0)
        asset = row["update"]["releaseAssets"][0]
        content = f.archive.read_bytes()
        asset["hashes"] = hashes if hashes is not None else {"sha512": hashlib.sha512(content).hexdigest()}
        asset["size"] = len(content) if size is None else size
        asset["assetProof"] = asset_proof(row["source"], row["update"], asset)
        snap = f.snapshot()
        snap["items"][0] = row
        f.service.set_snapshot(snap)
        f.ticket(901, "Example")
        f.service._downloads[901].update(provider=provider, assetKey=asset["assetKey"],
            assetProof=asset["assetProof"], targetVersion=row["update"]["latestVersion"])
        return asset

    def test_provider_sha512_success_runs_on_queue_worker(self):
        self.source_ticket()
        calls = []
        original = file_identity
        def measured(*args, **kwargs):
            if kwargs.get("expected"):
                calls.append(threading.current_thread().name)
            return original(*args, **kwargs)
        with patch("mo2_update_arsenal.content_recovery.file_identity", side_effect=measured):
            self.assertTrue(self.f.service.install_reviewed_download(901)["ok"])
            self.until(lambda: self.f.service.downloads()[901]["status"] == "installed")
        self.assertEqual(calls, ["Modhunter-reviewed-install-queue"])
        self.assertEqual(len(self.f.installs), 1)
        proof = next(iter(self.f.service.content._jobs.values()))["providerVerification"]
        self.assertEqual(proof["publishedDigestsMatched"], ["sha512"])
        self.assertTrue(proof["publishedSizeMatched"])

    def test_wrong_provider_digest_never_starts_backup_or_installer(self):
        self.source_ticket(hashes={"sha512": "0" * 128})
        self.f.service.install_reviewed_download(901)
        self.until(lambda: not self.f.service.install_queue()["active"])
        self.assertFalse(self.f.installs)
        self.assertFalse(self.f.service.content.runner.calls)
        self.assertIn("digest differs", self.f.service.downloads()[901]["lastError"])

    def test_wrong_provider_size_never_starts_installer(self):
        self.source_ticket(size=1)
        self.f.service.install_reviewed_download(901)
        self.until(lambda: not self.f.service.install_queue()["active"])
        self.assertFalse(self.f.installs)
        self.assertIn("size differs", self.f.service.downloads()[901]["lastError"])

    def test_provider_hashes_cannot_fall_back_to_legacy_gui_install(self):
        self.source_ticket()
        self.f.service.content.native_enabled = False
        self.f.service.install_reviewed_download(901)
        self.until(lambda: not self.f.service.install_queue()["active"])
        self.assertFalse(self.f.installs)
        self.assertIn("requires the native content worker", self.f.service.downloads()[901]["lastError"])

    def test_mutation_after_rehash_before_native_handoff_is_rejected(self):
        self.source_ticket()
        f = self.f
        f.service.content.runner.before_verify = lambda: f.archive.write_bytes(b"changed bytes")
        f.service.install_reviewed_download(901)
        self.until(lambda: not f.service.install_queue()["active"])
        self.assertFalse(f.installs)
        if os.name != "nt":
            self.assertIn("changed before native handoff", f.service.downloads()[901]["lastError"])
        else:
            self.assertTrue(f.service.downloads()[901]["lastError"])  # OS denies the write itself.

    def test_cancel_during_provider_verification_never_installs(self):
        self.source_ticket()
        entered, released = threading.Event(), threading.Event()
        original = file_identity
        def held(*args, **kwargs):
            if kwargs.get("expected"):
                entered.set()
                if not released.wait(3):
                    raise AssertionError("verification not released")
            return original(*args, **kwargs)
        with patch("mo2_update_arsenal.content_recovery.file_identity", side_effect=held):
            self.f.service.install_reviewed_download(901)
            try:
                self.assertTrue(entered.wait(2))
                self.control("cancelPreparation")
            finally:
                released.set()
            self.until(lambda: not self.f.service.install_queue()["active"])
        self.assertFalse(self.f.installs)
        self.assertFalse(self.f.service.downloads()[901]["autoInstallRequested"])

    def test_context_change_during_verification_prevents_install(self):
        self.source_ticket()
        original = file_identity
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            if kwargs.get("expected"):
                self.f.service.content.invalidate(reason="profile")
            return result
        with patch("mo2_update_arsenal.content_recovery.file_identity", side_effect=changed):
            self.f.service.install_reviewed_download(901)
            self.until(lambda: not self.f.service.install_queue()["active"])
        self.assertFalse(self.f.installs)

    def test_all_published_digests_must_match_and_unknown_hash_is_not_ignored(self):
        content = self.f.archive.read_bytes()
        hashes = {name: hashlib.new(name, content).hexdigest() for name in ("sha1", "sha256", "sha512")}
        self.assertEqual(file_identity(self.f.archive, expected={"hashes": hashes})["sha256"], hashes["sha256"])
        hashes["sha1"] = "0" * 40
        with self.assertRaisesRegex(ValueError, "digest differs"):
            file_identity(self.f.archive, expected={"hashes": hashes})
        with self.assertRaisesRegex(ValueError, "invalid or unsupported"):
            file_identity(self.f.archive, expected={"hashes": {"md5": "0" * 32}})

    def test_missing_provider_hash_does_not_claim_a_digest_match(self):
        self.source_ticket(hashes={}, provider="gitlab")
        self.f.service.install_reviewed_download(901)
        self.until(lambda: self.f.service.downloads()[901]["status"] == "installed")
        # Size and local consistency still checked; no fabricated provider digest.
        self.assertEqual(len(self.f.installs), 1)
        proof = next(iter(self.f.service.content._jobs.values()))["providerVerification"]
        self.assertEqual(proof["publishedDigestsMatched"], [])

    def test_archive_mutation_during_hash_is_rejected(self):
        content = self.f.archive.read_bytes()
        expected = {"hashes": {"sha512": hashlib.sha512(content).hexdigest()}}
        factory = hashlib.new
        archive = self.f.archive
        class MutatingDigest:
            def __init__(self, name):
                self.inner = factory(name)
            def update(self, data):
                self.inner.update(data)
                archive.write_bytes(content + b"changed")
            def hexdigest(self):
                return self.inner.hexdigest()
        with patch("mo2_update_arsenal.content_recovery.hashlib.new", side_effect=MutatingDigest):
            with self.assertRaisesRegex(ValueError, "changed while"):
                file_identity(archive, expected=expected)

    def test_restart_snapshot_of_verification_is_interrupted_not_replayed(self):
        from mo2_update_arsenal.content_recovery import ContentRecovery
        self.source_ticket()
        entered, released = threading.Event(), threading.Event()
        original = file_identity
        def held(*args, **kwargs):
            if kwargs.get("expected"):
                entered.set()
                if not released.wait(3):
                    raise AssertionError("verification not released")
            return original(*args, **kwargs)
        recovery_path = self.f.root / "verification-restart.json"
        with patch("mo2_update_arsenal.content_recovery.file_identity", side_effect=held):
            self.f.service.install_reviewed_download(901)
            try:
                self.assertTrue(entered.wait(2))
                recovery_path.write_bytes(self.f.service.content.ledger.read_bytes())
                self.control("cancelPreparation")
            finally:
                released.set()
            self.until(lambda: not self.f.service.install_queue()["active"])
        recovered = ContentRecovery(self.f.service, recovery_path)
        self.assertEqual(len(recovered._jobs), 1)
        self.assertEqual(next(iter(recovered._jobs.values()))["state"], "interrupted")
        self.assertEqual(recovered._requests, {})
        self.assertFalse(self.f.installs)

    def test_github_published_sha256_uses_same_verified_handoff(self):
        self.source_ticket(provider="github", hashes={"sha256": hashlib.sha256(self.f.archive.read_bytes()).hexdigest()})
        self.f.service.install_reviewed_download(901)
        self.until(lambda: self.f.service.downloads()[901]["status"] == "installed")
        self.assertEqual(len(self.f.installs), 1)

    def test_preserved_mtime_mutation_is_caught_by_filesystem_change_stamp(self):
        self.source_ticket()
        f = self.f
        before = f.archive.stat()
        content = f.archive.read_bytes()
        f.archive.write_bytes(content)
        os.utime(f.archive, ns=(before.st_atime_ns, before.st_mtime_ns))
        if f.archive.stat().st_ctime_ns == before.st_ctime_ns:
            self.skipTest("Filesystem does not expose changed metadata time; immutable-handle protection is not claimed")
        def changed():
            current = f.archive.stat()
            f.archive.write_bytes(bytes([content[0] ^ 1]) + content[1:])
            os.utime(f.archive, ns=(current.st_atime_ns, current.st_mtime_ns))
        f.service.content.runner.before_verify = changed
        f.service.install_reviewed_download(901)
        self.until(lambda: not f.service.install_queue()["active"])
        self.assertFalse(f.installs)
        if os.name != "nt":
            self.assertIn("changed before native handoff", f.service.downloads()[901]["lastError"])
        else:
            self.assertTrue(f.service.downloads()[901]["lastError"])  # OS denies the write itself.

    def test_unavailable_read_lease_stops_before_hash_or_installer(self):
        self.source_ticket()
        with patch("mo2_update_arsenal.archive_read_lock.archive_read_lock", side_effect=OSError("file held by writer")):
            self.f.service.install_reviewed_download(901)
            self.until(lambda: not self.f.service.install_queue()["active"])
        self.assertFalse(self.f.installs)
        self.assertFalse(self.f.service.content.runner.calls)
        self.assertIn("file held by writer", self.f.service.downloads()[901]["lastError"])

    def test_read_lease_lives_through_native_installer_result(self):
        from contextlib import contextmanager
        self.source_ticket()
        held = threading.Event()
        @contextmanager
        def lease(path):
            held.set()
            try:
                yield "fixture-lease"
            finally:
                held.clear()
        self.f.gate.clear()
        with patch("mo2_update_arsenal.archive_read_lock.archive_read_lock", side_effect=lease):
            self.f.service.install_reviewed_download(901)
            try:
                self.assertTrue(self.f.entered.wait(2))
                self.assertTrue(held.is_set())
            finally:
                self.f.gate.set()
            self.until(lambda: self.f.service.downloads()[901]["status"] == "installed")
        self.assertFalse(held.is_set())
