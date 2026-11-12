"""Mod health separates observed facts, provider limitations and stale advice."""
from __future__ import annotations

import concurrent.futures
from email.message import Message
import io
import http.client
import json
import threading
import unittest
import urllib.error
import urllib.request

from mo2_update_arsenal.mod_health import (
    MAX_PAGE_BYTES, NexusPageClient, _SameModRedirect, _RedirectBlocked,
    build_mod_health, freshness, nexus_identity, nexus_page_url, parse_nexus_page,
)


NOW = 1_800_000_000.0
GAME = "skyrimspecialedition"
MID = 145168
URL = nexus_page_url(GAME, MID)


def item(**changes):
    result = {"id": "installed-one", "name": "Test installed mod", "nexusId": MID,
              "installedVersion": "1.0", "enabled": True,
              "source": {"verified": True, "type": "nexus", "kind": "project", "url": URL}}
    result.update(changes)
    return result


def markup(*, count="1", tab=True, attrs="", notice="", title="Fixture at Skyrim Special Edition Nexus", mod_id=MID):
    # Minimal synthetic fixture retaining only selectors verified in the actual
    # public 145168 bugs page. Its badge is 1 despite two displayed report rows.
    badge = f'<span class="alert">{count}</span>' if count is not None else ""
    bugs = f'<li id="mod-page-tab-bugs" {attrs}><a href="/{GAME}/mods/{mod_id}?tab=bugs"><span class="tab-label">Bugs</span>{badge}</a></li>' if tab else ""
    return f'''<html><head><title>{title}</title><link rel="canonical" href="https://www.nexusmods.com/{GAME}/mods/{mod_id}"></head><body>
    <h1>Please log in</h1><noscript>JavaScript is required</noscript>
    <ul><li id="mod-page-tab-description"><a href="/{GAME}/mods/{mod_id}?tab=description">Description</a></li>{bugs}</ul>
    <div id="tab-modbugs"><h2 id="bugs_tab_h2" data-mod-id="{mod_id}">Bug reports</h2>{notice}
    <table><tr><td>Known issue</td></tr><tr><td>Not a bug</td></tr></table></div></body></html>'''


def observe(body=None, **kwargs):
    return parse_nexus_page(markup() if body is None else body, game=GAME, mod_id=MID, checked_at=NOW, **kwargs)


class FakeResponse(io.BytesIO):
    def __init__(self, body, *, status=200, url=URL, headers=None):
        super().__init__(body.encode() if isinstance(body, str) else body)
        self.status, self.url = status, url
        self.headers = {"Content-Type": "text/html; charset=utf-8", **(headers or {})}

    def geturl(self):
        return self.url


class FakeOpener:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def open(self, request, *, timeout):
        self.requests.append((request, timeout))
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response() if callable(self.response) else self.response


class PublicPageEvidenceTests(unittest.TestCase):
    def test_real_navigation_badge_is_not_open_total_or_resolved_count(self):
        evidence = observe()
        self.assertEqual(evidence["bugs"]["displayedCount"], 1)
        self.assertEqual(evidence["bugs"]["state"], "available")
        self.assertEqual(evidence["page"]["status"], "available")
        self.assertEqual(evidence["fetch"]["state"], "ok")
        for key in ("open", "total", "resolved"):
            self.assertIsNone(evidence["bugs"][key])

    def test_zero_is_an_observation_and_missing_badge_is_unknown(self):
        self.assertEqual(observe(markup(count="0"))["bugs"]["displayedCount"], 0)
        self.assertIsNone(observe(markup(count=None))["bugs"]["displayedCount"])
        self.assertEqual(observe(markup(count="1,234"))["bugs"]["displayedCount"], 1234)
        self.assertIsNone(observe(markup(count="1,23"))["bugs"]["displayedCount"])

    def test_missing_navigation_never_claims_disabled_or_author_removed(self):
        evidence = observe(markup(tab=False, notice="<p>The author discusses bug reports being disabled in other mods.</p>"))
        self.assertEqual(evidence["bugs"]["state"], "unknown")
        self.assertIsNone(evidence["bugs"]["attributedTo"])
        self.assertEqual(evidence["page"]["status"], "available")

    def test_explicit_semantic_disabled_and_hidden_are_distinct_from_locked(self):
        for attrs, expected in (("aria-disabled='true'", "disabled"), ("hidden", "hidden")):
            with self.subTest(attrs=attrs):
                bugs = observe(markup(attrs=attrs))["bugs"]
                self.assertEqual(bugs["state"], expected)
                self.assertIsNone(bugs["displayedCount"])
                self.assertIsNone(bugs["attributedTo"])
        locked = observe(markup(notice='<div role="alert">Bug reports are locked for this mod.</div>'))
        self.assertEqual(locked["bugs"]["state"], "locked")

    def test_unscoped_user_text_is_not_a_setting_and_conflicts_are_unknown(self):
        evidence = observe(markup(notice='<p>Bug reports are disabled for this mod.</p>'))
        self.assertEqual(evidence["bugs"]["state"], "available")
        contradictory = '<div role="alert">Bug reports are disabled.</div><div role="alert">Bug reports are locked.</div>'
        evidence = observe(markup(notice=contradictory))
        self.assertEqual(evidence["bugs"]["state"], "unknown")

    def test_other_mod_canonical_heading_or_redirect_is_rejected(self):
        self.assertEqual(observe(markup(mod_id=2))["fetch"]["state"], "identity-mismatch")
        wrong_heading = markup().replace(f'data-mod-id="{MID}"', 'data-mod-id="2"')
        self.assertEqual(observe(wrong_heading)["fetch"]["state"], "identity-mismatch")
        self.assertEqual(observe(url="https://www.nexusmods.com/fallout4/mods/145168")["fetch"]["state"], "identity-mismatch")

    def test_challenge_and_login_are_not_bug_tab_settings(self):
        for body, state in (("<title>Just a moment...</title><div id='cf-chl-widget'></div>", "challenge"),
                            ("<title>Log in - Nexus Mods</title>", "authentication-required")):
            with self.subTest(state=state):
                evidence = observe(body)
                self.assertEqual(evidence["fetch"]["state"], state)
                self.assertEqual(evidence["bugs"]["state"], "unavailable")
                self.assertIsNone(evidence["bugs"]["displayedCount"])
        # Actual pages contain generic login and JavaScript notices too.
        self.assertEqual(observe()["bugs"]["state"], "available")

    def test_bounded_markup_rejects_size_depth_and_ignores_script_badges(self):
        self.assertEqual(observe(b"x" * (MAX_PAGE_BYTES + 1))["fetch"]["state"], "response-too-large")
        self.assertEqual(observe("<div>" * 130)["fetch"]["state"], "unsupported-page")
        self.assertIsNone(observe("<script>" + markup() + "</script>")["bugs"]["displayedCount"])


class PageClientTests(unittest.TestCase):
    def make_client(self, response, **kwargs):
        opener = FakeOpener(response)
        client = NexusPageClient(opener_factory=lambda game, mid: opener, clock=lambda: NOW, **kwargs)
        return client, opener

    def test_fetches_public_html_and_caches_only_scalar_evidence(self):
        client, opener = self.make_client(lambda: FakeResponse(markup()))
        first = client.fetch(GAME, MID)
        first["bugs"]["displayedCount"] = 999
        second = client.fetch(GAME, MID)
        self.assertEqual(second["bugs"]["displayedCount"], 1)
        self.assertTrue(second["cache"]["hit"])
        self.assertEqual(len(opener.requests), 1)
        headers = {key.lower(): value for key, value in opener.requests[0][0].header_items()}
        self.assertFalse(set(headers) & {"cookie", "authorization", "apikey"})
        stored = json.dumps(client._cache[(GAME, MID)])
        self.assertNotIn("<html", stored)
        self.assertNotIn("Known issue", stored)
        self.assertLess(len(stored), 2500)

    def test_expired_evidence_refreshes_and_explicit_disable_records_transition(self):
        clock = [NOW]
        payload = [markup()]
        opener = FakeOpener(lambda: FakeResponse(payload[0]))
        client = NexusPageClient(opener_factory=lambda game, mid: opener, clock=lambda: clock[0], ttl_seconds=30)
        client.fetch(GAME, MID)
        clock[0] += 31
        payload[0] = markup(notice='<div role="alert">Bug reporting is disabled for this mod.</div>')
        evidence = client.fetch(GAME, MID)
        self.assertEqual(len(opener.requests), 2)
        self.assertTrue(evidence["bugs"]["changedSincePrevious"])
        self.assertEqual(evidence["bugs"]["previousState"], "available")
        self.assertIsNone(evidence["bugs"]["attributedTo"])

    def test_transient_failure_does_not_erase_last_known_tab_transition(self):
        clock = [NOW]
        payload = [markup()]
        opener = FakeOpener(lambda: FakeResponse(payload[0]))
        client = NexusPageClient(opener_factory=lambda game, mid: opener, clock=lambda: clock[0], ttl_seconds=30)
        client.fetch(GAME, MID)
        clock[0] += 31
        payload[0] = "<title>Just a moment...</title>"
        failed = client.fetch(GAME, MID, force=True)
        self.assertEqual(failed["bugs"]["state"], "unavailable")
        self.assertIsNone(failed["bugs"]["changedSincePrevious"])
        clock[0] += 61
        payload[0] = markup(notice='<div role="alert">Bug reports are disabled.</div>')
        observed = client.fetch(GAME, MID)
        self.assertTrue(observed["bugs"]["changedSincePrevious"])
        self.assertEqual(observed["bugs"]["previousState"], "available")

    def test_http_errors_timeout_and_content_failures_stay_unavailable(self):
        for code, state in ((401, "authentication-required"), (403, "forbidden"), (404, "not-found"), (429, "rate-limited"), (503, "unavailable")):
            with self.subTest(code=code):
                client, _ = self.make_client(urllib.error.HTTPError(URL, code, "error", Message(), io.BytesIO(b"")))
                result = client.fetch(GAME, MID)
                self.assertEqual(result["fetch"]["state"], state)
                self.assertEqual(result["bugs"]["state"], "unavailable")
                self.assertNotEqual(result["page"]["status"], "removed")
        client, _ = self.make_client(TimeoutError())
        self.assertEqual(client.fetch(GAME, MID)["fetch"]["state"], "timeout")
        client, _ = self.make_client(http.client.IncompleteRead(b"partial page"))
        self.assertEqual(client.fetch(GAME, MID)["fetch"]["state"], "unavailable")
        self.assertFalse(client._pending)
        client, _ = self.make_client(lambda: FakeResponse(markup(), headers={"Content-Length": str(MAX_PAGE_BYTES + 1)}))
        self.assertEqual(client.fetch(GAME, MID)["fetch"]["state"], "response-too-large")
        client, _ = self.make_client(lambda: FakeResponse(markup(), headers={"Content-Type": "application/json"}))
        self.assertEqual(client.fetch(GAME, MID)["fetch"]["state"], "unavailable")

    def test_rate_limit_cooldown_prevents_next_mod_and_force_retry(self):
        headers = Message()
        headers["Retry-After"] = "120"
        client, opener = self.make_client(urllib.error.HTTPError(URL, 429, "error", headers, io.BytesIO(b"")))
        client.fetch(GAME, MID)
        next_mod = client.fetch(GAME, MID + 1, force=True)
        self.assertEqual(next_mod["fetch"]["state"], "rate-limited")
        self.assertEqual(client.fetch(GAME, MID, force=True)["fetch"]["state"], "rate-limited")
        self.assertEqual(len(opener.requests), 1)

    def test_same_mod_redirect_policy_blocks_other_origin_game_mod_and_secrets(self):
        for target in ("http://www.nexusmods.com/skyrimspecialedition/mods/145168?tab=bugs",
                       "https://evil.example/skyrimspecialedition/mods/145168?tab=bugs",
                       "https://www.nexusmods.com/skyrimspecialedition/mods/2?tab=bugs",
                       "https://www.nexusmods.com/fallout4/mods/145168?tab=bugs",
                       URL + "&token=private", "https://www.nexusmods.com:443/skyrimspecialedition/mods/145168?tab=bugs"):
            with self.subTest(target=target):
                with self.assertRaises(_RedirectBlocked):
                    _SameModRedirect(GAME, MID).redirect_request(urllib.request.Request(URL), None, 302, "Found", {}, target)
        allowed = _SameModRedirect(GAME, MID).redirect_request(urllib.request.Request(URL), None, 302, "Found", {}, URL.replace("?", "/?"))
        self.assertEqual(allowed.full_url, URL.replace("?", "/?"))

    def test_simultaneous_calls_share_one_bounded_request(self):
        entered, release = threading.Event(), threading.Event()
        def blocking():
            entered.set()
            self.assertTrue(release.wait(2))
            return FakeResponse(markup())
        client, opener = self.make_client(blocking)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(client.fetch, GAME, MID)
            self.assertTrue(entered.wait(1))
            second = pool.submit(client.fetch, GAME, MID)
            release.set()
            self.assertEqual(first.result(2)["bugs"]["displayedCount"], 1)
            self.assertEqual(second.result(2)["bugs"]["displayedCount"], 1)
        self.assertEqual(len(opener.requests), 1)
        self.assertFalse(client._pending)

    def test_response_final_identity_and_lru_limit(self):
        client, _ = self.make_client(lambda: FakeResponse(markup(), url="https://evil.example/"))
        self.assertEqual(client.fetch(GAME, MID)["fetch"]["state"], "identity-mismatch")
        client = NexusPageClient(max_entries=1, opener_factory=lambda game, mid: FakeOpener(lambda: FakeResponse(markup(mod_id=mid), url=nexus_page_url(game, mid))), clock=lambda: NOW)
        client.fetch(GAME, MID)
        client.fetch(GAME, MID + 1)
        self.assertEqual(list(client._cache), [(GAME, MID + 1)])
        self.assertEqual(list(client._known_bugs), [(GAME, MID + 1)])


class HealthEvaluationTests(unittest.TestCase):
    def test_nexus_metadata_never_invents_bug_counts_even_with_unrelated_fields(self):
        health = build_mod_health(item(), game=GAME, now=NOW, metadata_checked_at=NOW,
                                  metadata={"status": "published", "updated_timestamp": NOW - 200, "bugReports": 0, "open_issues_count": 5})
        self.assertEqual(health["page"]["status"], "available")
        self.assertEqual(health["page"]["updatedTimestamp"], NOW - 200)
        self.assertEqual(health["bugs"]["state"], "unknown")
        self.assertIsNone(health["bugs"]["total"])
        self.assertIsNone(health["bugs"]["displayedCount"])
        self.assertEqual(health["freshness"]["state"], "partial")
        self.assertTrue(health["links"]["files"].endswith("?tab=files"))
        self.assertIn("tab=description", health["links"]["changelog"])

    def test_explicit_removed_hidden_and_missing_are_different(self):
        for status, expected in (("hidden", "hidden"), ("removed", "removed"), ("not_found", "not-found"), ("under_moderation", "under-moderation")):
            health = build_mod_health(item(), game=GAME, now=NOW, metadata_checked_at=NOW, metadata={"status": status})
            self.assertEqual(health["page"]["status"], expected)
            self.assertEqual(health["bugs"]["state"], "unknown")
        health = build_mod_health(item(), game=GAME, now=NOW, metadata_checked_at=NOW, metadata={"available": False})
        self.assertEqual(health["page"]["status"], "unavailable")

    def test_verified_external_source_overrides_legacy_nexus_id(self):
        external = item(source={"type": "github", "verified": True, "kind": "project", "url": "https://github.com/example/project"})
        self.assertIsNone(nexus_identity(external, GAME))
        health = build_mod_health(external, game=GAME, now=NOW, page_evidence=observe(), metadata={"status": "removed"})
        self.assertEqual(health["provider"], "github")
        self.assertEqual(health["bugs"]["state"], "unsupported")
        self.assertEqual(health["links"]["bugs"], "https://github.com/example/project/issues")
        self.assertEqual(health["page"]["status"], "unknown")

    def test_wrong_game_mod_or_future_evidence_is_not_applied(self):
        self.assertIsNone(nexus_identity(item(), "fallout4"))
        self.assertIsNone(nexus_identity(item(nexusId=2), GAME))
        evidence = observe()
        evidence["identity"]["modId"] = 2
        self.assertEqual(build_mod_health(item(), game=GAME, now=NOW, page_evidence=evidence)["bugs"]["state"], "unknown")
        evidence = observe()
        evidence["checkedAt"] = NOW + 1000
        self.assertEqual(build_mod_health(item(), game=GAME, now=NOW, page_evidence=evidence)["bugs"]["state"], "unknown")

    def test_recent_fetch_does_not_make_old_metadata_fresh(self):
        health = build_mod_health(item(), game=GAME, now=NOW, metadata_checked_at=NOW - 100_000,
                                  metadata={"status": "published", "updated_timestamp": NOW - 200_000}, page_evidence=observe())
        self.assertEqual(health["page"]["freshness"]["state"], "stale")
        self.assertEqual(health["bugs"]["freshness"]["state"], "fresh")
        self.assertEqual(health["freshness"]["state"], "stale")
        self.assertIn("refresh-health", [action["id"] for action in health["actions"]])
        self.assertEqual(freshness(NOW + 1000, NOW)["state"], "clock-skew")

    def test_new_provider_timestamp_signals_review_not_an_install_guarantee(self):
        previous = build_mod_health(item(), game=GAME, now=NOW - 100, metadata_checked_at=NOW - 100,
                                    metadata={"status": "published", "updated_timestamp": NOW - 1000})
        health = build_mod_health(item(), game=GAME, now=NOW, metadata_checked_at=NOW,
                                  metadata={"status": "published", "updated_timestamp": NOW - 200}, previous=previous)
        self.assertTrue(health["page"]["changedSincePrevious"])
        self.assertIn("review-page-change", [action["id"] for action in health["actions"]])
        self.assertNotIn("review-update", [action["id"] for action in health["actions"]])
        repeated = build_mod_health(item(), game=GAME, now=NOW + 1, metadata_checked_at=NOW,
                                    metadata={"status": "published", "updated_timestamp": NOW - 200}, previous=health)
        self.assertTrue(repeated["page"]["changedSincePrevious"])
        self.assertIn("review-page-change", [action["id"] for action in repeated["actions"]])

    def test_local_actions_use_explicit_master_and_hash_evidence(self):
        analysis = {"plugins": [{"name": "Patch.esp", "winner": True,
                                 "masterIssues": [{"name": "Parent.esm", "status": "missing"}],
                                 "eligibility": {"status": "flag-only-candidate"},
                                 "provenance": {"tool": "ESLifier", "state": "source-changed",
                                                "originalSha256": "a" * 64, "outputSha256": "b" * 64}}]}
        health = build_mod_health(item(), game=GAME, now=NOW, local_analysis=analysis)
        actions = {action["id"]: action for action in health["actions"]}
        self.assertIn("master-missing-Patch.esp-Parent.esm", actions)
        self.assertIn("esl-stale-Patch.esp", actions)
        self.assertIn("esl-candidate-Patch.esp", actions)
        analysis["plugins"][0]["provenance"]["state"] = "unknown"
        health = build_mod_health(item(installedVersion="2.0", flags=["ESL"]), game=GAME, now=NOW, local_analysis=analysis)
        self.assertFalse(any(action["id"].startswith("esl-stale") for action in health["actions"]))

    def test_rust_provenance_states_work_on_losing_original_and_require_real_records(self):
        for state in ("source-changed", "output-changed", "source-and-output-changed", "winner-changed"):
            with self.subTest(state=state):
                plugin = {"name": "Original.esp", "winner": False,
                          "provenance": {"state": state, "tool": "ESLifier", "originalSha256": "a" * 64, "outputSha256": "b" * 64}}
                health = build_mod_health(item(), game=GAME, now=NOW, local_analysis={"status": "conversion-stale", "plugins": [plugin]})
                action = next(a for a in health["actions"] if a["id"] == "esl-stale-Original.esp")
                self.assertEqual(action["provenanceState"], state)
                plugin["provenance"]["originalSha256"] = None
                health = build_mod_health(item(), game=GAME, now=NOW, local_analysis={"plugins": [plugin]})
                self.assertFalse(any(a["id"].startswith("esl-stale") for a in health["actions"]))

    def test_malformed_optional_evidence_is_ignored_and_previous_timestamps_normalize(self):
        previous = build_mod_health(item(), game=GAME, now=NOW - 100, metadata_checked_at=NOW - 100,
                                    metadata={"status": "published", "updated_timestamp": NOW - 1000})
        previous["page"]["checkedAt"] = str(NOW - 100)
        previous["page"]["updatedTimestamp"] = str(NOW - 1000)
        evidence = observe()
        evidence["bugs"] = []
        evidence["page"] = "unexpected"
        health = build_mod_health(item(), game=GAME, now=NOW, previous=previous, metadata_checked_at=NOW,
                                  metadata={"status": "published", "updated_timestamp": NOW - 200}, page_evidence=evidence,
                                  local_analysis={"plugins": {}, "eslProvenance": [None, "unexpected"]})
        self.assertTrue(health["page"]["changedSincePrevious"])
        self.assertEqual(health["bugs"]["state"], "unknown")


if __name__ == "__main__":
    unittest.main()
