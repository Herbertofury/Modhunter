"""Exercise actual request/dispatcher code without pretending to run Windows Qt."""
from __future__ import annotations

import ast
import copy
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mo2_update_arsenal.bridge import ArsenalService, UIRequest
from mo2_update_arsenal.native_nexus import NativeNexusGateway


class Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self, *args):
        for callback in self.callbacks:
            callback(*args)


class FakeNexus:
    def __init__(self):
        self.descriptionAvailable, self.filesAvailable, self.requestFailed = Signal(), Signal(), Signal()
        self.calls = []

    def requestDescription(self, *args):
        self.calls.append(args)

    def requestFiles(self, *args):
        self.calls.append(args)


def actual_dispatcher():
    """Compile the real dispatcher body without importing unavailable mobase/PyQt."""
    source = Path(__file__).resolve().parents[1] / "mo2_update_arsenal/plugin.py"
    module = ast.parse(source.read_text("utf-8"))
    cls = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "ModUpdateArsenal")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "_dispatch")
    namespace = {"UIRequest": UIRequest}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), namespace)
    return namespace["_dispatch"]


PROFILE = {"profileId": "p1", "profileName": "One", "game": "skyrimspecialedition",
           "items": [{"id": "live-one", "mo2InternalName": "One", "name": "One",
                      "nexusId": 7, "installedVersion": "1.0"}], "groups": []}


class RequestLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.queued = []
        self.service = ArsenalService("<html></html>", Path(self.temp.name) / "cache.json",
                                      self.queued.append, copy.deepcopy(PROFILE))
        self.addCleanup(self.service.stop)

    def test_expired_queued_mutation_cannot_run_when_qt_recovers(self):
        result = self.service.call_ui("download", {"modId": 7, "fileId": 8}, timeout=0.001)
        self.assertFalse(result["ok"])
        self.assertFalse(result["started"])
        native = SimpleNamespace(_download=Mock())
        actual_dispatcher()(native, self.queued[0])
        native._download.assert_not_called()
        self.assertFalse(self.queued[0].finish({"ok": True, "downloadId": 12}))
        self.assertEqual(self.queued[0].result, result)
        self.assertEqual(self.service._pending_ui, set())

    def test_deadline_rejected_by_dispatch_even_before_waiter_cancels(self):
        request = UIRequest("download", {}, timeout=1)
        with patch("mo2_update_arsenal.bridge.time.monotonic", return_value=request.deadline + 1):
            self.assertFalse(request.begin())
        self.assertTrue(request.event.is_set())
        self.assertEqual(request.state, "cancelled")

    def test_only_one_thread_can_claim_request(self):
        request = UIRequest("download", {})
        gate = threading.Barrier(8)
        results = []
        def claim():
            gate.wait(2)
            results.append(request.begin())
        workers = [threading.Thread(target=claim) for _ in range(8)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(3)
        self.assertEqual(results.count(True), 1)
        self.assertFalse(request.cancel_if_pending())
        self.assertTrue(request.finish({"ok": True}))
        self.assertFalse(request.begin())

    def test_started_mutation_retains_ownership_after_wait_deadline(self):
        accepted, release = threading.Event(), threading.Event()
        native_workers = []
        def submit(request):
            self.assertTrue(request.begin())
            accepted.set()
            def complete():
                release.wait(2)
                request.finish({"ok": True, "downloadId": 71})
            worker = threading.Thread(target=complete)
            native_workers.append(worker)
            worker.start()
        self.service.ui_call = submit
        result = []
        caller = threading.Thread(target=lambda: result.append(self.service.call_ui("download", {}, timeout=.01)))
        caller.start()
        self.assertTrue(accepted.wait(1))
        caller.join(.04)
        self.assertTrue(caller.is_alive(), "An in-flight native mutation must not be reported failed/retryable")
        release.set()
        caller.join(2)
        for worker in native_workers:
            worker.join(2)
        self.assertEqual(result, [{"ok": True, "downloadId": 71}])
        self.assertEqual(self.service._pending_ui, set())

    def test_stop_cancels_queued_work_and_late_dispatch_is_inert(self):
        request = UIRequest("install", {})
        self.service._submit_ui(request)
        self.service.stop()
        self.assertFalse(request.begin())
        self.assertTrue(request.result["cancelled"])
        self.assertEqual(self.service._pending_ui, set())

    def test_dispatch_rechecks_actual_profile_before_debounced_refresh(self):
        request = UIRequest("download", {"profileId": "p1", "modId": 7, "fileId": 8})
        native = SimpleNamespace(server=self.service, org=SimpleNamespace(profileName=lambda: "Switched"),
                                 _download=Mock())
        actual_dispatcher()(native, request)
        native._download.assert_not_called()
        self.assertFalse(request.result["ok"])
        self.assertIn("profile changed", request.result["error"])

    def test_dispatch_rechecks_installed_version_before_mutation(self):
        request = UIRequest("download", {"profileId": "p1", "itemId": "live-one",
                                          "installedVersion": "0.5", "modId": 7, "fileId": 8})
        native = SimpleNamespace(server=self.service, org=SimpleNamespace(profileName=lambda: "One"),
                                 _download=Mock())
        actual_dispatcher()(native, request)
        native._download.assert_not_called()
        self.assertIn("Installed mod changed", request.result["error"])

    def test_dispatch_rechecks_native_version_before_debounced_snapshot_update(self):
        request = UIRequest("download", {"profileId": "p1", "itemId": "live-one",
                                          "installedVersion": "1.0", "modId": 7, "fileId": 8})
        modlist = SimpleNamespace(getMod=lambda name: SimpleNamespace(version=lambda: "2.0"))
        organizer = SimpleNamespace(profileName=lambda: "One", modList=lambda: modlist)
        native = SimpleNamespace(server=self.service, org=organizer, _download=Mock())
        actual_dispatcher()(native, request)
        native._download.assert_not_called()
        self.assertIn("Native installed version changed", request.result["error"])

    def test_dispatch_rechecks_runtime_before_mutation(self):
        request = UIRequest("download", {"profileId": "p1", "gameRuntime": "1.6.1170",
                                          "modId": 7, "fileId": 8})
        native = SimpleNamespace(server=self.service, org=SimpleNamespace(profileName=lambda: "One"), _download=Mock())
        actual_dispatcher()(native, request)
        native._download.assert_not_called()
        self.assertIn("Game runtime changed", request.result["error"])

    def test_valid_native_dispatch_executes_once_and_keeps_real_download_id(self):
        request = UIRequest("download", {"profileId": "p1", "itemId": "live-one",
                                          "installedVersion": "1.0", "modId": 7, "fileId": 8})
        modlist = SimpleNamespace(getMod=lambda name: SimpleNamespace(version=lambda: "1.0"))
        native = SimpleNamespace(server=self.service,
            org=SimpleNamespace(profileName=lambda: "One", modList=lambda: modlist),
            _download=Mock(return_value={"ok": True, "downloadId": 40}))
        dispatch = actual_dispatcher()
        dispatch(native, request)
        dispatch(native, request)
        native._download.assert_called_once_with(7, 8)
        self.assertEqual(request.result, {"ok": True, "downloadId": 40})


class NexusLifetimeTests(unittest.TestCase):
    def setUp(self):
        self.native = FakeNexus()
        self.gateway = NativeNexusGateway(SimpleNamespace(createNexusBridge=lambda: self.native))

    def request(self, kind="nexus-files"):
        request = UIRequest(kind, {"game": "skyrimspecialedition", "modId": 7})
        self.gateway.request(request)
        return request, self.native.calls[-1][-1]

    def test_cancel_cleans_pending_and_late_response_cannot_resurrect_it(self):
        request, ticket = self.request()
        self.assertEqual(len(self.gateway.pending), 1)
        request.cancel()
        self.assertEqual(self.gateway.pending, {})
        old = copy.deepcopy(request.result)
        self.native.filesAvailable.emit("skyrimspecialedition", 7, ticket, [{"fileId": 9}])
        self.assertEqual(request.result, old)

    def test_response_must_match_game_mod_and_operation(self):
        for game, mod_id, response_kind in (("fallout4", 7, "files"), ("skyrimspecialedition", 8, "files"),
                                             ("skyrimspecialedition", 7, "metadata")):
            with self.subTest(game=game, mod_id=mod_id, kind=response_kind):
                request, ticket = self.request()
                if response_kind == "files":
                    self.native.filesAvailable.emit(game, mod_id, ticket, [{"fileId": 9}])
                else:
                    self.native.descriptionAvailable.emit(game, mod_id, ticket, {"version": "2"})
                self.assertFalse(request.result["ok"])
                self.assertIn("identity", request.result["error"])
                self.assertEqual(self.gateway.pending, {})

    def test_cancelled_request_never_reaches_native_repository(self):
        request = UIRequest("nexus-files", {"game": "skyrimspecialedition", "modId": 7})
        request.cancel_if_pending()
        self.gateway.request(request)
        self.assertEqual(self.native.calls, [])
        self.assertEqual(self.gateway.pending, {})

    def test_duplicate_native_callbacks_keep_original_result(self):
        request, ticket = self.request("nexus-metadata")
        self.native.descriptionAvailable.emit("skyrimspecialedition", 7, ticket, {"version": "2"})
        self.native.requestFailed.emit("skyrimspecialedition", 7, 0, ticket, 401, "late failure")
        self.assertTrue(request.result["ok"])
        self.assertEqual(request.result["meta"]["version"], "2")


if __name__ == "__main__":
    unittest.main()
