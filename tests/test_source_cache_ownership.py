"""An old external-source scan cannot overwrite a newer cache observation."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from mo2_update_arsenal.source_registry import SourceScanner


PROFILE = {"profileId": "source-profile", "game": "skyrimspecialedition", "items": [
    {"id": "external", "installedVersion": "1.0", "nexusId": 0,
     "source": {"url": "https://github.com/example/mod", "verified": True, "kind": "project"}}]}


class Runner:
    available = True

    def __init__(self):
        self.calls = 0

    def run(self, request, **kwargs):
        self.calls += 1
        return {"itemRecords": {}, "cache": {"entries": [{"observation": self.calls}]}, "errors": [], "stats": {}}


class SourceCacheOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "sources.json"
        self.runner = Runner()
        self.scanner = SourceScanner(runner=self.runner, cache_path=self.path)

    def test_full_evidence_shared_and_retried_after_native_response_compaction(self):
        from mo2_update_arsenal.source_adapters import compact_source_update, normalize_adapter_record
        import time
        row = PROFILE["items"][0]
        urls = [row["source"]["url"], "https://github.com/other/release"]
        items = [dict(row,id=str(i),source=dict(row["source"],url=url)) for i,url in enumerate((urls[0],urls[0],urls[1],urls[1]))]
        items[1]["installedVersion"] = "3.0"
        stamp = time.time()
        base = dict(provider="github",outcome="release",freshness="fresh",checkedAt=stamp,expiresAt=stamp+900,latestVersion="2.0",
                    dependencies=[{"projectId":str(n),"kind":"required"} for n in range(401)])
        partial = dict(outcome="release",provider="github",detailsTruncated=True)
        def run(request, **kwargs):
            ids = [i["id"] for i in request["items"]]
            self.runner.calls += 1
            if self.runner.calls == 1:
                self.assertEqual(ids,["0","2"])
                records = {i["id"]:dict(base if i["id"]=="0" else partial,sourceUrl=i["source"]["url"]) for i in request["items"]}
            else:
                self.assertEqual(ids,["2"])
                self.assertFalse(request["force"])
                records = {"2":dict(base,sourceUrl=urls[1])}
            return {"itemRecords":records,"cache":{"entries":[]},"stats":{"queried":1},"errors":[]}
        self.runner.run = run
        result = self.scanner.scan(dict(PROFILE,items=items))
        self.assertEqual(self.runner.calls,2)
        self.assertEqual(result["queried"],2)
        self.assertEqual(result["adapterStats"]["items"],4)
        self.assertEqual(result["adapterStats"]["providerLookupRows"],2)
        self.assertEqual(result["itemRecords"]["0"]["verdict"],"candidate")
        self.assertEqual(result["itemRecords"]["1"]["verdict"],"newer-installed")
        self.assertIsNot(result["itemRecords"]["2"]["dependencies"],result["itemRecords"]["3"]["dependencies"])
        for record in result["itemRecords"].values():
            self.assertEqual(compact_source_update(record)["dependencyCount"],401)
            self.assertEqual(record["dependencies"][-1]["projectId"],"400")
        unavailable = normalize_adapter_record(row,dict(partial,sourceUrl=urls[0]))
        self.assertEqual(unavailable["verdict"],"review")
        self.assertNotIn("dependencyCount",compact_source_update(unavailable))

    def delayed_write(self, cancel_old):
        paused, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        cancel = threading.Event()
        results, failures = [], []
        original = self.scanner._save_cache
        def delayed(cache, **kwargs):
            if threading.current_thread().name == "old-source-scan":
                paused.set()
                if not release.wait(4):
                    raise RuntimeError("Old scan not released")
            return original(cache, **kwargs)
        def old_scan():
            try:
                results.append(self.scanner.scan(PROFILE, cancel_event=cancel))
            except Exception as exc:
                failures.append(exc)
        with patch.object(self.scanner, "_save_cache", side_effect=delayed):
            worker = threading.Thread(target=old_scan, name="old-source-scan")
            worker.start()
            self.assertTrue(paused.wait(3))
            if cancel_old:
                cancel.set()
            new = self.scanner.scan(PROFILE)
            self.assertFalse(new.get("cancelled", False))
            self.assertEqual(self.scanner._cache["entries"], [{"observation": 2}])
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertFalse(failures)
        self.assertTrue(results[0]["cancelled"])
        self.assertEqual(self.scanner._cache["entries"], [{"observation": 2}])
        self.assertEqual(json.loads(self.path.read_text())["entries"], [{"observation": 2}])

    def test_cancelled_writer_rechecks_inside_cache_write(self):
        self.delayed_write(cancel_old=True)

    def test_new_scan_lease_rejects_older_writer_even_without_explicit_cancel(self):
        self.delayed_write(cancel_old=False)

    def test_pre_cancelled_scan_neither_dispatches_nor_invalidates_existing_lease(self):
        self.scanner.scan(PROFILE)
        epoch = self.scanner._scan_epoch
        cancel = threading.Event()
        cancel.set()
        result = self.scanner.scan(PROFILE, cancel_event=cancel)
        self.assertTrue(result["cancelled"])
        self.assertEqual(self.runner.calls, 1)
        self.assertEqual(self.scanner._scan_epoch, epoch)
        self.assertEqual(self.scanner._cache["entries"], [{"observation": 1}])

    def test_cache_cleanup_failure_preserves_completed_scan_and_last_good_disk(self):
        self.scanner.scan(PROFILE)
        original = self.path.read_bytes()
        with patch("mo2_update_arsenal.source_registry.os.replace", side_effect=OSError("fixture unavailable")), \
             patch.object(Path, "unlink", side_effect=OSError("fixture cleanup unavailable")), \
             self.assertLogs("mo2_update_arsenal.source_registry", level="WARNING") as messages:
            result = self.scanner.scan(PROFILE)
        self.assertIn("OSError", messages.output[0])
        self.assertNotIn("fixture cleanup unavailable", messages.output[0])
        self.assertNotIn("cancelled", result)
        self.assertIn("external", result["itemRecords"])
        self.assertEqual(self.scanner._cache["entries"], [{"observation": 2}])
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(SourceScanner(runner=Runner(), cache_path=self.path)._cache["entries"],
                         [{"observation": 1}])
        recovered = self.scanner.scan(PROFILE)
        self.assertIn("external", recovered["itemRecords"])
        self.assertEqual(json.loads(self.path.read_text())["entries"], [{"observation": 3}])

    def test_invalid_cache_does_not_discard_current_exact_row_results(self):
        self.runner.run = lambda request, **kwargs: {"itemRecords": {}, "cache": None, "errors": [], "stats": {}}
        result = self.scanner.scan(PROFILE)
        self.assertNotIn("cancelled", result)
        self.assertIn("external", result["itemRecords"])
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
