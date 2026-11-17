from __future__ import annotations
import copy
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mo2_update_arsenal.bridge import ArsenalService
from mo2_update_arsenal.model import call_str, take_snapshot
from mo2_update_arsenal.mo2_metadata import native_color, qsettings_color, read_metadata
from test_core import Mod, Organizer
from test_plugin_autostart import plugin_type


def variant(red, green, blue, alpha=255):
    data = struct.pack(">IB5H", 67, 1, alpha * 257, red * 257, green * 257, blue * 257, 0)
    return "@Variant(" + "".join(f"\\x{value:02x}" for value in data) + ")"


class MetadataTests(unittest.TestCase):
    def test_qsettings_rgb_variant_keeps_alpha_and_rejects_unknown_or_truncated_types(self):
        self.assertEqual(qsettings_color(variant(32, 160, 240, 128)), "#20a0f080")
        self.assertEqual(qsettings_color('"' + variant(255, 0, 0, 0) + '"'), "#ff000000")
        for raw in (variant(1, 2, 3)[:-5] + ")", "@Variant(garbage)", "@Variant(\\x1000)", "red"):
            self.assertIsNone(qsettings_color(raw))

    def test_utf16_meta_preserves_real_installed_pairs_and_separator_color(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            text = ("[General]\ninstallationFile=D:\\Downloads\\Fishing Patch.7z\nignoredVersion=1.0.2\ncolor=" + variant(4, 8, 12) +
                    "\n[installedFiles]\n1\\modid=42\n1\\fileid=123\n2\\modid=42\n2\\fileid=124\nsize=2\n")
            (path / "meta.ini").write_text(text, encoding="utf-16")
            facts = read_metadata(path)
            self.assertEqual(facts["installedFiles"], [{"modId": 42, "fileId": 123}, {"modId": 42, "fileId": 124}])
            self.assertEqual(facts["installationFile"], "Fishing Patch.7z")
            self.assertEqual(facts["separatorColor"], "#04080cff")
            self.assertEqual(facts["ignoredVersion"], "1.0.2")

    def test_live_color_only_change_updates_content_hash_and_clearing_does_not_restore_stale_disk_color(self):
        with tempfile.TemporaryDirectory() as temporary:
            separator = Mod("Actual MO2 color", separator=True, path=temporary)
            rgba = [15, 40, 90, 200]
            valid = [True]
            separator.color = lambda: SimpleNamespace(isValid=lambda: valid[0], getRgb=lambda: tuple(rgba))
            mod = Mod("Fishing Integration Patch", nexus_id=42, ver="1.0.1")
            mod.installedFiles = lambda: {(42, 700)}
            mod.installationFile = lambda: r"D:\Downloads\Fishing Patch.7z"
            organizer = Organizer({"Color_separator": separator, "Patch": mod})
            first = take_snapshot(organizer)
            self.assertEqual(first["groups"][0]["separatorColor"], "#0f285ac8")
            self.assertEqual(first["items"][0]["installedFiles"], [{"modId": 42, "fileId": 700}])
            rgba[0] = 80
            second = take_snapshot(organizer)
            self.assertEqual(first["items"], second["items"])
            self.assertEqual(first["build"]["itemsHash"], second["build"]["itemsHash"])
            self.assertNotEqual(first["build"]["contentHash"], second["build"]["contentHash"])
            (Path(temporary) / "meta.ini").write_text("[General]\ncolor=" + variant(255, 0, 0), encoding="utf-8")
            valid[0] = False
            cleared = take_snapshot(organizer)
            self.assertIsNone(cleared["groups"][0]["separatorColor"])

    def test_color_watch_only_reads_known_separators_without_invalidating_scan_or_auditing_mod_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            separator = Mod("Custom", separator=True)
            rgba = [4, 8, 12, 255]
            separator.color = lambda: SimpleNamespace(isValid=lambda: True, getRgb=lambda: tuple(rgba))
            organizer = Organizer({"Custom_separator": separator, "Normal mod": Mod("Normal mod")})
            snapshot = take_snapshot(organizer)
            service = ArsenalService("", Path(temporary) / "cache.json", lambda request: self.fail("No native request"), snapshot)
            self.addCleanup(service.stop)
            timers = []
            plugin = plugin_type(timers)()
            plugin.server, plugin.org = service, organizer
            plugin._sync_separator_colors.__func__.__globals__.update(native_color=native_color, read_metadata=read_metadata, call_str=call_str, Path=Path)
            plugin._remember_separator_colors(snapshot)
            before, generation, scan = service.snapshot_response_bytes(), service._generation, service.scan_state()
            rgba[2] = 99
            with patch.object(service, "set_snapshot", side_effect=AssertionError("Full inventory refresh")), \
                 patch.object(service.intelligence, "invalidate", side_effect=AssertionError("Content audit")), \
                 patch.object(organizer.mods, "allModsByProfilePriority", side_effect=AssertionError("Full mod enumeration")):
                plugin._sync_separator_colors()
            self.assertEqual(service._generation, generation + 1)
            self.assertNotEqual(service.snapshot_response_bytes(), before)
            self.assertEqual(service.scan_state(), scan)
            self.assertEqual(service.snapshot()["groups"][0]["separatorColor"], "#040863ff")
            self.assertEqual(timers[-1][0], 2000)
            self.assertFalse(service.set_separator_colors("different-profile", {snapshot["groups"][0]["id"]: {"color": "#ffffffff", "source": "mo2-native"}}))
            plugin._closing = True
            timers.clear(); plugin._sync_separator_colors()
            self.assertEqual(timers, [])


if __name__ == "__main__":
    unittest.main()
