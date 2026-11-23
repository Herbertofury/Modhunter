"""Windows-only detached full-layout diagnostic; no generic browser interface."""
from __future__ import annotations

import importlib.metadata
import json
import os
import re
import sys
import tempfile
from pathlib import Path, PureWindowsPath

import gallery_behavior as gallery

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
PARENT = "1d50b1ddb4c9ec1268994664f0e6b95249a67517"
REF = "refs/heads/diagnostics/gallery-full-visible-1d50"
WHEELS = {"playwright": "1.57.0", "greenlet": "3.2.4", "pyee": "13.0.0", "typing-extensions": "4.15.0"}
# Only *remove* weakening/default-overriding flags from Playwright 1.57.0.
# Exact values from v1.57.0 chromiumSwitches.ts; no profile or external target.
RESTORE_STOCK = (
    "--disable-client-side-phishing-detection", "--disable-component-update",
    "--disable-ipc-flooding-protection", "--disable-popup-blocking",
    "--unsafely-disable-devtools-self-xss-warnings", "--password-store=basic",
    "--use-mock-keychain", "--edge-skip-compat-layer-relaunch",
    "--disable-features=AcceptCHFrame,AvoidUnnecessaryBeforeUnloadCheckSync,DestroyProfileOnBrowserClose,DialMediaRouteProvider,GlobalMediaControls,HttpsUpgrades,LensOverlay,MediaRouter,PaintHolding,ThirdPartyStoragePartitioning,Translate,AutoDeElevate,RenderDocument,OptimizationHints",
)
# Review exact retained arguments against upstream chromiumSwitches.ts/1.57.0 and
# Chromium.defaultArgs. Fail, rather than guessing, on extra or missing flags.
REQUIRED = frozenset({
    "--headless", "--hide-scrollbars", "--mute-audio", "--remote-debugging-pipe",
    "--enable-automation", "--no-startup-window",
    "--blink-settings=primaryHoverType=2,availableHoverTypes=2,primaryPointerType=4,availablePointerTypes=4",
})
OTHER_VERIFIED = frozenset({
    "--disable-field-trial-config", "--disable-background-networking",
    "--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows",
    "--disable-back-forward-cache", "--disable-breakpad",
    "--disable-component-extensions-with-background-pages",
    "--no-default-browser-check", "--disable-default-apps", "--disable-dev-shm-usage",
    "--disable-extensions", "--enable-features=CDPScreenshotNewSurface",
    "--allow-pre-commit-input", "--disable-hang-monitor", "--disable-prompt-on-repost",
    "--disable-renderer-backgrounding", "--force-color-profile=srgb",
    "--metrics-recording-only", "--no-first-run", "--no-service-autorun",
    "--export-tagged-pdf", "--disable-search-engine-choice-screen", "--disable-infobars",
    "--disable-sync",
})

def require(ok, why):
    if not ok:
        raise ValueError(why)

def launch_options():
    return {"channel": "msedge", "headless": True, "chromium_sandbox": True,
            "ignore_default_args": list(RESTORE_STOCK), "timeout": 30_000}

def attest(argv, expected_images, tempdir):
    require(isinstance(argv, list) and 7 <= len(argv) <= 64 and
            all(isinstance(x, str) and 0 < len(x) <= 4096 for x in argv), "unknown argv")
    exe = PureWindowsPath(argv[0])
    require(exe in expected_images and exe.is_absolute(), "not preinstalled Edge")
    flags = argv[1:]
    profiles = [x.partition("=")[2] for x in flags if x.startswith("--user-data-dir=")]
    require(len(profiles) == 1, "missing or duplicate ephemeral profile")
    profile = PureWindowsPath(profiles[0])
    require(profile.parent == tempdir and
            re.fullmatch(r"playwright_chromiumdev_profile-[A-Za-z0-9]+", profile.name) is not None,
            "profile outside approved disposable directory")
    remaining = [x for x in flags if not x.startswith("--user-data-dir=")]
    distinct = set(remaining)
    # The upstream switch list duplicates this same flag. It is the only tolerated duplicate.
    require(all(remaining.count(x) == 1 for x in distinct if x != "--disable-search-engine-choice-screen")
            and remaining.count("--disable-search-engine-choice-screen") <= 2, "duplicate argv")
    require(REQUIRED <= distinct <= REQUIRED | OTHER_VERIFIED, "unexpected browser flags")

def select_frozen_checkout():
    # Repoint only path bindings of the exact previously verified driver.
    previous = gallery.ROOT
    gallery.EXPECTED = {REPO / path.relative_to(previous) if path.is_relative_to(previous) else path: digest
                        for path, digest in gallery.EXPECTED.items()}
    gallery.ROOT = REPO
    gallery.exact_inputs()

def phases(browser):
    for live in (False, True):
        mode = "mock-live" if live else "offline"
        for density in ("compact", "comfy"):
            yield "full-" + mode + "-" + density, lambda live=live, density=density: gallery.run_case(
                browser, live, density, 11207, 1300)
        yield "keyboard-" + mode, lambda live=live: gallery.run_case(
            browser, live, "compact", 12, 900, interaction=True)
        yield "narrow-" + mode, lambda live=live: gallery.run_case(browser, live, "compact", 12, 580)
    yield "source-truncation", lambda: gallery.run_case(
        browser, True, "compact", 12, 1250, require_truncation=True)
    for reason in ("locale", "density", "font-membership", "footer-css"):
        yield "invalidates-" + reason, lambda reason=reason: gallery.lifetime_case(browser, reason)

def write_report(report_path, status, phase, checked, boundary):
    result = {"schema": 1, "scope": "full-visible-owned-gallery-only", "base": PARENT,
              "status": status, "phase": phase, "passedCases": checked, "edgeLaunchChecked": boundary}
    head, run_id = os.environ.get("GITHUB_SHA", ""), os.environ.get("GITHUB_RUN_ID", "")
    if re.fullmatch(r"[a-f0-9]{40}", head) and re.fullmatch(r"[0-9]{1,25}", run_id):
        result.update(head=head, run=run_id, attempt=1)
    payload = json.dumps(result, separators=(",", ":"))
    require(len(payload) <= 2048, "report bound exceeded")
    report_path.write_text(payload, "utf8")

def run(report_path):
    phase, checked, boundary = "supported-host", 0, False
    try:
        require(sys.platform == "win32" and sys.version_info[:3] == (3, 12, 10) and
                os.environ.get("RUNNER_ARCH") == "X64" and sys.maxsize == 2**63-1 and
                os.environ.get("GITHUB_ACTIONS") == "true" and
                os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted" and
                os.environ.get("GITHUB_REF") == REF and os.environ.get("GITHUB_RUN_ATTEMPT") == "1" and
                os.environ.get("MODHUNTER_FULL_VISIBLE_CARRIER") == "reviewed-owned-fixture",
                "not the approved disposable host scope")
        phase = "exact-provenance"
        select_frozen_checkout()
        require(all(importlib.metadata.version(name) == version for name, version in WHEELS.items()),
                "unsupported client")
        phase = "stock-edge-available"
        images = set()
        for name in ("ProgramFiles(x86)", "ProgramFiles"):
            root = os.environ.get(name)
            if root:
                candidate = Path(root) / "Microsoft/Edge/Application/msedge.exe"
                if candidate.is_file():
                    images.add(PureWindowsPath(candidate))
        require(bool(images), "stock Edge unavailable")
        temp = Path(tempfile.gettempdir())
        require(temp.parent == report_path.parent and temp.name == "browser-temp", "not scoped disposable temp")
        # Standard client-managed debugging pipe. No loaded profile, port,
        # executable override, certificate bypass, proxy, remote endpoint or flag injection.
        from playwright.sync_api import sync_playwright
        phase = "stock-edge-launch"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**launch_options())
            try:
                phase = "stock-edge-command-check"
                session = browser.new_browser_cdp_session()
                try:
                    attest(session.send("Browser.getBrowserCommandLine").get("arguments"),
                           images, PureWindowsPath(temp))
                finally:
                    session.detach()
                boundary = True
                for case, invoke in phases(browser):
                    phase = case
                    invoke()
                    checked += 1
            finally:
                browser.close()
        require(checked == 13, "incomplete witness set")
        write_report(report_path, "passed", "complete", checked, boundary)
        return 0
    except Exception:
        # No exception text, DOM, timing, paths, browser args, profiles or raw trace retained.
        write_report(report_path, "unusable", phase, checked, boundary)
        return 1

if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(2)
    raise SystemExit(run(Path(sys.argv[1]).resolve()))
