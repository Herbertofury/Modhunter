from __future__ import annotations

import builtins
import json
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, fields, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, get_ident
from types import SimpleNamespace
from unittest.mock import patch

from mo2_update_arsenal import model, snapshot_projection as projection


class _Clock:
    @staticmethod
    def now(tz):
        return datetime(2026, 1, 2, tzinfo=timezone.utc)


class _Native:
    """Ensure a Mobase-like access from the worker fails even in a fixture."""
    def __init__(self, **methods):
        self._methods = methods
        self._thread = get_ident()

    def __getattr__(self, name):
        if get_ident() != self._thread:
            raise AssertionError("native binding accessed off capture thread")
        try:
            return self._methods[name]
        except KeyError:
            raise AttributeError(name) from None


def _mod(label, *, path="", notes="", url="", nexus=0, version="0", latest="None", separator=False):
    return _Native(name=lambda: label, isSeparator=lambda: separator, absolutePath=lambda: str(path),
                   notes=lambda: notes, url=lambda: url, nexusId=lambda: nexus, version=lambda: version,
                   newestVersion=lambda: latest, repository=lambda: "fixture-repository")


def _organizer(rows, *, profile="Fixture", game="Skyrim Special Edition", fallback=False, states=None, priorities=None):
    keys = tuple(rows)
    by_priority = priorities if priorities is not None else dict(zip(keys, range(len(keys))))
    methods = dict(getMod=rows.get, priority=by_priority.__getitem__,
                   state=lambda name: (states or {}).get(name, 0x40), allMods=lambda: reversed(keys))
    if not fallback:
        methods["allModsByProfilePriority"] = lambda requested: keys if requested == profile else ()
    return _Native(
        managedGame=lambda: _Native(gameNexusName=lambda: game, gameVersion=lambda: "1.5.97.0"),
        profileName=lambda: profile, basePath=lambda: "Fixture portable base",
        profile=lambda: profile, modList=lambda: _Native(**methods),
    )


def _same_primitive_tree(value, test):
    if is_dataclass(value):
        test.assertFalse(callable(value))
        for field in fields(value):
            _same_primitive_tree(getattr(value, field.name), test)
    elif type(value) is tuple:
        for entry in value:
            _same_primitive_tree(entry, test)
    else:
        test.assertIn(type(value), (str, int, bool))


class SnapshotProjectionTest(unittest.TestCase):
    def new_metadata(self, root, name, source="", *, disabled=False, dll=False):
        folder = root / name
        plugin_dir = folder / "SKSE" / "Plugins"
        plugin_dir.mkdir(parents=True)
        (folder / "meta.ini").write_text(
            f"[General]\nhasCustomUrl={'false' if disabled else 'true'}\nurl={source}\n", encoding="utf-8",
        )
        (plugin_dir / "folder.dll").mkdir()
        (plugin_dir / "unrelated.toml").write_text("inert", encoding="utf-8")
        if dll:
            (plugin_dir / "library.DLL").write_bytes(b"fixture, not an executable")
        return folder

    def test_real_files_frozen_capture_and_ordered_projection_match_shipped_model(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            occupied = self.new_metadata(root, "one", "https://modrinth.com/mod/file-metadata", dll=True)
            disabled = self.new_metadata(root, "two", "https://github.com/fixture/disabled", disabled=True)
            fallback = self.new_metadata(root, "three", "https://github.com/fixture/fallback")
            old = self.new_metadata(root, "four", "https://github.com/fixture/old", disabled=True)
            rows = {
                "entry-0": _mod("Unsorted custom", path=occupied, url="https://github.com/fixture/native?token=hidden#about", nexus=125,
                                version="1.0a", latest="2.0"),
                "separator-1": _mod("Second group", separator=True),
                "empty-or-raced": None,
                "entry-2": _mod("Nexus fallback", path=disabled, url="https://github.com/fixture/no",
                                notes="https://modrinth.com/mod/related", nexus=52),
                "entry-3": _mod("Metadata fallback", path=fallback, url="http://not-accepted.example/", nexus=-2),
                "separator-2": _mod("Final group", separator=True),
                "entry-4": _mod("Notes advisory", path=old, notes="https://modrinth.com/mod/related"),
                "entry-5": _mod("Unknown source"),
            }
            states = {"entry-0": 0x40, "entry-2": 0x02, "entry-3": "ModState.ACTIVE", "entry-4": 0}
            priorities = dict(zip(rows, (11, 20, 22, "bad", 48, 51, 56, 90)))
            org = _organizer(rows, states=states, priorities=priorities)
            with (patch.dict(sys.modules, {"mobase": SimpleNamespace(ModState=SimpleNamespace(ACTIVE=0x40))}),
                  patch.object(model, "datetime", _Clock), patch.object(projection, "datetime", _Clock)):
                expected = model.take_snapshot(org, metadata_cache=model.LocalMetadataCache(), force_metadata=True)
                with (patch.object(Path, "stat", side_effect=AssertionError("capture must not stat paths")),
                      patch.object(Path, "open", side_effect=AssertionError("capture must not open paths"))):
                    captured = projection.capture_snapshot(org)
                _same_primitive_tree(captured, self)
                with self.assertRaises(FrozenInstanceError):
                    captured.profile = "wrong profile"
                with self.assertRaises(FrozenInstanceError):
                    captured.mods[0].native_url = "https://wrong.example/"
                del org, rows
                real_import = builtins.__import__

                def no_mobase(name, *args, **kwargs):
                    if name == "mobase":
                        raise AssertionError("projection must not import mobase")
                    return real_import(name, *args, **kwargs)

                with ThreadPoolExecutor(max_workers=1) as worker, patch("builtins.__import__", no_mobase):
                    actual = worker.submit(projection.project_snapshot, captured,
                                           metadata_cache=model.LocalMetadataCache(), force_metadata=True).result()

            # Full serialized equality, including group order, every row and content hash.
            self.assertEqual(json.dumps(expected), json.dumps(actual))
            self.assertEqual(["github", "nexus", "github", "modrinth", "unknown"],
                             [row["source"]["type"] for row in actual["items"]])
            self.assertEqual(["DLL"], actual["items"][0]["flags"])
            self.assertNotIn("hidden", json.dumps(actual))
            self.assertNotIn(str(root), json.dumps(actual))
            self.assertTrue(actual["items"][0]["enabled"])
            self.assertFalse(actual["items"][1]["enabled"])
            self.assertEqual("advisory", actual["items"][3]["source"]["kind"])

    def test_fallback_mod_order_duplicates_and_explicit_runtime(self):
        rows = {"later": _mod("Later"), "first": _mod("First")}
        org = _organizer(rows, fallback=True, game="<invalid>", priorities={"later": 8, "first": 1})
        with patch.object(model, "datetime", _Clock), patch.object(projection, "datetime", _Clock):
            actual = projection.project_snapshot(projection.capture_snapshot(org, "2.6"))
            self.assertEqual(model.take_snapshot(org, "2.6"), actual)
        self.assertEqual(["first", "later"], [row["mo2InternalName"] for row in actual["items"]])
        self.assertEqual(("unknown", "2.6"), (actual["game"], actual["gameRuntime"]))

        repeated = _Native(allModsByProfilePriority=lambda _: ("same", "same"),
                           getMod=lambda _: _mod("Same"), priority=lambda _: 0)
        org = _organizer({})
        org._methods["modList"] = lambda: repeated
        with self.assertRaisesRegex(ValueError, "Duplicate MO2 mod ID"):
            model.take_snapshot(org)
        with self.assertRaisesRegex(ValueError, "Duplicate MO2 mod ID"):
            projection.project_snapshot(projection.capture_snapshot(org))

    def test_cache_force_observes_replacement_and_successful_retain(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path_a = self.new_metadata(root, "a", "https://github.com/fixture/v1", dll=True)
            path_b = self.new_metadata(root, "b", "https://github.com/fixture/second")
            a = _organizer({"m": _mod("Same name", path=path_a)}, profile="A")
            b = _organizer({"m": _mod("Same name", path=path_b)}, profile="B")
            cache = model.LocalMetadataCache(ttl=200)
            cap_a, cap_b = projection.capture_snapshot(a), projection.capture_snapshot(b)
            run = lambda cap, **kw: projection.project_snapshot(cap, metadata_cache=cache, **kw)
            initial = run(cap_a)
            (path_a / "replace").write_text("[General]\nurl=https://github.com/fixture/v2\n", encoding="utf-8")
            os.replace(path_a / "replace", path_a / "meta.ini")
            (path_a / "SKSE" / "Plugins" / "library.DLL").unlink()
            current = run(cap_a)
            self.assertEqual("https://github.com/fixture/v2", current["items"][0]["source"]["url"])
            self.assertEqual([], current["items"][0]["flags"])
            self.assertEqual(initial["profileId"], current["profileId"])
            self.assertNotEqual(initial["build"]["contentHash"], current["build"]["contentHash"])
            with patch.object(cache, "_signature", return_value=("unchanged coarse filesystem",)):
                run(cap_a, force_metadata=True)
                (path_a / "meta.ini").write_text("[General]\nurl=https://github.com/fixture/v3\n", encoding="utf-8")
                self.assertEqual(current["build"]["contentHash"], run(cap_a)["build"]["contentHash"])
                self.assertEqual("https://github.com/fixture/v3", run(cap_a, force_metadata=True)["items"][0]["source"]["url"])
            switched = run(cap_b)
            self.assertNotEqual(current["profileId"], switched["profileId"])
            self.assertEqual({cache._key(path_b)}, set(cache._entries))
            self.assertEqual(initial["profileId"], run(cap_a)["profileId"])
            self.assertEqual({cache._key(path_a)}, set(cache._entries))

    @unittest.skipIf(os.name == "nt", "POSIX link fixture; no Windows reparse inference")
    def test_symlinked_path_uses_same_existing_metadata_policy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = self.new_metadata(root, "real", "https://github.com/fixture/shared", dll=True)
            linked = root / "linked"
            linked.symlink_to(target, target_is_directory=True)
            org = _organizer({"a": _mod("Linked", path=linked)})
            with patch.object(model, "datetime", _Clock), patch.object(projection, "datetime", _Clock):
                baseline = model.take_snapshot(org, metadata_cache=model.LocalMetadataCache())
                actual = projection.project_snapshot(projection.capture_snapshot(org),
                                                     metadata_cache=model.LocalMetadataCache())
            self.assertEqual(baseline, actual)
            self.assertEqual(["DLL"], actual["items"][0]["flags"])

    def test_cancel_during_real_metadata_keeps_last_complete_cache_and_no_partial_result(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = self.new_metadata(root, "old", "https://github.com/fixture/old")
            first = self.new_metadata(root, "first", "https://github.com/fixture/first")
            next_path = self.new_metadata(root, "next", "https://github.com/fixture/next")
            last = projection.capture_snapshot(_organizer({"old": _mod("Old", path=old)}, profile="Old"))
            replacement = projection.capture_snapshot(_organizer(
                {"first": _mod("First", path=first), "next": _mod("Next", path=next_path)}, profile="Next"))
            cache = model.LocalMetadataCache()
            cancellation, started, continue_io = Event(), Event(), Event()
            real_inspect = cache.inspect

            def inspect(path, *args, **kwargs):
                if path == first:
                    started.set()
                    if not continue_io.wait(4):
                        raise AssertionError("test deadlocked")
                return real_inspect(path, *args, **kwargs)

            published = projection.project_snapshot(last, metadata_cache=cache)
            with ThreadPoolExecutor(max_workers=1) as worker, patch.object(cache, "inspect", side_effect=inspect) as calls:
                future = worker.submit(projection.project_snapshot, replacement, metadata_cache=cache,
                                       force_metadata=True, cancel_event=cancellation)
                try:
                    self.assertTrue(started.wait(4))
                    cancellation.set()
                finally:
                    continue_io.set()
                with self.assertRaises(projection.SnapshotCancelled):
                    future.result(timeout=4)
                self.assertEqual([first], [call.args[0] for call in calls.call_args_list])
            self.assertEqual("Old", published["profileName"])
            self.assertIn(cache._key(old), cache._entries)
            succeeding = projection.project_snapshot(replacement, metadata_cache=cache, force_metadata=True)
            self.assertEqual(["First", "Next"], [row["name"] for row in succeeding["items"]])
            self.assertNotIn(cache._key(old), cache._entries)

    def test_pre_cancelled_empty_inventory_does_not_prune_cache(self):
        capture = projection.capture_snapshot(_organizer({}))
        cancellation = Event()
        cancellation.set()
        cache = model.LocalMetadataCache()
        with patch.object(cache, "retain") as retain:
            with self.assertRaises(projection.SnapshotCancelled):
                projection.project_snapshot(capture, metadata_cache=cache, cancel_event=cancellation)
            retain.assert_not_called()


class IncrementalSnapshotCaptureTest(unittest.TestCase):
    def test_one_name_iterator_bounded_calls_and_same_full_serialized_projection(self):
        # Large whole-profile inventory; no events or sleeps made by the model.
        # Mix ordered separators, dropped rows and ordinary rows.
        count = 1200
        names = [f"mod-{i}" for i in range(count)]
        self.assertEqual(count, len(names))
        expected_names = tuple(names)
        rows = {name: _mod(name, separator=(i % 67 == 1), nexus=i) if i % 41 != 2 else None
                for i, name in enumerate(names)}
        baseline_org = _organizer(rows)
        calls = []
        checks = []
        slices = []

        class Once:
            used = False

            def __iter__(self):
                self.assert_unused()
                self.used = True
                return iter(names)

            def assert_unused(self):
                if self.used:
                    raise AssertionError("native iterable was consumed again")

        native_names = Once()

        def enumerate_once(_profile):
            calls.append(("names", None))
            return native_names

        def get_mod(name):
            calls.append(("getMod", name))
            return rows.get(name)

        def is_current():
            checks.append(len(calls))
            return True

        org = _organizer(rows)
        original_ml = org.modList()
        original_ml._methods.update(allModsByProfilePriority=enumerate_once, getMod=get_mod)
        org._methods["modList"] = lambda: original_ml
        capture = projection.begin_snapshot_capture(org, is_current=is_current)
        self.assertEqual([("names", None)], calls, "begin does not look up any row")
        self.assertEqual([0, 1], checks, "native enumeration is generation-fenced too")
        names[:] = ["never admitted"]
        while True:
            calls_before, checks_before = len(calls), len(checks)
            output = capture.step(max_rows=64, max_seconds=None)
            delta = len(calls) - calls_before
            self.assertEqual([calls_before, len(calls)], checks[checks_before:])
            self.assertLessEqual(delta, 64)
            slices.append(delta)
            if output is not None:
                break
        self.assertEqual([64] * 18 + [48], slices)
        self.assertEqual(list(expected_names), [name for function, name in calls if function == "getMod"])
        self.assertEqual(projection.capture_snapshot(baseline_org), output)
        with patch.object(model, "datetime", _Clock), patch.object(projection, "datetime", _Clock):
            self.assertEqual(json.dumps(model.take_snapshot(baseline_org)),
                             json.dumps(projection.project_snapshot(output)))
        self.assertIsNone(capture._mod_list)
        with self.assertRaises(projection.SnapshotCancelled):
            capture.step(max_rows=1)

    def test_epoch_a_b_a_between_slices_does_not_admit_a_loser(self):
        state = {"epoch": 0, "profile": "A"}
        first = _organizer({"old1": _mod("Old one"), "old2": _mod("Old two")}, profile="A")
        cap_a = projection.begin_snapshot_capture(first, is_current=lambda: state["epoch"] == 0)
        self.assertIsNone(cap_a.step(max_rows=1))
        state.update(epoch=1, profile="B")
        state.update(epoch=2, profile="A")
        winner = _organizer({"new": _mod("Current A")}, profile=state["profile"])
        cap_winner = projection.begin_snapshot_capture(winner, is_current=lambda: state["epoch"] == 2)
        with self.assertRaises(projection.SnapshotCancelled):
            cap_a.step(max_rows=1)
        self.assertIsNone(cap_a._mod_list)
        self.assertEqual(["Current A"], [row.label for row in cap_winner.step(max_rows=1).mods])

    def test_epoch_a_b_a_inside_final_native_row_and_enumeration_are_fenced(self):
        state = {"epoch": 0, "profile": "A"}
        row = _mod("Last old A")
        organizer = _organizer({"last": row}, profile="A")
        capture = projection.begin_snapshot_capture(organizer, is_current=lambda: state["epoch"] == 0)

        def change_profile():
            state.update(epoch=1, profile="B")
            state.update(epoch=2, profile="A")
            return "loser cannot be published"

        row._methods["repository"] = change_profile
        with self.assertRaises(projection.SnapshotCancelled):
            capture.step(max_rows=1)
        self.assertIsNone(capture._mod_list)
        self.assertEqual("A", state["profile"])

        ml = organizer.modList()

        def enumerate_changed(_):
            change_profile()
            return ["last"]

        organizer._methods["modList"] = lambda: ml
        ml._methods["allModsByProfilePriority"] = enumerate_changed
        state["epoch"] = 0
        with self.assertRaises(projection.SnapshotCancelled):
            projection.begin_snapshot_capture(organizer, is_current=lambda: state["epoch"] == 0)

    def test_soft_time_budget_between_native_rows_and_owner_only_handle(self):
        organizer = _organizer({str(i): _mod(str(i)) for i in range(3)})
        capture = projection.begin_snapshot_capture(organizer, is_current=lambda: True)
        with ThreadPoolExecutor(max_workers=1) as worker:
            with self.assertRaisesRegex(RuntimeError, "owner thread"):
                worker.submit(capture.step, max_rows=1).result()
            with self.assertRaisesRegex(RuntimeError, "owner thread"):
                worker.submit(capture.close).result()
        # One native row may itself overrun. The next row is not started.
        with patch.object(projection, "monotonic", side_effect=[1.0, 1.010, 2.0, 2.100, 3.0, 3.008]):
            self.assertIsNone(capture.step(max_rows=64, max_seconds=0.006))
            self.assertIsNone(capture.step(max_rows=64, max_seconds=0.006))
            output = capture.step(max_rows=64, max_seconds=0.006)
        self.assertEqual(["0", "1", "2"], [row.name for row in output.mods])
        _same_primitive_tree(output, self)

    def test_owner_can_close_during_a_native_call_and_no_next_row_starts(self):
        mod, next_mod = _mod("First"), _mod("Next")
        organizer = _organizer({"first": mod, "next": next_mod})
        capture = projection.begin_snapshot_capture(organizer, is_current=lambda: True)
        calls = []
        mod._methods["repository"] = capture.close
        next_mod._methods["name"] = lambda: calls.append("should not look up next item")
        with self.assertRaises(projection.SnapshotCancelled):
            capture.step(max_rows=64)
        self.assertEqual([], calls)
        self.assertIsNone(capture._mod_list)


if __name__ == "__main__":
    unittest.main()
