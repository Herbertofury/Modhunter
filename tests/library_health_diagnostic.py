"""Measure plain health text without a redundant anonymous flex item."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import platform
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import library_grid_diagnostic as shared  # noqa: E402
import library_layout_diagnostic as geometry  # noqa: E402
import visual_release_qa as engine  # noqa: E402

git, sha = shared.git, shared.sha
RELEASED = shared.RELEASED
UNCHANGED = shared.UNCHANGED
MODS, ROUNDS = 11207, 3
CSS = 'body[data-layout="compact"] .mod-health-text{display:block}\n'
HEALTH_EXPRESSION = "(INSIGHTS?.chips(m,{includeBugs:false})||'')+(SOURCES?.chips(m)||'')"
DECLARATION = " const healthMarkup=compact?(" + HEALTH_EXPRESSION + "):'';\n"
BEFORE_CLASS = '<div class="mod-health" aria-label="Plugin and source health">'
AFTER_CLASS = '<div class="mod-health${healthMarkup?\'\':\' mod-health-text\'}" aria-label="Plugin and source health">'
BEFORE_CONTENT = "${" + HEALTH_EXPRESSION + "||'Not analyzed'}"
AFTER_CONTENT = "${healthMarkup||'Not analyzed'}"


def plain_health_source(source):
    start = source.index("function buildModHTML(m){const compact=")
    boundary = source.index("\n", start) + 1
    assert source.count(BEFORE_CLASS) == source.count(BEFORE_CONTENT) == 1
    result = source[:boundary] + DECLARATION + source[boundary:]
    result = result.replace(BEFORE_CLASS, AFTER_CLASS, 1).replace(BEFORE_CONTENT, AFTER_CONTENT, 1)
    assert result.replace(DECLARATION, "", 1).replace(AFTER_CLASS, BEFORE_CLASS, 1).replace(AFTER_CONTENT, BEFORE_CONTENT, 1) == source
    return result


def compile_variants(released_ref, unchanged_ref):
    refs = {"v0121": git("rev-parse", released_ref + "^{commit}"),
            "current013": git("rev-parse", unchanged_ref + "^{commit}")}
    compiled = {name: engine.compile_dashboard(ref) for name, ref in refs.items()}
    current, sources = compiled["current013"]
    patched = plain_health_source(current)
    assert current.count("</head>") == 1
    injection = '<style id="health-diagnostic-override">' + CSS + "</style>"
    compiled["after"] = (patched.replace("</head>", injection + "</head>", 1), dict(sources))
    raw_app = engine.read(engine.WEB + "app.js", refs["current013"])
    patch = {"declaration": DECLARATION, "beforeClass": BEFORE_CLASS, "afterClass": AFTER_CLASS,
             "beforeContent": BEFORE_CONTENT, "afterContent": AFTER_CONTENT, "css": CSS,
             "originalAppSha256": sha(raw_app), "modifiedAppSha256": sha(plain_health_source(raw_app))}
    identities = {name: {"sourceCommit": refs.get(name, refs["current013"]),
                         "sourceSha256": files, "compiledHtmlSha256": sha(html),
                         "compiledSourcePatchSha256": sha(json.dumps(patch, sort_keys=True)) if name == "after" else None}
                  for name, (html, files) in compiled.items()}
    assert compiled["after"][1] == compiled["current013"][1]
    return compiled, identities, patch


def protected_geometry(state):
    """Normalize only the declared marker; preserve all measured box values."""
    result = copy.deepcopy(state)
    def visit(value):
        if isinstance(value, dict):
            if value.get("class") == "mod-health mod-health-text":
                assert value.get("display") in ("block", "none"), value
                value["class"] = "mod-health"
                if value["display"] == "block":
                    value["display"] = "flex"
            for key, entry in value.items():
                if key == "class" and isinstance(entry, str) and "mod-health-text" in entry.split():
                    assert entry == "mod-health mod-health-text", entry
                    value[key] = "mod-health"
                else:
                    visit(entry)
        elif isinstance(value, list):
            for entry in value:
                visit(entry)
    visit(result)
    return result


def rich_geometry_fixture():
    fixture = engine.dashboard.catalog(count=307)
    fixture["offline"] = True
    fixture["items"][0]["localAnalysis"] = {"inventoryComplete": True, "plugins": [{
        "name": "Observed.esp", "winner": True, "active": True, "winnerKnown": True,
        "header": {"lightEffective": True}, "eligibility": {"status": "already-light"},
        "errors": [], "masterIssues": []}]}
    fixture["items"][0]["update"].update(checkedAt=1800000000, expiresAt=9999999999,
        fileEvidence={"status": "matched", "basis": "installed-file-id", "installedFileIds": [144],
                      "installedFileName": "Fishing patch 1.0.zip", "targetFileId": 155,
                      "targetFileName": "Fishing patch 1.1.zip", "targetVersion": "2.0", "updated": True})
    fixture["items"][-1]["localAnalysis"] = {"inventoryComplete": True, "needsRecheck": True, "plugins": [{
        "name": "Needs evidence.esp", "winner": True, "active": True, "winnerKnown": True,
        "header": {"lightEffective": False}, "eligibility": {"status": "blocked"},
        "provenance": {"state": "source-changed"}, "errors": ["Fixture read issue"],
        "masterIssues": ["Fixture missing master"], "recordScanComplete": False}]}
    return fixture


def health_observer(candidate):
    def inspect(page):
        state = page.evaluate("""() => {
          const cells=[...document.querySelectorAll('#modList .mod-health')];
          const marked=cells.filter(cell=>cell.classList.contains('mod-health-text'));
          const plain=cells.filter(cell=>cell.children.length===0);
          const rich=cells.filter(cell=>cell.children.length>0);
          return {cells:cells.length,plain:plain.length,rich:rich.length,
            plainLabels:plain.map(cell=>cell.textContent),richLabels:rich.map(cell=>cell.textContent),
            allMarkersArePlain:marked.every(cell=>cell.children.length===0),marked:marked.length,
            richStillWrap:rich.every(cell=>getComputedStyle(cell).display==='none'||getComputedStyle(cell).display==='flex'),
            plainLayoutCorrect:plain.every(cell=>getComputedStyle(cell).display==='none'||getComputedStyle(cell).display===(cell.classList.contains('mod-health-text')?'block':'flex'))};
        }""")
        assert state["cells"] == 307 and state["plain"] > 0 and state["rich"] >= 2, state
        assert state["allMarkersArePlain"] and state["richStillWrap"] and state["plainLayoutCorrect"], state
        assert state["marked"] == (state["plain"] if candidate else 0), state
        return state
    return inspect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--released-ref", default=RELEASED)
    parser.add_argument("--unchanged-ref", default=UNCHANGED)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check-inputs", action="store_true")
    args = parser.parse_args()
    compiled, identities, patch = compile_variants(args.released_ref, args.unchanged_ref)
    if args.check_inputs:
        print(json.dumps({"browserExecuted": False, "variants": identities, "patch": patch}, indent=2))
        return
    args.output.mkdir(parents=True, exist_ok=True)
    status = git("status", "--porcelain", "--untracked-files=all").splitlines()
    assert not status, "Diagnostic source must be committed and clean"
    rich_fixture = rich_geometry_fixture()
    proof = {"schema": 1, "kind": "library-health-diagnostic", "ok": False,
             "scope": "Controlled full-content Chromium import/actions after load; does not replace release acceptance",
             "proofCommit": git("rev-parse", "HEAD"), "platform": platform.platform(),
             "variants": identities, "compiledSourcePatch": patch,
             "expectedPresentationDelta": {"plainHealthClassToken": "mod-health-text", "visiblePlainHealthDisplay": {"before": "flex", "after": "block"}},
             "richGeometryFixtureSha256": sha(json.dumps(rich_fixture, sort_keys=True)),
             "engine": {"path": "tests/visual_release_qa.py", "sha256": sha(Path(engine.__file__).read_text("utf-8"))},
             "diagnosticSourceSha256": {path: sha((ROOT / path).read_text("utf-8")) for path in (
                 "tests/library_health_diagnostic.py", "tests/library_grid_diagnostic.py", "tests/library_layout_diagnostic.py",
                 "tests/test_frontend_responsiveness.py", ".github/workflows/library-health-diagnostic.yml")},
             "sourceStatusAtStart": status, "mods": MODS, "rounds": ROUNDS}
    receipt = args.output / "library-health-diagnostic.json"
    def save():
        temporary = receipt.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(proof, indent=2), encoding="utf-8")
        temporary.replace(receipt)
    def checkpoint(value):
        proof["completeLibrary"] = value
        save()
    (args.output / "health-diagnostic-patch.json").write_text(json.dumps(patch, indent=2), encoding="utf-8")
    save()
    print("HEALTH source identity " + json.dumps({"proofCommit": proof["proofCommit"], "engine": proof["engine"],
        "variants": {name: {key: value for key, value in identity.items() if key != "sourceSha256"}
                     for name, identity in identities.items()}}, sort_keys=True), flush=True)
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
            proof["geometry"] = {name: geometry.capture_geometry(browser, compiled[name][0], args.output, name)
                                 for name in ("current013", "after")}
            proof["interactionGeometry"], proof["healthEvidence"] = {}, {}
            for name in ("current013", "after"):
                state, evidence = shared.capture_interaction_geometry(browser, compiled[name][0], args.output, name,
                    fixture=rich_fixture, observe=health_observer(name == "after"), require_shared_tracks=False)
                proof["interactionGeometry"][name], proof["healthEvidence"][name] = state, evidence
            proof["geometryDifferences"] = geometry.geometry_differences(protected_geometry(proof["geometry"]["current013"]), protected_geometry(proof["geometry"]["after"]))
            proof["interactionGeometryDifferences"] = geometry.geometry_differences(protected_geometry(proof["interactionGeometry"]["current013"]), protected_geometry(proof["interactionGeometry"]["after"]))
            save()
            differences = proof["geometryDifferences"] + proof["interactionGeometryDifferences"]
            if differences:
                print("HEALTH geometry mismatch " + json.dumps({"count": len(differences), "first": differences[:10]}, sort_keys=True), flush=True)
            assert not differences, "Plain-text layout changed protected geometry; benchmark not run"
            print("HEALTH geometry passed: 10 static and 13 rich-evidence transitions; original labels/chips retained; starting unchanged full11,207 benchmark", flush=True)
            proof["completeLibrary"] = engine.benchmark(browser, {name: html for name, (html, _) in compiled.items()}, MODS, ROUNDS, checkpoint)
            medians = proof["completeLibrary"]["medians"]
            proof["comparisonsPercent"] = {name: {metric: (medians["after"][metric] / value - 1) * 100
                                                  for metric, value in medians[name].items()}
                                              for name in ("v0121", "current013")}
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
