"""Production lifecycle methods with explicit event delivery; native CI tests MO2."""
from __future__ import annotations
import ast
import inspect
import logging
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from mo2_update_arsenal.model import LocalMetadataCache
from mo2_update_arsenal.download_ids import download_id

SOURCE=Path(__file__).resolve().parents[1]/"mo2_update_arsenal/plugin.py"
def plugin_type(timers):
    node=next(n for n in ast.parse(SOURCE.read_text()).body if isinstance(n,ast.ClassDef) and n.name=="ModUpdateArsenal")
    node.bases=[ast.Name(id="object",ctx=ast.Load())]
    tree=ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[]))
    scope={"LocalMetadataCache":LocalMetadataCache,"mobase":SimpleNamespace(IOrganizer=object),
        "LOG":logging.getLogger("modhunter-lifecycle-test"),"parse_download_id":download_id,
        "QtCore":SimpleNamespace(QTimer=SimpleNamespace(singleShot=lambda ms,fn:timers.append((ms,fn))),
            QCoreApplication=SimpleNamespace(instance=lambda:None)),
        "Dispatcher":lambda handler:SimpleNamespace(submitted=SimpleNamespace(emit=handler)),
        "NativeNexusGateway":lambda org:SimpleNamespace()}
    exec(compile(tree,str(SOURCE),"exec"),scope)
    return scope["ModUpdateArsenal"]
class StartupTests(unittest.TestCase):
    def setUp(self):
        self.timers=[]; self.kind=plugin_type(self.timers);self.plugin=self.kind()
    def test_map_callbacks_have_exactly_one_argument_and_coalesce(self):
        self.plugin.server=object()
        for callback in (self.plugin._on_mod_states_changed,self.plugin._on_plugin_states_changed):
            self.assertEqual(len(inspect.signature(callback).parameters),1)
            callback({"A":1,"B":2})
        self.assertEqual(len(self.timers),1)
        self.assertEqual(self.timers[0][0],150)
    def test_ui_ready_starts_without_opening_the_tool_or_browser(self):
        with patch.object(self.plugin,"_ensure_service") as ensure:
            self.plugin._start_automatically();ensure.assert_not_called()
            self.plugin._on_ui_initialized(object())
            self.assertEqual(self.timers[0][0],0)
            self.timers.pop()[1]();ensure.assert_called_once()
    def test_startup_failure_has_bounded_retries_and_shutdown_stops_retry(self):
        with patch.object(self.plugin,"_ensure_service",side_effect=OSError("fixture")),self.assertLogs("modhunter-lifecycle-test",level="WARNING"):
            self.plugin._on_ui_initialized(object())
            while self.timers:self.timers.pop(0)[1]()
        self.assertEqual(self.plugin._startup_attempts,5)
        self.assertEqual(self.plugin._snapshot_error,"OSError")
        self.plugin._shutdown()
        with patch.object(self.plugin,"_ensure_service") as ensure:
            self.plugin._start_automatically();ensure.assert_not_called()
    def test_optional_subscription_failure_does_not_remove_other_hooks(self):
        called=[]
        class Owner:
            def onProfileChanged(self,fn):raise RuntimeError("optional missing hook")
            def __getattr__(self,name):
                return lambda fn:called.append((name,fn)) or True
        owner=Owner()
        organizer=SimpleNamespace(onProfileChanged=owner.onProfileChanged,
            modList=lambda:owner,pluginList=lambda:owner,downloadManager=lambda:owner,
            persistent=lambda *args:"unknown",onUserInterfaceInitialized=owner.onUserInterfaceInitialized)
        with self.assertLogs("modhunter-lifecycle-test",level="WARNING"):
            self.assertTrue(self.plugin.init(organizer))
        names={name for name,_ in called}
        self.assertTrue({"onModStateChanged","onPluginStateChanged","onDownloadComplete","onDownloadFailed","onUserInterfaceInitialized"}<=names)
        self.assertEqual(len(self.plugin._subscription_errors),1)
    def test_shutdown_removes_server_and_never_schedules_refresh(self):
        server=Mock();self.plugin.server=server;self.plugin._shutdown()
        server.stop.assert_called_once();self.assertIsNone(self.plugin.server)
        self.plugin._schedule_refresh();self.assertEqual(self.timers,[])

if __name__=="__main__":unittest.main()
