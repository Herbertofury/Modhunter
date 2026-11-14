"""Exercise the real backup method with disposable files, not a native MO2 profile."""
import ast
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


def actual_backup(name="_profile_backup"):
    source = Path(__file__).resolve().parents[1] / "mo2_update_arsenal/plugin.py"
    module = ast.parse(source.read_text("utf-8"))
    cls = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == "ModUpdateArsenal")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)
    namespace = dict(Path=Path, datetime=datetime, timezone=timezone, json=json, shutil=shutil, os=os,
                     __package__="mo2_update_arsenal")
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), namespace)
    return namespace[name]


class ProfileBackupDurabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = self.root / "profile"
        self.profile.mkdir()
        self.names = ("modlist.txt", "plugins.txt", "loadorder.txt", "archives.txt", "lockedorder.txt")
        self.original = {name: ("+Étoile 雪\r\n" + name).encode() for name in self.names}
        for name, data in self.original.items():
            (self.profile / name).write_bytes(data)
        self.plugin = SimpleNamespace(
            org=SimpleNamespace(profilePath=lambda: str(self.profile),
                                pluginDataPath=lambda: str(self.root / "data"), profileName=lambda: "Fixture"),
            server=SimpleNamespace(profile_id=lambda: "fixture"))

    def test_missing_profile_does_not_create_a_successful_empty_backup(self):
        shutil.rmtree(self.profile)
        with self.assertRaises(OSError):
            actual_backup()(self.plugin)

    def test_empty_profile_path_does_not_fall_back_to_working_directory(self):
        self.plugin.org.profilePath = lambda: ""
        with self.assertRaises(OSError):
            actual_backup()(self.plugin)

    def test_empty_profile_cannot_be_reported_as_a_safety_snapshot(self):
        for name in self.names:
            (self.profile / name).unlink()
        with self.assertRaises(OSError):
            actual_backup()(self.plugin)

    def test_receipt_requires_synced_copies_and_manifest_without_changing_originals(self):
        synced = []
        fsync = os.fsync
        def sync(fd):
            self.assertGreater(os.fstat(fd).st_size, 0)
            synced.append(fd)
            fsync(fd)
        with patch.object(os, "fsync", side_effect=sync):
            result = actual_backup()(self.plugin)
        self.assertEqual(len(synced), 6)
        self.assertTrue(result["ok"])
        destination = Path(result["backupPath"])
        for name, data in self.original.items():
            self.assertEqual((destination / name).read_bytes(), data)
            self.assertEqual((self.profile / name).read_bytes(), data)
        manifest = json.loads((destination / "manifest.json").read_text())
        self.assertEqual(manifest["files"], list(self.names))

    def test_native_installer_is_not_called_when_backup_sync_fails(self):
        downloads = self.root / "downloads"
        downloads.mkdir()
        archive = downloads / "fixture.zip"
        archive.write_bytes(b"fixture archive; never installed")
        snapshot = {"profileId": "fixture", "profileName": "Fixture", "items": []}
        self.plugin.server.downloads = lambda: {7: {"status": "installing", "profileId": "fixture"}}
        self.plugin.server.selection_snapshot = lambda ids: snapshot
        self.plugin.server.native_snapshot = lambda: snapshot
        self.plugin.org.downloadsPath = lambda: str(downloads)
        self.plugin.org.downloadManager = lambda: SimpleNamespace(downloadPath=lambda did: str(archive))
        installed = []
        self.plugin.org.installMod = lambda path: installed.append(path)
        self.plugin._profile_backup = lambda: actual_backup()(self.plugin)
        with patch.object(os, "fsync", side_effect=OSError("fixture persistence failure")):
            result = actual_backup("_install")(self.plugin, 7)
        self.assertFalse(result["ok"])
        self.assertIn("persistence failure", result["error"])
        self.assertEqual(installed, [])
        self.assertTrue(archive.exists())
        shutil.rmtree(self.profile)
        result = actual_backup("_install")(self.plugin, 7)
        self.assertFalse(result["ok"])
        self.assertIn("profile directory is unavailable", result["error"])
        self.assertEqual(installed, [])


    def test_failed_file_or_manifest_sync_never_returns_success(self):
        for failure_at in (1, 6):
            calls = 0
            def fail(fd):
                nonlocal calls
                calls += 1
                if calls == failure_at:
                    raise OSError("fixture persistence failure")
            with self.subTest(failure_at=failure_at), patch.object(os, "fsync", side_effect=fail):
                with self.assertRaisesRegex(OSError, "persistence failure"):
                    actual_backup()(self.plugin)
            for name, data in self.original.items():
                self.assertEqual((self.profile / name).read_bytes(), data)
        self.assertTrue(actual_backup()(self.plugin)["ok"])


if __name__ == "__main__":
    unittest.main()
