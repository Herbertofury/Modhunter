"""Protect the trace reducer's completeness, units and source identity boundaries."""
import unittest

import library_trace_diagnostic as trace


class TraceReductionTests(unittest.TestCase):
    def test_selectors_keep_stylesheets_distinct_and_sum_raw_microseconds(self):
        def event(sheet, elapsed):
            return {"name": "SelectorStats", "args": {"selector_stats": {"selector_timings": [{
                "style_sheet_id": sheet, "selector": ".mod-row", "elapsed (us)": elapsed,
                "match_attempts": 11207, "match_count": 11207, "fast_reject_count": 0,
                "invalidation_count": 1}]}}}
        events = [event("one", 3), event("two", 20), event("one", 5)]
        result = trace.summarize_trace({"traceEvents": events})
        self.assertEqual(result["selectorStatsEvents"], events)
        self.assertEqual([(row["styleSheetId"], row["elapsedUs"]) for row in result["selectors"]], [("two", 20), ("one", 8)])
        self.assertEqual(result["selectors"][1]["matchAttempts"], 22414)

    def test_layout_records_and_distinct_invalidation_causes_are_not_truncated(self):
        layout = {"name": "Layout", "dur": 800, "args": {"beginData": {"dirtyObjects": 11207}}}
        recalc = {"name": "UpdateLayoutTree", "dur": 600, "args": {"elementCount": 212940}}
        events = [layout, recalc]
        for attribute in ("data-workspace", "data-theme", "data-workspace"):
            events.append({"name": "ScheduleStyleInvalidationTracking", "args": {"data": {
                "nodeName": "BODY", "changedAttribute": attribute, "stackTrace": [{"functionName": "selectWorkspace"}]}}})
        result = trace.summarize_trace({"traceEvents": events})
        self.assertEqual(result["eventCount"], 5)
        self.assertEqual(result["layouts"], [layout])
        self.assertEqual(result["recalculations"], [recalc])
        self.assertEqual([(row["changedAttribute"], row["count"]) for row in result["invalidations"]], [("data-workspace", 2), ("data-theme", 1)])
        self.assertTrue(all(row["stackTrace"] for row in result["invalidations"]))


if __name__ == "__main__":
    unittest.main()
