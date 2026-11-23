"""Run the released Python capture and queued refresh under actual Qt timers.

Windows hosted diagnostic; deliberately no Mod Organizer process, registry or
native mobase. The organizer substitution is strict, and there is no mock Qt
fallback. Outputs are not release nor installed-profile evidence.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
from threading import Lock, get_ident
import time
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from fixture import ACTIVE, Organizer, PERIOD_MS, SIZES, interval_metrics, semantic_witness

EXPECTED_TEST1_COMMIT = "b692f37920e4eeadc272c9b36900eae3189dcc31"
EXPECTED_QT = "6.11.1"
EXPECTED_PYQT = "6.11.0"
PHASES = ("startup", "ordinary", "forced")


def check(condition, reason):
    if not condition:
        raise ValueError(reason)


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args])


def stage_verified_python(repo, parent, *, expected_commit=None):
    """Copy only exact regular Git Python sources + the shipped dashboard document."""
    if expected_commit is not None:
        check(git(repo, "rev-parse", "HEAD").decode().strip() == expected_commit, "Wrong exact checkout")
        # Other CI asset generation is not part of this operation; exact Git
        # object checks below still reject any changed staged or working Python/HTML byte.
        check(not git(repo, "diff", "--cached", "--name-only", "HEAD", "--", "mo2_update_arsenal"),
              "Tracked staged package differs from the candidate commit")
    raw = git(repo, "ls-files", "--stage", "-z", "--", "mo2_update_arsenal")
    seen, source_hash = set(), hashlib.sha256()
    for record in filter(None, raw.split(b"\0")):
        fields, raw_path = record.split(b"\t", 1)
        mode, sha, slot = fields.decode("ascii").split()
        name = raw_path.decode("utf-8")
        if not name.endswith(".py") and name != "mo2_update_arsenal/web/index.html":
            continue
        rel = PurePosixPath(name)
        check(not rel.is_absolute() and name not in seen and ".." not in rel.parts
              and mode == "100644" and slot == "0" and rel.parts[0] == "mo2_update_arsenal",
              "Unexpected package candidate")
        source = repo.joinpath(*rel.parts)
        cursor = source
        while cursor != repo:
            check(not cursor.is_symlink() and not getattr(cursor, "is_junction", lambda: False)(),
                  "Linked source path refused")
            cursor = cursor.parent
        check(source.is_file() and source.stat().st_size < 16 * 1024 * 1024, "Bad source file")
        data = source.read_bytes()
        actual = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
        check(actual == sha, f"File differs from checked-out index: {rel.name}")
        destination = parent.joinpath(*rel.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        seen.add(name)
        source_hash.update(raw_path + b"\0" + bytes.fromhex(sha))
    check({"mo2_update_arsenal/" + part for part in ("plugin.py", "model.py", "bridge.py",
          "plugin_intelligence.py", "web/index.html")} <= seen, "Required package sources missing")
    return {"pythonAndHtmlManifestSha256": source_hash.hexdigest(), "files": len(seen)}


def load_production(staged):
    # Only the external native bindings are substituted; no timers, production
    # methods, service, model, Chronicle, dispatcher or disk APIs are patched.
    check("mobase" not in sys.modules, "Real MO2 plugin context was refused")
    stub = ModuleType("mobase")
    stub.IPluginTool = type("OwnedFixturePlugin", (), {})
    stub.IOrganizer = type("OwnedFixtureOrganizer", (), {})
    stub.ModState = SimpleNamespace(ACTIVE=ACTIVE)
    stub.VersionInfo = str
    sys.modules["mobase"] = stub
    sys.path.insert(0, str(staged))
    from mo2_update_arsenal import plugin  # only verified staged copy accessible first
    check(staged in Path(plugin.__file__).resolve().parents, "Wrong Python package import")
    return plugin


class StageTrace:
    """Fixed diagnostic stages only. No Qt calls, no identifiers or payloads."""
    def __init__(self, clock):
        self._clock = clock
        self._owner = get_ident()
        self._lock = Lock()
        self._spans = []

    def wrap(self, name, function, *, owner=True):
        def observed(*args, **kwargs):
            check((get_ident() == self._owner) == owner, f"Wrong telemetry thread: {name}")
            entered = self._clock()
            try:
                return function(*args, **kwargs)
            finally:
                exited = self._clock()
                with self._lock:
                    self._spans.append((name, "qt-owner" if owner else "worker", entered, exited))
        return observed

    def snapshot(self):
        with self._lock:
            return tuple(self._spans)


def phase_substages(name, begin, end, native, spans):
    """Bind measured nested wall times to one phase without adding to totals."""
    phase = [(label, lane, a, b) for label, lane, a, b in spans if a < end and b > begin]
    check(all(begin <= a < b <= end for _, _, a, b in phase), "Stage crosses phase boundary")
    owner = [(label, a, b) for label, lane, a, b in phase if lane == "qt-owner"]
    workers = [(label, a, b) for label, lane, a, b in phase if lane == "worker"]
    check(len(phase) == len(owner) + len(workers), "Unknown telemetry lane")

    expected = (["native-attach", "service-construction", "service-start"]
                if name == "startup" else ["native-attach", "service-commit"])
    check([row[0] for row in owner] == expected, "Missing, out-of-order or duplicate owner substage")
    parent_label, parent_start, parent_end = native[-1]
    check(all(parent_start <= a < b <= parent_end for _, a, b in owner),
          "Owner substage escaped its parent Qt window")
    check(all(left[2] <= right[1] for left, right in zip(owner, owner[1:])),
          "Overlapping owner substages")
    if parent_label == "native-publish":
        check(len(workers) == 1 and workers[0][0] == "worker-projection",
              "Missing or duplicate worker projection")
        _, a, b = workers[0]
        check(len(native) >= 2 and native[-2][2] <= a < b <= parent_start,
              "Worker projection overlapped native capture or publication")
    else:
        check(parent_label in {"startup", "synchronous"} and not workers,
              "Legacy synchronous phase unexpectedly ran a projection worker")

    def describe(row):
        label, a, b = row
        return {"name": label, "enterNs": a - begin, "exitNs": b - begin, "wallNs": b - a}
    # These are wall-clock brackets. Do not attribute heartbeat gaps to both
    # a parent and its children, nor add nested duration to endToEnd.
    return {"ownerParentWindow": parent_label,
            "owner": [describe(row) for row in owner], "worker": [describe(row) for row in workers]}


def measure(QtCore, QtWidgets, production, fixture, *, discovery_root=None):
    with ExitStack() as instrumentation:
        return _measure(QtCore, QtWidgets, production, fixture, discovery_root, instrumentation)


def _measure(QtCore, QtWidgets, production, fixture, discovery_root, instrumentation):
    app = QtWidgets.QApplication([])
    zero = time.perf_counter_ns()
    clock = lambda: time.perf_counter_ns() - zero
    substage_trace = StageTrace(clock)
    instrumentation.enter_context(patch.object(
        production, "attach_native_plugin_state",
        substage_trace.wrap("native-attach", production.attach_native_plugin_state)))
    service_type = production.ArsenalService
    for attribute, label in (("__init__", "service-construction"), ("start", "service-start"),
                             ("set_snapshot", "service-commit")):
        instrumentation.enter_context(patch.object(
            service_type, attribute, substage_trace.wrap(label, getattr(service_type, attribute))))
    ticks, records, windows, completion_edges, dashboard_opens = [], [], [], [], []
    phase, requested, previous = 0, None, 0
    failure = request = None
    discovery_seen = False
    heartbeat = QtCore.QTimer()
    heartbeat.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
    heartbeat.setInterval(PERIOD_MS)

    def completion_edge(observer):
        if requested is None or observer.server is None or observer.server._server is None:
            return
        if observer.server.revision()["generation"] != previous + 1:
            return
        if phase != 2 or (request and request.state == "completed" and request.result.get("ok")):
            completion_edges.append(clock())

    class Observer(production.ModUpdateArsenal):
        def bracket(self, name, action):
            t = clock()
            try:
                return action()
            finally:
                windows.append((name, t, clock()))
        def _ensure_service(self):
            result = self.bracket("startup", super()._ensure_service)
            completion_edge(self)
            return result
        def _refresh_snapshot(self, *, force_metadata=False):
            result = self.bracket("synchronous", lambda: super(Observer, self)._refresh_snapshot(force_metadata=force_metadata))
            completion_edge(self)
            return result
        def _dispatch(self, received):
            super()._dispatch(received)
            if phase == 2 and received is request and not hasattr(production.ModUpdateArsenal, "_capture_background_refresh"):
                # The released synchronous refresh returns before dispatch
                # resolves the authenticated request.
                completion_edge(self)
        def _on_background_refresh_completed(self, result):
            super()._on_background_refresh_completed(result)
            completion_edge(self)
        def _open_dashboard(self, address):
            dashboard_opens.append(address)  # No browser; any such call fails the trial.
        def _capture_background_refresh(self):
            return self.bracket("native-capture", super()._capture_background_refresh)
        def _begin_background_capture(self, valid):
            capture, started = self.bracket("native-enumeration",
                lambda: super(Observer, self)._begin_background_capture(valid))
            observer = self
            class ObservedCapture:
                def step(self):
                    return observer.bracket("native-slice", capture.step)
                def close(self):
                    return capture.close()
            return ObservedCapture(), started
        def _publish_background_refresh(self, snapshot, valid):
            return self.bracket("native-publish", lambda: super(Observer, self)._publish_background_refresh(snapshot, valid))

    p = Observer()
    check(p.init(fixture) and not p._subscription_errors, "Could not wire actual callbacks")
    check(p.server is None, "Service started before UI-ready signal")
    coordinator = getattr(p, "_background_refresh", None)
    if coordinator is not None:
        instrumentation.enter_context(patch.object(
            coordinator, "_project", substage_trace.wrap("worker-projection", coordinator._project, owner=False)))
    phase_calls = fixture.calls.copy()
    capture_completed = None

    def start():
        nonlocal requested, request, phase_calls, capture_completed
        phase_calls = fixture.calls.copy()
        capture_completed = None
        completion_edges.clear()
        requested = clock()
        try:
            if phase == 0:
                fixture.callbacks["onUserInterfaceInitialized"](None)
            elif phase == 1:
                fixture.callbacks["onRefreshed"]()
            elif phase == 2:
                fixture.callbacks["onRefreshed"]()  # scheduled debounce should be subsumed
                request = production.UIRequest("refresh", {}, timeout=60)
                p.dispatcher.submitted.emit(request)
        except Exception as exc:
            fail(exc)

    def fail(exc):
        nonlocal failure
        failure = str(exc)[:300]
        heartbeat.stop()
        app.exit(2)

    def tick():
        nonlocal phase, requested, previous, capture_completed, discovery_seen
        now = clock()
        ticks.append(now)
        if requested is None:
            return
        try:
            check(now - requested < 60_000_000_000, "Fixture phase exceeded 60 seconds")
            if p.server is None:
                return
            service = p.server
            generation = service.revision()["generation"]
            check(generation <= previous + 1, "Unintended duplicate publication")
            if generation != previous + 1 or (phase == 2 and not
                    (request and request.state == "completed" and request.result.get("ok"))):
                return
            if capture_completed is None:
                check(len(completion_edges) == 1 and requested < completion_edges[0] <= now,
                      "Published generation lacks one complete callback edge")
                capture_completed = now
                if phase == 0 and discovery_root is not None:
                    files = list(discovery_root.glob("*.json"))
                    check(len(files) == 1 and files[0].stat().st_size < 4096, "Startup descriptor missing")
                    d = json.loads(files[0].read_text(encoding="utf-8"))
                    check(d.get("pid") == os.getpid() and d.get("profileId") == service.profile_id()
                          and str(d.get("endpoint")).startswith("http://127.0.0.1:"), "Startup descriptor differs")
                    discovery_seen = True
                return
            if now - capture_completed < 350_000_000:
                return  # wait past the ordinary debounce, ensure no late duplicate
            calls = fixture.calls - phase_calls
            check(calls["allModsByProfilePriority"] == 1 and calls["getMod"] == fixture.count
                  and calls["pluginNames"] == 1 and calls["resolvePath"] == fixture.expected_plugins,
                  "Phase did not capture exactly one complete fixture")
            check(not p._snapshot_error and not service.intelligence.runner.available, "Unexpected runtime or helper error")
            check(len(completion_edges) == 1 and not dashboard_opens, "Duplicate completion or automatic dashboard open")
            selected = [item for item in windows if requested <= item[1]]
            names = [s[0] for s in selected]
            if phase == 0 and not hasattr(production.ModUpdateArsenal, "_finish_background_startup"):
                expected = ["startup"]
            elif hasattr(production.ModUpdateArsenal, "_begin_background_capture"):
                check(len(names) >= 3 and names[0] == "native-enumeration"
                      and names[-1] == "native-publish"
                      and set(names[1:-1]) == {"native-slice"},
                      "Wrong actual sliced Qt call sequence")
                expected = names
            else:
                expected = ["native-capture", "native-publish"] if hasattr(
                    production.ModUpdateArsenal, "_capture_background_refresh") else ["synchronous"]
            check(names == expected, "Wrong actual Qt call sequence")
            records.append((PHASES[phase], requested, completion_edges[0], selected, service._snapshot, calls))
            previous = generation
            phase += 1
            requested = None
            if phase == len(PHASES):
                heartbeat.stop()
                app.quit()
            else:
                QtCore.QTimer.singleShot(30, start)
        except Exception as exc:
            fail(exc)

    heartbeat.timeout.connect(tick)
    heartbeat.start()
    QtCore.QTimer.singleShot(150, start)
    rc = app.exec()
    check(rc == 0 and failure is None, f"Qt operation failed: {failure}")
    check(len(records) == len(PHASES) and len(ticks) >= 20 and p._startup_attempts == 1,
          "Missing complete phases or startup retried")
    report = []
    spans = substage_trace.snapshot()
    for name, begin, end, pieces, snapshot, calls in records:
        report.append({"name": name, "endToEnd": interval_metrics(ticks, begin, end),
                       "nativeWindows": [{"name": t, **interval_metrics(ticks, a, b)} for t,a,b in pieces],
                       "input": semantic_witness(snapshot, fixture), "calls": dict(calls),
                       "substageTelemetry": phase_substages(name, begin, end, pieces, spans)})
    check(all(r["input"] == report[0]["input"] for r in report), "Capture changed complete normalized output")
    return {"phases": report, "heartbeatPeriodMs": PERIOD_MS, "nativeMO2": False, "nativeMobase": False,
            "auditHelperUsed": False, "startupClock": "UI-ready callback; not process start",
            "ownedDiscoveryDescriptorObserved": discovery_seen}


def child(args):
    check(sys.platform == "win32" and os.environ.get("GITHUB_ACTIONS") == "true", "Windows hosted runner only")
    runner = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    root = args.private_child.resolve(strict=True)
    check(root.parent == runner and root.name.startswith("modhunter-qt-heartbeat-"),
          "Private fixture must be inside a dedicated hosted runner temp directory")
    check(not any(root.iterdir()), "Fixture base must be freshly created and empty")
    # Direct production discovery writes remain in this exact owned directory.
    os.environ["LOCALAPPDATA"] = str(root / "local-appdata")
    os.environ["APPDATA"] = str(root / "appdata")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ.pop("QT_PLUGIN_PATH", None)
    from PyQt6 import QtCore, QtWidgets
    check(QtCore.qVersion() == EXPECTED_QT and QtCore.PYQT_VERSION_STR == EXPECTED_PYQT, "Wrong exact Qt runtime")
    staged = root / "compiled-package"
    home = root / "instance"
    home.mkdir()
    manifest = stage_verified_python(args.repository.resolve(strict=True), staged, expected_commit=args.expect_commit)
    fixture = Organizer(home, args.count)  # all file fabrication outside measured Qt window
    production = load_production(staged)
    discovery = root / "local-appdata" / "Modhunter" / "mo2-sessions"
    result = measure(QtCore, QtWidgets, production, fixture, discovery_root=discovery)
    check(not list(discovery.glob("*.json")),
          "Production shutdown did not remove its own fixture discovery descriptor")
    result.update(ok=True, inputCount=args.count, source=manifest, candidateCommit=args.expect_commit,
                  fixtureFilesSha256=fixture.fixture_sha.hexdigest(), fixturePlugins=fixture.expected_plugins,
                  hostedWindowsQt=True, qt=QtCore.qVersion(), pyqt=QtCore.PYQT_VERSION_STR, platform=sys.platform)
    print(json.dumps(result, separators=(",", ":"), allow_nan=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--count", type=int, choices=SIZES, default=11207)
    parser.add_argument("--expect-commit", default=EXPECTED_TEST1_COMMIT)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--private-child", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    check(len(args.expect_commit) == 40 and all(c in "0123456789abcdef" for c in args.expect_commit), "Pin an exact commit")
    if args.private_child is not None:
        child(args)
        return
    check(sys.platform == "win32" and os.environ.get("GITHUB_ACTIONS") == "true", "Windows hosted runner only")
    runner = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    check(not args.output.exists(), "Refuse to overwrite evidence")
    with tempfile.TemporaryDirectory(prefix="modhunter-qt-heartbeat-", dir=runner) as private:
        inputs = ["--repository", str(args.repository), "--count", str(args.count), "--expect-commit",
                  args.expect_commit, "--output", str(args.output), "--private-child", private]
        # A worker outside Qt bounds hangs; subprocess timeout kills only its own child.
        proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), *inputs],
                              timeout=180, check=True, stdout=subprocess.PIPE, text=True)
        payload = json.loads(proc.stdout)
        check(payload.get("ok") and payload.get("candidateCommit") == args.expect_commit
              and payload.get("inputCount") == args.count and payload.get("hostedWindowsQt")
              and not payload.get("nativeMO2"), "Missing or invalid native-timer diagnostic")
    with args.output.open("x", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, allow_nan=False)
        f.write("\n")


if __name__ == "__main__":
    main()
