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
from mo2_update_arsenal.model import LocalMetadataCache, call_str, take_snapshot
from mo2_update_arsenal.refresh_work import RefreshMetadataCache, RefreshWork
from mo2_update_arsenal.snapshot_projection import begin_snapshot_capture, capture_snapshot, project_snapshot
from mo2_update_arsenal.plugin_intelligence import attach_native_plugin_state
from mo2_update_arsenal.download_ids import download_id
from mo2_update_arsenal.content_recovery import NativeContent

SOURCE=Path(__file__).resolve().parents[1]/"mo2_update_arsenal/plugin.py"
def plugin_type(timers):
    node=next(n for n in ast.parse(SOURCE.read_text()).body if isinstance(n,ast.ClassDef) and n.name=="ModUpdateArsenal")
    node.bases=[ast.Name(id="object",ctx=ast.Load())]
    tree=ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[]))
    scope={"time":time,"call_str":call_str,"take_snapshot":take_snapshot,"attach_native_plugin_state":attach_native_plugin_state,
        "RefreshMetadataCache":RefreshMetadataCache,"RefreshWork":RefreshWork,"capture_snapshot":capture_snapshot,"project_snapshot":project_snapshot,
        "begin_snapshot_capture":begin_snapshot_capture,
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



class BackgroundRefreshIntegrationTests(unittest.TestCase):
    def setUp(self):
        from queue import Queue
        from threading import Event, get_ident
        from mo2_update_arsenal.bridge import UIRequest
        self.UIRequest = UIRequest
        self.owner, self.go = get_ident(), Event()
        self.port, self.entered = Queue(), Queue()
        self.timers = []
        kind = plugin_type(self.timers)
        self.namespace = kind.init.__globals__
        def dispatcher(handler):
            return SimpleNamespace(submitted=SimpleNamespace(
                emit=self.port.put if handler.__name__ == "_on_background_refresh_completed" else handler))
        self.namespace["Dispatcher"] = dispatcher
        self.namespace["attach_native_plugin_state"] = Mock()
        self.original = self.namespace["project_snapshot"]
        def project(*args, **kwargs):
            self.assertNotEqual(get_ident(), self.owner)
            self.entered.put(kwargs["force_metadata"])
            self.go.wait(2)
            return self.original(*args, **kwargs)
        self.namespace["project_snapshot"] = project
        self.profile = "Fixture A"
        native = SimpleNamespace(name="A mod", version="1.0", nexusId=42)
        modlist = Mock()
        modlist.allModsByProfilePriority.side_effect = lambda _: ["A mod"]
        modlist.getMod.return_value = native
        modlist.priority.return_value = 0
        modlist.state.return_value = 2
        org = Mock()
        org.managedGame.side_effect = lambda: SimpleNamespace(gameNexusName="skyrimspecialedition")
        org.modList.side_effect = lambda: modlist
        org.profileName.side_effect = lambda: self.profile
        org.profilePath.side_effect = lambda: "/fake/profiles/" + self.profile
        org.basePath.side_effect = lambda: "/fake"
        org.persistent.return_value = "unknown"
        self.plugin = kind()
        self.plugin.init(org)
        self.service = SimpleNamespace(set_snapshot=Mock(), content=Mock(), stop=Mock())
        self.plugin.server = self.service
        self.plugin._publish_diagnostics = Mock()
        self.addCleanup(self.plugin._shutdown)
        self.addCleanup(self.go.set)

    def drain(self):
        self.plugin._on_background_refresh_completed(self.port.get(timeout=2))

    def pump_capture(self):
        while self.timers and self.timers[0][0] == 0:
            self.timers.pop(0)[1]()

    def test_explicit_http_request_hands_off_without_blocking_owner(self):
        request = self.UIRequest("refresh", {})
        self.plugin._dispatch(request)
        self.pump_capture()
        self.assertTrue(self.entered.get(timeout=2))
        self.assertEqual(request.state, "running")
        self.assertFalse(request.event.is_set())
        self.service.set_snapshot.assert_not_called()
        guarded = self.UIRequest("download", {})
        self.plugin._dispatch(guarded)
        self.assertEqual(guarded.state, "completed")
        self.assertIn("refreshing", guarded.result["error"])
        self.plugin._content_native = SimpleNamespace(handle=Mock())
        recovery = self.UIRequest("content-refresh", {})
        self.plugin._dispatch(recovery)
        self.plugin._content_native.handle.assert_called_once_with(recovery, self.namespace["QtCore"].QTimer)
        acquired = self.UIRequest("content-lease", {})
        self.plugin._dispatch(acquired)
        self.assertIn("refreshing", acquired.result["error"])
        self.go.set()
        self.drain()
        self.assertEqual(request.state, "completed")
        self.assertTrue(request.result["ok"])
        self.assertEqual(request.result["count"], 1)
        self.assertEqual(request.result["catalog"]["items"][0]["name"], "A mod")
        self.service.set_snapshot.assert_called_once()

    def test_profile_switch_invalidates_delayed_reply_and_requeues_owned_automatic(self):
        request = self.UIRequest("refresh", {})
        self.plugin._dispatch(request)
        self.pump_capture()
        self.assertTrue(self.entered.get(timeout=2))
        for name in ("Fixture B", "Fixture A"):
            previous, self.profile = self.profile, name
            self.plugin._on_profile_changed(previous, name)
        self.assertEqual(len(self.timers), 1)
        self.timers.pop(0)[1]()
        self.go.set()
        self.drain()
        self.assertFalse(request.result["ok"])
        self.service.set_snapshot.assert_not_called()
        self.pump_capture()
        self.assertFalse(self.entered.get(timeout=2))
        self.drain()
        self.service.set_snapshot.assert_called_once()
        self.assertEqual(self.service.set_snapshot.call_args.args[0]["profileName"], "Fixture A")

class BackgroundStartupIntegrationTests(unittest.TestCase):
    """Exact plugin/coordinator/metadata methods; controlled Qt/Mobase and bridge sockets."""

    def setUp(self):
        from queue import Queue
        from threading import Event, get_ident
        self.owner = get_ident()
        self.result_port, self.entered = Queue(), Queue()
        self.timers = []
        self.release_worker = Event()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root / "web").mkdir()
        (root / "web" / "index.html").write_text("<main>Fixture dashboard</main>", encoding="utf-8")
        mod = root / "real metadata"
        mod.mkdir()
        (mod / "meta.ini").write_text("[General]\nurl=https://github.com/example/owned-fixture\n",
                                     encoding="utf-8")
        kind = plugin_type(self.timers)
        ns = kind.init.__globals__
        self.address = "http://127.0.0.1:12345/"
        self.messages = Mock()
        self.service = SimpleNamespace(start=Mock(return_value=self.address), stop=Mock(), content=Mock(),
                                       set_snapshot=Mock())
        self.created = Mock(return_value=self.service)

        def dispatcher(handler):
            return SimpleNamespace(submitted=SimpleNamespace(
                emit=self.result_port.put if handler.__name__ == "_on_background_refresh_completed" else handler))

        def project(capture, **kwargs):
            self.assertNotEqual(get_ident(), self.owner)
            self.entered.put(capture)
            if not self.release_worker.wait(3):
                raise AssertionError("test did not release snapshot worker")
            return project_snapshot(capture, **kwargs)

        self.attach = Mock(side_effect=lambda org, snap: self.assertEqual(get_ident(), self.owner))
        ns.update(Dispatcher=dispatcher, project_snapshot=project, __file__=str(root / "plugin.py"), Path=Path,
                  ArsenalService=self.created, attach_native_plugin_state=self.attach,
                  QtWidgets=SimpleNamespace(QMessageBox=self.messages),
                  NativeNexusGateway=lambda _: SimpleNamespace(files_available=False, files_error="fixture",
                                                               file_transport="unavailable"),
                  VERSION="fixture-version")
        ns["QtCore"].qVersion = lambda: "fixture"
        self.opened = Mock()
        self.synchronous = Mock(side_effect=AssertionError("production startup must not use synchronous metadata"))
        ns["take_snapshot"] = self.synchronous
        self.profile = "Fixture A"
        self.rows = [f"Row {i}" for i in range(128)]
        self.org = Mock()
        self.org.profileName.side_effect = lambda: self.profile
        self.org.profilePath.side_effect = lambda: "/test/profile/" + self.profile if self.profile else ""
        self.org.basePath.side_effect = lambda: "/test"
        self.org.persistent.return_value = "unknown"
        self.org.pluginDataPath.return_value = str(root)
        self.org.managedGame.side_effect = lambda: SimpleNamespace(gameNexusName="skyrimspecialedition")
        ml = self.org.modList.return_value
        ml.allModsByProfilePriority.side_effect = lambda _: self.rows
        ml.getMod.side_effect = lambda name: SimpleNamespace(name=name, absolutePath=str(mod), version="1.0")
        ml.priority.return_value = 0
        ml.state.return_value = 2
        self.plugin = kind()
        self.plugin.init(self.org)
        self.plugin._open_dashboard = self.opened
        self.plugin._bind_native_selection = Mock()
        self.addCleanup(self.plugin._shutdown)
        self.addCleanup(self.release_worker.set)

    def tick(self, delay=0):
        self.assertEqual(self.timers[0][0], delay)
        self.timers.pop(0)[1]()

    def pump_capture(self):
        while self.timers and self.timers[0][0] == 0:
            self.tick()

    def deliver(self):
        self.plugin._on_background_refresh_completed(self.result_port.get(timeout=3))

    def test_auto_start_yields_and_admits_complete_catalog_once_without_opening_browser(self):
        self.plugin._on_ui_initialized(object())
        self.plugin._on_plugins_refreshed()  # The queued startup absorbs it.
        self.assertEqual(len(self.timers), 1)
        self.tick()
        self.assertIsNone(self.plugin.server)
        self.assertEqual(self.org.modList.return_value.getMod.call_count, 0)
        self.tick()
        self.assertEqual(self.org.modList.return_value.getMod.call_count, 64)
        self.attach.assert_not_called()
        self.created.assert_not_called()
        self.pump_capture()
        self.assertEqual(len(self.entered.get(timeout=3).mods), 128)
        self.assertIsNone(self.plugin.server)
        self.created.assert_not_called()
        self.release_worker.set()
        self.deliver()
        self.assertIs(self.plugin.server, self.service)
        snapshot = self.created.call_args.args[3]
        self.assertEqual([r["name"] for r in snapshot["items"]], self.rows)
        self.assertTrue(all(r["source"]["url"] == "https://github.com/example/owned-fixture" for r in snapshot["items"]))
        self.service.start.assert_called_once()
        self.attach.assert_called_once_with(self.org, snapshot)
        self.synchronous.assert_not_called()
        self.opened.assert_not_called()
        self.assertEqual(self.plugin._startup_attempts, 1)
        self.assertEqual(self.timers, [])

    def test_first_tools_action_joins_auto_start_and_opens_exact_valid_address_once(self):
        self.plugin._on_ui_initialized(object())
        self.pump_capture()
        self.entered.get(timeout=3)
        self.plugin.display()
        self.plugin.display()
        self.created.assert_not_called()
        self.opened.assert_not_called()
        self.release_worker.set()
        self.deliver()
        self.service.start.assert_called_once()
        self.opened.assert_called_once_with(self.address)
        self.assertEqual(self.timers, [])

    def test_manual_start_before_ui_ready_uses_same_async_path(self):
        self.plugin.display()
        self.pump_capture()
        self.entered.get(timeout=3)
        self.release_worker.set()
        self.deliver()
        self.opened.assert_called_once_with(self.address)
        self.service.start.assert_called_once()
        self.synchronous.assert_not_called()

    def test_a_b_a_during_worker_invalidates_first_start_without_losing_menu_intent(self):
        self.plugin._on_ui_initialized(object())
        self.pump_capture()
        captured = self.entered.get(timeout=3)
        self.plugin.display()
        for target in ("Fixture B", "Fixture A"):
            before, self.profile = self.profile, target
            self.plugin._on_profile_changed(before, target)
        self.rows = ["New committed A"]
        self.release_worker.set()
        with self.assertLogs("modhunter-lifecycle-test", level="WARNING"):
            self.deliver()
        self.created.assert_not_called()
        self.opened.assert_not_called()
        self.assertIsNone(self.plugin.server)
        self.pump_capture()
        ready = self.entered.get(timeout=3)
        self.assertEqual(captured.profile_id, ready.profile_id)
        self.deliver()
        self.assertEqual(self.created.call_args.args[3]["items"][0]["name"], "New committed A")
        self.service.start.assert_called_once()
        self.opened.assert_called_once_with(self.address)

    def test_attach_reentrancy_checks_epoch_before_starting_and_shutdown_drops_success(self):
        self.rows = ["Stable"]
        self.plugin._on_ui_initialized(object())
        self.plugin.display()
        self.pump_capture()
        self.entered.get(timeout=3)
        self.release_worker.set()

        def stale_attach(*args):
            self.plugin._on_profile_changed("Fixture A", "Fixture B")
            self.plugin._on_profile_changed("Fixture B", "Fixture A")

        self.attach.side_effect = stale_attach
        with self.assertLogs("modhunter-lifecycle-test", level="WARNING"):
            self.deliver()
        self.created.assert_not_called()
        self.attach.side_effect = None
        self.pump_capture()
        self.entered.get(timeout=3)
        self.plugin._shutdown()
        self.deliver()
        self.service.start.assert_not_called()
        self.opened.assert_not_called()
        self.assertIsNone(self.plugin.server)

    def test_retry_budget_is_five_and_new_manual_intent_can_try_again(self):
        self.profile = ""
        self.plugin._on_ui_initialized(object())
        self.plugin.display()
        delays = []
        with self.assertLogs("modhunter-lifecycle-test", level="WARNING") as failures:
            while self.timers:
                delays.append(self.timers[0][0])
                self.tick(delays[-1])
        self.assertEqual(delays, [0, 1000, 1000, 1000, 1000])
        self.assertEqual(len(failures.output), 5)
        self.assertEqual(self.plugin._startup_attempts, 5)
        self.messages.critical.assert_called_once()
        self.created.assert_not_called()
        self.opened.assert_not_called()
        self.profile = "Fixture A"
        self.plugin.display()
        self.pump_capture()
        self.entered.get(timeout=3)
        self.release_worker.set()
        self.deliver()
        self.opened.assert_called_once_with(self.address)
        self.assertEqual(self.plugin._startup_attempts, 1)
        self.service.start.assert_called_once()

    def test_failed_listener_does_not_publish_or_open_and_retries_on_owner(self):
        self.rows = ["Stable"]
        bad = SimpleNamespace(start=Mock(side_effect=OSError("Owned test listener failure")),
                              stop=Mock(), content=Mock())
        self.created.side_effect = [bad, self.service]
        self.plugin.display()
        self.pump_capture()
        self.entered.get(timeout=3)
        self.release_worker.set()
        with self.assertLogs("modhunter-lifecycle-test", level="WARNING"):
            self.deliver()
        bad.stop.assert_called_once()
        self.assertIsNone(self.plugin.server)
        self.opened.assert_not_called()
        self.tick(1000)
        self.pump_capture()
        self.entered.get(timeout=3)
        self.deliver()
        self.assertIs(self.plugin.server, self.service)
        self.service.start.assert_called_once()
        self.opened.assert_called_once_with(self.address)
        self.assertEqual(self.plugin._startup_attempts, 2)


if __name__ == "__main__":
    unittest.main()
