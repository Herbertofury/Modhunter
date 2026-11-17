"""Prepare one complete, checksummed MO2 installer resource before desktop build.

The desktop embeds SHA256SUMS.txt at compile time. NSIS and the portable ZIP
must ship this directory unchanged; neither rebuilds it after testing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[2]
RESOURCE = ROOT / "native/resources/mo2-installer"
PLUGIN_PREFIX = "MO2/mo2_update_arsenal/"
ARCHIVE_FILES = ("7z.exe", "7z.dll", "License.txt", "provenance.json")
ROOT_FILES = {
    "Install_MO2_Plugin.ps1": "native/Install_MO2_Plugin.ps1",
    "Install_MO2_Plugin.bat": "native/Install_MO2_Plugin.bat",
    "PROJECT_NOTICE.txt": "native/PROJECT_NOTICE.txt",
    "THIRD_PARTY_NOTICES.json": "native/resources/THIRD_PARTY_NOTICES.json",
    "START-HERE-MO2-253.md": "wiki/MO2-253-Testing.md",
    "GameSync-Update-Techniques.md": "wiki/GameSync-Update-Techniques.md",
}


def regular_bytes(path: Path) -> bytes:
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink() or (
            ancestor.exists() and getattr(ancestor.stat(follow_symlinks=False),
                                         "st_file_attributes", 0) & 0x400
        ):
            raise ValueError(f"Linked installer input: {path}")
    details = path.stat()
    if not stat.S_ISREG(details.st_mode) or not 0 < details.st_size <= 256 * 1024 * 1024:
        raise ValueError(f"Missing, empty or oversized installer input: {path}")
    body = path.read_bytes()
    if len(body) != details.st_size:
        raise ValueError(f"Installer input changed while reading: {path}")
    return body


def source_version(root: Path) -> str:
    cargo = tomllib.loads((root / "native/Cargo.toml").read_text("utf-8"))
    version = cargo["workspace"]["package"]["version"]
    plugin = re.findall(r'''(?m)^\s*VERSION\s*=\s*["'](\d+\.\d+\.\d+)["']\s*$''',
                        (root / "mo2_update_arsenal/version.py").read_text("utf-8"))
    tauri = json.loads((root / "native/crates/modhunter-desktop/tauri.conf.json").read_text("utf-8"))
    if not re.fullmatch(r"\d+\.\d+\.\d+", version) or plugin != [version] or tauri["version"] != version:
        raise ValueError("Rust, desktop and MO2 plugin versions must match before bundling")
    return version


def tracked_plugin_files(root: Path) -> list[str]:
    names = subprocess.check_output(
        ["git", "ls-files", "-z", "--", "mo2_update_arsenal"], cwd=root
    ).decode("utf-8").split("\0")
    return sorted(name for name in names if name)


def runtime_sources(root: Path, source_files: list[str] | None = None) -> list[str]:
    selected = []
    for relative in source_files if source_files is not None else tracked_plugin_files(root):
        path = PurePosixPath(relative)
        if (len(path.parts) < 2 or path.parts[0] != "mo2_update_arsenal"
                or any(part in (".", "..", "__pycache__", "data", "bin") for part in path.parts)
                or path.suffix == ".pyc"):
            raise ValueError(f"Unexpected tracked plugin payload: {relative}")
        # The compiled index contains its web sources. Include precisely the
        # runtime files that the installer owns, so the host can verify every
        # declared MO2 payload file after installation.
        if (len(path.parts) == 2 and path.suffix == ".py") or relative == "mo2_update_arsenal/web/index.html":
            selected.append(relative)
    for required in ("__init__.py", "plugin.py", "version.py", "web/index.html"):
        if "mo2_update_arsenal/" + required not in selected:
            raise ValueError(f"The complete tracked MO2 payload is missing {required}")
    return sorted(selected)


def manifest_bytes(bodies: dict[str, bytes]) -> bytes:
    seen: set[str] = set()
    lines = []
    for name, body in sorted(bodies.items()):
        parts = PurePosixPath(name).parts
        if (not parts or name.startswith("/") or "\\" in name
                or any(part in ("", ".", "..") or re.search(r'[<>:"|?*\x00-\x1f]|[ .]$', part)
                       or re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..+)?", part, re.I)
                       for part in name.split("/"))
                or name.casefold() in seen or name == "SHA256SUMS.txt"):
            raise ValueError(f"Unsafe or duplicate installer resource name: {name}")
        seen.add(name.casefold())
        lines.append(f"{hashlib.sha256(body).hexdigest()}  {name}\n")
    return "".join(lines).encode("utf-8")


def assemble(root: Path, cli: Path, qt_bridge: Path, commit: str, run_url: str,
             source_files: list[str] | None = None) -> dict[str, bytes]:
    version = source_version(root)
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit):
        raise ValueError("Installer resources require an exact lowercase source commit")
    if not re.fullmatch(r"https://github\.com/[^/\s]+/[^/\s]+/actions/runs/[0-9]+", run_url):
        raise ValueError("Installer resources require the exact GitHub Actions run URL")
    bodies = {name: regular_bytes(root / source) for name, source in ROOT_FILES.items()}
    for relative in runtime_sources(root, source_files):
        bodies["MO2/" + relative] = regular_bytes(root / relative)
    bodies[PLUGIN_PREFIX + "bin/modhunter-cli.exe"] = regular_bytes(cli)
    bodies[PLUGIN_PREFIX + "bin/modhunter-qt-bridge.dll"] = regular_bytes(qt_bridge)
    for name in ARCHIVE_FILES:
        bodies[PLUGIN_PREFIX + "bin/archive-tools/" + name] = regular_bytes(root / "native/resources/archive-tools" / name)
    bodies["BUILD_INFO.json"] = (json.dumps({"version": version, "commit": commit, "workflow": run_url},
                                           indent=2, sort_keys=True) + "\n").encode("utf-8")
    bodies["SHA256SUMS.txt"] = manifest_bytes(bodies)
    return bodies


def write_bundle(output: Path, bodies: dict[str, bytes]) -> None:
    output = output.absolute()
    for ancestor in (output, *output.parents):
        if ancestor.is_symlink() or (ancestor.exists() and getattr(
                ancestor.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400):
            raise ValueError("Refusing a linked installer resource output")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".mo2-installer-staging-", dir=output.parent))
    backup = staging.with_name(staging.name + "-previous")
    try:
        for name, body in bodies.items():
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
        observed = {path.relative_to(staging).as_posix(): regular_bytes(path)
                    for path in staging.rglob("*") if path.is_file()}
        if observed != bodies:
            raise ValueError("Prepared installer resources changed while writing")
        if output.exists():
            output.rename(backup)
        try:
            staging.rename(output)
        except BaseException:
            if backup.exists() and not output.exists():
                backup.rename(output)
            raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        if backup.exists() and output.exists():
            shutil.rmtree(backup)


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.commit = "a" * 40
        self.run = "https://github.com/Herbertofury/Modhunter/actions/runs/123"
        fixtures = {source: "Fixture " + source for source in ROOT_FILES.values()}
        fixtures.update({"native/Cargo.toml": '[workspace.package]\nversion="9.8.7"\n',
                         "native/crates/modhunter-desktop/tauri.conf.json": '{"version":"9.8.7"}',
                         "mo2_update_arsenal/version.py": 'VERSION = "9.8.7"\n',
                         "mo2_update_arsenal/__init__.py": "# fixture\n",
                         "mo2_update_arsenal/plugin.py": "# fixture plugin\n",
                         "mo2_update_arsenal/web/index.html": "<html>Fixture</html>",
                         "mo2_update_arsenal/web/app.js": "// development source already compiled into index.html",
                         "cli.exe": "Inert CLI fixture", "qt.dll": "Inert Qt fixture"})
        fixtures.update({"native/resources/archive-tools/" + name: "Inert " + name for name in ARCHIVE_FILES})
        for name, body in fixtures.items():
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
        self.sources = sorted(name for name in fixtures if name.startswith("mo2_update_arsenal/"))

    def bundle(self):
        return assemble(self.root, self.root / "cli.exe", self.root / "qt.dll", self.commit, self.run, self.sources)

    def test_complete_reproducible_manifest_and_written_bundle(self):
        bodies = self.bundle()
        self.assertEqual(bodies, self.bundle())
        manifest = bodies.pop("SHA256SUMS.txt")
        self.assertEqual(manifest, manifest_bytes(bodies))
        self.assertEqual(json.loads(bodies["BUILD_INFO.json"])["version"], "9.8.7")
        self.assertNotIn(PLUGIN_PREFIX + "web/app.js", bodies)
        bodies["SHA256SUMS.txt"] = manifest
        output = self.root / "resources/mo2-installer"
        write_bundle(output, bodies)
        write_bundle(output, bodies)
        self.assertEqual({p.relative_to(output).as_posix(): p.read_bytes() for p in output.rglob("*") if p.is_file()}, bodies)

    def test_missing_helper_and_mixed_version_are_rejected(self):
        (self.root / "cli.exe").unlink()
        with self.assertRaises(FileNotFoundError):
            self.bundle()
        (self.root / "cli.exe").write_bytes(b"fixture")
        (self.root / "mo2_update_arsenal/version.py").write_text('VERSION = "0.0.0"\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "versions must match"):
            self.bundle()

    def test_required_source_and_identity_cannot_be_omitted(self):
        self.sources.remove("mo2_update_arsenal/plugin.py")
        with self.assertRaisesRegex(ValueError, "missing plugin.py"):
            self.bundle()
        self.commit = "main"
        with self.assertRaisesRegex(ValueError, "exact lowercase source commit"):
            self.bundle()

    def test_tracked_bytecode_and_mutable_plugin_state_are_rejected(self):
        for relative in ("mo2_update_arsenal/__pycache__/__init__.cpython-314.pyc",
                         "mo2_update_arsenal/legacy.pyc",
                         "mo2_update_arsenal/data/chronicle.json",
                         "mo2_update_arsenal/bin/unreviewed.exe"):
            with self.subTest(relative=relative), self.assertRaisesRegex(
                    ValueError, "Unexpected tracked plugin payload"):
                assemble(self.root, self.root / "cli.exe", self.root / "qt.dll",
                         self.commit, self.run, [*self.sources, relative])

    def test_linked_source_and_output_are_rejected(self):
        source = self.root / "mo2_update_arsenal/plugin.py"
        source.unlink()
        source.symlink_to(self.root / "cli.exe")
        with self.assertRaisesRegex(ValueError, "Linked installer input"):
            self.bundle()
        source.unlink()
        source.write_bytes(b"fixture")
        output = self.root / "linked-output"
        output.symlink_to(self.root / "mo2_update_arsenal", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "linked installer resource output"):
            write_bundle(output, self.bundle())

    def test_manifest_rejects_windows_collisions_and_unsafe_names(self):
        for bodies in ({"A.py": b"a", "a.py": b"b"}, {"../bad": b"a"},
                       {"folder/name.": b"a"}, {"C:/bad": b"a"}, {"folder\\bad": b"a"},
                       {"folder//bad": b"a"}, {"CON.txt": b"a"}):
            with self.subTest(bodies=bodies), self.assertRaises(ValueError):
                manifest_bytes(bodies)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", type=Path)
    parser.add_argument("--qt-bridge", type=Path)
    parser.add_argument("--commit")
    parser.add_argument("--run-url")
    parser.add_argument("--output", type=Path, default=RESOURCE)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--source-preflight", action="store_true",
                        help="Check actual tracked runtime source files without creating installer resources")
    args = parser.parse_args()
    if args.self_test:
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(BundleTests))
        raise SystemExit(0 if result.wasSuccessful() else 1)
    if args.source_preflight:
        hashes = {name: hashlib.sha256(regular_bytes(ROOT / name)).hexdigest()
                  for name in runtime_sources(ROOT)}
        print(json.dumps({"ok": True, "sourcePreflight": True, "version": source_version(ROOT),
                          "files": len(hashes), "sourceSha256": hashes}, indent=2))
        return
    if not all((args.cli, args.qt_bridge, args.commit, args.run_url)):
        parser.error("--cli, --qt-bridge, --commit and --run-url are required")
    bodies = assemble(ROOT, args.cli, args.qt_bridge, args.commit, args.run_url)
    write_bundle(args.output, bodies)
    print(json.dumps({"ok": True, "files": len(bodies), "version": source_version(ROOT),
                      "commit": args.commit, "manifestSha256": hashlib.sha256(bodies["SHA256SUMS.txt"]).hexdigest()}))


if __name__ == "__main__":
    main()
