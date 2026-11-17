"""Synthetic failure-mode tests for public bytes; no native/browser execution."""
from __future__ import annotations

import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "native/scripts"))
import verify_public_release as verifier

gates = verifier.release_gates


def png(width, height, value=0):
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))
    pixels = (b"\0" + bytes([value]) * (width * 3)) * height
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


class PublicReleaseEvidenceTests(unittest.TestCase):
    """Exercise real release validators against a stable source snapshot."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="modhunter-readback-tests-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.source = Path(cls.temporary.name)
        names = set(gates.native_sources()) | {
            "native/scripts/installer_smoke.ps1", "native/scripts/measure_native_library.cjs",
            "native/scripts/benchmark_native_library.ps1",
        }
        for spec in gates.WORKSPACE_BROWSER_SPECS.values():
            names.update(spec["sources"])
        for name in names:
            target = cls.source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(gates.ROOT / name, target)
        subprocess.run(["git", "init", "--quiet"], cwd=cls.source, check=True)
        subprocess.run(["git", "add", "."], cwd=cls.source, check=True)
        root_patch = patch.object(gates, "ROOT", cls.source)
        root_patch.start()
        cls.addClassCleanup(root_patch.stop)
        cls.source_hashes = {name: gates.file_hash(cls.source / name) for name in gates.native_sources()}
        cls.pngs = {
            "browser/" + name: png(*dimensions)
            for spec in gates.WORKSPACE_BROWSER_SPECS.values()
            for name, dimensions in spec["screenshots"].items()
        }

    def setUp(self):
        self.commit = "a" * 40
        self.executable_sha = hashlib.sha256(b"synthetic desktop, not executable").hexdigest()
        self.installer_sha = hashlib.sha256(b"synthetic installer, not executable").hexdigest()
        self.bundle = {
            "version": gates.VERSION, "commit": self.commit,
            "manifestSha256": "b" * 64,
            "resourceHashes": {"SHA256SUMS.txt": "b" * 64, "MO2/mo2_update_arsenal/bridge.py": "c" * 64},
            "pluginPayloadHashes": {"bridge.py": "c" * 64},
        }
        self.installer = {
            "schemaVersion": 1, "ok": True, "candidateCommit": self.commit, "sourceDirty": False,
            "scope": "Synthetic public-readback validator fixture; no NSIS execution",
            "installerSha256": self.installer_sha, "executableSha256": self.executable_sha,
            "installedExecutableSha256": self.executable_sha,
            "installerScriptSha256": gates.file_hash(self.source / "native/scripts/installer_smoke.ps1"),
            "checks": [{"name": name, "passed": True} for name in sorted(gates.INSTALLER_CHECKS)],
            "archiveResourceHashes": dict(gates.ARCHIVE_PINS),
            "pluginInstallerVersion": gates.VERSION, "pluginInstallerCommit": self.commit,
            "pluginInstallerManifestSha256": self.bundle["manifestSha256"],
            "pluginInstallerResourceHashes": dict(self.bundle["resourceHashes"]),
            "installedPluginPayloadHashes": dict(self.bundle["pluginPayloadHashes"]),
        }

        def geometry(view="list", density="compact"):
            return {
                "rows": gates.NATIVE_LIBRARY_COUNT, "lastId": "item-11206", "completeOrderedIds": True,
                "allLaidOut": True, "noContentVisibility": True, "view": view, "density": density,
                "viewport": {"width": 1440, "height": 900, "devicePixelRatio": 1},
                "firstRect": {"width": 900, "height": 64}, "lastRect": {"width": 900, "height": 64},
            }

        self.performance = {
            "nativeBindingSchema": 1, "candidateCommit": self.commit, "sourceDirty": False,
            "executableSha256": self.executable_sha, "sourceFiles": dict(self.source_hashes),
            "schemaVersion": 1, "ok": True, "nativeWebView2": True, "nativeProcessId": 1234,
            "phase": "complete", "errors": [],
            "scope": "Synthetic public-readback validator fixture; no Windows renderer execution",
            "scriptSha256": gates.file_hash(self.source / "native/scripts/measure_native_library.cjs"),
            "launcherSha256": gates.file_hash(self.source / "native/scripts/benchmark_native_library.ps1"),
            "fixtureSha256": gates.NATIVE_LIBRARY_FIXTURE_SHA, "fixtureBytes": gates.NATIVE_LIBRARY_FIXTURE_BYTES,
            "records": gates.NATIVE_LIBRARY_COUNT,
            "checks": [{"name": name, "passed": True} for name in sorted(gates.NATIVE_LIBRARY_CHECKS)],
            "engine": {"protocolVersion": "1.3", "product": "Chrome/150.0.0.0", "revision": "synthetic",
                       "userAgent": "Synthetic validator fixture Windows", "jsVersion": "15.0",
                       "executable": r"C:\Program Files\Microsoft\EdgeWebView\Application\150.0.0.0\msedgewebview2.exe"},
            "initial": {"url": "http://tauri.localhost/Modhunter.html", "view": "list", "density": "compact",
                        "width": 1440, "height": 900, "devicePixelRatio": 1, "visibilityState": "visible",
                        "prefersReducedMotion": False, "documentHasFocus": True},
            "importGeometry": geometry(), "restoreGeometry": geometry(),
            "timingsMs": {name: 100 for name in gates.NATIVE_LIBRARY_BUDGET_MS},
            "interactions": {
                name: {"wallMs": 100, "completeLibrary": geometry(*state),
                       "frameEndpoint": {"structuralBatchActive": False, "transitionProperty": "background", "transitionDuration": "0.12s"},
                       "metricsBefore": {key: 1 for key in gates.NATIVE_LIBRARY_METRICS},
                       "metricsAfter": {key: 2 for key in gates.NATIVE_LIBRARY_METRICS},
                       "metricDeltas": {key: 1 for key in gates.NATIVE_LIBRARY_METRICS}}
                for name, state in gates.NATIVE_LIBRARY_ACTIONS.items()
            },
            "isolation": {"schemaVersion": 1, "ok": True, "cleanupErrors": [], "policyValuesRemoved": 2,
                          "elapsedMs": 1000, "restoredFolders": [
                              {"path": r"C:\Users\runner\AppData\Roaming\com.modhunter.desktop", "originalExisted": True, "manifestMatched": True},
                              {"path": r"C:\Users\runner\AppData\Local\com.modhunter.desktop", "originalExisted": False, "manifestMatched": True}]},
        }
        self.reports = {
            "windows/installer-smoke.json": self.installer,
            "windows/native-library-performance.json": self.performance,
        }
        self.images = dict(self.pngs)
        for kind, spec in gates.WORKSPACE_BROWSER_SPECS.items():
            sources = {name: gates.file_hash(self.source / name) for name in spec["sources"]}
            self.reports["browser/" + kind + ".json"] = {
                "schema": 1, "kind": kind, "version": gates.VERSION, "ok": True,
                "commit": self.commit, "commitAtStart": self.commit, "dirty": False, "sourceUnchanged": True,
                "realChromium": True, "nativeMO2": False, "nativeWebView2": False,
                "browserIdentity": {"engine": "chromium", "version": "157.0.0.0", "userAgent": "Synthetic validator fixture HeadlessChrome/157.0.0.0"},
                "scope": "Synthetic public-readback validator fixture; no Chromium execution",
                "testsRun": len(spec["checks"]), "requiredTests": len(spec["checks"]), "failures": 0, "errors": 0, "skipped": 0,
                "checks": [{"name": name, "passed": True} for name in sorted(spec["checks"])],
                "sourceSha256": sources, "sourceSha256AtStart": dict(sources),
                "screenshots": sorted(spec["screenshots"]),
                "screenshotEvidence": {
                    name: {"sha256": hashlib.sha256(self.images["browser/" + name]).hexdigest(),
                           "width": dimensions[0], "height": dimensions[1]}
                    for name, dimensions in spec["screenshots"].items()
                },
            }

    def validate(self, installer_sha=None):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, report in self.reports.items():
                archive.writestr(name, json.dumps(report))
            for name, image in self.images.items():
                archive.writestr(name, image)
        data.seek(0)
        with zipfile.ZipFile(data) as archive:
            return verifier.validate_public_evidence(
                archive, self.commit, self.executable_sha,
                self.installer_sha if installer_sha is None else installer_sha, self.bundle)

    def test_complete_public_receipts_and_all_nineteen_pngs_pass(self):
        result = self.validate()
        self.assertEqual(result["browserChecks"], {"instances-browser": 12, "library-workspace": 8})
        self.assertEqual(len(result["screenshots"]), 19)
        self.assertEqual(set(result["screenshots"]), set(self.images))

    def test_public_installer_bytes_must_match_the_acceptance_receipt(self):
        with self.assertRaisesRegex(gates.ReleaseError, "exact tested executable"):
            self.validate(installer_sha="0" * 64)

    def test_installer_cannot_omit_or_fail_a_check(self):
        original = copy.deepcopy(self.installer["checks"])
        for replacement in (original[:-1], [{**original[0], "passed": False}] + original[1:]):
            with self.subTest(checks=len(replacement)):
                self.installer["checks"] = replacement
                with self.assertRaisesRegex(gates.ReleaseError, "omitted or failed"):
                    self.validate()

    def test_installed_plugin_payload_must_match_the_downloaded_bundle(self):
        self.installer["installedPluginPayloadHashes"]["bridge.py"] = "0" * 64
        with self.assertRaisesRegex(gates.ReleaseError, "complete verified resource"):
            self.validate()

    def test_native_performance_success_and_identity_are_mandatory(self):
        for field, value in (("ok", False), ("candidateCommit", "0" * 40),
                             ("executableSha256", "0" * 64), ("sourceDirty", True)):
            with self.subTest(field=field):
                original = self.performance[field]
                self.performance[field] = value
                with self.assertRaises(gates.ReleaseError):
                    self.validate()
                self.performance[field] = original

    def test_native_performance_source_hash_must_match(self):
        self.performance["sourceFiles"][next(iter(self.source_hashes))] = "0" * 64
        with self.assertRaisesRegex(gates.ReleaseError, "source hashes"):
            self.validate()

    def test_native_performance_requires_all_11207_records(self):
        self.performance["records"] = 11206
        with self.assertRaisesRegex(gates.ReleaseError, "11,207-record fixture"):
            self.validate()

    def test_native_performance_rejects_hidden_records_in_returned_list(self):
        self.performance["interactions"]["list"]["completeLibrary"]["allLaidOut"] = False
        with self.assertRaisesRegex(gates.ReleaseError, "dropped, hid or reordered"):
            self.validate()

    def test_every_original_native_timing_budget_is_enforced(self):
        for name, ceiling in gates.NATIVE_LIBRARY_BUDGET_MS.items():
            with self.subTest(interaction=name, ceiling=ceiling):
                self.performance["timingsMs"][name] = ceiling + 1
                if name in self.performance["interactions"]:
                    self.performance["interactions"][name]["wallMs"] = ceiling + 1
                with self.assertRaisesRegex(gates.ReleaseError, name + " release ceiling"):
                    self.validate()
                self.performance["timingsMs"][name] = 100
                if name in self.performance["interactions"]:
                    self.performance["interactions"][name]["wallMs"] = 100

    def test_every_required_browser_png_must_be_present(self):
        for name in list(self.images):
            with self.subTest(capture=name):
                data = self.images.pop(name)
                with self.assertRaises(KeyError):
                    self.validate()
                self.images[name] = data

    def test_corrupt_browser_png_fails_even_with_matching_report_hash(self):
        name = next(iter(self.images))
        broken = bytearray(self.images[name])
        broken[-1] ^= 1
        self.images[name] = bytes(broken)
        self.capture(name)["sha256"] = hashlib.sha256(self.images[name]).hexdigest()
        with self.assertRaisesRegex(gates.ReleaseError, "PNG.*CRC|PNG.*checksum|CRC"):
            self.validate()

    def capture(self, name):
        for path, report in self.reports.items():
            if path.startswith("browser/") and name.removeprefix("browser/") in report["screenshotEvidence"]:
                return report["screenshotEvidence"][name.removeprefix("browser/")]
        self.fail("Unknown screenshot in synthetic fixture")

    def test_valid_replaced_png_must_match_its_receipt_digest(self):
        name = next(iter(self.images))
        capture = self.capture(name)
        self.images[name] = png(capture["width"], capture["height"], value=1)
        with self.assertRaisesRegex(gates.ReleaseError, "screenshot bytes differ"):
            self.validate()

    def test_wrong_actual_png_dimensions_fail_even_with_matching_digest(self):
        name = next(iter(self.images))
        capture = self.capture(name)
        self.images[name] = png(capture["width"] - 1, capture["height"])
        capture["sha256"] = hashlib.sha256(self.images[name]).hexdigest()
        with self.assertRaisesRegex(gates.ReleaseError, "dimensions"):
            self.validate()

    def test_browser_receipts_require_named_checks_no_skips_and_source_binding(self):
        for kind in gates.WORKSPACE_BROWSER_SPECS:
            path = "browser/" + kind + ".json"
            original = self.reports[path]
            mutations = [
                {"skipped": 1}, {"checks": original["checks"][:-1]}, {"commit": "0" * 40},
                {"sourceSha256": {name: "0" * 64 for name in original["sourceSha256"]}},
            ]
            for changes in mutations:
                with self.subTest(kind=kind, changes=list(changes)):
                    self.reports[path] = {**original, **changes}
                    with self.assertRaises(gates.ReleaseError):
                        self.validate()
            self.reports[path] = original


if __name__ == "__main__":
    unittest.main()
