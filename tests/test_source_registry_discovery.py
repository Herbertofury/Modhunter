"""Source discovery preserves its strict identity and fallback boundaries."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from mo2_update_arsenal import source_registry as source


class SourceRegistryDiscoveryTests(TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.ini = self.root / "meta.ini"

    def discover(self, data, notes=""):
        self.ini.write_bytes(data)
        return source.discover_local_source(self.root, notes)

    def test_signal_free_metadata_needs_no_parser_but_external_notes_keep_advisory(self):
        with patch.object(source.configparser, "ConfigParser", side_effect=AssertionError("unused parse")):
            actual = self.discover(b"\xef\xbb\xbf[General]\r\nnotes=ordinary note\r\nurl=ignore\r\n",
                                   "An advisory HTTPS://example.com/info?token=secret&section=compat")
            self.assertEqual(actual, {
                "type": "website", "verified": False, "kind": "advisory",
                "label": "Unverified link from MO2 notes", "url": "https://example.com/info?section=compat",
                "origin": "MO2 notes",
            })
            self.assertEqual(self.discover(b"\xff\x00 syntactically broken"),
                             source.source_record(""))

    def test_false_switch_without_metadata_url_remains_observable(self):
        data = b"[DEFAULT]\r\nhAsCuStOmUrL = FALSE\r\n[GeNeRaL]\r\nnotes=plain\r\n"
        no_notes = self.discover(data)
        self.assertFalse(no_notes["customUrlEnabled"])
        self.assertEqual(no_notes["kind"], "unresolved")
        record = self.discover(data, "https://example.com/proof")
        self.assertFalse(record["customUrlEnabled"])
        self.assertEqual(record["kind"], "advisory")
        self.assertEqual(record["url"], "https://example.com/proof")

    def test_options_follow_sections_then_field_priority_and_default_rules(self):
        data = (b"[DEFAULT]\nsource=https://website.example/default\n[General]\n"
                b"url=https://github.com/owner/first\nurl=HTTPS://github.com/owner/repeat?sig=drop&preserve=1\n"
                b"updateurl=https://gitlab.com/alternate/project\nnotes=https://example.com/project-note\n"
                b"[Secondary]\nurl=https://example.com/another\n")
        record = self.discover(data, "https://external.example/notice")
        self.assertTrue(record["verified"])
        self.assertEqual(record["url"], "https://github.com/owner/repeat?preserve=1")
        self.assertEqual(record["origin"], "MO2 meta.ini")

    def test_metadata_notes_continuations_and_external_notes_precedence(self):
        rendered = b"[General]\nnotes = first\n    HTTPS://example.com/metadata\n"
        self.assertEqual(self.discover(rendered)["url"], "https://example.com/metadata")
        self.assertEqual(self.discover(rendered, "external https://example.com/override")["url"],
                         "https://example.com/override")
        split = b"[General]\nnotes=https:\n    //example.com/not-a-url\n"
        self.assertEqual(self.discover(split)["kind"], "unresolved")

    def test_corrupt_binary_and_size_guard_retain_previous_results(self):
        invalid = b"no section\nurl=https://metadata.example/rejected\n[General]\nnotes=\"irrelevant\""
        self.assertEqual(self.discover(invalid, "https://example.com/fallback")["url"],
                         "https://example.com/fallback")
        utf16 = "[General]\nURL=https://example.com/utf16\n".encode("utf-16")
        self.assertEqual(self.discover(utf16)["kind"], "unresolved")
        binary = b"[General]\nnotes=\xff\x00\nurl=https://example.com/still-parseable\n"
        self.assertEqual(self.discover(binary)["url"], "https://example.com/still-parseable")
        oversized = b"[General]\nurl=https://example.com/too-large\n" + b" " * 65536
        self.assertEqual(self.discover(oversized)["kind"], "unresolved")

    def test_unsupported_url_shorthands_do_not_become_sources(self):
        data = (b"[General]\nurl=github.com/owner/repo\n"
                b"customurl=//github.com/owner/repo\nmodurl=https:github.com/owner/repo\n")
        with patch.object(source.configparser, "ConfigParser", side_effect=AssertionError("unused parse")):
            self.assertEqual(self.discover(data)["kind"], "unresolved")
        self.assertEqual(self.discover(b"[General]\nurl=https://127.1/private\n")["kind"], "unresolved")
        self.assertEqual(self.discover(b"[General]\nurl=https://example.com/\x1fhidden\n")["kind"],
                         "unresolved")

    def test_signal_in_comment_is_conservative_and_falls_through(self):
        with patch.object(source.configparser, "ConfigParser", wraps=source.configparser.ConfigParser) as parser:
            record = self.discover(b"[General]\n; manual: https://example.com/comment\nnotes=ordinary\n")
        parser.assert_called_once_with(interpolation=None, strict=False)
        self.assertEqual(record["kind"], "unresolved")

    def test_forced_cache_retries_plain_to_linked_change_and_refuses_second_race(self):
        from mo2_update_arsenal.refresh_work import RefreshMetadataCache, UnstableSnapshotMetadata

        self.ini.write_bytes(b"[General]\nnotes=ordinary\n")
        actual_read = Path.read_text
        reads = []

        def change_after_read(path, *args, **kwargs):
            data = actual_read(path, *args, **kwargs)
            if path == self.ini:
                reads.append(data)
                if len(reads) == 1:
                    path.write_bytes(b"[General]\nurl=https://example.com/replaced\n")
            return data

        cache = RefreshMetadataCache()
        with patch.object(Path, "read_text", change_after_read):
            _, record = cache.inspect(self.root, "", force=True)
        self.assertEqual(record["url"], "https://example.com/replaced")
        self.assertEqual(len(reads), 2)

        def twice_changing(path, *args, **kwargs):
            data = actual_read(path, *args, **kwargs)
            if path == self.ini:
                path.write_text(data + "# changed\n", encoding="utf-8")
            return data

        with patch.object(Path, "read_text", twice_changing):
            with self.assertRaises(UnstableSnapshotMetadata):
                cache.inspect(self.root, "", force=True)
        self.assertFalse(cache._entries)
