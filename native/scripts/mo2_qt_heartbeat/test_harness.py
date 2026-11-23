"""Pure harness contracts, never Windows timing evidence."""
from __future__ import annotations
from contextlib import contextmanager
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
from qt_heartbeat import PHASES, stage_verified_python, load_production, measure


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
    def __init__(self, _): pass
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
                    receipt = measure(qt.QtCore, qt.QtWidgets, p, org)
                    out.append(receipt["phases"])
                    shutil.rmtree(f)
            self.assertEqual([a["input"] for a in out[0]], [a["input"] for a in out[1]])
            self.assertEqual([[w["name"] for w in a["nativeWindows"]] for a in out[0]],
                             [["startup"], ["synchronous"], ["synchronous"]])
            candidate_windows = [[w["name"] for w in a["nativeWindows"]] for a in out[1]]
            self.assertEqual(candidate_windows[0], ["startup"])
            for names in candidate_windows[1:]:
                self.assertEqual(names[0], "native-enumeration")
                self.assertEqual(names[-1], "native-publish")
                self.assertTrue(names[1:-1])
                self.assertEqual(set(names[1:-1]), {"native-slice"})
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


if __name__ == "__main__":
    unittest.main()
