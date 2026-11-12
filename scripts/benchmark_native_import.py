"""Compare the existing Electron importer and Rust engine on identical fixtures.

The repeated-process measurement keeps one Rust ProfileImporter alive; the
fresh-process measurement does not. Neither measurement drops OS filesystem
caches. Fixtures are generated locally and never touch an installed MO2/game.
Run: python scripts/benchmark_native_import.py --output native/evidence/import-comparison.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import statistics
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "native"

NODE_WORKER = r"""
'use strict';
const {readMo2Profile}=require(process.argv[2]);
const profile=process.argv[3],rounds=Number(process.argv[4]);
(async()=>{
 const runs=[];let catalog=null,previous=null;
 for(let n=0;n<rounds;n++){
  const start=process.hrtime.bigint();
  catalog=await readMo2Profile(profile);
  const engineMs=Number(process.hrtime.bigint()-start)/1e6;
  if(previous&&JSON.stringify(previous.items)!==JSON.stringify(catalog.items))
   throw Error('Repeated Electron import changed row semantics');
  runs.push({engineMs,metadataCacheHits:null,filesRead:null});previous=catalog;
 }
 process.stdout.write(JSON.stringify({runs,catalog}));
})().catch(error=>{console.error(error);process.exitCode=1;});
"""

RUST_WORKER = r"""
use modhunter_core::{ImportRequest,ImportResult,ProfileImporter};
use std::{path::PathBuf,time::Instant};
fn main(){
 let args:Vec<String>=std::env::args().collect();
 let request=ImportRequest{modlist_path:PathBuf::from(&args[1]),ini_path:None,mods_path:None,workers:Some(args[3].parse().unwrap())};
 let rounds:usize=args[2].parse().unwrap();
 let importer=ProfileImporter::new();
 let mut previous:Option<ImportResult>=None;let mut runs=Vec::new();
 for _ in 0..rounds{
  let start=Instant::now();let result=importer.import(&request).expect("Rust fixture import failed");
  let elapsed=start.elapsed().as_secs_f64()*1000.;
  if let Some(ref old)=previous{assert_eq!(old.catalog["items"],result.catalog["items"],"Repeated Rust import changed row semantics");}
  runs.push(format!("{{\"engineMs\":{},\"metadataCacheHits\":{},\"filesRead\":{}}}",elapsed,result.stats.metadata_cache_hits,result.stats.files_read));
  previous=Some(result);
 }
 println!("{{\"runs\":[{}],\"catalog\":{}}}",runs.join(","),previous.unwrap().catalog);
}
"""


def executable(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    fallback = Path.home() / ".cargo" / "bin" / (name + (".exe" if os.name == "nt" else ""))
    if fallback.is_file():
        return str(fallback)
    raise RuntimeError(f"{name} is required on PATH")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_fixture(root: Path, count: int) -> Path:
    profile = root / "profiles" / "Benchmark"
    mods = root / "mods"
    profile.mkdir(parents=True)
    mods.mkdir()
    (root / "ModOrganizer.ini").write_text(
        "[General]\ngameName=Skyrim Special Edition\ngameVersion=1.6.1170\n", encoding="utf-8"
    )
    lines = ["# Identical generated MO2 fixture for both importers"]
    for n in reversed(range(count)):
        name = f"Fixture Mod {n:05}"
        lines.append(("-" if n % 3 == 0 else "+") + name)
        directory = mods / name
        directory.mkdir()
        (directory / "meta.ini").write_text(
            "[General]\ngameName=Skyrim Special Edition\n"
            f"modid={n+1}\nversion=1.0\nnewestVersion=2.0\n"
            "author=Fixture Author\nnotes=Deterministic import comparison fixture\n",
            encoding="utf-8",
        )
        if n % 50 == 0:
            plugin_dir = directory / "SKSE" / "Plugins"
            plugin_dir.mkdir(parents=True)
            (plugin_dir / "fixture.dll").write_bytes(b"fixture-not-executable")
    modlist = profile / "modlist.txt"
    modlist.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return modlist


def measure(command: list[str]) -> tuple[dict, float, int]:
    start = time.perf_counter()
    result = subprocess.run(command, check=True, capture_output=True, timeout=180)
    elapsed = (time.perf_counter() - start) * 1000
    # Parent-side JSON parsing/verification is deliberately outside the timer.
    return json.loads(result.stdout), elapsed, len(result.stdout)


def common_rows(catalog: dict, count: int) -> dict:
    rows = catalog["items"]
    if len(rows) != count:
        raise AssertionError(f"Expected {count} rows, found {len(rows)}")
    names = [row["name"] for row in rows]
    if len(set(names)) != count:
        raise AssertionError("An importer duplicated row identities")
    fields = ("name", "mo2InternalName", "installedVersion", "latestKnownVersion", "enabled", "nexusId", "author", "screenshotNote", "flags")
    return {
        row["name"]: {
            **{field: row.get(field) for field in fields},
            "sourceUrl": row["source"]["url"],
            "sourceVerified": row["source"]["verified"],
            "sourceType": row["source"]["type"],
        }
        for row in rows
    }


def verify(rust: dict, electron: dict, count: int) -> None:
    if common_rows(rust, count) != common_rows(electron, count):
        raise AssertionError("Importers disagree on common mod metadata")
    for n, row in enumerate(rust["items"]):
        if row["name"] != f"Fixture Mod {n:05}" or row["priority"] != n:
            raise AssertionError("Rust importer did not restore MO2 priority order")
    if electron["items"][0]["name"] != f"Fixture Mod {count-1:05}":
        raise AssertionError("Legacy priority behavior changed; reassess the comparison contract")


def summary(samples: list[dict]) -> dict:
    return {
        "engineMedianMs": statistics.median(s["engineMs"] for s in samples),
        "processMedianMs": statistics.median(s["processMs"] for s in samples),
        "samples": samples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", nargs="+", type=int, default=[2500, 10000])
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--output", type=Path, default=ROOT / "native/evidence/import-comparison.json")
    parser.add_argument("--skip-build", action="store_true", help="Reuse a previously built release core")
    args = parser.parse_args()
    if not 1 <= args.rounds <= 30 or not 1 <= args.workers <= 32 or any(n < 1 or n > 20000 for n in args.sizes):
        parser.error("Expected 1..30 rounds, 1..32 workers, and 1..20000 mods per fixture")
    cargo, rustc, node = executable("cargo"), executable("rustc"), executable("node")
    if not args.skip_build:
        subprocess.run([cargo, "build", "--locked", "--release", "-p", "modhunter-core", "--lib", "--bin", "modhunter-cli"], cwd=NATIVE, check=True)
    deps = NATIVE / "target/release/deps"
    core = max(deps.glob("libmodhunter_core-*.rlib"), key=lambda p: p.stat().st_mtime_ns)
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except subprocess.CalledProcessError:
        revision = "unavailable"
    report = {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "fixture": "Identical generated local MO2 metadata directories, reversed modlist, 2/3 enabled, one DLL per 50 mods; no native MO2, Nexus, download, install, or game operations",
        "method": {
            "freshProcess": "Each sample launches a new Node or Rust worker and a fresh importer; engine timing encloses only readMo2Profile/import; process timing includes process startup, engine, JSON serialization and exit, excluding parent JSON parsing. Alternating execution order limits order bias.",
            "persistentProcess": "One process and importer per engine; first import measured separately, followed by repeated imports. Rust metadata cache stays alive; the old Node importer rereads metadata. These are not fresh-process timings.",
            "osCache": "OS filesystem caches are not forcibly evicted; fixture creation warms the page cache. 'First' or 'fresh' means empty application cache, not cold physical storage.",
            "guiStartup": "Not measured. Fresh-process timings launch Node/Rust import workers, not the Electron application or Windows Rust/WebView2 desktop.",
            "equivalence": "Compare every row by name, internal name, versions, enabled state, Nexus ID, author, notes, DLL flags and exact source URL/type/verified flag. IDs are platform/path-dependent and excluded. Legacy reversed priority order is excluded from equality; Rust priority order independently asserted.",
            "workers": {"rust": args.workers, "legacyNode": 24},
        },
        "environment": {"os": platform.platform(), "architecture": platform.machine(), "logicalCpuCount": os.cpu_count(), "node": subprocess.check_output([node, "--version"], text=True).strip(), "rustc": subprocess.check_output([rustc, "--version"], cwd=NATIVE, text=True).strip()},
        "source": {"gitHead": revision, "legacyImporterSha256": sha256(ROOT / "desktop/offline_mo2.cjs"), "rustImporterSha256": sha256(NATIVE / "crates/modhunter-core/src/importer.rs"), "cargoLockSha256": sha256(NATIVE / "Cargo.lock")},
        "results": [],
    }
    with tempfile.TemporaryDirectory(prefix="modhunter-import-comparison-") as temporary:
        temp = Path(temporary)
        node_worker = temp / "legacy-worker.cjs"
        node_worker.write_text(NODE_WORKER, encoding="utf-8")
        rust_source = temp / "rust_worker.rs"
        rust_source.write_text(RUST_WORKER, encoding="utf-8")
        rust_worker = temp / ("rust-worker.exe" if os.name == "nt" else "rust-worker")
        subprocess.run([rustc, "--edition=2024", "-O", "-C", "debuginfo=0", "--extern", f"modhunter_core={core}", "-L", f"dependency={deps}", str(rust_source), "-o", str(rust_worker)], cwd=NATIVE, check=True)
        for count in args.sizes:
            modlist = make_fixture(temp / str(count), count)
            commands = {
                "rust": [str(rust_worker), str(modlist), "1", str(args.workers)],
                "electron": [node, str(node_worker), str(ROOT / "desktop/offline_mo2.cjs"), str(modlist), "1"],
            }
            samples: dict[str, list[dict]] = {"rust": [], "electron": []}
            for index in range(args.rounds):
                pair = {}
                for kind in (["rust", "electron"] if index % 2 == 0 else ["electron", "rust"]):
                    result, process_ms, byte_count = measure(commands[kind])
                    samples[kind].append({**result["runs"][0], "processMs": process_ms, "outputBytes": byte_count})
                    pair[kind] = result["catalog"]
                verify(pair["rust"], pair["electron"], count)
            persistent = {}
            catalogs = {}
            for kind in ("electron", "rust"):
                command = list(commands[kind])
                command[-2 if kind == "rust" else -1] = str(args.rounds + 1)
                result, process_ms, byte_count = measure(command)
                runs = result["runs"]
                persistent[kind] = {"firstEngineMs": runs[0]["engineMs"], "warmEngineMedianMs": statistics.median(r["engineMs"] for r in runs[1:]), "runs": runs, "totalProcessMs": process_ms, "outputBytes": byte_count}
                catalogs[kind] = result["catalog"]
            verify(catalogs["rust"], catalogs["electron"], count)
            if any(r["metadataCacheHits"] != count or r["filesRead"] != 2 for r in persistent["rust"]["runs"][1:]):
                raise AssertionError("Persistent Rust metadata cache failed its expected reuse contract")
            fresh = {kind: summary(values) for kind, values in samples.items()}
            row = {"mods": count, "freshProcess": fresh, "persistentProcess": persistent, "equivalentMetadata": True, "rustPriorityOrderVerified": True, "rustPersistentCacheVerified": True,
                   "ratiosLegacyOverRust": {"freshEngine": fresh["electron"]["engineMedianMs"] / fresh["rust"]["engineMedianMs"], "freshProcess": fresh["electron"]["processMedianMs"] / fresh["rust"]["processMedianMs"], "persistentWarmEngine": persistent["electron"]["warmEngineMedianMs"] / persistent["rust"]["warmEngineMedianMs"]}}
            report["results"].append(row)
            print(json.dumps({"mods": count, "freshRustEngineMs": fresh["rust"]["engineMedianMs"], "freshElectronEngineMs": fresh["electron"]["engineMedianMs"], "freshRustProcessMs": fresh["rust"]["processMedianMs"], "freshElectronProcessMs": fresh["electron"]["processMedianMs"], "warmRustEngineMs": persistent["rust"]["warmEngineMedianMs"], "warmElectronEngineMs": persistent["electron"]["warmEngineMedianMs"], "ratios": row["ratiosLegacyOverRust"]}), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Evidence written to {args.output}")


if __name__ == "__main__":
    main()
