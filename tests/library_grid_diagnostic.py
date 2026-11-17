"""Measure exact shared header tracks without changing production or release gates."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import library_layout_diagnostic as previous  # noqa: E402
import visual_release_qa as engine  # noqa: E402

RELEASED = "f96b86969ec9f9c7ba6928284f16223b673d33c0"
UNCHANGED = "3eae6a4f59b3b471d9a899ac253d9ba175a880b1"
RUNTIME = "tests/library_grid_diagnostic.js"
MODS = 11207
ROUNDS = 3


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def compile_variants(released_ref, unchanged_ref):
    refs = {"v0121": git("rev-parse", released_ref + "^{commit}"),
            "current013": git("rev-parse", unchanged_ref + "^{commit}")}
    compiled = {name: engine.compile_dashboard(ref) for name, ref in refs.items()}
    runtime = (ROOT / RUNTIME).read_text("utf-8")
    current, sources = compiled["current013"]
    assert current.count("</body>") == 1 and "</script>" not in runtime.lower()
    injection = '<script id="grid-diagnostic-runtime">' + runtime + "</script>"
    compiled["after"] = (current.replace("</body>", injection + "</body>", 1), dict(sources))
    assert compiled["after"][1] == compiled["current013"][1]
    assert compiled["after"][0].replace(injection, "", 1) == current
    identities = {name: {"sourceCommit": refs.get(name, refs["current013"]),
                         "sourceSha256": files, "compiledHtmlSha256": sha(html),
                         "runtimeOverrideSha256": sha(runtime) if name == "after" else None}
                  for name, (html, files) in compiled.items()}
    return compiled, identities, runtime


def capture_interaction_geometry(browser, html, output, name):
    """Exercise transitions that invalidate shared widths, beyond static cases."""
    fixture = engine.dashboard.catalog(count=307)
    fixture["offline"] = True
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    page.set_default_timeout(15000)
    states, diagnostics, errors = {}, {}, []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.set_content(html, wait_until="load")
        page.evaluate("value=>window.modhunterImportCatalog(value)", fixture)
        engine.settle(page)

        def capture(key):
            engine.settle(page)
            engine.scroll_dashboard_to_top(page)
            state = page.evaluate("""() => {
              const rect=node=>{const r=node.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height}};
              const rows=[...document.querySelectorAll('#modList .mod-row')],header=document.querySelector('.list-header');
              const viewport=document.getElementById('libraryViewport');
              const cells=node=>[...node.children].map(cell=>({class:cell.className,box:rect(cell),display:getComputedStyle(cell).display}));
              const sample=[rows[0],rows[Math.floor(rows.length/2)],rows.at(-1)];
              return {view:document.body.dataset.libraryView,density:document.body.dataset.density,
                theme:document.body.dataset.theme,nav:document.body.dataset.nav,
                columns:document.body.dataset.libraryColumns,hiddenColumns:document.body.dataset.libraryHiddenColumns,
                ids:rows.map(row=>row.dataset.id),rows:rows.length,allLaidOut:rows.every(row=>row.getClientRects().length>0),
                noContentVisibility:rows.every(row=>getComputedStyle(row).contentVisibility==='visible'),
                nodes:document.querySelectorAll('#modList *').length,text:document.getElementById('modList').textContent,
                documentWidth:document.documentElement.scrollWidth,viewportWidth:innerWidth,
                header:{box:rect(header),cells:cells(header),tracks:getComputedStyle(header).gridTemplateColumns},
                sampleRows:sample.map(rect),sampleCells:sample.map(cells),
                boxes:Object.fromEntries(['workbench','libraryViewport','controls','listFooter'].map(id=>[id,rect(document.getElementById(id))])),
                scroll:{clientWidth:viewport.clientWidth,clientHeight:viewport.clientHeight,
                  scrollWidth:viewport.scrollWidth,scrollHeight:viewport.scrollHeight}};
            }""")
            assert state["rows"] == 307 and state["allLaidOut"] and state["noContentVisibility"], state
            assert state["documentWidth"] <= state["viewportWidth"] + 1, state
            state["textSha256"] = sha(state.pop("text"))
            image = page.screenshot(path=str(output / f"interaction-{name}-{key}.png"))
            state["screenshotSha256"] = hashlib.sha256(image).hexdigest()
            states[key] = state
            diagnostics[key] = page.evaluate("window.__MODHUNTER_GRID_DIAGNOSTIC__?.()||null")

        capture("initial-1440")
        page.set_viewport_size({"width": 1920, "height": 1080})
        capture("resize-1920")
        page.locator("#railToggle").click()
        capture("expanded-rail")
        page.locator("#libraryColumns").click()
        page.locator('[data-library-column="health"]').uncheck()
        page.locator('[data-library-column="priority"]').uncheck()
        page.locator("#libraryColumnsDone").click()
        capture("hidden-health-priority")
        page.locator("#densityToggle").click()
        capture("hidden-comfy")
        page.locator("#libraryGallery").click()
        capture("hidden-gallery-complete-details")
        page.locator("#libraryList").click()
        capture("hidden-list-restored")
        page.locator("#libraryColumns").click()
        page.locator("#libraryColumnsReset").click()
        page.locator("#libraryColumnsDone").click()
        capture("all-columns-restored")
        engine.set_dashboard_theme(page, "snow")
        capture("light-all-columns")
        page.set_viewport_size({"width": 900, "height": 1000})
        capture("overflow-900")
        page.set_viewport_size({"width": 390, "height": 844})
        capture("narrow-390")
        page.set_viewport_size({"width": 1440, "height": 900})
        page.locator("#railToggle").click()
        page.locator("#densityToggle").click()
        capture("returned-1440")
        page.locator('.workspace-tabs [data-workspace="updates"]').click()
        page.locator('.workspace-tabs [data-workspace="mods"]').click()
        capture("workspace-round-trip")
        assert not errors, errors
        if name == "after":
            assert all(value and value["writes"] > 0 for value in diagnostics.values()), diagnostics
            assert all(value["fallbacks"] == 0 for value in diagnostics.values()), diagnostics
        return states, diagnostics
    finally:
        context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--released-ref", default=RELEASED)
    parser.add_argument("--unchanged-ref", default=UNCHANGED)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check-inputs", action="store_true")
    args = parser.parse_args()
    compiled, identities, runtime = compile_variants(args.released_ref, args.unchanged_ref)
    if args.check_inputs:
        print(json.dumps({"browserExecuted": False, "variants": identities, "runtimeOverrideSha256": sha(runtime)}, indent=2))
        return
    args.output.mkdir(parents=True, exist_ok=True)
    status = git("status", "--porcelain", "--untracked-files=all").splitlines()
    assert not status, "Diagnostic source must be committed and clean"
    proof = {"schema": 1, "kind": "library-grid-diagnostic", "ok": False,
             "scope": "Controlled Chromium full-content diagnosis; does not replace release acceptance",
             "proofCommit": git("rev-parse", "HEAD"), "platform": platform.platform(),
             "variants": identities, "runtimeOverrideSha256": sha(runtime),
             "engine": {"path": "tests/visual_release_qa.py", "sha256": sha(Path(engine.__file__).read_text("utf-8"))},
             "diagnosticSourceSha256": {path: sha((ROOT / path).read_text("utf-8")) for path in (
                 "tests/library_grid_diagnostic.py", RUNTIME, "tests/library_layout_diagnostic.py",
                 "tests/test_frontend_responsiveness.py", ".github/workflows/library-grid-diagnostic.yml")},
             "sourceStatusAtStart": status, "mods": MODS, "rounds": ROUNDS}
    receipt = args.output / "library-grid-diagnostic.json"

    def save():
        temporary = receipt.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(proof, indent=2), encoding="utf-8")
        temporary.replace(receipt)

    def checkpoint(value):
        proof["completeLibrary"] = value
        save()

    (args.output / "grid-diagnostic-runtime.js").write_text(runtime, encoding="utf-8")
    save()
    print("GRID source identity " + json.dumps({
        "proofCommit": proof["proofCommit"], "engine": proof["engine"],
        "runtimeOverrideSha256": proof["runtimeOverrideSha256"],
        "variants": {name: {"sourceCommit": identity["sourceCommit"],
                            "compiledHtmlSha256": identity["compiledHtmlSha256"],
                            "sourceSetSha256": sha(json.dumps(identity["sourceSha256"], sort_keys=True))}
                     for name, identity in identities.items()}}, sort_keys=True), flush=True)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as runtime_api:
        launch = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
        executable = shutil.which("chromium") or shutil.which("chromium-browser")
        if executable:
            launch["executable_path"] = executable
        browser = runtime_api.chromium.launch(**launch)
        proof["browserVersion"] = browser.version
        proof["browserExecutable"] = executable or "Playwright bundled Chromium"
        try:
            proof["geometry"] = {name: previous.capture_geometry(browser, compiled[name][0], args.output, name)
                                 for name in ("current013", "after")}
            proof["interactionGeometry"], proof["optimizationDiagnostics"] = {}, {}
            for name in ("current013", "after"):
                geometry, diagnostics = capture_interaction_geometry(browser, compiled[name][0], args.output, name)
                proof["interactionGeometry"][name] = geometry
                proof["optimizationDiagnostics"][name] = diagnostics
            proof["geometryDifferences"] = previous.geometry_differences(proof["geometry"]["current013"], proof["geometry"]["after"])
            proof["interactionGeometryDifferences"] = previous.geometry_differences(proof["interactionGeometry"]["current013"], proof["interactionGeometry"]["after"])
            save()
            differences = proof["geometryDifferences"] + proof["interactionGeometryDifferences"]
            if differences:
                print("GRID geometry mismatch " + json.dumps({"count": len(differences), "first": differences[:10]}, sort_keys=True), flush=True)
            assert not differences, "Shared tracks changed protected geometry; benchmark not run for an invalid variant"
            print("GRID geometry passed: 10 complete static cases and 13 resize/column/view transitions; starting unchanged full11,207 benchmark", flush=True)
            proof["completeLibrary"] = engine.benchmark(browser, {name: html for name, (html, _) in compiled.items()}, MODS, ROUNDS, checkpoint)
            medians = proof["completeLibrary"]["medians"]
            proof["comparisonsPercent"] = {name: {metric: (medians["after"][metric] / value - 1) * 100
                                                  for metric, value in medians[name].items()}
                                              for name in ("v0121", "current013")}
            assert not proof["geometryDifferences"] and not proof["interactionGeometryDifferences"], "Shared tracks changed protected geometry; inspect receipt"
            assert git("rev-parse", "HEAD") == proof["proofCommit"]
            assert not git("status", "--porcelain", "--untracked-files=all")
            proof["ok"] = True
        except BaseException as error:
            proof["error"] = str(error)
            raise
        finally:
            browser.close()
            proof["sourceStatusAtEnd"] = git("status", "--porcelain", "--untracked-files=all").splitlines()
            if proof["sourceStatusAtEnd"]:
                proof["ok"] = False
            save()
    print(json.dumps({"receipt": str(receipt), "ok": proof["ok"],
                      "medians": proof["completeLibrary"]["medians"],
                      "comparisonsPercent": proof["comparisonsPercent"]}, indent=2))


if __name__ == "__main__":
    main()
