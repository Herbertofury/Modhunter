"""Exercise zero-based native handles through queue, HTTP, and persisted history."""
from __future__ import annotations
import ast
import copy
import json
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from mo2_update_arsenal.bridge import ArsenalService
from mo2_update_arsenal.download_ids import download_id
from mo2_update_arsenal.operation_journal import OperationJournal
from test_update_install_queue import SNAPSHOT

class ZeroHandleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.calls = []
        self.handle = 0
        self.early = False
        def native(request):
            self.calls.append(request.cmd)
            if request.cmd == "download":
                if self.early:
                    self.service.set_download_status(self.handle, "complete")
                request.finish({"ok": True, "downloadId": self.handle})
            elif request.cmd == "install":
                request.finish({"ok": True, "verifiedUpdate": True, "installedName": "Test Mod"})
            else:
                request.finish({"ok": False, "error": "unexpected fixture command"})
        self.service = ArsenalService("<html><!--__MO2_BOOT__--></html>", Path(self.temp.name)/"cache.json", native, copy.deepcopy(SNAPSHOT))
        self.service.fetch_files = lambda *args, **kwargs: {"ok":True,"files":[{"fileId":55}]}
        self.addCleanup(self.service.stop)
    def queue(self):
        return self.service.queue_reviewed_downloads({"profileId":SNAPSHOT["profileId"],
            "confirm":"QUEUE_REVIEWED_DOWNLOADS", "entries":[{"itemId":"live-test","modId":101,
            "fileId":55,"installedVersion":"1.0","reviewed":True}]})
    def api(self, path, payload=None, authorized=True):
        address = self.service.start()
        headers = {"Origin":address.rstrip("/"),"Content-Type":"application/json"}
        if authorized: headers["X-Arsenal-Token"] = self.service.token
        request = urllib.request.Request(address+"api/"+path,
            data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=3) as response:
            return json.load(response)
    def test_parser_distinguishes_native_zero_from_missing_and_malformed(self):
        for value in (0, "0", 1, "2147483647"):
            self.assertEqual(download_id(value), int(value))
        for value in (None, False, True, -1, 2147483648, 0.0, 1.5, "", "0.0", "-1", "wrong", [], {}):
            with self.subTest(value=value): self.assertIsNone(download_id(value))
    def test_first_download_can_complete_preview_install_and_persist(self):
        queued = self.queue()
        self.assertEqual(queued["queued"][0]["downloadId"], 0, queued)
        self.service.set_download_status(0, "complete")
        self.assertTrue(self.api("install-preview?downloadId=0")["canInstall"])
        self.assertTrue(self.api("install", {"downloadId":0,"confirm":"INSTALL"})["ok"])
        deadline = time.monotonic()+2
        while self.service.downloads()[0]["status"] != "installed" and time.monotonic()<deadline:
            time.sleep(.005)
        self.assertEqual(self.service.downloads()[0]["status"], "installed")
        self.assertEqual(self.calls, ["download","install"])
        history = OperationJournal(self.service.journal.path,"new-session").profile(SNAPSHOT["profileId"])
        self.assertEqual(history["records"][0]["downloadId"],0)
        self.assertFalse(history["records"][0]["actionable"])
    def test_missing_or_invalid_http_id_never_selects_first_download(self):
        self.queue(); self.service.set_download_status(0,"complete")
        for query in ("install-preview", "install-preview?downloadId=oops", "install-preview?downloadId=-1"):
            self.assertFalse(self.api(query)["canInstall"])
        for value in (None,False,True,-1,0.0,[],{},""):
            with self.subTest(value=value):
                self.assertFalse(self.api("install",{"downloadId":value,"confirm":"INSTALL"})["ok"])
        self.assertFalse(self.api("install",{"confirm":"INSTALL"})["ok"])
        self.assertEqual(self.calls,["download"])
    def test_completion_before_native_return_is_kept_for_zero(self):
        self.early=True
        self.assertTrue(self.queue()["ok"])
        self.assertEqual(self.service.downloads()[0]["status"],"complete")
    def test_boolean_or_fractional_native_event_cannot_alias_zero(self):
        self.queue()
        for value in (False, True, 0.0, "0", None):
            self.service.set_download_status(value,"complete")
        self.assertEqual(self.service.downloads()[0]["status"],"downloading")
    def test_malformed_native_return_is_not_success_or_a_ticket(self):
        self.handle=None
        result=self.queue()
        self.assertEqual(result["queued"],[])
        self.assertTrue(result["failed"])
        self.assertEqual(self.service.downloads(),{})
    def test_first_failed_download_can_be_retried(self):
        self.queue(); self.service.set_download_status(0,"failed"); self.handle=1
        result=self.service.retry_failed_download(0)
        self.assertTrue(result["ok"],result)
        self.assertIn(1,self.service.downloads())
    def test_diagnostics_requires_the_same_session_authorization(self):
        with self.assertRaises(urllib.error.HTTPError) as denied:
            self.api("diagnostics",authorized=False)
        self.assertEqual(denied.exception.code,403)
        report=self.api("diagnostics")
        self.assertTrue(report["ok"])
        self.assertNotIn(self.service.token,json.dumps(report))
    def test_real_plugin_method_accepts_zero_but_not_declined_handles(self):
        source=Path(__file__).resolve().parents[1]/"mo2_update_arsenal/plugin.py"
        cls=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.ClassDef) and n.name=="ModUpdateArsenal")
        method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=="_download")
        scope={"parse_download_id":download_id}
        exec(compile(ast.Module(body=[method],type_ignores=[]),str(source),"exec"),scope)
        plugin=SimpleNamespace(server=SimpleNamespace(has_nexus_mod=lambda mid:True),
            org=SimpleNamespace(downloadManager=lambda:SimpleNamespace(startDownloadNexusFile=lambda mid,fid:self.handle)))
        for value,good in ((0,True),(1,True),(-1,False),(None,False),(True,False),(0.0,False)):
            self.handle=value
            self.assertEqual(scope["_download"](plugin,101,55)["ok"],good)

if __name__=="__main__": unittest.main()
