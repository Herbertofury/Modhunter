#!/usr/bin/env python3
"""Compare real loopback HTTP catalog delivery and progress saves with 0.9.1.

Both services run the same complete synthetic profile. The baseline loads its
entire Python package from Git, not a mixture of old/new dependencies. Timing
includes HTTP connection, Python preparation, JSON encoding, transfer/readback,
and (for progress) the Chronicle write. It excludes native MO2, browser parsing,
Nexus requests, archive transfers and installation. No user files are touched.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import http.client
import importlib
import io
import json
import math
import platform
import statistics
import subprocess
import sys
import tarfile
import tempfile
import time
import types
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mo2_update_arsenal.bridge import ArsenalService

BASELINE = "63e5df3e3ef218ae72ce58b5e4502165c40ca604"
STAMP = 1_794_500_000.0
SOURCE_FILES = ("bridge.py", "gameplay.py", "intelligence.py", "scanner.py", "source_registry.py")


def baseline_package(destination: Path, reference: str):
    content = subprocess.check_output(["git", "archive", reference, "mo2_update_arsenal"], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(content)) as archive:
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if not member.isfile() or path.suffix != ".py":
                continue
            if path.is_absolute() or ".." in path.parts or path.parts[0] != "mo2_update_arsenal":
                raise ValueError("Unexpected source path in Git archive")
            target = destination.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())
    package = types.ModuleType("modhunter_091_benchmark")
    package.__path__ = [str(destination / "mo2_update_arsenal")]
    sys.modules[package.__name__] = package
    return importlib.import_module(package.__name__ + ".bridge").ArsenalService


def catalog(count):
    return {"profileId": "http-benchmark", "profileName": "Synthetic fixture", "game": "skyrimspecialedition",
            "gameRuntime": "1.5.97", "groups": [], "items": [
                {"id": f"live-{i}", "name": f"Fixture {i}", "nexusId": i + 1,
                 "installedVersion": "1.0", "flags": [],
                 "source": {"type": "nexus", "verified": True, "kind": "project",
                            "url": f"https://www.nexusmods.com/skyrimspecialedition/mods/{i + 1}"},
                 "update": {"source": "nexus-api", "verdict": "candidate", "latestVersion": "2.0"}}
                for i in range(count)]}


def analysis(count):
    # Each synthetic mod has one plugin and 32 bounded record-type counters.
    # Full details remain available to the inspector; list projection is compact.
    return {"depth": "records", "checkedAt": STAMP, "inventoryComplete": True,
            "mods": {f"live-{i}": {"status": "analyzed", "plugins": [
                {"name": f"Fixture{i}.esp", "winner": True, "winnerKnown": True,
                 "winnerModId": f"live-{i}", "active": True, "loadOrder": i,
                 "header": {"lightEffective": True, "masters": [f"Master{m}.esm" for m in range(8)]},
                 "fingerprint": {"sha256": "a" * 64, "size": 4096},
                 "records": {"scanComplete": True, "newRecordTypes": {f"A{n:03d}": n + 1 for n in range(32)}},
                 "eligibility": {"status": "already-light", "reasons": ["Synthetic fixture"]},
                 "provenance": {"state": "not-configured"}, "masterIssues": [], "errors": []}]}
                for i in range(count)}}


def request(service, method, path, body=None):
    port = service._server.server_port
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    headers = {"X-Arsenal-Token": service.token, "Origin": f"http://127.0.0.1:{port}"}
    payload = json.dumps(body).encode() if body is not None else None
    started = time.perf_counter()
    connection.request(method, "/api/" + path, body=payload, headers=headers)
    response = connection.getresponse()
    result = response.read()
    elapsed = (time.perf_counter() - started) * 1000
    connection.close()
    if response.status != 200 or not json.loads(result).get("ok"):
        raise AssertionError((response.status, result[:500]))
    return elapsed, result


def summary(samples):
    ordered = sorted(samples)
    return {"medianMs": statistics.median(samples), "p95Ms": ordered[math.ceil(.95 * len(ordered)) - 1],
            "minMs": ordered[0], "maxMs": ordered[-1], "samplesMs": samples}


def compare(old_class, count, rounds, temporary):
    fixture = catalog(count)
    services = {}
    for variant, service_class in (("baseline", old_class), ("current", ArsenalService)):
        folder = temporary / variant / str(count)
        folder.mkdir(parents=True)
        service = service_class("<html/>", folder / "cache.json",
            lambda req: (_ for _ in ()).throw(AssertionError("Benchmark must not reach native MO2")),
            copy.deepcopy(fixture), discovery_directory=folder / "sessions")
        service.intelligence.runner = types.SimpleNamespace(available=False)
        service.start()
        services[variant] = service
    result = {"mods": count, "fixtureSha256": hashlib.sha256(json.dumps(fixture, sort_keys=True).encode()).hexdigest(), "workloads": {}}
    try:
        # Freeze only wall-clock freshness. All elapsed timing uses perf_counter.
        with patch("time.time", return_value=STAMP):
            for workload in ("repeated_full_catalog_http", "single_row_progress_http", "uncached_audited_catalog_http"):
                timings = {"baseline": [], "current": []}
                if workload == "uncached_audited_catalog_http":
                    for service in services.values():
                        service.intelligence._result = analysis(count)
                        service.intelligence._state = {"state": "completed", "profileId": fixture["profileId"],
                                                       "depth": "records", "checkedAt": STAMP}
                payload_bytes = 0
                for iteration in range(rounds + 2):
                    order = ("baseline", "current") if iteration % 2 == 0 else ("current", "baseline")
                    replies = {}
                    for variant in order:
                        service = services[variant]
                        if workload == "single_row_progress_http":
                            elapsed, reply = request(service, "POST", "progress", {
                                "profileId": fixture["profileId"], "rows": {"live-20": {
                                    "note": "One saved mod note", "favorite": bool(iteration % 2)}},
                                "prefs": {"sort": "order"}, "verifiedRuntime": "1.5.97"})
                        else:
                            if workload == "uncached_audited_catalog_http":
                                with service._guard:
                                    service._generation += 1  # Actual cache invalidation, before timing.
                            elapsed, reply = request(service, "GET", "snapshot")
                            document = json.loads(reply)
                            assert len(document["catalog"]["items"]) == count
                            assert document["catalog"]["items"][-1]["id"] == f"live-{count - 1}"
                            payload_bytes = len(reply)
                        replies[variant] = reply
                        if iteration >= 2:
                            timings[variant].append(elapsed)
                    if workload != "single_row_progress_http":
                        assert replies["baseline"] == replies["current"], "Catalog behavior changed"
                    else:
                        assert services["baseline"].chronicle.progress(fixture["profileId"]) == services["current"].chronicle.progress(fixture["profileId"])
                previous, current = summary(timings["baseline"]), summary(timings["current"])
                result["workloads"][workload] = {"baseline": previous, "current": current,
                    "medianSpeedup": previous["medianMs"] / current["medianMs"],
                    "catalogBytes": payload_bytes or None, "equivalentData": True}
    finally:
        for service in services.values():
            service.stop()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default=BASELINE)
    parser.add_argument("--rounds", type=int, default=9)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 5 <= args.rounds <= 100:
        parser.error("Use 5–100 rounds")
    with tempfile.TemporaryDirectory() as directory:
        temporary = Path(directory)
        old_class = baseline_package(temporary / "baseline-source", args.baseline)
        result = {"schema": 1, "measuredAtUtc": datetime.now(timezone.utc).isoformat(),
                  "baselineCommit": args.baseline, "python": platform.python_version(), "platform": platform.platform(),
                  "rounds": args.rounds, "warmups": 2, "order": "alternating baseline/current",
                  "scope": "Actual Python loopback HTTP with complete synthetic catalogs, owned temporary Chronicle writes, and byte-for-byte catalog equivalence; excludes native MO2, browser rendering, Nexus, downloads and installation",
                  "freshnessClock": "Fixed equally for both variants; elapsed timing uses perf_counter",
                  "currentSourceSha256": {name: hashlib.sha256((ROOT / "mo2_update_arsenal" / name).read_bytes()).hexdigest() for name in SOURCE_FILES},
                  "cases": [compare(old_class, count, args.rounds, temporary) for count in (2500, 10000)]}
    document = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(document, encoding="utf-8")
    print(document)


if __name__ == "__main__":
    main()
