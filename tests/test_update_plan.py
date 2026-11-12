"""Update Workshop authorization tests; no MO2 installation or Nexus connection needed."""
import copy
import unittest

from mo2_update_arsenal.update_plan import PlanError, plan_updates, validate_batch_selection, risk_reasons


SNAP = {
    "profileId": "profile-alpha", "profileName": "Skyrim SE", "game": "skyrimspecialedition",
    "gameRuntime": "1.5.97",
    "items": [
        {"id": "live-a", "name": "Anim Framework", "nexusId": 101, "installedVersion": "1.0",
         "update": {"verdict": "candidate", "latestVersion": "2.0"}, "flags": ["DLL"]},
        {"id": "live-b", "name": "Anim Framework - options", "nexusId": 101, "installedVersion": "1.1",
         "update": {"verdict": "candidate", "latestVersion": "2.0"}, "flags": []},
        {"id": "live-c", "name": "Manual Mod", "nexusId": 0, "installedVersion": "1.0"},
        {"id": "live-d", "name": "Finished", "nexusId": 44, "installedVersion": "3.0",
         "update": {"verdict": "current"}, "flags": []},
    ],
}


def choice(item="live-a", mod_id=101, file_id=11, version="1.0", risk=True):
    return {"itemId": item, "modId": mod_id, "fileId": file_id,
            "installedVersion": version, "reviewed": True, "riskAcknowledged": risk}


def packet(*entries, profile="profile-alpha", confirm="QUEUE_REVIEWED_DOWNLOADS"):
    return {"profileId": profile, "confirm": confirm, "entries": list(entries)}


class UpdateWorkshopTests(unittest.TestCase):
    def test_conversion_changes_and_active_master_issues_reach_update_plan(self):
        snapshot = copy.deepcopy(SNAP)
        row = snapshot["items"][1]
        row["localAnalysis"] = {"plugins": [{"name": "Example.esp", "winner": False,
            "provenance": {"state": "source-changed"}}, {"name": "Patch.esp", "winner": True,
            "active": True, "masterIssues": [{"name": "Missing.esm", "status": "missing"}]}]}
        plan = plan_updates(snapshot)
        item = plan["items"][1]
        self.assertTrue(any("ESLifier" in risk for risk in item["risks"]))
        self.assertTrue(any("masters" in risk for risk in item["risks"]))
        self.assertEqual(item["localAnalysis"], row["localAnalysis"])
        with self.assertRaises(PlanError):
            validate_batch_selection(snapshot, packet(choice(item="live-b", version="1.1", risk=False)))

    def test_bug_reports_and_light_headers_do_not_become_install_risks(self):
        row = {"flags": [], "localAnalysis": {"plugins": [{"name": "Example.esp", "winner": True,
            "active": True, "header": {"lightEffective": True}, "provenance": {"state": "current"},
            "eligibility": {"status": "already-light"}}]},
            "modHealth": {"bugs": {"state": "available", "displayedCount": 42}}}
        self.assertEqual(risk_reasons(row), [])

    def test_incomplete_record_scan_survives_compact_and_full_plans(self):
        for record in ({"records": {"scanComplete": False}}, {"recordScanComplete": False}):
            with self.subTest(record=record):
                self.assertTrue(any("incomplete" in risk for risk in risk_reasons({
                    "localAnalysis": {"plugins": [{"name": "Truncated.esp", **record}]}})))

    def test_plan_includes_all_rows_and_dedicated_lanes(self):
        original = copy.deepcopy(SNAP)
        plan = plan_updates(SNAP)
        self.assertEqual(plan["summary"], {
            "candidates": 2, "review": 0, "manual": 1, "current": 1, "risky": 1, "total": 4
        })
        self.assertEqual([r["id"] for r in plan["items"]], ["live-a", "live-b", "live-c", "live-d"])
        self.assertEqual(SNAP, original)

    def test_exact_reviewed_file_accepted(self):
        self.assertEqual(validate_batch_selection(SNAP, packet(choice())),
                         [{"itemId": "live-a", "modId": 101, "fileId": 11, "installedVersion": "1.0"}])

    def test_profile_change_and_no_confirmation_blocked(self):
        for payload in (packet(choice(), profile="other"),
                        packet(choice(), confirm="NONE"),
                        packet()):
            with self.subTest(payload=payload), self.assertRaises(PlanError):
                validate_batch_selection(SNAP, payload)

    def test_invalid_file_identity_and_changed_installed_version(self):
        for sel in (choice(mod_id=99), choice(file_id=0),
                    choice(version="0.9"), choice(item="live-unknown")):
            with self.subTest(sel=sel), self.assertRaises(PlanError):
                validate_batch_selection(SNAP, packet(sel))

    def test_current_and_manual_are_not_update_candidates(self):
        for sel in (choice(item="live-c", mod_id=0),
                    choice(item="live-d", mod_id=44)):
            with self.subTest(sel=sel), self.assertRaises(PlanError):
                validate_batch_selection(SNAP, packet(sel))

    def test_require_explicit_file_review_and_risk_acknowledgment(self):
        for override in ({"reviewed": False}, {"riskAcknowledged": False}):
            sel = {**choice(), **override}
            with self.subTest(override=override), self.assertRaises(PlanError):
                validate_batch_selection(SNAP, packet(sel))

    def test_no_double_download_of_same_nexus_file(self):
        result = validate_batch_selection(SNAP, packet(
            choice(), choice(item="live-b", file_id=11, version="1.1", risk=False)))
        self.assertEqual(len(result), 1)
        result = validate_batch_selection(SNAP, packet(
            choice(), choice(item="live-b", file_id=12, version="1.1", risk=False)))
        self.assertEqual(len(result), 2)

    def test_no_duplicate_row_and_no_boolean_ids(self):
        with self.assertRaises(PlanError):
            validate_batch_selection(SNAP, packet(choice(), choice(file_id=12)))
        with self.assertRaises(PlanError):
            validate_batch_selection(SNAP, packet(choice(mod_id=True)))


if __name__ == "__main__":
    unittest.main()
