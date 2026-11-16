"""Identity-bound profile evidence and durable, reversible migration choices."""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.message import Message
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error

from mo2_update_arsenal.bridge import ArsenalService
from mo2_update_arsenal.creator_continuity import (
    CreatorStatusMonitor, MAX_PAGE_BYTES, fetch_nexus_profile, parse_nexus_profile,
    parse_nexus_profile_evidence, public_url, sanitize_choice, sanitize_observation, sanitize_registry,
)
from mo2_update_arsenal.gameplay import Chronicle
from mo2_update_arsenal.source_registry import SourceScanner

HANDLE = "SethSaidThat"
PROFILE_URL = "https://www.nexusmods.com/profile/" + HANDLE
ORIGINAL = "https://www.nexusmods.com/skyrimspecialedition/mods/196696"
DESTINATION = "https://modholdmods.com/g/skyrim-se/mods/11/estates-of-skyrim-immersive-settlement-and-survival-overhaul"
# Reduced verbatim profile-header structure observed on 2026-11-16. No
# scripts, page sessions, full profile prose or third-party mod contents.
OBSERVED_HEADER = '''<div class="relative z-10 next-container space-y-6 sm:space-y-8">
<div class="flex flex-col items-center gap-y-3 sm:flex-row sm:items-center sm:gap-x-6">
<div class="flex shrink-0 flex-col items-center"><img alt="SethSaidThat" class="size-25 rounded-full" src="https://avatars.nexusmods.com/199502561/100"/>
<span class="nxm-pill nxm-pill-danger -mt-0.5 drop-shadow-lg"><svg viewBox="0 0 24 24" role="presentation" class="shrink-0 nxm-pill-icon"></svg><span class="nxm-pill-label">Banned</span></span></div>
<div class="flex flex-col gap-2"><span>United States</span><h1 class="text-heading-sm sm:text-heading-lg text-neutral-strong text-center break-all sm:text-left">SethSaidThat</h1></div>
</div></div>'''


def active_header():
    return OBSERVED_HEADER.replace("nxm-pill-danger", "nxm-pill-premium").replace(">Banned<", ">Premium<")


def observation(handle=HANDLE, account="199502561", stamp=None, state="banned"):
    return {"state": state, "verified": True, "handle": handle,
            "profileUrl": "https://www.nexusmods.com/profile/" + handle,
            "checkedAt": time.time() if stamp is None else stamp, "accountId": account,
            "evidenceKind": "provider-profile-header"}


def choice(**changes):
    return {"schemaVersion": 1, "candidateId": "nexus-skyrimspecialedition-196696--modhold-skyrim-se-11",
            "authorId": "nexus:sethsaidthat", "expectedAuthor": HANDLE,
            "originalUrl": ORIGINAL, "previousOverride": "", "destinationUrl": DESTINATION,
            "acceptedAt": datetime.now(timezone.utc).isoformat(), "evidenceRevision": "2026-11-16.1",
            "state": "tracking", **changes}


def registry():
    return json.loads((Path(__file__).resolve().parents[1] / "shared" / "creator-continuity.v1.json").read_text(encoding="utf-8"))


class Response(io.BytesIO):
    def __init__(self, html=OBSERVED_HEADER, *, status=200, url=PROFILE_URL, mime="text/html", encoding="identity"):
        super().__init__(html.encode("utf-8"))
        self.status, self.url = status, url
        self.headers = Message()
        self.headers["Content-Type"] = mime
        self.headers["Content-Encoding"] = encoding

    def geturl(self):
        return self.url


class Opener:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def open(self, request, **kwargs):
        self.calls.append((request, kwargs))
        if self.error:
            raise self.error
        return self.response


class CreatorProfileParserTests(unittest.TestCase):
    def test_observed_header_binds_h1_avatar_pill_and_stable_account(self):
        self.assertEqual(parse_nexus_profile(OBSERVED_HEADER, HANDLE), "banned")
        self.assertEqual(parse_nexus_profile_evidence(OBSERVED_HEADER, HANDLE),
                         {"state": "banned", "accountId": "199502561"})
        self.assertEqual(parse_nexus_profile(active_header(), HANDLE), "not-marked-banned")

    def test_banned_commenter_never_changes_the_profile_author_status(self):
        comment = '<article><div><img alt="FRIENDMYFRIEND" src="https://avatars.nexusmods.com/777/100"/><span class="nxm-pill nxm-pill-danger"><span class="nxm-pill-label">BANNED</span></span></div><p>Comment body</p></article>'
        self.assertEqual(parse_nexus_profile(active_header() + comment, HANDLE), "not-marked-banned")
        # Even a commenter using the same visible name is outside the header.
        self.assertEqual(parse_nexus_profile(active_header() + comment.replace("FRIENDMYFRIEND", HANDLE), HANDLE), "not-marked-banned")
        self.assertEqual(parse_nexus_profile(comment, HANDLE), "unknown")

    def test_changed_status_markup_cannot_turn_banned_into_unmarked(self):
        changed = OBSERVED_HEADER.replace("nxm-pill-danger", "new-provider-style")
        self.assertEqual(parse_nexus_profile(changed, HANDLE), "unknown")
        self.assertEqual(parse_nexus_profile(OBSERVED_HEADER.replace("nxm-pill-label", "new-label"), HANDLE), "unknown")

    def test_header_identity_and_layout_must_be_unambiguous(self):
        for html in (OBSERVED_HEADER.replace(">SethSaidThat</h1>", ">AnotherAuthor</h1>"),
                     OBSERVED_HEADER.replace("avatars.nexusmods.com/", "avatars.nexusmods.com.evil.test/"),
                     OBSERVED_HEADER + "<h1>Another heading</h1>", "<h1>Verify your browser</h1>"):
            self.assertEqual(parse_nexus_profile(html, HANDLE), "unknown")
        self.assertEqual(parse_nexus_profile('<script>' + OBSERVED_HEADER + '</script>', HANDLE), "unknown")

    def test_profile_inputs_are_bounded(self):
        for handle in ("../SethSaidThat", "SethSaidThat?key=secret", "", "a" * 81):
            with self.assertRaises(ValueError):
                parse_nexus_profile(OBSERVED_HEADER, handle)
        with self.assertRaises(ValueError):
            parse_nexus_profile("x" * (MAX_PAGE_BYTES + 1), HANDLE)
        with self.assertRaises(ValueError):
            parse_nexus_profile("<div>" * 129 + OBSERVED_HEADER, HANDLE)

    def test_fetch_uses_exact_public_profile_and_preserves_stable_id(self):
        opener = Opener(Response())
        result = fetch_nexus_profile(HANDLE, opener=opener)
        self.assertTrue(result["verified"])
        self.assertEqual(result["accountId"], "199502561")
        request, kwargs = opener.calls[0]
        self.assertEqual(request.full_url, PROFILE_URL)
        self.assertEqual(kwargs["timeout"], 10)
        self.assertNotIn("Authorization", request.headers)
        self.assertNotIn("Cookie", request.headers)

    def test_fetch_failures_challenges_redirects_and_oversize_are_unknown(self):
        bad = [Opener(Response(status=403)), Opener(Response(url=PROFILE_URL + "Other")),
               Opener(Response(mime="application/json")), Opener(Response(encoding="gzip")),
               Opener(Response("<h1>Verify you are human</h1>")),
               Opener(Response("x" * (MAX_PAGE_BYTES + 1))),
               Opener(error=urllib.error.HTTPError(PROFILE_URL, 404, "Missing", {}, None)),
               Opener(error=TimeoutError())]
        for opener in bad:
            result = fetch_nexus_profile(HANDLE, opener=opener)
            self.assertEqual(result["state"], "unknown")
            self.assertFalse(result["verified"])


class CreatorMonitorTests(unittest.TestCase):
    def test_one_fetch_is_shared_and_cached_across_many_mods(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def fetch(handle):
            calls.append(handle); entered.set()
            self.assertTrue(release.wait(3))
            return observation(handle)
        monitor = CreatorStatusMonitor(fetch=fetch)
        with ThreadPoolExecutor(max_workers=8) as pool:
            first = pool.submit(monitor.check, HANDLE)
            self.assertTrue(entered.wait(2))
            rest = [pool.submit(monitor.check, HANDLE) for _ in range(7)]
            release.set()
            results = [first.result(timeout=3)] + [future.result(timeout=3) for future in rest]
        self.assertEqual(calls, [HANDLE])
        self.assertTrue(all(r["state"] == "banned" for r in results))
        self.assertEqual(monitor.check(HANDLE.lower())["accountId"], "199502561")
        self.assertEqual(calls, [HANDLE])

    def test_failed_recheck_and_exception_retain_last_verified_account(self):
        for raises in (False, True):
            now = [time.time() - 3600]
            count = [0]
            def fetch(handle):
                count[0] += 1
                if count[0] == 1:
                    return observation(handle, stamp=now[0])
                if raises:
                    raise RuntimeError("Transport stopped")
                return {"state": "unknown", "verified": False, "checkedAt": now[0]}
            monitor = CreatorStatusMonitor(fetch=fetch, clock=lambda: now[0])
            first = monitor.check(HANDLE)
            now[0] += 901
            failed = monitor.check(HANDLE)
            self.assertEqual(failed["state"], "unknown")
            self.assertFalse(failed["verified"])
            self.assertEqual(failed["lastVerified"]["checkedAt"], first["checkedAt"])
            self.assertEqual(failed["lastVerified"]["accountId"], "199502561")
            failed["lastVerified"]["state"] = "forged"
            self.assertEqual(monitor.check(HANDLE)["lastVerified"]["state"], "banned")
            self.assertEqual(count[0], 2)

    def test_handle_reuse_is_not_attributed_to_the_previous_creator(self):
        now = [time.time() - 3600]
        account = ["199502561"]
        monitor = CreatorStatusMonitor(fetch=lambda h: observation(h, account[0], now[0]), clock=lambda: now[0])
        monitor.check(HANDLE)
        account[0] = "999999999"; now[0] += 901
        result = monitor.check(HANDLE)
        self.assertFalse(result["verified"])
        self.assertTrue(result["identityChanged"])
        self.assertEqual(result["lastVerified"]["accountId"], "199502561")

    def test_seed_account_pin_is_applied_per_caller_without_refetching(self):
        calls = []
        def fetch(handle):
            calls.append(handle)
            return observation(handle)
        monitor = CreatorStatusMonitor(fetch=fetch)
        self.assertTrue(monitor.check(HANDLE, "199502561")["verified"])
        mismatch = monitor.check(HANDLE, "999")
        self.assertFalse(mismatch["verified"])
        self.assertEqual(mismatch["state"], "unknown")
        self.assertNotIn("lastVerified", mismatch)
        self.assertTrue(monitor.check(HANDLE, "199502561")["verified"])
        self.assertEqual(calls, [HANDLE])

    def test_failed_recheck_does_not_leak_another_pinned_accounts_history(self):
        now = [time.time() - 3600]
        calls = []
        def fetch(handle):
            calls.append(handle)
            return observation(handle, stamp=now[0]) if len(calls) == 1 else {"state": "unknown", "verified": False}
        monitor = CreatorStatusMonitor(fetch=fetch, clock=lambda: now[0])
        monitor.check(HANDLE, "199502561")
        now[0] += 901
        self.assertIn("lastVerified", monitor.check(HANDLE, "199502561"))
        self.assertNotIn("lastVerified", monitor.check(HANDLE, "999"))
        self.assertEqual(len(calls), 2)


class CreatorRegistryTests(unittest.TestCase):
    def test_observed_shared_registry_is_accepted_unchanged(self):
        payload = json.dumps(registry(), ensure_ascii=False)
        self.assertEqual(sanitize_registry(payload), payload)

    def test_import_requires_complete_bounded_author_and_status_evidence(self):
        cases = [
            ("revision", None), ("updatedAt", "2100-01-01T00:00:00Z"),
            ("authors.0.name", ""), ("authors.0.source", []),
            ("authors.0.source.accountId", "not-an-account"),
            ("authors.0.status.state", "a-rumor"), ("authors.0.status.observedAt", "yesterday"),
            ("authors.0.status.evidenceUrl", "https://nexusmods.com.evil.test/author"),
            ("authors.0.status.evidenceKind", []),
            ("authors.0.destinations", [{}]),
            ("authors.0.destinations.0.identityEvidence.url", "https://example.com/?api_key=secret"),
            ("authors.0.destinations.0.identityEvidence.targetUrl", "file:///etc/passwd"),
            ("authors.0.destinations.0.identityEvidence.observedAt", "2026-01-01"),
        ]
        for path, value in cases:
            with self.subTest(path=path):
                raw = registry(); cursor = raw
                parts = path.split(".")
                for part in parts[:-1]:
                    cursor = cursor[int(part)] if isinstance(cursor, list) else cursor[part]
                cursor[parts[-1]] = value
                self.assertEqual(sanitize_registry(json.dumps(raw)), "")

    def test_migration_url_game_mod_and_author_destination_must_agree(self):
        mutations = [
            lambda r: r["migrations"][0]["to"].update(projectId="999"),
            lambda r: r["migrations"][0]["to"].update(game="different-game"),
            lambda r: r["migrations"][0]["to"].update(url=DESTINATION.replace("modholdmods.com", "modholdmods.com.example.com")),
            lambda r: r["migrations"][0]["to"].update(url="https://modholdmods.com/g/skyrim-se/mods/9007199254740992", projectId="9007199254740992"),
            lambda r: r["migrations"][0]["to"].update(url="https://modholdmods.com/g/skyrim-se/mods/011", projectId="011"),
            lambda r: r["migrations"][0]["to"].update(url="https://modholdmods.com/g/skyrim-se/mods/11/one/two"),
            lambda r: r["migrations"][0]["from"].update(provider="website"),
            lambda r: r["migrations"][0].update(authorId="nexus:unregistered"),
            lambda r: r["migrations"][0].update(reviewRequired=False),
            lambda r: r["migrations"][0].update(evidenceUrls=[]),
            lambda r: r["authors"][0]["destinations"][0].update(profileUrl="https://another.example.com/u/SethSaidThat"),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(case=index):
                raw = registry(); mutate(raw)
                self.assertEqual(sanitize_registry(json.dumps(raw)), "")

    def test_duplicate_creator_handles_and_original_projects_are_rejected(self):
        raw = registry()
        raw["authors"].append({**copy.deepcopy(raw["authors"][0]), "id": "another-id"})
        self.assertEqual(sanitize_registry(json.dumps(raw)), "")
        raw = registry()
        raw["migrations"].append({**copy.deepcopy(raw["migrations"][0]), "id": "another-migration"})
        self.assertEqual(sanitize_registry(json.dumps(raw)), "")

    def test_invalid_json_types_unicode_and_size_fail_closed(self):
        for raw in (None, {}, "{invalid", "[]", "null", "\ud800", "x" * (MAX_PAGE_BYTES + 1)):
            self.assertEqual(sanitize_registry(raw), "")
        raw = registry(); raw["schemaVersion"] = True
        self.assertEqual(sanitize_registry(json.dumps(raw)), "")


class CreatorPersistenceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "chronicle.json"
        self.ledger = Chronicle(self.path)
        self.row = {"url": DESTINATION, "creatorContinuity": choice(), "creatorObservation": observation(), "note": "Keep custom settings"}

    def test_round_trip_preserves_original_author_pin_and_observation(self):
        self.assertTrue(self.ledger.set_progress("profile-a", {"mod": self.row}, {}, "", {"mod"})["ok"])
        restored = Chronicle(self.path)
        self.assertEqual(restored.creator_choices("profile-a")["mod"]["expectedAuthor"], HANDLE)
        self.assertEqual(restored.progress("profile-a")["rows"]["mod"]["creatorContinuity"]["originalUrl"], ORIGINAL)
        self.assertEqual(restored.progress("profile-a")["rows"]["mod"]["creatorObservation"]["accountId"], "199502561")
        self.assertEqual(restored.creator_choices("profile-b"), {})

    def test_paused_undone_and_manually_changed_sources_do_not_keep_active_pin(self):
        for state in ("paused", "undone"):
            row = {**self.row, "url": "", "creatorContinuity": choice(state=state)}
            self.ledger.set_progress("p", {"mod": row}, {}, "", {"mod"})
            self.assertEqual(self.ledger.creator_choices("p"), {})
            self.assertEqual(self.ledger.progress("p")["rows"]["mod"]["creatorContinuity"]["state"], state)
        row = {**self.row, "url": "https://github.com/example/replacement"}
        self.ledger.set_progress("p", {"mod": row}, {}, "", {"mod"})
        self.assertEqual(self.ledger.creator_choices("p"), {})

    def test_invalid_registry_replacement_preserves_saved_progress(self):
        payload = json.dumps(registry())
        self.assertTrue(self.ledger.set_progress("p", {"mod": self.row}, {"creatorRegistry": payload}, "", {"mod"})["ok"])
        before = self.ledger.progress("p")
        self.assertEqual(before["prefs"]["creatorRegistry"], payload)
        result = self.ledger.set_progress("p", {"mod": self.row}, {"creatorRegistry": "{invalid"}, "", {"mod"})
        self.assertFalse(result["ok"])
        self.assertEqual(self.ledger.progress("p"), before)

    def test_unrelated_save_retains_a_loaded_corrupt_registry_for_recovery(self):
        self.assertTrue(self.ledger.set_progress("p", {"mod": self.row}, {"creatorRegistry": json.dumps(registry())}, "", {"mod"})["ok"])
        document = json.loads(self.path.read_text(encoding="utf-8"))
        document["profiles"]["p"]["progress"]["prefs"]["creatorRegistry"] = "{unfinished registry"
        self.path.write_text(json.dumps(document), encoding="utf-8")
        restored = Chronicle(self.path)
        restored.set_progress("p", {"mod": {**self.row, "note": "An unrelated note edit"}}, {}, "", {"mod"})
        self.assertEqual(restored.progress("p")["prefs"].get("creatorRegistry"), "{unfinished registry")
        reloaded = Chronicle(self.path)
        self.assertEqual(reloaded.progress("p")["prefs"].get("creatorRegistry"), "{unfinished registry")

    def test_invalid_choice_replacement_does_not_silently_remove_author_pin(self):
        self.ledger.set_progress("p", {"mod": self.row}, {}, "", {"mod"})
        before = self.ledger.progress("p")
        bad = {**self.row, "creatorContinuity": {**choice(), "expectedAuthor": "wrong\nidentity"}}
        result = self.ledger.set_progress("p", {"mod": bad}, {}, "", {"mod"})
        self.assertFalse(result["ok"])
        self.assertEqual(self.ledger.progress("p"), before)

    def test_sanitizers_reject_secret_links_controls_and_invalid_account_ids(self):
        for url in ("https://example.com/?api_key=secret", "https://user:secret@example.com/", "https://example.com/a\nb", "https://example.com/\\evil"):
            self.assertEqual(public_url(url), "")
            self.assertIsNone(sanitize_choice(choice(destinationUrl=url)))
        self.assertIsNone(sanitize_choice(choice(acceptedAt="1900-01-01T00:00:00Z")))
        self.assertIsNone(sanitize_observation({**observation(), "accountId": "wrong"}))
        self.assertIsNone(sanitize_choice(choice(state=[])))
        self.assertIsNone(sanitize_choice(choice(schemaVersion=True)))
        self.assertIsNone(sanitize_observation({**observation(), "state": {}}))

    def test_destination_handle_can_follow_a_non_nexus_naming_scheme(self):
        for handle in ("author.name", "An Author", "a" * 180):
            self.assertEqual(sanitize_choice(choice(expectedAuthor=handle))["expectedAuthor"], handle)
        for handle in ("", "  ", "a" * 181, "author\x85name"):
            self.assertIsNone(sanitize_choice(choice(expectedAuthor=handle)))

    def test_approved_migration_reaches_scanner_with_pin_and_keeps_native_metadata(self):
        snap = {"profileId": "p", "game": "skyrimspecialedition", "items": [{"id": "mod", "name": "Estate", "nexusId": 196696,
                "installedVersion": "0.1.20", "source": {"url": ORIGINAL, "verified": True, "kind": "project", "type": "nexus"}}]}
        self.ledger.set_progress("p", {"mod": self.row}, {}, "", {"mod"})
        rewritten = ArsenalService._apply_source_links(copy.deepcopy(snap), self.ledger.source_links("p"), self.ledger.creator_choices("p"))
        item = rewritten["items"][0]
        self.assertEqual(item["source"]["url"], DESTINATION)
        self.assertEqual(item["source"]["expectedAuthor"], HANDLE)
        self.assertEqual(item["nexusId"], 196696)
        self.assertEqual(item["installedVersion"], "0.1.20")
        self.assertEqual(SourceScanner._items(rewritten), [item])
        self.assertEqual(snap["items"][0]["source"]["url"], ORIGINAL)

    def test_source_change_invalidates_old_observation(self):
        snap = {"profileId": "p", "items": [{"id": "mod", "source": {"url": ORIGINAL},
                "update": {"sourceUrl": ORIGINAL, "publisherId": HANDLE, "verdict": "current"}}]}
        result = ArsenalService._apply_source_links(snap, {"mod": DESTINATION}, {"mod": choice()})
        self.assertNotIn("update", result["items"][0])


class CreatorBridgeLifecycleTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.snapshot = {"profileId": "p", "game": "skyrimspecialedition", "items": [{"id": "mod", "name": "Estate",
                         "nexusId": 196696, "installedVersion": "0.1.20", "author": HANDLE}], "groups": []}
        self.service = ArsenalService("", Path(tmp.name) / "cache.json", lambda request: None, copy.deepcopy(self.snapshot))
        self.addCleanup(self.service.stop)

    def test_status_response_cannot_mutate_a_new_profile(self):
        entered, release = threading.Event(), threading.Event()
        def fetch(handle):
            entered.set(); self.assertTrue(release.wait(3)); return observation(handle)
        self.service._creator_status = CreatorStatusMonitor(fetch=fetch)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.service.creator_status, "mod", "p", HANDLE)
            self.assertTrue(entered.wait(2))
            self.service.set_snapshot({**copy.deepcopy(self.snapshot), "profileId": "another-profile"})
            release.set()
            result = future.result(timeout=3)
        self.assertFalse(result["ok"])
        self.assertNotIn("creatorObservation", self.service.chronicle.progress("another-profile").get("rows", {}).get("mod", {}))

    def test_stale_progress_save_cannot_apply_source_choice_to_new_profile(self):
        self.service.set_snapshot({**copy.deepcopy(self.snapshot), "profileId": "another-profile"})
        result = self.service.save_progress({"profileId": "p", "executionEpoch": 0,
                 "rows": {"mod": {"url": DESTINATION, "creatorContinuity": choice()}}, "prefs": {}})
        self.assertFalse(result["ok"])
        self.assertEqual(self.service.chronicle.creator_choices("another-profile"), {})


if __name__ == "__main__":
    unittest.main()
