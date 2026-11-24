"""Genuine stdio-to-HTTP coverage against the production service in an isolated profile."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

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
