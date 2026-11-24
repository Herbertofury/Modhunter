"""Build a local MO2-only deployment from separately verified component bytes.

The public release pipeline continues to require its full validated desktop ZIP.
Use a checkout with the product sources bound by the supplied receipts. Run
verify_archive_review.py and verify_qt_forward.py for the current product
commit before invoking this command. The native MO2 fixture may come from a
different build with identical shipped Python and dashboard files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tomllib
from pathlib import Path
from tempfile import TemporaryDirectory

from prepare_archive_tools import BASE, PACKAGES, VERSION as ARCHIVE_VERSION
from prepare_release import (
    ARCHIVE_PINS, PRODUCTION_FILES, ROOT, component_archive, require, source_version,
    validate_archive_report, validate_mo2, validate_qt_forward,
)


def json_file(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def prepare(args: argparse.Namespace) -> None:
    version = source_version()
    archive = json_file(args.archive_receipt)
    commit = archive["candidateCommit"]
    # A packaging-only change can be cherry-picked after the product was built.
    # Verify both the product tree and worktree against the reported commit.
    sources = ["mo2_update_arsenal", "native/crates/modhunter-core", "native/Cargo.lock",
               "native/Cargo.toml", "native/qt_bridge", "native/Install_MO2_Plugin.ps1",
               "native/Install_MO2_Plugin.bat"]
    resolved = subprocess.check_output(["git", "rev-parse", "--verify", "HEAD"], cwd=ROOT, text=True).strip()
    require(isinstance(commit, str) and len(commit) == 40 and all(c in "0123456789abcdef" for c in commit),
            "Product commit must be an exact Git SHA")
    subprocess.run(["git", "diff", "--quiet", commit, resolved, "--", *sources], cwd=ROOT, check=True)
    subprocess.run(["git", "diff", "--quiet", commit, "--", *sources], cwd=ROOT, check=True)
    cli = args.cli.read_bytes()
    qt = args.qt_bridge.read_bytes()
    validate_archive_report(archive, commit, sha(cli))
    validate_qt_forward(json_file(args.qt_receipt), commit, sha(qt))
    native = json_file(args.mo2_result)
    validate_mo2(native, json_file(args.mo2_receipt))
    native_files = native["productionFileHashes"]
    require(set(native_files) == set(PRODUCTION_FILES), "Native MO2 evidence omits a production source file")
    prefix = "MO2/mo2_update_arsenal/"
    bodies = {}
    for name in PRODUCTION_FILES:
        body = (ROOT / "mo2_update_arsenal" / name).read_bytes()
        require(sha(body) == native_files[name], "Shipped MO2 bytes differ from native fixture: " + name)
        bodies[prefix + name] = body
    bodies[prefix + "bin/modhunter-cli.exe"] = cli
    bodies[prefix + "bin/modhunter-qt-bridge.dll"] = qt

    archive_dir = args.cli.parent / "archive-tools"
    provenance = json_file(archive_dir / "provenance.json")
    require(provenance.get("version") == ARCHIVE_VERSION and provenance.get("files") == ARCHIVE_PINS and
            provenance.get("source") == BASE and provenance.get("packages") ==
            {key: {"url": BASE + name, "sha256": digest} for key, (name, digest) in PACKAGES.items()},
            "Archive provenance differs from the pinned packages")
    for name in (*ARCHIVE_PINS, "License.txt", "provenance.json"):
        body = (archive_dir / name).read_bytes()
        if name in ARCHIVE_PINS or name == "License.txt":
            require(sha(body) == ARCHIVE_PINS.get(name, provenance["licenseSha256"]),
                    "Archive component digest mismatch: " + name)
        bodies[prefix + "bin/archive-tools/" + name] = body
    for name, path in {
        "Install_MO2_Plugin.ps1": "native/Install_MO2_Plugin.ps1",
        "Install_MO2_Plugin.bat": "native/Install_MO2_Plugin.bat",
        "Check_MO2_Readiness.ps1": "native/Check_MO2_Readiness.ps1",
        "PROJECT_NOTICE.txt": "native/PROJECT_NOTICE.txt",
        "START-HERE-MO2-253.md": "wiki/MO2-253-Testing.md",
        "GameSync-Update-Techniques.md": "wiki/GameSync-Update-Techniques.md",
    }.items():
        bodies[name] = (ROOT / path).read_bytes()
    notices = args.notices.read_bytes()
    license_data = json.loads(notices)
    locked = tomllib.loads((ROOT / "native/Cargo.lock").read_text(encoding="utf-8"))
    expected = {(row["name"], row["version"]) for row in locked["package"] if row.get("source")}
    packages = license_data.get("packages", [])
    require(license_data.get("schema") == 1 and len(packages) == len(expected)
            and {(row["name"], row["version"]) for row in packages} == expected,
            "Generate third-party notices with prepare_notices.py for the tested Cargo.lock")
    bodies["THIRD_PARTY_NOTICES.json"] = notices
    build = {
        "component": "mo2-plugin", "distribution": "local", "version": version, "commit": commit,
        "cliSha256": sha(cli), "qtBridgeSha256": sha(qt),
        "nativeFixtureCliSha256": native["rustCli"]["sha256"],
        "nativeFixtureQtBridgeSha256": native["qtBridge"]["sha256"],
        "nativeFixtureMo2Version": native["release"]["version"],
        "nativeFixtureProductionFiles": len(native_files),
    }
    bodies["BUILD_INFO.json"] = (json.dumps(build, indent=2, sort_keys=True) + "\n").encode()
    bodies["START-HERE.txt"] = (
        f"Modhunter {version} local MO2 plugin\n\n"
        "Extract the complete ZIP into a new folder. Close Mod Organizer. Run Install_MO2_Plugin.bat "
        "and choose the folder containing ModOrganizer.exe. The installer verifies each plugin file.\n\n"
        "To check this package and installed files from Windows PowerShell, run\n"
        '.\\Check_MO2_Readiness.ps1 -PluginOnly -MO2Directory "C:\\path\\to\\MO2" -Json\n\n'
        "The readiness check reads file versions and hashes. BUILD_INFO.json records the packaged helpers "
        "and the helper hashes used in the separate native MO2 fixture. SHA256SUMS.txt lists every other "
        "archive member. Keep the accompanying qualification receipt for the tested runtime scope.\n"
    ).encode()
    require(not args.output.exists(), "Choose a new output file")
    with TemporaryDirectory(dir=args.output.parent) as tmp:
        candidate = Path(tmp) / "plugin.zip"
        component_archive(candidate, bodies)
        # Link a complete checked archive atomically without replacing another run.
        args.output.hardlink_to(candidate)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("cli", "qt-bridge", "archive-receipt", "qt-receipt", "mo2-result", "mo2-receipt", "notices", "output"):
        parser.add_argument("--" + option, type=Path, required=True)
    prepare(parser.parse_args())
