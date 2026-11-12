"""Native facts, real helper IO and deterministic background lifecycle regressions.

The fixtures model the documented mobase API. They never claim to run MO2;
the subprocess fixtures do execute a real temporary helper on every platform.
"""
from __future__ import annotations

import ast
import copy
import http.client
import json
import queue
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from mo2_update_arsenal.bridge import ArsenalService, UIRequest
from mo2_update_arsenal.intelligence import ModIntelligence
from mo2_update_arsenal.mod_health import parse_nexus_page
from mo2_update_arsenal.plugin_intelligence import (
    RustAuditRunner, attach_native_plugin_state, build_audit_request, native_plugin_facts,
)


GAME = "skyrimspecialedition"
NOW = 1_800_000_000.0


def profile(name="A", game=GAME, count=1):
    return {"profileId": name, "profileName": name, "game": game, "gameRuntime": "1.6.1170",
            "groups": [], "items": [
                {"id": f"m{i}", "mo2InternalName": f"Native {i}", "name": f"Displayed {i}",
                 "nexusId": 77 + i, "installedVersion": "1.0", "enabled": True, "priority": i,
                 "source": {"verified": True, "kind": "project", "type": "nexus",
                            "url": f"https://www.nexusmods.com/{game}/mods/{77 + i}?tab=files"}}
                for i in range(count)]}


def public_evidence(game=GAME, mid=77, *, count="0", checked=NOW):
    url = f"https://www.nexusmods.com/{game}/mods/{mid}?tab=bugs"
    html = f'<li id="mod-page-tab-bugs"><a href="{url}"><span class="tab-label">Bugs</span><span class="alert">{count}</span></a></li>'
    return parse_nexus_page(html, game=game, mod_id=mid, checked_at=checked)


def wait_until(predicate, message="Worker did not complete", timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        threading.Event().wait(0.005)
    raise AssertionError(message)


class FakePluginList:
    def __init__(self, rows):
        self.rows = rows

    def pluginNames(self):
        return list(self.rows)

    def state(self, name):
        return self.rows[name].get("state")

    def loadOrder(self, name):
        return self.rows[name].get("order", -1)

    def origin(self, name):
        return self.rows[name].get("origin", "")

    def isLightFlagged(self, name):
        return self.rows[name].get("flagged", False)

    def hasLightExtension(self, name):
        return self.rows[name].get("extension", False)

    def masters(self, name):
        return self.rows[name].get("masters", [])


class FakeOrganizer:
    def __init__(self, root, snapshot, plugins=None):
        self.root, self.snapshot = root, snapshot
        self.active_profile = snapshot["profileName"]
        self.plugin_list = FakePluginList(plugins or {})
        self.mod_paths = {}
        self.configured = ""
        for row in snapshot["items"]:
            path = root / "mods" / row["mo2InternalName"]
            path.mkdir(parents=True, exist_ok=True)
            self.mod_paths[row["mo2InternalName"]] = path

    def profileName(self):
        return self.active_profile

    def pluginList(self):
        return self.plugin_list

    def modList(self):
        return SimpleNamespace(getMod=lambda name: SimpleNamespace(absolutePath=lambda: str(self.mod_paths[name])))

    def profilePath(self):
        return str(self.root / "profiles" / self.active_profile)

    def managedGame(self):
        return SimpleNamespace(dataDirectory=lambda: SimpleNamespace(absolutePath=lambda: str(self.root / "game" / "Data")))

    def overwritePath(self):
        return str(self.root / "overwrite")

    def pluginSetting(self, plugin, key):
        return self.configured if (plugin, key) == ("ESLifier", "ESLifier Folder") else None

    def resolvePath(self, name):
        origin = self.plugin_list.origin(name)
        path = self.mod_paths.get(origin, self.root / "game" / "Data")
        return str(path / name)


def actual_dispatcher(**extra_namespace):
    path = Path(__file__).resolve().parents[1] / "mo2_update_arsenal" / "plugin.py"
    module = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "ModUpdateArsenal")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "_dispatch")
    namespace = {"UIRequest": UIRequest, "build_audit_request": build_audit_request}
    namespace.update(extra_namespace)
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)
    return namespace["_dispatch"]


class NativeFactsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.snapshot = profile(count=2)

    def organizer(self):
        return FakeOrganizer(self.root, self.snapshot, {
            "Winner.esp": {"state": 2, "order": 2, "origin": "Native 1", "flagged": True,
                           "masters": ["Base.esm", "Inactive.esm", "Late.esm", "Absent.esm"]},
            "Base.esm": {"state": 2, "order": 0, "origin": "data"},
            "Inactive.esm": {"state": 1, "order": 1, "origin": "Native 0"},
            "Late.esm": {"state": 2, "order": 3, "origin": "Native 0"},
        })

    def test_actual_winner_light_state_and_master_issues_are_native_facts(self):
        native, rows, warnings = native_plugin_facts(self.organizer(), self.snapshot)
        winner = rows["m1"]["plugins"][0]
        self.assertEqual(winner["name"], "Winner.esp")
        self.assertTrue(winner["header"]["lightEffective"])
        self.assertEqual(winner["eligibility"]["status"], "already-light")
        self.assertEqual(winner["provenance"]["state"], "unknown")
        self.assertEqual(winner["masterIssues"], [{"name": "Inactive.esm", "status": "inactive"},
                                                  {"name": "Late.esm", "status": "loads-after"},
                                                  {"name": "Absent.esm", "status": "missing"}])
        self.assertEqual(winner["ownerModId"], "m1")
        self.assertEqual(next(p for p in native if p["name"] == "Winner.esp")["path"], str(self.root / "mods" / "Native 1" / "Winner.esp"))
        self.assertFalse(warnings)

    def test_explicit_native_missing_state_is_not_merely_inactive(self):
        org = self.organizer()
        org.plugin_list.rows["Absent.esm"] = {"state": 0, "order": -1, "origin": ""}
        _, rows, _ = native_plugin_facts(org, self.snapshot)
        issues = {issue["name"]: issue["status"] for issue in rows["m1"]["plugins"][0]["masterIssues"]}
        self.assertEqual(issues["Absent.esm"], "missing")

    def test_unavailable_activation_does_not_become_disabled_or_a_complete_inventory(self):
        org = self.organizer()
        org.plugin_list.rows["Winner.esp"]["state"] = None
        native, rows, warnings = native_plugin_facts(org, self.snapshot)
        self.assertIsNone(native)
        self.assertIsNone(rows["m1"]["plugins"][0]["active"])
        self.assertTrue(warnings)
        self.assertFalse(rows["m1"]["inventoryComplete"])

    def test_no_native_light_flag_is_not_an_esl_eligibility_claim(self):
        org = self.organizer()
        org.plugin_list.rows["Winner.esp"]["flagged"] = False
        _, rows, _ = native_plugin_facts(org, self.snapshot)
        self.assertEqual(rows["m1"]["plugins"][0]["eligibility"]["status"], "unknown")
        self.assertEqual(rows["m1"]["plugins"][0]["provenance"]["state"], "unknown")

    def test_audit_paths_come_only_from_native_ownership_and_selected_settings(self):
        org = self.organizer()
        self.snapshot["items"][0].update(path="/untrusted/browser/path", absolutePath="/untrusted/other")
        org.configured = str(self.root / "ESLifier")
        request = build_audit_request(org, self.snapshot, deep=True)
        self.assertTrue(request["deep"])
        self.assertEqual(request["mods"][0]["path"], str(self.root / "mods" / "Native 0"))
        self.assertEqual(request["gameDataPath"], str(self.root / "game" / "Data"))
        self.assertEqual(request["eslifierDataPaths"], [str(self.root / "ESLifier" / "ESLifier_Data")])
        self.assertNotIn("/untrusted/", json.dumps(request))

    def test_request_capture_rejects_real_profile_change_before_debounce(self):
        org = self.organizer()
        org.active_profile = "B"
        with self.assertRaisesRegex(ValueError, "profile changed"):
            build_audit_request(org, self.snapshot, deep=True)

    def test_native_state_hash_changes_for_winner_activation_and_is_time_independent(self):
        org = self.organizer()
        first = attach_native_plugin_state(org, copy.deepcopy(self.snapshot))
        second = attach_native_plugin_state(org, copy.deepcopy(self.snapshot))
        self.assertEqual(first["pluginStateHash"], second["pluginStateHash"])
        org.plugin_list.rows["Winner.esp"]["state"] = 1
        third = attach_native_plugin_state(org, copy.deepcopy(self.snapshot))
        self.assertNotEqual(first["pluginStateHash"], third["pluginStateHash"])

    def test_real_dispatcher_ignores_browser_supplied_analyzer_paths(self):
        org = self.organizer()
        service = ArsenalService("<html/>", self.root / "cache.json", lambda req: None, copy.deepcopy(self.snapshot))
        self.addCleanup(service.stop)
        request = UIRequest("plugin-audit-input", {"profileId": "A", "deep": True,
                            "eslifierDataPaths": ["/browser/selected/path"], "path": "/browser/mod", "executable": "/browser/evil"})
        with patch.object(service, "snapshot", side_effect=AssertionError("Qt capture must not decorate a full catalog")), \
             patch.object(service.intelligence, "decorate", side_effect=AssertionError("Qt capture ran provider health work")):
            actual_dispatcher()(SimpleNamespace(server=service, org=org), request)
        self.assertTrue(request.result["ok"])
        captured = request.result["request"]
        self.assertEqual(captured["eslifierDataPaths"], [])
        self.assertEqual(captured["mods"][0]["path"], str(self.root / "mods" / "Native 0"))
        self.assertNotIn("/browser/", json.dumps(captured))

    def test_folder_dialog_rechecks_actual_profile_after_nested_event_loop(self):
        org = self.organizer()
        service = ArsenalService("<html/>", self.root / "cache.json", lambda req: None, copy.deepcopy(self.snapshot))
        self.addCleanup(service.stop)
        folder = self.root / "selected-eslifier"
        folder.mkdir()
        (folder / "settings.json").write_text("{}", encoding="utf-8")
        def choose(*_args):
            org.active_profile = "B"  # Stored snapshot A has not been debounced yet.
            return str(folder)
        widgets = SimpleNamespace(QFileDialog=SimpleNamespace(getExistingDirectory=choose))
        request = UIRequest("eslifier-data", {"profileId": "A"})
        actual_dispatcher(QtWidgets=widgets)(SimpleNamespace(server=service, org=org), request)
        self.assertFalse(request.result["ok"])
        self.assertIn("profile", request.result["error"].lower())
        self.assertEqual(service.intelligence.configured_paths(), [])

    def test_native_selection_is_indexed_and_duplicate_labels_select_none(self):
        service = ArsenalService("<html/>", self.root / "cache.json", lambda req: None, copy.deepcopy(self.snapshot))
        self.addCleanup(service.stop)
        initial_generation = service.revision()["generation"]
        with patch.object(service, "snapshot", side_effect=AssertionError("Selection walked the catalog")), \
             patch.object(service.intelligence, "decorate", side_effect=AssertionError("Selection built health")):
            service.set_selected_by_label("Displayed 0")
            self.assertEqual(service.revision()["selectedModId"], "m0")
            self.assertEqual(service.revision()["generation"], initial_generation)
            changed = copy.deepcopy(self.snapshot)
            changed["items"][1]["name"] = "Displayed 0"
            service.set_snapshot(changed)
            service.set_selected_by_label("Displayed 0")
            self.assertEqual(service.revision()["selectedModId"], "")


HELPER = '''import json, pathlib, sys, time
if sys.argv[1:] != ["audit-plugins", "--request", "-"]:
    raise SystemExit(17)
request = json.load(sys.stdin)
mode = request.get("mode", "ok")
if mode == "error":
    print("helper diagnostic", file=sys.stderr)
    raise SystemExit(7)
if mode == "timeout":
    time.sleep(10)
if mode == "oversize":
    sys.stdout.buffer.write(b"x" * 32000001)
    sys.stdout.flush()
    time.sleep(0.5)
    pathlib.Path(request["marker"]).write_text("output was not capped while running")
    time.sleep(0.5)
    raise SystemExit(0)
result = {"schemaVersion": 2 if mode == "schema" else 1,
          "profileId": "wrong" if mode == "profile" else request.get("profileId"),
          "mods": {}, "warnings": [], "depth": "records" if request.get("deep") else "header",
          "receivedPath": request.get("mods", [{}])[0].get("path")}
print(json.dumps(result))
'''


class RustRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.helper = self.root / "fixture_helper.py"
        self.helper.write_text(HELPER, encoding="utf-8")
        original_popen = subprocess.Popen
        self.invocations = []
        def portable_python_helper(argv, **kwargs):
            # Only the platform's launching adapter is replaced; the runner's
            # actual pipes/files, bytes, timeout and process handling execute.
            self.invocations.append(argv)
            return original_popen([sys.executable, str(self.helper), *argv[1:]], **kwargs)
        self.patcher = patch("mo2_update_arsenal.plugin_intelligence.subprocess.Popen", side_effect=portable_python_helper)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_real_helper_request_response_and_native_warnings(self):
        request = {"profileId": "A", "deep": True, "mods": [{"path": str(self.root / "owned mod")}],
                   "nativeWarnings": ["Native activation unavailable"]}
        result = RustAuditRunner(self.helper, timeout=2).run(request)
        self.assertEqual(result["receivedPath"], str(self.root / "owned mod"))
        self.assertEqual(result["depth"], "records")
        self.assertIn("Native activation unavailable", result["warnings"])
        self.assertEqual(self.invocations[0], [str(self.helper), "audit-plugins", "--request", "-"])

    def test_bad_exit_schema_profile_and_timeout_are_clear_failures(self):
        for mode, message in (("error", "helper diagnostic"), ("schema", "schema"), ("profile", "profile"), ("timeout", "time limit")):
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(RuntimeError, message):
                    RustAuditRunner(self.helper, timeout=0.15 if mode == "timeout" else 2).run({"profileId": "A", "mode": mode})

    def test_missing_helper_is_graceful_and_request_size_checked_before_launch(self):
        with self.assertRaisesRegex(RuntimeError, "not installed"):
            RustAuditRunner(self.root / "missing").run({"profileId": "A"})
        with self.assertRaisesRegex(ValueError, "bound"):
            RustAuditRunner(self.helper).run({"profileId": "A", "padding": "x" * 32_000_000})
        self.assertFalse(self.invocations)

    def test_oversized_output_stops_running_helper_before_it_can_continue(self):
        marker = self.root / "continued-after-limit.txt"
        with self.assertRaisesRegex(RuntimeError, "bound|output|response"):
            RustAuditRunner(self.helper, timeout=3).run({"profileId": "A", "mode": "oversize", "marker": str(marker)})
        self.assertFalse(marker.exists(), "Output size must be enforced while the helper runs, not after it exits")

    def test_superseded_read_stops_its_own_helper_without_waiting_for_the_audit_deadline(self):
        cancel = threading.Event()
        errors = []
        def run():
            try:
                RustAuditRunner(self.helper, timeout=30).run_cancellable({"profileId": "A", "mode": "timeout"}, cancel)
            except RuntimeError as exc:
                errors.append(str(exc))
        worker = threading.Thread(target=run)
        worker.start()
        wait_until(lambda: bool(self.invocations))
        cancel.set()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive(), "A superseded scan must release the worker for the current profile")
        self.assertTrue(errors and "superseded" in errors[0])


class GatedRunner:
    available = True

    def __init__(self):
        self.calls = []
        self.started = queue.Queue()

    def run(self, request):
        entry = {"request": copy.deepcopy(request), "release": threading.Event(), "index": len(self.calls) + 1}
        self.calls.append(entry)
        self.started.put(entry)
        if not entry["release"].wait(3):
            raise RuntimeError("Test did not release audit")
        if entry.get("error"):
            raise RuntimeError(entry["error"])
        return {"schemaVersion": 1, "profileId": request["profileId"], "depth": "records" if request.get("deep") else "header",
                "checkedAt": str(entry["index"]), "mods": {"m0": {"status": "from-call-" + str(entry["index"]), "plugins": []}},
                "plugins": [], "warnings": []}

    def release_all(self):
        for entry in self.calls:
            entry["release"].set()


class GatedPages:
    def __init__(self):
        self.started = queue.Queue()
        self.calls = []
        self.lock = threading.Lock()
        self.active = self.maximum = 0

    def fetch(self, game, mid, *, force=False):
        entry = {"identity": (game, mid), "release": threading.Event(), "force": force}
        with self.lock:
            self.calls.append(entry)
            self.active += 1
            self.maximum = max(self.maximum, self.active)
        self.started.put(entry)
        try:
            if not entry["release"].wait(3):
                raise RuntimeError("Test did not release page")
            return public_evidence(game, mid, checked=time.time())
        finally:
            with self.lock:
                self.active -= 1

    def release_all(self):
        for entry in self.calls:
            entry["release"].set()


class IntelligenceLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.captures = []
        def native(request):
            request.begin()
            self.captures.append(request)
            if request.cmd == "plugin-audit-input":
                request.finish({"ok": True, "request": {"profileId": request.payload["profileId"],
                    "game": GAME, "deep": request.payload["deep"], "mods": []}})
            else:
                request.finish({"ok": False, "cancelled": True})
        self.service = ArsenalService("<html/>", self.root / "cache.json", native, profile(count=3))
        self.runner, self.pages = GatedRunner(), GatedPages()
        self.coordinator = ModIntelligence(self.service, self.root / "intelligence.json", runner=self.runner, page_client=self.pages)
        self.service.intelligence = self.coordinator
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.service.stop()
        self.runner.release_all()
        self.pages.release_all()
        wait_until(lambda: not self.coordinator._worker_active and not self.coordinator._health_active)

    def finish_audit(self, entry):
        entry["release"].set()
        wait_until(lambda: not self.coordinator._worker_active)

    def test_profile_aba_discards_old_completion_before_new_job_can_finish(self):
        self.coordinator.start_audit("A", deep=True)
        old = self.runner.started.get(timeout=1)
        self.service.set_snapshot(profile("B", count=3))
        self.service.set_snapshot(profile("A", count=3))
        old["release"].set()
        new = self.runner.started.get(timeout=1)
        self.assertEqual(new["request"]["profileId"], "A")
        self.assertIsNone(self.coordinator._result)
        self.assertEqual(self.coordinator.state()["state"], "running")
        self.finish_audit(new)
        self.assertEqual(self.coordinator._result["mods"]["m0"]["status"], "from-call-2")

    def test_profile_switch_discards_old_failure_and_worker_does_not_hold_service_lock(self):
        self.coordinator.start_audit("A", deep=False)
        old = self.runner.started.get(timeout=1)
        revision = self.service.revision()
        self.assertEqual(revision["profileId"], "A")
        self.service.set_snapshot(profile("B", count=3))
        old["error"] = "obsolete profile failure"
        old["release"].set()
        new = self.runner.started.get(timeout=1)
        self.assertEqual(self.coordinator.state()["profileId"], "B")
        self.assertNotIn("error", self.coordinator.state())
        self.finish_audit(new)

    def test_header_to_deep_upgrade_coalesces_without_overlapping_helpers(self):
        self.coordinator.start_audit("A", deep=False)
        header = self.runner.started.get(timeout=1)
        for _ in range(4):
            self.assertTrue(self.coordinator.start_audit("A", deep=True)["ok"])
        header["release"].set()
        deep = self.runner.started.get(timeout=1)
        self.assertTrue(deep["request"]["deep"])
        self.coordinator.start_audit("A", deep=True)
        self.finish_audit(deep)
        self.assertEqual(len(self.runner.calls), 2)
        self.assertEqual(self.coordinator.state()["depth"], "records")

    def test_stopped_service_discards_inflight_audit(self):
        self.coordinator.start_audit("A", deep=True)
        running = self.runner.started.get(timeout=1)
        self.service.stop()
        generation = self.service._generation
        self.finish_audit(running)
        self.assertIsNone(self.coordinator._result)
        self.assertEqual(self.service._generation, generation)

    def test_missing_rust_keeps_native_state_and_health_available(self):
        self.runner.available = False
        local = {"status": "native-headers", "plugins": [{"name": "Light.esp", "header": {"lightEffective": True}}]}
        self.service._items_by_id["m0"]["localAnalysis"] = local
        self.assertFalse(self.coordinator.start_audit("A")["ok"])
        snapshot = self.service.selection_snapshot(["m0"])
        self.assertEqual(snapshot["items"][0]["localAnalysis"]["plugins"][0]["name"], "Light.esp")
        self.assertEqual(snapshot["items"][0]["modHealth"]["bugs"]["state"], "unknown")
        self.assertEqual(self.coordinator.state()["state"], "unavailable")
        self.assertFalse(self.captures)

    def test_snapshot_preserves_old_metadata_freshness_and_observed_zero(self):
        self.service.cache.set(GAME, 77, {"checkedAt": NOW - 100_000, "meta": {"status": "published", "updated_timestamp": NOW - 200_000}})
        self.coordinator._evidence[f"{GAME}/77"] = public_evidence()
        with patch("mo2_update_arsenal.intelligence.time.time", return_value=NOW):
            result = self.coordinator.health_state("m0", "A")
        self.assertTrue(result["ok"])
        self.assertEqual(result["health"]["page"]["freshness"]["state"], "stale")
        self.assertEqual(result["health"]["bugs"]["state"], "available")
        self.assertEqual(result["health"]["bugs"]["displayedCount"], 0)
        self.assertIsNone(result["health"]["bugs"]["open"])

    def test_catalog_summaries_are_compact_and_inspector_retains_full_evidence(self):
        local = {"status": "already-light", "plugins": [{"name": "Light.esp", "winner": True,
                 "header": {"lightEffective": True}, "fingerprint": {"sha256": "a" * 64},
                 "path": str(self.root / "mods" / "Native 0" / "Light.esp"), "eligibility": {"status": "already-light"}}]}
        self.service._items_by_id["m0"]["localAnalysis"] = local
        self.service.cache.set(GAME, 77, {"checkedAt": NOW, "meta": {"status": "published", "updated_timestamp": NOW - 200}})
        self.coordinator._evidence[f"{GAME}/77"] = public_evidence()
        with patch("mo2_update_arsenal.intelligence.time.time", return_value=NOW):
            compact = self.service.snapshot()["items"][0]
            inspected = self.coordinator.health_state("m0", "A")
        self.assertEqual(compact["modHealth"]["bugs"]["displayedCount"], 0)
        self.assertNotIn("actions", compact["modHealth"])
        self.assertNotIn("links", compact["modHealth"])
        self.assertNotIn("fingerprint", compact["localAnalysis"]["plugins"][0])
        self.assertIn("links", inspected["health"])
        self.assertIn("actions", inspected["health"])
        self.assertEqual(inspected["localAnalysis"]["plugins"][0]["fingerprint"]["sha256"], "a" * 64)

    def test_caller_snapshot_mutation_cannot_poison_cached_health_summary(self):
        with patch("mo2_update_arsenal.intelligence.time.time", return_value=NOW):
            first = self.service.snapshot()
            self.assertEqual(first["items"][0]["modHealth"]["bugs"]["state"], "unknown")
            first["items"][0]["modHealth"]["bugs"]["state"] = "disabled"
            second = self.service.snapshot()
        self.assertEqual(second["items"][0]["modHealth"]["bugs"]["state"], "unknown")

    def test_provider_cache_keys_include_game_and_ignore_unrelated_external_source(self):
        self.runner.available = False
        self.coordinator._evidence[f"{GAME}/77"] = public_evidence(count="9", checked=time.time())
        self.service.set_snapshot(profile("B", game="fallout4", count=3))
        health = self.coordinator.health_state("m0", "B")["health"]
        self.assertEqual(health["bugs"]["state"], "unknown")
        self.assertIsNone(health["bugs"]["displayedCount"])
        self.service._items_by_id["m0"]["source"] = {"verified": True, "kind": "project", "url": "https://github.com/example/project"}
        self.assertFalse(self.coordinator.start_health("m0", "B")["ok"])
        self.assertFalse(self.pages.calls)

    def test_health_worker_bound_and_same_project_coalescing(self):
        self.assertTrue(self.coordinator.start_health("m0", "A")["ok"])
        first = self.pages.started.get(timeout=1)
        self.assertTrue(self.coordinator.start_health("m0", "A")["ok"])
        self.assertTrue(self.coordinator.start_health("m1", "A")["ok"])
        second = self.pages.started.get(timeout=1)
        self.assertFalse(self.coordinator.start_health("m2", "A")["ok"])
        self.assertEqual(self.pages.maximum, 2)
        self.assertEqual(len(self.pages.calls), 2)
        first["release"].set()
        second["release"].set()
        wait_until(lambda: not self.coordinator._health_active)
        self.assertTrue(self.coordinator.health_state("m0", "A")["health"]["bugs"]["displayedCount"] == 0)

    def test_failed_health_evidence_obeys_short_retry_ttl(self):
        saved = public_evidence(checked=NOW - 61)
        saved["fetch"]["state"] = "forbidden"
        saved["bugs"].update(state="unavailable", displayedCount=None)
        saved["cache"] = {"expiresAt": NOW - 1, "hit": False}
        self.coordinator._evidence[f"{GAME}/77"] = saved
        with patch("mo2_update_arsenal.intelligence.time.time", return_value=NOW):
            self.coordinator.start_health("m0", "A")
            try:
                started = self.pages.started.get(timeout=0.5)
            except queue.Empty:
                self.fail("An expired failed page check was incorrectly cached for the full successful-page TTL")
            started["release"].set()
            wait_until(lambda: not self.coordinator._health_active)

    def test_page_changed_observation_survives_repeated_snapshot_reads(self):
        self.service.cache.set(GAME, 77, {"checkedAt": NOW - 100, "meta": {"status": "published", "updated_timestamp": NOW - 1000}})
        with patch("mo2_update_arsenal.intelligence.time.time", return_value=NOW):
            self.coordinator.health_state("m0", "A")
            self.service.cache.set(GAME, 77, {"checkedAt": NOW, "meta": {"status": "published", "updated_timestamp": NOW - 200}})
            first = self.coordinator.health_state("m0", "A")["health"]
            second = self.coordinator.health_state("m0", "A")["health"]
        self.assertTrue(first["page"]["changedSincePrevious"])
        self.assertTrue(second["page"]["changedSincePrevious"], "Reading a snapshot must not consume the page-change observation")
        self.assertIn("review-page-change", [action["id"] for action in second["actions"]],
                      "The suggested review action must survive together with the observation")

    def test_failed_configuration_save_restores_previous_effective_folder(self):
        previous = self.root / "old-eslifier"
        selected = self.root / "new-eslifier"
        selected.mkdir()
        (selected / "settings.json").write_text("{}", encoding="utf-8")
        self.coordinator._configured = [str(previous)]
        blocked_destination = self.root / "directory-instead-of-save-file"
        blocked_destination.mkdir()
        self.coordinator.path = blocked_destination
        result = self.coordinator.configure_path(str(selected), "A")
        self.assertFalse(result["ok"])
        self.assertEqual(self.coordinator.configured_paths(), [str(previous)],
                         "A failed save must not silently change the effective configured folder")
        self.assertFalse(self.runner.calls)

    def test_http_folder_configuration_ignores_browser_paths_and_requires_native_dialog(self):
        self.runner.available = False
        self.service.start()
        port = self.service._server.server_port
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        try:
            payload = {"profileId": "A", "path": "/browser/injected/path", "eslifierDataPaths": ["/browser/other"], "executable": "/browser/evil"}
            connection.request("POST", "/api/eslifier-data", body=json.dumps(payload), headers={
                "Content-Type": "application/json", "X-Arsenal-Token": self.service.token,
                "Origin": f"http://127.0.0.1:{port}"})
            response = connection.getresponse()
            result = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertTrue(result["cancelled"])
        finally:
            connection.close()
        self.assertEqual(self.captures[-1].cmd, "eslifier-data")
        self.assertEqual(self.captures[-1].payload, {"profileId": "A"})


if __name__ == "__main__":
    unittest.main()
