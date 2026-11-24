"""Signed Inspector replies explain proven MO2/Nexus project conflicts."""
from __future__ import annotations

import http.client
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from mo2_update_arsenal.bridge import ArsenalService
from mo2_update_arsenal.mod_health import nexus_page_url
from mo2_update_arsenal.scanner import summary_from_metadata
from mo2_update_arsenal.snapshot_projection import SnapshotInput, _ModInput, project_snapshot
from test_mod_health import GAME, MID, NOW, observe


class InspectorSourceConflict(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.native_calls = []
        self.profile = "owned-source-fixture"
        self.service = ArsenalService("", self.path / "cache.json", self.native_calls.append, self.snapshot())
        self.service.start()
        self.addCleanup(self.service.stop)
        self.port = self.service._server.server_port

    def snapshot(self, *, url=None, nid=MID, game=GAME):
        ids = (game, "1.6.1170", "Disposable fixture", self.profile)
        # A stock-style local row with a manual MO2 URL override. Actual
        # capture projection sees the independent native ID and URL.
        base = _ModInput("Owned mod", "Owned mod", 0, False, nid, "1.0", "", True, "", "",
                         url or nexus_page_url(GAME, MID+1, "files"), "Nexus")
        snapshot = project_snapshot(SnapshotInput(*ids, (base,)))
        raw = snapshot["items"][0]
        self.row_id = raw["id"]
        raw["update"] = summary_from_metadata(raw, {"version": "2.0", "status": "published"}, NOW - 12)
        return snapshot

    def request(self, *, method="GET", token=True, profile=None, proper_origin=True, force=True):
        request = {"profileId": profile or self.profile, "itemId": self.row_id}
        headers = {"X-Arsenal-Token": self.service.token} if token else {}
        route = "/api/mod-health"
        if method == "GET":
            route += "?" + urlencode(request)
            body = None
        else:
            headers.update({"Origin": f"http://127.0.0.1:{self.port}" if proper_origin else "https://example.com",
                            "Content-Type": "application/json"})
            body = json.dumps({**request, "force": force})
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            with patch("mo2_update_arsenal.intelligence.time.time", return_value=NOW):
                connection.request(method, route, body, headers)
                response = connection.getresponse()
                return response.status, json.loads(response.read())
        finally:
            connection.close()

    def assert_source_conflict(self, linked):
        status, response = self.request()
        self.assertEqual(status, 200)
        self.assertTrue(response["ok"])
        self.assertEqual(response["profileId"], self.profile)
        health = response["health"]
        self.assertEqual(health["links"], {"project": linked})
        self.assertEqual(health["bugs"]["state"], "unknown")
        self.assertIsNone(health["bugs"]["displayedCount"])
        self.assertEqual(health["bugs"]["reasonCode"], "nexus-source-conflict")
        self.assertEqual(health["page"]["status"], "unknown")
        self.assertEqual([action["id"] for action in health["actions"]], ["review-nexus-source-conflict"])
        action = health["actions"][0]
        self.assertEqual(action["severity"], "warning")
        self.assertEqual(action["label"], "Nexus project identity conflicts with MO2")
        self.assertEqual(action["url"], linked)
        self.assertNotIn("https://", action["detail"])
        return health

    def test_existing_health_and_newer_version_are_not_assigned_to_other_linked_project(self):
        self.service.intelligence._evidence[f"{GAME}/{MID}"] = observe()
        self.service.cache.set(GAME, MID, {"checkedAt": NOW-10, "meta": {"status": "published"}})
        linked = nexus_page_url(GAME, MID+1, "files")
        health = self.assert_source_conflict(linked)
        # Opening the Inspector uses the existing non-forced browser POST,
        # not the GET used by other providers or the forced Refresh button.
        passive_status, passive = self.request(method="POST", force=False)
        self.assertEqual(passive_status, 200)
        self.assertTrue(passive["ok"])
        self.assertEqual(passive["health"], health)
        self.assertNotIn("job", passive["health"])
        status, response = self.request(method="POST")
        self.assertEqual(status, 200)
        self.assertFalse(response["ok"])
        self.assertIn("no unambiguous Nexus project identity", response["error"])
        self.assertEqual(self.service.intelligence._health_jobs, {})
        self.assertEqual(self.native_calls, [])

    def test_cross_game_conflict_is_also_visible_for_the_same_numeric_id(self):
        linked = nexus_page_url("fallout4", MID, "files")
        self.service.set_snapshot(self.snapshot(url=linked))
        self.assert_source_conflict(linked)

    def test_corrected_link_recovers_existing_owned_evidence_and_update_advice(self):
        self.service.set_snapshot(self.snapshot(url=nexus_page_url(GAME, MID, "files")))
        self.service.intelligence._evidence[f"{GAME}/{MID}"] = observe()
        _, response = self.request()
        health = response["health"]
        self.assertEqual(health["bugs"]["displayedCount"], 1)
        action_ids = [action["id"] for action in health["actions"]]
        self.assertIn("review-update", action_ids)
        self.assertNotIn("review-nexus-source-conflict", action_ids)
        self.assertEqual(self.native_calls, [])

    def test_token_profile_and_csrf_still_protect_existing_routes(self):
        self.assertEqual(self.request(token=False)[0], 403)
        self.assertFalse(self.request(profile="other-profile")[1]["ok"])
        self.assertEqual(self.request(method="POST", proper_origin=False)[0], 403)
        self.assertEqual(self.native_calls, [])


if __name__ == "__main__":
    unittest.main()
