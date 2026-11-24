"""Strictly additive, diagnostic-only same-runner ModHunter native A/B.

Requires pinned packages and two *real clean Git checkouts*. Does not build,
install, synthesize source, modify stock launch scripts or publish artifacts.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import stat
import subprocess
import sys
from zipfile import ZipFile

sys.dont_write_bytecode = True
REPO = "https://github.com/Herbertofury/Modhunter"
ABLATION = "ABBA"
PORTABLE = "Modhunter-Rust-Windows.zip"
TEST2 = {
    "commit": "ec9da4a0322deb9435ac223f605e7bf1a4b2f34f",
    "tree": "ccd70daae8002ec836416ffd92019d217f6c8804",
    "version": "0.12.4",
    "workflow": REPO + "/actions/runs/45123416470",
    "portableBytes": 17112561,
    "portableSha256": "3f4414f517119b6067ace761170ed1a1714935f201a8fce32d44142a5093938d",
    "executableSha256": "7cfd8f930b8b1e5987ab5bb086173bb0779fa4f5c8b4d43a5742129fe67d1994",
}
SCRIPTS = {
    "native/scripts/benchmark_native_library.ps1": "e83d6e420117122e8ce0e809b4573f4e623bc974b13a38e933cd5fdd14c2bcb9",
    "native/scripts/bind_native_proof.py": "1ac9e84ca4ab00f55bea96f6b129f60e82ba207170e8cc7fc7f18d021e1858bc",
    "native/scripts/measure_native_library.cjs": "3eb7df125e5ae7c63f9587ac0c2e3008c94a1e8157be8a7a49f0be983e948787",
    "tests/test_frontend_responsiveness.py": "9dd5cc7cd0b1c491669e4e139c0a3a481e3a578652f3700c16569213d0b5c8a5",
    "native/scripts/prepare_release.py": "d5186bdb09657f643d0c3a5c2679dcdc288cf86a814d5bbb0e27086a8a84e21f",
}
LAUNCHER = "native/scripts/benchmark_native_library.ps1"
VALIDATOR = "native/scripts/prepare_release.py"
GALLERY_THRESHOLD_MS = 150.0
GALLERY_MAX_DRIFT_MS = 300.0
LIMIT = 160 * 1024 * 1024
ARM_PINS = set(TEST2)
GEOMETRY = ("rows", "completeOrderedIds", "lastId", "allLaidOut", "noContentVisibility",
            "viewport", "view", "density", "firstRect", "lastRect")


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def guard_bytes(path, length, sha, label):
    require(path.is_file() and path.stat().st_size == length and digest(path) == sha,
            f"{label} byte count or SHA-256 mismatch")


def load_json(path):
    require(path.is_file() and 0 < path.stat().st_size <= 4 * 1024 * 1024, "Unbounded JSON file")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], encoding="utf-8").strip()


def checkout(root, pins):
    require(git(root, "rev-parse", "--show-toplevel").replace("\\", "/").casefold()
            == root.as_posix().casefold(), "Source must be the actual worktree root")
    require(git(root, "rev-parse", "HEAD") == pins["commit"]
            and git(root, "rev-parse", "HEAD^{tree}") == pins["tree"]
            and not git(root, "status", "--porcelain", "--untracked-files=all"),
            "Source commit/tree or cleanliness mismatch")
    for path, sha in SCRIPTS.items():
        require(digest(root / path) == sha, f"Benchmark, binder or fixture changed: {path}")


def validator(root: Path):
    spec = importlib.util.spec_from_file_location("own_unchanged_release_validator", root / VALIDATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def candidate_pins(pins):
    require(set(pins) == ARM_PINS | {"artifactId", "artifactBytes", "artifactSha256"},
            "Candidate pin fields incomplete")
    for key in ("commit", "tree"):
        require(re.fullmatch(r"[0-9a-f]{40}", pins[key]), f"Invalid {key}")
    for key in ("portableSha256", "executableSha256", "artifactSha256"):
        require(re.fullmatch(r"[0-9a-f]{64}", pins[key]), f"Invalid {key}")
    for key in ("artifactBytes", "portableBytes"):
        require(type(pins[key]) is int and 0 < pins[key] <= LIMIT, f"Invalid {key}")
    require(re.fullmatch(r"\d+\.\d+\.\d+", pins["version"]) and
            re.fullmatch(re.escape(REPO) + r"/actions/runs/[0-9]+", pins["workflow"]) and
            type(pins["artifactId"]) is int and pins["artifactId"] > 0, "Invalid candidate provenance")
    require(pins["commit"] != TEST2["commit"] and pins["executableSha256"] != TEST2["executableSha256"],
            "Not a candidate with a different immutable commit and executable")


def members(z, release):
    listing = z.infolist()
    require(len(listing) <= 1000 and sum(info.file_size for info in listing) <= LIMIT, "Unbounded archive")
    require(all(not info.flag_bits & 1 and
                (info.create_system != 0 or not info.external_attr & 0x400) and
                stat.S_IFMT(info.external_attr >> 16) in (0, stat.S_IFREG, stat.S_IFDIR)
                for info in listing), "Encrypted, linked or special archive member")
    checked = release.portable_member_index(listing)
    require(z.testzip() is None, "Archive CRC failed")
    return checked


def stage(package, target, source, pins, release, outer=False):
    if outer:
        guard_bytes(package, pins["artifactBytes"], pins["artifactSha256"], "Candidate Actions outer ZIP")
        with ZipFile(package) as z:
            index = members(z, release)
            require(PORTABLE in index and index[PORTABLE].file_size == pins["portableBytes"],
                    "Expected exact, bounded root portable ZIP member")
            pkg = z.read(index[PORTABLE])
        require(hashlib.sha256(pkg).hexdigest() == pins["portableSha256"], "Candidate nested portable hash")
        port = ZipFile(io.BytesIO(pkg))
    else:
        guard_bytes(package, pins["portableBytes"], pins["portableSha256"], "Published Test 2 ZIP")
        port = ZipFile(package)
    with port as z:
        index = members(z, release)
        sums = release.portable_checksums(z, index)
        build = json.loads(z.read(index["BUILD_INFO.json"]).decode("utf-8-sig"))
        require(build == {key: pins[key] for key in ("commit", "workflow", "version")},
                "Portable BUILD_INFO mismatch")
        require(sums.get("Modhunter.exe") == pins["executableSha256"] and
                all(f"archive-tools/{name}" in sums for name in ("7z.exe", "7z.dll", "provenance.json")),
                "Portable executable archive tools identity not verified")
        require(sums.get("MO2/mo2_update_arsenal/web/index.html") ==
                digest(source / "mo2_update_arsenal/web/index.html"), "Packaged dashboard source mismatch")
        target.mkdir(parents=False, exist_ok=False)
        for name, entry in index.items():
            if entry.is_dir():
                continue
            dst = target.joinpath(*name.split("/"))
            dst.parent.mkdir(parents=True, exist_ok=True)
            with z.open(entry) as i, dst.open("xb") as o:
                shutil.copyfileobj(i, o)
            require(digest(dst) == sums[name] if name in sums else dst.read_bytes() == z.read(entry),
                    "Extraction changed packaged member")
    manifest = target / "SHA256SUMS.txt"
    return target / "Modhunter.exe", {**sums, "SHA256SUMS.txt": digest(manifest)}


def compare_environment(report):
    initial = dict(report["initial"])
    engine = dict(report["engine"])
    return {"engine": engine, "initial": initial}


def visible_geometry(report):
    def project(value):
        return {k: value[k] for k in GEOMETRY}
    shapes = {"import": project(report["importGeometry"]), "searchRestore": project(report["restoreGeometry"])}
    for name, value in report["interactions"].items():
        shapes[name] = project(value["completeLibrary"])
    return shapes


def isolation(report):
    proof = report["isolation"]
    require(proof["ok"] is True and proof["cleanupErrors"] == [] and proof["policyValuesRemoved"] == 2
            and re.fullmatch(r"[0-9a-f]{32}", proof["marker"]) and bool(proof["restoredFolders"])
            and all(x["manifestMatched"] is True for x in proof["restoredFolders"]),
            "Original native isolation/restore did not pass")
    return proof["marker"]


def classify(arms):
    require("".join(x["arm"] for x in arms) == ABLATION, "Missing or unexpected same-runner trial order")
    marks = [isolation(sample["raw"]) for sample in arms]
    require(len(set(marks)) == len(marks), "Owned browser profiles were not independent")
    env, geometry = compare_environment(arms[0]["raw"]), visible_geometry(arms[0]["raw"])
    require(all(compare_environment(x["raw"]) == env and visible_geometry(x["raw"]) == geometry for x in arms),
            "Engine, view context or complete geometry differed between arms")
    series = [x["raw"]["timingsMs"] for x in arms]
    require(all(type(t) in (int, float) and math.isfinite(t) and t > 0
                for s in series for t in s.values()), "Missing or invalid raw timings")
    deltas = {key: [series[1][key]-series[0][key], series[2][key]-series[3][key]]
              for key in series[0]}
    drift = [abs(series[0]["gallery"]-series[3]["gallery"]), abs(series[1]["gallery"]-series[2]["gallery"])]
    passes = all(x["releaseGate"] == "passed" for x in arms)
    strong = (passes and max(drift) <= GALLERY_MAX_DRIFT_MS and max(deltas["gallery"]) <= -GALLERY_THRESHOLD_MS
              and all(max(pair) <= max(30.0, min(series[0][key], series[3][key])*0.05)
                      for key, pair in deltas.items() if key != "gallery"))
    env["engine"]["executable"] = PureWindowsPath(env["engine"]["executable"]).name
    return {"schema": 1, "scope": "descriptive diagnostic only; never release proof", "order": ABLATION,
            "releaseGatePassedOnAllFour": passes, "repeatDriftMs": drift, "signedCandidateMinusBaselineMs": deltas,
            "candidateLooksFasterOnThisHostOnly": strong, "environment": env,
            "samples": [{"arm": s["arm"], "build": s["build"], "timingsMs": s["raw"]["timingsMs"],
                         "releaseGate": s["releaseGate"],
                         "checks": s["raw"]["checks"],
                         "fixtureSha256": s["raw"]["fixtureSha256"],
                         "geometry": visible_geometry(s["raw"])} for s in arms]}


def write_new(path, value):
    with path.open("x", encoding="utf-8") as f:
        json.dump(value, f, indent=2, allow_nan=False)
        f.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("a-src", "b-src", "baseline", "candidate", "pins", "out"):
        parser.add_argument("--" + arg, required=True, type=Path)
    args = parser.parse_args()
    temp = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    out = args.out.resolve()
    require(sys.platform == "win32" and os.environ.get("GITHUB_ACTIONS") == "true" and
            os.environ.get("RUNNER_OS") == "Windows" and out.parent == temp,
            "Requires a new direct output directory in a disposable Windows Actions runner")
    baseline_src, candidate_src = args.a_src.resolve(strict=True), args.b_src.resolve(strict=True)
    require(baseline_src != candidate_src and not baseline_src.is_relative_to(candidate_src)
            and not candidate_src.is_relative_to(baseline_src), "Source checkouts must be disjoint")
    pins = {"A": TEST2, "B": load_json(args.pins)}
    candidate_pins(pins["B"])
    sources = {"A": baseline_src, "B": candidate_src}
    modules = {}
    for arm in pins:
        checkout(sources[arm], pins[arm])
        modules[arm] = validator(sources[arm])
    # Neither exact arm can accidentally run on a validator changed by the proposal.
    require(digest(baseline_src / VALIDATOR) == digest(candidate_src / VALIDATOR), "Release gate source changed")
    for path in ("atelier.css", "style.css", "aether_fx.css"):
        name = Path("mo2_update_arsenal/web") / path
        require(digest(baseline_src / name) == digest(candidate_src / name), "Baseline Gallery CSS changed")
    out.mkdir(exist_ok=False)
    samples = []
    prepared = []
    try:
        # Pre-stage all four complete packs before the first sample. Distinct
        # application, browser and known-folder profiles, no install.
        for i, arm in enumerate(ABLATION, 1):
            scope = out / f"{i:02}-{arm}"
            scope.mkdir(exist_ok=False)
            source, release, pin = sources[arm], modules[arm], pins[arm]
            package = args.baseline if arm == "A" else args.candidate
            package = package.resolve(strict=True)
            require(package.is_relative_to(temp), "Input archives must be owned runner temporaries")
            exe, sums = stage(package, scope / "portable", source, pin, release, outer=arm == "B")
            prepared.append((i, arm, source, release, pin, scope, exe, sums))
        for i, arm, source, release, pin, scope, exe, sums in prepared:
            checkout(source, pin)
            log = scope / "launcher.log"
            with log.open("xb") as f:
                # Let the original launcher perform its own bounded isolation
                # cleanup. Do not kill it from an outer watchdog.
                result = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-File", str(source / LAUNCHER),
                                         "-Executable", str(exe), "-Output", str(scope / "receipt")],
                                        cwd=source, stdout=f, stderr=subprocess.STDOUT, check=False)
            require(result.returncode == 0, f"Unchanged native launcher failed at attempt {i}; stop, no retry")
            checkout(source, pin)
            files = {entry.relative_to(exe.parent).as_posix(): entry for entry in exe.parent.rglob("*")
                     if entry.is_file()}
            require(set(files) == set(sums) and all(digest(files[path]) == sha for path, sha in sums.items()),
                    "Portable package was changed or acquired an unexpected file during launch")
            report = load_json(scope / "receipt" / "native-library-performance.json")
            isolation(report)
            gate = "passed"
            try:
                release.validate_native_library(report, pin["commit"], sums["Modhunter.exe"])
            except release.ReleaseError as error:
                # A performance ceiling breach, not loss of runtime provenance,
                # remains a descriptive sample. Never suppress another failure.
                if not re.fullmatch(r"Native library performance exceeds the \w+ release ceiling: [0-9.]+ ms > [0-9]+ ms",
                                   str(error)):
                    raise
                gate = str(error)
            samples.append({"arm": arm, "build": {"commit": pin["commit"], "artifactId": pin.get("artifactId"),
                                                  "portableSha256": pin["portableSha256"],
                                                  "executableSha256": pin["executableSha256"]},
                            "raw": report, "releaseGate": gate})
        write_new(out / "comparison.json", classify(samples))
    except Exception:
        # Preserve categorical count only on security, cleanup or integrity
        # failure. Do not convert partial attempts into public comparative data.
        write_new(out / "incomplete.json", {"scope": "diagnostic incomplete", "completedAttempts": len(samples)})
        raise


if __name__ == "__main__":
    main()
