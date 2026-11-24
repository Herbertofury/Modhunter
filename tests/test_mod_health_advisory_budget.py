"""Actual read-only inspector keeps its established advice and raw native evidence."""
from contextlib import contextmanager
from copy import deepcopy
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mo2_update_arsenal import mod_health
from mo2_update_arsenal.bridge import ArsenalService


ID = "live-profile-advice"
PROFILE = "profile-advice"


def fixture_analysis(plugin_count, issues, *, all_valid=False):
    states = ("missing", "inactive", "loads-after", "unknown-test" if not all_valid else "missing")
    return {
        "status": "native-headers", "depth": "native",
        "inventoryComplete": True,
        "plugins": [{
            "name": f"Español plugin {p}.esp", "winner": True, "active": True, "loadOrder": p + 1,
            "header": {"masters": [f"Master {p}-{i}.esm" for i in range(issues)]},
            "masterIssues": [{"name": f"Master {p}-{i}.esm", "status": states[i % len(states)]}
                             for i in range(issues)],
            "eligibility": {"status": "flag-only-candidate" if p % 7 == 0 else "not-candidate"},
            "provenance": ({"state": "winner-changed", "tool": "ESLifier",
                            "originalSha256": "e" * 64, "outputSha256": "f" * 64}
                           if p % 11 == 0 else {"state": "unknown"}),
        } for p in range(plugin_count)],
    }


@contextmanager
def service_with_analysis(analysis):
    service_snapshot = {"profileId": PROFILE, "profileName": "Compatibility advice fixture",
                        "game": "skyrimspecialedition", "gameRuntime": "1.6.1170",
                        "items": [{"id": ID, "name": "Example", "nexusId": 0,
                                   "localAnalysis": deepcopy(analysis)}]}
    with tempfile.TemporaryDirectory() as tmp:
        service = ArsenalService("<html/>", Path(tmp) / "cache.json",
                                 lambda req: (_ for _ in ()).throw(AssertionError(req)), service_snapshot)
        try:
            service.start()
            yield service
        finally:
            service.stop()


def fetch(service, profile=PROFILE):
    connection = HTTPConnection("127.0.0.1", service._server.server_port, timeout=10)
    try:
        path = "/api/mod-health?itemId=" + ID + "&profileId=" + profile
        connection.request("GET", path, headers={"X-Arsenal-Token": service.token})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


class AdvisoryBudgetTest(unittest.TestCase):
    def test_native_inspector_keeps_every_raw_issue_and_same_response_budget(self):
        fixture = fixture_analysis(8, 64, all_valid=True)
        with service_with_analysis(fixture) as service:
            with patch.object(mod_health, "_master_issue_detail", wraps=mod_health._master_issue_detail) as compose:
                status, raw = fetch(service)
            self.assertEqual(status, 200)
            payload = json.loads(raw)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["localAnalysis"]["plugins"], fixture["plugins"])
            advice = [a for a in payload["health"]["actions"] if a["action"] == "review-plugins"]
            self.assertEqual(len(advice), 256)
            self.assertEqual(compose.call_count, sum(a["id"].startswith("master-") for a in advice))
            self.assertEqual(compose.call_args_list[2].args, ("Español plugin 0.esp", "Master 0-2.esm", "loads-after"))
            self.assertTrue(any("loads before its declared master" in a["detail"] for a in advice))
            self.assertTrue(any("installed but inactive" in a["detail"] for a in advice))
            self.assertTrue(any("complete scanned plugin inventory" in a["detail"] for a in advice))
            self.assertTrue(any(a["action"] == "review-plugins" and "ESLifier" in a["evidence"] for a in advice))
            rejected = json.loads(fetch(service, profile="stale-profile")[1])
            self.assertFalse(rejected["ok"])
            self.assertNotIn("health", rejected)

    def test_evidence_after_public_advice_limit_is_still_read(self):
        fixture = fixture_analysis(8, 64, all_valid=True)
        fixture["plugins"][-1]["masterIssues"][-1]["status"] = ["invalid-shape"]
        # Existing malformed-input behavior remains after the current display
        # limit. An optimization which breaks out of the loop would hide it.
        with self.assertRaises(TypeError):
            mod_health._local_actions({}, fixture)


if __name__ == "__main__":
    unittest.main()
