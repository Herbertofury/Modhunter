"""Exact project routes survive sanitization; notes never establish identity."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mo2_update_arsenal.model import source_url, take_snapshot
from mo2_update_arsenal.source_registry import SourceScanner, canonical_source_url, discover_local_source


class SourceURLTests(unittest.TestCase):
    def test_forum_routes_and_project_query_identifiers_are_preserved(self):
        cases = {
            "https://www.loverslab.com/index.php?/topic/123-mod/": "https://www.loverslab.com/index.php?/topic/123-mod/",
            "https://www.loverslab.com/index.php?/files/file/1234/&do=download&token=secret":
                "https://www.loverslab.com/index.php?/files/file/1234/&do=download",
            "https://example.com/project?id=123&page=2&tab=files": "https://example.com/project?id=123&page=2&tab=files",
            "https://example.com/index.php?route=%2Ftopic%2F123&name=some+mod&utm_source=mo2#post-5":
                "https://example.com/index.php?route=%2Ftopic%2F123&name=some+mod",
        }
        for original, expected in cases.items():
            with self.subTest(url=original):
                self.assertEqual(canonical_source_url(original), expected)

    def test_credentials_and_tracking_removed_without_reencoding_route(self):
        dirty = ("https://www.loverslab.com/index.php?/topic/123/&id=4&%74oken=secret&API_KEY=secret"
                 "&ACCESS_TOKEN=secret&sid=secret&X-Amz-Signature=secret&x-goog-credential=secret"
                 "&__cf_chl_tk=secret&utm_campaign=tracking&fbclid=tracking&tab=files")
        self.assertEqual(canonical_source_url(dirty), "https://www.loverslab.com/index.php?/topic/123/&id=4&tab=files")

    def test_unsafe_schemes_credentials_ports_and_local_hosts_remain_rejected(self):
        for url in ("http://example.com/mod", "file:///mods/readme", "javascript:alert(1)",
                    "https://user:password@example.com/mod", "https://example.com:8443/mod",
                    "https://localhost/mod", "https://node.localhost/mod", "https://host.local/mod",
                    "https://127.0.0.1/mod", "https://10.0.0.1/mod", "https://[::1]/mod",
                    "https://127.1/mod", "https://0x7f.0x0.0x0.0x1/mod"):
            with self.subTest(url=url):
                self.assertEqual(canonical_source_url(url), "")

    def test_unknown_game_does_not_become_verified_nexus_identity(self):
        self.assertFalse(source_url("unknown", 77)["verified"])
        self.assertEqual(source_url("unknown", 77)["url"], "")


class MetadataIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)

    def snapshot(self, nexus_id, notes="", native_url=""):
        mod = SimpleNamespace(name=lambda: "Mod", nexusId=lambda: nexus_id,
            version=lambda: "1.0", newestVersion=lambda: "2.0", isSeparator=lambda: False,
            absolutePath=lambda: str(self.path), notes=lambda: notes,
            repository=lambda: "Nexus", url=lambda: native_url)
        modlist = SimpleNamespace(allModsByProfilePriority=lambda profile: ["Mod"],
            getMod=lambda name: mod, priority=lambda name: 1, state=lambda name: "ACTIVE")
        organizer = SimpleNamespace(managedGame=lambda: SimpleNamespace(gameNexusName=lambda: "skyrimspecialedition"),
            profileName=lambda: "Default", basePath=lambda: str(self.path),
            modList=lambda: modlist, profile=lambda: "Default")
        return take_snapshot(organizer)["items"][0]

    def test_disabled_custom_url_and_dependency_notes_cannot_replace_nexus_identity(self):
        (self.path / "meta.ini").write_text("[General]\nhasCustomURL=false\nurl=https://github.com/old/custom\n")
        item = self.snapshot(77, "Requires https://github.com/dependency/project", "https://github.com/old/custom")
        self.assertEqual(item["source"]["type"], "nexus")
        self.assertEqual(item["source"]["url"], "https://www.nexusmods.com/skyrimspecialedition/mods/77?tab=files")
        self.assertTrue(item["source"]["verified"])

    def test_notes_without_known_identity_remain_available_only_as_advisory(self):
        item = self.snapshot(0, "Read https://github.com/dependency/project")
        self.assertEqual(item["source"]["url"], "https://github.com/dependency/project")
        self.assertFalse(item["source"]["verified"])
        self.assertEqual(item["source"]["kind"], "advisory")
        calls = []
        scanner = SourceScanner(lambda url: calls.append(url))
        result = scanner.scan({"items": [item]})
        self.assertEqual(calls, [])
        self.assertEqual(result["manualSources"], 1)
        self.assertEqual(result["itemRecords"][item["id"]]["verdict"], "unverified")

    def test_explicit_custom_source_and_legacy_override_preserve_exact_routes(self):
        for flag in ("hasCustomURL=true\n", ""):
            (self.path / "meta.ini").write_text("[General]\n" + flag +
                "url=https://www.loverslab.com/index.php?/topic/123-mod/&id=4&token=secret\n")
            item = self.snapshot(77, "Requires https://github.com/dependency/project")
            self.assertEqual(item["source"]["url"], "https://www.loverslab.com/index.php?/topic/123-mod/&id=4")
            self.assertEqual(item["source"]["type"], "loverslab")
            self.assertTrue(item["source"]["verified"])

    def test_bom_metadata_and_notes_field_keep_identity_conservative(self):
        (self.path / "meta.ini").write_text("\ufeff[General]\nhasCustomURL=0\nurl=https://github.com/stale/source\n"
                                           "notes=Requires https://github.com/dependency/project\n", encoding="utf-8")
        source = discover_local_source(self.path)
        self.assertEqual(source["url"], "https://github.com/dependency/project")
        self.assertFalse(source["verified"])
        self.assertEqual(source["kind"], "advisory")


if __name__ == "__main__":
    unittest.main()
