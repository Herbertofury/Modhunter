"""Compare two Rust CLIs on identical, generated Bethesda plugin byte streams.

Build each revision in release mode with the same locked Rust toolchain, then:
python scripts/benchmark_plugin_audit.py --baseline /path/to/old/modhunter-cli \
    --candidate native/target/release/modhunter-cli \
    --output native/evidence/plugin-audit-comparison.json

This measures read-only audits of generated local files. It does not benchmark
live MO2, actual mods, Nexus, download/install speed, or cold physical storage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import struct
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


def make_plugin(path: Path, records: int, payload_size: int) -> str:
    hedr = b"HEDR" + struct.pack("<HfII", 12, 1.7, records + 1, 0x800)
    header = struct.pack("<4sIIIIHH", b"TES4", len(hedr), 0, 0, 0, 44, 0)
    group = struct.pack("<4sI4sIII", b"GRUP", 24 + records * (24 + payload_size), b"MISC", 0, 0, 0)
    payload = bytes(i % 251 for i in range(payload_size))
    digest = hashlib.sha256()
    with path.open("wb") as output:
        for value in (header, hedr, group):
            output.write(value)
            digest.update(value)
        for index in range(records):
            record = struct.pack("<4sIIIIHH", b"MISC", payload_size, 0, 0x800 + index, 0, 44, 0)
            for value in (record, payload):
                output.write(value)
                digest.update(value)
    return digest.hexdigest()


def run(cli: Path, request: dict, expected_hash: str, expected_size: int, records: int) -> tuple[dict, dict]:
    start = time.perf_counter()
    process = subprocess.run(
        [str(cli), "audit-plugins", "--request", "-"],
        input=json.dumps(request).encode(), capture_output=True, check=True, timeout=120,
    )
    process_ms = (time.perf_counter() - start) * 1000
    result = json.loads(process.stdout)
    copy = result["mods"]["fixture"]["plugins"][0]
    summary = copy["records"]
    if (copy["errors"] or copy["fingerprint"]["sha256"] != expected_hash
            or not summary["scanComplete"] or summary["totalRecords"] != records
            or summary["newRecordTypes"] != {"MISC": records}
            or result["stats"]["bytesRead"] != expected_size
            or result["stats"]["deepScanned"] != 1):
        raise AssertionError("Audit bytes, fresh digest, or record semantics differ from the generated fixture")
    sample = {"engineMs": result["stats"]["elapsedMs"], "processMs": process_ms,
              "bytesRead": result["stats"]["bytesRead"], "records": summary["totalRecords"]}
    # Ignore only timing/run metadata; all plugin facts, paths, hashes, master
    # findings and eligibility guidance must remain exactly equivalent.
    semantic = {key: value for key, value in result.items() if key not in {"stats", "checkedAt"}}
    return sample, semantic


def describe(path: Path) -> dict:
    return {"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "version": subprocess.check_output([str(path), "--version"], text=True).strip()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--rounds", default=7, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not 1 <= args.rounds <= 30:
        parser.error("rounds must be between 1 and 30")
    binaries = {"baseline": args.baseline.resolve(strict=True), "candidate": args.candidate.resolve(strict=True)}
    report = {
        "schemaVersion": 1, "generatedAt": datetime.now(timezone.utc).isoformat(),
        "environment": {"os": platform.platform(), "architecture": platform.machine()},
        "executables": {name: describe(path) for name, path in binaries.items()},
        "method": {
            "fixture": "One deterministic Skyrim SE plugin per workload; 2 audit workers; fresh CLI per sample",
            "order": "One discarded warmup per revision, then alternating baseline/candidate order",
            "engine": "PluginAnalyzer audit elapsed time reported by the engine; includes enumeration, record scan, SHA-256 and fact resolution",
            "process": "CLI launch, JSON request, complete audit, result serialization and exit; excludes parent JSON parsing",
            "equivalence": "Every non-timing result field matches between revisions; exact file bytes, digest and record count also independently verified",
            "osCache": "Generated files and warmup populate OS caches; this is not a cold disk benchmark",
            "limitations": "Synthetic local Linux/Windows workload only, not actual MO2, mod files, GUI latency or download/install throughput",
        },
        "workloads": [],
    }
    with tempfile.TemporaryDirectory(prefix="modhunter-plugin-audit-") as temporary:
        root = Path(temporary)
        for records, payload_size in ((100000, 0), (100000, 32), (10000, 1024), (1024, 65537)):
            folder = root / f"records-{records}-payload-{payload_size}"
            folder.mkdir()
            path = folder / "Fixture.esp"
            expected_hash = make_plugin(path, records, payload_size)
            expected_size = path.stat().st_size
            request = {
                "game": "skyrimspecialedition", "gameRuntime": "1.6.1170", "profileId": "benchmark",
                "deep": True, "workers": 2,
                "mods": [{"id": "fixture", "name": "Fixture", "path": str(folder), "enabled": True, "priority": 0}],
                "pluginStates": [{"name": "Fixture.esp", "active": True, "loadOrder": 0}],
            }
            samples: dict[str, list] = {name: [] for name in binaries}
            expected_semantic = None
            for index in range(args.rounds + 1):
                order = ("baseline", "candidate") if index % 2 == 0 else ("candidate", "baseline")
                for name in order:
                    sample, semantic = run(binaries[name], request, expected_hash, expected_size, records)
                    if expected_semantic is None:
                        expected_semantic = semantic
                    elif semantic != expected_semantic:
                        raise AssertionError("Revisions disagree on plugin facts or guidance")
                    if index:
                        samples[name].append(sample)
            medians = {name: {"engineMedianMs": statistics.median(s["engineMs"] for s in values),
                              "processMedianMs": statistics.median(s["processMs"] for s in values),
                              "samples": values} for name, values in samples.items()}
            entry = {"records": records, "payloadBytesPerRecord": payload_size, "fileBytes": expected_size,
                     "fileSha256": expected_hash, "semanticallyEquivalent": True, **medians,
                     "engineSpeedup": medians["baseline"]["engineMedianMs"] / medians["candidate"]["engineMedianMs"]}
            report["workloads"].append(entry)
            print(f"{records:,} records x {payload_size:,} payload bytes: "
                  f"{entry['baseline']['engineMedianMs']:.2f} -> {entry['candidate']['engineMedianMs']:.2f} ms "
                  f"({entry['engineSpeedup']:.2f}x); hashes and plugin facts match")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
