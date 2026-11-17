"""Controlled full-library diagnosis; this never replaces release acceptance.

Compare the released 0.12.1 sources, unchanged 0.13 sources, and the same 0.13
HTML with exactly two flex-basis overrides. Reuse the release benchmark without
changing its workload, timing boundaries, warmup, assertions, or measured rounds.
"""
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
import visual_release_qa as engine  # noqa: E402

RELEASED = "f96b86969ec9f9c7ba6928284f16223b673d33c0"
UNCHANGED = "674a1d83f077c55d304d11defdaa5351eb3ef4d2"
MODS = 11207
ROUNDS = 3
OVERRIDE = """/* Diagnostic only: one flexible child at each constrained level. */
body[data-layout="compact"][data-workspace="mods"] #workbench{flex-basis:0}
body[data-layout="compact"] .library-table-viewport{flex-basis:0}
"""
GEOMETRY_CASES = (
    (1920, 1080, "night", "list", "compact"),
    (1920, 1080, "night", "list", "comfy"),
    (1920, 1080, "night", "gallery", "compact"),
    (1920, 1080, "night", "gallery", "comfy"),
    (1440, 900, "night", "list", "compact"),
    (1440, 900, "night", "gallery", "compact"),
    (900, 1000, "snow", "list", "compact"),
    (900, 1000, "snow", "gallery", "compact"),
    (390, 844, "snow", "list", "compact"),
    (390, 844, "snow", "gallery", "compact"),
)


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def compiled_variants(released_ref, unchanged_ref):
    refs = {"v0121": git("rev-parse", released_ref + "^{commit}"),
            "current013": git("rev-parse", unchanged_ref + "^{commit}")}
    compiled = {name: engine.compile_dashboard(ref) for name, ref in refs.items()}
    current, sources = compiled["current013"]
    assert current.count("</head>") == 1
    modified = current.replace("</head>", '<style id="layout-diagnostic-override">' + OVERRIDE + "</style></head>", 1)
    compiled["after"] = (modified, dict(sources))
    identities = {name: {"sourceCommit": refs.get(name, refs["current013"]),
                         "sourceSha256": files, "compiledHtmlSha256": sha(html),
                         "overrideSha256": sha(OVERRIDE) if name == "after" else None}
                  for name, (html, files) in compiled.items()}
    assert compiled["after"][1] == compiled["current013"][1]
    assert compiled["after"][0] == compiled["current013"][0].replace(
        "</head>", '<style id="layout-diagnostic-override">' + OVERRIDE + "</style></head>", 1)
    return compiled, identities


def capture_geometry(browser, html, output, name):
    fixture = engine.dashboard.catalog(count=307)
    fixture["offline"] = True
    context = browser.new_context(viewport={"width": 1920, "height": 1080}, reduced_motion="reduce")
    page = context.new_page()
    page.set_default_timeout(15000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    states = {}
    try:
        page.set_content(html, wait_until="load")
        page.evaluate("value=>window.modhunterImportCatalog(value)", fixture)
        engine.settle(page)
        for width, height, theme, view, density in GEOMETRY_CASES:
            page.set_viewport_size({"width": width, "height": height})
            engine.set_dashboard_theme(page, theme)
            page.locator("#libraryGallery" if view == "gallery" else "#libraryList").click()
            if page.locator("body").get_attribute("data-density") != density:
                page.locator("#densityToggle").click()
            engine.scroll_dashboard_to_top(page)
            state = page.evaluate("""() => {
              const rect=node=>{const r=node.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height}};
              const rows=[...document.querySelectorAll('#modList .mod-row')];
              const viewport=document.getElementById('libraryViewport');
              return {view:document.body.dataset.libraryView,density:document.body.dataset.density,
                theme:document.body.dataset.theme,rows:rows.length,
                ids:rows.map(row=>row.dataset.id),allLaidOut:rows.every(row=>row.getClientRects().length>0),
                noContentVisibility:rows.every(row=>getComputedStyle(row).contentVisibility==='visible'),
                nodes:document.querySelectorAll('#modList *').length,
                text:document.getElementById('modList').textContent,
                documentWidth:document.documentElement.scrollWidth,viewportWidth:innerWidth,
                boxes:Object.fromEntries(['workbench','libraryViewport','controls','listFooter'].map(id=>[id,rect(document.getElementById(id))])),
                sampleRows:[rows[0],rows[Math.floor(rows.length/2)],rows.at(-1)].map(rect),
                sampleCells:[...rows[0].children].map(node=>({class:node.className,box:rect(node),display:getComputedStyle(node).display})),
                scroll:{clientWidth:viewport.clientWidth,clientHeight:viewport.clientHeight,
                  scrollWidth:viewport.scrollWidth,scrollHeight:viewport.scrollHeight}};
            }""")
            assert state["rows"] == 307 and state["allLaidOut"] and state["noContentVisibility"], state
            assert state["documentWidth"] <= width + 1, state
            assert state["view"] == view and state["density"] == density and state["theme"] == theme, state
            state["textSha256"] = sha(state.pop("text"))
            key = f"{width}-{theme}-{view}-{density}"
            states[key] = state
            image = page.screenshot(path=str(output / f"geometry-{name}-{key}.png"))
            state["screenshotSha256"] = hashlib.sha256(image).hexdigest()
        assert not errors, errors
    finally:
        context.close()
    return states


def geometry_differences(before, after):
    differences = []
    def compare(left, right, path):
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(set(left) | set(right)):
                if key == "screenshotSha256":
                    continue
                compare(left.get(key), right.get(key), path + "." + key)
        elif isinstance(left, list) and isinstance(right, list):
            if len(left) != len(right):
                differences.append({"path": path, "beforeLength": len(left), "afterLength": len(right)})
            for index, (one, two) in enumerate(zip(left, right)):
                compare(one, two, path + f"[{index}]")
        elif type(left) in (int, float) and type(right) in (int, float):
            if abs(left - right) > 0.5:
                differences.append({"path": path, "before": left, "after": right})
        elif left != right:
            differences.append({"path": path, "before": left, "after": right})
    compare(before, after, "geometry")
    return differences


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--released-ref", default=RELEASED)
    parser.add_argument("--unchanged-ref", default=UNCHANGED)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check-inputs", action="store_true", help="Validate exact source/override identity without claiming browser proof")
    args = parser.parse_args()
    compiled, identities = compiled_variants(args.released_ref, args.unchanged_ref)
    if args.check_inputs:
        print(json.dumps({"browserExecuted": False, "variants": identities, "override": OVERRIDE}, indent=2))
        return
    args.output.mkdir(parents=True, exist_ok=True)
    start_status = git("status", "--porcelain", "--untracked-files=all").splitlines()
    assert not start_status, "Diagnostic source must be committed and clean"
    proof = {"schema": 1, "kind": "library-layout-diagnostic", "ok": False,
             "scope": "Controlled Chromium fixture diagnosis; does not replace native or v0.12.0 release acceptance",
             "proofCommit": git("rev-parse", "HEAD"), "platform": platform.platform(),
             "variants": identities, "override": OVERRIDE, "overrideSha256": sha(OVERRIDE),
             "engine": {"path": "tests/visual_release_qa.py", "sha256": sha(Path(engine.__file__).read_text("utf-8"))},
             "diagnosticSourceSha256": {name: sha((ROOT / name).read_text("utf-8")) for name in (
                 "tests/library_layout_diagnostic.py", "tests/test_frontend_responsiveness.py",
                 ".github/workflows/library-layout-diagnostic.yml")},
             "sourceStatusAtStart": start_status, "mods": MODS, "rounds": ROUNDS}
    receipt = args.output / "library-layout-diagnostic.json"
    def save():
        temporary = receipt.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(proof, indent=2), encoding="utf-8")
        temporary.replace(receipt)
    def checkpoint(value):
        proof["completeLibrary"] = value
        save()
    (args.output / "diagnostic-overrides.css").write_text(OVERRIDE, encoding="utf-8")
    save()
    from playwright.sync_api import sync_playwright
    with sync_playwright() as runtime:
        launch = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
        executable = shutil.which("chromium") or shutil.which("chromium-browser")
        if executable:
            launch["executable_path"] = executable
        browser = runtime.chromium.launch(**launch)
        proof["browserVersion"] = browser.version
        proof["browserExecutable"] = executable or "Playwright bundled Chromium"
        try:
            proof["geometry"] = {name: capture_geometry(browser, compiled[name][0], args.output, name)
                                 for name in ("current013", "after")}
            proof["geometryDifferences"] = geometry_differences(proof["geometry"]["current013"], proof["geometry"]["after"])
            save()
            # This is the mandatory release engine, not a shortened reproduction.
            proof["completeLibrary"] = engine.benchmark(browser, {name: html for name, (html, _) in compiled.items()}, MODS, ROUNDS, checkpoint)
            medians = proof["completeLibrary"]["medians"]
            proof["comparisonsPercent"] = {name: {metric: (medians["after"][metric] / value - 1) * 100
                                                  for metric, value in medians[name].items()}
                                              for name in ("v0121", "current013")}
            assert not proof["geometryDifferences"], "Flex override changed protected geometry; inspect the complete diagnostic receipt"
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
