#!/usr/bin/env python3
"""Compare single-row MO2 update preparation against a repository checkpoint.

The fixture has two equally plausible cached files, so every call finishes at
the file-choice boundary. No network request, native MO2 action, or installation
is performed. This measures Python preparation work, not download throughput.
"""
from __future__ import annotations

import argparse
import json
import math
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import types
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mo2_update_arsenal.bridge import ArsenalService


def catalog(count):
    return {"profileId": "benchmark", "profileName": "Benchmark", "game": "skyrimspecialedition",
            "gameRuntime": "1.5.97", "groups": [], "items": [
                {"id": f"mod-{index}", "name": f"Fixture {index}", "mo2InternalName": f"Fixture {index}",
                 "nexusId": index + 1, "installedVersion": "1.0", "flags": [],
                 "source": {"type": "nexus", "verified": True,
                            "url": f"https://www.nexusmods.com/skyrimspecialedition/mods/{index + 1}"},
                 "update": {"source": "nexus-api", "verdict": "candidate", "latestVersion": "2.0"}}
                for index in range(count)]}


def baseline_class(reference):
    source = subprocess.check_output(
        ["git", "show", f"{reference}:mo2_update_arsenal/bridge.py"], cwd=ROOT, text=True)
    module = types.ModuleType("mo2_update_arsenal._benchmark_baseline")
    exec(compile(source, f"{reference}:bridge.py", "exec"), module.__dict__)
    return module.ArsenalService


def measure(service_class, count, rounds):
    with tempfile.TemporaryDirectory() as temporary:
        def no_native(_):
            raise AssertionError("Ambiguous file fixture must never reach MO2")

        service = service_class("<html/>", Path(temporary) / "cache.json", no_native, catalog(count))
        service.fetch_files = lambda _: {"ok": True, "files": [
            {"fileId": file_id, "name": name, "version": "2.0", "category": 1}
            for file_id, name in ((700, "Main option A"), (701, "Main option B"))]}
        request = {"profileId": "benchmark", "itemIds": ["mod-20"]}
        timings = []
        try:
            for iteration in range(rounds + 3):
                began = time.perf_counter()
                result = service.quick_update(request)
                elapsed = (time.perf_counter() - began) * 1000
                if not result.get("ok") or len(result.get("skipped", [])) != 1 or result.get("queued"):
                    raise AssertionError(f"Fixture changed outcome: {result}")
                if iteration >= 3:
                    timings.append(elapsed)
        finally:
            service.stop()
        ordered = sorted(timings)
        return {"medianMs": statistics.median(timings), "p95Ms": ordered[math.ceil(.95 * len(ordered)) - 1],
                "minMs": ordered[0], "maxMs": ordered[-1], "samplesMs": timings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="17e4a9fc93712f6f4db4aafd762a5e5aedb2647d")
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 5 <= args.rounds <= 1000:
        parser.error("Use between 5 and 1000 rounds")
    old = baseline_class(args.baseline)
    result = {"schema": 1, "measuredAtUtc": datetime.now(timezone.utc).isoformat(),
              "baselineCommit": args.baseline, "python": platform.python_version(),
              "platform": platform.platform(), "rounds": args.rounds,
              "workload": "single-row Quick Update preparation with two ambiguous cached Nexus main files",
              "scope": "Synthetic local Python preparation only; no network, Windows GUI, native MO2, download or install timing",
              "cases": []}
    for count in (2500, 10000):
        previous = measure(old, count, args.rounds)
        current = measure(ArsenalService, count, args.rounds)
        result["cases"].append({"mods": count, "baseline": previous, "current": current,
                                "medianSpeedup": previous["medianMs"] / current["medianMs"]})
    document = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(document, encoding="utf-8")
    print(document)


if __name__ == "__main__":
    main()
