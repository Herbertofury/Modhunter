import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest

from scripts.protected_inventory import capture, compare


class ProtectedInventoryTests(unittest.TestCase):
    def test_same_size_and_timestamp_content_change_is_detected(self):
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(temp) / "mods"
            root.mkdir()
            empty = root / "empty directory"
            empty.mkdir()
            asset = root / "asset.bin"
            asset.write_bytes(b"abc")
            before, after = (root.parent / name for name in ("before.sqlite", "after.sqlite"))
            capture(before, [root], [])
            stamp = asset.stat()
            asset.write_bytes(b"xyz")
            os.utime(asset, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            capture(after, [root], [])
            self.assertEqual(compare(before, before), 0)
            self.assertEqual(compare(before, after), 1)

    def test_receipt_cannot_overwrite_or_live_inside_protected_scope(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "mods"
            root.mkdir()
            with self.assertRaises(ValueError):
                capture(root / "receipt", [root], [])
            receipt = root.parent / "receipt"
            receipt.write_bytes(b"original")
            with self.assertRaises(FileExistsError):
                capture(receipt, [root], [])
            self.assertEqual(receipt.read_bytes(), b"original")
