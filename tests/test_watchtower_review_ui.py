"""Run dependency-free native review UI transaction and pagination regressions."""
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which("node"), "Node is required for the review UI protocol tests")
class WatchtowerReviewUITests(unittest.TestCase):
    def test_native_review_protocol_and_complete_pagination(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([shutil.which("node"), "--test", str(root / "tests/watchtower_review_ui.test.cjs")],
                                cwd=root, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
