"""Exact callback routing and capture cancellation with explicit event delivery."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from mo2_update_arsenal.refresh_work import RefreshWork
from test_plugin_autostart import plugin_type


class ModStateEventBatchTests(unittest.TestCase):
    def setUp(self):
        self.timers = []
        self.plugin = plugin_type(self.timers)()

    def test_bulk_and_singleton_invalidations_remain_separate_but_timer_coalesces(self):
        class LegacyMapping(dict):
            def __iter__(self):
                return iter(("adapter-key",))

        self.plugin.server = SimpleNamespace(content=Mock())
        big = {f"Fixture {i}": object() for i in range(11_207)}
        sample = (
            big, {"A": 2}, {42: 3}, {}, LegacyMapping(big), [("not a dict", 4)],
        )
        for values in sample:
            self.plugin._on_mod_states_changed(values)
        self.assertEqual(self.plugin.server.content.invalidate.call_args_list, [
            call(reason="external", mod_name=""),
            call(reason="target", mod_name="A"),
            call(reason="target", mod_name="42"),
            call(reason="external", mod_name=""),
            call(reason="target", mod_name="adapter-key"),
            call(reason="external", mod_name=""),
        ])
        self.assertEqual(len(big), 11_207)
        self.assertEqual([ms for ms, _ in self.timers], [150])

    def test_absent_service_does_not_read_input_or_schedule_before_ui_init(self):
        class UnreadableMapping(dict):
            def __iter__(self):
                raise AssertionError("Callback unexpectedly inspected a mapping")

        self.plugin._on_mod_states_changed(UnreadableMapping())
        self.assertEqual(self.timers, [])
        self.assertIsNone(self.plugin._pending_refresh)

    def test_live_bulk_event_cancels_active_capture_and_preserves_one_followup(self):
        plugin = self.plugin
        plugin.server = SimpleNamespace(content=Mock(), stop=Mock(), invalidate_focus_profile=Mock())
        plugin._publish_diagnostics = Mock()
        captured, projected, published, deliveries, slices = [], [], [], [], []
        context = ["Profile A"]

        def begin(valid):
            value = context[0]
            capture = SimpleNamespace(
                step=Mock(return_value=value), close=Mock(),
            )
            self.assertTrue(valid())
            captured.append(capture)
            return capture, 0

        def project(value, force, cancel):
            projected.append((value[0], force, cancel.is_set()))
            return value[0]

        def publish(value, valid):
            self.assertTrue(valid())
            published.append(value)
            return {"ok": True}

        run_inline = lambda target, **_: SimpleNamespace(start=target)
        plugin._background_refresh = bg = RefreshWork(
            context=lambda: tuple(context), capture=None, project=project,
            publish=publish, deliver=deliveries.append,
            begin_capture=begin, schedule_capture=slices.append,
        )
        with patch("mo2_update_arsenal.refresh_work.Thread", side_effect=run_inline):
            # Idle maintenance is event driven even with the live service present.
            self.assertEqual(self.timers, [])
            plugin._on_mod_states_changed({"Initial": 1})
            self.timers.pop(0)[1]()
            self.assertEqual(len(slices), 1)
            old = bg._active
            old_epoch = bg._epoch

            # Profile returns to the same name before any owner capture is delivered.
            plugin._on_mod_states_changed({f"Fixture {i}": 2 for i in range(11_207)})
            context[0] = "Profile B"
            plugin._on_profile_changed("Profile A", "Profile B")
            context[0] = "Profile A"
            plugin._on_profile_changed("Profile B", "Profile A")
            plugin._on_plugin_states_changed({"Plugin": 1})

            plugin.server.invalidate_focus_profile.assert_called()
            self.assertEqual(plugin.server.invalidate_focus_profile.call_count, 2)
            self.assertTrue(old.cancel.is_set())
            self.assertEqual(bg._epoch, old_epoch + 4)
            self.assertEqual([ms for ms, _ in self.timers], [150])
            self.timers.pop(0)[1]()
            slices.pop(0)()  # Old owner capture cannot inspect a row, launch or publish.
            captured[0].step.assert_not_called()
            captured[0].close.assert_called_once_with()
            self.assertEqual(published, [])
            self.assertEqual(projected, [])
            self.assertEqual(len(slices), 1)

            slices.pop(0)()
            bg.complete(deliveries.pop(0))
            self.assertEqual(projected, [("Profile A", False, False)])
            self.assertEqual(published, ["Profile A"])
            self.assertEqual(plugin.server.content.invalidate.call_args_list, [
                call(reason="target", mod_name="Initial"),
                call(reason="external", mod_name=""),
                call(reason="profile"),
                call(reason="profile"),
            ])
            self.assertEqual(self.timers, [])
            self.assertEqual(slices, [])
            self.assertFalse(bg.busy)

            plugin._on_mod_states_changed({"Later": 0})
            plugin._shutdown()
            self.timers.pop(0)[1]()
            self.assertIsNone(plugin.server)
            self.assertEqual(published, ["Profile A"])
            self.assertEqual(slices, [])
