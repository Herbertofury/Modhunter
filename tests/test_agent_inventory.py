"""Genuine stdio-to-HTTP coverage against the production service in an isolated profile."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mo2_update_arsenal.agent_control import set_enabled

from mo2_update_arsenal.bridge import ArsenalService


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

    @unittest.skipUnless(os.environ.get("MODHUNTER_CONTENT_CLI"), "set MODHUNTER_CONTENT_CLI to built CLI")
    def test_real_mcp_lists_service_and_rejects_profile_change_and_writes(self):
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
                self.service.set_snapshot(dict(self.catalog, profileId="different"))
                args["arguments"] = {"offset":page["nextOffset"], "revision":page["revision"]}
                self.assertTrue(call("tools/call", args, 4)["result"]["isError"])
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
                                 _content_native=policy, _dirty=False, _schedule_refresh=Mock())
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
        plugin._dirty = True
        with self.assertRaises(ValueError): set_enabled(plugin,dict(args,expectedEnabled=True,enabled=False),2)

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
