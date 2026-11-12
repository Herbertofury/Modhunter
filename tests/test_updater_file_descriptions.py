"""Exact-file review sanitizes source metadata without losing file identity."""
import tempfile
import unittest
from pathlib import Path

from mo2_update_arsenal.bridge import ArsenalService


class FileDescriptionsTest(unittest.TestCase):
    def test_native_mo2_file_description_is_plain_text_and_keeps_exact_id(self):
        snapshot = {"profileId": "primary", "profileName": "Primary",
                    "game": "skyrimspecialedition", "items": [
                        {"id": "live-one", "nexusId": 44, "installedVersion": "1.0"}]}
        with tempfile.TemporaryDirectory() as temp:
            service = ArsenalService("<html></html>", Path(temp) / "cache.json",
                                     lambda request: request.finish({"ok": False}), snapshot)
            service.native_nexus_available = True
            calls = []
            def native(cmd, payload, timeout=20):
                calls.append((cmd, payload))
                return {"ok": True, "files": [{"fileId": 778, "name": "Main Archive",
                        "version": "2.0", "category": 1,
                        "description": "<b>Skyrim SE</b> build. Requires <i>Address Library</i>."}]}
            service.call_ui = native
            result = service.fetch_files(44)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["files"][0]["fileId"], 778)
            self.assertEqual(result["files"][0]["description"],
                             "Skyrim SE  build. Requires  Address Library .")
            self.assertEqual(calls[0][0], "nexus-files")
            self.assertNotIn("<", result["files"][0]["description"])

    def test_mod_not_in_profile_cannot_browse_nexus_files(self):
        snapshot = {"profileId": "primary", "profileName": "Primary",
                    "game": "skyrimspecialedition", "items": []}
        with tempfile.TemporaryDirectory() as temp:
            service = ArsenalService("<html></html>", Path(temp) / "cache.json",
                                     lambda request: request.finish({"ok": True}), snapshot)
            service.native_nexus_available = True
            self.assertFalse(service.fetch_files(44)["ok"])


if __name__ == "__main__":
    unittest.main()
