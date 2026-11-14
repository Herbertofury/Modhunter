from copy import deepcopy
import threading
from types import SimpleNamespace
import unittest

from mo2_update_arsenal.notification_facts import collect, facts


class NotificationFactsTests(unittest.TestCase):
    def setUp(self):
        self.context = {"profileId": "profile", "profileName": "Profile", "game": "skyrimspecialedition",
                        "gameRuntime": "1.6.1170", "executionEpoch": 3,
                        "nativeLease": "a" * 32, "nativeGeneration": 7}
        self.item = {"id": "live-exact", "name": "Private mod name", "installedVersion": "1.0",
                     "nexusId": 12, "source": {"type": "nexus", "verified": True,
                        "url": "https://www.nexusmods.com/skyrimspecialedition/mods/12"},
                     "update": {"verdict": "candidate", "source": "nexus-api", "latestVersion": "2.0",
                        "checkedAt": 900, "expiresAt": 1200, "freshness": "fresh"}}
        self.snapshot = {**self.context, "items": [self.item]}

    def result(self):
        return collect(self.snapshot, self.context, now=1000)

    def test_fresh_exact_fact_is_private_and_has_a_stable_proof(self):
        first = self.result()["facts"][0]
        self.assertNotIn("Private mod name", str(first))
        self.assertNotIn("sourceUrl", first)
        self.item["update"].update(checkedAt=950, expiresAt=1250)
        self.assertEqual(self.result()["facts"][0]["proofDigest"], first["proofDigest"])
        details = collect(self.snapshot, self.context, now=1000, detailed=True)["facts"][0]
        self.assertEqual(details["itemName"], "Private mod name")
        self.assertIn("nothing was downloaded", details["scope"])

    def test_failed_stale_ambiguous_local_and_incompatible_results_never_notify(self):
        for changes in ({"expiresAt": 999}, {"checkedAt": None}, {"expiresAt": None},
                        {"checkedAt": True}, {"checkedAt": 2000}, {"freshness": "stale"},
                        {"verdict": "unknown"}, {"source": "mo2-cached-metadata"},
                        {"latestVersion": "2beta"}, {"error": "sign in required"},
                        {"compatibility": {"status": "incompatible"}}):
            snapshot = deepcopy(self.snapshot)
            snapshot["items"][0]["update"].update(changes)
            self.assertEqual(collect(snapshot, self.context, now=1000)["facts"], [], changes)

    def test_changed_native_project_does_not_inherit_old_nexus_evidence(self):
        self.item["source"]["url"] = "https://www.nexusmods.com/skyrimspecialedition/mods/99"
        self.assertEqual(self.result()["facts"], [])

    def test_public_release_requires_exact_verified_available_source(self):
        self.item["source"] = {"url": "https://github.com/owner/repo", "verified": True}
        self.item["update"].update(source="github-release-api", provider="github", sourceUrl="https://github.com/owner/repo",
                                   outcome="release", availability="available", projectId="owner/repo", releaseId="release-2")
        self.assertEqual(len(self.result()["facts"]), 1)
        self.item["update"]["availability"] = "sign-in-required"
        self.assertEqual(self.result()["facts"], [])
        self.item["update"]["availability"] = "available"
        self.item["source"]["verified"] = False
        self.assertEqual(self.result()["facts"], [])

    def test_profile_runtime_and_duplicate_identity_invalidates_complete_result(self):
        for key, changed in (("profileId", "other"), ("gameRuntime", "other"), ("executionEpoch", 4)):
            snapshot = deepcopy(self.snapshot); snapshot[key] = changed
            with self.assertRaises(ValueError):
                collect(snapshot, self.context, now=1000)
        self.snapshot["items"].append(deepcopy(self.item))
        with self.assertRaises(ValueError):
            self.result()

    def test_native_service_generation_and_capture_context_must_be_complete(self):
        for changes in ({"nativeLease": ""}, {"nativeGeneration": True}, {"nativeGeneration": -1}):
            context = {**self.context, **changes}
            with self.assertRaises(ValueError):
                collect(self.snapshot, context, now=1000)

    def test_native_callback_during_fact_capture_rejects_all_results_and_releases_context(self):
        content = SimpleNamespace(_lock=threading.RLock(), _contexts={"captured": deepcopy(self.context)})
        content.capture = lambda *args, **kwargs: {"ok": True, "contextId": "captured"}
        calls = 0
        def context(_):
            nonlocal calls
            calls += 1
            value = deepcopy(self.context)
            if calls > 1:
                value["nativeGeneration"] += 1
            return value
        content._context = context
        service = SimpleNamespace(content=content, _guard=threading.RLock(), _generation=1, _stopped=False,
                                  snapshot=lambda: self.snapshot)
        result = facts(service)
        self.assertFalse(result["ok"])
        self.assertEqual(result["facts"], [])
        self.assertEqual(content._contexts, {})


if __name__ == "__main__":
    unittest.main()
