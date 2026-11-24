"""Published advisory provenance, package scope and end-to-end inspector coverage."""

from pathlib import Path
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from mo2_update_arsenal.bridge import ArsenalService
from mo2_update_arsenal.github_health import (
    GitHubAdvisories,
    npm_in_range,
    package_manifest,
)
from mo2_update_arsenal.source_registry import SourceScanner


REPO = "fixture/arc"
SOURCE = {
    "url": "https://github.com/fixture/arc",
    "type": "github",
    "verified": True,
    "kind": "project",
}
ID = "GHSA-aaaa-bbbb-cccc"
NOTICE = {
    "ghsa_id": ID,
    "state": "published",
    "published_at": "2026-01-01T00:00:00Z",
    "html_url": f"https://github.com/{REPO}/security/advisories/{ID}",
    "summary": "Fixture advisory",
    "severity": "high",
    "vulnerabilities": [
        {
            "package": {"ecosystem": "npm", "name": "@fixture/arc"},
            "vulnerable_version_range": ">= 1.0.0, < 2.0.0",
            "patched_versions": "2.0.0",
        }
    ],
}


class AdvisoryChecks(unittest.TestCase):
    @patch("mo2_update_arsenal.github_health.sqlite3", None)
    def test_embedded_cache_without_sqlite_preserves_evidence_and_provider_backoff(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "advisories.sqlite"
            path.write_bytes(b"existing cache from another runtime")
            cancel = threading.Event()
            observed = GitHubAdvisories(path, lambda _: [NOTICE]).observe(REPO, cancel)
            self.assertEqual(observed["findings"][0]["id"], ID)
            restored = GitHubAdvisories(path, lambda _: self.fail("unexpected fetch"))
            self.assertEqual(restored.observe(REPO, cancel), observed)

            def limited(_):
                raise HTTPError("https://api.github.com", 429, "", {"Retry-After": "60"}, None)

            backoff = GitHubAdvisories(path, limited).observe("second/project", cancel)
            self.assertEqual(backoff["state"], "rate-limited")
            self.assertEqual(restored.observe("third/project", cancel)["retryAt"], backoff["retryAt"])
            self.assertEqual(path.read_bytes(), b"existing cache from another runtime")

    def test_untested_ranges_are_explicitly_unknown(self):
        for version, spec, expected in [
            ("1.2.0", ">= 1.0.0, < 2.0.0", True),
            ("2.0.0", "<2.0.0", False),
            ("1.2.0-beta.1", "<2.0.0", None),
            ("1.2.0", "1.*", None),
            ("1.2.0", "1.0.0 || 1.2.0", None),
        ]:
            self.assertIs(npm_in_range(version, spec), expected)

    def test_real_source_scanner_applies_observation_per_row_and_inspector_preserves_evidence(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "package.json").write_text(
                json.dumps({"name": "@fixture/arc", "version": "1.2.3"})
            )
            evidence = package_manifest(root, SOURCE)
            self.assertEqual(evidence["installedPackage"]["name"], "@fixture/arc")
            items = [
                {
                    "id": i,
                    "name": i,
                    "installedVersion": "9.9.9",
                    "source": SOURCE,
                    **fields,
                }
                for i, fields in [
                    ("affected", evidence),
                    ("unknown", {}),
                    (
                        "patched",
                        {
                            "installedPackage": {
                                **evidence["installedPackage"],
                                "version": "2.0.0",
                            }
                        },
                    ),
                ]
            ]
            profile = {"items": items, "profileId": "A", "game": "skyrimspecialedition"}
            urls = []

            def advisories(url):
                urls.append(url)
                return [
                    NOTICE,
                    dict(NOTICE, state="draft"),
                    dict(NOTICE, withdrawn_at="2026-02-01"),
                ]

            store = GitHubAdvisories(root / "advisories.sqlite", advisories)
            scanner = SourceScanner(
                fetch_json=lambda url: {
                    "tag_name": "v10.0.0",
                    "html_url": "https://github.com/fixture/arc/releases/tag/v10.0.0",
                },
                advisory_store=store,
            )
            res = scanner.scan(profile)
            self.assertEqual(len(urls), 1)
            for item in items:
                item["update"] = res["itemRecords"][item["id"]]
            service = ArsenalService(
                "",
                root / "cache.json",
                lambda _: self.fail("read-only advisory dispatched a mutation"),
                profile,
            )
            self.addCleanup(service.stop)
            for id, expected in [
                ("affected", "affected"),
                ("unknown", "unverified"),
                ("patched", "outside-range"),
            ]:
                health = service.intelligence.health_state(id, "A")["health"]
                evidence = health["securityAdvisories"]
                self.assertEqual(len(evidence["findings"]), 1)
                notice = evidence["findings"][0]
                self.assertEqual(
                    (notice["applicability"], notice["url"]),
                    (expected, NOTICE["html_url"]),
                )
                self.assertEqual(
                    bool(evidence["affectedCount"]), expected == "affected"
                )
            compact = service.snapshot()["items"][0]["modHealth"]["securityAdvisories"]
            self.assertEqual(compact["affectedCount"], 1)
            self.assertNotIn("findings", compact)
            restored = GitHubAdvisories(
                root / "advisories.sqlite",
                lambda _: self.fail("fresh cache made a new request"),
            )
            self.assertEqual(
                restored.observe(REPO, threading.Event())["state"], "checked"
            )

    def test_foreign_url_partial_pagination_and_rate_limit_are_not_zero_advisories(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            def wrong(_):
                return [
                    dict(
                        NOTICE,
                        html_url="https://github.com/another/project/security/advisories/"
                        + ID,
                    )
                ]

            self.assertEqual(
                GitHubAdvisories(root / "a", wrong).observe(REPO, threading.Event())[
                    "state"
                ],
                "unavailable",
            )
            count = []

            def rate(_):
                count.append(1)
                if len(count) == 1:
                    return [NOTICE] * 100
                raise HTTPError(
                    "https://api.github.com",
                    429,
                    "Too many",
                    {"Retry-After": "30"},
                    None,
                )

            store = GitHubAdvisories(root / "b", rate)
            self.assertEqual(
                store.observe(REPO, threading.Event())["state"], "rate-limited"
            )
            self.assertEqual(
                store.observe("other/repo", threading.Event())["state"], "rate-limited"
            )
            self.assertEqual(len(count), 2)
