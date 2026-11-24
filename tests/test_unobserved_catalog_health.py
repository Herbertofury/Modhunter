"""Preserve complete MO2 catalog health when no upstream observation exists."""
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from mo2_update_arsenal.bridge import ArsenalService
from test_catalog_delivery import catalog


class UnobservedCatalogHealthTests(TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(TemporaryDirectory())
        self.stack.enter_context(patch("mo2_update_arsenal.bridge.time.time", return_value=1_794_500_000.0))
        data = catalog(3)
        data["items"][1].update(nexusId=0, source={"url": "https://github.com/demo/mod", "verified": True, "kind": "project"})
        data["items"][2].update(nexusId=0, source={})
        self.service = ArsenalService("", Path(directory, "cache.json"), lambda _: None, data)
        self.service.intelligence.runner = SimpleNamespace(available=False)
        self.addCleanup(self.service.stop)

    def test_no_synthetic_observation_projection_or_cache_for_unobserved_rows(self):
        intelligence = self.service.intelligence
        with patch.object(intelligence, "_compact_health", side_effect=AssertionError("unobserved row used observed projection")):
            first = self.service.snapshot()["items"]
            second = self.service.snapshot()["items"]
        self.assertEqual(first, second)
        self.assertEqual(intelligence._summary_cache, {})
        for item, provider, bugs in zip(second, ("nexus", "github", "unknown"), ("unknown", "unsupported", "unknown")):
            self.assertEqual(item["modHealth"], {
                "schema": 1, "provider": provider, "checkedAt": None, "freshness": {"state": "unknown"},
                "page": {"status": "unknown"}, "bugs": {"state": bugs, "displayedCount": None},
                "detailsAvailable": True})
        first[0]["modHealth"]["bugs"]["state"] = "available"
        self.assertEqual(second[0]["modHealth"]["bugs"]["state"], "unknown")

    def test_unobserved_health_uses_only_the_current_job(self):
        jobs = self.service.intelligence._health_jobs
        key = "skyrimspecialedition/1"
        jobs[key] = {"state": "running", "detail": {"phase": "metadata"}}
        old = self.service.snapshot()["items"][0]["modHealth"]
        old["job"]["detail"]["phase"] = "caller mutation"
        self.assertEqual(jobs[key]["detail"], {"phase": "metadata"})
        del jobs[key]
        self.assertNotIn("job", self.service.snapshot()["items"][0]["modHealth"])

    def test_first_observation_and_detailed_view_still_use_existing_health_pipeline(self):
        intelligence = self.service.intelligence
        self.service.snapshot()
        self.service.cache.set("skyrimspecialedition", 1, {
            "meta": {"status": "published", "updated_timestamp": 1_794_499_900},
            "checkedAt": 1_794_499_970})
        with patch.object(intelligence, "_compact_health", wraps=intelligence._compact_health) as project:
            compact = self.service.snapshot()["items"][0]["modHealth"]
        self.assertGreater(project.call_count, 0)
        self.assertEqual(compact["checkedAt"], 1_794_499_970)
        self.assertIn("live-0", intelligence._summary_cache)
        detailed = self.service.selection_snapshot(["live-0"])["items"][0]["modHealth"]
        self.assertEqual(detailed["checkedAt"], compact["checkedAt"])
        self.assertNotIn("detailsAvailable", detailed)
