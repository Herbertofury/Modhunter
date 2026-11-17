#!/usr/bin/env python3
"""Reproducible complete-catalog Nexus check fixture; no live account or network.

The pinned baseline's ordinary manual check requested project metadata only.
The complete-evidence control additionally runs that same revision's actual
file-list adapter for every project. Both costs stay inside the timed region.
This distinguishes added correctness work on cold checks from the measured
repeat-check cache benefit. It does not claim native MO2 or network timings.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import platform
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE = "3231bb689bebb050e597044c4f001e006f7ab82a"
GAME = "skyrimspecialedition"
SOURCES = ["scanner.py", "nexus_files.py", "bridge.py", "file_advisor.py", "quick_update.py", "update_plan.py"]
SOURCE_HASHES = {name: hashlib.sha256((ROOT / "mo2_update_arsenal" / name).read_bytes()).hexdigest() for name in SOURCES}
SCRIPT_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
sys.path.insert(0, str(ROOT))

from mo2_update_arsenal.scanner import Cache, NexusClient, UpdateScanner, MAX_NEXUS_WORKERS


def baseline_module(name: str):
    filename = f"mo2_update_arsenal/{name}.py"
    result = subprocess.run(["git", "show", f"{BASELINE}:{filename}"], cwd=ROOT,
                            check=True, capture_output=True, text=True)
    module = types.ModuleType(f"mo2_update_arsenal._benchmark_baseline_{name}")
    module.__package__ = "mo2_update_arsenal"
    sys.modules[module.__name__] = module
    exec(compile(result.stdout, f"{BASELINE}/{filename}", "exec"), module.__dict__)
    return module, hashlib.sha256(result.stdout.encode()).hexdigest()


class Provider:
    def __init__(self, delay: float):
        self.delay, self.lock = delay, threading.Lock()
        self.calls, self.active, self.peak = [], 0, 0

    @staticmethod
    def metadata(mid: int):
        return {"mod_id": mid, "domain_name": GAME, "version": "1.5.1", "status": "published",
                "picture_url": f"https://staticdelivery.nexusmods.com/mods/1704/images/{mid}-1.png",
                "author": f"Fixture creator {mid}", "summary": f"Full project summary {mid}"}

    @staticmethod
    def files(mid: int):
        return {"mod_id": mid, "domain_name": GAME, "files": [
            {"file_id": 10000 + mid, "name": "Installed main", "file_name": f"Main-old-{mid}.7z",
             "version": "1.0", "category_id": 4},
            {"file_id": 20000 + mid, "name": "Current main", "file_name": f"Main-new-{mid}.7z",
             "version": "1.5.1", "category_id": 1},
            {"file_id": 30000 + mid, "name": "Fishing optional patch", "file_name": f"Fishing-{mid}.7z",
             "version": "1.0.1", "category_id": 3}],
            "file_updates": [{"old_file_id": 10000 + mid, "new_file_id": 20000 + mid}]}

    def get(self, path: str):
        with self.lock:
            self.calls.append(path)
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            time.sleep(self.delay)
            mid = int(path.split("/mods/")[1].split("/")[0].split(".")[0])
            # Raw files.json deliberately carries no embedded project metadata:
            # the author/artwork request must really occur and be counted.
            return self.files(mid) if path.endswith("/files.json") else self.metadata(mid)
        finally:
            with self.lock:
                self.active -= 1

    def reset(self):
        assert self.active == 0
        self.calls, self.peak = [], 0


def catalog(projects: int, copies: int):
    items = []
    for mid in range(1, projects + 1):
        for number in range(copies):
            optional = number % 2 == 1
            items.append({"id": f"{mid}-{number}", "nexusId": mid,
                          "installedVersion": "1.0.1" if optional else "1.0",
                          "installedFiles": [{"modId": mid, "fileId": (30000 if optional else 10000) + mid}],
                          "source": {"type": "nexus", "url": f"https://www.nexusmods.com/{GAME}/mods/{mid}"}})
    return {"profileId": "benchmark", "game": GAME, "items": items}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projects", type=int, default=600)
    parser.add_argument("--rows-per-project", type=int, default=8)
    parser.add_argument("--latency-ms", type=float, default=10)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, default=ROOT / "native/evidence/nexus-file-checks-0.13.0.json")
    args = parser.parse_args()
    if args.projects < 1 or args.rows_per_project < 2 or args.repeats < 1 or not 0 <= args.latency_ms < 60000:
        parser.error("Use positive project/repeat counts, at least two rows per project and finite latency below60seconds")
    old_scanner, scanner_hash = baseline_module("scanner")
    old_bridge, bridge_hash = baseline_module("bridge")
    snapshot = catalog(args.projects, args.rows_per_project)
    expected_ids = {row["id"] for row in snapshot["items"]}
    runs = []
    for repeat in range(args.repeats):
        with tempfile.TemporaryDirectory(prefix="modhunter-nexus-benchmark-") as directory:
            old_provider, new_provider = Provider(args.latency_ms / 1000), Provider(args.latency_ms / 1000)
            old_cache, new_cache = old_scanner.Cache(Path(directory) / "old.json"), Cache(Path(directory) / "new.json")
            old = old_scanner.UpdateScanner(old_cache, old_scanner.NexusClient("fixture", old_provider.get))
            new = UpdateScanner(new_cache, NexusClient("fixture", new_provider.get))
            old_bridge.NexusClient = lambda key: old_scanner.NexusClient(key, old_provider.get)
            cases = [("beforeProjectOnlyCold", old, old_cache, "deep", old_provider, False),
                     ("beforeProjectOnlyRepeat", old, old_cache, "deep", old_provider, False),
                     ("beforeStartupWarm", old, old_cache, "startup", old_provider, False),
                     ("beforeCompleteEvidence", old, old_cache, "deep", old_provider, True),
                     ("afterCold", new, new_cache, "startup", new_provider, False),
                     ("afterRepeat", new, new_cache, "startup", new_provider, False),
                     ("afterForcedAudit", new, new_cache, "deep", new_provider, False)]
            for label, scanner, cache, mode, provider, complete_control in cases:
                provider.reset()
                started, cpu_started = time.perf_counter(), time.process_time()
                result = scanner.scan(snapshot, mode)
                control_files = {}
                if complete_control:
                    def read_files(mid):
                        return mid, old_bridge.ArsenalService._fetch_files_remote(object(), mid, GAME, "fixture", False)
                    with ThreadPoolExecutor(max_workers=MAX_NEXUS_WORKERS) as pool:
                        control_files = dict(pool.map(read_files, range(1, args.projects + 1)))
                elapsed, cpu = time.perf_counter() - started, time.process_time() - cpu_started
                assert set(result["itemRecords"]) == expected_ids and not result["errors"], (label, result["errors"])
                counts = {}
                for item in snapshot["items"]:
                    row = result["itemRecords"][item["id"]]
                    counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
                    meta = Provider.metadata(item["nexusId"])
                    assert row.get("thumbnailUrl") == meta["picture_url"], (label, "artwork lost")
                    assert row.get("author", {}).get("name") == meta["author"], (label, "author lost")
                    if scanner is new:
                        optional = item["installedVersion"] == "1.0.1"
                        assert row["verdict"] == ("current" if optional else "candidate")
                        assert row["fileEvidence"]["targetFileId"] == (30000 if optional else 20000) + item["nexusId"]
                complete_file_rows = 0
                for mid in range(1, args.projects + 1):
                    cached = cache.get(GAME, mid)
                    assert cached["meta"]["summary"] == Provider.metadata(mid)["summary"]
                    files = (cached["files"]["files"] if scanner is new else control_files.get(mid, {}).get("files"))
                    if files is not None:
                        expected = Provider.files(mid)["files"]
                        assert {file["fileId"] for file in files} == {file["file_id"] for file in expected}
                        assert {file["fileName"] for file in files} == {file["file_name"] for file in expected}
                        complete_file_rows += len(files)
                assert provider.peak <= MAX_NEXUS_WORKERS
                metadata_calls = sum(not path.endswith("/files.json") for path in provider.calls)
                file_calls = len(provider.calls) - metadata_calls
                expected_calls = (0 if label in {"beforeStartupWarm", "afterRepeat"} else
                                  2 * args.projects if complete_control or scanner is new else args.projects)
                assert len(provider.calls) == expected_calls, (label, len(provider.calls), expected_calls)
                runs.append({"repeat": repeat + 1, "case": label, "seconds": round(elapsed, 6),
                             "cpuSeconds": round(cpu, 6), "providerRequests": len(provider.calls),
                             "metadataRequests": metadata_calls, "fileRequests": file_calls,
                             "peakProviderRequests": provider.peak, "installedRows": len(expected_ids),
                             "completeFileRows": complete_file_rows, "artworkRows": len(expected_ids),
                             "authorRows": len(expected_ids), "verdicts": counts})
    summary = {}
    for name in dict.fromkeys(row["case"] for row in runs):
        selected = [row for row in runs if row["case"] == name]
        summary[name] = {"medianSeconds": round(statistics.median(row["seconds"] for row in selected), 6),
                         "medianCpuSeconds": round(statistics.median(row["cpuSeconds"] for row in selected), 6),
                         "providerRequests": [row["providerRequests"] for row in selected],
                         "metadataRequests": selected[0]["metadataRequests"], "fileRequests": selected[0]["fileRequests"],
                         "verdicts": selected[0]["verdicts"]}
    current_hashes = {name: hashlib.sha256((ROOT / "mo2_update_arsenal" / name).read_bytes()).hexdigest() for name in SOURCES}
    assert current_hashes == SOURCE_HASHES, "Production source changed while benchmarking; rerun the exact final sources"
    assert SCRIPT_HASH == hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "Benchmark changed during its run"
    receipt = {"schema": 1, "baselineRef": BASELINE,
               "scope": "Controlled Python provider fixture; no live network, account, GUI or native MO2 timing.",
               "comparisons": {
                   "standardCheck": "Actual old manual all/deep metadata-only check versus current exact-file plus metadata check. Cold adds required file/history requests; warm repeated standard checks reuse both for up to4hours.",
                   "completeEvidence": "Pinned old metadata scan plus its actual file-list adapter for every project, versus new forced full audit. Both timed workloads fetch all project metadata and full Files responses. Only the new path evaluates exact installed-file replacement history.",
                   "startup": "Old automatic startup already reused fresh project metadata. Its separate warm control prevents attributing that existing benefit to this change.",
                   "freshness": "Explicit Full audit refreshes both endpoints. Failed/incomplete evidence cannot renew current status. Native MO2 metadata bootstrap reuse is not simulated; all raw API calls are counted."},
               "projects": args.projects, "installedRows": len(expected_ids),
               "rowsPerProject": args.rows_per_project, "perRequestSimulatedLatencySeconds": args.latency_ms / 1000,
               "workers": MAX_NEXUS_WORKERS, "repeats": args.repeats,
               "environment": {"python": platform.python_version(), "platform": platform.system(), "machine": platform.machine()},
               "fixtureSha256": hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest(),
               "baselineSourceHashes": {"scanner.py": scanner_hash, "bridge.py": bridge_hash},
               "sourceHashes": SOURCE_HASHES,
               "benchmarkSha256": SCRIPT_HASH,
               "summary": summary, "runs": runs,
               "assertions": {"completeInstalledInventory": True, "projectMetadataPreserved": True,
                              "artworkAndAuthorsPreserved": True, "completeFileListsPreserved": True,
                              "exactOptionalAndMainVerdicts": True, "allProviderCallsCounted": True,
                              "fiveRequestConcurrencyBound": True}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"receipt": str(args.output), "summary": summary}, indent=2))


if __name__ == "__main__":
    main()
