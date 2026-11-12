"""Real HTTP checks for the Update Workshop's live MO2 bridge and queue policy."""
from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from mo2_update_arsenal.bridge import ArsenalService


PROFILE = {"profileId": "profile-a", "profileName": "Skyrim SE",
           "game": "skyrimspecialedition", "gameRuntime": "1.5.97",
           "items": [
               {"id": "live-one", "name": "Native DLL", "nexusId": 101,
                "installedVersion": "1.0", "flags": ["DLL"],
                "update": {"verdict": "candidate", "latestVersion": "2.0"}},
               {"id": "live-two", "name": "Second Row", "nexusId": 101,
                "installedVersion": "1.1", "flags": [],
                "update": {"verdict": "candidate", "latestVersion": "2.0"}},
               {"id": "live-manual", "name": "Manual", "nexusId": 0,
                "installedVersion": "1.0"},
           ], "groups": []}


class WorkshopHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.native_calls = []

        def native(req):
            self.native_calls.append((req.cmd, dict(req.payload)))
            req.finish({"ok": True, "downloadId": 400 + len(self.native_calls)})

        self.service = ArsenalService("<html><!--__MO2_BOOT__--></html>",
                                     Path(self.tmp.name) / "cache.json",
                                     native, json.loads(json.dumps(PROFILE)))
        self.service.fetch_files = lambda mid: {
            "ok": True, "modId": mid,
            "files": [{"fileId": n, "name": f"File {n}"} for n in (11, 12)]
        }
        self.addr = self.service.start()

    def tearDown(self):
        self.service.stop()
        self.tmp.cleanup()

    def request(self, path, body=None, token=True, origin=True):
        url = self.addr.rstrip("/") + path
        data = json.dumps(body).encode() if body is not None else None
        headers = {"X-Arsenal-Token": self.service.token if token else "wrong"}
        if data is not None:
            headers.update({"Content-Type": "application/json",
                            "Origin": self.addr.rstrip("/") if origin else "https://bad.example"})
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.load(resp)
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    @staticmethod
    def file(item="live-one", fid=11, version="1.0", risk=True):
        return {"itemId": item, "modId": 101, "fileId": fid,
                "installedVersion": version, "reviewed": True,
                "riskAcknowledged": risk}

    def packet(self, entries):
        return {"profileId": "profile-a", "confirm": "QUEUE_REVIEWED_DOWNLOADS",
                "entries": entries}

    def test_plan_returns_all_categories_but_cannot_be_read_without_token(self):
        status, plan = self.request("/api/update-plan")
        self.assertEqual(status, 200)
        self.assertEqual(plan["summary"]["total"], 3)
        self.assertEqual(plan["summary"]["manual"], 1)
        self.assertEqual(plan["summary"]["risky"], 1)
        self.assertEqual(self.request("/api/update-plan", token=False)[0], 403)

    def test_batch_queues_download_only_and_deduplicates_exact_files(self):
        status, result = self.request("/api/update-queue", self.packet([
            self.file(), self.file("live-two", 11, "1.1", False)]))
        self.assertEqual(status, 200)
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(result["queued"]), 1)
        self.assertEqual([cmd for cmd, _ in self.native_calls], ["download"])
        self.assertEqual(self.service.downloads()[401]["status"], "downloading")

    def test_wrong_origin_profile_and_missing_risk_ack_do_not_download(self):
        selection = self.packet([self.file(risk=False)])
        self.assertEqual(self.request("/api/update-queue", selection, origin=False)[0], 403)
        self.assertFalse(self.request("/api/update-queue", selection)[1]["ok"])
        selection["profileId"] = "other"
        self.assertFalse(self.request("/api/update-queue", selection)[1]["ok"])
        self.assertFalse(self.native_calls)

    def test_changed_profile_rejected_before_native_dispatch(self):
        previous = self.packet([self.file()])
        switched = json.loads(json.dumps(PROFILE))
        switched["profileId"] = "profile-b"
        self.service.set_snapshot(switched)
        status, result = self.request("/api/update-queue", previous)
        self.assertEqual(status, 200)
        self.assertFalse(result["ok"])
        self.assertFalse(self.native_calls)

    def test_invalid_second_file_rejects_whole_batch(self):
        pkt = self.packet([self.file(), self.file("live-two", 777, "1.1", False)])
        self.assertFalse(self.request("/api/update-queue", pkt)[1]["ok"])
        self.assertFalse(self.native_calls)


if __name__ == "__main__":
    unittest.main()
