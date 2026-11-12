"""Validate one successful Actions run and stage public Modhunter release assets.

Usage:
  python native/scripts/prepare_release.py ARTIFACTS OUTPUT --commit SHA --run-url URL
  python native/scripts/prepare_release.py --self-test

This command does not publish anything. OUTPUT must be absent or empty. It
copies the tested ZIP/installer unchanged and only publishes an explicit JSON/
PNG evidence allowlist; diagnostic logs, settings and session files are omitted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import stat
import struct
import tempfile
import zlib
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

VERSION = "0.9.0"
PORTABLE_NAME = "Modhunter-Rust-Windows.zip"
VERIFICATION_NAME = f"Modhunter-{VERSION}-Verification.zip"
JSON_LIMIT = 4 * 1024 * 1024
PNG_LIMIT = 32 * 1024 * 1024
PORTABLE_EXPANDED_LIMIT = 512 * 1024 * 1024
PRODUCTION_FILES = ("plugin.py", "bridge.py", "model.py", "focus.py", "intelligence.py",
                    "plugin_intelligence.py", "mod_health.py", "source_adapters.py",
                    "source_registry.py", "update_plan.py", "quick_update.py",
                    "install_brief.py", "web/index.html")
BUNDLED_HELPER = "MO2/mo2_update_arsenal/bin/modhunter-cli.exe"
FIXTURE_PLUGIN_SHA256 = hashlib.sha256(
    struct.pack("<4sIIIIHH", b"TES4", 18, 0, 0, 0, 44, 0)
    + b"HEDR" + struct.pack("<HfII", 12, 1.7, 0, 0x800)
).hexdigest()
REQUIRED_MO2_CHECKS = {
    "native_plugin_initialized", "native_nexus_gateway_created", "native_ui_initialized",
    "native_profile_snapshot", "loopback_health", "protected_snapshot_authentication",
    "queued_refresh", "queued_focus", "native_dispatch_thread", "native_view_selection",
    "native_window_screenshot", "compact_game_response", "rust_cli_live_probe", "rust_plugin_audit",
}
FORBIDDEN_JSON_KEYS = {
    "token", "accesstoken", "refreshtoken", "idtoken", "apikey", "authorization",
    "password", "passwd", "clientsecret", "cookie", "setcookie", "xarsenaltoken",
}
SECRET_TEXT = re.compile(
    r"(?i)(?:x-arsenal-token|authorization|access_token|refresh_token|api_key|apikey|password)"
    r"[\s\"']*[:=][\s\"']*[A-Za-z0-9_+/.=\-]{12,}"
)


class ReleaseError(ValueError):
    """An artifact failed a release gate; no output is publishable."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReleaseError(message)


def regular_file(path: Path) -> Path:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ReleaseError(f"Required artifact is missing: {path}") from exc
    require(stat.S_ISREG(metadata.st_mode) and not path.is_symlink(), f"Artifact is not a regular file: {path}")
    if hasattr(metadata, "st_file_attributes"):
        require(not metadata.st_file_attributes & 0x400, f"Reparse artifact rejected: {path}")
    return path


def artifact_directory(path: Path) -> Path:
    require(path.is_dir() and not path.is_symlink(), f"Required artifact directory is missing or linked: {path}")
    return path


def bounded_bytes(path: Path, maximum: int) -> bytes:
    regular_file(path)
    require(path.stat().st_size <= maximum, f"Artifact exceeds its {maximum}-byte limit: {path.name}")
    with path.open("rb") as handle:
        content = handle.read(maximum + 1)
    require(len(content) <= maximum, f"Artifact grew beyond its read limit: {path.name}")
    return content


def json_object(content: bytes, description: str) -> dict:
    require(len(content) <= JSON_LIMIT, f"JSON proof is too large: {description}")
    try:
        value = json.loads(content.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"Invalid JSON proof: {description}") from exc
    require(isinstance(value, dict), f"Expected a JSON object: {description}")
    reject_credentials(value, description)
    return value


def read_json(path: Path) -> dict:
    return json_object(bounded_bytes(path, JSON_LIMIT), path.name)


def reject_credentials(value: object, description: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            require(normalized not in FORBIDDEN_JSON_KEYS, f"Credential field must not appear in public proof: {description}")
            reject_credentials(child, description)
    elif isinstance(value, list):
        for child in value:
            reject_credentials(child, description)
    elif isinstance(value, str):
        require(SECRET_TEXT.search(value) is None, f"Credential text must not appear in public proof: {description}")


def png_bytes(path: Path) -> bytes:
    data = bounded_bytes(path, PNG_LIMIT)
    require(data.startswith(b"\x89PNG\r\n\x1a\n"), f"Not a PNG screenshot: {path.name}")
    offset, seen_header, seen_data, seen_end = 8, False, False, False
    while offset + 12 <= len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        end = offset + 12 + length
        require(end <= len(data), f"Truncated PNG screenshot: {path.name}")
        payload = data[offset + 8:offset + 8 + length]
        checksum = struct.unpack(">I", data[offset + 8 + length:end])[0]
        require(zlib.crc32(kind + payload) & 0xFFFFFFFF == checksum, f"PNG checksum failed: {path.name}")
        if not seen_header:
            require(kind == b"IHDR" and length == 13, f"PNG has no valid image header: {path.name}")
            width, height = struct.unpack(">II", payload[:8])
            require(0 < width <= 65535 and 0 < height <= 65535, f"Invalid PNG dimensions: {path.name}")
            seen_header = True
        elif kind == b"IHDR":
            raise ReleaseError(f"PNG has duplicate image headers: {path.name}")
        if kind == b"IDAT":
            seen_data = True
        if kind == b"IEND":
            require(length == 0 and end == len(data), f"Invalid PNG end marker: {path.name}")
            seen_end = True
            break
        offset = end
    require(seen_header and seen_data and seen_end, f"Incomplete PNG screenshot: {path.name}")
    return data


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def public_json(value: dict) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def validate_run(commit: str, run_url: str) -> tuple[str, str]:
    require(re.fullmatch(r"[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?", commit) is not None, "--commit must be the full hexadecimal build commit")
    parsed = urlsplit(run_url)
    require(parsed.scheme == "https" and parsed.hostname == "github.com" and parsed.port is None
            and parsed.username is None and parsed.password is None and not parsed.query and not parsed.fragment,
            "--run-url must be a canonical HTTPS GitHub Actions run URL")
    match = re.fullmatch(r"/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/actions/runs/[0-9]+(?:/attempts/[0-9]+)?/?", parsed.path)
    require(match is not None, "--run-url must identify a GitHub Actions run")
    return commit.lower(), "https://github.com/" + match.group(1)


def validate_native_report(report: dict) -> None:
    require(report.get("ok") is True, "Windows WebView2 smoke did not pass")
    require(report.get("nativeWebView2") is True and report.get("shellIpc") is True, "Windows report does not prove native WebView2 and toolbar IPC")
    require(report.get("exportDownloadVerified") is True, "Windows exported-backup verification did not pass")
    require(report.get("gameViewNativeIpcDenied") in ("1", True), "Game-view native IPC isolation was not verified")


def validate_mo2(result: dict, receipt: dict) -> None:
    require(result.get("ok") is True and result.get("phase") == "completed", "MO2 orchestration did not complete successfully")
    require(result.get("nativeExitCode") == 0 and not isinstance(result.get("nativeExitCode"), bool), "MO2 did not exit cleanly with code zero")
    require(result.get("release", {}).get("version") == "2.5.2", "Expected evidence from the pinned official MO2 2.5.2 release")
    require(receipt.get("ok") is True and receipt.get("nativeMO2") is True, "Native MO2 receipt did not pass")
    require(result.get("nativeReceipt") == receipt, "MO2 orchestration and native receipts disagree")
    checks = receipt.get("checks")
    require(isinstance(checks, list) and bool(checks), "MO2 receipt contains no checks")
    require(all(isinstance(check, dict) and check.get("passed") is True for check in checks), "MO2 receipt contains a failed or invalid check")
    passing = {check.get("name") for check in checks}
    missing = REQUIRED_MO2_CHECKS - passing
    require(not missing, "MO2 receipt omitted required passing checks: " + ", ".join(sorted(missing)))
    audit_checks = [check for check in checks if check.get("name") == "rust_plugin_audit"]
    require(len(audit_checks) == 1, "Expected one native Rust plugin-audit receipt")
    audit = audit_checks[0]
    profile = receipt.get("profileId")
    require(isinstance(profile, str) and bool(profile) and audit.get("profileId") == profile
            and type(audit.get("schemaVersion")) is int and audit["schemaVersion"] == 1
            and audit.get("state") == "completed" and audit.get("depth") == "records",
            "Native Rust plugin audit did not complete schema 1 deep analysis for the native profile")
    fixture_mods = audit.get("fixtureMods")
    require(isinstance(fixture_mods, dict)
            and all(isinstance(fixture_mods.get(name), dict)
                    and isinstance(fixture_mods[name].get("itemId"), str) and bool(fixture_mods[name]["itemId"])
                    and type(fixture_mods[name].get("pluginCount")) is int
                    and fixture_mods[name]["pluginCount"] >= (1 if name == "Smoke Disabled" else 0)
                    for name in ("Smoke Enabled", "Smoke Disabled")),
            "Native Rust plugin audit did not inspect both exact fixture mods")
    require(fixture_mods["Smoke Enabled"]["itemId"] != fixture_mods["Smoke Disabled"]["itemId"],
            "Native Rust plugin audit repeated the same fixture mod")
    synthetic = audit.get("syntheticPlugin") or {}
    require(synthetic.get("name") == "ModhunterSmoke.esp" and synthetic.get("owner") == "Smoke Disabled"
            and synthetic.get("verified") is True and synthetic.get("bytes") == 42
            and synthetic.get("declaredRecords") == 0 and synthetic.get("sha256") == FIXTURE_PLUGIN_SHA256,
            "Native Rust plugin audit did not verify the inert fixture header and fresh SHA256")
    cli = result.get("rustCli") or {}
    helper_hash = audit.get("helperSha256")
    require(cli.get("required") is True and isinstance(helper_hash, str)
            and re.fullmatch(r"[0-9a-f]{64}", helper_hash) is not None
            and helper_hash == cli.get("sha256"),
            "Native Rust audit helper differs from the supplied compiled CLI")


def portable_contents(path: Path, commit: str, run_url: str, native_report: dict, mo2_result: dict) -> dict[str, bytes]:
    public: dict[str, bytes] = {}
    try:
        with ZipFile(path) as archive:
            entries = archive.infolist()
            require(len(entries) <= 10000 and sum(e.file_size for e in entries) <= PORTABLE_EXPANDED_LIMIT, "Portable archive exceeds the release validation bounds")
            index = {}
            for entry in entries:
                name = entry.filename.replace("\\", "/")
                parts = PurePosixPath(name).parts
                require(not name.startswith("/") and ".." not in parts and not any(":" in part for part in parts), "Unsafe path in portable archive")
                require(name not in index, f"Duplicate portable archive member: {name}")
                require(not stat.S_ISLNK(entry.external_attr >> 16), "Linked member in portable archive")
                index[name] = entry
            for name in ("BUILD_INFO.json", "Modhunter.exe", "modhunter-cli.exe", BUNDLED_HELPER, "Install_MO2_Plugin.ps1", "Install_MO2_Plugin.bat", "Evidence/native-smoke.json"):
                require(name in index and not index[name].is_dir() and index[name].file_size > 0, f"Portable package is missing {name}")
            require(archive.testzip() is None, "Portable ZIP failed its CRC check")
            build = json_object(archive.read(index["BUILD_INFO.json"]), "BUILD_INFO.json")
            require(build.get("version") == VERSION, f"Portable version must be {VERSION}")
            require(str(build.get("commit", "")).lower() == commit, "Portable BUILD_INFO commit does not match the requested build")
            require(str(build.get("workflow", "")).rstrip("/") == run_url.rstrip("/"), "Portable BUILD_INFO identifies a different workflow run")
            embedded_native = json_object(archive.read(index["Evidence/native-smoke.json"]), "portable native-smoke.json")
            require(embedded_native == native_report, "Portable native smoke differs from the Windows job receipt")
            def binary_hash(name):
                digest = hashlib.sha256()
                with archive.open(index[name]) as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                return digest.hexdigest()

            # Both files come from this portable job's build. The separate MO2
            # job binds its installed helper to its own compiled CLI in its
            # receipt; independent Windows builds need not be byte-identical.
            require(binary_hash(BUNDLED_HELPER) == binary_hash("modhunter-cli.exe"),
                    "Packaged MO2 Rust helper differs from the portable diagnostic CLI")
            source_hashes = mo2_result.get("productionFileHashes")
            require(isinstance(source_hashes, dict), "MO2 orchestration omitted tested production source hashes")
            for relative in PRODUCTION_FILES:
                name = "MO2/mo2_update_arsenal/" + relative
                require(name in index, f"Portable plugin is missing {relative}")
                expected = source_hashes.get(relative)
                require(isinstance(expected, str) and re.fullmatch(r"[0-9a-fA-F]{64}", expected) is not None,
                        f"Native MO2 proof omitted the production hash for {relative}")
                observed = hashlib.sha256(archive.read(index[name])).hexdigest()
                require(observed == expected.lower(), f"Tested MO2 source differs from the portable plugin: {relative}")
            public["BUILD_INFO.json"] = public_json(build)
            benchmark_name = "Evidence/import-comparison.json"
            if benchmark_name in index:
                benchmark = json_object(archive.read(index[benchmark_name]), "import-comparison.json")
                public["benchmarks/import-comparison.json"] = public_json(benchmark)
    except (BadZipFile, RuntimeError, OSError) as exc:
        raise ReleaseError(f"Cannot validate portable archive: {path.name}: {exc}") from exc
    return public


def release_notes(commit: str, run_url: str, repository_url: str, installer_name: str) -> str:
    return f"""# Modhunter {VERSION} — Rust desktop

The Windows desktop, MO2 profile import/cache engine, connection checks, tab lifecycle and native dialogs now run in Rust. Microsoft Edge WebView2 renders the existing dashboard and all game modes. The shipped desktop does not require Node or Electron.

MO2's Python/Qt plugin remains the in-process adapter for MO2's authenticated Nexus connection, native downloads, interactive FOMOD installer, focus and verified rewards. Dashboard/game presentation remains HTML/CSS/JavaScript.

## Download and run

- **{PORTABLE_NAME}** includes Modhunter.exe, the compiled diagnostic CLI, the MO2 plugin and its installer scripts. Extract it and run Modhunter.exe. The portable app requires Microsoft Edge WebView2 Runtime.
- **{installer_name}** installs the Rust desktop for the current Windows user and bootstraps WebView2 when needed. Use the portable ZIP's included plugin installer for live MO2 integration.
- **{VERIFICATION_NAME}** contains the selected native receipts, screenshots, build identity and available benchmark evidence. **SHA256SUMS.txt** verifies the release files.

## Controls and existing progress

Choose **Import MO2** and select a profile's modlist.txt. For split directories, select **Instance INI**, or set an explicit **Mods path**. **Auto paths** clears overrides. **Refresh** reloads metadata, reusing unchanged cached files; import remains read-only.

For live updates, close MO2, run **Install_MO2_Plugin.bat**, select the folder containing ModOrganizer.exe, then restart MO2 and open **Tools → Update Arsenal – Quest Board**. Click **Find MO2** in Modhunter; choose a session if several are running. A loopback address can also be entered in **Connect**. Nexus sign-in stays in MO2. **Update now**, background queues, retries and sequential installation retain MO2's native installation choices.

JSON, CSV and PowerShell exports are saved to Windows Downloads with unique filenames. Export a progress backup in the older Electron edition and import it in the Rust edition after selecting the same portable profile. Existing Electron browser storage is not automatically migrated; the live MO2 Chronicle remains with the plugin.

## What this build actually passed

- Rust tests, formatting and Clippy, Python MO2 lifecycle tests, and dashboard interaction checks in Chromium.
- A compiled Windows application running in real WebView2: fixture-profile import, rendered dashboard, actual backup export/readback, and denial of toolbar-native commands from the game view.
- The production plugin inside official **MO2 2.5.2**, using a disposable fixture game/profile: native initialization, real Qt dispatch, profile snapshot, protected loopback requests, row focus/selection, a native screenshot, and a compiled Rust CLI health probe against that running service. Its bundled Rust helper also completed the real plugin-audit endpoint, decorated both fixture mods, and verified the header and fresh SHA256 of an inert disabled plugin within a bounded background job.
- Release preparation verifies the commit inside the portable ZIP, matching native receipts, matching tested MO2 source hashes, complete passing required checks, and nonempty valid screenshot evidence before staging these files.

The native MO2 fixture does not sign into Nexus, download mod archives, exercise account-specific callbacks or FOMOD choices, or launch an owned game. Its synthetic game does not prove ESL eligibility, conversion safety or the behavior of a populated personal installation. The application is not code-signed.

Import benchmarks use identical generated 2,500- and 10,000-mod metadata fixtures. They distinguish engine duration, import-worker process startup, and persistent metadata-cache reuse; they do not measure full desktop startup or guarantee timing on other disks or PCs. See the [benchmark evidence]({repository_url}/blob/{commit}/native/evidence/import-comparison.json) and [reproducible comparison script]({repository_url}/blob/{commit}/scripts/benchmark_native_import.py).

**Build commit:** [{commit}]({repository_url}/commit/{commit})  
**Passing workflow and original artifacts:** [Rust desktop and MO2 readiness]({run_url})  
**Architecture and setup:** [Rust desktop guide]({repository_url}/blob/{commit}/native/README.md)
"""


def prepare(artifacts_dir: Path, output_dir: Path, commit: str, run_url: str) -> list[Path]:
    commit, repository_url = validate_run(commit, run_url)
    artifacts_dir = artifact_directory(artifacts_dir.absolute())
    output_dir = output_dir.absolute()
    require(not output_dir.is_symlink(), "Output directory must not be a symlink")
    require(not output_dir.exists() or (output_dir.is_dir() and not any(output_dir.iterdir())), "Output directory must be absent or empty; old release files are not overwritten")
    windows = artifact_directory(artifacts_dir / "Modhunter-Rust-Windows")
    mo2 = artifact_directory(artifacts_dir / "Modhunter-MO2-Native-Proof")
    browser = artifact_directory(artifacts_dir / "Modhunter-Rust-Browser-Proof")
    portable_candidates = sorted(windows.rglob("*.zip"))
    require(len(portable_candidates) == 1 and portable_candidates[0].name == PORTABLE_NAME, "Expected exactly one Modhunter portable ZIP in the Windows artifact")
    portable = regular_file(portable_candidates[0])
    installers = sorted((windows / "release/bundle/nsis").glob("*.exe"))
    require(len(installers) == 1, "Expected exactly one NSIS installer in the Windows artifact")
    installer = regular_file(installers[0])
    require(re.fullmatch(r"[A-Za-z0-9_.-]+\.exe", installer.name) is not None and installer.stat().st_size > 0,
            "NSIS installer has an invalid filename or is empty")
    native_report = read_json(windows / "native-smoke/native-smoke.json")
    validate_native_report(native_report)
    mo2_result = read_json(mo2 / "result.json")
    mo2_receipt = read_json(mo2 / "native-receipt.json")
    validate_mo2(mo2_result, mo2_receipt)
    public = portable_contents(portable, commit, run_url, native_report, mo2_result)
    public.update({"windows/native-smoke.json": public_json(native_report), "mo2/result.json": public_json(mo2_result), "mo2/native-receipt.json": public_json(mo2_receipt), "mo2/mo2-fixture.png": png_bytes(mo2 / "mo2-fixture.png")})
    screenshots = sorted(browser.rglob("*.png"))
    require(bool(screenshots), "Browser proof contains no PNG screenshots")
    require(len(screenshots) <= 128, "Unexpectedly many browser screenshots")
    for screenshot in screenshots:
        relative = screenshot.relative_to(browser)
        require(all(part not in (".", "..") and not any(c in part for c in "\\:\r\n") for part in relative.parts), "Unsafe browser screenshot path")
        public["browser/" + relative.as_posix()] = png_bytes(screenshot)
    # All proof is validated before creating final output. Build in an adjacent
    # temporary directory and publish it only after copy/checksum verification.
    expected_binary_hashes = {portable.name: file_hash(portable), installer.name: file_hash(installer)}
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".modhunter-release-", dir=output_dir.parent) as temporary:
        staged = Path(temporary)
        for source in (portable, installer):
            target = staged / source.name
            shutil.copyfile(source, target)
            require(file_hash(target) == expected_binary_hashes[source.name], f"Release copy changed the tested binary: {source.name}")
        with ZipFile(staged / VERIFICATION_NAME, "w", ZIP_DEFLATED, compresslevel=9) as archive:
            for name, content in sorted(public.items()):
                require(PurePosixPath(name).suffix.lower() in (".json", ".png"), "Unexpected non-public evidence file type")
                archive.writestr(name, content)
        (staged / "release-notes.md").write_text(release_notes(commit, run_url, repository_url, installer.name), encoding="utf-8")
        # release-notes.md becomes the hosted release description rather than an
        # uploaded asset; checksum only the binary/evidence assets users fetch.
        checksums = [f"{file_hash(path)}  {path.name}" for path in sorted(staged.iterdir()) if path.is_file() and path.suffix.lower() in (".zip", ".exe")]
        (staged / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")
        require(not output_dir.exists() or (output_dir.is_dir() and not any(output_dir.iterdir())), "Output changed during preparation; refusing to mix releases")
        if output_dir.exists():
            output_dir.rmdir()
        staged.rename(output_dir)
    return sorted(output_dir.iterdir())


def self_test() -> None:
    """Small fixture tests for release gates; these are not native smoke proof."""
    import unittest

    class ReleaseTests(unittest.TestCase):
        def setUp(self):
            self.temporary = tempfile.TemporaryDirectory(prefix="modhunter-release-tests-")
            self.addCleanup(self.temporary.cleanup)
            self.root = Path(self.temporary.name)
            self.artifacts = self.root / "input"
            self.output = self.root / "output"
            self.commit = "a" * 40
            self.run = "https://github.com/Herbertofury/Modhunter/actions/runs/123456"
            self.windows = self.artifacts / "Modhunter-Rust-Windows"
            self.mo2 = self.artifacts / "Modhunter-MO2-Native-Proof"
            self.browser = self.artifacts / "Modhunter-Rust-Browser-Proof"
            for path in (self.windows / "release/bundle/nsis", self.windows / "native-smoke", self.mo2, self.browser):
                path.mkdir(parents=True)
            self.native = {"ok": True, "nativeWebView2": True, "shellIpc": True, "exportDownloadVerified": True, "gameViewNativeIpcDenied": "1"}
            self.cli_bytes = b"non-executable synthetic test fixture"
            self.cli_hash = hashlib.sha256(self.cli_bytes).hexdigest()
            self.receipt = {"ok": True, "nativeMO2": True, "profileId": "fixture-profile",
                            "checks": [{"name": name, "passed": True} for name in sorted(REQUIRED_MO2_CHECKS)]}
            self.audit = next(check for check in self.receipt["checks"] if check["name"] == "rust_plugin_audit")
            self.audit.update(schemaVersion=1, profileId="fixture-profile", state="completed", depth="records",
                helperSha256=self.cli_hash,
                fixtureMods={"Smoke Enabled": {"itemId": "enabled", "pluginCount": 0},
                             "Smoke Disabled": {"itemId": "disabled", "pluginCount": 1}},
                syntheticPlugin={"name": "ModhunterSmoke.esp", "owner": "Smoke Disabled", "verified": True,
                                 "bytes": 42, "declaredRecords": 0, "sha256": FIXTURE_PLUGIN_SHA256})
            self.sources = {name: ("test fixture " + name).encode() for name in PRODUCTION_FILES}
            self.result = {"ok": True, "phase": "completed", "nativeExitCode": 0, "release": {"version": "2.5.2"},
                           "rustCli": {"required": True, "sha256": self.cli_hash}, "nativeReceipt": self.receipt,
                           "productionFileHashes": {name: hashlib.sha256(body).hexdigest() for name, body in self.sources.items()}}
            self.write_json(self.windows / "native-smoke/native-smoke.json", self.native)
            self.write_json(self.mo2 / "result.json", self.result)
            self.write_json(self.mo2 / "native-receipt.json", self.receipt)
            self.zip_path = self.windows / PORTABLE_NAME
            self.write_portable()
            (self.windows / "release/bundle/nsis/Modhunter_0.9.0_x64-setup.exe").write_bytes(b"synthetic installer fixture - not executable")
            image = self.png()
            (self.mo2 / "mo2-fixture.png").write_bytes(image)
            (self.browser / "review.png").write_bytes(image)
            (self.mo2 / "fixture-settings.ini").write_text("not public")
            (self.mo2 / "mo2.log").write_text("not public")
            (self.browser / "session.json").write_text('{"token":"must never be selected"}')

        @staticmethod
        def write_json(path, value):
            path.write_bytes(b"\xef\xbb\xbf" + public_json(value))

        @staticmethod
        def png():
            def chunk(kind, content):
                return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content) & 0xFFFFFFFF)
            return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\0\0\0\0\0")) + chunk(b"IEND", b"")

        def write_portable(self, commit=None, *, include_helper=True, helper_bytes=None):
            with ZipFile(self.zip_path, "w") as archive:
                archive.writestr("BUILD_INFO.json", b"\xef\xbb\xbf" + public_json({"version": VERSION, "commit": commit or self.commit, "workflow": self.run}))
                for name in ("Modhunter.exe", "modhunter-cli.exe", "Install_MO2_Plugin.ps1", "Install_MO2_Plugin.bat"):
                    archive.writestr(name, self.cli_bytes)
                if include_helper:
                    archive.writestr(BUNDLED_HELPER, self.cli_bytes if helper_bytes is None else helper_bytes)
                archive.writestr("Evidence/native-smoke.json", public_json(self.native))
                for name, body in self.sources.items():
                    archive.writestr("MO2/mo2_update_arsenal/" + name, body)

        def test_success_keeps_binaries_and_checksums_and_public_allowlist(self):
            portable_digest = file_hash(self.zip_path)
            prepare(self.artifacts, self.output, self.commit, self.run)
            self.assertEqual(file_hash(self.output / PORTABLE_NAME), portable_digest)
            for line in (self.output / "SHA256SUMS.txt").read_text().splitlines():
                digest, name = line.split("  ", 1)
                self.assertEqual(file_hash(self.output / name), digest)
            self.assertNotIn("release-notes.md", (self.output / "SHA256SUMS.txt").read_text())
            with ZipFile(self.output / VERIFICATION_NAME) as archive:
                names = archive.namelist()
                self.assertEqual(set(names), {"BUILD_INFO.json", "windows/native-smoke.json", "mo2/result.json", "mo2/native-receipt.json", "mo2/mo2-fixture.png", "browser/review.png"})
                self.assertTrue(all(PurePosixPath(name).suffix in (".json", ".png") for name in names))

        def test_missing_or_failed_proof_never_creates_output(self):
            (self.browser / "review.png").unlink()
            with self.assertRaisesRegex(ReleaseError, "no PNG"):
                prepare(self.artifacts, self.output, self.commit, self.run)
            self.assertFalse(self.output.exists())
            (self.browser / "review.png").write_bytes(self.png())
            self.native["exportDownloadVerified"] = False
            self.write_json(self.windows / "native-smoke/native-smoke.json", self.native)
            with self.assertRaisesRegex(ReleaseError, "exported-backup"):
                prepare(self.artifacts, self.output, self.commit, self.run)
            self.assertFalse(self.output.exists())

        def test_wrong_commit_stale_source_and_missing_rust_probe_fail(self):
            self.write_portable("b" * 40)
            with self.assertRaisesRegex(ReleaseError, "commit"):
                prepare(self.artifacts, self.output, self.commit, self.run)
            self.write_portable()
            self.result["productionFileHashes"]["bridge.py"] = "0" * 64
            self.write_json(self.mo2 / "result.json", self.result)
            with self.assertRaisesRegex(ReleaseError, "source differs"):
                prepare(self.artifacts, self.output, self.commit, self.run)
            self.receipt["checks"] = [c for c in self.receipt["checks"] if c["name"] != "rust_cli_live_probe"]
            self.write_json(self.mo2 / "native-receipt.json", self.receipt)
            self.write_json(self.mo2 / "result.json", self.result)
            with self.assertRaisesRegex(ReleaseError, "rust_cli_live_probe"):
                prepare(self.artifacts, self.output, self.commit, self.run)

        def test_required_deep_native_audit_identity_and_evidence(self):
            import copy
            original = copy.deepcopy(self.audit)
            for changes, error in (({"depth": "header"}, "deep analysis"),
                                   ({"profileId": "another-profile"}, "native profile"),
                                   ({"fixtureMods": {"Smoke Enabled": {"itemId": "enabled", "pluginCount": 0}}}, "both exact"),
                                   ({"syntheticPlugin": {}}, "fresh SHA256"),
                                   ({"helperSha256": "0" * 64}, "supplied compiled CLI")):
                with self.subTest(changes=changes):
                    self.audit.clear()
                    self.audit.update(original)
                    self.audit.update(changes)
                    self.write_json(self.mo2 / "native-receipt.json", self.receipt)
                    self.write_json(self.mo2 / "result.json", self.result)
                    with self.assertRaisesRegex(ReleaseError, error):
                        prepare(self.artifacts, self.output, self.commit, self.run)
                    self.assertFalse(self.output.exists())
            self.receipt["checks"] = [c for c in self.receipt["checks"] if c["name"] != "rust_plugin_audit"]
            self.write_json(self.mo2 / "native-receipt.json", self.receipt)
            self.write_json(self.mo2 / "result.json", self.result)
            with self.assertRaisesRegex(ReleaseError, "rust_plugin_audit"):
                prepare(self.artifacts, self.output, self.commit, self.run)

        def test_intelligence_and_adapter_sources_are_bound_to_native_proof(self):
            for name in ("intelligence.py", "plugin_intelligence.py", "mod_health.py",
                         "source_adapters.py", "source_registry.py", "update_plan.py",
                         "quick_update.py", "install_brief.py"):
                with self.subTest(name=name):
                    original = self.result["productionFileHashes"].pop(name)
                    self.write_json(self.mo2 / "result.json", self.result)
                    with self.assertRaisesRegex(ReleaseError, "production hash for " + re.escape(name)):
                        prepare(self.artifacts, self.output, self.commit, self.run)
                    self.result["productionFileHashes"][name] = "0" * 64
                    self.write_json(self.mo2 / "result.json", self.result)
                    with self.assertRaisesRegex(ReleaseError, "source differs.*" + re.escape(name)):
                        prepare(self.artifacts, self.output, self.commit, self.run)
                    self.result["productionFileHashes"][name] = original
                    self.assertFalse(self.output.exists())

        def test_portable_helper_is_present_and_matches_same_job_cli(self):
            self.write_portable(include_helper=False)
            with self.assertRaisesRegex(ReleaseError, "missing.*bin/modhunter-cli"):
                prepare(self.artifacts, self.output, self.commit, self.run)
            self.write_portable(helper_bytes=b"different synthetic helper build")
            with self.assertRaisesRegex(ReleaseError, "helper differs from the portable"):
                prepare(self.artifacts, self.output, self.commit, self.run)
            self.assertFalse(self.output.exists())
            # Native MO2 compiles in a different job. Only its own copied
            # helper must equal that job's CLI, not this portable job's bytes.
            other_hash = hashlib.sha256(b"separate native MO2 job fixture").hexdigest()
            self.audit["helperSha256"] = other_hash
            self.result["rustCli"]["sha256"] = other_hash
            self.write_json(self.mo2 / "native-receipt.json", self.receipt)
            self.write_json(self.mo2 / "result.json", self.result)
            self.write_portable()
            prepare(self.artifacts, self.output, self.commit, self.run)

        def test_credential_fields_and_old_output_are_rejected(self):
            self.native["token"] = "never-publish"
            self.write_json(self.windows / "native-smoke/native-smoke.json", self.native)
            with self.assertRaisesRegex(ReleaseError, "Credential field"):
                prepare(self.artifacts, self.output, self.commit, self.run)
            self.output.mkdir()
            (self.output / "old.zip").write_bytes(b"old")
            with self.assertRaisesRegex(ReleaseError, "absent or empty"):
                prepare(self.artifacts, self.output, self.commit, self.run)
            self.assertEqual((self.output / "old.zip").read_bytes(), b"old")

        def test_duplicate_binaries_and_invalid_png_are_rejected(self):
            shutil.copyfile(self.zip_path, self.windows / "other.zip")
            with self.assertRaisesRegex(ReleaseError, "exactly one"):
                prepare(self.artifacts, self.output, self.commit, self.run)
            (self.windows / "other.zip").unlink()
            (self.browser / "review.png").write_bytes(b"not a screenshot")
            with self.assertRaisesRegex(ReleaseError, "Not a PNG"):
                prepare(self.artifacts, self.output, self.commit, self.run)

    outcome = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ReleaseTests))
    if not outcome.wasSuccessful():
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts_dir", nargs="?", type=Path)
    parser.add_argument("output_dir", nargs="?", type=Path)
    parser.add_argument("--commit")
    parser.add_argument("--run-url")
    parser.add_argument("--self-test", action="store_true", help="Exercise validation using temporary synthetic artifacts; does not publish")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if args.artifacts_dir is None or args.output_dir is None or not args.commit or not args.run_url:
        parser.error("ARTIFACTS OUTPUT --commit SHA --run-url URL are required")
    try:
        assets = prepare(args.artifacts_dir, args.output_dir, args.commit, args.run_url)
    except (ReleaseError, OSError, ValueError) as exc:
        parser.exit(1, f"Release preparation failed: {exc}\n")
    print(json.dumps({"ok": True, "version": VERSION, "commit": args.commit.lower(), "assets": [path.name for path in assets]}, indent=2))


if __name__ == "__main__":
    main()
