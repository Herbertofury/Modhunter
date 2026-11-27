"""Read-only acceptance for ModHunter's exact Nexus ticket and install briefing."""
import copy
import json
import unittest

from mo2_update_arsenal.install_brief import build_install_brief
from mo2_update_arsenal.update_plan import PlanError, validate_batch_selection, validate_release_asset

SNAPSHOT = {"profileId": "se:fixture", "profileName": "Fixture",
            "game": "skyrimspecialedition", "gameRuntime": "1.5.97",
            "items": [{"id": "fixture-mod", "name": "Fixture mod", "nexusId": 45,
                       "installedVersion": "1.2", "flags": [],
                       "update": {"verdict": "candidate", "latestVersion": "2.4",
                                  "source": "nexus-api"}}]}


def ticket_file(file_id):
    """Exercise the two pure production call sites, not a packaged plugin."""
    selected, = validate_batch_selection(SNAPSHOT, {
        "profileId": "se:fixture", "confirm": "QUEUE_REVIEWED_DOWNLOADS",
        "entries": [{"itemId": "fixture-mod", "modId": 45, "fileId": file_id,
                     "installedVersion": "1.2", "targetVersion": "2.4", "reviewed": True}],
    })
    ticket = {**selected, "profileId": "se:fixture", "profileName": "Fixture",
              "game": "skyrimspecialedition", "gameRuntime": "1.5.97",
              "provider": "nexus", "status": "complete", "fileName": "same-name.7z",
              "fileVersion": "2.4", "targetVersion": "2.4", "reviewed": True}
    return selected, ticket


class ExactFileHandoffTests(unittest.TestCase):
    def test_equal_names_and_versions_keep_the_explicit_file(self):
        before = json.dumps(SNAPSHOT, sort_keys=True)
        for file_id in (48, 49):
            selection, ticket = ticket_file(file_id)
            saved = copy.deepcopy(ticket)
            preview = build_install_brief(SNAPSHOT, ticket, 711)
            self.assertEqual(selection["fileId"], file_id)
            self.assertEqual(preview["fileId"], file_id)
            self.assertEqual(preview["archiveName"], "same-name.7z")
            self.assertEqual(preview["fileVersion"], "2.4")
            self.assertTrue(preview["canInstall"])
            self.assertNotIn("deployed", preview)
            self.assertNotIn("options", preview)
            self.assertEqual(ticket, saved)
        self.assertEqual(json.dumps(SNAPSHOT, sort_keys=True), before)

    def test_same_exact_file_is_refused_after_profile_change(self):
        _, ticket = ticket_file(48)
        new_profile = {**SNAPSHOT, "profileId": "se:other"}
        preview = build_install_brief(new_profile, ticket, 711)
        self.assertFalse(preview["canInstall"])
        self.assertIn("The active MO2 profile no longer matches this download", preview["errors"])

    def test_duplicate_target_row_cannot_choose_either_record(self):
        selection, ticket = ticket_file(48)
        sibling = {**SNAPSHOT["items"][0], "name": "Another row with same ID"}
        for rows in ([SNAPSHOT["items"][0], sibling], [sibling, SNAPSHOT["items"][0]]):
            ambiguous = {**SNAPSHOT, "items": rows}
            with self.assertRaisesRegex(PlanError, "Multiple MO2 rows"):
                validate_batch_selection(ambiguous, {"profileId": "se:fixture",
                    "confirm": "QUEUE_REVIEWED_DOWNLOADS", "entries": [
                    {**selection, "reviewed": True}]})
            preview = build_install_brief(ambiguous, ticket, 711)
            self.assertFalse(preview["canInstall"])
            self.assertTrue(any("Multiple MO2 rows" in e for e in preview["errors"]))
            with self.assertRaisesRegex(PlanError, "Multiple MO2 rows"):
                validate_release_asset(ambiguous, {"kind": "source",
                    "itemId": "fixture-mod", "reviewed": True})

    def test_another_rows_duplicate_does_not_block_the_exact_target(self):
        selection, ticket = ticket_file(48)
        other = {**SNAPSHOT["items"][0], "id": "other"}
        large_scope = {**SNAPSHOT, "items": [other, *SNAPSHOT["items"], other]}
        selected = validate_batch_selection(large_scope, {"profileId": "se:fixture",
            "confirm": "QUEUE_REVIEWED_DOWNLOADS", "entries": [
            {**selection, "reviewed": True}]})
        self.assertEqual(selected, [selection])
        self.assertTrue(build_install_brief(large_scope, ticket, 711)["canInstall"])
