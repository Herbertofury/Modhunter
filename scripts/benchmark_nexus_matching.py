"""Measure full, checked exact-file decisions against a dated public Nexus inventory."""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "scripts" / "fixtures" / "nexus-50307-public-files.json"
# Public provider IDs and independently expected lineages. Installations in
# the workload are constructed from these cases, never from anyone's profile.
CASES = (
    (252976, "2.6.0.0", "current", 252976),
    (696437, "4.1.0", "current", 696437),
    (563201, "4.0.3", "candidate", 696437),
    (548332, "4.0", "candidate", 696437),
    (205114, "V1", "review", None),
    (205115, "V1", "review", None),
    (696439, "4.1.0", "current", 696439),
    (None, "1.0", "review", None),
)


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def peak_rss_bytes():
    if sys.platform == "win32":
        import psutil
        return psutil.Process().memory_info().peak_wset
    import resource
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == "darwin" else value * 1024


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-root", type=Path, default=ROOT)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    engine = args.engine_root.resolve()
    sys.path.insert(0, str(engine))
    from mo2_update_arsenal.file_advisor import NexusFileIndex
    from mo2_update_arsenal.scanner import public_project_metadata, summary_from_metadata

    fixture_bytes = FIXTURE.read_bytes()
    fixture = json.loads(fixture_bytes)
    files = fixture["files"]
    by_id = {row["fileId"]: row for row in files}
    if len(files) != 61 or len(by_id) != len(files):
        raise ValueError("The reference inventory must include all 61 distinct public files.")
    observed = datetime.fromisoformat(fixture["observedAt"]).timestamp()
    items = []
    for file_id, version, verdict, target in CASES:
        row = by_id.get(file_id, {})
        item = {"name": row.get("name", "Unidentified local archive"), "nexusId": 50307,
                "installationArchive": row.get("uri", ""), "installedVersion": version,
                "installedFiles": [[50307, file_id]] if file_id else []}
        items.append((item, (verdict, target)))

    samples = []
    fingerprints = set()
    for trial in range(6):
        wall_start = time.perf_counter_ns()
        cpu_start = time.process_time_ns()
        if trial == 0:
            metadata = public_project_metadata("skyrimspecialedition", 50307, fixture["project"], files)
            index = NexusFileIndex(metadata["fileCatalog"])
        durations = []
        results = []
        counts = Counter()
        for i in range(11_325):
            item, expected = items[i % len(items)]
            row_start = time.perf_counter_ns()
            result = summary_from_metadata(item, metadata, observed, index)
            durations.append((time.perf_counter_ns() - row_start) / 1e6)
            verdict = result["verdict"]
            if (verdict, result.get("targetFileId")) != expected:
                raise RuntimeError(f"Unexpected verdict or target at row {i}: {result}")
            counts[verdict] += 1
            results.append(result)
        serialized = json.dumps(results, sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(serialized).hexdigest()
        fingerprints.add(digest)
        elapsed_ms = (time.perf_counter_ns() - wall_start) / 1e6
        samples.append({"indexBuiltDuringTrial": trial == 0, "rows": len(results), "verdicts": counts,
                        "fullDecisionSha256": digest, "elapsedMs": elapsed_ms,
                        "cpuMs": (time.process_time_ns() - cpu_start) / 1e6,
                        "peakProcessRssBytes": peak_rss_bytes(),
                        "rowsPerSecond": len(results) * 1000 / elapsed_ms,
                        "rowP50Ms": statistics.median(durations), "rowP99Ms": percentile(durations, 0.99)})
    if len(fingerprints) != 1:
        raise RuntimeError("Cold and warm trials returned different completed decisions.")
    warm = [r["elapsedMs"] for r in samples[1:]]
    report = {"schema": 1, "engineCommit": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=engine, text=True).strip(),
        "python": platform.python_version(), "host": platform.platform(),
        "scope": "61 public files and eight constructed install cases repeated to 11,325 rows. "
                 "The cold trial builds the index. Every trial validates and serializes every decision. "
                 "Network time and the private production collection are outside this benchmark.",
        "source": fixture["sourceUrl"], "observedAt": fixture["observedAt"],
        "fixtureSha256": hashlib.sha256(fixture_bytes).hexdigest(), "samples": samples,
        "warmMedianMs": statistics.median(warm), "warmP95Ms": percentile(warm, 0.95)}
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Verified {len(samples)} batches of 11,325 rows. Warm median {report['warmMedianMs']:.1f} ms.")


if __name__ == "__main__":
    main()
