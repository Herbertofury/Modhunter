"""Real project artwork survives compact transport without changing authority."""
from __future__ import annotations

import unittest

from mo2_update_arsenal.native_nexus import NativeNexusGateway
from mo2_update_arsenal.scanner import summary_from_metadata
from mo2_update_arsenal.source_adapters import compact_source_update, normalize_adapter_record, preview_image_url
from test_site_adapters import fixture
from nexus_fixture import file_item, cached_files
from mo2_update_arsenal.nexus_files import FileLineage


class LibraryArtworkTests(unittest.TestCase):
    def test_nexus_native_metadata_and_summary_retain_only_public_artwork_and_author(self):
        url = "https://staticdelivery.nexusmods.com/mods/1704/images/123-456.png"
        data = {"version": "2.0", "status": "published", "mod_id": 123,
                "domain_name": "skyrimspecialedition", "picture_url": url,
                "author": "Actual creator", "description": "Not compact metadata",
                "api_key": "private", "uploaded_users_profile_url": "private"}
        metadata = NativeNexusGateway._metadata("skyrimspecialedition", 123, data)
        self.assertEqual(metadata["picture_url"], url)
        self.assertNotIn("api_key", metadata)
        self.assertNotIn("description", metadata)
        self.assertNotIn("uploaded_users_profile_url", metadata)
        summary = summary_from_metadata(file_item(123), metadata, 1000, FileLineage(cached_files(123), 'skyrimspecialedition', 123))
        self.assertEqual(summary["thumbnailUrl"], url)
        self.assertEqual(summary["author"]["name"], "Actual creator")
        self.assertEqual(summary["verdict"], "candidate")
        with self.assertRaises(ValueError):
            NativeNexusGateway._metadata("skyrimspecialedition", 124, data)

    def test_compact_public_record_prefers_project_media_and_preserves_exact_release(self):
        item, raw = fixture("modrinth")
        raw.update(iconUrl="https://cdn.modrinth.com/data/AbCdEfGh/icon.png",
                   media=[{"type": "image", "url": "https://cdn.modrinth.com/data/AbCdEfGh/gallery.png"}])
        normalized = normalize_adapter_record(item, raw)
        compact = compact_source_update(normalized)
        self.assertEqual(compact["thumbnailUrl"], raw["media"][0]["url"])
        self.assertEqual(compact["thumbnailKind"], "image")
        for key in ("sourceUrl", "projectId", "releaseId", "verdict"):
            self.assertEqual(compact[key], normalized[key])
        self.assertNotIn("media", compact)
        self.assertNotIn("releaseAssets", compact)
        self.assertEqual(compact["releaseAssetCount"], 1)
        raw["media"] = []
        compact = compact_source_update(normalize_adapter_record(item, raw))
        self.assertEqual(compact["thumbnailUrl"], raw["iconUrl"])
        self.assertEqual(compact["thumbnailKind"], "icon")

    def test_changed_project_never_reuses_an_old_thumbnail(self):
        item, raw = fixture("modrinth")
        raw["iconUrl"] = "https://cdn.modrinth.com/data/AbCdEfGh/icon.png"
        raw["sourceUrl"] = "https://modrinth.com/mod/a-different-project"
        compact = compact_source_update(normalize_adapter_record(item, raw))
        self.assertNotIn("thumbnailUrl", compact)
        self.assertEqual(compact["verdict"], "unknown")

    def test_missing_artwork_never_uses_publisher_portrait(self):
        item, raw = fixture("github")
        raw["publisher"]["avatarUrl"] = "https://avatars.githubusercontent.com/u/1"
        compact = compact_source_update(normalize_adapter_record(item, raw))
        self.assertNotIn("thumbnailUrl", compact)
        self.assertEqual(compact["publisher"]["name"], "Release Publisher")

    def test_previews_reject_local_signed_and_wrong_provider_destinations(self):
        for raw in ("javascript:alert(1)", "file:///private.jpg", "https://127.0.0.1/a.png",
                    "https://staticdelivery.nexusmods.com.evil.test/a.png",
                    "https://user:password@staticdelivery.nexusmods.com/a.png",
                    "https://staticdelivery.nexusmods.com/a.png?token=secret",
                    "https://cdn.modrinth.com/a.png", {}, None):
            with self.subTest(raw=raw):
                self.assertEqual(preview_image_url(raw, "nexus"), "")


if __name__ == "__main__":
    unittest.main()
