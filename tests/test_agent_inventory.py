"""Genuine stdio-to-HTTP coverage against the production service in an isolated profile."""
import copy
import json
import os
from pathlib import Path
import subprocess
import time
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mo2_update_arsenal.agent_control import set_enabled

from mo2_update_arsenal.bridge import ArsenalService
from mo2_update_arsenal.file_advisor import NexusFileIndex
from mo2_update_arsenal.model import installed_file_fields
from mo2_update_arsenal.scanner import summary_from_metadata


class AgentInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.rows = [
            {"id": str(i), "name": f"Mod {i}", "enabled": bool(i % 2), "priority": i,
             "installedVersion": "2.6", "screenshotNote": "private comment"}
            for i in range(293)
        ]
        self.catalog = {"profileId": "target", "profileName": "Target", "items": self.rows,
                        "game": "skyrimspecialedition", "groups": []}
        self.service = ArsenalService("", Path(self.temp.name) / "cache.json",
                                      lambda req: self.fail("Read operation dispatched a mutation"),
                                      copy.deepcopy(self.catalog))
        self.service.start()
        self.addCleanup(self.service.stop)

    def test_consistent_pages_and_restart_fence(self):
        params = {"profileId": ["target"]}
        page = self.service.agent_mods(params)
        revision = page["revision"]
        ids = []
        while True:
            ids.extend(row["id"] for row in page["items"])
            self.assertNotIn("private comment", json.dumps(page))
            if page["nextOffset"] is None:
                break
            params.update(offset=[str(page["nextOffset"])], revision=[revision])
            page = self.service.agent_mods(params)
        self.assertEqual(ids, [row["id"] for row in self.rows])
        for q in ({"profileId": ["other"]}, dict(params, revision=["old-process-0"]),
                  {"profileId": ["target"], "offset": ["1"]}, dict(params, offset=["-1"])):
            self.assertFalse(self.service.agent_mods(q)["ok"])

    def test_background_provider_results_preserve_inventory_pages_and_refresh_row_evidence(self):
        profile = {"profileId": ["target"]}
        first = self.service.agent_mods(profile)
        revision = first["revision"]
        selection = dict(profile, itemId=["0"], revision=[revision])
        initial = self.service.agent_evidence(selection)
        self.assertEqual(initial["updateDecision"]["verdict"], "unknown")
        self.assertIn("No update decision has been recorded", initial["updateDecision"]["reason"])
        self.assertNotIn("checkedAt", initial["updateDecision"])
        published, finish = threading.Event(), threading.Event()
        def scan(snapshot, mode, progress, *, cancel_event):
            record = {"verdict": "review", "reason": "Source observed during inventory traversal"}
            progress({"itemRecords": {"0": record}, "checked": 1, "total": 1})
            published.set()
            finish.wait(4)
            return {"itemRecords": {"0": record}, "queried": 1, "errors": []}

        self.service.api_key = "fixture"
        with patch("mo2_update_arsenal.bridge.UpdateScanner.scan", side_effect=scan):
            try:
                self.assertTrue(self.service.begin_scan("deep", "target")["ok"])
                self.assertTrue(published.wait(3))
                page = self.service.agent_mods(dict(profile, offset=[str(first["nextOffset"])], revision=[revision]))
                self.assertTrue(page["ok"])
                self.assertEqual(page["revision"], revision)
                changed = self.service.agent_evidence(selection)
                self.assertEqual(changed["updateDecision"]["verdict"], "review")
                self.assertNotEqual(changed["evidenceRevision"], initial["evidenceRevision"])
                self.assertFalse(self.service.agent_evidence(dict(selection, evidenceRevision=[initial["evidenceRevision"]]))["ok"])
            finally:
                finish.set()

    def test_inventory_cursor_tracks_row_fields_order_and_native_notifications(self):
        catalog = copy.deepcopy(self.catalog)
        revision = self.service.agent_revision()
        self.service.set_snapshot(copy.deepcopy(catalog))
        self.assertEqual(self.service.agent_revision(), revision)
        for field, value in [("enabled", True), ("priority", 42), ("name", "Display name"),
                             ("mo2InternalName", "MO2 name"), ("repository", "Nexus"), ("group", "Changed")]:
            catalog["items"][0][field] = value
            self.service.set_snapshot(copy.deepcopy(catalog))
            self.assertNotEqual(self.service.agent_revision(), revision, field)
            revision = self.service.agent_revision()
        catalog["items"].reverse()
        self.service.set_snapshot(copy.deepcopy(catalog))
        self.assertNotEqual(self.service.agent_revision(), revision)
        revision = self.service.agent_revision()
        self.service.content._native_generation += 1
        self.assertNotEqual(self.service.agent_revision(), revision)

    def test_public_advisories_page_without_leaking_private_row_fields(self):
        row = self.service._items_by_id["1"]
        checked = time.time()
        findings = [{"id": "GHSA-%04d-bbbb-cccc" % n, "severity": "moderate", "title": "Public finding",
                     "url": "https://github.com/team/test/security/advisories/GHSA-%04d-bbbb-cccc" % n,
                     "package": "test", "affectedRange": "< 2.0.0", "applicability": "affected" if n == 0 else "unverified",
                     "privateDebug": "private comment"} for n in range(73)]
        findings[-1].update(title="REJECTED (not a vulnerability): retained public notice", applicability="affected")
        row.update(source={"type": "github", "url": "https://github.com/team/test", "verified": True},
                   update={"securityAdvisories": {"repository": "team/test", "state": "checked", "checkedAt": checked,
                                                  "findings": findings}})
        params = {"profileId": ["target"], "itemId": ["1"], "revision": [self.service.agent_revision()]}
        page = self.service.agent_evidence(params)
        self.assertTrue(page["ok"])
        self.assertEqual(page["health"]["securityAdvisories"]["affectedCount"], 1)
        self.assertEqual(page["nextOffset"], 64)
        rows = list(page["evidence"])
        later = dict(params, offset=["64"], evidenceRevision=[page["evidenceRevision"]])
        tail = self.service.agent_evidence(later)
        self.assertIsNone(tail["nextOffset"])
        rows.extend(tail["evidence"])
        self.assertEqual(len(rows), page["total"])
        self.assertEqual(sum(entry["kind"] == "advisory" for entry in rows), len(findings))
        self.assertEqual(next(n for n in rows if n.get("id") == findings[-1]["id"])["applicability"], "publisher-retracted")
        self.assertEqual(self.service.snapshot()["items"][1]["modHealth"]["securityAdvisories"]["affectedCount"], 1)
        self.assertNotIn("private comment", json.dumps([page,tail]))
        findings[65]["severity"] = "high"
        self.assertFalse(self.service.agent_evidence(later)["ok"])
        for invalid in (dict(params, revision=["wrong"]), dict(params,itemId=["absent"]),
                        dict(params,profileId=["other"]), dict(params,offset=["-1"]),dict(params,offset=["64"])):
            self.assertFalse(self.service.agent_evidence(invalid)["ok"])

    def test_explicit_mo2_archive_statuses(self):
        for status in (0, 1, 1000):
            with self.subTest(status=status):
                self.assertEqual(installed_file_fields({"status":str(status)}, "", "skyrimspecialedition", "skyrimse")["mo2FileStatus"], status)
        self.assertNotIn("mo2FileStatus", installed_file_fields({}, "", "skyrimspecialedition", "skyrimse"))

    @unittest.skipUnless(os.environ.get("MODHUNTER_CONTENT_CLI"), "set MODHUNTER_CONTENT_CLI to built CLI")
    def test_real_mcp_lists_service_and_rejects_profile_change_and_writes(self):
        row = self.service._items_by_id["0"]
        row.update(nexusId=50307, installedFiles=[[50307, 252976]], installedVersion="2.6.0.0",
                   source={"type": "nexus", "verified": True, "url": "https://www.nexusmods.com/skyrimspecialedition/mods/50307"})
        files = NexusFileIndex({"files":[{"fileId":252976,"version":"2.6","name":"Bone Wolf","category":1}], "file_updates":[]})
        row["update"] = summary_from_metadata(row, {"version":"4.1"}, time.time(), files)
        endpoint = f"http://127.0.0.1:{self.service._server.server_port}/"
        with subprocess.Popen([os.environ["MODHUNTER_CONTENT_CLI"], "mcp-stdio", "--legacy-2025-11-25",
                               "--mo2", "target", endpoint], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, encoding="utf-8") as cli:
            self.addCleanup(cli.kill)
            def call(method, params, call_id=None):
                frame = {"jsonrpc": "2.0", "method": method, "params": params}
                if call_id is not None:
                    frame["id"] = call_id
                cli.stdin.write(json.dumps(frame) + "\n")
                cli.stdin.flush()
                if call_id is not None:
                    return json.loads(cli.stdout.readline())
            try:
                call("initialize", {"protocolVersion":"2025-11-25", "capabilities":{},
                                    "clientInfo":{"name":"integration-test","version":"1"}}, 1)
                call("notifications/initialized", {})
                tools = call("tools/list", {}, 2)["result"]["tools"]
                self.assertIn("mo2_mods", [tool["name"] for tool in tools])
                self.assertNotIn("mo2_set_enabled", [tool["name"] for tool in tools])
                args = {"name": "mo2_mods", "arguments": {}}
                page = call("tools/call", args, 3)["result"]["structuredContent"]
                self.assertEqual(page["total"], len(self.rows))
                tool = next(t for t in tools if t["name"]=="mo2_mod_evidence")
                self.assertTrue(tool["annotations"]["readOnlyHint"])
                evidence = {"name":"mo2_mod_evidence", "arguments":{"itemId":"0", "revision":page["revision"]}}
                result = call("tools/call",evidence,6)["result"]["structuredContent"]
                self.assertEqual(result["installed"]["installedVersion"], "2.6.0.0")
                decision = result["updateDecision"]
                self.assertEqual((decision["verdict"],decision["targetFileId"],decision["latestVersion"],decision["projectVersion"]),
                                 ("current",252976,"2.6","4.1"))
                self.assertTrue(decision["fileUrl"].endswith("file_id=252976"))
                self.assertFalse(decision["expired"])
                self.assertNotIn("private comment",json.dumps(result))
                self.service.set_snapshot(dict(self.catalog, profileId="different"))
                args["arguments"] = {"offset":page["nextOffset"], "revision":page["revision"]}
                self.assertTrue(call("tools/call", args, 4)["result"]["isError"])
                self.assertTrue(call("tools/call", evidence, 7)["result"]["isError"])
                self.assertIn("error", call("tools/call", {"name":"set_mod_enabled", "arguments":{}}, 5))
            finally:
                cli.stdin.close()
                cli.wait(timeout=10)

    def test_host_scope_and_native_compare_and_set(self):
        name = "Mod 0"
        row = self.service._items_by_id["0"]
        row["mo2InternalName"] = name
        native_states = {name: 0}
        native = Mock()
        native.state.side_effect = native_states.__getitem__
        def change(target, enabled):
            native_states[target] = 2 if enabled else 0
            return True
        native.setActive.side_effect = change
        organizer = Mock()
        organizer.profileName.return_value = "Target"
        organizer.modList.return_value = native
        policy = Mock()
        policy.capture.return_value = {"modName":name}
        plugin = SimpleNamespace(org=organizer, name=lambda:"MO2 Update Arsenal", server=self.service,
                                 _content_native=policy, _background_dirty=False, _pending_refresh=None,
                                 _background_refresh=None, _schedule_refresh=Mock())
        args = {"profileId":"target", "revision":self.service.agent_revision(), "itemId":"0",
                "name":name, "expectedEnabled":False, "enabled":True}
        for setting in ("{}", '{"different":["Mod 0"]}', '{"target":"Mod 0"}', '{"target":["Mod 1"]}'):
            organizer.pluginSetting.return_value = setting
            with self.assertRaises(ValueError): set_enabled(plugin,args,2)
        native.setActive.assert_not_called()
        organizer.pluginSetting.return_value = '{"target":["Mod 0"]}'
        for values in ({"itemId":"1"}, {"revision":"old"}, {"expectedEnabled":True}, {"enabled":"true"}):
            with self.assertRaises(ValueError): set_enabled(plugin,dict(args,**values),2)
        native.setActive.assert_not_called()
        self.assertTrue(set_enabled(plugin,args,2)["enabled"])
        policy.check.assert_called()
        native.setActive.assert_called_once_with(name,True)
        with self.assertRaises(ValueError): set_enabled(plugin,args,2)
        for key, value in (("_background_dirty", True), ("_pending_refresh", object()),
                           ("_background_refresh", SimpleNamespace(busy=True))):
            old = getattr(plugin, key)
            setattr(plugin, key, value)
            with self.assertRaisesRegex(ValueError, "inventory changed"):
                set_enabled(plugin,dict(args,expectedEnabled=True,enabled=False),2)
            setattr(plugin, key, old)
        native.setActive.assert_called_once_with(name,True)

    def test_protected_mode_never_queues_or_dispatches_an_install(self):
        content = self.service.content
        old_jobs = content.jobs()
        for protection in (True, "bad"):
            result = content.start_install({"requireProtectedInstall":protection})
            self.assertFalse(result["nativeStarted"])
            self.assertFalse(result["ok"])
        with patch.dict(os.environ, {"MODHUNTER_PROTECTED_INSTALLS": "1"}):
            self.assertEqual(content.start_install({})["code"], "protected-install-unavailable")
            with self.assertRaisesRegex(RuntimeError, "cannot guarantee"):
                self.service._enqueue_install({"kind":"nexus"})
        self.assertEqual(old_jobs, content.jobs())
