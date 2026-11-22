"""Actual host sharing tests; Windows never falls back to metadata-only mode."""
import os
from pathlib import Path
import tempfile
import unittest
from mo2_update_arsenal.archive_read_lock import archive_read_lock


class ArchiveReadLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "archive.zip"
        self.path.write_bytes(b"verified bytes")

    def test_platform_protection_is_explicit_and_other_readers_work(self):
        with archive_read_lock(self.path) as mode:
            self.assertEqual(mode, "windows-deny-write-delete" if os.name == "nt" else "metadata-only")
            self.assertEqual(self.path.read_bytes(), b"verified bytes")
        self.path.write_bytes(b"released")

    @unittest.skipUnless(os.name == "nt", "Windows file-sharing contract")
    def test_write_delete_and_replace_are_denied_until_release(self):
        replacement = self.path.with_name("other.zip")
        replacement.write_bytes(b"different bytes")
        with archive_read_lock(self.path):
            for action in (lambda: self.path.write_bytes(b"wrong"), self.path.unlink,
                           lambda: os.replace(replacement, self.path)):
                with self.assertRaises(OSError):
                    action()
            self.assertEqual(self.path.read_bytes(), b"verified bytes")
        os.replace(replacement, self.path)
        self.assertEqual(self.path.read_bytes(), b"different bytes")

    @unittest.skipUnless(os.name == "nt", "Windows file-sharing contract")
    def test_existing_writer_fails_closed_and_exception_releases_lease(self):
        with self.path.open("r+b"):
            with self.assertRaisesRegex(OSError, "cannot be held read-only"):
                with archive_read_lock(self.path):
                    self.fail("writer was silently accepted")
        with self.assertRaisesRegex(RuntimeError, "test exception"):
            with archive_read_lock(self.path):
                raise RuntimeError("test exception")
        self.path.write_bytes(b"released")
