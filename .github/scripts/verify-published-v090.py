"""Verify the four published assets, then update only the reviewed release body.

This isolated publication workflow never executes downloaded code, extracts
archives, changes application source, moves tags, or uploads/replaces assets.
"""
from __future__ import annotations

import hashlib
import json
import re
import runpy
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

REPO = "Herbertofury/Modhunter"
TAG = "v0.9.0"
COMMIT = "11515c27b0c33b788d7fb9ba15fe1692f337e14b"
BUILD_RUN = "https://github.com/Herbertofury/Modhunter/actions/runs/43272384505"
PORTABLE = "Modhunter-Rust-Windows.zip"
EVIDENCE = "Modhunter-0.9.0-Verification.zip"
INSTALLER = "Modhunter_0.9.0_x64-setup.exe"
EXPECTED_ASSETS = {PORTABLE, EVIDENCE, INSTALLER, "SHA256SUMS.txt"}
FEATURES = """## New mod-maintenance features

- **Rust source adapters:** GitHub, GitLab, Modrinth and Thunderstore release metadata alongside MO2's native Nexus connection.
- **Exact update review:** inspect the selected release and file with freshness, dependency and compatibility context. Rich source details load when opened; newly observed asset changes invalidate prior review.
- **Plugin and ESL insight on each mod:** see winning light/full plugin state, flag-only candidates, compaction-review requirements and unknown results, plus stale ESLifier output and master issues.
- **Observed Nexus health:** display captured bug counts and bug-tab states with their evidence and freshness. Unavailable evidence stays unknown.

See the versioned [source-coverage guide](https://github.com/Herbertofury/Modhunter/blob/v0.9.0/wiki/Updater-Source-Coverage.md) and [mod-intelligence guide](https://github.com/Herbertofury/Modhunter/blob/v0.9.0/wiki/Mod-Intelligence.md).

"""


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def gh(*arguments):
    result = subprocess.run(
        ["gh", *arguments], capture_output=True, text=True,
        encoding="utf-8", timeout=180, check=False,
    )
    require(result.returncode == 0, "A GitHub CLI publication operation failed")
    return result.stdout


def api(path):
    return json.loads(gh("api", path))


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def hash_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def asset_identity(release):
    return sorted(
        (asset["id"], asset["name"], asset["size"], asset["digest"],
         asset["state"], asset["browser_download_url"])
        for asset in release["assets"]
    )


def validate_release(release):
    require(release.get("tag_name") == TAG, "Unexpected release tag")
    require(release.get("target_commitish") == COMMIT, "Release targets another commit")
    require(release.get("draft") is False and release.get("prerelease") is False,
            "Expected the published stable release")
    require(bool(release.get("published_at")) and isinstance(release.get("body"), str),
            "Release publication or description is missing")
    assets = release.get("assets", [])
    require(len(assets) == len(EXPECTED_ASSETS)
            and {asset.get("name") for asset in assets} == EXPECTED_ASSETS,
            "Published asset names differ from the reviewed four-file release")
    for asset in assets:
        require(asset.get("state") == "uploaded" and 0 < asset.get("size", 0) <= 64 * 1024 * 1024,
                "Unexpected asset state or size")
        require(re.fullmatch(r"sha256:[0-9a-f]{64}", asset.get("digest") or "") is not None,
                "GitHub omitted a published asset SHA256")
        require(asset.get("browser_download_url") ==
                f"https://github.com/{REPO}/releases/download/{TAG}/{asset['name']}",
                "An asset download points outside the expected release")


def validate_tag():
    require(api(f"repos/{REPO}/commits/{TAG}").get("sha") == COMMIT,
            "The published tag no longer resolves to the tested main commit")


def manifest(data):
    entries = {}
    for line in data.decode("utf-8-sig").splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"([0-9a-fA-F]{64})  (.+)", line)
        require(match is not None, "Malformed checksum manifest")
        digest, name = match.groups()
        require(name not in entries, "Duplicate checksum-manifest entry")
        entries[name] = digest.lower()
    return entries


def zip_index(archive):
    entries = archive.infolist()
    require(len(entries) <= 10000 and sum(item.file_size for item in entries) <= 512 * 1024 * 1024,
            "Archive exceeds verification limits")
    index = {}
    for item in entries:
        name = item.filename.replace("\\", "/")
        parts = PurePosixPath(name).parts
        require(not name.startswith("/") and ".." not in parts and
                not any(":" in part for part in parts), "Unsafe archive member")
        require(name not in index and not stat.S_ISLNK(item.external_attr >> 16),
                "Duplicate or linked archive member")
        index[name] = item
    require(archive.testzip() is None, "Published archive CRC check failed")
    return index


def read_json(archive, index, name):
    require(name in index and index[name].file_size <= 4 * 1024 * 1024,
            f"Missing or oversized proof: {name}")
    return json.loads(archive.read(index[name]).decode("utf-8-sig"))


def main():
    before = api(f"repos/{REPO}/releases/tags/{TAG}")
    validate_release(before)
    validate_tag()
    original_body = before["body"]
    original_assets = asset_identity(before)
    directory = Path("published-files")
    directory.mkdir()
    verified_assets = []
    for asset in sorted(before["assets"], key=lambda item: item["name"]):
        gh("release", "download", TAG, "--repo", REPO,
           "--pattern", asset["name"], "--dir", str(directory))
        path = directory / asset["name"]
        require(path.is_file() and not path.is_symlink()
                and path.stat().st_size == asset["size"], "Downloaded asset size differs")
        digest = hash_file(path)
        require("sha256:" + digest == asset["digest"], "Published bytes differ from GitHub's digest")
        verified_assets.append({"name": asset["name"], "bytes": asset["size"],
                                "sha256": digest, "assetId": asset["id"]})
    checksums = manifest((directory / "SHA256SUMS.txt").read_bytes())
    require(set(checksums) == EXPECTED_ASSETS - {"SHA256SUMS.txt"},
            "Release checksum manifest has unexpected membership")
    for name, digest in checksums.items():
        require(hash_file(directory / name) == digest, f"Release checksum mismatch: {name}")

    validator = runpy.run_path("native/scripts/prepare_release.py",
                              run_name="modhunter_published_release_validator")
    with ZipFile(directory / PORTABLE) as portable, ZipFile(directory / EVIDENCE) as evidence:
        package = zip_index(portable)
        proof = zip_index(evidence)
        build = read_json(portable, package, "BUILD_INFO.json")
        require(build.get("version") == "0.9.0" and build.get("commit") == COMMIT
                and build.get("workflow") == BUILD_RUN, "Portable build identity differs")
        require(read_json(evidence, proof, "BUILD_INFO.json") == build,
                "Verification archive identifies a different build")
        native = read_json(evidence, proof, "windows/native-smoke.json")
        mo2 = read_json(evidence, proof, "mo2/result.json")
        receipt = read_json(evidence, proof, "mo2/native-receipt.json")
        validator["validate_native_report"](native)
        validator["validate_mo2"](mo2, receipt)
        require(read_json(portable, package, "Evidence/native-smoke.json") == native,
                "Portable and published Windows proof differ")
        internal = manifest(portable.read(package["SHA256SUMS.txt"]))
        files = {name for name, item in package.items() if not item.is_dir()}
        require(set(internal) == files - {"SHA256SUMS.txt"},
                "Portable checksum manifest has unexpected membership")
        for name, digest in internal.items():
            require(sha256(portable.read(package[name])) == digest,
                    f"Portable member checksum mismatch: {name}")
        require(sha256(portable.read(package["modhunter-cli.exe"])) ==
                sha256(portable.read(package[validator["BUNDLED_HELPER"]])),
                "Portable and MO2 Rust helpers differ")
        require(validator["BUNDLED_QT_HELPER"] in package
                and package[validator["BUNDLED_QT_HELPER"]].file_size > 0,
                "The Qt interoperability helper is missing")
        require(not any(PurePosixPath(name).name.lower().startswith("qt6")
                        and name.lower().endswith(".dll") for name in package),
                "An SDK Qt runtime was unexpectedly shipped")
        for relative in validator["PRODUCTION_FILES"]:
            name = "MO2/mo2_update_arsenal/" + relative
            require(name in package and sha256(portable.read(package[name])) ==
                    mo2["productionFileHashes"].get(relative),
                    f"Published source differs from actual MO2 proof: {relative}")
        screenshots = [name for name in proof if name.endswith(".png")]
        require(bool(screenshots), "Published visual proof is missing")
        for name in screenshots:
            require(evidence.read(proof[name]).startswith(b"\x89PNG\r\n\x1a\n"),
                    "Published visual proof is not PNG data")
        portable_count = len(files)

    marker = "\n## Download and run\n"
    if FEATURES in original_body:
        updated_body = original_body
    else:
        require("## New mod-maintenance features" not in original_body,
                "A different feature section needs review before editing")
        require(original_body.count(marker) == 1, "Release description layout changed")
        updated_body = original_body.replace(marker, "\n" + FEATURES + "## Download and run\n", 1)
        require(updated_body.replace("\n" + FEATURES, "\n", 1) == original_body,
                "The metadata edit must preserve every original description byte")
    Path("release-body-original.md").write_text(original_body, encoding="utf-8")
    Path("release-body-reviewed.md").write_text(updated_body, encoding="utf-8")

    # Re-read immediately before the body-only edit and stop on concurrent edits.
    current = api(f"repos/{REPO}/releases/tags/{TAG}")
    validate_release(current)
    require(current["id"] == before["id"] and current["body"] == original_body
            and asset_identity(current) == original_assets,
            "Release metadata or assets changed concurrently; no edit was made")
    validate_tag()
    if updated_body != original_body:
        gh("release", "edit", TAG, "--repo", REPO, "--notes-file", "release-body-reviewed.md")
    after = api(f"repos/{REPO}/releases/tags/{TAG}")
    validate_release(after)
    validate_tag()
    require(after["id"] == before["id"] and after["body"] == updated_body
            and asset_identity(after) == original_assets,
            "Post-edit verification found changed assets or unexpected metadata")
    result = {
        "schemaVersion": 1, "ok": True,
        "observedAt": datetime.now(timezone.utc).isoformat(),
        "release": after["html_url"], "tag": TAG, "commit": COMMIT,
        "buildRun": BUILD_RUN, "assets": verified_assets,
        "releaseManifestVerified": True, "portableManifestVerified": True,
        "portableFilesVerified": portable_count,
        "productionSourceProofFilesVerified": len(validator["PRODUCTION_FILES"]),
        "nativeExportVerified": native["exportDownloadVerified"],
        "nativeMO2ChecksPassed": len(receipt["checks"]),
        "visualProofFiles": len(screenshots),
        "releaseBodyUpdated": updated_body != original_body,
        "originalBodySha256": sha256(original_body.encode()),
        "reviewedBodySha256": sha256(updated_body.encode()),
        "assetsUnchangedByMetadataEdit": True,
        "downloadedCodeExecuted": False,
    }
    Path("published-verification.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
