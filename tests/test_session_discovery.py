"""Atomic local discovery hints, without credentials or native-process probing."""
from __future__ import annotations

import copy
import http.client
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mo2_update_arsenal.bridge import ArsenalService
from mo2_update_arsenal.session_discovery import MAX_DESCRIPTOR_BYTES, SessionDiscovery, default_session_directory


PROFILE = {"profileId": "first-profile", "profileName": "Nord · 日本語", "game": "skyrimspecialedition",
           "items": [], "groups": []}


class SessionDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / "sessions"

    def service(self, folder=None):
        service = ArsenalService("<html><!--__MO2_BOOT__--></html>", self.root / "cache.json",
                                 lambda request: request.finish({"ok": True}), copy.deepcopy(PROFILE),
                                 discovery_directory=folder or self.folder)
        self.addCleanup(service.stop)
        return service

    def test_default_is_windows_localappdata_only(self):
        with patch("mo2_update_arsenal.session_discovery.sys.platform", "linux"):
            self.assertIsNone(default_session_directory())
        with patch("mo2_update_arsenal.session_discovery.sys.platform", "win32"), \
             patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}):
            self.assertEqual(default_session_directory(), self.root / "Modhunter" / "mo2-sessions")

    def test_live_descriptor_is_small_exact_schema_and_contains_no_credentials(self):
        service = self.service()
        service.set_key("do-not-write-nexus-credentials")
        address = service.start()
        port = service._server.server_port
        path = self.folder / f"{os.getpid()}-{port}.json"
        encoded = path.read_bytes()
        self.assertLessEqual(len(encoded), MAX_DESCRIPTOR_BYTES)
        self.assertNotIn(service.token.encode(), encoded)
        self.assertNotIn(b"do-not-write-nexus-credentials", encoded)
        self.assertEqual(json.loads(encoded), {"app": "Modhunter", "protocol": 1, "endpoint": address,
            "profileId": "first-profile", "profileName": "Nord · 日本語", "pid": os.getpid()})
        self.assertEqual(list(self.folder.glob("*.tmp-*")), [])

    def test_profile_switch_updates_descriptor_and_stop_removes_only_owned_file(self):
        service = self.service()
        service.start()
        own = next(self.folder.glob("*.json"))
        other = self.folder / "another-process.json"
        other.write_text('{"pid":9999}', encoding="utf-8")
        changed = copy.deepcopy(PROFILE)
        changed.update(profileId="second-profile", profileName="Second")
        service.set_snapshot(changed)
        self.assertEqual(json.loads(own.read_bytes())["profileId"], "second-profile")
        self.assertEqual(json.loads(own.read_bytes())["profileName"], "Second")
        service.stop()
        self.assertFalse(own.exists())
        self.assertTrue(other.exists())
        service._publish_discovery()
        self.assertFalse(own.exists(), "A stopped service must never republish its old endpoint")

    def test_repeated_metadata_refresh_does_not_rewrite_same_descriptor(self):
        service = self.service()
        service.start()
        with patch("mo2_update_arsenal.session_discovery.os.replace", side_effect=AssertionError("Unexpected rewrite")):
            service.set_snapshot(copy.deepcopy(PROFILE))
            service.start()

    def test_refresh_restores_missing_descriptor_without_restarting_live_service(self):
        service = self.service()
        address = service.start()
        server = service._server
        own = next(self.folder.glob("*.json"))
        original = own.read_bytes()
        own.unlink()
        service.set_snapshot(copy.deepcopy(PROFILE))
        self.assertTrue(own.exists())
        self.assertEqual(own.read_bytes(), original)
        self.assertIs(service._server, server)
        self.assertEqual(json.loads(own.read_bytes())["endpoint"], address)
        self.assertNotIn(service.token.encode(), own.read_bytes())

    def test_missing_descriptor_write_failure_retries_on_next_identical_publish(self):
        discovery = SessionDiscovery(self.folder)
        self.addCleanup(discovery.close)
        endpoint = "http://127.0.0.1:51234/"
        self.assertTrue(discovery.publish(endpoint, "first", "First"))
        own = next(self.folder.glob("*.json"))
        original = own.read_bytes()
        own.unlink()
        with patch("mo2_update_arsenal.session_discovery.os.replace", side_effect=OSError("fixture unavailable")), \
             self.assertLogs("mo2_update_arsenal.session_discovery", level="WARNING"):
            self.assertFalse(discovery.publish(endpoint, "first", "First"))
        self.assertFalse(own.exists())
        self.assertEqual(list(self.folder.glob("*.tmp-*")), [])
        self.assertTrue(discovery.publish(endpoint, "first", "First"))
        self.assertEqual(own.read_bytes(), original)

    def test_restarting_service_publishes_current_endpoint_without_leaving_old_hint(self):
        service = self.service()
        service.start()
        first_path = next(self.folder.glob("*.json"))
        service.stop()
        self.assertFalse(first_path.exists())
        current = service.start()
        descriptors = list(self.folder.glob("*.json"))
        self.assertEqual(len(descriptors), 1)
        self.assertEqual(json.loads(descriptors[0].read_bytes())["endpoint"], current)

    def test_cleanup_does_not_remove_replaced_descriptor(self):
        service = self.service()
        service.start()
        own = next(self.folder.glob("*.json"))
        replaced = b'{"app":"Different owner"}'
        own.write_bytes(replaced)
        service.stop()
        self.assertEqual(own.read_bytes(), replaced)

    def test_write_failure_does_not_block_live_http_service(self):
        invalid_directory = self.root / "regular-file"
        invalid_directory.write_text("not a directory")
        service = self.service(invalid_directory)
        with self.assertLogs("mo2_update_arsenal.session_discovery", level="WARNING") as messages:
            service.start()
        self.assertIn("could not be written", messages.output[0])
        client = http.client.HTTPConnection("127.0.0.1", service._server.server_port, timeout=2)
        client.request("GET", "/api/health")
        response = client.getresponse()
        self.assertEqual(response.status, 200)
        self.assertTrue(json.loads(response.read())["live"])
        client.close()

    def test_failed_atomic_replace_keeps_old_valid_json_and_cleans_temporary(self):
        discovery = SessionDiscovery(self.folder)
        self.addCleanup(discovery.close)
        self.assertTrue(discovery.publish("http://127.0.0.1:51234/", "first", "First"))
        path = next(self.folder.glob("*.json"))
        original = path.read_bytes()
        with patch("mo2_update_arsenal.session_discovery.os.replace", side_effect=PermissionError("fixture denied")), \
             self.assertLogs("mo2_update_arsenal.session_discovery", level="WARNING"):
            self.assertFalse(discovery.publish("http://127.0.0.1:51234/", "second", "Second"))
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(self.folder.glob("*.tmp-*")), [])

    def test_invalid_or_oversized_descriptor_never_written(self):
        discovery = SessionDiscovery(self.folder)
        for endpoint, profile in (("https://remote.example/", "p"), ("http://127.0.0.1:51234/?token=secret", "p"),
                                  ("http://127.0.0.1:51234/", "p" * 300)):
            with self.subTest(endpoint=endpoint), self.assertLogs("mo2_update_arsenal.session_discovery", level="WARNING"):
                self.assertFalse(discovery.publish(endpoint, profile, "Profile"))
        self.assertFalse(self.folder.exists())


if __name__ == "__main__":
    unittest.main()
