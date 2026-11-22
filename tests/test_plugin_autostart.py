"""Production lifecycle methods with explicit event delivery; native CI tests MO2."""
from __future__ import annotations
import ast
import inspect
import logging
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from mo2_update_arsenal.bridge import ArsenalService
from mo2_update_arsenal.model import LocalMetadataCache, take_snapshot
from mo2_update_arsenal.plugin_intelligence import attach_native_plugin_state
from mo2_update_arsenal.download_ids import download_id
from mo2_update_arsenal.content_recovery import NativeContent

SOURCE=Path(__file__).resolve().parents[1]/"mo2_update_arsenal/plugin.py"
def plugin_type(timers):
    node=next(n for n in ast.parse(SOURCE.read_text()).body if isinstance(n,ast.ClassDef) and n.name=="ModUpdateArsenal")
    node.bases=[ast.Name(id="object",ctx=ast.Load())]
    tree=ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[]))
    scope={"time":time,"take_snapshot":take_snapshot,"attach_native_plugin_state":attach_native_plugin_state,
        "LocalMetadataCache":LocalMetadataCache,"NativeContent":NativeContent,"mobase":SimpleNamespace(IOrganizer=object),
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
        self.plugin.server=SimpleNamespace(content=Mock())
        for callback in (self.plugin._on_mod_states_changed,self.plugin._on_plugin_states_changed):
            self.assertEqual(len(inspect.signature(callback).parameters),1)
            callback({"A":1,"B":2})
        self.assertEqual(len(self.timers),1)
        self.assertEqual(self.timers[0][0],150)
        self.plugin.server.content.invalidate.assert_called_once()
    def test_explicit_refresh_subsumes_queued_callback_without_rescanning_large_profile(self):
        rows, plugin_rows = 2500, 1000
        mod_names = [f"Fixture {i}" for i in range(rows)]
        names = [f"Fixture-{i}.esp" for i in range(plugin_rows)]
        counters = {"mod": 0, "resolve": 0}
        def get_mod(name):
            counters["mod"] += 1
            return SimpleNamespace(name=name, version="1.0", nexusId=42)
        def resolve(name):
            counters["resolve"] += 1
            return "X:/synthetic/" + name  # MO2-owned path fixture, not read from disk
        mods = SimpleNamespace(allModsByProfilePriority=lambda _: mod_names,
                               getMod=get_mod, priority=lambda _: 1, state=lambda _: 2)
        plugins = SimpleNamespace(pluginNames=lambda: names, state=lambda _: 2,
                                  loadOrder=lambda _: 1, origin=lambda _: mod_names[0])
        self.plugin.org = SimpleNamespace(managedGame=lambda: SimpleNamespace(gameNexusName="skyrimspecialedition"),
                                          profileName=lambda: "Fixture A", modList=lambda: mods,
                                          profile=lambda: object(), pluginList=lambda: plugins, resolvePath=resolve)
        working = tempfile.TemporaryDirectory()
        self.addCleanup(working.cleanup)
        service = ArsenalService("fixture", Path(working.name) / "cache.json", Mock(),
                                 {"profileId": "fixture", "profileName": "Fixture A", "items": []})
        self.addCleanup(service.stop)
        self.plugin.server = service
        self.plugin._publish_diagnostics = Mock()
        with (patch.object(service, "_publish_discovery") as discovery,
              patch.object(service.content, "invalidate", wraps=service.content.invalidate) as content_invalidate,
              patch.object(service.intelligence, "invalidate", wraps=service.intelligence.invalidate) as invalidate,
              patch.object(service.intelligence, "auto_audit") as audit):
            self.plugin._on_plugins_refreshed()
            self.plugin._on_mod_states_changed({"Fixture 1": 2})
            self.assertEqual(len(self.timers), 1, "the original event burst coalesces")
            result = self.plugin._refresh_snapshot(force_metadata=True)
            self.assertTrue(result["ok"])
            first = result["catalog"]
            self.assertEqual(len(first["items"]), rows)
            self.assertEqual(len(first["items"][0]["localAnalysis"]["plugins"]), plugin_rows)
            self.timers.pop(0)[1]()
            self.assertEqual({**counters, "discovery": discovery.call_count,
                "audit_invalidations": invalidate.call_count, "audit_triggers": audit.call_count},
                {"mod": rows, "resolve": plugin_rows, "discovery": 1, "audit_invalidations": 1, "audit_triggers": 1},
                "the subsumed timer must not repeat inventory/VFS, snapshot publication or audit scheduling")
            content_invalidate.assert_called_once()
            service.ui_call.assert_not_called()
        self.assertEqual(self.timers, [])

    def test_older_timer_leaves_a_later_event_pending(self):
        self.plugin.org = object()
        self.plugin.server = Mock()
        self.plugin._publish_diagnostics = Mock()
        first = {"items": [], "profileName": "Fixture A"}
        next_profile = {"items": [], "profileName": "Fixture B"}
        def capture():
            # An event emitted during a native capture is not covered by a
            # snapshot that was already being built.
            self.plugin._on_profile_changed("Fixture A", "Fixture B")
            return first
        self.plugin._on_plugins_refreshed()
        namespace = self.kind._refresh_snapshot.__globals__
        with patch.dict(namespace, {"take_snapshot": lambda *a, **k: capture() if k["force_metadata"] else next_profile,
                                    "attach_native_plugin_state": Mock()}):
            self.assertTrue(self.plugin._refresh_snapshot(force_metadata=True)["ok"])
            self.assertEqual(len(self.timers), 2)
            self.timers.pop(0)[1]()
            self.plugin.server.set_snapshot.assert_called_once_with(first)
            self.timers.pop(0)[1]()
        self.assertEqual(self.plugin.server.set_snapshot.call_count, 2)
        self.plugin.server.set_snapshot.assert_called_with(next_profile)
        self.plugin.server.content.invalidate.assert_called_once_with(reason="profile")

    def test_failed_explicit_capture_does_not_drop_original_event_or_retry_forever(self):
        self.plugin.org = object()
        self.plugin.server = Mock()
        self.plugin._publish_diagnostics = Mock()
        self.plugin._on_plugins_refreshed()
        second = {"items": [], "profileName": "Fixture"}
        captures = Mock(side_effect=[OSError("explicit fixture failure"), second, OSError("scheduled fixture failure")])
        with patch.dict(self.kind._refresh_snapshot.__globals__, {
                "take_snapshot": captures, "attach_native_plugin_state": Mock()}):
            with self.assertLogs("modhunter-lifecycle-test", level="WARNING"):
                self.assertFalse(self.plugin._refresh_snapshot(force_metadata=True)["ok"])
            while self.timers: self.timers.pop(0)[1]()
            self.plugin.server.set_snapshot.assert_called_once_with(second)
            self.plugin._on_plugins_refreshed()
            with self.assertLogs("modhunter-lifecycle-test", level="WARNING"):
                self.timers.pop(0)[1]()
        self.assertEqual(captures.call_count, 3)
        self.assertEqual(self.timers, [])

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
            onAboutToRun=owner.onAboutToRun,onFinishedRun=owner.onFinishedRun,
            modList=lambda:owner,pluginList=lambda:owner,downloadManager=lambda:owner,
            persistent=lambda *args:"unknown",onUserInterfaceInitialized=owner.onUserInterfaceInitialized)
        with self.assertLogs("modhunter-lifecycle-test",level="WARNING"):
            self.assertTrue(self.plugin.init(organizer))
        names={name for name,_ in called}
        self.assertTrue({"onModStateChanged","onPluginStateChanged","onDownloadComplete","onDownloadFailed","onUserInterfaceInitialized","onAboutToRun","onFinishedRun"}<=names)
        self.assertTrue(self.plugin._content_launch_guard)
        self.assertEqual(len(self.plugin._subscription_errors),1)
    def test_shutdown_removes_server_and_never_schedules_refresh(self):
        server=Mock();self.plugin.server=server;self.plugin._shutdown()
        server.stop.assert_called_once();self.assertIsNone(self.plugin.server)
        self.plugin._schedule_refresh();self.assertEqual(self.timers,[])

    def test_pending_refresh_after_shutdown_is_a_noop(self):
        server = self.plugin.server = Mock()
        self.plugin._on_plugins_refreshed()
        self.assertEqual(len(self.timers), 1)
        self.plugin._shutdown()
        with patch.object(self.plugin, "_refresh_snapshot") as capture:
            self.timers.pop(0)[1]()
        capture.assert_not_called()
        server.stop.assert_called_once()

if __name__=="__main__":unittest.main()
