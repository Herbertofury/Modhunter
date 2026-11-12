"""Bounded-work and stale-result regressions for large live MO2 profiles."""
from __future__ import annotations

import copy
import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from mo2_update_arsenal.bridge import ArsenalService
from mo2_update_arsenal.model import LocalMetadataCache, take_snapshot


def catalog(count=12000):
    return {"profileId": "large-profile", "profileName": "Large", "game": "skyrimspecialedition",
            "gameRuntime": "1.5.97", "groups": [], "items": [
                {"id": f"live-{i}", "name": f"Mod {i}", "mo2InternalName": f"Mod {i}",
                 "nexusId": i + 1, "installedVersion": "1.0", "flags": [],
                 "source": {"type": "nexus", "url": f"https://www.nexusmods.com/skyrimspecialedition/mods/{i + 1}"},
                 "update": {"verdict": "candidate", "latestVersion": "2.0"}}
                for i in range(count)]}


class LargeProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = ArsenalService("<!doctype html><!--__MO2_BOOT__-->" + "x" * 2_000_000,
                                      Path(self.temp.name) / "cache.json",
                                      lambda request: request.finish({"ok": True}), catalog())
        self.addCleanup(self.service.stop)

    def http(self, path, *, host=None, token=True):
        if self.service._server is None:
            self.service.start()
        port = self.service._server.server_port
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        headers = {"Host": host or f"127.0.0.1:{port}"}
        if token:
            headers["X-Arsenal-Token"] = self.service.token
        conn.request("GET", path, headers=headers)
        response = conn.getresponse()
        code, body = response.status, response.read()
        conn.close()
        return code, body

    def test_health_is_small_unauthenticated_identity_without_secrets(self):
        self.service.set_key("private-nexus-key")
        code, body = self.http("/api/health", token=False)
        self.assertEqual(code, 200)
        self.assertLess(len(body), 512)
        self.assertNotIn(self.service.token.encode(), body)
        self.assertNotIn(b"private-nexus-key", body)
        self.assertEqual(json.loads(body), {"ok": True, "app": "Modhunter", "protocol": 1,
            "version": "0.9.0", "live": True, "profileId": "large-profile", "generation": 1})
        self.assertEqual(self.http("/api/health", host="evil.example", token=False)[0], 403)
        self.assertEqual(self.http("/api/snapshot", token=False)[0], 403)

    def test_profile_only_polling_never_copies_catalog(self):
        with patch.object(self.service, "snapshot", side_effect=AssertionError("Unexpected full-catalog copy")):
            for endpoint in ("health", "revision", "game", "progress", "realm", "realm/world",
                             "siege/state", "siege/bounties", "scan", "downloads", "quick-state"):
                code, body = self.http("/api/" + endpoint)
                self.assertEqual(code, 200, endpoint)
                self.assertEqual(json.loads(body)["profileId"], "large-profile", endpoint)

    def test_compact_game_omits_row_progress_and_preserves_every_reward_field(self):
        self.service.chronicle.set_progress("large-profile", {"live-20": {"note": "x" * 8000}}, {}, "", {"live-20"})
        self.service.chronicle.record_update("large-profile", {"id": "live-20", "name": "Mod 20"},
                                             "1.0", "2.0", "version-observed")
        code, complete_body = self.http("/api/game")
        compact_code, compact_body = self.http("/api/game?compact=1")
        self.assertEqual((code, compact_code), (200, 200))
        complete, compact = json.loads(complete_body), json.loads(compact_body)
        self.assertIn("progress", complete)
        self.assertNotIn("progress", compact)
        self.assertEqual(compact, {key: value for key, value in complete.items() if key != "progress"})
        self.assertEqual(compact["updates"], 1)
        self.assertEqual(compact["profileId"], "large-profile")
        self.assertGreater(len(complete_body) - len(compact_body), 8000)

    def test_exact_row_queue_view_keeps_current_explicit_source_links(self):
        self.service.chronicle.set_progress("large-profile", {
            "live-20": {"url": "https://github.com/example/new-source", "note": "x" * 8000}}, {}, "", {"live-20"})
        with patch.object(self.service, "snapshot", side_effect=AssertionError("Full scan")), \
             patch.object(self.service.chronicle, "progress", side_effect=AssertionError("Full progress copy")):
            projected = self.service.selection_snapshot(["live-20"])
        self.assertEqual(len(projected["items"]), 1)
        item = projected["items"][0]
        self.assertEqual(item["source"]["url"], "https://github.com/example/new-source")
        self.assertNotIn("update", item, "Old source verdict must not follow an edited source")
        item["name"] = "Changed caller copy"
        self.assertEqual(self.service.selection_snapshot(["live-20"])["items"][0]["name"], "Mod 20")

    def test_cached_file_lookup_and_exact_focus_do_not_scan_all_mods(self):
        self.service._file_cache[("skyrimspecialedition", 21)] = {"ok": True, "files": [{"fileId": 8}]}
        with patch.object(self.service, "snapshot", side_effect=AssertionError("Full scan")):
            self.assertTrue(self.service.fetch_files(21)["ok"])
            self.assertTrue(self.service.focus_mod("live-20", "large-profile")["ok"])
        changed = catalog(1)
        changed["profileId"] = "replacement"
        self.service.set_snapshot(changed)
        self.assertFalse(self.service.fetch_files(21)["ok"])
        self.assertFalse(self.service.focus_mod("live-20", "replacement")["ok"])

    def test_scan_rejects_stale_profile_before_dispatch(self):
        with patch("mo2_update_arsenal.bridge.threading.Thread") as worker:
            result = self.service.begin_scan("local", "old-profile")
        self.assertFalse(result["ok"])
        worker.assert_not_called()


class NexusRequestSharingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = ArsenalService("<html/>", Path(self.temp.name) / "cache.json", lambda req: None, catalog(2))
        self.addCleanup(self.service.stop)
        self.service.native_nexus_available = True
        self.started, self.release = threading.Event(), threading.Event()
        self.addCleanup(self.release.set)
        self.calls, self.results, self.threads = [], [], []
        def remote(*args):
            self.calls.append(args)
            self.started.set()
            self.release.wait(3)
            return {"ok": True, "files": [{"fileId": 9, "version": "2.0"}], "modId": 1}
        self.service._fetch_files_remote = remote

    def spawn(self):
        thread = threading.Thread(target=lambda: self.results.append(self.service.fetch_files(1)))
        self.threads.append(thread)
        thread.start()

    def finish(self):
        self.release.set()
        for thread in self.threads:
            thread.join(3)
            self.assertFalse(thread.is_alive())

    def test_concurrent_same_project_reviews_share_one_provider_request(self):
        for _ in range(8):
            self.spawn()
        self.assertTrue(self.started.wait(1))
        self.finish()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.results), 8)
        self.assertTrue(all(value["ok"] for value in self.results))
        self.results[0]["files"][0]["version"] = "corrupted"
        self.assertEqual(self.service.fetch_files(1)["files"][0]["version"], "2.0")

    def test_profile_switch_during_provider_call_never_poisons_new_profile(self):
        self.spawn()
        self.assertTrue(self.started.wait(1))
        changed = catalog(2)
        changed["profileId"] = "switched"
        self.service.set_snapshot(changed)
        self.finish()
        self.assertFalse(self.results[0]["ok"])
        self.assertEqual(self.service._file_cache, {})
        self.assertEqual(self.service._file_inflight, {})
        self.assertTrue(self.service.fetch_files(1)["ok"])
        self.assertEqual(len(self.calls), 2)

    def test_new_audit_invalidates_provider_request_already_in_flight(self):
        self.spawn()
        self.assertTrue(self.started.wait(1))
        self.assertTrue(self.service.begin_scan("local", "large-profile")["ok"])
        self.finish()
        self.assertFalse(self.results[0]["ok"])
        self.assertEqual(self.service._file_cache, {})


class MetadataCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.mod = Path(self.temp.name) / "Mod"
        self.mod.mkdir()
        self.meta = self.mod / "meta.ini"
        self.meta.write_text("[General]\nurl=https://github.com/example/first\n", encoding="utf-8")
        self.cache = LocalMetadataCache()

    def test_repeated_refresh_skips_metadata_reads_but_returns_independent_values(self):
        flags, source = self.cache.inspect(self.mod, "")
        source["url"] = "mutated"
        with patch("mo2_update_arsenal.model.discover_local_source", side_effect=AssertionError("Unchanged meta.ini reread")), \
             patch("mo2_update_arsenal.model.inspect_local_flags", side_effect=AssertionError("Unchanged DLL directory reread")):
            flags, source = self.cache.inspect(self.mod, "")
        self.assertEqual(source["url"], "https://github.com/example/first")

    def test_file_replacement_and_dll_addition_invalidate_immediately(self):
        self.cache.inspect(self.mod, "")
        replacement = self.mod / "replacement.ini"
        replacement.write_text("[General]\nurl=https://github.com/example/next\n", encoding="utf-8")
        os.replace(replacement, self.meta)
        plugins = self.mod / "SKSE" / "Plugins"
        plugins.mkdir(parents=True)
        (plugins / "Native.dll").write_bytes(b"fixture")
        flags, source = self.cache.inspect(self.mod, "")
        self.assertEqual(flags, ["DLL"])
        self.assertEqual(source["url"], "https://github.com/example/next")
        (plugins / "Native.dll").unlink()
        self.assertEqual(self.cache.inspect(self.mod, "")[0], [])

    def test_notes_force_refresh_and_ttl_each_invalidate(self):
        from mo2_update_arsenal.model import discover_local_source
        self.cache.inspect(self.mod, "")
        with patch("mo2_update_arsenal.model.discover_local_source", wraps=discover_local_source) as inspect:
            self.cache.inspect(self.mod, "New notes")
            self.cache.inspect(self.mod, "New notes", force=True)
            self.cache.ttl = 0
            self.cache.inspect(self.mod, "New notes")
            self.assertEqual(inspect.call_count, 3)

    def test_removed_mods_do_not_accumulate_in_cache(self):
        self.cache.inspect(self.mod, "")
        self.cache.retain(set())
        self.assertEqual(self.cache._entries, {})


if __name__ == "__main__":
    unittest.main()
