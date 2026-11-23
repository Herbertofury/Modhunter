"""Exercise ordered observation batching through the real Chronicle disk path."""
from copy import deepcopy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mo2_update_arsenal.gameplay import Chronicle

STAMP = "2026-11-23T05:00:00+00:00"
GAMEPLAY = "mo2_update_arsenal.gameplay"


class ChronicleObservationBatchTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        clock = patch(f"{GAMEPLAY}.utcnow", return_value=STAMP)
        clock.start()
        self.addCleanup(clock.stop)
        self.baseline = Chronicle(Path(tmp.name) / "sequential.json")
        self.batch = Chronicle(Path(tmp.name) / "batch.json")
        for ledger in (self.baseline, self.batch):
            ledger.set_profile_name("a", "Main")
            ledger.set_profile_name("b", "Other")
            for i in range(8):
                ledger.record_update("a", {"id": f"live-seed{i}"}, "1", "2", "mo2-install-complete")
            ledger.record_update("b", {"id": "live-other"}, "1", "2", "mo2-install-complete")
            ledger.set_progress("a", {"live-0": {"note": "keep", "favorite": True}}, {"theme": "dark"},
                                "1.5.97", {"live-0"})
            profile = ledger.data["profiles"]["a"]
            profile["bossBounties"] = [{"id": "bounty", "modId": "live-0", "name": "Boss",
                                         "before": "1", "status": "defeated"}]
            profile["victories"] = [{"id": "a:3", "ack": True}]
            ledger._write()
        self.before = {"profileId": "a", "items": [
            {"id": f"live-{i}", "name": f"Mod {i}", "installedVersion": "1"} for i in range(20)]}
        self.after = deepcopy(self.before)
        for row in self.after["items"]:
            row["installedVersion"] = "2"

    def sequential_awards(self):
        # The pre-batch observation persisted each successful record_update.
        return [self.baseline.record_update("a", row, "1", "2", "version-observed")
                for row in self.after["items"]]

    def assert_parity(self, expected, actual):
        self.assertEqual(expected, actual)
        self.assertEqual(self.baseline.data, self.batch.data)
        self.assertEqual(self.baseline.path.read_bytes(), self.batch.path.read_bytes())
        self.assertEqual(self.baseline.data, Chronicle(self.batch.path).data)

    def test_twenty_awards_one_write_exact_sequential_state_and_receipts(self):
        other = self.batch.data["profiles"]["b"]
        saved_other = deepcopy(other)
        with (patch.object(self.baseline, "_write", wraps=self.baseline._write) as old,
              patch.object(self.batch, "_write", wraps=self.batch._write) as new):
            expected = self.sequential_awards()
            actual = self.batch.observe(self.before, self.after)
        self.assertEqual((old.call_count, new.call_count), (20, 1))
        self.assert_parity(expected, actual)
        self.assertEqual(self.batch.data["profiles"]["b"], saved_other)
        self.assertIs(self.batch.data["profiles"]["b"], other)
        self.assertEqual(len(actual), 20)
        self.assertEqual(actual[0]["event"]["bossBounty"]["id"], "bounty")
        self.assertEqual(actual[-1]["event"]["combo"], 20)
        self.assertIn("archmage", self.batch.data["profiles"]["a"]["achievements"])

    def test_unchanged_rejected_cross_profile_and_duplicate_observations_do_not_write(self):
        self.batch.observe(self.before, self.after)
        state = deepcopy(self.batch.data)
        disk = self.batch.path.read_bytes()
        downgrade = deepcopy(self.after)
        for row in downgrade["items"]:
            row["installedVersion"] = "0"
        with patch.object(self.batch, "_write", wraps=self.batch._write) as write:
            self.assertEqual(self.batch.observe(self.before, self.before), [])
            self.assertEqual(self.batch.observe(self.before, downgrade), [])
            self.assertEqual(self.batch.observe(self.before, {**self.after, "profileId": "b"}), [])
            self.assertEqual(self.batch.observe(self.before, self.after), [])
        write.assert_not_called()
        self.assertEqual(self.batch.data, state)
        self.assertEqual(self.batch.path.read_bytes(), disk)

    def assert_failed_write_can_retry(self, target):
        state = deepcopy(self.batch.data)
        disk = self.batch.path.read_bytes()
        other = self.batch.data["profiles"]["b"]
        with patch(target, side_effect=OSError("injected")):
            with self.assertRaisesRegex(OSError, "injected"):
                self.batch.observe(self.before, self.after)
        self.assertEqual(self.batch.data, state)
        self.assertEqual(self.batch.path.read_bytes(), disk)
        self.assertIs(self.batch.data["profiles"]["b"], other)
        self.assert_parity(self.sequential_awards(), self.batch.observe(self.before, self.after))
        self.assertEqual(self.batch.observe(self.before, self.after), [])

    def test_pending_file_write_failure_rolls_back_then_retries(self):
        self.assert_failed_write_can_retry("pathlib.Path.write_text")

    def test_atomic_replace_failure_rolls_back_then_retries(self):
        self.assert_failed_write_can_retry(f"{GAMEPLAY}.os.replace")

    def test_failed_new_profile_observation_does_not_create_phantom_progress(self):
        del self.batch.data["profiles"]["a"]
        self.batch._write()
        state = deepcopy(self.batch.data)
        disk = self.batch.path.read_bytes()
        with patch(f"{GAMEPLAY}.os.replace", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                self.batch.observe(self.before, self.after)
        self.assertEqual(self.batch.data, state)
        self.assertEqual(self.batch.path.read_bytes(), disk)
        self.assertEqual(len(self.batch.observe(self.before, self.after)), 20)

    def test_direct_install_still_persists_and_deduplicates_later_observation(self):
        with patch.object(self.batch, "_write", wraps=self.batch._write) as write:
            result = self.batch.record_update("a", self.after["items"][0], "1", "2",
                                              "mo2-install-complete")
        self.assertTrue(result["ok"])
        write.assert_called_once()
        self.assertEqual(Chronicle(self.batch.path).data, self.batch.data)
        awards = self.batch.observe(self.before, self.after)
        self.assertEqual(len(awards), 19)
        self.assertNotIn("live-0", [award["event"]["modId"] for award in awards])
