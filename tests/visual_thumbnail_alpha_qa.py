"""Run the existing 14 Gallery cases against the isolated transparent-cover fix.

This is a diagnostic receipt, not native Windows or release acceptance. The
standard Gallery test class owns all browser behavior; this runner records its
actual engine, results, source binding and six retained screenshots. It adds no
test cases, runtime style overrides, image normalization or missing-result caps.
"""
from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import os
import shutil
import subprocess
import sys
import time
import traceback
import unittest

from PIL import Image

import ci_font_rendering_qa as fonts
import test_gallery_workspace as gallery
import visual_release_qa as visual

ROOT = Path(__file__).resolve().parents[1]
FOUNDATION = "7b2d734e5cac762c25a987f8b2d0bb24fae655e1"
FOUNDATION_TREE = "bbe5a45c81b011df8d7e8bf946de5beb1362228e"
EXPECTED_TEST_SHA = "3e9d2c5a9f9684df829028b9eb426b90c96adb510eeb305505128022ba37de5a"
EXPECTED_CSS_SHA = "bbea22036d4a84158264aa723ae25b956b78b4edabeecc0dfef8d02612c008bc"
CHANGED_FILES = {
    "mo2_update_arsenal/web/atelier.css", "tests/test_gallery_workspace.py",
    "tests/visual_thumbnail_alpha_qa.py", ".github/workflows/atelier-style-profile.yml",
}
SOURCES = tuple(sorted(CHANGED_FILES | {
    "tests/test_frontend_responsiveness.py", "tests/visual_release_qa.py",
    "tests/ci_font_rendering_qa.py", "tests/prepare_ci_fonts.py",
    "tests/fixtures/visual_public_catalog.json", "tests/fixtures/visual_thumbnails/iris.webp",
}))
SCREENSHOTS = {
    "thumbnail-alpha-library-1440-night.png": (1440, True),
    "thumbnail-alpha-inspector-1440-night.png": (1440, False),
    "thumbnail-alpha-library-390-snow.png": (390, True),
    "thumbnail-alpha-inspector-390-snow.png": (390, False),
    "thumbnail-failed-library-390-snow.png": (390, True),
    "thumbnail-failed-inspector-390-snow.png": (390, False),
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def test_ids(suite) -> list[str]:
    result = []
    for test in suite:
        result.extend(test_ids(test) if isinstance(test, unittest.TestSuite) else [test.id()])
    return result


def screenshot_inventory(output: Path) -> dict:
    result = {}
    for name, (expected_width, full_page) in SCREENSHOTS.items():
        path = output / name
        if not path.is_file():
            result[name] = {"present": False}
            continue
        data = path.read_bytes()
        with Image.open(path) as image:
            assert image.format == "PNG", name
            dimensions = image.size
            image.verify()
        assert dimensions[0] == expected_width, (name, dimensions)
        assert dimensions[1] >= 900 if full_page else dimensions[1] == 900, (name, dimensions)
        result[name] = {"present": True, "bytes": len(data), "sha256": sha(data),
                        "width": dimensions[0], "height": dimensions[1], "fullPage": full_page,
                        "pngVerified": True, "capture": "Actual unmodified browser screenshot"}
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--executable-path", type=Path, required=True)
    parser.add_argument("--launcher-path", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    receipt_path = args.output / "thumbnail-alpha-qa.json"
    assert not receipt_path.exists(), "Choose a new output directory; do not overwrite diagnostic evidence"
    report = {"schema": 1, "kind": "transparent-thumbnail-browser-diagnostic", "ok": False,
              "scopeAcceptance": False, "scope": __doc__, "phase": "source-and-font-verification",
              "errors": [], "cases": [], "foundationCommit": FOUNDATION, "foundationTree": FOUNDATION_TREE,
              "workingCommit": git("rev-parse", "HEAD"), "sourceStatusAtStart": fonts.source_status(),
              "expectedActualExecutable": str(args.executable_path.resolve()),
              "requestedLauncher": str(args.launcher_path.resolve()),
              "packages": {name: version(name) for name in ("playwright", "Pillow")}}

    def save():
        temporary = receipt_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(report, indent=2) + "\n")
        temporary.replace(receipt_path)

    save()
    started = time.monotonic()
    try:
        assert os.environ.get("MODHUNTER_REQUIRE_BROWSER") == "1"
        assert Path(os.environ["MODHUNTER_QA_OUTPUT"]).resolve() == args.output.resolve()
        assert report["packages"] == {"playwright": "1.57.0", "Pillow": "13.0.0"}
        assert args.executable_path.is_file() and args.launcher_path.is_file()
        selected = shutil.which("chromium") or shutil.which("chromium-browser")
        # The existing test harness launches the discovered command. On this CI
        # image it is a regular shell wrapper, not the browser ELF executable.
        assert selected and Path(selected).resolve() == args.launcher_path.resolve()
        report["discoveredLauncher"] = selected
        report["launcherSha256"] = sha(args.launcher_path.read_bytes())
        assert not report["sourceStatusAtStart"], "Committed clean source is required"
        assert git("rev-parse", FOUNDATION + "^{tree}") == FOUNDATION_TREE
        report["changedFiles"] = git("diff", "--name-only", FOUNDATION, "HEAD", "--").splitlines()
        assert set(report["changedFiles"]) == CHANGED_FILES, report["changedFiles"]
        report["sourceFiles"] = {name: sha((ROOT / name).read_bytes()) for name in SOURCES}
        assert report["sourceFiles"]["tests/test_gallery_workspace.py"] == EXPECTED_TEST_SHA
        assert report["sourceFiles"]["mo2_update_arsenal/web/atelier.css"] == EXPECTED_CSS_SHA
        before_html, before_assets = visual.compile_dashboard(FOUNDATION)
        after_html, after_assets = visual.compile_dashboard()
        report["compiledAssets"] = {"foundation": before_assets, "candidate": after_assets}
        report["changedCompiledAssets"] = [name for name in before_assets if before_assets[name] != after_assets[name]]
        assert report["changedCompiledAssets"] == ["mo2_update_arsenal/web/atelier.css"]
        report["compiledHtmlSha256"] = {"foundation": sha(before_html.encode()), "candidate": sha(after_html.encode())}
        report["testedHtmlSha256"] = sha(gallery.harness.build_page().encode())
        report["testCompilationScope"] = "Existing responsiveness harness compiles the same dashboard sources with its established empty PowerShell fixture and unminified catalog. Compiled release-source asset hashes are recorded separately."
        _, report["fontProof"] = fonts.verify_font_receipt(args.output / "ci-fonts.json")
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(gallery.GalleryWorkspaceTests)
        expected_ids = test_ids(suite)
        assert len(expected_ids) == 14 and len(set(expected_ids)) == 14, expected_ids
        report["expectedTests"] = expected_ids
        report["phase"] = "running-existing-gallery-suite"
        save()

        class RecordedResult(unittest.TextTestResult):
            def startTest(self, test):
                super().startTest(test)
                if "engine" not in report:
                    browser = gallery.GalleryWorkspaceTests.browser
                    session = browser.new_browser_cdp_session()
                    try:
                        arguments = session.send("Browser.getBrowserCommandLine")["arguments"]
                        report["engine"] = {"version": browser.version,
                            "protocol": session.send("Browser.getVersion"), "arguments": arguments,
                            "resolvedExecutable": str(Path(arguments[0]).resolve())}
                    finally:
                        session.detach()
                    assert report["engine"]["resolvedExecutable"] == report["expectedActualExecutable"]
                    assert sha(gallery.GalleryWorkspaceTests.html.encode()) == report["testedHtmlSha256"]
                self.case_started = time.monotonic()
                self.case = {"id": test.id(), "status": "running"}
                report["cases"].append(self.case)
                save()

            def addSuccess(self, test):
                super().addSuccess(test)
                self.case["status"] = "passed"

            def addFailure(self, test, err):
                super().addFailure(test, err)
                self.case.update(status="failed", error=self.failures[-1][1])

            def addError(self, test, err):
                super().addError(test, err)
                if getattr(self, "case", None) and self.case["id"] == test.id():
                    self.case.update(status="error", error=self.errors[-1][1])

            def addSkip(self, test, reason):
                super().addSkip(test, reason)
                if getattr(self, "case", None) and self.case["id"] == test.id():
                    self.case.update(status="skipped", reason=reason)

            def stopTest(self, test):
                self.case["seconds"] = time.monotonic() - self.case_started
                super().stopTest(test)
                save()

        result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2, resultclass=RecordedResult).run(suite)
        report["testResult"] = {"testsRun": result.testsRun, "passed": sum(case["status"] == "passed" for case in report["cases"]),
            "failures": [{"id": test.id(), "error": error} for test, error in result.failures],
            "errors": [{"id": test.id(), "error": error} for test, error in result.errors],
            "skipped": [{"id": test.id(), "reason": reason} for test, reason in result.skipped],
            "expectedFailures": len(result.expectedFailures), "unexpectedSuccesses": len(result.unexpectedSuccesses)}
        report["screenshots"] = screenshot_inventory(args.output)
        assert result.wasSuccessful() and result.testsRun == 14 and not result.skipped
        assert not result.expectedFailures and not result.unexpectedSuccesses
        assert [case["id"] for case in report["cases"]] == expected_ids
        assert all(case["status"] == "passed" for case in report["cases"])
        assert all(value["present"] for value in report["screenshots"].values())
        report["sourceStatusAtEnd"] = fonts.source_status()
        report["workingCommitAtEnd"] = git("rev-parse", "HEAD")
        assert not report["sourceStatusAtEnd"] and report["workingCommitAtEnd"] == report["workingCommit"]
        assert all(sha((ROOT / name).read_bytes()) == digest for name, digest in report["sourceFiles"].items())
        assert visual.compile_dashboard()[1] == after_assets
        assert sha(gallery.harness.build_page().encode()) == report["testedHtmlSha256"]
        report.update(ok=True, phase="complete", sourceDirty=False)
    except Exception:
        report["errors"].append(traceback.format_exc())
    finally:
        browser = getattr(gallery.GalleryWorkspaceTests, "browser", None)
        if browser is not None and browser.is_connected():
            browser.close()
            gallery.harness.FrontendResponsivenessTests.runtime.stop()
        report["elapsedSeconds"] = time.monotonic() - started
        report["sourceStatusAtEnd"] = fonts.source_status()
        report["workingCommitAtEnd"] = git("rev-parse", "HEAD")
        report["sourceDirty"] = bool(report["sourceStatusAtStart"] or report["sourceStatusAtEnd"])
        save()
    print(json.dumps({key: report.get(key) for key in ("ok", "phase", "workingCommit", "testResult", "errors")}))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
