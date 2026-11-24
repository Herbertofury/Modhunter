"""Replay complete saved provider observations through the public and native decision paths."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mo2_update_arsenal.file_advisor import NexusFileIndex
from mo2_update_arsenal.scanner import (
    Cache, NativeScanCatalog, NexusClient, NexusError, PUBLIC_BATCH_SIZE, UpdateScanner,
    nexus_project_id, public_batch_metadata, public_project_metadata, summary_from_metadata,
)


def signature(record):
    return tuple(record.get(key) for key in ("verdict", "evidenceScope", "installedFileId", "targetFileId", "latestVersion"))


def scanner_replay(catalog, native, public, raw_valid, oracle, output_dir):
    """Actually run the scanner and its durable checkpoints on all paired real observations."""
    game = catalog["game"]
    rows = [{**item, "updateEligible": False} if nexus_project_id(item, game) not in oracle else item
            for item in catalog["items"]]
    snapshot = {**catalog, "items": rows}
    class RecordedGraph:
        def get(self, path):
            mid = int(path.rsplit("/", 1)[1].split(".")[0])
            if mid not in raw_valid:
                raise NexusError("No published observation")
            return raw_valid[mid]

        def get_projects(self, ids):
            return {mid: public[mid] for mid in ids if mid in public}

    def signed(path):
        mid = int(re.search(r"/mods/([0-9]+)", path)[1])
        metadata = native[f"{game}/{mid}"]["meta"]
        if path.endswith("/files.json"):
            return metadata["fileCatalog"]
        return {k: v for k, v in metadata.items() if k != "fileCatalog"}

    results, reference = {}, {}
    for name in ("signed-v1", "previous-per-project-public", "batched-startup", "batched-warm-restart"):
        mode = "deep" if name == "previous-per-project-public" else "startup"
        # On a cold cache, deep executes the previous one-at-a-time public
        # verification handler; both baselines receive the same signed replies.
        cache_name = "batched-startup" if name == "batched-warm-restart" else name
        lookup = None if name == "signed-v1" else NativeScanCatalog(game, lambda _: RecordedGraph())
        start = time.perf_counter()
        scanner = UpdateScanner(Cache(output_dir / (cache_name + ".json")), NexusClient("recorded", signed), native_catalog=lookup)
        result = scanner.scan(snapshot, mode)
        elapsed = time.perf_counter() - start
        decisions = {key: signature(record) for key, record in result["itemRecords"].items()}
        if not reference:
            reference = decisions
        counts = {k: result.get(k, 0) for k in ("publicGraphRequests", "providerProjectRequests", "providerFileRequests", "fromCache")}
        counts["totalTransports"] = sum(counts[k] for k in ("publicGraphRequests", "providerProjectRequests", "providerFileRequests"))
        results[name] = {**counts, "elapsedSeconds": elapsed, "projectsPerSecond": len(oracle) / elapsed,
                         "providerErrors": len(result["errors"]), "records": len(decisions),
                         "decisionMismatches": sum(reference.get(k) != decisions.get(k) for k in reference.keys() | decisions.keys())}
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("profile", "public-cache", "native-cache"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    catalog = json.loads(args.profile.read_text(encoding="utf-8-sig"))["catalog"]
    game = catalog["game"]
    native = json.loads(args.native_cache.read_text(encoding="utf-8-sig"))["entries"]
    with sqlite3.connect(args.public_cache.resolve().as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        raw = {mid: json.loads(payload) for mid, payload in db.execute(
            "SELECT mod_id,payload FROM projects WHERE game=? AND payload IS NOT NULL", (game,))}
    grouped = defaultdict(list)
    for item in catalog["items"]:
        mid = nexus_project_id(item, game)
        if mid and not item.get("installedFileEvidenceIncomplete"):
            grouped[mid].append(item)
    counters, timings, mismatches = {}, {}, []
    oracle, public, raw_valid = {}, {}, {}
    started = time.perf_counter()
    for mid, rows in grouped.items():
        try:
            project, files = raw[mid]
            public[mid] = public_batch_metadata(game, mid, project)
            raw_valid[mid] = public_project_metadata(game, mid, project, files)
        except (KeyError, ValueError, TypeError, NexusError):
            pass
        prior = native.get(f"{game}/{mid}", {}).get("meta", {})
        if not isinstance(prior.get("fileCatalog", {}).get("file_updates"), list):
            continue
        index = NexusFileIndex(prior["fileCatalog"])
        oracle[mid] = [summary_from_metadata(row, prior, 1, index) for row in rows]
    timings["preparePublicAndNativeOracleSeconds"] = time.perf_counter() - started
    engine = NativeScanCatalog(game)
    for warm in (False, True):
        counts = Counter()
        started = time.perf_counter()
        for mid, rows in grouped.items():
            previous = native.get(f"{game}/{mid}") if warm else None
            needs_files = any(r.get("installedFiles") or r.get("installationArchive") for r in rows)
            safe = public.get(mid) if not needs_files and engine.matches_published(mid, public.get(mid), previous) else None
            counts["publicQueries"] += 1
            counts["fastProjects" if safe else "nativeFallbackProjects"] += 1
            if not safe:
                counts["nativeFallbackFileLists"] += needs_files
                counts["nativeProjectMetadataRequests"] += not engine.matches_published(mid, public.get(mid), previous)
            if mid not in oracle:
                counts["projectsWithoutNativeFileOracle"] += 1
                continue
            counts["nativeOracleRows"] += len(rows)
            counts["nativeOracleProjects"] += 1
            if mid in raw_valid and not warm:
                raw_index = NexusFileIndex(raw_valid[mid]["fileCatalog"])
                for item, before in zip(rows, oracle[mid]):
                    pub = summary_from_metadata(item, raw_valid[mid], 1, raw_index)
                    if signature(pub) != signature(before):
                        counts["rawPublicDifferences"] += 1
                        if pub["verdict"] in {"current", "candidate"} and before["verdict"] in {"current", "candidate"}:
                            counts["rawPublicDifferencesBothDecisive"] += 1
            if safe:
                index = NexusFileIndex(safe["fileCatalog"]) if "fileCatalog" in safe else None
                for item, before in zip(rows, oracle[mid]):
                    after = summary_from_metadata(item, safe, 1, index)
                    counts["comparedFastRows"] += 1
                    if signature(after) != signature(before):
                        counts["decisionMismatches"] += 1
                        if len(mismatches) < 16:
                            mismatches.append({"warm": warm, "project": mid, "native": signature(before), "public": signature(after)})
            else:
                counts["retainedNativeOracleRows"] += len(rows)
        key = "warm" if warm else "cold"
        counters[key] = dict(counts)
        timings[key + "DecisionSeconds"] = time.perf_counter() - started
    with tempfile.TemporaryDirectory(prefix="modhunter-scan-replay-") as temporary:
        replay = scanner_replay(catalog, native, public, raw_valid, oracle, Path(temporary))
    result = {"profileRows": len(catalog["items"]), "eligibleProjects": len(grouped), "eligibleRows": sum(map(len, grouped.values())),
              "publicBatchRequests": (len(grouped)+PUBLIC_BATCH_SIZE-1)//PUBLIC_BATCH_SIZE, "gameLookupRequests": 1,
              "counts": counters, "timings": timings, "mismatches": mismatches,
              "pairedScannerReplay": replay, "pairedScannerReplayProjects": len(oracle),
              "pairedScannerReplayRows": sum(map(len, oracle.values())),
              "notes": "Wall times cover local playback, actual scheduling, per-row decisions and durable cache writes. "
                       "Projects without a saved signed file list are counted in full workload planning and excluded from the paired scanner timing. "
                       "The saved public packet had no project update timestamps."}
    encoded = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded)
    return int(bool(mismatches or any(r["providerErrors"] for r in replay.values())
                    or replay["batched-startup"]["decisionMismatches"] or replay["batched-warm-restart"]["decisionMismatches"]))


if __name__ == "__main__":
    sys.exit(main())
