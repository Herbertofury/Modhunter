"""Pure harness contracts, never Windows timing evidence."""
from __future__ import annotations
import ast
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import heapq
from pathlib import Path
import os
import sys
from tempfile import TemporaryDirectory
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from fixture import Organizer, interval_metrics, semantic_witness
from qt_heartbeat import PHASES, StageTrace, stage_verified_python, load_production, measure, phase_substages


class SimApp:
    """Only sequencing simulation; does not claim real Qt."""
    current = None
    @classmethod
    def instance(cls): return cls.current
    @staticmethod
    def topLevelWidgets(): return []

    def __init__(self, _):
        type(self).current = self
        self.queue = []
        self.order = 0
        self.result = None
        self.aboutToQuit = Slot()

    def push(self, ms, fn):
        self.order += 1
        heapq.heappush(self.queue, (time.perf_counter_ns()+int(ms*1e6), self.order, fn))

    def quit(self): self.exit(0)
    def exit(self, code): self.result = code
    def exec(self):
        while self.result is None:
            target, _, fn = heapq.heappop(self.queue)
            sleep = (target-time.perf_counter_ns())/1e9
            if sleep > 0:
                time.sleep(sleep)
            fn()
        self.aboutToQuit.activate()
        return self.result


class Slot:
    def __init__(self):
        self.functions = []
    def connect(self, callback, *_):
        self.functions.append(callback)
    def activate(self, *args):
        for cb in self.functions: cb(*args)
    def emit(self, *args):
        SimApp.current.push(0, lambda: self.activate(*args))


class Signal:
    def __init__(self, *_): pass
    def __set_name__(self, _, name): self.name = name
    def __get__(self, obj, _): return obj.__dict__.setdefault(self.name, Slot())


class SimTimer:
    @staticmethod
    def singleShot(ms, fn): SimApp.current.push(ms, fn)
    def __init__(self):
        self.timeout = Slot()
        self.active = False
    def setTimerType(self, _): pass
    def setInterval(self, delay): self.delay = delay
    def fire(self):
        if self.active:
            self.timeout.activate()
            self.singleShot(self.delay, self.fire)
    def start(self):
        self.active = True
        self.singleShot(self.delay, self.fire)
    def stop(self): self.active = False


@contextmanager
def injected_qt():
    qt = ModuleType("PyQt6")
    qt.QtCore=SimpleNamespace(QObject=object, pyqtSignal=Signal, QCoreApplication=SimApp,
        QTimer=SimTimer, qVersion=lambda:"simulated-not-native",
        Qt=SimpleNamespace(ConnectionType=SimpleNamespace(QueuedConnection=1),
                           TimerType=SimpleNamespace(PreciseTimer=1)))
    qt.QtGui=SimpleNamespace()
    qt.QtWidgets=SimpleNamespace(QApplication=SimApp)
    old_sys_path = sys.path[:]
    try:
        with patch.dict(sys.modules, {"PyQt6": qt}, clear=False):
            yield qt
    finally:
        sys.path[:] = old_sys_path
        SimApp.current = None


class HarnessTests(unittest.TestCase):
    def test_interval_brackets_not_merely_nearest_previous_tick(self):
        self.assertEqual(interval_metrics([0, 10_000_000, 120_000_000, 130_000_000],
                                          15_000_000,110_000_000),
           dict(callbackMs=95.0, intersectingGapsMs=[110.0], worstGapMs=110.0,
                p95GapMs=110.0, over50ms=1, over100ms=1))
        for wrong in ([16,120_000_000], [0,110], [0,122_000_000,119_000_000]):
            with self.assertRaises(ValueError):
                interval_metrics(wrong,2,110_000_000)

    def test_substage_brackets_are_relative_and_nested_not_counted_twice(self):
        parent = [("native-enumeration", 20, 25), ("native-slice", 26, 30), ("native-publish", 75, 99)]
        spans = [
            ("native-attach", "qt-owner", 5, 8),  # another phase
            ("worker-projection", "worker", 31, 73),
            ("native-attach", "qt-owner", 76, 82),
            ("service-commit", "qt-owner", 83, 96),
        ]
        self.assertEqual(phase_substages("forced", 10, 100, parent, spans), {
            "ownerParentWindow": "native-publish",
            "owner": [{"name": "native-attach", "enterNs": 66, "exitNs": 72, "wallNs": 6},
                      {"name": "service-commit", "enterNs": 73, "exitNs": 86, "wallNs": 13}],
            "worker": [{"name": "worker-projection", "enterNs": 21, "exitNs": 63, "wallNs": 42}],
        })

    def test_substage_counts_reject_duplicate_overlap_or_unbounded_worker(self):
        parent = [("native-enumeration", 20, 25), ("native-slice", 26, 30), ("native-publish", 75, 99)]
        worker = ("worker-projection", "worker", 31, 73)
        attach = ("native-attach", "qt-owner", 76, 82)
        commit = ("service-commit", "qt-owner", 83, 96)
        for rows, reasoning in (
            ([worker, attach, commit, commit], "duplicate owner"),
            ([worker, worker, attach, commit], "duplicate worker"),
            ([attach, commit], "worker projection"),
            ([worker, attach, ("service-commit", "qt-owner", 80, 96)], "Overlapping owner"),
            ([worker, attach, ("service-commit", "qt-owner", 83, 101)], "crosses phase"),
            ([worker, ("native-attach", "qt-owner", 74, 82), commit], "escaped"),
            ([("worker-projection", "worker", 25, 70), attach, commit], "overlapped"),
            ([("worker-projection", "worker", 31, 76), attach, commit], "overlapped"),
        ):
            with self.subTest(reasoning=reasoning), self.assertRaisesRegex(ValueError, reasoning):
                phase_substages("forced", 10, 100, parent, rows)

    def test_legacy_substages_have_no_worker_and_startup_is_not_commit(self):
        parent = [("startup", 10, 90)]
        rows = [("native-attach", "qt-owner", 35, 40),
                ("service-construction", "qt-owner", 55, 60),
                ("service-start", "qt-owner", 65, 89)]
        got = phase_substages("startup", 2, 98, parent, rows)
        self.assertEqual(got["worker"], [])
        self.assertEqual(got["ownerParentWindow"], "startup")
        self.assertEqual([w["name"] for w in got["owner"]],
                         ["native-attach", "service-construction", "service-start"])
        with self.assertRaisesRegex(ValueError, "unexpectedly ran"):
            phase_substages("startup", 2, 98, parent, rows+[("worker-projection", "worker", 20, 29)])

    def test_substage_wrappers_preserve_thread_returns_and_never_run_on_wrong_thread(self):
        clock = iter((101, 107, 115, 132, 150, 160)).__next__
        trace = StageTrace(clock)
        called = []
        def actual(value):
            called.append(value)
            if value == "raise":
                raise RuntimeError("original error")
            return value
        on_owner = trace.wrap("native-attach", actual)
        on_worker = trace.wrap("worker-projection", actual, owner=False)
        self.assertEqual(on_owner("owner"), "owner")
        with self.assertRaisesRegex(ValueError, "Wrong telemetry thread: worker-projection"):
            on_worker("wrong")
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.assertRaisesRegex(ValueError, "Wrong telemetry thread: native-attach"):
                pool.submit(on_owner, "also-wrong").result(timeout=2)
            self.assertEqual(pool.submit(on_worker, "worker").result(timeout=2), "worker")
            with self.assertRaisesRegex(RuntimeError, "original error"):
                pool.submit(on_worker, "raise").result(timeout=2)
        self.assertEqual(called, ["owner", "worker", "raise"])
        self.assertEqual(trace.snapshot(), (
            ("native-attach", "qt-owner", 101, 107),
            ("worker-projection", "worker", 115, 132),
            ("worker-projection", "worker", 150, 160),
        ))

    def test_static_real_fixture_inputs(self):
        for n, c in ((40, 4), (11207,934)):
            with TemporaryDirectory() as tmp:
                a = Organizer(Path(tmp),n)
                self.assertEqual((n,c),(len(a.mods),len(a.plugins)))
                self.assertEqual((Path(tmp)/"mods/QtFixture-00000/SKSE/Plugins/fixture.dll").read_bytes(),
                                 b"INERT NOT AN EXECUTABLE")
                self.assertFalse(any((Path(tmp)/"mods").rglob("*.esp")))

    @unittest.skipUnless(os.getenv("HEARTBEAT_SOURCE"), "Requires exact source path")
    def test_real_release_python_and_all_phases_on_simulated_event_scheduler(self):
        root = Path(os.environ["HEARTBEAT_SOURCE"]).resolve()
        n = int(os.getenv("HEARTBEAT_TEST_ROWS","40"))
        with TemporaryDirectory() as tmp, injected_qt() as qt:
            t = Path(tmp)
            s = t / "package"
            f = t / "fixture"
            f.mkdir()
            manifest = stage_verified_python(root, s)
            self.assertGreaterEqual(manifest["files"],28)
            self.assertFalse((s/"mo2_update_arsenal/bin").exists())
            org = Organizer(f,n)
            p = load_production(s)
            receipt = measure(qt.QtCore, qt.QtWidgets, p, org)
            self.assertFalse(receipt["nativeMO2"])
            self.assertEqual(tuple(x["name"] for x in receipt["phases"]),PHASES)
            self.assertTrue(all(x["input"]["mods"] == n for x in receipt["phases"]))
            self.assertTrue(all(x["input"]["pluginOrigins"] == org.expected_plugins for x in receipt["phases"]))
            self.assertTrue(all(x["calls"]["getMod"] == n for x in receipt["phases"]))
            self.assertEqual(len({x["input"]["catalogContentSha256"] for x in receipt["phases"]}),1)
            print("FUNCTIONAL_ONLY",n,org.expected_plugins,org.fixture_sha.hexdigest(),
                  receipt["phases"][0]["input"]["portableWitnessSha256"], flush=True)

    def test_only_exact_opt_in_push_is_selected(self):
        from pilot import OPT_IN_REF, REPO, opted_in
        target = {"GITHUB_ACTIONS":"true", "GITHUB_REPOSITORY":REPO,
                  "GITHUB_EVENT_NAME":"push", "GITHUB_REF":OPT_IN_REF}
        self.assertTrue(opted_in(target))
        for key,value in (("GITHUB_ACTIONS","false"), ("GITHUB_REPOSITORY","other/fork"),
                          ("GITHUB_EVENT_NAME","pull_request"), ("GITHUB_REF","refs/heads/main"),
                          ("GITHUB_REF","refs/tags/v0.12.3-test.1")):
            self.assertFalse(opted_in({**target, key:value}))

    @unittest.skipUnless(os.getenv("HEARTBEAT_CANDIDATE") and os.getenv("HEARTBEAT_SOURCE"),
                         "Requires checked exact baseline and candidate")
    def test_baseline_and_async_same_path_all_normalized_output(self):
        import shutil
        roots = [Path(os.environ[name]).resolve() for name in ("HEARTBEAT_SOURCE", "HEARTBEAT_CANDIDATE")]
        with TemporaryDirectory() as tmp:
            t = Path(tmp)
            n = int(os.getenv("HEARTBEAT_TEST_ROWS", "40"))
            out = []
            for i, root in enumerate(roots):
                with injected_qt() as qt:
                    source, f = t / f"package-{i}", t / "fixture"
                    f.mkdir()
                    stage_verified_python(root, source)
                    org = Organizer(f,n)
                    p = load_production(source)
                    originals = (p.attach_native_plugin_state, p.ArsenalService.set_snapshot,
                                 p.ArsenalService.__init__, p.ArsenalService.start)
                    receipt = measure(qt.QtCore, qt.QtWidgets, p, org)
                    self.assertEqual(originals, (p.attach_native_plugin_state, p.ArsenalService.set_snapshot,
                                                 p.ArsenalService.__init__, p.ArsenalService.start))
                    out.append(receipt["phases"])
                    if i == 1:
                        async_first_start = hasattr(p.ModUpdateArsenal, "_finish_background_startup")
                    shutil.rmtree(f)
            self.assertEqual([a["input"] for a in out[0]], [a["input"] for a in out[1]])
            for arm in out:
                for i, phase in enumerate(arm):
                    telemetry = phase["substageTelemetry"]
                    parent = phase["nativeWindows"][-1]
                    self.assertEqual(telemetry["ownerParentWindow"], parent["name"])
                    self.assertEqual([w["name"] for w in telemetry["owner"]],
                                     (["native-attach", "service-construction", "service-start"]
                                      if not i else ["native-attach", "service-commit"]))
                    self.assertEqual([w["name"] for w in telemetry["worker"]],
                                     ["worker-projection"] if parent["name"] == "native-publish" else [])
                    self.assertLessEqual(sum(w["wallNs"] for w in telemetry["owner"])/1e6,
                                         parent["callbackMs"] + 0.001)
                    self.assertTrue(all("intersectingGapsMs" not in w
                                        for w in telemetry["owner"]+telemetry["worker"]))
            self.assertEqual([[w["name"] for w in a["nativeWindows"]] for a in out[0]],
                             [["startup"], ["synchronous"], ["synchronous"]])
            candidate_windows = [[w["name"] for w in a["nativeWindows"]] for a in out[1]]
            if not async_first_start:
                self.assertEqual(candidate_windows[0], ["startup"])
            for phase, names in zip(out[1][0 if async_first_start else 1:],
                                    candidate_windows[0 if async_first_start else 1:]):
                self.assertEqual(names[0], "native-enumeration")
                self.assertEqual(names[-1], "native-publish")
                self.assertTrue(names[1:-1])
                self.assertEqual(set(names[1:-1]), {"native-slice"})
                self.assertGreaterEqual(phase["endToEnd"]["callbackMs"],
                                        sum(w["callbackMs"] for w in phase["nativeWindows"]) - 0.001 * len(names))
            print("FULL_NON_TIME_OUTPUT_MATCHES", n, out[0][0]["input"]["fullCatalogExceptTimestampsSha256"], flush=True)

    def test_hosted_only_no_linux_run_output(self):
        import subprocess
        with TemporaryDirectory() as t:
            dst = Path(t) / "never.json"
            p = subprocess.run([sys.executable,str(Path(__file__).with_name("qt_heartbeat.py")),
                                "--repository",t,"--output",str(dst)],
                               capture_output=True, text=True)
            self.assertNotEqual(p.returncode,0)
            self.assertIn("Windows hosted runner only",p.stderr)
            self.assertFalse(dst.exists())


class NativeReadinessObserverTests(unittest.TestCase):
    """Execute the stock smoke observer methods, not an actual MO2 listener."""

    def setup_observer(self, temp):
        import urllib.parse
        from unittest.mock import Mock

        runner = Path(__file__).resolve().parent.parent / "mo2_smoke_plugin/modhunter_native_smoke/runner.py"
        program = ast.parse(runner.read_text(encoding="utf-8"))
        classes = [node for node in program.body if isinstance(node, ast.ClassDef)
                   and node.name in ("LoopbackUrlCapture", "NativeSmokePlugin")]
        clock = SimpleNamespace(now=0, monotonic=lambda: clock.now)
        app = SimApp([])
        service_slots = {}

        class Product:
            def __init__(self):
                self.server = None
                self._ui_initialized = True
                self._startup_attempts = 1
                self._subscription_errors = []
                self.display_calls = 0
                self.org = SimpleNamespace(basePath=lambda: temp, pluginSetting=lambda *_: True,
                    setPluginSetting=Mock(), managedGame=lambda: SimpleNamespace(gameName=lambda: "fixture-game"),
                    profileName=lambda: "ready-fixture")

            def _publish_diagnostics(self):
                pass

            def display(self):
                self.display_calls += 1  # Product completes this action asynchronously.

        class Worker:
            def __init__(self, *, target, args, **_):
                self.args = args
                self.starts = 0

            def start(self):
                self.starts += 1

        import traceback
        scope = dict(
            ModUpdateArsenal=Product, time=clock, utc_now=lambda: "fixture", Path=Path,
            os=SimpleNamespace(environ={"MODHUNTER_NATIVE_SMOKE_INSTANCE": temp}), urllib=urllib, traceback=traceback,
            GAME_NAME="fixture-game", PROFILE_NAME="ready-fixture", EXPECTED_MODS=range(2),
            exercise_native_nexus_binding=lambda _: {}, threading=SimpleNamespace(
                Lock=lambda: object(), Thread=Worker),
            QtCore=SimpleNamespace(QObject=object, pyqtSignal=Signal, QUrl=object, QTimer=SimTimer,
                pyqtSlot=lambda *_: lambda function: function,
                Qt=SimpleNamespace(ConnectionType=SimpleNamespace(QueuedConnection=1))),
            QtGui=SimpleNamespace(QDesktopServices=SimpleNamespace(
                setUrlHandler=lambda scheme, receiver, slot: service_slots.update({scheme: getattr(receiver, slot)}))),
            QtWidgets=SimpleNamespace(QApplication=SimpleNamespace(activeModalWidget=lambda: None)),
            find_mo2_view=lambda _: (object(), SimpleNamespace(
                  model=lambda: SimpleNamespace(rowCount=lambda: 2))),
        )
        exec(compile(ast.Module(body=classes, type_ignores=[]), str(runner), "exec"), scope)
        p = scope["NativeSmokePlugin"]()
        p._on_qt_thread = lambda: True
        p._native_nexus = object()
        p._exercise_http = lambda *_: None
        p._finish = Mock(side_effect=lambda _: setattr(p, "_finished", True))
        self.addCleanup(setattr, SimApp, "current", None)
        return app, p, clock, service_slots

    def drain(self, app):
        while app.queue:
            _, _, run = heapq.heappop(app.queue)
            run()

    def test_delayed_listener_and_display_events_do_not_run_early_or_twice(self):
        with TemporaryDirectory() as t:
            app, smoke, clock, sink = self.setup_observer(t)
            smoke._ui_ready(object())
            self.assertIn("http", sink)
            self.drain(app)
            self.assertEqual(smoke.display_calls, 0)
            self.assertIsNone(smoke._worker)
            smoke.server = SimpleNamespace(_server=object(), _stopped=False, token="fixture-session-token")
            smoke._publish_diagnostics()
            smoke._publish_diagnostics()
            self.drain(app)
            self.assertEqual(smoke.display_calls, 1)
            self.assertIsNone(smoke._worker)
            sink["http"](SimpleNamespace(toString=lambda: "http://127.0.0.1:1234/"))
            self.drain(app)
            self.assertEqual(smoke._worker.starts, 1)
            self.assertEqual(smoke._worker.args, ("http://127.0.0.1:1234/", "fixture-session-token"))
            self.assertTrue(all(k["passed"] for k in smoke._checks))
            smoke._finish.assert_not_called()

    def test_stale_event_or_placeholder_cannot_open_and_existing_deadline_still_fails(self):
        with TemporaryDirectory() as t:
            app, smoke, clock, _ = self.setup_observer(t)
            smoke._ui_ready(object())
            smoke._publish_diagnostics()  # e.g. after a canceled startup: no listener.
            smoke.server = SimpleNamespace(_server=None, _stopped=False)
            smoke._publish_diagnostics()
            self.drain(app)
            self.assertEqual(smoke.display_calls, 0)
            clock.now = 90
            smoke._check_liveness()
            smoke._finish.assert_not_called()
            clock.now = 90.001
            smoke._check_liveness()
            smoke._finish.assert_called_once_with(
                {"ok": False, "error": "Native smoke exceeded its bounded deadline"})

    def test_ready_retry_or_automatic_browser_still_fails_before_manual_action(self):
        for attempts, auto_url in ((2, False), (1, True)):
            with self.subTest(attempts=attempts, auto_url=auto_url), TemporaryDirectory() as t:
                app, smoke, _, sink = self.setup_observer(t)
                smoke._ui_ready(object())
                if auto_url:
                    sink["http"](SimpleNamespace(toString=lambda: "http://127.0.0.1:1234/"))
                smoke._startup_attempts = attempts
                smoke.server = SimpleNamespace(_server=object(), _stopped=False, token="ignored")
                smoke._publish_diagnostics()
                self.drain(app)
                self.assertEqual(smoke.display_calls, 0)
                self.assertIsNone(smoke._worker)
                self.assertIn("native_bridge_autostart", smoke._finish.call_args.args[0]["error"])


if __name__ == "__main__":
    unittest.main()
