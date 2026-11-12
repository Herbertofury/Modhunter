"""Owned callback/bootstrap contracts plus an optional real C++/PyQt proof.

The compiled cases require MODHUNTER_QT_BRIDGE_TEST_DIR and PyQt6. They never
make account or HTTP requests; a C++-created fixture QObject exercises the exact
protected-sender limitation seen on the native MO2 bridge.
"""
from __future__ import annotations
import ast
import ctypes
import hashlib
import os
from pathlib import Path
import threading
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from mo2_update_arsenal.bridge import ArsenalService, UIRequest
from mo2_update_arsenal.native_nexus import NativeNexusGateway, normalize_native_files


class Signal:
    def __init__(self):
        self.handlers = []
    def connect(self, callback):
        self.handlers.append(callback)
    def disconnect(self, callback):
        self.handlers.remove(callback)
    def emit(self, *args):
        for callback in list(self.handlers):
            callback(*args)


class RawSource:
    def __init__(self, thread):
        self.nxmFilesAvailable, self.destroyed = Signal(), Signal()
        self._thread = thread
        self.class_name = "NexusInterface"
    def thread(self):
        return self._thread
    def metaObject(self):
        return SimpleNamespace(className=lambda: self.class_name,
                               indexOfSignal=lambda signature: 0 if signature == b"nxmFilesAvailable(QString,int,QVariant,QVariant,int)" else -1)


class DelegatedBridge:
    def __init__(self, qt_object=None):
        self.thread = object()
        self.qt_object = qt_object or SimpleNamespace(thread=lambda: self.thread)
        self.descriptionAvailable = getattr(qt_object, "descriptionAvailable", Signal())
        self.requestFailed = getattr(qt_object, "requestFailed", Signal())
        self.calls = []
    def _object(self):
        return self.qt_object
    @property
    def filesAvailable(self):
        raise AssertionError("The unsupported pointer-list signal must never be accessed")
    def requestDescription(self, *args):
        self.calls.append(("metadata", *args))
    def requestFiles(self, *args):
        self.calls.append(("files", *args))


class Resolver:
    info = {"abi": 1, "buildQt": "fixture", "runtimeQt": "fixture", "mechanism": "portable-fixture"}
    def __init__(self, source):
        self.source, self.calls = source, 0
    def sender(self, receiver):
        self.calls += 1
        return self.source


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.native = DelegatedBridge()
        self.source = RawSource(self.native.thread)
        self.resolver = Resolver(self.source)
        self.gateway = NativeNexusGateway(SimpleNamespace(createNexusBridge=lambda: self.native), sender_resolver=self.resolver)
    def request(self, kind="nexus-files"):
        request = UIRequest(kind, {"game": "skyrimspecialedition", "modId": 7})
        self.gateway.request(request)
        return request
    def metadata(self, ticket=None, game="skyrimspecialedition", mod=7):
        self.native.descriptionAvailable.emit(game, mod, ticket or self.native.calls[-1][-1],
                                              {"version": "2", "mod_id": mod, "domain_name": game})
    def files(self, ticket=None, data=None, game="skyrimspecialedition", mod=7):
        self.source.nxmFilesAvailable.emit(game, mod, ticket or self.native.calls[-1][-1],
            data if data is not None else {"files": [{"file_id": 12, "name": "Primary", "file_name": "Primary.zip",
                "category_id": 1, "version": "2", "size_in_bytes": 4096, "uploaded_time": "2026-01-01"}]}, 42)

    def test_first_files_request_bootstraps_once_then_uses_owned_raw_json(self):
        request = self.request()
        self.assertEqual(self.native.calls[-1][0], "metadata")
        bootstrap = self.native.calls[-1][-1]
        self.assertIn(bootstrap, self.gateway._bootstrap)
        self.metadata()
        self.assertEqual(self.native.calls[-1][0], "files")
        self.assertNotEqual(bootstrap, self.native.calls[-1][-1])
        self.assertEqual(self.gateway.file_transport, "qt-raw-variant")
        self.assertEqual(self.gateway._bootstrap, {})
        self.files()
        self.assertTrue(request.result["ok"], request.result)
        self.assertEqual(request.result["files"][0]["sizeInKB"], 4)
        self.assertEqual(request.result["files"][0]["uploaded_time"], "2026-01-01")
        self.assertEqual(self.gateway.pending, {})
        self.metadata(bootstrap)
        self.assertEqual(len(self.native.calls), 2)

    def test_metadata_first_captures_sender_and_files_do_not_bootstrap(self):
        metadata = self.request("nexus-metadata")
        self.metadata()
        self.assertTrue(metadata.result["ok"])
        request = self.request()
        self.assertEqual([call[0] for call in self.native.calls], ["metadata", "files"])
        self.files()
        self.assertTrue(request.result["ok"])
        self.assertEqual(self.resolver.calls, 1)

    def test_unowned_callback_never_captures_sender_or_consumes_request(self):
        request = self.request()
        self.metadata("other-plugin:ticket")
        self.assertEqual(self.resolver.calls, 0)
        self.assertEqual(request.state, "running")
        self.assertEqual(len(self.native.calls), 1)
        self.metadata()
        self.files("other-plugin:ticket")
        self.assertEqual(request.state, "running")
        self.files()
        self.assertTrue(request.result["ok"])

    def test_cancelled_bootstrap_cannot_issue_file_request(self):
        request = self.request()
        bootstrap = self.native.calls[-1][-1]
        request.cancel()
        self.metadata(bootstrap)
        self.assertEqual(len(self.native.calls), 1)
        self.assertEqual(self.resolver.calls, 0)
        self.assertEqual(self.gateway.pending, {})
        self.assertEqual(self.gateway._bootstrap, {})
        self.assertEqual(self.gateway._bootstrap_parent, {})

    def test_bootstrap_failure_owns_callback_but_does_not_retry_authentication(self):
        request = self.request()
        token = self.native.calls[-1][-1]
        self.native.requestFailed.emit("skyrimspecialedition", 7, 0, token, 401, "Not connected")
        self.assertFalse(request.result["ok"])
        self.assertIn("Not connected", request.result["error"])
        self.assertEqual(len(self.native.calls), 1)
        self.assertEqual(self.resolver.calls, 1)
        self.assertEqual(self.gateway._bootstrap, {})

    def test_wrong_bootstrap_game_or_mod_cannot_capture_source(self):
        request = self.request()
        self.metadata(game="fallout4")
        self.assertFalse(request.result["ok"])
        self.assertIn("identity", request.result["error"])
        self.assertEqual(self.resolver.calls, 0)

    def test_unexpected_native_sender_fails_without_connecting_files(self):
        request = self.request()
        self.source.class_name = "UnrelatedObject"
        self.metadata()
        self.assertFalse(request.result["ok"])
        self.assertIn("expected raw files signal", request.result["error"])
        self.assertEqual(self.source.nxmFilesAvailable.handlers, [])
        self.assertFalse(self.gateway.files_available)

    def test_missing_helper_keeps_metadata_and_reports_actionable_file_error(self):
        with patch("mo2_update_arsenal.qt_bridge.NativeQtSender", side_effect=RuntimeError("Missing native Qt helper")):
            native = DelegatedBridge()
            gateway = NativeNexusGateway(SimpleNamespace(createNexusBridge=lambda: native))
        metadata = UIRequest("nexus-metadata", {"game": "skyrimspecialedition", "modId": 7})
        gateway.request(metadata)
        native.descriptionAvailable.emit("skyrimspecialedition", 7, native.calls[-1][-1], {"version": "2"})
        self.assertTrue(metadata.result["ok"])
        request = UIRequest("nexus-files", {"game": "skyrimspecialedition", "modId": 7})
        gateway.request(request)
        self.assertFalse(request.result["ok"])
        self.assertIn("optional personal Nexus API key", request.result["error"])
        self.assertEqual(len(native.calls), 1)

    def test_raw_callback_rejects_wrong_project_and_malformed_files(self):
        first = self.request()
        self.metadata()
        self.files(mod=8)
        self.assertFalse(first.result["ok"])
        second = self.request()
        self.files(data={"files": "bad"})
        self.assertFalse(second.result["ok"])
        self.assertEqual(self.gateway.pending, {})

    def test_source_destruction_finishes_pending_and_drops_borrowed_object(self):
        request = self.request()
        self.metadata()
        self.source.destroyed.emit()
        self.assertFalse(request.result["ok"])
        self.assertIsNone(self.gateway._raw_source)
        self.assertEqual(self.gateway.pending, {})
        self.assertEqual(self.gateway.file_transport, "qt-raw-bootstrap")

    def test_worker_cannot_dispatch_qt_request(self):
        request = UIRequest("nexus-files", {"game": "skyrimspecialedition", "modId": 7})
        worker = threading.Thread(target=lambda: self.gateway.request(request))
        worker.start()
        worker.join(2)
        self.assertFalse(request.result["ok"])
        self.assertIn("GUI thread", request.result["error"])
        self.assertEqual(self.native.calls, [])

    def test_file_units_and_raw_api_record_names_are_explicit(self):
        rows = normalize_native_files([
            {"file_id": 1, "size": 1234, "file_name": "Legacy.zip"},
            {"file_id": 2, "size_kb": 5678}, {"file_id": True},
            {"file_id": 3, "size_in_bytes": 102400, "size_kb": 1}], raw_api=True)
        self.assertEqual({row["fileId"]: row["sizeInKB"] for row in rows}, {1: 1234, 2: 5678, 3: 100})
        self.assertEqual(rows[-1]["name"], "Legacy.zip")

    def test_explicit_native_rejection_clears_metadata_and_bootstrap_requests(self):
        for kind in ("nexus-metadata", "nexus-files"):
            with self.subTest(kind=kind), patch.object(self.native, "requestDescription", return_value=False):
                request = self.request(kind)
                self.assertFalse(request.result["ok"])
                self.assertIn("declined", request.result["error"])
                self.assertEqual(self.gateway.pending, {})
                self.assertEqual(self.gateway._bootstrap, {})
                self.assertEqual(self.gateway._bootstrap_parent, {})

    def test_explicit_native_file_rejection_after_bootstrap_finishes_parent(self):
        request = self.request()
        with patch.object(self.native, "requestFiles", return_value=False):
            self.metadata()
        self.assertFalse(request.result["ok"])
        self.assertIn("declined", request.result["error"])
        self.assertEqual(self.gateway.pending, {})
        self.assertEqual(self.gateway._bootstrap, {})
        with patch.object(self.native, "requestFiles", return_value=False):
            direct = self.request()
        self.assertFalse(direct.result["ok"])
        self.assertEqual(self.gateway.pending, {})

    def test_file_lookup_does_not_mistake_metadata_readiness_for_native_file_support(self):
        with tempfile.TemporaryDirectory() as temporary:
            service = ArsenalService("", Path(temporary) / "cache.json", lambda req: self.fail("No Qt request expected"),
                {"profileId": "p", "game": "skyrimspecialedition", "items": [{"id": "mod", "nexusId": 7}]})
            self.addCleanup(service.stop)
            service.native_nexus_available = True
            service.native_nexus_files_available = False
            service.native_nexus_files_error = "Missing native Qt helper"
            result = service.fetch_files(7)
            self.assertFalse(result["ok"])
            self.assertIn("Missing native Qt helper", result["error"])
            service.set_key("optional-key")
            with patch.object(service, "_fetch_files_remote", return_value={"ok": True, "files": [], "modId": 7}) as remote:
                result = service.fetch_files(7)
            self.assertTrue(result["ok"])
            remote.assert_called_once_with(7, "skyrimspecialedition", "optional-key", False)


try:
    from PyQt6 import QtCore, sip
except ImportError:
    QtCore = sip = None

_TEST_DIR = os.environ.get("MODHUNTER_QT_BRIDGE_TEST_DIR", "")


@unittest.skipUnless(QtCore is not None and _TEST_DIR, "Requires built C++ Qt helper/fixture and matching PyQt6")
class CompiledQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from mo2_update_arsenal.qt_bridge import NativeQtSender
        cls.app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])
        suffix = ".dll" if os.name == "nt" else ".so"
        directory = Path(_TEST_DIR)
        cls.resolver = NativeQtSender(directory / ("modhunter-qt-bridge" + suffix))
        cls.fixture = ctypes.CDLL(str(directory / ("modhunter-qt-bridge-fixture" + suffix)))
        cls.fixture.modhunter_qt_fixture_create.argtypes = []
        cls.fixture.modhunter_qt_fixture_create.restype = ctypes.c_void_p
        cls.fixture.modhunter_qt_fixture_destroy.argtypes = [ctypes.c_void_p]
        cls.fixture.modhunter_qt_fixture_destroy.restype = None

    def setUp(self):
        class NexusInterface(QtCore.QObject):
            description = QtCore.pyqtSignal(str, int, "QVariant", "QVariant")
            nxmFilesAvailable = QtCore.pyqtSignal(str, int, "QVariant", "QVariant", int)
        self.source = NexusInterface()
        self.pointer = self.fixture.modhunter_qt_fixture_create()
        self.qobject = sip.wrapinstance(self.pointer, QtCore.QObject)
        self.native = DelegatedBridge(self.qobject)
        self.gateway = NativeNexusGateway(SimpleNamespace(createNexusBridge=lambda: self.native), sender_resolver=self.resolver)
        self.source.description.connect(self.qobject.descriptionAvailable)

    def tearDown(self):
        self.fixture.modhunter_qt_fixture_destroy(self.pointer)

    def test_cpp_receiver_protected_sender_gap_is_closed_by_typed_helper(self):
        with self.assertRaisesRegex(RuntimeError, "protected"):
            self.qobject.sender()
        self.assertIsNone(self.resolver.sender(self.qobject))
        request = UIRequest("nexus-metadata", {"game": "skyrimspecialedition", "modId": 7})
        self.gateway.request(request)
        self.source.description.emit("skyrimspecialedition", 7, self.native.calls[-1][-1], {"version": "2"})
        self.assertTrue(request.result["ok"], request.result)
        self.assertEqual(self.gateway.file_transport, "qt-raw-variant")
        self.assertEqual(self.gateway._raw_source, self.source)
        self.assertIsNone(self.resolver.sender(self.qobject))

    def test_cpp_receiver_first_file_bootstrap_and_raw_qvariant_payload(self):
        request = UIRequest("nexus-files", {"game": "skyrimspecialedition", "modId": 7})
        self.gateway.request(request)
        self.source.description.emit("skyrimspecialedition", 7, self.native.calls[-1][-1], {"version": "2"})
        self.assertEqual(self.native.calls[-1][0], "files")
        ticket = self.native.calls[-1][-1]
        self.source.nxmFilesAvailable.emit("skyrimspecialedition", 7, "unowned", {"files": [{"file_id": 99}]}, 2)
        self.assertEqual(request.state, "running")
        self.source.nxmFilesAvailable.emit("skyrimspecialedition", 7, ticket,
            {"files": [{"file_id": 12, "name": "Actual QVariant", "file_name": "Mod.zip", "size_in_bytes": 3072}]}, 3)
        self.assertTrue(request.result["ok"], request.result)
        self.assertEqual(request.result["files"][0]["fileId"], 12)
        self.assertEqual(request.result["files"][0]["sizeInKB"], 3)

    def test_compiled_helper_rejects_worker_thread_before_qobject_use(self):
        failures = []
        def invoke():
            try:
                self.resolver.sender(self.qobject)
            except RuntimeError as exc:
                failures.append(str(exc))
        worker = threading.Thread(target=invoke)
        worker.start()
        worker.join(2)
        self.assertEqual(len(failures), 1)
        self.assertIn("GUI thread", failures[0])

    @staticmethod
    def optimized_native_probe():
        # Execute the actual native acceptance function with the same assert
        # stripping as MO2's optimized embedded Python. The test receiver stays
        # a compiled C++ QObject, not a Python-created stand-in.
        path = Path(__file__).resolve().parents[1] / "native/scripts/mo2_smoke_plugin/modhunter_native_smoke/runner.py"
        module = ast.parse(path.read_text(encoding="utf-8"))
        probe = next(node for node in module.body if isinstance(node, ast.FunctionDef)
                     and node.name == "exercise_native_nexus_binding")
        namespace = {"QtCore": QtCore, "sip": sip, "UIRequest": UIRequest,
                     "hashlib": hashlib, "os": os}
        exec(compile(ast.Module(body=[probe], type_ignores=[]), str(path), "exec", optimize=2), namespace)
        return namespace[probe.name]

    def test_actual_native_probe_completes_and_checks_lifetimes_under_optimized_python(self):
        probe = self.optimized_native_probe()
        helper_hash = hashlib.sha256(self.resolver.path.read_bytes()).hexdigest()
        with patch.dict(os.environ, {"MODHUNTER_NATIVE_SMOKE_QT_BRIDGE_SHA256": helper_hash}):
            result = probe(self.gateway, expected_qt_version=QtCore.qVersion())
        self.assertTrue(result["metadataCompleted"])
        self.assertTrue(result["rawFilesCompleted"])
        self.assertTrue(result["unownedIgnored"])
        self.assertTrue(result["cancelledIgnored"])
        self.assertEqual(result["networkRequests"], 0)
        self.assertFalse(result["accountAuthentication"])
        self.assertEqual(self.native.calls, [])
        self.assertEqual(self.gateway.pending, {})
        self.assertIsNone(self.gateway._raw_source)

    def test_optimized_native_probe_still_rejects_wrong_helper_hash(self):
        probe = self.optimized_native_probe()
        with patch.dict(os.environ, {"MODHUNTER_NATIVE_SMOKE_QT_BRIDGE_SHA256": "0" * 64}), \
                self.assertRaisesRegex(AssertionError, "differs from the compiled fixture"):
            probe(self.gateway, expected_qt_version=QtCore.qVersion())
        self.assertEqual(self.native.calls, [])
        self.assertEqual(self.gateway.pending, {})

    def test_optimized_native_probe_still_rejects_wrong_qt_runtime(self):
        probe = self.optimized_native_probe()
        helper_hash = hashlib.sha256(self.resolver.path.read_bytes()).hexdigest()
        with patch.dict(os.environ, {"MODHUNTER_NATIVE_SMOKE_QT_BRIDGE_SHA256": helper_hash}), \
                self.assertRaises(AssertionError):
            probe(self.gateway, expected_qt_version="0.0.0")
        self.assertEqual(self.native.calls, [])
        self.assertEqual(self.gateway.pending, {})


if __name__ == "__main__":
    unittest.main()
