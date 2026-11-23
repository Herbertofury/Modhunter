"""Verify state-preserving moves through the actual frontend reconciler."""

from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which("node"), "Node is required for frontend row reconciliation tests")
class KeyedRowStateTests(unittest.TestCase):
    def test_same_owner_moves_and_legacy_fallbacks(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [shutil.which("node"), "--test", "tests/keyed_row_state.test.cjs"],
            cwd=root, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
