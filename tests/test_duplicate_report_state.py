"""Read-only native duplicate review, using actual loopback and owned fixture receipts."""
import contextlib
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mo2_update_arsenal import duplicate_report
from mo2_update_arsenal.bridge import ArsenalService


PROFILE = {"profileId": "duplicates-fixture", "profileName": "Fixture", "items": []}


@contextlib.contextmanager
def fixture_service():
    with tempfile.TemporaryDirectory() as tmp:
        service = ArsenalService("<html/>", Path(tmp) / "cache.json",
                                 lambda command: (_ for _ in ()).throw(AssertionError(command)), PROFILE)
        try:
            service.duplicates.directory.mkdir()
            service.start()
            yield service
        finally:
            service.stop()


def job(service, kind, ident, *, report=None, created=1):
    service.content._jobs[ident] = {
        "jobId": ident, "action": "duplicate:" + kind, "state": "complete",
        "reportId": report or ident, "createdAt": created, "profileId": PROFILE["profileId"],
    }


def record(service, ident, data):
    path = service.duplicates.directory / (ident + ".json")
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def fetch(service, *, token=True):
    connection = HTTPConnection("127.0.0.1", service._server.server_port, timeout=5)
    try:
        connection.request("GET", "/api/duplicates",
                           headers={"X-Arsenal-Token": service.token} if token else {})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def bytes_for(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def expected(service, report, operation, *, report_id="scan", operation_id="scan"):
    return {
        "ok": True, "state": "complete", "jobId": operation_id,
        "helperAvailable": service.duplicates.runner.available,
        "report": report, "reportId": report_id,
        "operationResult": operation, "operationJobId": operation_id,
    }


class DuplicateReportStateTest(unittest.TestCase):
    def test_same_report_read_once_per_authorized_response_with_exact_redaction(self):
        with fixture_service() as service:
            private_root = str(service.duplicates.directory.parent / "private")
            report = {"complete": True, "groups": [{
                "mods": [{"name": "日本語 expansion", "root": private_root, "relative": "local"}],
                "paths": [{"path": r"C:\Private\m.dds"}, {"path": r"\\host\Private\b"},
                          {"path": "textures/keep.dds", "context": "preserve"}],
            }]}
            public = {"complete": True, "groups": [{
                "mods": [{"name": "日本語 expansion", "relative": "local"}],
                "paths": [{}, {}, {"path": "textures/keep.dds", "context": "preserve"}],
            }]}
            job(service, "scan", "scan")
            path = record(service, "scan", report)
            raw = path.read_bytes()
            with patch.object(duplicate_report, "read_json", wraps=duplicate_report.read_json) as read:
                self.assertEqual(fetch(service, token=False)[0], 403)
                read.assert_not_called()
                self.assertEqual(fetch(service), (200, bytes_for(expected(service, public, public))))
                self.assertEqual(read.call_count, 1, "One completed receipt was opened/decoded twice")
                self.assertEqual(path.read_bytes(), raw)
                report["complete"] = public["complete"] = False
                record(service, "scan", report)
                self.assertEqual(fetch(service), (200, bytes_for(expected(service, public, public))))
                self.assertEqual(read.call_count, 2, "No cross-request stale cache")

    def test_distinct_scan_and_operation_use_both_real_receipts(self):
        with fixture_service() as service:
            job(service, "scan", "scan")
            job(service, "previewRestore", "review", created=2)
            a, b = {"groups": [{"name": "Original"}]}, {"canRestore": True, "modsRoot": r"D:\Private"}
            first, second = record(service, "scan", a), record(service, "review", b)
            with patch.object(duplicate_report, "read_json", wraps=duplicate_report.read_json) as read:
                value = expected(service, a, {"canRestore": True}, operation_id="review")
                self.assertEqual(fetch(service), (200, bytes_for(value)))
                self.assertEqual([call.args[0] for call in read.call_args_list], [first, second])

    def test_failed_read_is_not_reused_and_preserves_existing_error_order(self):
        with fixture_service() as service:
            job(service, "scan", "scan")
            # An interrupted/damaged receipt cannot be promoted to a success.
            failure = ValueError("Fixture cannot read completed receipt")
            with patch.object(duplicate_report, "read_json", side_effect=failure) as read:
                status, body = fetch(service)
            value = json.loads(body)
            self.assertEqual(status, 200)
            self.assertFalse(value["ok"])
            self.assertNotIn("report", value)
            self.assertNotIn("operationResult", value)
            self.assertEqual(value["error"], "Saved duplicate operation could not be read: " + str(failure))
            self.assertEqual(read.call_count, 2)


if __name__ == "__main__":
    unittest.main()
