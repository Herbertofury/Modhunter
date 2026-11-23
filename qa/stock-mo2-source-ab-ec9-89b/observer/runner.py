"""Test-only observer, imported by the stock 2.5.2 plugin loader."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import time

from PyQt6 import QtCore, QtWidgets, sip
import mobase

from .trace import StartupTraceMixin, verify_trace
from .fixture import (GAME, INTERVAL_MS, MAX_TICKS, PACKAGE_PIN, PROFILE, REFRESH_PINS,
                      SIZES, TEST, VERSION, own_path, package_digest, require, timing, verify_catalog)

WORK = Path(os.environ.get("MODHUNTER_NATIVE_SMOKE_INSTANCE", ""))
LIBS = Path(__file__).resolve().parents[2] / "modhunter-fixture-libs"
PACKAGE = LIBS / "mo2_update_arsenal"
require(sys.platform == "win32" and os.environ.get("MODHUNTER_NATIVE_SMOKE") == "1",
        "Requires the real disposable Windows host")
require(WORK.is_absolute(), "Expected an owned absolute fixture path")
own_path(WORK, directory=True)
require(package_digest(PACKAGE, runtime_helper=True) == PACKAGE_PIN, "Staged production package bytes changed")
helper = own_path(PACKAGE / 'bin' / 'modhunter-qt-bridge.dll', directory=False)
require(0 < helper.stat().st_size <= 32 * 1024 * 1024
        and hashlib.sha256(helper.read_bytes()).hexdigest() == os.environ.get('MODHUNTER_NATIVE_SMOKE_QT_BRIDGE_SHA256'),
        'Staged Qt helper does not match the orchestrator evidence')
sys.path.insert(0, str(LIBS))

from mo2_update_arsenal.plugin import ModUpdateArsenal


class NativeSmokePlugin(StartupTraceMixin, ModUpdateArsenal):
    """Only observe exact production callbacks and application timer delivery."""

    def __init__(self):
        super().__init__()
        self._perf_init = self._perf_ui = self._perf_start = self._perf_ready = None
        self._perf_ticks = []
        self._perf_done = False
        self._perf_receipt = None
        self._perf_rows = None
        self._perf_fixture = None
        self._perf_generation = None

    def name(self):
        return "Modhunter Native Scale Observer"

    def displayName(self):
        return "Modhunter Native Scale Observer (disposable fixture only)"

    def description(self):
        return "Measures unmodified Modhunter publication and the stock MO2 Qt timer."

    def init(self, organizer):
        self._perf_init = time.perf_counter_ns()
        receipt = Path(os.environ.get("MODHUNTER_NATIVE_SMOKE_RESULT", ""))
        require(receipt.is_absolute() and receipt.parent.resolve() == WORK.parent.resolve() / "evidence"
                and receipt.name == "native-receipt.json", "Expected the isolated stock receipt path")
        self._perf_receipt = receipt
        require(not receipt.exists(), "Refused an existing stock receipt")
        try:
            marker = own_path(WORK / "modhunter-stock-scale.json", directory=False)
            stock_marker = own_path(WORK / "modhunter-smoke-fixture.json", directory=False)
            require(max(marker.stat().st_size, stock_marker.stat().st_size) <= 4096, "Oversize fixture marker")
            fixture = json.loads(marker.read_text(encoding="utf-8"))
            stock = json.loads(stock_marker.read_text(encoding="utf-8"))
            self._perf_fixture = fixture
            self._perf_rows = fixture["rows"]
            require(fixture["source"] == VERSION and fixture["test"] == TEST
                    and self._perf_rows in SIZES and fixture["profile"] == PROFILE
                    and fixture["runId"] == stock["runId"] and stock["purpose"] == "isolated native MO2 CI fixture",
                    "Wrong fixture identity")
            require(isinstance(organizer, mobase.IOrganizer), "Refused synthetic organizer bindings")
            self._owner()
            self._perf_timer = QtCore.QTimer()
            self._perf_timer.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
            self._perf_timer.setInterval(INTERVAL_MS)
            self._perf_timer.timeout.connect(self._heartbeat)
            self._perf_timer.start()
            if not super().init(organizer):
                raise RuntimeError("Production init did not succeed")
            return True
        except Exception as exc:
            # Never turn a local harness exception into a stock success.
            QtCore.QTimer.singleShot(0, lambda error=str(exc): self._finish(False, error))
            return False

    def _owner(self):
        app = QtWidgets.QApplication.instance()
        require(app is not None and QtCore.QThread.currentThread() == app.thread(), "Not the stock Qt owner thread")

    def _on_ui_initialized(self, window):
        edge = time.perf_counter_ns()
        try:
            self._owner()
            require(self._perf_ui is None, "Duplicate native UI-ready callback")
            require(window is not None and not sip.ispycreated(window), "Expected the C++ main window")
            require(self.org.managedGame().gameName() == "Modhunter CI Fixture"
                    and self.org.profileName() == PROFILE
                    and Path(self.org.basePath()).resolve() == WORK.resolve()
                    and Path(self.org.profilePath()).resolve() == (WORK / "profiles" / PROFILE).resolve(),
                    "MO2 is not on the isolated game, base and profile")
            self._perf_ui = edge
            return super()._on_ui_initialized(window)
        except Exception as exc:
            QtCore.QTimer.singleShot(0, lambda error=str(exc): self._finish(False, error))

    def _start_automatically(self):
        if self._perf_start is None and self._perf_ui is not None:
            self._perf_start = time.perf_counter_ns()
        return super()._start_automatically()

    def _publish_diagnostics(self):
        super()._publish_diagnostics()
        if self._perf_ready is None and self.server is not None and self.server._server is not None:
            self._perf_ready = time.perf_counter_ns()
            self._perf_generation = self.server.revision()["generation"]
            self._perf_service = self.server

    def _heartbeat(self):
        try:
            self._owner()
            self._perf_ticks.append(time.perf_counter_ns())
            require(len(self._perf_ticks) <= MAX_TICKS, "Bounded Qt timer capture exceeded")
            if self._perf_ready is None:
                return
            self._perf_timer.stop()
            # No inventory check, snapshot deepcopy, hashing or write in the
            # measured interval. The boundary includes the first real Qt tick
            # after publication, not just the production callback's return.
            self._finish_capture()
        except Exception as exc:
            self._finish(False, str(exc))

    def _finish_capture(self):
        stats = timing(self._perf_init, self._perf_ui, self._perf_start, self._perf_ready, self._perf_ticks)
        witness = self.server.native_snapshot()
        require(self.server is self._perf_service and self._perf_generation == 1
                and self.server.revision()["generation"] == self._perf_generation,
                "The first live publication was replaced before its complete witness")
        ml = self.org.modList()
        ordered = []
        for name in ml.allModsByProfilePriority(self.org.profile()):
            mod = ml.getMod(name)
            if mod is not None and not mod.isSeparator():
                ordered.append(str(name))
        catalog = verify_catalog(witness, ordered, self._perf_rows)
        require(catalog["fixtureHash"] == self._perf_fixture["fixtureHash"], "Mismatched fixture provenance")
        checked = {}
        for name, pin in REFRESH_PINS.items():
            modulename = "mo2_update_arsenal." + Path(name).stem
            mod = sys.modules[modulename]
            shipping = own_path(PACKAGE / name, directory=False)
            require(Path(mod.__file__).resolve() == shipping.resolve()
                    and hashlib.sha256(shipping.read_bytes()).hexdigest() == pin,
                    "Unexpected native imported source")
            checked[name] = pin
        gateway = self._native_nexus
        sender = getattr(gateway, "_qt_sender", None)
        require(sender is not None and gateway.files_available and gateway._qt_bridge is not None
                and not sip.ispycreated(gateway._qt_bridge) and sender.path.resolve() == helper.resolve()
                and sender.info == {"abi": 1, "buildQt": "6.7.1", "runtimeQt": "6.7.1", "mechanism": "typed-qt-sender"},
                "Released Qt bridge did not load against the stock native QObject")
        startup_trace = verify_trace(self._scale_trace, origin=self._perf_init, first=self._perf_start,
                                     published=self._perf_ready, terminal_count=self._startup_attempts)
        self._finish(True, None, timing=stats, startupTrace=startup_trace, inventory=catalog, loadedSourceSha256=checked,
                     generation=self._perf_generation, startupAttempts=self._startup_attempts,
                     loadedQtBridgeSha256=hashlib.sha256(sender.path.read_bytes()).hexdigest(),
                     qtBridgeBinding=sender.info)

    def _finish(self, success: bool, error: str | None, **details):
        if self._perf_done:
            return
        self._perf_done = True
        timer = getattr(self, "_perf_timer", None)
        if timer is not None:
            timer.stop()
        outcome = {"schemaVersion": 1, "test": TEST, "source": VERSION, "ok": success,
                   "nativeMO2": success, "error": error,
                   "runtime": {"platform": sys.platform, "mo2": QtCore.QCoreApplication.applicationVersion(),
                               "qt": QtCore.qVersion(), "pyqt": QtCore.PYQT_VERSION_STR},
                   "requiresOuterOfficialExeVersionWitness": True,
                   "fixture": {"profile": PROFILE, "installedGame": False, "ownedProfile": False},
                   "checks": [{"name": name, "passed": success} for name in
                              ("stock_isolated_fixture", "stock_large_snapshot", "stock_ui_heartbeat", "stock_startup_trace")],
                   **details}
        temporary = self._perf_receipt.with_suffix(".tmp")
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(outcome, handle, indent=2)
        temporary.replace(self._perf_receipt)
        QtCore.QCoreApplication.exit(0 if success else 1)
