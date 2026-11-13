"""GameSync-inspired strategies, constrained to complete provider version fields."""
import copy
import time
import unittest
from mo2_update_arsenal.version_evidence import compare_versions, parse_version
from mo2_update_arsenal.scanner import normalized_version, summary_from_metadata, version_verdict


class VersionEvidenceTests(unittest.TestCase):
    def test_numeric_compatibility(self):
        for a, b, expected in [('v1.2', '1.3', 'candidate'), ('1.2.0', '1.2', 'current'),
                               ('1.7', '1.5', 'newer-installed'), ('3.1-beta', '3.1', 'review'),
                               ('1', '', 'unknown'), ('', '2', 'review')]:
            with self.subTest(a=a, b=b):
                self.assertEqual(version_verdict(a, b), expected)
        self.assertEqual(normalized_version('v1.2'), (1, 2))
        self.assertIsNone(normalized_version('1' * 10000))

    def test_calendar_validation_and_mixed_families(self):
        for a, b, expected in [('2026-11-01', '2026.11.13', 'candidate'),
                               ('2024-02-29', '2024-03-01', 'candidate'),
                               ('2026-11-13', '2026-11-01', 'newer-installed'),
                               ('2026-11-13', '2026.11.13', 'current'),
                               ('2026-02-29', '2026-03-01', 'review'),
                               ('2026-01-01', '2026-13-01', 'review'),
                               ('11/01/2026', '12/01/2026', 'review'),
                               ('1.2.3', '2026-11-13', 'review'),
                               ('build 123', '124', 'review')]:
            with self.subTest(a=a, b=b):
                self.assertEqual(version_verdict(a, b), expected)
        self.assertEqual(compare_versions('2026-01-01', '2026-02-01')['strategy'], 'calendar-date')

    def test_explicit_builds(self):
        self.assertEqual(version_verdict('Build 99', 'build 100'), 'candidate')
        self.assertEqual(version_verdict('rev: 123', 'revision #123'), 'current')
        self.assertEqual(version_verdict('Build 100', 'build 99'), 'newer-installed')
        self.assertEqual(version_verdict('Build 100', 'Skyrim build 101'), 'review')

    def test_prerelease_numeric_order_and_channel_guard(self):
        for a, b, expected in [('1.2.3-beta.9', '1.2.3-beta.12', 'candidate'),
                               ('v2.5.3b9', 'v2.5.3b12', 'candidate'),
                               ('1.2.3-beta.12', '1.2.3-beta.9', 'newer-installed'),
                               ('1.2.3-beta.12', '1.2.3', 'candidate'),
                               ('1.2.3', '1.3.0-beta.1', 'review'),
                               ('1.2.3-alpha.1', '1.2.3-beta.1', 'review'),
                               ('1.2.3-beta.1', '1.2.3-rc.1', 'review'),
                               ('1.2.3-custom.1', '1.2.3-custom.2', 'review'),
                               ('1.2.3-custom.1', '1.2.3', 'review'),
                               ('1.2.3-beta.01', '1.2.3-beta.2', 'review'),
                               ('1.2.3-nightly.9', '1.2.3-nightly.10', 'candidate')]:
            with self.subTest(a=a, b=b):
                self.assertEqual(version_verdict(a, b), expected)

    def test_build_metadata_does_not_hide_different_files(self):
        self.assertEqual(version_verdict('1.2.3+abc', '1.2.3+def'), 'review')
        self.assertEqual(version_verdict('1.2.3+abc', '1.2.3+abc'), 'current')
        self.assertEqual(version_verdict('1.2.3+abc', '1.2.4+def'), 'candidate')

    def test_prose_prices_sizes_and_runtime_mentions_never_become_versions(self):
        for label in ['Skyrim SE 1.6.1170 / mod 2.0', '$2.99', '1920x1080', '2.5 MB',
                      'Updated November 13, 2026', 'requires 1.6.640', '1.2.3 / 1.2.4',
                      '1.2.3<script>', '1.2.3\x00', '9' * 10000, None, {}, 123,
                      '2026.11-13', '2026.11.31', '2026.11.13.1']:
            with self.subTest(label=str(label)[:60]):
                self.assertIsNone(parse_version(label))
                self.assertNotIn(compare_versions('1.0', label)['verdict'], ['candidate', 'current'])

    def test_repeated_extraction_is_bounded_not_a_non_global_regex_loop(self):
        start = time.monotonic()
        for _ in range(1000):
            self.assertEqual(version_verdict('1.2.3-beta.1', '1.2.3-beta.2'), 'candidate')
        self.assertLess(time.monotonic() - start, 3.0)

    def test_public_adapters_share_strategies_without_bypassing_freshness(self):
        from mo2_update_arsenal.source_adapters import normalize_adapter_record
        from mo2_update_arsenal.source_registry import release_verdict
        item = {'installedVersion': 'build 99', 'source': {'url': 'https://github.com/team/mod', 'verified': True}}
        raw = {'provider': 'github', 'sourceUrl': item['source']['url'], 'outcome': 'release',
               'freshness': 'fresh', 'checkedAt': 100, 'expiresAt': 200, 'latestVersion': 'build 100'}
        fresh = normalize_adapter_record(item, raw, now=110)
        self.assertEqual(fresh['verdict'], 'candidate')
        self.assertEqual(fresh['versionComparison']['strategy'], 'build')
        stale = normalize_adapter_record(item, raw, now=201)
        self.assertNotIn(stale['verdict'], ('candidate', 'current'))
        item['installedVersion'] = '1.2.3'
        raw['latestVersion'] = '1.3.0-beta.1'
        beta = normalize_adapter_record(item, raw, now=110)
        self.assertEqual(beta['verdict'], 'review')
        self.assertIn('channel', beta['reason'])
        legacy = release_verdict(item, {'tag_name': '1.3.0-beta.1'}, 'github', 'team/mod')
        self.assertEqual(legacy['verdict'], 'review')
        self.assertIn('channel', legacy['reason'])

    def test_provider_summary_explains_strategy_without_mutating_identity(self):
        item = {'id': 'installed-mod', 'nexusId': 42, 'installedVersion': 'Build 99'}
        original = copy.deepcopy(item)
        evidence = summary_from_metadata(item, {'version': 'build 100', 'status': 'published'}, 123)
        self.assertEqual(evidence['versionComparison']['strategy'], 'build')
        self.assertEqual(evidence['verdict'], 'candidate')
        self.assertIn('exact FILE', evidence['reason'])
        hidden = summary_from_metadata(item, {'status': 'hidden', 'version': 'build 999'}, 124)
        self.assertEqual(hidden['verdict'], 'review')
        self.assertEqual(item, original)
        self.assertEqual(summary_from_metadata(item, {}, 125)['verdict'], 'unknown')


if __name__ == '__main__':
    unittest.main()
