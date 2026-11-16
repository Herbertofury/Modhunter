"""Bounded C7-only full-library raster repeatability diagnostic.

Three fresh contexts import the identical complete 11,207-record fixture in the
same real Chromium process. Each retains two unmodified List-import viewport
PNGs, complete ordered geometry/markup, and the active All button's computed
styles and bounds. Exact pixel differences are reported, never tolerated or
normalized. This diagnoses screenshot repeatability; it is not an acceptance
gate, performance comparison, candidate test, or reason to waive a pixel gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import traceback
import uuid

import ci_font_rendering_qa as font_qa
import visual_surface_comparison as surface
from PIL import Image, ImageChops

ROOT = surface.ROOT
WORKER_SECONDS = 120
REPORT_NAME = "raster-repeatability.json"
CONTEXTS = 3
CAPTURES = 2
SOURCES = (
    "tests/visual_raster_repeatability.py", "tests/visual_surface_comparison.py",
    "tests/visual_release_qa.py", "tests/visual_style_profile.py",
    "tests/visual_startup_trace.py", "tests/test_frontend_responsiveness.py",
    "tests/ci_font_rendering_qa.py", "tests/prepare_ci_fonts.py",
)
BUTTON_JS = """() => {
 const button=document.querySelector('.workspace-summary [data-filter="all"]');
 if(!button)throw Error('The actual All summary button is missing');
 const style=getComputedStyle(button),rect=button.getBoundingClientRect();
 return {selector:'.workspace-summary [data-filter="all"]',text:button.textContent,
   className:button.className,markup:button.outerHTML,
   rect:[rect.x,rect.y,rect.width,rect.height],
   styles:Object.fromEntries([...style].map(key=>[key,style.getPropertyValue(key)])),
   hover:button.matches(':hover'),focus:button.matches(':focus-within'),
   activeElement:document.activeElement?.id||document.activeElement?.tagName,
   animations:button.getAnimations({subtree:true}).map(animation=>({
     playState:animation.playState,currentTime:animation.currentTime,
     transitionProperty:animation.transitionProperty||null,
     animationName:animation.animationName||null,timing:animation.effect?.getComputedTiming()}))};
}"""


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compare_rgba(reference: tuple, current: tuple) -> dict:
    result = surface.compare_pixels(reference, current)
    if reference[0] != current[0]:
        return {**result, "maximumChannelDelta": None, "maximumChannelDeltaRgba": None}
    difference = ImageChops.difference(Image.frombytes("RGBA", reference[0], reference[1]),
                                      Image.frombytes("RGBA", current[0], current[1]))
    maxima = [high for low, high in difference.getextrema()]
    return {**result, "maximumChannelDelta": max(maxima), "maximumChannelDeltaRgba": maxima}


def worker(args) -> int:
    report_path = args.output / REPORT_NAME
    report = {
        "schema": 1, "kind": "same-source-raster-repeatability", "ok": False,
        "scopeAcceptance": False, "scope": __doc__, "runId": args.run_id,
        "phase": "source-and-font-verification", "errors": [], "contexts": [],
        "workingCommit": surface.git("rev-parse", "HEAD"),
        "compiledSourceCommit": surface.git("rev-parse", args.control_ref),
        "sourceStatusAtStart": surface.profile.source_status(),
        "harnessFiles": {name: sha((ROOT / name).read_bytes()) for name in SOURCES},
        "platform": platform.platform(), "viewport": surface.VIEWPORT,
        "motionPreference": "no-preference", "theme": "snow",
        "tracing": False, "cssOverrides": False, "warmupTrials": 0,
        "discardedTrials": 0, "requestedExecutable": str(args.executable_path.resolve()),
        "method": "Exact frozen surface-harness List import setup: ordinary persisted light preference on its owned origin, real shipped full-fixture import followed by two animation frames, Performance agent enabled. Three fresh contexts, two sequential raw viewport captures per context; all button/geometry/pixel reads occur after the import timer. No hover, focus, CSS, clock, rAF, image or viewport normalization. Pixel inequality is a reported diagnostic observation, not an accepted comparison.",
    }

    def save():
        surface.profile.write_json(report_path, report)

    save()
    try:
        assert sys.platform.startswith("linux") and Path("/proc/self/stat").is_file()
        assert report["compiledSourceCommit"] == surface.CONTROL_REF
        assert not report["sourceStatusAtStart"], "Committed clean diagnostic source is required"
        _, report["fontProof"] = font_qa.verify_font_receipt(args.font_receipt)
        html, assets = surface.acceptance.compile_dashboard(surface.CONTROL_REF)
        report["compiledSource"] = {"assets": assets, "htmlSha256": sha(html.encode())}
        fixture = surface.acceptance.dashboard.catalog(count=surface.COUNT)
        fixture["offline"] = True
        encoded = json.dumps(fixture, sort_keys=True).encode()
        assert len(encoded) == surface.FIXTURE_BYTES and sha(encoded) == surface.FIXTURE_SHA
        report.update(records=surface.COUNT, fixtureSha256=sha(encoded), fixtureBytes=len(encoded))
        save()
        from playwright.sync_api import sync_playwright
        references = {}
        with sync_playwright() as runtime:
            marker = "--modhunter-raster-repeatability=" + args.run_id
            report["phase"] = "launching-owned-browser"
            save()
            browser = runtime.chromium.launch(executable_path=str(args.executable_path), headless=True,
                timeout=30000, args=["--no-sandbox", "--disable-dev-shm-usage", marker])
            try:
                identity = browser.new_browser_cdp_session()
                try:
                    arguments = identity.send("Browser.getBrowserCommandLine")["arguments"]
                    processes = identity.send("SystemInfo.getProcessInfo")["processInfo"]
                    report["browserPid"] = next(int(value["id"]) for value in processes if value["type"] == "browser")
                    report["browserProtocolIdentity"] = identity.send("Browser.getVersion")
                    report["browserArguments"] = arguments
                    report["resolvedExecutable"] = str(Path(arguments[0]).resolve())
                    report["browserVersion"] = browser.version
                    assert marker in arguments and Path(arguments[0]).resolve() == args.executable_path.resolve()
                finally:
                    identity.detach()
                save()
                for number in range(1, CONTEXTS + 1):
                    arm = {"context": number, "ok": False, "captures": [], "errors": []}
                    report["contexts"].append(arm)
                    report["phase"] = f"context-{number}:starting"
                    save()
                    context = browser.new_context(viewport=surface.VIEWPORT, reduced_motion="no-preference")
                    metrics = None
                    try:
                        page = context.new_page()
                        page.set_default_timeout(60000)
                        page.on("pageerror", lambda error, arm=arm: arm["errors"].append(str(error)))
                        metrics = context.new_cdp_session(page)
                        metrics.send("Performance.enable")
                        preference_keys = ["dragonborn-mod-update-arsenal-v1",
                                           "modhunter-offline-profile:v1:" + fixture["profileId"]]
                        seed = json.dumps({"origin": surface.OWNED_URL.rstrip("/"), "keys": preference_keys,
                                           "value": json.dumps({"prefs": {"theme": "snow"}})})
                        page.add_init_script("(seed=>{if(location.origin===seed.origin)for(const key of seed.keys)"
                                             "localStorage.setItem(key,seed.value);})(" + seed + ");")
                        page.route(surface.OWNED_URL, lambda route: route.fulfill(status=200, content_type="text/html", body=html))
                        page.goto(surface.OWNED_URL, wait_until="load")
                        page.wait_for_function("!!window.__ARSENAL_QA__")
                        initial = page.evaluate("""() => ({view:document.body.dataset.libraryView,
                          density:document.body.dataset.density,theme:document.body.dataset.theme,
                          reducedMotion:matchMedia('(prefers-reduced-motion:reduce)').matches,
                          width:innerWidth,height:innerHeight,devicePixelRatio,visibility:document.visibilityState})""")
                        assert initial == {"view": "list", "density": "compact", "theme": "snow",
                                           "reducedMotion": False, "width": 1028, "height": 633,
                                           "devicePixelRatio": 1, "visibility": "visible"}, initial
                        arm["initial"] = initial
                        report["phase"] = f"context-{number}:importing"
                        save()
                        arm["importMs"] = page.evaluate(surface.IMPORT_JS, fixture)
                        assert surface.finite_time(arm["importMs"])
                        assert page.evaluate("document.body.dataset.theme") == "snow"
                        for shot in range(1, CAPTURES + 1):
                            report["phase"] = f"context-{number}:capture-{shot}"
                            save()
                            before_button = page.evaluate(BUTTON_JS)
                            receipt = surface.capture_state(page, args.output, f"context-{number}-capture-{shot}", "import", references)
                            after_button = page.evaluate(BUTTON_JS)
                            arm["captures"].append({"capture": shot, "state": receipt,
                                                    "buttonBefore": before_button, "buttonAfter": after_button,
                                                    "buttonStableDuringCapture": before_button == after_button})
                            save()
                            if "comparison" in receipt:
                                assert surface.state_passed(receipt["comparison"]), "Identical-source record geometry/markup changed"
                        assert not arm["errors"], arm["errors"]
                        arm["ok"] = True
                    finally:
                        if metrics is not None:
                            metrics.detach()
                        context.close()
                        save()
            finally:
                browser.close()
        captures = [(arm["context"], capture) for arm in report["contexts"] for capture in arm["captures"]]
        assert len(report["contexts"]) == CONTEXTS and len(captures) == CONTEXTS * CAPTURES
        assert all(arm["ok"] for arm in report["contexts"])
        decoded = {}
        for _, capture in captures:
            receipt = capture["state"]["viewportScreenshot"]
            raw = (args.output / receipt["file"]).read_bytes()
            assert len(raw) == receipt["bytes"] and sha(raw) == receipt["sha256"]
            decoded[receipt["file"]] = surface.decoded_png(raw)
            assert sha(decoded[receipt["file"]][1]) == receipt["decodedRgbaSha256"]
        comparisons = []
        for left_index, (left_context, left) in enumerate(captures):
            for right_context, right in captures[left_index + 1:]:
                left_name, right_name = (capture["state"]["viewportScreenshot"]["file"] for capture in (left, right))
                comparisons.append({"reference": left_name, "current": right_name,
                    "sameContext": left_context == right_context,
                    "buttonComputedStateEqual": left["buttonBefore"] == right["buttonBefore"],
                    "pixels": compare_rgba(decoded[left_name], decoded[right_name])})
        report["pixelComparisons"] = comparisons
        report["identicalAcrossAllCaptures"] = all(pair["pixels"]["identical"] for pair in comparisons)
        report["identicalWithinEachContext"] = all(pair["pixels"]["identical"] for pair in comparisons if pair["sameContext"])
        report["sourceStatusAtEnd"] = surface.profile.source_status()
        report["workingCommitAtEnd"] = surface.git("rev-parse", "HEAD")
        assert not report["sourceStatusAtEnd"] and report["workingCommitAtEnd"] == report["workingCommit"]
        assert surface.acceptance.compile_dashboard(surface.CONTROL_REF)[1] == assets
        assert all(sha((ROOT / name).read_bytes()) == digest for name, digest in report["harnessFiles"].items())
        report.update(ok=True, phase="complete", sourceDirty=False)
    except Exception:
        report["errors"].append(traceback.format_exc())
    finally:
        save()
    return 0 if report["ok"] else 1


def supervise(args) -> int:
    report_path = args.output / REPORT_NAME
    assert not report_path.exists(), "Choose a fresh output directory; prior evidence is never overwritten"
    run_id = uuid.uuid4().hex
    command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--run-id", run_id,
               "--output", str(args.output), "--control-ref", args.control_ref,
               "--font-receipt", str(args.font_receipt), "--executable-path", str(args.executable_path)]
    surface.profile.write_json(report_path, {"ok": False, "phase": "starting-owned-worker", "runId": run_id, "errors": []})
    log_path = args.output / "raster-repeatability.log"
    cleanup, timed_out = None, False
    started = time.monotonic()
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True, env=dict(os.environ, DEBUG="pw:browser"))
        try:
            code = process.wait(timeout=WORKER_SECONDS)
        except subprocess.TimeoutExpired:
            timed_out = True
            cleanup = surface.process_owner.cleanup_owned_processes(process, json.loads(report_path.read_text()),
                log_path, "--modhunter-raster-repeatability=" + run_id)
            code = process.poll()
        if code not in (0, None) and cleanup is None:
            cleanup = surface.process_owner.cleanup_owned_processes(process, json.loads(report_path.read_text()),
                log_path, "--modhunter-raster-repeatability=" + run_id)
    report = json.loads(report_path.read_text())
    report.update(workerExitCode=code, workerDeadlineSeconds=WORKER_SECONDS,
                  maximumExternalSeconds=WORKER_SECONDS + surface.process_owner.TERMINATE_GRACE_SECONDS + surface.process_owner.KILL_GRACE_SECONDS,
                  externallyTimedOut=timed_out, supervisorElapsedSeconds=time.monotonic() - started)
    if cleanup is not None:
        report["ownedProcessCleanup"] = cleanup
    if timed_out or code != 0:
        report["ok"] = False
        report.setdefault("errors", []).append("Owned worker exceeded120s" if timed_out else f"Owned worker exited{code}")
    surface.profile.write_json(report_path, report)
    print(json.dumps({key: report.get(key) for key in ("ok", "phase", "compiledSourceCommit", "workingCommit",
        "browserVersion", "identicalAcrossAllCaptures", "identicalWithinEachContext", "errors", "externallyTimedOut")}))
    return 0 if report["ok"] else 1


def self_test() -> int:
    before = ((2, 1), bytes([10, 20, 30, 255, 40, 50, 60, 255]))
    same = compare_rgba(before, before)
    assert same["identical"] and same["changedPixels"] == 0 and same["maximumChannelDelta"] == 0
    after = ((2, 1), bytes([10, 21, 30, 255, 40, 50, 58, 254]))
    difference = compare_rgba(before, after)
    assert not difference["identical"] and difference["changedPixels"] == 2
    assert difference["bounds"] == [0, 0, 2, 1] and difference["maximumChannelDeltaRgba"] == [0, 1, 2, 1]
    assert difference["maximumChannelDelta"] == 2 and difference["tolerance"] == 0
    assert WORKER_SECONDS == 120 and CONTEXTS == 3 and CAPTURES == 2
    print("Same-source raster exact-RGBA and bounded-protocol self-tests passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--executable-path", type=Path)
    parser.add_argument("--font-receipt", type=Path)
    parser.add_argument("--control-ref", default=surface.CONTROL_REF)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--run-id", help=argparse.SUPPRESS)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if args.output is None or args.executable_path is None or args.font_receipt is None:
        parser.error("--output, --executable-path and --font-receipt are required")
    assert args.control_ref == surface.CONTROL_REF and args.executable_path.is_file()
    assert args.font_receipt.is_file()
    args.output.mkdir(parents=True, exist_ok=True)
    return worker(args) if args.worker else supervise(args)


if __name__ == "__main__":
    raise SystemExit(main())
