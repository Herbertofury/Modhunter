"""Measure trace-identified equivalent selectors; preserve complete rows and geometry."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import library_grid_diagnostic as shared  # noqa: E402
import library_layout_diagnostic as geometry  # noqa: E402
import visual_release_qa as engine  # noqa: E402

git, sha = shared.git, shared.sha
RELEASED, UNCHANGED = shared.RELEASED, shared.UNCHANGED
MODS, ROUNDS = 11207, 3
COLUMNS = (("art", "mod-art", "preview-heading"), ("priority", "mod-priority", "priority-heading"),
           ("version", "mod-version", "version-heading"), ("status", "mod-assessment", "status-heading"),
           ("health", "mod-health", "health-heading"), ("bugs", "mod-bugs", "bugs-heading"),
           ("source", "mod-source", "source-heading"))
OTHER_PAIRS = (
    ("locate-light", "style.css", 'body[data-theme="snow"] ', (".mod-inline-locate", ".workshop-inline-locate")),
    ("activity-fields", "style.css", ".activity-toolbar ", ("input", "select")),
    ("activity-fields-light", "style.css", 'body[data-theme="snow"] .activity-toolbar ', ("input", "select")),
    ("workshop-fields-dark", "atelier.css", 'body[data-theme="night"] .workshop-filter ', ("input", "select")),
    ("workshop-fields-light", "atelier.css", 'body[data-theme="snow"] .workshop-filter ', ("input", "select")),
    ("workshop-fields-compact", "atelier.css", 'body[data-layout="compact"] .workshop-filter ', ("input", "select")),
    ("activity-fields-compact", "atelier.css", 'body[data-layout="compact"]:not(.realm-route) .activity-toolbar ', ("input", "select")),
)


def selectors():
    result = []
    for column, cell, heading in COLUMNS:
        prefix = f'body[data-layout="compact"][data-library-view="list"][data-library-hidden-columns~="{column}"] '
        result.append({"id": column, "kind": "column", "source": "atelier.css", "prefix": prefix,
                       "branches": [f".{cell}", f".{heading}"]})
    for key, source, prefix, branches in OTHER_PAIRS:
        result.append({"id": key, "kind": "literal-pair", "source": source, "prefix": prefix, "branches": list(branches)})
    for pair in result:
        branches = pair["branches"]
        assert len(branches) == 2
        # Every branch is one literal class or one literal type. Equal branch
        # specificity makes expansion of :is() preserve the exact cascade.
        assert all(re.fullmatch(r"\.[a-z][a-z0-9-]*", branch) for branch in branches) or all(
            re.fullmatch(r"[a-z][a-z0-9-]*", branch) for branch in branches), pair
        pair["branchSpecificity"] = [0, 1, 0] if branches[0].startswith(".") else [0, 0, 1]
        pair["before"] = pair["prefix"] + ":is(" + ",".join(branches) + ")"
        pair["after"] = ",".join(pair["prefix"] + branch for branch in branches)
    return result


def expanded_source(source, source_name=None):
    pairs = [pair for pair in selectors() if source_name is None or pair["source"] == source_name]
    def replace_exact(value, before, after):
        # A shorter literal selector can be a suffix of a longer prefixed one.
        # Match a complete selector-list entry, never such a suffix.
        pattern = re.compile(r"(^|[{},])(\s*)" + re.escape(before) + r"(?=\s*[,{}])", re.MULTILINE)
        matches = list(pattern.finditer(value))
        assert len(matches) == 1, {"selector": before, "matches": len(matches), "source": source_name}
        return pattern.sub(lambda match: match.group(1) + match.group(2) + after, value, count=1)
    result = source
    for pair in pairs:
        result = replace_exact(result, pair["before"], pair["after"])
    restored = result
    for pair in reversed(pairs):
        restored = replace_exact(restored, pair["after"], pair["before"])
    assert restored == source, "Only the fourteen exact selector lists may change"
    return result


def compile_variants(released_ref, unchanged_ref):
    refs = {"v0121": git("rev-parse", released_ref + "^{commit}"),
            "current013": git("rev-parse", unchanged_ref + "^{commit}")}
    compiled = {name: engine.compile_dashboard(ref) for name, ref in refs.items()}
    html, files = compiled["current013"]
    compiled["after"] = (expanded_source(html), dict(files))
    css = {name: engine.read(engine.WEB + name, refs["current013"]) for name in ("atelier.css", "style.css")}
    patch = {"selectors": selectors(), "sources": {name: {"originalSha256": sha(value),
             "modifiedSha256": sha(expanded_source(value, name))} for name, value in css.items()},
             "invariant": "Identical selector specificity, matching sets, declaration, importance and source order"}
    identities = {name: {"sourceCommit": refs.get(name, refs["current013"]), "sourceSha256": sources,
                        "compiledHtmlSha256": sha(value),
                        "compiledSourcePatchSha256": sha(json.dumps(patch, sort_keys=True)) if name == "after" else None}
                  for name, (value, sources) in compiled.items()}
    return compiled, identities, patch


def selector_equivalence(browser, html):
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.set_content(html, wait_until="load")
        fixture = engine.dashboard.catalog(count=307)
        fixture["offline"] = True
        page.evaluate("fixture=>window.modhunterImportCatalog(fixture)", fixture)
        engine.settle(page)
        result = page.evaluate("""pairs => {
          const body=document.body,keys=['data-layout','data-library-view','data-library-hidden-columns','data-theme'];
          const original=keys.map(key=>body.getAttribute(key)),originalRealm=body.classList.contains('realm-route');
          const nodes=[...document.querySelectorAll('*')],ids=new Map(nodes.map((node,index)=>[node,index]));
          const columns=pairs.filter(pair=>pair.kind==='column'),other=pairs.filter(pair=>pair.kind==='literal-pair');
          let cases=0,otherCases=0;const matches=Object.fromEntries(columns.map(pair=>[pair.id,0]));
          const probeMatches=Object.fromEntries(other.map(pair=>[pair.id,0]));
          let probe=null;
          const equalMatches=(pair,detail)=>{
            const before=[...document.querySelectorAll(pair.before)],after=[...document.querySelectorAll(pair.after)];
            if(before.length!==after.length||before.some((node,index)=>node!==after[index]))throw Error('Selector mismatch '+JSON.stringify({id:pair.id,...detail}));
            return before;
          };
          const modes=[['compact','list'],['compact','gallery'],['legacy','list']];
          try{
            for(const [layout,view] of modes){
              body.setAttribute(keys[0],layout);body.setAttribute(keys[1],view);
              for(let mask=0;mask<(1<<columns.length);mask++){
                const hidden=columns.filter((_,index)=>mask&(1<<index)).map(pair=>pair.id).join(' ');
                if(mask)body.setAttribute(keys[2],hidden);else body.removeAttribute(keys[2]);
                for(const pair of columns){
                  const before=equalMatches(pair,{layout,view,hidden});
                  if(before.some(node=>!ids.has(node)))throw Error('Column matching created nodes');
                  if(layout==='compact'&&view==='list'&&hidden.split(' ').includes(pair.id)&&before.length!==308)throw Error('Missing complete cell/header coverage '+pair.id);
                  if((layout!=='compact'||view!=='list')&&before.length)throw Error('Inactive layout selector matched '+pair.id);
                  matches[pair.id]+=before.length;cases++;
                }
              }
            }
            // Inert probes make both branches nonvacuous even though offline
            // catalog fixtures intentionally do not expose native locate buttons.
            probe=document.createElement('div');probe.inert=true;
            for(const className of ['mod-inline-locate','workshop-inline-locate']){
              const button=document.createElement('button');button.className=className;probe.append(button);
            }
            for(const className of ['workshop-filter','activity-toolbar']){
              const container=document.createElement('div');container.className=className;
              for(const tag of ['input','select','button'])container.append(document.createElement(tag));
              probe.append(container);
            }
            body.append(probe);
            for(const theme of ['night','snow'])for(const layout of ['compact','legacy'])for(const view of ['list','gallery'])for(const realm of [false,true]){
              body.setAttribute(keys[0],layout);body.setAttribute(keys[1],view);body.setAttribute(keys[3],theme);
              body.classList.toggle('realm-route',realm);
              for(const pair of other){
                const before=equalMatches(pair,{theme,layout,view,realm});
                const expected={
                  'locate-light':theme==='snow','activity-fields':true,'activity-fields-light':theme==='snow',
                  'workshop-fields-dark':theme==='night','workshop-fields-light':theme==='snow',
                  'workshop-fields-compact':layout==='compact','activity-fields-compact':layout==='compact'&&!realm
                }[pair.id]?2:0;
                const count=before.filter(node=>probe.contains(node)).length;
                if(count!==expected)throw Error('Missing literal branch coverage '+JSON.stringify({id:pair.id,theme,layout,view,realm,count,expected}));
                probeMatches[pair.id]+=count;otherCases++;
              }
            }
          }finally{
            probe?.remove();body.classList.toggle('realm-route',originalRealm);
            keys.forEach((key,index)=>{if(original[index]===null)body.removeAttribute(key);else body.setAttribute(key,original[index])});
          }
          const finalNodes=[...document.querySelectorAll('*')];
          return {cases,matches,rows:document.querySelectorAll('#modList .mod-row').length,
            otherCases,probeMatches,nodes:nodes.length,
            allNodeIdentitiesRetained:nodes.length===finalNodes.length&&nodes.every((node,index)=>finalNodes[index]===node)};
        }""", selectors())
        assert result["cases"] == 3 * 128 * 7 and result["rows"] == 307
        assert result["allNodeIdentitiesRetained"] and all(value == 64 * 308 for value in result["matches"].values())
        assert result["otherCases"] == 16 * 7 and all(value > 0 for value in result["probeMatches"].values())
        engine.settle(page)
        assert not errors, errors
        return {**result, "pageErrors": errors, "fixtureSha256": sha(json.dumps(fixture, sort_keys=True)),
                "scope": "All 128 column subsets in 3 modes, plus 7 measured literal pairs across 16 theme/layout/view/route states; exact matching node identities"}
    finally:
        context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check-inputs", action="store_true")
    args = parser.parse_args()
    compiled, identities, patch = compile_variants(RELEASED, UNCHANGED)
    if args.check_inputs:
        print(json.dumps({"browserExecuted": False, "variants": identities, "patch": patch}, indent=2))
        return
    args.output.mkdir(parents=True, exist_ok=True)
    status = git("status", "--porcelain", "--untracked-files=all").splitlines()
    assert not status, "Diagnostic source must be committed and clean"
    proof = {"schema": 1, "kind": "library-selector-diagnostic", "ok": False,
        "scope": "Controlled equivalent-selector/full-content diagnosis; does not replace release acceptance",
        "proofCommit": git("rev-parse", "HEAD"), "platform": platform.platform(), "variants": identities,
        "compiledSourcePatch": patch, "mods": MODS, "rounds": ROUNDS, "sourceStatusAtStart": status,
        "engine": {"path": "tests/visual_release_qa.py", "sha256": sha(Path(engine.__file__).read_text("utf-8"))},
        "diagnosticSourceSha256": {path: sha((ROOT / path).read_text("utf-8")) for path in (
            "tests/library_selector_diagnostic.py", "tests/library_grid_diagnostic.py", "tests/library_layout_diagnostic.py",
            "tests/test_frontend_responsiveness.py", ".github/workflows/library-selector-diagnostic.yml")}}
    receipt = args.output / "library-selector-diagnostic.json"
    def save():
        temporary = receipt.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(proof, indent=2), encoding="utf-8")
        temporary.replace(receipt)
    def checkpoint(value):
        proof["completeLibrary"] = value
        save()
    (args.output / "selector-diagnostic-patch.json").write_text(json.dumps(patch, indent=2), encoding="utf-8")
    for name, (html, _) in compiled.items():
        (args.output / f"{name}.html").write_text(html, encoding="utf-8")
    save()
    print("SELECTORS exact source identity " + json.dumps({"proofCommit": proof["proofCommit"], "engine": proof["engine"],
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
            proof["selectorEquivalence"] = selector_equivalence(browser, compiled["after"][0])
            save()
            proof["geometry"] = {name: geometry.capture_geometry(browser, compiled[name][0], args.output, name)
                                 for name in ("current013", "after")}
            proof["interactionGeometry"] = {name: shared.capture_interaction_geometry(browser, compiled[name][0], args.output, name,
                require_shared_tracks=False)[0] for name in ("current013", "after")}
            proof["geometryDifferences"] = geometry.geometry_differences(proof["geometry"]["current013"], proof["geometry"]["after"])
            proof["interactionGeometryDifferences"] = geometry.geometry_differences(proof["interactionGeometry"]["current013"], proof["interactionGeometry"]["after"])
            save()
            differences = proof["geometryDifferences"] + proof["interactionGeometryDifferences"]
            if differences:
                print("SELECTORS geometry mismatch " + json.dumps({"count": len(differences), "first": differences[:10]}), flush=True)
            assert not differences, "Equivalent selectors changed geometry; benchmark not run"
            print("SELECTORS all 2800 matching-set assertions and 23 geometry cases passed; starting unchanged full 11,207 benchmark", flush=True)
            proof["completeLibrary"] = engine.benchmark(browser, {name: html for name, (html, _) in compiled.items()}, MODS, ROUNDS, checkpoint)
            medians = proof["completeLibrary"]["medians"]
            proof["comparisonsPercent"] = {name: {metric: (medians["after"][metric] / value - 1) * 100
                for metric, value in medians[name].items()} for name in ("v0121", "current013")}
            assert git("rev-parse", "HEAD") == proof["proofCommit"] and not git("status", "--porcelain", "--untracked-files=all")
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
    print(json.dumps({"ok": proof["ok"], "receipt": str(receipt), "medians": proof["completeLibrary"]["medians"],
                      "comparisonsPercent": proof["comparisonsPercent"]}, indent=2))


if __name__ == "__main__":
    main()
