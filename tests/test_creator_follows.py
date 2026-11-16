"""Exact creator identities, durable local follows and native completion races."""
from __future__ import annotations

import copy
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from mo2_update_arsenal.bridge import ArsenalService
from mo2_update_arsenal.creator_follows import (
    ModholdCatalog, _creator_from_update, download_project_receipt, empty_document,
    nexus_creator, project_identity, validate_document,
)
from mo2_update_arsenal.native_nexus import NativeNexusGateway
from mo2_update_arsenal.scanner import summary_from_metadata

GAME = "skyrimspecialedition"
ACCOUNT = 199502561
CID = "nexus:user:" + str(ACCOUNT)
MODHOLD_CID = "modhold:profile:sethsaidthat"


def item(mid=77, *, item_id=None, provider="nexus"):
    url = (f"https://www.nexusmods.com/{GAME}/mods/{mid}" if provider == "nexus"
           else f"https://modholdmods.com/g/skyrim-se/mods/{mid}/fixture-project")
    return {"id": item_id or "live-" + str(mid), "name": "Project " + str(mid),
            "mo2InternalName": "Native " + str(mid), "nexusId": mid if provider == "nexus" else 0,
            "installedVersion": "1.0", "source": {"url": url, "type": provider, "kind": "project", "verified": True}}


def metadata(mid=77, account=ACCOUNT, name="SethSaidThat", version="2.0"):
    return {"mod_id": mid, "domain_name": GAME, "version": version, "status": "published",
            "author": "The credited author is not necessarily the uploader", "uploaded_by": name,
            "uploaded_users_profile_url": f"https://www.nexusmods.com/users/{account}",
            "user": {"member_id": account, "name": name, "avatar_url": f"https://avatars.nexusmods.com/{account}/100",
                     "email": "never-retain@example.invalid", "private_token": "never-retain"}}


def update(row=None, *, version="2.0", account=ACCOUNT, name="SethSaidThat", checked=None, release_id=""):
    row = row or item()
    identity = project_identity(row["source"]["url"])
    checked = checked or time.time()
    if identity["provider"] == "nexus":
        return summary_from_metadata(row, metadata(int(identity["projectId"]), account, name, version), checked)
    return {"provider": "modhold", "source": "modhold-release-api", "sourceUrl": row["source"]["url"],
            "projectId": identity["projectId"], "publisherId": name,
            "author": {"name": name, "url": "https://modholdmods.com/u/" + name, "role": "mod author"},
            "outcome": "release", "latestVersion": version, "releaseId": release_id or "541",
            "freshness": "fresh", "checkedAt": checked, "expiresAt": checked + 900, "verdict": "candidate"}


def profile(rows=None, profile_id="profile-a"):
    return {"profileId": profile_id, "profileName": "Profile A" if profile_id == "profile-a" else "Profile B",
            "game": GAME, "gameRuntime": "unknown", "loader": "", "groups": [], "items": rows or [item()]}


def catalog_project(mid=11, handle="SethSaidThat"):
    return {"id": mid, "name": "Project " + str(mid), "game": "skyrim-se", "version": "1.2.3",
            "updated": "2026-11-16 05:20:19", "author": {"handle": handle, "name": handle},
            "page": f"https://modholdmods.com/g/skyrim-se/mods/{mid}/fixture-project"}


class CreatorIdentityTests(unittest.TestCase):
    def test_native_owned_metadata_preserves_only_public_uploader_identity(self):
        raw = metadata()
        normalized = NativeNexusGateway._metadata(GAME, 77, raw)
        self.assertEqual(normalized["creator"]["id"], CID)
        self.assertEqual(normalized["creator"]["role"], "uploader")
        self.assertEqual(normalized["creator"]["avatarUrl"], f"https://avatars.nexusmods.com/{ACCOUNT}/100")
        self.assertNotIn("user", normalized)
        self.assertNotIn("never-retain", json.dumps(normalized))
        self.assertEqual(summary_from_metadata(item(), normalized, time.time())["creator"]["id"], CID)
        self.assertNotEqual(normalized["author"], normalized["creator"]["name"])

    def test_absent_native_body_ids_use_only_the_owned_request_context(self):
        raw = metadata(); raw.pop("domain_name"); raw.pop("mod_id")
        self.assertIsNone(nexus_creator(raw, GAME, 77))
        self.assertEqual(NativeNexusGateway._metadata(GAME, 77, raw)["creator"]["id"], CID)
        with self.assertRaises(ValueError):
            NativeNexusGateway._metadata(GAME, 78, metadata())

    def test_name_credit_and_profile_spoofs_do_not_establish_an_account(self):
        for change in ({"user": {}}, {"uploaded_by": "Different"},
                       {"uploaded_users_profile_url": "https://www.nexusmods.com/users/99"},
                       {"uploaded_users_profile_url": f"https://nexusmods.com.evil.test/users/{ACCOUNT}"},
                       {"user": {"member_id": True, "name": "SethSaidThat"}},
                       {"mod_id": 78}, {"domain_name": "fallout4"}):
            self.assertIsNone(nexus_creator({**metadata(), **change}, GAME, 77), change)
        raw = metadata(); raw["uploaded_users_profile_url"] = f"https://www.nexusmods.com/Users/{ACCOUNT}"
        self.assertEqual(nexus_creator(raw, GAME, 77)["id"], CID)

    def test_same_title_never_maps_a_different_exact_project(self):
        foreign = item(78); foreign["name"] = item()["name"]
        self.assertNotIn("creator", summary_from_metadata(foreign, metadata(77), time.time()))
        for address in ("https://modholdmods.com.evil.test/g/skyrim-se/mods/11/title",
                        "https://modholdmods.com/g/skyrim-se/mods/011/title",
                        "https://modholdmods.com/g/skyrim-se/mods/11/../12",
                        "https://modholdmods.com/g/skyrim-se/mods/9007199254740992/title",
                        "https://www.nexusmods.com/skyrimspecialedition/mods/77?token=secret"):
            self.assertIsNone(project_identity(address), address)

    def test_external_author_requires_exact_source_publisher_and_freshness(self):
        row = item(11, provider="modhold"); identity = project_identity(row["source"]["url"])
        self.assertEqual(_creator_from_update(identity, update(row))["id"], MODHOLD_CID)
        for change in ({"publisherId": "Different"}, {"sourceUrl": item(12, provider="modhold")["source"]["url"]},
                       {"freshness": "stale"}, {"checkedAt": float("nan")},
                       {"expiresAt": time.time() - 1}, {"outcome": "unavailable"}):
            self.assertIsNone(_creator_from_update(identity, {**update(row), **change}), change)


class CatalogTests(unittest.TestCase):
    def test_complete_pages_share_one_window_across_all_creators(self):
        calls = []
        rows = [catalog_project(11), catalog_project(12, "OtherAuthor"), catalog_project(13)]
        def fetch(url):
            calls.append(url)
            page = int(url.rsplit("=", 1)[1])
            return {"total": 3, "page": page, "per_page": 2, "mods": rows[(page-1)*2:page*2]}
        catalog = ModholdCatalog(fetch, clock=lambda: 1000)
        first, checked = catalog.collect(threading.Event())
        second, _ = catalog.collect(threading.Event())
        self.assertEqual(len(first), 3); self.assertEqual(first, second); self.assertEqual(checked, 1000)
        self.assertEqual(calls, ["https://modholdmods.com/api/v1/mods?page=1", "https://modholdmods.com/api/v1/mods?page=2"])

    def test_partial_or_reordered_catalog_never_replaces_last_good(self):
        now = [1000]; mode = ["good"]
        def fetch(url):
            page = int(url.rsplit("=", 1)[1])
            if mode[0] == "good":
                return {"total": 1, "page": 1, "per_page": 2, "mods": [catalog_project()]}
            return {"total": 3, "page": page, "per_page": 2, "mods": [catalog_project(11), catalog_project(12)] if page == 1 else []}
        catalog = ModholdCatalog(fetch, clock=lambda: now[0], ttl=1)
        original, checked = catalog.collect(threading.Event()); now[0] += 2; mode[0] = "bad"
        with self.assertRaisesRegex(ValueError, "incomplete"):
            catalog.collect(threading.Event())
        self.assertEqual(catalog._rows, original); self.assertEqual(catalog._checked, checked)
        with self.assertRaisesRegex(ValueError, "could not be verified"):
            catalog.collect(threading.Event())

    def test_duplicate_ids_changed_totals_and_wrong_game_are_rejected(self):
        def cases(page):
            if page == 1:
                return {"total": 2, "page": 1, "per_page": 1, "mods": [catalog_project()]}
            return {"total": 2, "page": 2, "per_page": 1, "mods": [catalog_project()]}
        with self.assertRaisesRegex(ValueError, "repeated"):
            ModholdCatalog(lambda u: cases(int(u.rsplit("=", 1)[1]))).collect(threading.Event())
        bad = catalog_project(); bad["game"] = "fallout4"
        with self.assertRaises(ValueError):
            ModholdCatalog.project(bad)
        cancel = threading.Event(); cancel.set(); fetch = Mock()
        with self.assertRaises(ValueError):
            ModholdCatalog(fetch).collect(cancel)
        fetch.assert_not_called()


class CreatorFollowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.service = ArsenalService("<!doctype html>", self.root / "cache.json", lambda request: request.finish({"ok": False}), profile(), discovery_directory=self.root / "sessions")
        self.addCleanup(self.service.stop)
        self.service.intelligence.auto_audit = Mock()
        self.follows = self.service.creator_follows
        self.service._creator_status = SimpleNamespace(check=lambda handle, account: {
            "verified": True, "state": "not-marked-banned", "accountId": account, "handle": handle,
            "profileUrl": "https://www.nexusmods.com/profile/" + handle, "checkedAt": time.time()})
        self.follows._catalog = ModholdCatalog(lambda url: {"mods": [], "total": 0, "page": 1, "per_page": 50})
        self.follows._scan_metadata = lambda snapshot, cancel, force: (
            {row["id"]: update(row) for row in snapshot["items"] if project_identity((row.get("source") or {}).get("url"))}, [])

    def command(self, action, **fields):
        return self.follows.command({"profileId": self.service.profile_id(), "executionEpoch": self.service._execution_epoch,
                                     "action": action, **fields})

    def settle(self):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            with self.service._guard:
                busy = self.follows._active or self.follows._inventory_running or self.follows._receipt_workers
            if not busy:
                return self.follows.snapshot()
            threading.Event().wait(0.005)
        self.fail("Creator lifecycle worker did not settle")

    def seed(self, rows=None):
        rows = rows or [item()]
        owned = profile(rows, self.service.profile_id())
        self.follows.observe_updates(owned, {row["id"]: update(row) for row in rows})
        return self.follows.snapshot()

    def test_existing_verified_library_follows_without_an_extra_metadata_fetch(self):
        row = item(); row["update"] = update(row)
        self.service._snapshot["items"] = [row]
        self.service._items_by_id = {row["id"]: row}
        scan = self.follows._scan_metadata = Mock(return_value=({}, []))
        self.follows.inventory(); result = self.settle()
        self.assertEqual(result["summary"]["following"], 1)
        self.assertEqual(result["creators"][0]["role"], "uploader")
        self.assertTrue(all(not call.args[0]["items"] for call in scan.call_args_list))
        provenance = result["creators"][0]["projects"][0]["provenance"]
        self.assertEqual(next(iter(provenance.values()))["evidenceKind"], "verified-installed-library-metadata")

    def test_native_snapshot_without_owned_metadata_or_install_does_not_spawn_discovery(self):
        with patch("mo2_update_arsenal.creator_follows.threading.Thread") as worker:
            self.follows.inventory()
            self.service.set_snapshot(profile(profile_id="profile-b"))
            worker.assert_not_called()
        self.assertFalse(self.follows._inventory_running)
        self.assertEqual(self.follows.snapshot()["creators"], [])

    def test_two_projects_and_renamed_account_dedupe_by_numeric_account(self):
        self.seed([item(77), item(78)])
        self.follows.observe_updates(profile(), {item()["id"]: update(name="RenamedUploader")})
        result = self.follows.snapshot()
        self.assertEqual(len(result["creators"]), 1)
        self.assertEqual(result["creators"][0]["name"], "RenamedUploader")
        self.assertEqual(len(result["creators"][0]["projects"]), 2)

    def test_unknown_author_name_requires_review_and_same_title_is_never_used(self):
        row = item(); observed = update(); observed.pop("creator")
        self.follows.observe_updates(profile(), {row["id"]: observed})
        result = self.follows.snapshot()
        self.assertEqual(result["creators"], [])
        self.assertEqual(len(result["review"]), 1)
        self.assertIn("not bound", result["review"][0]["reason"])

    def test_tombstone_survives_automatic_receipts_and_restart(self):
        self.seed(); self.assertTrue(self.command("unfollow", creatorIds=[CID])["ok"])
        self.follows.downloaded({"profileId": "profile-a", "game": GAME, "modId": 77}, 42)
        result = self.settle(); self.assertEqual(result["creators"][0]["state"], "unfollowed")
        with patch("mo2_update_arsenal.gameplay.Chronicle._write"):
            restarted = ArsenalService("", self.root / "cache.json", lambda request: None, profile())
        self.addCleanup(restarted.stop)
        self.assertEqual(restarted.creator_follows.snapshot()["creators"][0]["state"], "unfollowed")
        self.assertFalse(self.command("resume", creatorIds=[CID])["ok"])
        self.assertFalse(self.command("pause", creatorIds=[CID])["ok"])
        self.assertEqual(self.command("restore", creatorIds=[CID])["creators"][0]["state"], "following")

    def test_pause_preserves_normal_source_updates(self):
        self.seed(); self.command("pause", creatorIds=[CID])
        self.follows.observe_updates(profile(), {item()["id"]: update(version="9.0")})
        self.assertEqual(self.follows.snapshot()["creators"][0]["projects"][0]["latestVersion"], "2.0")
        self.assertTrue(self.service.begin_scan("local")["ok"])
        deadline = time.monotonic() + 3
        while self.service.scan_state()["state"] == "running" and time.monotonic() < deadline:
            threading.Event().wait(0.005)
        self.assertEqual(self.service.scan_state()["state"], "completed")
        self.assertIn("update", self.service._items_by_id[item()["id"]])

    def test_unread_changes_only_for_a_release_and_mark_seen_is_durable(self):
        self.seed(); baseline = self.follows.snapshot()["creators"][0]
        self.assertEqual(baseline["unreadCount"], 0)
        self.follows.observe_updates(profile(), {item()["id"]: update(checked=time.time() + 1)})
        self.assertEqual(self.follows.snapshot()["creators"][0]["unreadCount"], 0)
        self.follows.observe_updates(profile(), {item()["id"]: update(version="3.0")})
        self.assertEqual(self.follows.snapshot()["creators"][0]["unreadCount"], 1)
        self.command("mark-seen", creatorIds=[CID])
        self.follows.observe_updates(profile(), {item()["id"]: update(version="3.0", checked=time.time() + 2)})
        self.assertEqual(self.follows.snapshot()["creators"][0]["unreadCount"], 0)

    def test_failed_reads_preserve_the_last_good_and_creator_conflicts_hold_migration(self):
        self.seed()
        self.follows.observe_updates(profile(), {item()["id"]: {"outcome": "unavailable", "reason": "HTTP 404 is unresolved"}})
        before = self.follows.snapshot()["creators"][0]["projects"][0]
        self.assertEqual(before["latestVersion"], "2.0")
        self.follows.observe_updates(profile(), {item()["id"]: update(account=1234, name="OtherCreator")})
        result = self.follows.snapshot()
        self.assertEqual(len(result["creators"]), 1)
        self.assertEqual(result["creators"][0]["id"], CID)
        self.assertIn("different creator", result["creators"][0]["migrationHold"]["reason"])
        self.assertEqual(self.service._items_by_id[item()["id"]]["source"]["url"], item()["source"]["url"])
        self.assertFalse(result["integration"]["canonicalFeed"])

    def test_bulk_mutations_validate_entire_selection_revision_and_context(self):
        result = self.seed(); revision = result["revision"]
        self.assertFalse(self.command("unfollow", creatorIds=[CID, "not-a-creator"])["ok"])
        self.assertFalse(self.command("pause", creatorIds=[CID], revision=revision - 1)["ok"])
        self.assertFalse(self.follows.command({"profileId": "profile-b", "executionEpoch": 0, "action": "settings", "autoFollow": False})["ok"])
        self.assertEqual(self.follows.snapshot()["creators"][0]["state"], "following")
        self.assertFalse(self.command("discover", itemIds=["invented-mod"])["ok"])
        self.assertFalse(self.command("discover", itemIds=[])["ok"])

    def test_explicit_discovery_can_run_while_automatic_discovery_is_opted_out(self):
        self.command("settings", autoFollow=False)
        self.follows.observe_updates(profile(), {item()["id"]: update()})
        self.assertEqual(self.follows.snapshot()["creators"], [])
        self.assertTrue(self.command("discover", itemIds=[item()["id"]])["accepted"])
        result = self.settle()
        self.assertEqual(result["summary"]["following"], 1)
        self.assertFalse(result["autoFollow"])

    def test_profile_a_to_b_to_a_discards_inflight_discovery(self):
        entered, release = threading.Event(), threading.Event()
        def slow(snapshot, cancel, force):
            entered.set(); self.assertTrue(release.wait(3)); return {item()["id"]: update()}, []
        self.follows._scan_metadata = slow
        self.command("discover"); self.assertTrue(entered.wait(2))
        self.service.set_snapshot(profile(profile_id="profile-b"))
        self.service.set_snapshot(profile())
        release.set(); threading.Event().wait(0.03)
        self.assertEqual(self.settle()["creators"], [])

    def test_pause_resume_during_check_cannot_apply_old_creator_generation(self):
        self.seed(); entered, release = threading.Event(), threading.Event()
        def slow(snapshot, cancel, force):
            entered.set(); self.assertTrue(release.wait(3)); return {item()["id"]: update(version="9.0")}, []
        self.follows._scan_metadata = slow
        self.command("check"); self.assertTrue(entered.wait(2))
        self.command("pause", creatorIds=[CID]); self.command("resume", creatorIds=[CID])
        release.set(); result = self.settle()
        self.assertEqual(result["creators"][0]["projects"][0]["latestVersion"], "2.0")

    def test_normal_source_check_preempts_creator_work_without_losing_follows(self):
        self.seed(); entered = threading.Event()
        def wait_for_cancel(snapshot, cancel, force):
            entered.set(); self.assertTrue(cancel.wait(3)); return {}, []
        self.follows._scan_metadata = wait_for_cancel
        self.command("check"); self.assertTrue(entered.wait(2))
        self.assertTrue(self.service.begin_scan("local")["ok"])
        result = self.settle()
        self.assertEqual(result["creators"][0]["projects"][0]["latestVersion"], "2.0")

    def test_corrupt_state_and_failed_persistence_preserve_previous_bytes(self):
        self.seed(); original = (self.root / "chronicle.json").read_bytes()
        with patch.object(self.service.chronicle, "_write", side_effect=OSError("disk unavailable")):
            self.assertFalse(self.command("unfollow", creatorIds=[CID])["ok"])
        self.assertEqual(self.follows.snapshot()["creators"][0]["state"], "following")
        self.assertEqual((self.root / "chronicle.json").read_bytes(), original)
        raw = self.service.chronicle._profile("profile-a"); raw["creatorFollows"] = ["corrupt"]
        self.assertFalse(self.follows.snapshot()["ok"])
        self.assertFalse(self.command("settings", autoFollow=False)["ok"])
        self.assertEqual(raw["creatorFollows"], ["corrupt"])
        self.assertEqual((self.root / "chronicle.json").read_bytes(), original)

    def test_status_checks_pin_account_and_first_ban_is_unread(self):
        self.seed(); calls = []
        def status(handle, account):
            calls.append((handle, account))
            return {"verified": True, "state": "banned", "accountId": account, "handle": handle,
                    "profileUrl": "https://www.nexusmods.com/profile/" + handle, "checkedAt": time.time()}
        self.service._creator_status = SimpleNamespace(check=status)
        self.command("check", creatorIds=[CID]); result = self.settle()
        self.assertEqual(calls, [("SethSaidThat", str(ACCOUNT))])
        self.assertEqual(result["creators"][0]["notice"]["state"], "banned")
        self.assertEqual(result["creators"][0]["unreadCount"], 1)
        self.service._creator_status = SimpleNamespace(check=lambda *args: {"verified": False, "state": "unknown"})
        self.command("check"); result = self.settle()
        self.assertEqual(result["creators"][0]["notice"]["state"], "banned")

    def test_new_modhold_catalog_projects_are_uninstalled_and_unread_once(self):
        self.seed([item(11, provider="modhold")])
        calls = []
        def fetch(url):
            calls.append(url)
            return {"total": 3, "page": 1, "per_page": 50,
                    "mods": [catalog_project(11), catalog_project(12), catalog_project(13, "OtherCreator")]}
        self.follows._catalog = ModholdCatalog(fetch)
        self.command("check"); result = self.settle()
        projects = result["creators"][0]["projects"]
        self.assertEqual(len(projects), 2)
        fresh = next(p for p in projects if p["projectId"] == "12")
        self.assertEqual(fresh["itemId"], ""); self.assertEqual(fresh["origin"], "creator-catalog")
        self.assertTrue(fresh["unread"])
        self.assertEqual(len(self.service._items_by_id), 1)
        self.command("mark-seen", creatorIds=[MODHOLD_CID])
        self.follows._scan_metadata = lambda snapshot, cancel, force: (
            {row["id"]: update(row, version="1.2.3" if project_identity(row["source"]["url"])["projectId"] == "12" else "2.0") for row in snapshot["items"]}, [])
        self.command("check"); result = self.settle()
        self.assertEqual(result["creators"][0]["unreadCount"], 0)
        self.assertEqual(len(calls), 1)
        self.follows._scan_metadata = lambda snapshot, cancel, force: ({row["id"]: {
            "outcome": "unavailable", "reason": "Provider unavailable"} for row in snapshot["items"]}, ["Provider unavailable"])
        self.command("check"); result = self.settle()
        review = next(row for row in result["review"] if row["id"] == "modhold:skyrim-se:12")
        self.assertEqual(review["itemId"], "")

    def test_installed_callback_survives_same_profile_inventory_change(self):
        new = item(78)
        self.follows.installed(new["mo2InternalName"])
        self.service.set_snapshot(profile([item(), new]))
        result = self.settle()
        self.assertEqual(result["summary"]["following"], 1)
        self.assertEqual(result["creators"][0]["projects"][0]["projectId"], "78")
        self.assertEqual(result["creators"][0]["projects"][0]["origin"], "installed")

    def test_completion_before_download_handle_registration_is_consumed_once(self):
        def native(request):
            self.service.set_download_status(42, "complete")
            request.finish({"ok": True, "downloadId": 42})
        self.service.ui_call = native
        ticket = {"profileId": "profile-a", "game": GAME, "provider": "nexus", "modId": 77, "fileId": 9}
        self.assertTrue(self.service._start_native_download("download", {}, ticket)["ok"])
        result = self.settle()
        self.assertEqual(result["summary"]["following"], 1)
        self.service.set_download_status(42, "complete"); result = self.settle()
        provenance = result["creators"][0]["projects"][0]["provenance"]
        self.assertEqual(sum(key.startswith("download:") for key in provenance), 1)

    def test_failed_download_and_old_profile_completion_never_follow(self):
        ticket = {"profileId": "profile-a", "game": GAME, "modId": 77, "status": "downloading", "creatorFollowContext": self.follows.context_generation}
        self.service._downloads[42] = ticket
        self.service.set_download_status(42, "failed")
        self.assertEqual(self.follows.snapshot()["creators"], [])
        self.service.set_snapshot(profile(profile_id="profile-b")); self.service.set_snapshot(profile())
        self.service.set_download_status(42, "complete")
        self.assertEqual(self.settle()["creators"], [])

    def test_completed_native_sidecar_is_verified_without_storing_paths(self):
        archive = self.root / "archive.zip"; archive.write_bytes(b"fixture")
        Path(str(archive) + ".meta").write_text("[General]\nrepository=Nexus\ngameName=SkyrimSE\nmodID=77\nfileID=9\nmodName=Fixture\n")
        self.service.set_download_status(42, "complete")
        self.follows.native_download_completed(42, str(archive), str(self.root), "Profile A")
        result = self.settle()
        self.assertEqual(result["summary"]["following"], 1)
        self.assertNotIn(str(self.root), json.dumps(result))
        provenance = result["creators"][0]["projects"][0]["provenance"]
        self.assertEqual(next(iter(provenance.values()))["evidenceKind"], "native-download-complete-sidecar")

    def test_untrusted_sidecar_path_duplicate_fields_or_wrong_game_are_not_receipts(self):
        archive = self.root / "archive.zip"; archive.write_bytes(b"fixture")
        sidecar = Path(str(archive) + ".meta")
        for body in ("[General]\ngameName=SkyrimSE\nmodID=77\nmodID=78\nfileID=9\n",
                     "[General]\ngameName=Fallout4\nmodID=77\nfileID=9\n",
                     "[General]\ngameName=SkyrimSE\nmodID=77\nfileID=9\nrepository=Other\n"):
            sidecar.write_text(body)
            self.assertIsNone(download_project_receipt(archive, self.root, GAME))
        self.assertIsNone(download_project_receipt(archive, self.root / "other", GAME))

    def test_live_http_api_requires_session_origin_and_exact_profile_epoch(self):
        self.service.start(); port = self.service._server.server_port
        def call(method, payload=None, authorized=True, origin=True):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            headers = {"Content-Type": "application/json"}
            if authorized: headers["X-Arsenal-Token"] = self.service.token
            if origin: headers["Origin"] = f"http://127.0.0.1:{port}"
            connection.request(method, "/api/creator-follows", body=json.dumps(payload) if payload else None, headers=headers)
            response = connection.getresponse(); status, body = response.status, json.loads(response.read()); connection.close()
            return status, body
        self.assertEqual(call("GET", authorized=False)[0], 403)
        self.assertEqual(call("POST", {"action": "settings"}, origin=False)[0], 403)
        status, body = call("GET"); self.assertEqual(status, 200); self.assertTrue(body["autoFollow"])
        status, body = call("POST", {"profileId": "profile-a", "executionEpoch": 99, "action": "settings", "autoFollow": False})
        self.assertEqual(status, 200); self.assertFalse(body["ok"])

    def test_advisory_source_never_attributes_its_creator_to_the_installed_mod(self):
        row = item(); row["source"].update(verified=False, kind="advisory")
        self.follows.observe_updates(profile([row]), {row["id"]: update()})
        result = self.follows.snapshot()
        self.assertEqual(result["creators"], [])
        self.assertIn("exact project", result["review"][0]["reason"])

    def test_owned_native_nexus_scanner_discovery_reuses_exact_project_cache(self):
        from mo2_update_arsenal.creator_follows import CreatorFollowService
        self.follows._scan_metadata = CreatorFollowService._scan_metadata.__get__(self.follows)
        rows = [item(), item(item_id="second-installed-copy")]
        self.service._snapshot = profile(rows); self.service._items_by_id = {row["id"]: row for row in rows}
        self.service.native_nexus_available = True
        def native(request):
            self.assertEqual(request.cmd, "nexus-metadata")
            self.assertEqual(request.payload, {"game": GAME, "modId": 77})
            request.finish({"ok": True, "meta": NativeNexusGateway._metadata(GAME, 77, metadata())})
        calls = self.service.ui_call = Mock(side_effect=native)
        self.command("discover"); result = self.settle()
        self.assertEqual(result["summary"]["following"], 1); self.assertEqual(calls.call_count, 1)
        self.assertEqual(len(result["creators"][0]["projects"][0]["provenance"]), 2)
        self.command("discover"); self.settle(); self.assertEqual(calls.call_count, 1)

    def test_modhold_follows_use_the_existing_source_scanner_and_author_pin(self):
        from mo2_update_arsenal.creator_follows import CreatorFollowService
        from mo2_update_arsenal.source_registry import SourceScanner
        row = item(11, provider="modhold"); row["source"]["expectedAuthor"] = "SethSaidThat"
        self.service._snapshot = profile([row]); self.service._items_by_id = {row["id"]: row}
        self.follows._scan_metadata = CreatorFollowService._scan_metadata.__get__(self.follows)
        seen = []
        def run(request, cancel_event=None):
            seen.append(copy.deepcopy(request))
            return {"schemaVersion": 1, "profileId": request["profileId"], "cache": {"entries": []},
                    "itemRecords": {row["id"]: update(row)}, "stats": {}}
        self.service._source_scanner = SourceScanner(runner=SimpleNamespace(available=True, run=run))
        self.command("discover"); result = self.settle()
        self.assertEqual(result["creators"][0]["id"], MODHOLD_CID)
        self.assertEqual(seen[0]["items"][0]["source"]["expectedAuthor"], "SethSaidThat")
        self.assertFalse(result["integration"]["socialWrites"])

    def test_disabled_preference_and_cancel_invalidate_pending_receipt_metadata(self):
        entered, release = threading.Event(), threading.Event()
        def slow(snapshot, cancel, force):
            entered.set(); self.assertTrue(release.wait(3)); return {item()["id"]: update()}, []
        self.follows._scan_metadata = slow
        self.service.set_download_status(42, "complete")
        self.follows.downloaded({"profileId": "profile-a", "game": GAME, "modId": 77, "itemId": item()["id"]}, 42)
        self.assertTrue(entered.wait(2)); self.command("settings", autoFollow=False); release.set()
        threading.Event().wait(0.03)
        self.assertEqual(self.settle()["creators"], [])
        self.assertFalse(self.follows.snapshot()["autoFollow"])

    def test_removed_or_failed_completion_rejects_late_metadata_without_cancelling_installs(self):
        for state in ("removed", "failed", "paused"):
            entered, release = threading.Event(), threading.Event()
            def slow(snapshot, cancel, force):
                entered.set(); self.assertTrue(release.wait(3))
                return {row["id"]: update(row) for row in snapshot["items"]}, []
            self.follows._scan_metadata = slow
            self.service._downloads[42] = {"profileId": "profile-a", "game": GAME, "modId": 77,
                "itemId": item()["id"], "status": "downloading", "creatorFollowContext": self.follows.context_generation}
            self.service.set_download_status(42, "complete"); self.assertTrue(entered.wait(2))
            self.service.set_download_status(42, state); release.set()
            self.assertEqual(self.settle()["creators"], [], state)
        # A later, actual installation is its own successful native evidence.
        self.follows._scan_metadata = lambda snapshot, cancel, force: ({row["id"]: update(row) for row in snapshot["items"]}, [])
        self.follows.installed(item()["mo2InternalName"]); self.follows.inventory()
        self.assertEqual(self.settle()["summary"]["following"], 1)

    def test_waiting_creator_job_uses_scan_signal_and_cannot_cancel_normal_scan(self):
        self.service._scan = {"state": "running"}; self.service._scan_done.clear()
        scan = self.follows._scan_metadata = Mock(return_value=({item()["id"]: update()}, []))
        self.command("discover")
        deadline = time.monotonic() + 1
        while self.follows.snapshot()["job"]["state"] != "waiting" and time.monotonic() < deadline:
            threading.Event().wait(0.005)
        self.assertEqual(self.follows.snapshot()["job"]["state"], "waiting"); scan.assert_not_called()
        self.command("cancel")
        self.assertEqual(self.service._scan["state"], "running")
        self.service._scan = {"state": "completed"}; self.service._scan_done.set()
        self.assertEqual(self.settle()["creators"], [])

    def test_corrupt_saved_creator_identity_is_retained_not_replaced(self):
        self.seed()
        raw = self.service.chronicle._profile("profile-a")["creatorFollows"]
        raw["creators"][CID]["profileUrl"] = "https://www.nexusmods.com/users/333"
        self.assertFalse(self.follows.snapshot()["ok"])
        self.assertFalse(self.command("discover")["ok"])
        self.assertEqual(raw["creators"][CID]["profileUrl"], "https://www.nexusmods.com/users/333")
        invalid = empty_document(); invalid["creators"] = {"a": {"id": "a", "state": []}}
        with self.assertRaises(ValueError):
            validate_document(invalid)

    def test_scheduled_due_check_refreshes_uninstalled_catalog_project_versions(self):
        self.seed([item(11, provider="modhold")])
        self.follows._catalog = ModholdCatalog(lambda url: {"total": 2, "page": 1, "per_page": 50,
            "mods": [catalog_project(11), catalog_project(12)]})
        self.command("check"); self.settle(); self.command("mark-seen", creatorIds=[MODHOLD_CID])
        now = [time.time() + 1801]; self.follows.clock = lambda: now[0]
        observed = []
        def scan(snapshot, cancel, force):
            observed.append((copy.deepcopy(snapshot), force))
            return {row["id"]: update(row, version="3.0") for row in snapshot["items"]}, []
        self.follows._scan_metadata = scan
        self.assertTrue(self.follows.run_due()); result = self.settle()
        self.assertEqual({project_identity(row["source"]["url"])["projectId"] for row in observed[0][0]["items"]}, {"11", "12"})
        self.assertFalse(observed[0][1])  # Scheduled work respects the existing provider cache.
        fresh = next(p for p in result["creators"][0]["projects"] if p["projectId"] == "12")
        self.assertEqual(fresh["itemId"], ""); self.assertEqual(fresh["latestVersion"], "3.0"); self.assertTrue(fresh["unread"])
        self.assertEqual(result["creators"][0]["nextCheckAt"], now[0] + 1800)
        self.assertFalse(self.follows.run_due())

    def test_due_checks_wait_for_normal_scans_and_skip_paused_or_unfollowed_creators(self):
        self.seed(); self.follows.clock = lambda: time.time() + 1801
        self.service._scan = {"state": "running"}
        self.assertFalse(self.follows.run_due())
        self.service._scan = {"state": "completed"}
        self.command("pause", creatorIds=[CID]); self.assertFalse(self.follows.run_due())
        self.command("unfollow", creatorIds=[CID]); self.assertFalse(self.follows.run_due())

    def test_original_and_migrated_projects_sharing_one_installed_row_keep_distinct_scan_ids(self):
        self.seed()
        destination = item(11, item_id=item()["id"], provider="modhold")
        self.seed([destination])
        observed = []
        def scan(snapshot, cancel, force):
            observed.extend(snapshot["items"])
            return {row["id"]: update(row, version="3.0" if row["source"]["type"] == "nexus" else "4.0") for row in snapshot["items"]}, []
        self.follows._scan_metadata = scan
        self.command("check"); result = self.settle()
        self.assertEqual(len(observed), 2); self.assertEqual(len({row["id"] for row in observed}), 2)
        creators = {creator["id"]: creator for creator in result["creators"]}
        self.assertEqual(creators[CID]["projects"][0]["latestVersion"], "3.0")
        self.assertEqual(creators[MODHOLD_CID]["projects"][0]["latestVersion"], "4.0")
        self.assertEqual(creators[CID]["projects"][0]["itemId"], item()["id"])
        self.assertEqual(creators[MODHOLD_CID]["projects"][0]["itemId"], item()["id"])
        self.follows._scan_metadata = lambda snapshot, cancel, force: ({row["id"]: {
            "outcome": "unavailable", "reason": "Provider unavailable"} for row in snapshot["items"]}, ["Provider unavailable"])
        self.command("check"); result = self.settle()
        self.assertEqual({row["itemId"] for row in result["review"]}, {item()["id"]})

    def test_timer_lifetime_is_owned_and_old_tick_cannot_restart_a_stopped_session(self):
        timers = []
        class Timer:
            def __init__(self, interval, function, args=()):
                self.interval, self.function, self.args, self.cancelled = interval, function, args, False
                timers.append(self)
            def start(self): pass
            def cancel(self): self.cancelled = True
        with patch("mo2_update_arsenal.creator_follows.threading.Timer", Timer):
            self.follows.start(); self.follows.start(); self.assertEqual(len(timers), 1)
            self.assertEqual(timers[0].interval, 60)
            self.follows.stop(); self.assertTrue(timers[0].cancelled)
            self.follows.start(); self.assertEqual(len(timers), 2)
            timers[0].function(*timers[0].args)
            self.assertIs(self.follows._timer, timers[1]); self.assertEqual(len(timers), 2)
            timers[1].function(*timers[1].args); self.assertEqual(len(timers), 3)
            self.follows.stop(); self.assertTrue(timers[2].cancelled)
            timers[2].function(*timers[2].args); self.assertEqual(len(timers), 3)

    def test_cancel_defers_due_checks_without_unfollowing(self):
        self.seed(); self.follows.clock = lambda: time.time() + 1801
        result = self.command("cancel")
        self.assertEqual(result["creators"][0]["state"], "following")
        self.assertFalse(self.follows.run_due())


class NativeCreatorCallbackTests(unittest.TestCase):
    def setUp(self):
        from test_plugin_autostart import plugin_type
        self.timers = []; self.plugin = plugin_type(self.timers)()
        self.plugin._on_mod_installed.__globals__["call_str"] = lambda value, name, fallback: str(getattr(value, name)())
        self.follows = Mock(); self.server = SimpleNamespace(content=Mock(), creator_follows=self.follows,
            native_selection_snapshot=lambda _: {"profileName": "Profile A"}, set_download_status=Mock())
        self.plugin.server = self.server
        self.plugin.org = SimpleNamespace(profileName=lambda: "Profile A", downloadsPath=lambda: "/native/downloads",
            downloadManager=lambda: SimpleNamespace(downloadPath=lambda did: "/native/downloads/owned.zip"))

    def test_native_mod_interface_and_legacy_name_enqueue_only_exact_internal_name(self):
        self.plugin._on_mod_installed(SimpleNamespace(name=lambda: "Native 77"))
        self.plugin._on_mod_installed("Native 78")
        self.assertEqual([call.args[0] for call in self.follows.installed.call_args_list], ["Native 77", "Native 78"])
        self.assertEqual(len(self.timers), 1)

    def test_actual_profile_change_invalidates_before_debounced_inventory(self):
        self.plugin.org.profileName = lambda: "Profile B"
        self.plugin._on_mod_installed("Native 77"); self.follows.installed.assert_not_called()
        self.plugin._on_profile_changed("Profile A", "Profile B")
        self.follows.invalidate.assert_called_once()
        self.assertEqual(self.timers[0][0], 150)

    def test_only_complete_callback_captures_native_download_path(self):
        self.plugin._on_download_failed(42)
        self.follows.native_download_completed.assert_not_called()
        self.plugin._on_download_complete(42)
        self.server.set_download_status.assert_called_with(42, "complete")
        self.follows.native_download_completed.assert_called_once_with(42, "/native/downloads/owned.zip", "/native/downloads", "Profile A")


if __name__ == "__main__":
    unittest.main()
