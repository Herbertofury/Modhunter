"""Bounded actual-source Gallery surface comparison with exact viewport pixels.

Four fresh page contexts alternate C7 -> candidate, candidate -> C7 in one
browser. Every measured arm imports the exact complete 11,207-record native
fixture. No CSS override, tracing, sampled geometry, warm-up exclusion, result
limit, or virtualization is used. Preview-stretch changes only atelier.css;
flat-fill changes only app.js and atelier.css. Each immutable source candidate
compares against exact C7 independently. This diagnostic does not replace
Windows/release acceptance or waive any existing responsiveness ceiling.
Pixel-only mismatches retain their strict failure but defer termination until
all four planned arms are recorded; geometry/markup/state failures still abort.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import uuid

import visual_release_qa as acceptance
import visual_style_profile as profile
import visual_startup_trace as process_owner
from PIL import Image, ImageChops

ROOT = acceptance.ROOT
CONTROL_REF = "18883b32e0683dad8aed1dc87c6fe9ec577e12f2"
COUNT = 11207
FIXTURE_SHA = "afd855e4ac76f284630ffb4ed6d9dc37280113c9559aef4402516795b7fef0a0"
FIXTURE_BYTES = 4696493
WORKER_SECONDS = 690
REPORT_NAME = "surface-comparison.json"
VIEWPORT = {"width": 1028, "height": 633}
OWNED_URL = "http://modhunter-surface.invalid/"
EXPECTED_ASSETS = {
    "preview-stretch": {"mo2_update_arsenal/web/atelier.css"},
    "inner-card-flex": {"mo2_update_arsenal/web/atelier.css"},
    "flat-fill": {"mo2_update_arsenal/web/app.js", "mo2_update_arsenal/web/atelier.css"},
}
ACTIONS = (
    ("listDensity", "#densityToggle", "list", "comfy"),
    ("listDensityReset", "#densityToggle", "list", "compact"),
    ("gallery", "#libraryGallery", "gallery", "compact"),
    ("galleryDensity", "#densityToggle", "gallery", "comfy"),
    ("list", "#libraryList", "list", "comfy"),
    ("finalDensityReset", "#densityToggle", "list", "compact"),
)
ORDER = ((1, "control"), (1, "candidate"), (2, "candidate"), (2, "control"))
ENDPOINT_JS = """() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => {
 const list=document.getElementById('modList'),row=list?.querySelector(':scope > .mod-row');
 const style=row&&getComputedStyle(row);
 resolve({structuralBatchActive:!!list?.hasAttribute('data-layout-change'),
   transitionProperty:style?.transitionProperty,transitionDuration:style?.transitionDuration});
})))"""
IMPORT_JS = """async fixture => {
 const started=performance.now();window.modhunterImportCatalog(fixture);
 await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
 return performance.now()-started;
}"""
EXTRA_GEOMETRY_JS = """() => {
 const box=element=>{const r=element.getBoundingClientRect();return [r.x+scrollX,r.y+scrollY,r.width,r.height];};
 const list=document.getElementById('modList'),ancestors=[];
 for(let element=list.parentElement;element;element=element.parentElement)
   ancestors.push([element.tagName,element.id,element.className,...box(element)]);
 return {ancestors,headers:[...document.querySelectorAll('.list-header,.list-header > *,#workbench .workbench-heading')]
   .map((element,index)=>[index,element.tagName,element.id,element.textContent.trim(),...box(element)])};
}"""


def git(*arguments: str) -> str:
    return subprocess.check_output(["git", *arguments], cwd=ROOT, text=True).strip()


def finite_time(value) -> bool:
    return type(value) in (float, int) and math.isfinite(value) and value > 0


def validate_endpoint(endpoint: dict) -> None:
    assert endpoint == {"structuralBatchActive": False, "transitionProperty": "background",
                        "transitionDuration": "0.12s"}, (
        "Normal hover styling was not restored inside the measured second animation frame", endpoint)


def compare_state(reference: dict, current: dict) -> dict:
    geometry = profile.compare_geometry(reference["geometry"], current["geometry"])
    keys = ("listGeometry", "rowGeometry", "chapterGeometry", "emptyGeometry")
    exact = all(reference["geometry"][key] == current["geometry"][key] for key in keys)
    return {"geometry": geometry, "exactGeometryEqual": exact,
            "headerAndAncestorGeometryEqual": reference["extraGeometry"] == current["extraGeometry"],
            "orderedMarkupEqual": reference["markup"] == current["markup"],
            "stateEqual": all(reference["geometry"][key] == current["geometry"][key]
                              for key in ("view", "density", "documentNodes", "libraryNodes"))}


def state_passed(comparison: dict) -> bool:
    return not comparison["geometry"]["differenceCount"] and all(comparison[key] for key in (
        "exactGeometryEqual", "headerAndAncestorGeometryEqual", "orderedMarkupEqual", "stateEqual"))


def decoded_png(data: bytes) -> tuple[tuple[int, int], bytes]:
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "PNG", "Viewport artifact is not a PNG"
        rgba = image.convert("RGBA")
        return rgba.size, rgba.tobytes()


def compare_pixels(reference: tuple, current: tuple) -> dict:
    before_size, before = reference
    after_size, after = current
    if before_size != after_size:
        return {"identical": False, "referenceSize": list(before_size), "candidateSize": list(after_size),
                "changedPixels": None, "bounds": None, "reason": "Viewport image dimensions differ"}
    if before == after:
        return {"identical": True, "changedPixels": 0, "bounds": None,
                "width": before_size[0], "height": before_size[1], "tolerance": 0}
    diff = ImageChops.difference(Image.frombytes("RGBA", before_size, before),
                                Image.frombytes("RGBA", after_size, after))
    red, green, blue, alpha = diff.split()
    mask = ImageChops.lighter(ImageChops.lighter(red, green), ImageChops.lighter(blue, alpha))
    return {"identical": False, "changedPixels": sum(1 for value in mask.tobytes() if value),
            "bounds": list(mask.getbbox()), "width": before_size[0], "height": before_size[1], "tolerance": 0}


def capture_viewport(page, output: Path, label: str, state: str, references: dict) -> dict:
    path = output / f"{label}-{state}.png"
    data = page.screenshot(path=str(path), full_page=False, animations="allow", caret="hide", timeout=30000)
    decoded = decoded_png(data)
    assert decoded[0] == (VIEWPORT["width"], VIEWPORT["height"]), decoded[0]
    receipt = {"file": path.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
               "decodedRgbaSha256": hashlib.sha256(decoded[1]).hexdigest(), "width": decoded[0][0],
               "height": decoded[0][1], "capture": "Actual viewport PNG; animations remain active; only blinking text caret hidden; no clipping, cropping, scaling or style normalization"}
    images = references.setdefault("_viewport_images", {})
    if state not in images:
        images[state] = (decoded, path.name)
        receipt.update(reference=True, comparison=compare_pixels(decoded, decoded))
    else:
        previous, filename = images[state]
        receipt.update(referenceFile=filename, comparison=compare_pixels(previous, decoded))
    return receipt


def capture_state(page, output: Path, label: str, state: str, references: dict) -> dict:
    geometry = profile.complete_geometry(page, COUNT)
    assert all(row[0] == f"item-{index}" for index, row in enumerate(geometry["rowGeometry"])), (
        "The complete record order changed", label, state)
    data = {"geometry": geometry, "extraGeometry": page.evaluate(EXTRA_GEOMETRY_JS),
            "markup": profile.record_markup_fingerprint(page, COUNT)}
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode()
    path = output / f"{label}-{state}.geometry.json.gz"
    path.write_bytes(gzip.compress(payload, mtime=0))
    receipt = {"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
               "uncompressedSha256": hashlib.sha256(payload).hexdigest(), "uncompressedBytes": len(payload),
               "rows": geometry["rows"], "completeOrderedIds": True,
               "allLaidOut": geometry["allLaidOut"], "noContentVisibility": geometry["noContentVisibility"],
               "view": geometry["view"], "density": geometry["density"], "recordMarkup": data["markup"]}
    if state not in references:
        references[state] = data
        receipt["reference"] = True
    else:
        receipt["comparison"] = compare_state(references[state], data)
    receipt["viewportScreenshot"] = capture_viewport(page, output, label, state, references)
    receipt["ok"] = receipt["viewportScreenshot"]["comparison"]["identical"] and (
        "comparison" not in receipt or state_passed(receipt["comparison"]))
    return receipt


def validate_state(receipt: dict, label: str, state: str) -> None:
    # Preserve immediate semantic/layout failure. Pixel inequality remains in
    # the immutable receipt and is rejected after the full planned schedule,
    # so a raster mismatch cannot discard otherwise valid candidate timings.
    comparison = receipt.get("comparison")
    assert comparison is None or state_passed(comparison), (
        "Actual-source geometry, markup or state differ", label, state, comparison)


def require_strict_pixels(report: dict) -> None:
    trials = report["trials"]
    assert len(trials) == 4 and all(arm["measurementsComplete"] for arm in trials)
    failures = [{"label": arm["label"], "state": state,
                 "viewportScreenshot": receipt["viewportScreenshot"]}
                for arm in trials for state, receipt in arm["states"].items()
                if not receipt["viewportScreenshot"]["comparison"]["identical"]]
    report["measurementsComplete"] = True
    report["strictPixelFailures"] = failures
    report["strictComparisonPassed"] = not failures
    if failures:
        report["phase"] = "complete-with-strict-pixel-failures"
    assert not failures, (
        "Strict zero-tolerance viewport comparisons failed after all four planned measurements",
        len(failures))


def run_arm(browser, html: str, fixture: dict, output: Path, round_number: int, variant: str,
            references: dict, checkpoint) -> dict:
    label = f"round-{round_number}-{variant}"
    arm = {"round": round_number, "variant": variant, "label": label, "ok": False,
           "measurementsComplete": False, "strictComparisonPassed": False,
           "timingsMs": {}, "actions": {}, "states": {}, "errors": []}
    checkpoint(arm, label + ":starting")
    context = browser.new_context(viewport=VIEWPORT, reduced_motion="no-preference")
    page = context.new_page()
    page.set_default_timeout(60000)
    page.on("pageerror", lambda error: arm["errors"].append(str(error)))
    metrics = context.new_cdp_session(page)
    metrics.send("Performance.enable")
    try:
        # Native has canonical light preferences. This browser-only comparison
        # supplies the equivalent ordinary saved theme for both real profiles;
        # it does not inject native APIs or run a partial-library warm-up.
        preference_keys = ["dragonborn-mod-update-arsenal-v1",
                           "modhunter-offline-profile:v1:" + fixture["profileId"]]
        seed = json.dumps({"origin": OWNED_URL.rstrip("/"), "keys": preference_keys,
                           "value": json.dumps({"prefs": {"theme": "snow"}})})
        page.add_init_script("(seed=>{if(location.origin===seed.origin)for(const key of seed.keys)"
                             "localStorage.setItem(key,seed.value);})(" + seed + ");")
        page.route(OWNED_URL, lambda route: route.fulfill(status=200, content_type="text/html", body=html))
        page.goto(OWNED_URL, wait_until="load")
        page.wait_for_function("!!window.__ARSENAL_QA__")
        initial = page.evaluate("""() => ({view:document.body.dataset.libraryView,
          density:document.body.dataset.density,theme:document.body.dataset.theme,
          reducedMotion:matchMedia('(prefers-reduced-motion:reduce)').matches,
          width:innerWidth,height:innerHeight,devicePixelRatio,visibility:document.visibilityState})""")
        assert initial["view"] == "list" and initial["density"] == "compact" and initial["theme"] == "snow", initial
        assert initial["reducedMotion"] is False and initial["visibility"] == "visible", initial
        arm["initial"] = initial
        checkpoint(arm, label + ":importing")
        arm["timingsMs"]["render"] = page.evaluate(IMPORT_JS, fixture)
        assert page.evaluate("document.body.dataset.theme") == "snow", "Imported profile lost its ordinary saved theme"
        arm["states"]["import"] = capture_state(page, output, label, "import", references)
        validate_state(arm["states"]["import"], label, "import")
        page.evaluate("window.__SURFACE_SURVIVOR__=document.querySelector('#modList .mod-row:last-child')")
        started = time.perf_counter()
        page.locator("#search").fill("Unique Fixture 11206")
        page.wait_for_function("""() => {const rows=document.querySelectorAll('#modList .mod-row');
          return rows.length===1&&rows[0].dataset.id==='item-11206';}""")
        arm["timingsMs"]["lastItemSearch"] = (time.perf_counter() - started) * 1000
        assert page.evaluate("document.querySelector('#modList .mod-row')===window.__SURFACE_SURVIVOR__")
        started = time.perf_counter()
        page.locator("#search").fill("")
        page.wait_for_function("document.querySelectorAll('#modList .mod-row').length===11207")
        acceptance.settle(page)
        arm["timingsMs"]["searchRestore"] = (time.perf_counter() - started) * 1000
        assert page.evaluate("document.getElementById('row-item-11206')===window.__SURFACE_SURVIVOR__")
        arm["searchSurvivorPreserved"] = True
        arm["states"]["restore"] = capture_state(page, output, label, "restore", references)
        validate_state(arm["states"]["restore"], label, "restore")
        for name, selector, view, density in ACTIONS:
            checkpoint(arm, label + ":" + name + ":starting")
            before = profile.metric_snapshot(metrics)
            started = time.perf_counter()
            page.locator(selector).click()
            endpoint = page.evaluate(ENDPOINT_JS)
            elapsed = (time.perf_counter() - started) * 1000
            after = profile.metric_snapshot(metrics)
            arm["timingsMs"][name] = elapsed
            arm["actions"][name] = {"wallMs": elapsed, "frameEndpoint": endpoint,
                "metricsBefore": before, "metricsAfter": after,
                "metricDeltas": {key: after[key] - before[key] for key in before}}
            validate_endpoint(endpoint)
            arm["states"][name] = capture_state(page, output, label, name, references)
            validate_state(arm["states"][name], label, name)
            assert arm["states"][name]["view"] == view and arm["states"][name]["density"] == density
            assert not arm["errors"], arm["errors"]
            checkpoint(arm, label + ":" + name + ":complete")
            print(f"SURFACE_COMPARISON {label} {name} {elapsed:.3f}ms", flush=True)
        assert all(finite_time(value) for value in arm["timingsMs"].values()), arm["timingsMs"]
        assert len(arm["timingsMs"]) == 9 and len(arm["states"]) == 8
        assert not arm["errors"], arm["errors"]
        arm["measurementsComplete"] = True
        arm["strictComparisonPassed"] = all(receipt["ok"] for receipt in arm["states"].values())
        arm["ok"] = arm["strictComparisonPassed"]
    except Exception as error:
        arm["errors"].append(f"{type(error).__name__}: {error}")
        checkpoint(arm, label + ":failed")
    finally:
        metrics.detach()
        context.close()
        checkpoint(arm, label + ":closed")
    return arm


def worker(args) -> int:
    report_path = args.output / REPORT_NAME
    report = {"ok": False, "phase": "compiling-actual-sources", "runId": args.run_id,
        "mode": args.mode, "expectedChangedCompiledAssets": sorted(EXPECTED_ASSETS[args.mode]),
        "scopeAcceptance": False, "errors": [], "tracing": False, "cssOverrides": False,
        "measurementsComplete": False, "strictComparisonPassed": False,
        "pixelFailurePolicy": "Every raw pixel comparison remains zero-tolerance. Pixel-only failure is retained per state and rejected at completion after all four planned measurements; geometry, markup, state, endpoint, source and runtime failures remain immediate.",
        "warmupTrials": 0, "discardedTrials": 0, "order": [list(pair) for pair in ORDER],
        "motionPreference": "no-preference", "viewport": VIEWPORT,
        "themeSetup": "Fresh owned intercepted HTTP origin; ordinary persisted light preference for initial standalone and exact offline fixture profile. No native authority mock or partial-catalog warm-up.",
        "candidateCommit": git("rev-parse", "HEAD"), "controlCommit": git("rev-parse", args.control_ref),
        "sourceStatusAtStart": profile.source_status(), "platform": platform.platform(), "trials": [],
        "method": "Two opposite-order actual-source trials in fresh contexts of one Chromium process. Each imports the same full 11,207-record snapshot, searches/restores the final record, then clicks all six native-equivalent density/view controls. Action timer is real click through the second animation frame including guard removal/hover-style assertion. Complete geometry and markup checks are outside each timer. No trace, CSS override, hidden results, sample exclusion or native-performance claim.",
        "metricsScope": "CDP elapsed task/style/layout metrics bracket the click timer but their requests are outside it. They are not CPU time, exclusive categories, or proof of a particular style-flush cause.",
        "comparisonScope": "Complete ordered record outerHTML hash; every row, chapter header, empty-state and list rectangle; all list headers and the complete container ancestor chain. Exact geometry equality is required in addition to the existing 0.1px diagnostic comparison. Every state's actual viewport PNG is decoded to RGBA and compared pixel-for-pixel with the matching C7 state; no tolerance or image normalization.",
        "screenshotTiming": "After complete state geometry/markup collection, outside every interaction clock. CSS animations remain active. Only blinking text caret is hidden by the screenshot API; focus, hover, styling and viewport are not changed."}
    def save():
        profile.write_json(report_path, report)
    def checkpoint(arm, phase):
        report["phase"] = phase
        if not any(existing is arm for existing in report["trials"]):
            report["trials"].append(arm)
        save()
    save()
    try:
        assert sys.platform.startswith("linux") and Path("/proc/self/stat").is_file(), "Owned cleanup requires Linux /proc"
        assert report["controlCommit"] == CONTROL_REF, "Control must be exact C7, not a moving branch"
        assert not report["sourceStatusAtStart"], "Actual-source diagnostic requires clean committed source"
        control_html, control_assets = acceptance.compile_dashboard(CONTROL_REF)
        candidate_html, candidate_assets = acceptance.compile_dashboard()
        changed = [name for name in control_assets if control_assets[name] != candidate_assets[name]]
        assert set(changed) == EXPECTED_ASSETS[args.mode] and len(changed) == len(EXPECTED_ASSETS[args.mode]), (
            "Candidate compiled asset changes do not match the selected isolated experiment", args.mode, changed)
        report["sources"] = {"control": {"assets": control_assets, "htmlSha256": hashlib.sha256(control_html.encode()).hexdigest()},
                             "candidate": {"assets": candidate_assets, "htmlSha256": hashlib.sha256(candidate_html.encode()).hexdigest()},
                             "changedCompiledAssets": changed}
        report["harnessFiles"] = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (Path(__file__).resolve(), ROOT / "tests/visual_release_qa.py", ROOT / "tests/visual_style_profile.py",
                         ROOT / "tests/visual_startup_trace.py", ROOT / "tests/test_frontend_responsiveness.py")}
        fixture = acceptance.dashboard.catalog(count=COUNT)
        fixture["offline"] = True
        encoded = json.dumps(fixture, sort_keys=True).encode()
        assert len(encoded) == FIXTURE_BYTES and hashlib.sha256(encoded).hexdigest() == FIXTURE_SHA
        report.update(records=COUNT, fixtureSha256=FIXTURE_SHA, fixtureBytes=len(encoded))
        save()
        from playwright.sync_api import sync_playwright
        with sync_playwright() as runtime:
            marker = "--modhunter-surface-comparison=" + args.run_id
            browser = runtime.chromium.launch(headless=True, executable_path=str(args.executable_path),
                timeout=30000, args=["--no-sandbox", "--disable-dev-shm-usage", marker])
            try:
                identity = browser.new_browser_cdp_session()
                try:
                    arguments = identity.send("Browser.getBrowserCommandLine")["arguments"]
                    processes = identity.send("SystemInfo.getProcessInfo")["processInfo"]
                    report["browserPid"] = next(int(entry["id"]) for entry in processes if entry["type"] == "browser")
                    report["browserProtocolIdentity"] = identity.send("Browser.getVersion")
                    report["browserArguments"] = arguments
                    report["resolvedExecutable"] = str(Path(arguments[0]).resolve())
                    report["browserVersion"] = browser.version
                    assert marker in arguments and Path(arguments[0]).resolve() == args.executable_path.resolve()
                finally:
                    identity.detach()
                save()
                references = {}
                for round_number, variant in ORDER:
                    arm = run_arm(browser, control_html if variant == "control" else candidate_html,
                                  fixture, args.output, round_number, variant, references, checkpoint)
                    if not arm["measurementsComplete"]:
                        raise AssertionError("Actual-source measurement arm failed: " + arm["label"])
                report["pairedTimings"] = []
                for round_number in (1, 2):
                    pair = {arm["variant"]: arm for arm in report["trials"] if arm["round"] == round_number}
                    report["pairedTimings"].append({"round": round_number,
                        "actions": {name: {"controlMs": value,
                            "candidateMs": pair["candidate"]["timingsMs"][name],
                            "candidateOverControl": pair["candidate"]["timingsMs"][name] / value}
                            for name, value in pair["control"]["timingsMs"].items()}})
            finally:
                browser.close()
        report["sourceStatusAtEnd"] = profile.source_status()
        assert report["sourceStatusAtEnd"] == report["sourceStatusAtStart"], "Source changed during comparison"
        report["candidateCommitAtEnd"] = git("rev-parse", "HEAD")
        assert report["candidateCommitAtEnd"] == report["candidateCommit"], "Candidate commit changed during comparison"
        _, final_assets = acceptance.compile_dashboard()
        assert final_assets == candidate_assets, "Compiled source bytes changed during comparison"
        require_strict_pixels(report)
        report["ok"] = True
        report["phase"] = "complete"
    except Exception as error:
        report["errors"].append(f"{type(error).__name__}: {error}")
    finally:
        save()
    return 0 if report["ok"] else 1


def supervise(args) -> int:
    report_path = args.output / REPORT_NAME
    if report_path.exists():
        raise ValueError("Refusing to overwrite a previous diagnostic receipt; choose a fresh output directory")
    run_id = uuid.uuid4().hex
    command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--run-id", run_id,
               "--output", str(args.output), "--control-ref", args.control_ref, "--mode", args.mode,
               "--executable-path", str(args.executable_path)]
    profile.write_json(report_path, {"ok": False, "phase": "starting-owned-worker", "runId": run_id,
                                   "mode": args.mode, "errors": []})
    log_path = args.output / "surface-comparison.log"
    cleanup = None
    timed_out = False
    started = time.monotonic()
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True, env=dict(os.environ, DEBUG="pw:browser"))
        try:
            code = process.wait(timeout=WORKER_SECONDS)
        except subprocess.TimeoutExpired:
            timed_out = True
            partial = json.loads(report_path.read_text())
            # The reusable owner helper verifies this exact unique argument,
            # original process start ticks and PGID==PID before any signal.
            # This custom inert marker starts no trace or performance feature.
            cleanup = process_owner.cleanup_owned_processes(
                process, partial, log_path, "--modhunter-surface-comparison=" + run_id)
            code = process.poll()
        if code not in (0, None) and cleanup is None:
            cleanup = process_owner.cleanup_owned_processes(
                process, json.loads(report_path.read_text()), log_path, "--modhunter-surface-comparison=" + run_id)
    report = json.loads(report_path.read_text())
    report.update(workerExitCode=code, workerDeadlineSeconds=WORKER_SECONDS,
                  maximumExternalSeconds=WORKER_SECONDS + process_owner.TERMINATE_GRACE_SECONDS + process_owner.KILL_GRACE_SECONDS,
                  externallyTimedOut=timed_out, supervisorElapsedSeconds=time.monotonic() - started)
    if cleanup is not None:
        report["ownedProcessCleanup"] = cleanup
    if timed_out or code != 0:
        report["ok"] = False
        report.setdefault("errors", []).append(
            f"Owned comparison exceeded {WORKER_SECONDS}s at {report.get('phase')}" if timed_out else f"Owned comparison exited {code}")
    profile.write_json(report_path, report)
    print(json.dumps({key: report.get(key) for key in ("ok", "phase", "mode", "candidateCommit", "controlCommit",
        "browserVersion", "resolvedExecutable", "pairedTimings", "errors", "externallyTimedOut")}))
    return 0 if report["ok"] else 1


def self_test() -> int:
    endpoint = {"structuralBatchActive": False, "transitionProperty": "background", "transitionDuration": "0.12s"}
    validate_endpoint(endpoint)
    for key, value in (("structuralBatchActive", True), ("transitionProperty", "none"), ("transitionDuration", "0s")):
        try:
            validate_endpoint({**endpoint, key: value})
        except AssertionError:
            pass
        else:
            raise AssertionError("Endpoint validator accepted " + key)
    geometry = {"rows": 2, "uniqueIds": 2, "firstId": "item-0", "lastId": "item-1", "view": "list",
        "density": "compact", "documentNodes": 20, "libraryNodes": 8, "listGeometry": [0, 0, 500, 100],
        "rowGeometry": [["item-0", 0, 0, 500, 50], ["item-1", 0, 50, 500, 50]],
        "chapterGeometry": [], "emptyGeometry": []}
    state = {"geometry": geometry, "extraGeometry": {"headers": [[0, 0, 500, 20]]},
             "markup": {"rows": 2, "sha256": "a" * 64, "bytes": 100}}
    assert state_passed(compare_state(state, state))
    for field in ("row", "header", "markup", "density", "fraction"):
        altered = json.loads(json.dumps(state))
        if field == "row":
            altered["geometry"]["rowGeometry"][1][0] = "item-other"
        elif field == "header":
            altered["extraGeometry"]["headers"][0][-1] = 21
        elif field == "markup":
            altered["markup"]["sha256"] = "b" * 64
        elif field == "density":
            altered["geometry"]["density"] = "comfy"
        else:
            altered["geometry"]["rowGeometry"][1][1] += 0.015625
        assert not state_passed(compare_state(state, altered)), field
    assert all(not finite_time(value) for value in (0, -1, True, float("inf"), float("nan")))
    assert finite_time(1.25)
    image = Image.new("RGBA", (3, 2), (10, 20, 30, 255))
    stream = io.BytesIO(); image.save(stream, format="PNG")
    reference = decoded_png(stream.getvalue())
    assert compare_pixels(reference, reference)["identical"]
    image.putpixel((2, 1), (11, 20, 30, 255))
    stream = io.BytesIO(); image.save(stream, format="PNG")
    pixel = compare_pixels(reference, decoded_png(stream.getvalue()))
    assert pixel["identical"] is False and pixel["changedPixels"] == 1 and pixel["bounds"] == [2, 1, 3, 2]
    image = Image.new("RGBA", (3, 2), (10, 20, 30, 255))
    image.putpixel((0, 0), (10, 20, 30, 254))
    stream = io.BytesIO(); image.save(stream, format="PNG")
    alpha = compare_pixels(reference, decoded_png(stream.getvalue()))
    assert not alpha["identical"] and alpha["changedPixels"] == 1 and alpha["bounds"] == [0, 0, 1, 1]
    assert not compare_pixels(reference, ((1, 1), bytes(4)))["identical"]
    # A pixel-only mismatch must remain visible without ending a planned arm;
    # every semantic/layout failure must still stop immediately.
    clean_comparison = compare_state(state, state)
    pixel_receipt = {"ok": False, "comparison": clean_comparison,
                     "viewportScreenshot": {"comparison": pixel}}
    validate_state(pixel_receipt, "candidate", "import")
    assert pixel_receipt["ok"] is False and pixel_receipt["viewportScreenshot"]["comparison"]["identical"] is False
    for field in ("exactGeometryEqual", "headerAndAncestorGeometryEqual", "orderedMarkupEqual", "stateEqual"):
        bad = {**pixel_receipt, "comparison": {**clean_comparison, field: False}}
        try:
            validate_state(bad, "candidate", "import")
        except AssertionError:
            pass
        else:
            raise AssertionError("Deferred a non-pixel failure: " + field)
    strict_report = {"trials": [{"label": str(index), "measurementsComplete": True,
        "states": {"import": {"viewportScreenshot": {"comparison": compare_pixels(reference, reference)}}}}
        for index in range(4)]}
    require_strict_pixels(strict_report)
    assert strict_report["strictComparisonPassed"] is True
    strict_report["trials"][1]["states"]["import"] = pixel_receipt
    try:
        require_strict_pixels(strict_report)
    except AssertionError:
        assert strict_report["measurementsComplete"] is True
        assert strict_report["strictComparisonPassed"] is False
        assert strict_report["phase"] == "complete-with-strict-pixel-failures"
        assert len(strict_report["strictPixelFailures"]) == 1
    else:
        raise AssertionError("A pixel mismatch incorrectly passed final comparison")
    assert EXPECTED_ASSETS["preview-stretch"] == {"mo2_update_arsenal/web/atelier.css"}
    assert EXPECTED_ASSETS["flat-fill"] == {"mo2_update_arsenal/web/app.js", "mo2_update_arsenal/web/atelier.css"}
    assert WORKER_SECONDS + process_owner.TERMINATE_GRACE_SECONDS + process_owner.KILL_GRACE_SECONDS <= 720
    print("Surface comparison endpoint, full geometry/markup, exact pixels and deadline self-tests passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "native/target/surface-comparison")
    parser.add_argument("--control-ref", default=CONTROL_REF)
    parser.add_argument("--mode", choices=tuple(EXPECTED_ASSETS))
    parser.add_argument("--executable-path", type=Path)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--run-id", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if not args.mode:
        parser.error("Choose exactly one isolated source experiment with --mode")
    if not args.executable_path or not args.executable_path.expanduser().is_file():
        parser.error("Pass the exact Chromium executable used by the ordinary performance job")
    args.executable_path = args.executable_path.expanduser().resolve()
    args.output = args.output.expanduser().resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.worker:
        if not args.run_id or len(args.run_id) != 32 or any(c not in "0123456789abcdef" for c in args.run_id):
            parser.error("Worker requires its unique owned identifier")
        return worker(args)
    return supervise(args)


if __name__ == "__main__":
    raise SystemExit(main())
