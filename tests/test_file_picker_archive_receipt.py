"""Actual local bridge reads a durable fixture journal; no provider or MO2 used."""
import argparse
from copy import deepcopy
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mo2_update_arsenal.bridge import ArsenalService
from mo2_update_arsenal.operation_journal import OperationJournal

PROFILE = {"profileId": "a", "profileName": "Fixture", "game": "skyrimspecialedition", "gameRuntime": "1.5.97", "groups": [],
           "items": [{"id": f"item-{i}", "nexusId": 77, "installedVersion": "1.0", "name": f"Fixture row {i}",
                      "source": {"type": "nexus", "verified": True, "url": "https://www.nexusmods.com/skyrimspecialedition/mods/77"},
                      "update": {"verdict": "candidate", "source": "nexus-api", "latestVersion": "2.0"}}
                     for i in range(2)]}
FILES = [
    {"fileId": 10, "name": "Same archive name", "fileName": "Shared.7z", "version": "1.0", "category": 2},
    {"fileId": 11, "name": "Same archive name", "fileName": "Shared.7z", "version": "1.0", "category": 2},
    {"fileId": 12, "name": "SE main", "fileName": "SE.7z", "version": "2.0", "category": 1},
    {"fileId": 13, "name": "AE main", "fileName": "AE.7z", "version": "2.0", "category": 1},
    {"fileId": 14, "name": "Patch", "fileName": "Patch.7z", "version": "2.0", "category": 2},
]
STATE = {"profileId": "a", "itemId": "item-0", "game": "skyrimspecialedition", "gameRuntime": "1.5.97",
         "modId": 77, "fileId": 10, "installedVersion": "0.9", "targetVersion": "1.0", "provider": "nexus",
         "status": "installed", "verifiedUpdate": True, "fileName": "C:/Synthetic-Private/Shared.7z",
         "url": "https://example.invalid/token=PRIVATE", "backupPath": "C:/Synthetic-Backup"}


class Fixture:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.native = []
        self.service = ArsenalService("<!doctype html><!--__MO2_BOOT__-->", Path(self.tmp.name) / "cache.json",
                                      self.native.append, deepcopy(PROFILE))
        self.files = deepcopy(FILES)
        self.fetches = []
        self.service.fetch_files = self.fetch_files
        self.service.start()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.service.stop()
        self.tmp.cleanup()

    def fetch_files(self, mod_id):
        self.fetches.append(mod_id)
        return {"ok": True, "files": deepcopy(self.files), "modId": mod_id}

    def record(self, when, *, operation, **overrides):
        with patch("mo2_update_arsenal.operation_journal._stamp", return_value=when):
            assert self.service.journal.record({**STATE, "operationId": operation, **overrides}, 4)

    def call(self, item="item-0", include=True):
        port = self.service._server.server_port
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            conn.request("GET", "/api/files?modId=77&itemId=" + item + ("&reviewArchive=1" if include else ""),
                         headers={"X-Arsenal-Token": self.service.token})
            response = conn.getresponse()
            assert response.status == 200
            return json.loads(response.read())
        finally:
            conn.close()

    def reopen(self):
        journal = self.service.journal
        self.service.journal = OperationJournal(journal.path, "second-synthetic-session")


class PickerArchiveReceiptTest(unittest.TestCase):
    def test_exact_own_row_and_provider_id_match_without_granting_download(self):
        with Fixture() as f:
            f.record("2026-11-21T17:21:11+00:00", operation="a")
            f.record("2026-11-21T17:22:11+00:00", operation="b", itemId="item-1", fileId=11)
            f.record("2026-11-21T17:23:11+00:00", operation="other-profile", profileId="other", fileId=12)
            f.reopen()
            before = f.service.journal.path.read_bytes()
            a, b, standard = f.call(), f.call("item-1"), f.call(include=False)
            self.assertEqual(a["archiveReceipt"], {"state": "listed", "fileId": 10})
            self.assertEqual(b["archiveReceipt"], {"state": "listed", "fileId": 11})
            self.assertNotIn("archiveReceipt", standard)
            self.assertEqual(a["files"], standard["files"], "review evidence cannot reorder, hide or recommend a file")
            self.assertEqual(a["reviewAdvice"], standard["reviewAdvice"])
            self.assertEqual(a["profileId"], "a")
            self.assertEqual({file["fileId"] for file in a["files"]}, {10, 11, 12, 13, 14})
            self.assertEqual(a["reviewAdvice"]["matchingMainFiles"], 2)
            self.assertFalse(a["reviewAdvice"]["hasUniqueMainMatch"])
            self.assertEqual(f.native, [])
            self.assertEqual(f.service.journal.path.read_bytes(), before)
            self.assertNotRegex(json.dumps(a), "Synthetic-Private|Synthetic-Backup|token=")

    def test_same_name_does_not_substitute_unlisted_or_duplicate_id(self):
        with Fixture() as f:
            f.record("2026-11-21T17:21:11+00:00", operation="a")
            f.files = [file for file in FILES if file["fileId"] != 10]
            self.assertEqual(f.call()["archiveReceipt"], {"state": "notListed", "fileId": 10})
            f.files = deepcopy(FILES) + [{"fileId": 10, "name": "Conflicting row", "version": "3.0"}]
            self.assertEqual(f.call()["archiveReceipt"], {"state": "unknown"})
            self.assertEqual(f.native, [])

    def test_newer_uncertain_other_runtime_or_tied_evidence_does_not_launder_older_receipt(self):
        for overrides, when in [
            ({"status": "needs-review"}, "2026-11-21T17:22:11+00:00"),
            ({"status": "installing"}, "2026-11-21T17:22:11+00:00"),
            ({"gameRuntime": "1.6.1170"}, "2026-11-21T17:22:11+00:00"),
            ({"targetVersion": "1.2"}, "2026-11-21T17:22:11+00:00"),
            ({"provider": "github", "fileId": 11}, "2026-11-21T17:22:11+00:00"),
            ({"fileId": 11}, "2026-11-21T18:21:11+01:00"),
            ({"fileId": 11}, "invalid-clock"),
        ]:
            with self.subTest(overrides=overrides, when=when), Fixture() as f:
                f.record("2026-11-21T17:21:11+00:00", operation="first")
                f.record(when, operation="second", **overrides)
                self.assertEqual(f.call()["archiveReceipt"], {"state": "unknown"})
                self.assertEqual(f.native, [])

    def test_profile_epoch_invalidation_discards_late_archive_evidence(self):
        with Fixture() as f:
            f.record("2026-11-21T17:21:11+00:00", operation="a")
            def renamed(*args):
                changed = deepcopy(PROFILE)
                changed["profileId"] = "alternate"
                f.service.set_snapshot(changed)
                f.service.set_snapshot(deepcopy(PROFILE))
                return f.fetch_files(77)
            f.service.fetch_files = renamed
            result = f.call()
            self.assertFalse(result["ok"])
            self.assertNotIn("archiveReceipt", result)
            self.assertEqual(f.native, [])


def evidence(path):
    with Fixture() as f:
        f.record("2026-11-21T17:21:11+00:00", operation="a")
        f.record("2026-11-21T17:22:11+00:00", operation="b", itemId="item-1", fileId=11)
        a, b = f.call(), f.call("item-1")
        f.files = [file for file in FILES if file["fileId"] != 10]
        missing = f.call()
        Path(path).write_text(json.dumps({"catalog": PROFILE, "a": a, "b": b, "missing": missing}), "utf8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture")
    args = parser.parse_args()
    if args.fixture:
        evidence(args.fixture)
    else:
        unittest.main(argv=["test_file_picker_archive_receipt"])
