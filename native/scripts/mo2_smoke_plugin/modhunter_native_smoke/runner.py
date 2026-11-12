"""Exercise the production plugin in real MO2, mobase, PyQt and loopback HTTP.

This harness adds observation and test lifecycle only. It does not replace any
organizer, model, bridge, dispatcher, request handler or native selection API.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from PyQt6 import QtCore, QtGui, QtWidgets

from mo2_update_arsenal.focus import find_mo2_view, name_column
from mo2_update_arsenal.plugin import ModUpdateArsenal

from .audit_probe import exercise_plugin_audit


GAME_NAME = "Modhunter CI Fixture"
PROFILE_NAME = "Modhunter Smoke"
EXPECTED_MODS = {
    "Smoke Enabled": {"enabled": True, "installedVersion": "1.0.0"},
    "Smoke Disabled": {"enabled": False, "installedVersion": "2.0.0"},
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def repository_api_metadata(organizer):
    """Read public binding names/signatures only; never inspect account values."""
    details = {}
    try:
        import mobase

        details["createNexusBridgeDoc"] = str(
            getattr(organizer.createNexusBridge, "__doc__", "") or "")[:2000]
        exported = [name for name in dir(mobase)
                    if not name.startswith("_") and ("nexus" in name.lower()
                                                       or "repository" in name.lower())]
        details["mobaseExports"] = {}
        for name in exported[:30]:
            symbol = getattr(mobase, name)
            details["mobaseExports"][name] = {
                "members": [member for member in dir(symbol)
                            if not member.startswith("_")][:100],
                "doc": str(getattr(symbol, "__doc__", "") or "")[:4000],
            }
        bridge = organizer.createNexusBridge()
        details["bridgeType"] = type(bridge).__module__ + "." + type(bridge).__qualname__
        details["bridgeMembers"] = [name for name in dir(bridge)
                                    if not name.startswith("_")][:180]
        if bridge is not None and hasattr(bridge, "metaObject"):
            meta = bridge.metaObject()
            details["qtSignatures"] = [
                bytes(meta.method(index).methodSignature()).decode("utf-8", "replace")[:500]
                for index in range(min(meta.methodCount(), 120))]
    except Exception as exc:
        details["diagnosticError"] = str(exc)[:300]
    return details


class HarnessSignals(QtCore.QObject):
    finished = QtCore.pyqtSignal(object)


class LoopbackUrlCapture(QtCore.QObject):
    """Capture the real display() URL without opening the runner's browser."""

    def __init__(self):
        super().__init__()
        self.address = ""

    @QtCore.pyqtSlot(QtCore.QUrl)
    def capture(self, url):
        self.address = url.toString()


class NativeSmokePlugin(ModUpdateArsenal):
    def __init__(self):
        super().__init__()
        self._finished = False
        self._begun = False
        self._checks = []
        self._dispatches = []
        self._start_time = time.monotonic()
        self._started_at = utc_now()
        self._receipt_path = None
        self._main_window = None
        self._worker = None
        self._signals = None
        self._capture = None
        self._watchdog = None

    def name(self):
        return "Modhunter Native Smoke Harness"

    def displayName(self):
        return "Modhunter native smoke (CI fixture only)"

    def description(self):
        return "Runs the real Modhunter bridge against an isolated synthetic game."

    def _check(self, name, condition, **details):
        self._checks.append({"name": name, "passed": bool(condition), **details})
        if not condition:
            raise AssertionError(f"{name}: {details}")

    def _on_qt_thread(self):
        return QtCore.QThread.currentThread() == QtWidgets.QApplication.instance().thread()

    def init(self, organizer):
        # The harness is unusable unless explicitly launched by the fixture
        # script, with a marked, isolated instance and an evidence destination.
        if os.environ.get("MODHUNTER_NATIVE_SMOKE") != "1":
            return False
        receipt = os.environ.get("MODHUNTER_NATIVE_SMOKE_RESULT", "")
        fixture = os.environ.get("MODHUNTER_NATIVE_SMOKE_INSTANCE", "")
        if not receipt or not fixture:
            return False
        self._receipt_path = Path(receipt)
        self._signals = HarnessSignals()
        self._signals.finished.connect(self._finish, QtCore.Qt.ConnectionType.QueuedConnection)
        try:
            expected = Path(fixture).resolve()
            self._check("isolated_fixture", (expected / "modhunter-smoke-fixture.json").is_file()
                        and self._receipt_path.parent.resolve() != expected)
            self._check("native_plugin_initialized", bool(super().init(organizer))
                        and self._on_qt_thread())
            self._check("native_nexus_gateway_created", self._native_nexus is not None,
                        error=self._native_nexus_error,
                        api=repository_api_metadata(organizer) if self._native_nexus is None else None,
                        scope="Construction and Qt signal binding only; no account or requests")
            accepted = organizer.onUserInterfaceInitialized(self._ui_ready)
            self._check("native_ui_callback_registered", bool(accepted))
            self._watchdog = QtCore.QTimer()
            self._watchdog.setInterval(500)
            self._watchdog.timeout.connect(self._check_liveness)
            self._watchdog.start()
            return True
        except Exception:
            self._write_receipt({"ok": False, "error": traceback.format_exc()})
            return False

    def _ui_ready(self, window):
        self._main_window = window
        QtCore.QTimer.singleShot(0, self._begin)

    def _check_liveness(self):
        if self._finished:
            return
        dialog = QtWidgets.QApplication.activeModalWidget()
        if dialog is not None:
            labels = [label.text() for label in dialog.findChildren(QtWidgets.QLabel)]
            self._finish({"ok": False, "error": "Unexpected native modal dialog",
                          "dialog": {"title": dialog.windowTitle(), "text": labels[:12]}})
        elif time.monotonic() - self._start_time > 90:
            self._finish({"ok": False, "error": "Native smoke exceeded its 90 second deadline"})

    def _begin(self):
        if self._begun or self._finished:
            return
        try:
            self._check("native_ui_initialized", self._on_qt_thread()
                        and self._main_window is not None)
            self._check("fixture_instance_selected",
                        Path(self.org.basePath()).resolve()
                        == Path(os.environ["MODHUNTER_NATIVE_SMOKE_INSTANCE"]).resolve())
            self._check("fixture_game_selected", self.org.managedGame().gameName() == GAME_NAME)
            self._check("fixture_profile_selected", self.org.profileName() == PROFILE_NAME)
            window, view = find_mo2_view(QtWidgets)
            self._check("native_mod_view_present", window is not None and view is not None
                        and view.model() is not None and view.model().rowCount() >= len(EXPECTED_MODS))
            self._begun = True
            self._capture = LoopbackUrlCapture()
            QtGui.QDesktopServices.setUrlHandler("http", self._capture, "capture")
            # Run production initialization, metadata extraction, loopback server
            # creation and native selection binding without a fake organizer.
            super().display()
            if self._finished:
                return  # A production error dialog may have tripped the watchdog.
            self._check("production_display_started_bridge", self.server is not None
                        and bool(self._capture.address))
            address = self._capture.address
            parsed = urllib.parse.urlsplit(address)
            self._check("loopback_address", parsed.scheme == "http"
                        and parsed.hostname == "127.0.0.1" and parsed.port is not None)
            # The real in-memory session token is used, never persisted in evidence.
            self._worker = threading.Thread(target=self._exercise_http,
                                            args=(address, self.server.token), daemon=True,
                                            name="ModhunterNativeSmokeHTTP")
            self._worker.start()
        except Exception:
            self._finish({"ok": False, "error": traceback.format_exc()})

    def _dispatch(self, request):
        self._dispatches.append({"command": request.cmd, "qtThread": self._on_qt_thread(),
                                 "threadName": threading.current_thread().name})
        super()._dispatch(request)

    def _exercise_http(self, address, token):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        timings = {}

        def api(path, data=None, authenticated=True, *, timeout=20):
            headers = {"Accept": "application/json"}
            if authenticated:
                headers["X-Arsenal-Token"] = token
            body = None
            if data is not None:
                body = json.dumps(data).encode("utf-8")
                headers["Content-Type"] = "application/json"
                headers["Origin"] = address.rstrip("/")
            request = urllib.request.Request(address.rstrip("/") + path, data=body, headers=headers)
            started = time.perf_counter()
            with opener.open(request, timeout=timeout) as response:
                result = json.loads(response.read(4 * 1024 * 1024))
            timings[path] = round((time.perf_counter() - started) * 1000, 2)
            return result

        try:
            health = api("/api/health", authenticated=False)
            self._check("loopback_health", health.get("ok") is True and health.get("live") is True
                        and health.get("app") == "Modhunter" and health.get("protocol") == 1)
            rust_cli = os.environ.get("MODHUNTER_NATIVE_SMOKE_RUST_CLI", "")
            if rust_cli:
                started = time.perf_counter()
                completed = subprocess.run(
                    [rust_cli, "probe", "--endpoint", address],
                    stdin=subprocess.DEVNULL, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=12, check=False,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                timings["rust-cli-probe"] = round((time.perf_counter() - started) * 1000, 2)
                if completed.returncode != 0:
                    self._check("rust_cli_live_probe", False, exitCode=completed.returncode,
                                error=completed.stderr[:1000])
                probe = json.loads(completed.stdout)
                # BridgeClient::probe validates app="Modhunter", protocol=1,
                # ok and live before it returns this public BridgeHealth JSON.
                # The CLI receives only the public loopback address, no token.
                self._check("rust_cli_live_probe", probe.get("endpoint") == address
                            and probe.get("profileId") == health.get("profileId")
                            and probe.get("version") == health.get("version")
                            and isinstance(probe.get("generation"), int)
                            and probe["generation"] >= health.get("generation", 0)
                            and isinstance(probe.get("latencyMs"), (int, float))
                            and probe["latencyMs"] >= 0,
                            response=probe, app=health["app"], protocol=health["protocol"],
                            identityValidation="Rust BridgeClient rejects an incompatible app or protocol")
            try:
                api("/api/snapshot", authenticated=False)
            except urllib.error.HTTPError as error:
                self._check("protected_snapshot_authentication", error.code == 403,
                            responseStatus=error.code)
            else:
                self._check("protected_snapshot_authentication", False,
                            error="Protected native snapshot accepted an unauthenticated request")
            snapshot = api("/api/snapshot")
            catalog = snapshot.get("catalog", {})
            by_name = {item["mo2InternalName"]: item for item in catalog.get("items", [])}
            self._check("native_profile_snapshot", snapshot.get("ok") is True
                        and catalog.get("profileName") == PROFILE_NAME
                        and catalog.get("game") == "modhuntersmoke"
                        and catalog.get("profileId") == health.get("profileId")
                        and all(name in by_name and all(by_name[name].get(key) == value
                                for key, value in expected.items())
                                for name, expected in EXPECTED_MODS.items()),
                        itemCount=len(catalog.get("items", [])),
                        fixtureMods={name: {key: by_name.get(name, {}).get(key)
                                           for key in expected}
                                     for name, expected in EXPECTED_MODS.items()})
            refresh = api("/api/refresh", {})
            self._check("queued_refresh", refresh.get("ok") is True
                        and refresh.get("catalog", {}).get("profileId") == catalog["profileId"]
                        and refresh.get("count", 0) >= len(EXPECTED_MODS))
            target = by_name["Smoke Disabled"]
            focus = api("/api/focus", {"id": target["id"], "profileId": catalog["profileId"]})
            self._check("queued_focus", focus.get("ok") is True
                        and focus.get("selected") == target["name"], response=focus)
            revision = api("/api/revision")
            self._check("native_selection_revision", revision.get("selectedModId") == target["id"]
                        and revision.get("profileId") == catalog["profileId"])
            compact = api("/api/game?compact=1")
            self._check("compact_game_response", compact.get("ok") is True
                        and compact.get("profileId") == catalog["profileId"]
                        and "progress" not in compact)
            if rust_cli:
                # The production audit service chooses its own bundled helper;
                # neither this HTTP request nor browser input can select one.
                installed_cli = self.server.intelligence.runner.executable.resolve()
                expected_cli = (Path(sys.modules[ModUpdateArsenal.__module__].__file__).resolve().parent
                                / "bin" / "modhunter-cli.exe").resolve()
                self._check("rust_helper_bundled_path", installed_cli == expected_cli)
                audit = exercise_plugin_audit(api, catalog["profileId"], by_name,
                                              installed_cli, Path(rust_cli).resolve())
                self._check("rust_plugin_audit", True, **audit)
            self._signals.finished.emit({"ok": True, "timingsMs": timings,
                                         "targetName": target["name"],
                                         "profileId": catalog["profileId"]})
        except Exception:
            self._signals.finished.emit({"ok": False, "timingsMs": timings,
                                         "error": traceback.format_exc()})

    def _finish(self, outcome):
        if self._finished:
            return
        self._finished = True
        if self._watchdog is not None:
            self._watchdog.stop()
        try:
            if outcome.get("ok"):
                required_dispatches = {"refresh", "focus"}
                if os.environ.get("MODHUNTER_NATIVE_SMOKE_RUST_CLI"):
                    required_dispatches.add("plugin-audit-input")
                self._check("native_dispatch_thread", self._on_qt_thread()
                            and {row["command"] for row in self._dispatches} >= required_dispatches
                            and all(row["qtThread"] for row in self._dispatches))
                _, view = find_mo2_view(QtWidgets)
                index = view.currentIndex() if view is not None else QtCore.QModelIndex()
                label = (str(index.siblingAtColumn(name_column(view.model(), QtCore)).data(
                    QtCore.Qt.ItemDataRole.DisplayRole) or "") if index.isValid() else "")
                self._check("native_view_selection", label == outcome["targetName"], selected=label)
            window = QtWidgets.QApplication.activeModalWidget() or self._main_window
            if window is not None:
                saved = window.grab().save(str(self._receipt_path.with_name("mo2-fixture.png")), "PNG")
                self._check("native_window_screenshot", bool(saved))
        except Exception:
            outcome = {**outcome, "ok": False, "error": traceback.format_exc()}
        finally:
            if self._capture is not None:
                QtGui.QDesktopServices.unsetUrlHandler("http")
            if self.server is not None:
                try:
                    self.server.stop()
                except Exception:
                    outcome = {**outcome, "ok": False, "error": traceback.format_exc()}
            self._write_receipt(outcome)
            QtCore.QCoreApplication.exit(0 if outcome.get("ok") else 1)

    def _write_receipt(self, outcome):
        if self._receipt_path is None:
            return
        receipt = {"schemaVersion": 1, "test": "mo2-native-fixture-smoke",
                   "nativeMO2": True, "startedAt": self._started_at, "finishedAt": utc_now(),
                   "durationMs": round((time.monotonic() - self._start_time) * 1000, 2),
                   "runtime": {"mo2": QtCore.QCoreApplication.applicationVersion(),
                               "qt": QtCore.qVersion(), "pyqt": QtCore.PYQT_VERSION_STR,
                               "python": sys.version, "platform": sys.platform},
                   "fixture": {"game": GAME_NAME, "profile": PROFILE_NAME,
                               "ownedGameFiles": False, "accountAuthentication": False},
                   "rustCliProbeRequested": bool(os.environ.get("MODHUNTER_NATIVE_SMOKE_RUST_CLI")),
                   "checks": list(self._checks), "dispatches": list(self._dispatches),
                   "scopeLimits": ["No Nexus sign-in, remote scan or mod archive download",
                                   "No installer dialogs, FOMOD choices or game launch"],
                   **outcome}
        self._receipt_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._receipt_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        temporary.replace(self._receipt_path)
