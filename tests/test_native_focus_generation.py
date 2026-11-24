"""Signed loopback focus requests through the exact owner dispatch and model path.

Organizer/Qt bindings are narrow fixtures; no native Windows/MO2 claim.
"""
from copy import deepcopy
from http.client import HTTPConnection
import json
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from threading import Thread
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from mo2_update_arsenal.bridge import ArsenalService
from mo2_update_arsenal.focus import find_mo2_view, name_column, navigate
from test_focus import MockModel, Organizer, QT, View, Window, widgets
from test_plugin_autostart import plugin_type
from test_plugin_intelligence import GatedRunner, wait_until


class CountedModel(MockModel):
    def __init__(self, names):
        super().__init__(names)
        self.label_reads = 0
        self.on_read = None

    def data(self, index, *_):
        self.label_reads += 1
        if self.on_read is not None:
            f, self.on_read = self.on_read, None
            f()
        return super().data(index)


def catalog(pid="A", count=2300):
    return {"profileId": pid, "profileName": f"Native profile {pid}", "game": "skyrimspecialedition",
            "gameRuntime": "1.6.1170", "loader": "SKSE", "groups": [], "items": [
        {"id": f"{pid}-{i}", "name": f"Fixture {i}", "mo2InternalName": f"Exact installed name {i}",
         "nexusId": i+1, "installedVersion": "1", "flags": []} for i in range(count)]}


class FocusGenerationTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        path = Path(tmp.name)
        self.owner = Queue()
        self.profile_name = "Native profile A"
        self.timers = []
        self.initial = catalog()
        self.service = ArsenalService("fixture", path/"cache.json", self.owner.put, deepcopy(self.initial),
                                      discovery_directory=path/"owned-session")
        self.service.start(); self.addCleanup(self.service.stop)
        self.plugin = plugin_type(self.timers)()
        self.plugin.server = self.service
        self.plugin.org = Organizer({r["mo2InternalName"]: r["name"] for r in self.initial["items"]})
        self.plugin.org.profileName = lambda: self.profile_name
        self.model = CountedModel([r["name"] for r in self.initial["items"]])
        self.view = View(self.model)
        scope = self.plugin._focus_mod.__globals__
        scope.update(navigate=navigate, find_mo2_view=find_mo2_view, name_column=name_column)
        self.window = Window(self.view)
        scope["QtWidgets"] = widgets(self.window)
        for name in ("QModelIndex", "Qt", "QItemSelectionModel"):
            setattr(scope["QtCore"], name, getattr(QT, name))

    def observe_native_row(self, row):
        # A structural Qt index fixture, read by the exact currentChanged
        # handler on the owner. No call straight into the service to simulate
        # recency: native name-column lookup and model label both execute.
        index = SimpleNamespace(
            isValid=lambda: True,
            siblingAtColumn=lambda column: SimpleNamespace(
                data=lambda role: self.model.data(self.model.index(row, column), role)),
        )
        self.plugin._on_native_selection(index, None)

    def request(self, route, data=None, *, authorized=True):
        address = self.service._server.server_port
        conn = HTTPConnection("127.0.0.1", address, timeout=8)
        method = "POST" if data is not None else "GET"
        headers = {"Origin": f"http://127.0.0.1:{address}", "Content-Type": "application/json"}
        if authorized:
            headers["X-Arsenal-Token"] = self.service.token
        conn.request(method, "/api/" + route, json.dumps(data) if data is not None else None, headers)
        response = conn.getresponse()
        value = response.status, json.loads(response.read())
        conn.close()
        return value

    def queue_focus(self, i, **extra):
        data = {"profileId": "A", "id": f"A-{i}", **extra}
        output = Queue()
        def run():
            try:
                output.put(self.request("focus", data))
            except BaseException as exc:
                output.put(exc)
        worker = Thread(target=run, daemon=True)
        worker.start()
        self.addCleanup(worker.join, 9)
        return self.owner.get(timeout=5), output

    def assert_success(self, out, i):
        code, result = out.get(timeout=5)
        self.assertEqual(code, 200)
        revision = self.request("revision")[1]
        self.assertEqual(result, {"ok": True, "selected": f"Fixture {i}", "clearedTextFilter": False,
                                  "message": f"Revealed Fixture {i} in MO2's left pane",
                                  "selectionRevision": revision["selectionRevision"]})
        self.assertEqual(revision["selectedModId"], f"A-{i}")

    def test_success_uses_typed_exact_owner_target_without_copied_rows(self):
        request, out = self.queue_focus(2299, generation=1, name="untrusted browser field")
        with patch.object(self.service, "native_selection_snapshot", side_effect=AssertionError("full row copied")):
            self.assertEqual(request.payload["name"], "Exact installed name 2299")
            self.plugin._dispatch(request)
        self.assertEqual(self.model.label_reads, 2300, "complete ambiguity check")
        self.assertEqual(self.view.selected.row, 2299)
        self.assert_success(out, 2299)

    def test_normal_filter_reveal_and_duplicate_failure_keep_ui_semantics(self):
        self.model.rows = ["Some other visible mod"]
        outer = self
        class Filter:
            clears = 0
            def text(self):
                return "hide this exact name"
            def clear(self):
                self.clears += 1
                outer.model.rows = ["Fixture 12"]

        text_filter = Filter()
        original_find = self.window.findChild
        self.window.findChild = lambda kind, name: text_filter if name == "modFilterEdit" else original_find(kind, name)
        req, out = self.queue_focus(12)
        self.plugin._dispatch(req)
        result = out.get(timeout=5)[1]
        self.assertTrue(result["ok"])
        self.assertTrue(result["clearedTextFilter"])
        self.assertEqual(text_filter.clears, 1)
        previous_selection = self.view.selected
        self.model.rows = ["Fixture 14", "Fixture 14"]
        req, out = self.queue_focus(14)
        self.plugin._dispatch(req)
        duplicate = out.get(timeout=5)[1]
        self.assertEqual(duplicate, {"ok": False, "matchedRows": 2,
              "error": "Several MO2 rows have this display name. Refusing to select the wrong mod."})
        self.assertIs(self.view.selected, previous_selection)
        self.assertEqual(text_filter.clears, 1)

    def test_profile_roundtrip_and_equal_row_replacement_deny_before_traversal(self):
        request, out = self.queue_focus(2299)
        pending = request.state
        for target in ("B", "A"):
            previous = self.profile_name
            self.profile_name = f"Native profile {target}"
            self.plugin._on_profile_changed(previous, self.profile_name)
        # Profile A sends another full catalog with the exact same rows; the
        # old numeric generation is still gone. Neither condition revives it.
        self.service.set_snapshot(deepcopy(self.initial))
        self.plugin._dispatch(request)
        self.assertEqual(pending, "queued")
        self.assertEqual(self.model.label_reads, 0)
        self.assertIsNone(self.view.selected)
        self.assertFalse(out.get(timeout=5)[1]["ok"])
        # No extra refresh was added; the existing coalesced timer is enough.
        self.assertEqual([ms for ms, _ in self.timers], [150])
        fresh, fresh_reply = self.queue_focus(6, generation=2)
        self.plugin._dispatch(fresh)
        self.assert_success(fresh_reply, 6)
        self.assertEqual(self.model.label_reads, 2300)

    def test_latest_valid_service_admission_wins_same_or_different_row_before_old_dispatch(self):
        for old_row, new_row in ((10, 12), (50, 50)):
            self.model.label_reads = 0
            older, old = self.queue_focus(old_row)
            newer, new = self.queue_focus(new_row)
            self.plugin._dispatch(newer)
            self.plugin._dispatch(older)
            self.assertFalse(old.get(timeout=5)[1]["ok"])
            self.assert_success(new, new_row)
            self.assertEqual(self.model.label_reads, 2300)
            self.assertEqual(self.view.selected.row, new_row)

    def test_ingress_uses_actual_valid_service_admission_not_a_browser_click_clock(self):
        # Transport can deliver two UI actions in either order, on identical
        # or different authenticated pages. Claimed timing isn't authority:
        # only the actual admission order and later owned observations count.
        first, older = self.queue_focus(12, generation=1, intent={"client": "page-a", "sequence": 90})
        second, newer = self.queue_focus(34, generation=1, intent={"client": "page-b", "sequence": 1})
        # Not even a later invalid request with a purported new browser clock
        # can preempt the current valid admission.
        code, refused = self.request("focus", {"profileId": "A", "id": "A-5", "generation": 4,
                                             "intent": {"client": "page-b", "sequence": 999}})
        self.assertEqual(code, 200)
        self.assertFalse(refused["ok"])
        self.assertTrue(self.owner.empty())
        self.plugin._dispatch(first)
        self.assertFalse(older.get(timeout=5)[1]["ok"])
        self.assertEqual(self.model.label_reads, 0)
        self.plugin._dispatch(second)
        self.assert_success(newer, 34)
        self.assertEqual(self.model.label_reads, 2300)

    def test_native_selection_before_dispatch_supersedes_browser(self):
        request, out = self.queue_focus(52)
        self.observe_native_row(12)
        self.plugin._dispatch(request)
        self.assertEqual(self.model.label_reads, 1, "only the actual selection read, no stale traversal")
        self.assertEqual(out.get(timeout=5)[1]["ok"], False)
        self.assertEqual(self.request("revision")[1]["selectedModId"], "A-12")

    def test_generation_replaced_inside_traversal_cannot_select_into_new_view(self):
        request, out = self.queue_focus(44)
        self.model.on_read = lambda: self.service.set_snapshot(catalog("B", 3))
        self.plugin._dispatch(request)
        self.assertGreater(self.model.label_reads, 0)
        self.assertIsNone(self.view.selected)
        self.assertIsNone(self.view.selector.index)
        self.assertFalse(out.get(timeout=5)[1]["ok"])
        self.assertEqual(self.request("revision")[1]["selectedModId"], "")

    def test_programmatic_selection_signal_cannot_invalidate_newer_admitted_browser_action(self):
        first, old_out = self.queue_focus(17)
        selected = self.view.selector.select
        next_request = None

        def overlap(index, flags):
            nonlocal next_request
            selected(index, flags)
            if next_request is None:
                next_request = self.queue_focus(24)
            # Same-owner Qt signal caused by our own widget call; this is
            # not a newer keyboard selection by the user.
            self.plugin._on_native_selection(index, None)

        self.view.selector.select = overlap
        self.plugin._dispatch(first)
        self.assertIsNotNone(next_request)
        self.assertEqual(first.state, "completed")
        self.assertFalse(old_out.get(timeout=5)[1]["ok"])
        newer, new_out = next_request
        self.plugin._dispatch(newer)
        self.assert_success(new_out, 24)
        self.assertEqual(self.view.selected.row, 24)

    def test_native_action_completed_but_late_receipt_cannot_overwrite_new_native_selection(self):
        req, out = self.queue_focus(52)
        finish = req.finish
        def finish_then_deliver(result):
            # Our real owner action has succeeded. A newer selection is
            # observed before the HTTP waiter is released (no fake cancel).
            self.assertTrue(result["ok"], result)
            self.observe_native_row(15)
            return finish(result)
        req.finish = finish_then_deliver
        self.plugin._dispatch(req)
        self.assertEqual(req.state, "completed")
        self.assertEqual(self.view.selected.row, 52)
        self.assertFalse(out.get(timeout=5)[1]["ok"])
        self.assertEqual(self.request("revision")[1]["selectedModId"], "A-15")

    def start_audit_during_exact_focus(self):
        runner = GatedRunner()
        self.addCleanup(runner.release_all)
        self.service.intelligence.runner = runner
        # Use the ordinary post-content publication -> capture -> gated
        # helper -> audit observation lifecycle. Do not assign revisions.
        self.service.set_snapshot(deepcopy(self.initial))
        capture = self.owner.get(timeout=5)
        self.assertEqual(capture.cmd, "plugin-audit-input")
        capture.finish({"ok": True, "request": {"profileId": "A", "deep": False}})
        entry = runner.started.get(timeout=5)
        return entry, self.request("revision")[1]["generation"]

    def publish_audit(self, entry, generation):
        entry["release"].set()
        wait_until(lambda: self.service.revision()["generation"] == generation + 1)
        self.assertEqual(self.service.native_snapshot()["items"], self.initial["items"])

    def test_audit_only_change_between_admission_and_owner_dispatch_preserves_focus(self):
        entry, generation = self.start_audit_during_exact_focus()
        # Use the compatibility client path, which the stock native smoke uses.
        request, reply = self.queue_focus(5)
        self.publish_audit(entry, generation)
        self.plugin._dispatch(request)
        self.assert_success(reply, 5)

    def test_audit_only_change_during_traversal_preserves_focus(self):
        entry, generation = self.start_audit_during_exact_focus()
        request, reply = self.queue_focus(5, generation=generation)
        self.model.on_read = lambda: self.publish_audit(entry, generation)
        self.plugin._dispatch(request)
        self.assert_success(reply, 5)
        self.assertEqual(self.model.label_reads, 2300)

    def test_failed_audit_does_not_invalidate_exact_ack_before_delivery(self):
        entry, generation = self.start_audit_during_exact_focus()
        entry["error"] = "Synthetic audit failure"
        request, reply = self.queue_focus(5, generation=generation)
        finish = request.finish

        def deliver(result):
            self.assertTrue(result["ok"], result)
            self.publish_audit(entry, generation)
            return finish(result)

        request.finish = deliver
        self.plugin._dispatch(request)
        self.assert_success(reply, 5)
        self.assertEqual(self.service.intelligence.state()["state"], "failed")

    def test_audit_publication_does_not_accept_stale_browser_or_mask_real_owner_failure(self):
        entry, generation = self.start_audit_during_exact_focus()
        request, reply = self.queue_focus(5, generation=generation)
        self.publish_audit(entry, generation)
        code, rejected = self.request("focus", {"profileId": "A", "id": "A-5", "generation": generation})
        self.assertEqual(code, 200)
        self.assertFalse(rejected["ok"])
        self.assertTrue(self.owner.empty())
        self.model.rows = ["Different fixture"]
        self.plugin._dispatch(request)
        code, rejected = reply.get(timeout=5)
        self.assertEqual(code, 200)
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected.get("matchedRows"), 0)
        self.assertIsNone(self.view.selected)
        self.assertEqual(self.request("revision")[1]["selectedModId"], "")

    def test_audit_only_change_cannot_mask_a_true_same_profile_catalog_republication(self):
        entry, generation = self.start_audit_during_exact_focus()
        request, reply = self.queue_focus(5, generation=generation)
        self.publish_audit(entry, generation)
        self.service.intelligence.runner.available = False
        self.service.set_snapshot(deepcopy(self.initial))
        self.plugin._dispatch(request)
        self.assertFalse(reply.get(timeout=5)[1]["ok"])
        self.assertEqual(self.model.label_reads, 0)

    def test_stale_client_generation_and_unauthenticated_requests_never_enter_owner_queue(self):
        for auth, generation, status in ((True, 2, 200), (True, True, 200), (False, 1, 403)):
            code, result = self.request("focus", {"profileId": "A", "id": "A-10", "generation": generation},
                                        authorized=auth)
            self.assertEqual(code, status)
            self.assertFalse(result["ok"])
        self.assertTrue(self.owner.empty())
        self.assertEqual(self.model.label_reads, 0)
