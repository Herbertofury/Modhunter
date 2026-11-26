from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from sync_wiki import LEDGER, synchronize


class WikiSyncTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.source = Path(temp.name) / "source"
        self.wiki = Path(temp.name) / "wiki"
        self.source.mkdir()
        self.wiki.mkdir()

    def put(self, directory, name, text):
        (directory / name).write_bytes(text)

    def snapshot(self):
        return {x.name: x.read_bytes() for x in self.wiki.iterdir() if x.is_file()}

    def apply(self):
        return synchronize(self.source, self.wiki, apply=True)

    def test_preview_no_writes_then_apply_preserves_unknown_bytes(self):
        self.put(self.source, "Home.md", b"home\r\n")
        self.put(self.source, "README.md", b"repository instructions")
        self.put(self.wiki, "README.md", b"live instructions")
        self.put(self.wiki, "Personal.md", b"unknown\r\n")
        self.put(self.wiki, "image.png", b"\x00\xff")
        before = self.snapshot()
        self.assertEqual(synchronize(self.source, self.wiki), ["Home.md", LEDGER])
        self.assertEqual(self.snapshot(), before)
        self.apply()
        self.assertEqual(self.snapshot()["Home.md"], b"home\r\n")
        self.assertEqual({k: self.snapshot()[k] for k in before}, before)
        self.assertEqual(self.apply(), [])

    def test_home_sidebar_and_page_conflicts_stop_entire_apply(self):
        for name in ("Home.md", "_Sidebar.md", "Known.md"):
            self.put(self.source, name, b"source")
            self.put(self.wiki, name, b"hand edit")
        self.put(self.source, "First.md", b"should not be created")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, r"Home\.md.*Known\.md.*_Sidebar\.md"):
            self.apply()
        self.assertEqual(self.snapshot(), before)

    def test_identical_adoption_then_managed_update_without_deleting_retired_page(self):
        self.put(self.source, "Owned.md", b"first")
        self.put(self.source, "Retired.md", b"retained")
        self.put(self.wiki, "Owned.md", b"first")
        self.apply()
        (self.source / "Retired.md").unlink()
        self.put(self.source, "Owned.md", b"second")
        self.apply()
        self.assertEqual(self.snapshot()["Owned.md"], b"second")
        self.assertEqual(self.snapshot()["Retired.md"], b"retained")
        self.assertIn(b"Retired.md", self.snapshot()[LEDGER])

    def test_later_edit_or_deletion_requires_reconciliation(self):
        self.put(self.source, "Home.md", b"first")
        self.apply()
        self.put(self.source, "Home.md", b"second")
        self.put(self.wiki, "Home.md", b"manual addition")
        with self.assertRaisesRegex(ValueError, "independently edited"):
            self.apply()
        (self.wiki / "Home.md").unlink()
        with self.assertRaisesRegex(ValueError, "removed"):
            self.apply()
        self.assertFalse((self.wiki / "Home.md").exists())

    def test_corrupt_ledger_prevents_writes(self):
        self.put(self.source, "Home.md", b"new")
        self.put(self.wiki, LEDGER, b'{"Home.md": "no digest"}')
        with self.assertRaisesRegex(ValueError, "Invalid wiki ownership"):
            self.apply()
        self.assertFalse((self.wiki / "Home.md").exists())

    def test_case_alias_and_symlink_do_not_overwrite(self):
        self.put(self.source, "Home.md", b"new")
        self.put(self.wiki, "home.md", b"old")
        with self.assertRaisesRegex(ValueError, "case collision"):
            self.apply()
        (self.wiki / "home.md").unlink()
        (self.wiki / "Home.md").symlink_to(self.source / "Home.md")
        with self.assertRaisesRegex(ValueError, "regular file"):
            self.apply()


if __name__ == "__main__":
    unittest.main()
