#!/usr/bin/env python3
"""Bounded read-only smoke of four public release APIs through the compiled CLI.

This is an opt-in live observation, not a deterministic CI gate or a download,
installation, native-MO2, game, account, or compatibility acceptance certificate.
It writes counts/identity/status/hash evidence, never full third-party prose.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import platform
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_FILES = [
    "native/Cargo.lock",
    "native/rust-toolchain.toml",
    "native/crates/modhunter-core/Cargo.toml",
    "native/crates/modhunter-core/src/lib.rs",
    "native/crates/modhunter-core/src/bin/modhunter-cli.rs",
    "native/crates/modhunter-core/src/source_adapters/mod.rs",
    "native/crates/modhunter-core/src/source_adapters/identity.rs",
    "native/crates/modhunter-core/src/source_adapters/http.rs",
    "native/crates/modhunter-core/src/source_adapters/parsers.rs",
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def item(name: str, url: str) -> dict:
    return {"id": name, "installedVersion": "0.0", "source": {"url": url, "verified": True, "kind": "project"}}


def observe(cli: Path, request: dict) -> tuple[dict, float]:
    started = time.monotonic()
    process = subprocess.run([str(cli), "scan-sources", "--request", "-"],
                             input=json.dumps(request).encode(), capture_output=True,
                             cwd=ROOT, timeout=25, check=False)
    elapsed = round((time.monotonic() - started) * 1000, 3)
    if process.returncode != 0:
        raise RuntimeError(f"CLI exited {process.returncode}: {process.stderr.decode(errors='replace')[:300]}")
    if len(process.stdout) > 30 * 1024 * 1024:
        raise RuntimeError("CLI exceeded its output contract")
    result = json.loads(process.stdout)
    if result.get("schemaVersion") != 1 or result.get("profileId") != request["profileId"]:
        raise RuntimeError("CLI response profile/schema differs from request")
    if set(result.get("itemRecords", {})) != {i["id"] for i in request["items"]}:
        raise RuntimeError("CLI omitted or added source rows")
    return result, elapsed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cli = args.cli.resolve()
    before = {p: sha(ROOT / p) for p in SOURCE_FILES}
    binary = sha(cli)
    profiles = [
        {"profileId": "live-api-bethesda-tools", "game": "skyrimspecialedition", "gameRuntime": "1.6.1170", "items": [
            item("github", "https://github.com/ModOrganizer2/modorganizer"),
            item("gitlab", "https://gitlab.com/OpenMW/openmw")]},
        {"profileId": "live-api-minecraft", "game": "minecraft", "gameRuntime": "1.21.1", "loader": "fabric", "items": [
            item("modrinth", "https://modrinth.com/mod/sodium")]},
        {"profileId": "live-api-valheim", "game": "valheim", "gameRuntime": "unknown", "items": [
            item("thunderstore", "https://thunderstore.io/c/valheim/p/denikson/BepInExPack_Valheim/")]},
    ]
    records = []
    for profile in profiles:
        request = {"schemaVersion": 1, "budgetMs": 18_000, "workers": 4, **profile}
        first, elapsed = observe(cli, request)
        repeated, repeated_elapsed = observe(cli, {**request, "cache": first.get("cache", {})})
        for source in profile["items"]:
            result = first["itemRecords"][source["id"]]
            cached = repeated["itemRecords"][source["id"]]
            assets = result.get("releaseAssets") or []
            records.append({
                "provider": source["id"], "sourceUrl": source["source"]["url"],
                "outcome": result.get("outcome"), "availability": result.get("availability"),
                "reason": result.get("reason"), "projectId": result.get("projectId"),
                "releaseId": result.get("releaseId"), "latestVersion": result.get("latestVersion"),
                "checkedAt": result.get("checkedAt"), "retryAt": result.get("retryAt"),
                "compatibilityStatus": (result.get("compatibility") or {}).get("status"),
                "moderationStatus": result.get("moderationStatus"),
                "metadataCounts": {"files": len(assets), "queueableArchives": sum(a.get("installable") is True for a in assets),
                    "filesWithPublishedHashes": sum(bool(a.get("hashes")) for a in assets),
                    "dependencies": len(result.get("dependencies") or []), "media": len(result.get("media") or []),
                    "changelogCharacters": len(result.get("changelog") or "")},
                "publisherObserved": bool(result.get("publisher")), "authorObserved": bool(result.get("author")),
                "firstRequestStats": first.get("stats"), "processElapsedMs": elapsed,
                "cacheRepeat": {"outcome": cached.get("outcome"), "freshness": cached.get("freshness"),
                    "stats": repeated.get("stats"), "processElapsedMs": repeated_elapsed},
            })
    after = {p: sha(ROOT / p) for p in SOURCE_FILES}
    if before != after or sha(cli) != binary:
        raise RuntimeError("Sources or compiled helper changed during live observation")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    exact = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", *SOURCE_FILES], cwd=ROOT).returncode == 0
    untracked = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "--", *SOURCE_FILES], cwd=ROOT, text=True)
    evidence = {
        "schemaVersion": 1, "recordedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "scope": "Compiled Rust public API reads and cache reuse only; no credentials, downloads, installation, MO2, GUI or game execution.",
        "platform": platform.system(), "architecture": platform.machine(), "baseCommit": commit,
        "sourceMatchesBaseCommit": exact and not untracked, "sourceSha256": before, "binarySha256": binary,
        "protocolCompleted": True, "successfulProviderObservations": sum(r["outcome"] == "release" for r in records),
        "providerObservations": records,
        "limitations": ["Single live samples are not benchmarks or permanent availability guarantees.",
            "The chosen tool/game profiles are synthetic and do not certify game compatibility or MO2 installation support.",
            "Unavailable, denied and rate-limited responses remain failures to establish current release facts.",
            "Published file digests were observed in metadata; no archive was downloaded or locally hash-verified."],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "protocolCompleted": True,
                      "successfulProviderObservations": evidence["successfulProviderObservations"],
                      "outcomes": {r["provider"]: r["outcome"] for r in records}}, indent=2))


if __name__ == "__main__":
    main()
