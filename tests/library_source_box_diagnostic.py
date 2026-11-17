"""Measure one source wrapper box against the accepted selector baseline."""
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
import library_selector_diagnostic as selectors  # noqa: E402
import library_grid_diagnostic as shared  # noqa: E402
import library_layout_diagnostic as geometry  # noqa: E402
import visual_release_qa as engine  # noqa: E402

git, sha = shared.git, shared.sha
MODS, ROUNDS = 11207, 3
ACCEPTED_HTML = "3ca40897a359a7e1a6e459f49082f0ded309ae033e12b80de9120c932aa9bf5c"
OVERRIDE = '\n@media(min-width:821px){body[data-layout="compact"][data-library-view="list"] .mod-source{display:contents}}\n'
BOUNDARIES = ((820, 900, "night", "list", "compact"), (821, 900, "night", "list", "compact"),
              (820, 900, "night", "gallery", "compact"), (821, 900, "night", "gallery", "compact"))

SOURCE_STATE = r"""() => {
 const rect=node=>{const r=node.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height}};
 const rows=[...document.querySelectorAll('#modList .mod-row')],viewport=document.getElementById('libraryViewport'),header=document.querySelector('.list-header');
 const samples=[rows[0],rows[Math.floor(rows.length/2)],rows.at(-1)];
 const cells=node=>[...node.children].map(cell=>({class:cell.className,box:rect(cell),display:getComputedStyle(cell).display}));
 const originals=window.__SOURCE_BOX_NODES__??=new Map(rows.map(row=>[row.dataset.id,[row,row.querySelector('.mod-source'),row.querySelector('.source-btn')]]));
 const sources=rows.map(row=>{
   const wrapper=row.querySelector('.mod-source'),anchor=wrapper.querySelector('.source-btn');
   if(wrapper.children.length!==1||anchor.parentElement!==wrapper||anchor.tagName!=='A')throw Error('Source wrapper structure changed');
   if(wrapper.hasAttribute('role')||wrapper.hasAttribute('tabindex')||wrapper.hasAttribute('aria-label'))throw Error('Source wrapper acquired semantic ownership');
   const ranges=[],walker=document.createTreeWalker(anchor,NodeFilter.SHOW_TEXT);let node;
   while(node=walker.nextNode()){const range=document.createRange();range.selectNodeContents(node);
     ranges.push({text:node.textContent,boxes:[...range.getClientRects()].map(r=>({x:r.x,y:r.y,width:r.width,height:r.height}))});}
   const style=getComputedStyle(anchor),old=originals.get(row.dataset.id);
   return {id:row.dataset.id,rowBox:rect(row),retained:old?.[0]===row&&old?.[1]===wrapper&&old?.[2]===anchor,
     wrapper:{class:wrapper.className,display:getComputedStyle(wrapper).display,box:rect(wrapper)},
     anchor:{box:rect(anchor),ranges,text:anchor.textContent,tag:anchor.tagName,
       attributes:Object.fromEntries([...anchor.attributes].map(attr=>[attr.name,attr.value])),
       styles:Object.fromEntries(['display','font','line-height','white-space','overflow','text-overflow','padding','border','color','background-color'].map(key=>[key,style.getPropertyValue(key)]))}};
 });
 return {view:document.body.dataset.libraryView,density:document.body.dataset.density,theme:document.body.dataset.theme,
   viewportWidth:innerWidth,viewportHeight:innerHeight,documentWidth:document.documentElement.scrollWidth,
   hiddenColumns:document.body.dataset.libraryHiddenColumns||'',
   rows:rows.length,ids:rows.map(row=>row.dataset.id),allLaidOut:rows.every(row=>row.getClientRects().length>0),
   noContentVisibility:rows.every(row=>getComputedStyle(row).contentVisibility==='visible'),
   nodes:document.querySelectorAll('#modList *').length,text:document.getElementById('modList').textContent,
   sampleCells:samples.map(cells),header:{box:rect(header),display:getComputedStyle(header).display,cells:cells(header),tracks:getComputedStyle(header).gridTemplateColumns},
   boxes:Object.fromEntries(['workbench','libraryViewport','controls','listFooter'].map(id=>[id,rect(document.getElementById(id))])),
   scroll:{clientWidth:viewport.clientWidth,clientHeight:viewport.clientHeight,scrollWidth:viewport.scrollWidth,scrollHeight:viewport.scrollHeight},sources};
}"""


def compile_variants():
    old, old_identities, selector_patch = selectors.compile_variants(selectors.RELEASED, selectors.UNCHANGED)
    accepted, files = old["after"]
    assert sha(accepted) == ACCEPTED_HTML, "Accepted selector baseline changed"
    css = selectors.expanded_source(engine.read(engine.WEB + "atelier.css", selectors.UNCHANGED), "atelier.css")
    assert accepted.count(css) == 1
    candidate = accepted.replace(css, css + OVERRIDE, 1)
    assert candidate.replace(css + OVERRIDE, css, 1) == accepted
    compiled = {"v0121": old["v0121"], "selector013": (accepted, files), "after": (candidate, dict(files))}
    patch = {"selectorBaselinePatch": selector_patch, "cssOverride": OVERRIDE,
             "acceptedCompiledHtmlSha256": ACCEPTED_HTML, "acceptedAtelierSha256": sha(css),
             "candidateAtelierSha256": sha(css + OVERRIDE),
             "injection": "Append one media rule inside the existing atelier stylesheet; no extra DOM/style element"}
    identities = {"v0121": old_identities["v0121"], "selector013": old_identities["after"],
                  "after": {**old_identities["after"], "compiledHtmlSha256": sha(candidate),
                            "compiledSourcePatchSha256": sha(json.dumps(patch, sort_keys=True))}}
    return compiled, identities, patch


def close_box(left, right):
    return all(abs(left[key] - right[key]) <= 0.5 for key in ("x", "y", "width", "height"))


def source_state(page, candidate):
    value = page.evaluate(SOURCE_STATE)
    assert value["rows"] == 307 and value["allLaidOut"] and value["noContentVisibility"], value
    assert value["documentWidth"] <= value["viewportWidth"] + 1
    eligible = value["view"] == "list" and value["viewportWidth"] >= 821
    for source in value["sources"]:
        assert source["retained"] and source["anchor"]["tag"] == "A", source
        wrapper, anchor = source["wrapper"], source["anchor"]
        if eligible and wrapper["display"] != "none":
            assert wrapper["display"] == ("contents" if candidate else "block"), source
            if candidate:
                assert all(coordinate == 0 for coordinate in wrapper["box"].values()), source
            else:
                assert close_box(wrapper["box"], anchor["box"]), source
            assert anchor["box"]["width"] > 0 and anchor["box"]["height"] > 0, source
    value["textSha256"] = sha(value.pop("text"))
    return value


def protected_geometry(states, candidate):
    """Declare only the empty wrapper box; never normalize the rendered anchor."""
    result = copy.deepcopy(states)
    for state in result.values():
        if state["view"] != "list" or state["viewportWidth"] < 821:
            continue
        def visit(value):
            if isinstance(value, dict):
                if value.get("class") == "mod-source" and value.get("display") != "none":
                    assert value["display"] == ("contents" if candidate else "block"), value
                    if candidate:
                        assert all(number == 0 for number in value["box"].values()), value
                    else:
                        assert value["box"]["width"] > 0 and value["box"]["height"] > 0, value
                    value["display"] = "declared-source-wrapper-box-omission"
                    value["box"] = None
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
        visit(state)
    return result


def capture_source_geometry(browser, html, output, name):
    context = browser.new_context(viewport={"width": 1920, "height": 1080}, reduced_motion="reduce")
    page = context.new_page();page.set_default_timeout(15000)
    errors, states = [], {}
    page.on("pageerror", lambda error: errors.append(str(error)))
    fixture = engine.dashboard.catalog(count=307);fixture["offline"] = True
    try:
        page.set_content(html, wait_until="load")
        page.evaluate("value=>window.modhunterImportCatalog(value)", fixture)
        engine.settle(page)
        for case in (*geometry.GEOMETRY_CASES, *BOUNDARIES):
            width, height, theme, view, density = case
            page.set_viewport_size({"width": width, "height": height})
            engine.set_dashboard_theme(page, theme)
            page.locator("#libraryGallery" if view == "gallery" else "#libraryList").click()
            if page.locator("body").get_attribute("data-density") != density:
                page.locator("#densityToggle").click()
            engine.settle(page);engine.scroll_dashboard_to_top(page)
            key = f"{width}-{theme}-{view}-{density}"
            states[key] = source_state(page, name == "after")
            if case in BOUNDARIES:
                data = page.screenshot(path=str(output / f"boundary-{name}-{key}.png"))
                states[key]["screenshotSha256"] = hashlib.sha256(data).hexdigest()
        page.set_viewport_size({"width": 1440, "height": 900})
        page.locator("#libraryList").click();engine.set_dashboard_theme(page, "night")
        for hidden in (True, False):
            page.locator("#libraryColumns").click()
            page.locator('[data-library-column="source"]').set_checked(not hidden)
            page.locator("#libraryColumnsDone").click();engine.settle(page);engine.scroll_dashboard_to_top(page)
            key = "source-hidden-1440" if hidden else "source-restored-1440"
            states[key] = source_state(page, name == "after")
            assert ("source" in states[key]["hiddenColumns"].split()) == hidden
            assert all((source["wrapper"]["display"] == "none") == hidden for source in states[key]["sources"])
            data = page.screenshot(path=str(output / f"source-columns-{name}-{key}.png"))
            states[key]["screenshotSha256"] = hashlib.sha256(data).hexdigest()
        assert not errors, errors
        return states
    finally:
        context.close()


def semantics_fixture():
    fixture = engine.dashboard.catalog(count=7);fixture["offline"] = True
    for index, provider in enumerate(("nexus", "github", "patreon", "creations", "website", "An unusually long original provider label for exact ellipsis evidence", "lookup")):
        item = fixture["items"][index]
        item["source"] = {"type": provider, "verified": provider != "lookup", "url": "" if provider == "lookup" else f"https://example.org/source-fixture/{index}"}
        item["nexusId"] = 0
    return fixture


def source_semantics(browser, html, output, name):
    """Actual focus, hit tests, AX link and native anchor activation; no timing instrumentation."""
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page();page.set_default_timeout(15000)
    cdp = context.new_cdp_session(page)
    errors, results = [], []
    action_context, last_state = {}, None
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.set_content(html, wait_until="load")
        page.evaluate("value=>window.modhunterImportCatalog(value)", semantics_fixture())
        engine.settle(page)
        context.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body="<!doctype html><title>Declared source fixture</title>"))
        for width, view in ((1440, "list"), (821, "list"), (820, "list"), (390, "list"), (1440, "gallery"), (390, "gallery")):
            page.set_viewport_size({"width": width, "height": 900})
            page.locator("#libraryGallery" if view == "gallery" else "#libraryList").click();engine.settle(page)
            for index in (0, 5, 6):
                action_context = {"width": width, "view": view, "item": index};last_state = None
                selector = f'#row-item-{index} .source-btn';anchor = page.locator(selector)
                anchor.scroll_into_view_if_needed()
                page.locator(f'#row-item-{index} .mod-bugs').focus()
                page.keyboard.press("Tab")
                assert anchor.evaluate("element=>element===document.activeElement"), (width, view, index)
                state = anchor.evaluate(r"""element=>{
                  const r=element.getBoundingClientRect(),rect=b=>({x:b.x,y:b.y,width:b.width,height:b.height});
                  const hits={center:[r.x+r.width/2,r.y+r.height/2],left:[r.x+1,r.y+r.height/2],right:[r.right-1,r.y+r.height/2],
                    top:[r.x+r.width/2,r.y+1],bottom:[r.x+r.width/2,r.bottom-1],topLeft:[r.x+1,r.y+1],topRight:[r.right-1,r.y+1],
                    bottomLeft:[r.x+1,r.bottom-1],bottomRight:[r.right-1,r.bottom-1]};
                  const ranges=[],walker=document.createTreeWalker(element,NodeFilter.SHOW_TEXT);let text;
                  while(text=walker.nextNode()){const range=document.createRange();range.selectNodeContents(text);ranges.push({text:text.textContent,boxes:[...range.getClientRects()].map(rect)})}
                  const style=getComputedStyle(element);
                  return {box:rect(r),ranges,text:element.textContent,href:element.href,title:element.title,
                    target:element.target,rel:element.rel,tabIndex:element.tabIndex,
                    hitPoints:hits,hitResults:Object.fromEntries(Object.entries(hits).map(([name,[x,y]])=>[name,document.elementFromPoint(x,y)?.closest('.source-btn')===element])),
                    cornerRadii:Object.fromEntries(['border-top-left-radius','border-top-right-radius','border-bottom-left-radius','border-bottom-right-radius'].map(key=>[key,style.getPropertyValue(key)])),
                    parentClass:element.parentElement.className,rows:document.querySelectorAll('#modList .mod-row').length};
                }""")
                last_state = state
                # Rounded rectangular corners can legitimately miss the link.
                # Keep their exact hit mask in the before/after comparison;
                # require the center and four edge midpoints to remain active.
                assert all(state["hitResults"][key] for key in ("center", "left", "right", "top", "bottom")), state
                assert state["rows"] == 7 and state["tabIndex"] == 0, state
                assert state["target"] == "_blank" and set(state["rel"].split()) == {"noopener", "noreferrer"}, state
                root = cdp.send("DOM.getDocument")["root"]["nodeId"]
                node_id = cdp.send("DOM.querySelector", {"nodeId": root, "selector": selector})["nodeId"]
                backend = cdp.send("DOM.describeNode", {"nodeId": node_id})["node"]["backendNodeId"]
                tree = cdp.send("Accessibility.getPartialAXTree", {"nodeId": node_id, "fetchRelatives": False})["nodes"]
                matches = [node for node in tree if node.get("backendDOMNodeId") == backend]
                assert len(matches) == 1 and not matches[0]["ignored"], matches
                node = matches[0];properties = {entry["name"]: entry["value"].get("value") for entry in node.get("properties", [])}
                assert node["role"]["value"] == "link" and node["name"]["value"] == state["text"], node
                assert properties.get("focusable") is True and properties.get("url") == state["href"], properties
                state["accessibility"] = {"role": node["role"]["value"], "name": node["name"]["value"], "url": properties["url"], "focusable": properties["focusable"]}
                page.evaluate("selector=>window.__SOURCE_ACTION_NODE__=document.querySelector(selector)", selector)
                old = page.evaluate("id=>__ARSENAL_QA__.verifyRow(id).state.lastOpened||null", f"item-{index}")
                with context.expect_page() as opened:
                    anchor.click()
                popup = opened.value;popup.wait_for_url(lambda url: url == state["href"], wait_until="domcontentloaded")
                assert popup.url == state["href"], (popup.url, state["href"])
                popup.close()
                updated = page.evaluate("id=>__ARSENAL_QA__.verifyRow(id).state.lastOpened||null", f"item-{index}")
                assert updated and updated != old
                assert anchor.evaluate("element=>element===window.__SOURCE_ACTION_NODE__")
                state["openedExactUrl"] = True;state["lastOpenedUpdated"] = True;state["anchorNodeRetained"] = True
                results.append({"width": width, "view": view, "item": index, **state})
            data = page.screenshot(path=str(output / f"source-actions-{name}-{width}-{view}.png"))
            results[-1]["screenshotSha256"] = hashlib.sha256(data).hexdigest()
        assert not errors, errors
        return {"cases": results, "pageErrors": errors, "fixtureSha256": sha(json.dumps(semantics_fixture(), sort_keys=True))}
    except BaseException as error:
        failure = {"error": str(error), "action": action_context, "lastState": last_state,
                   "completedActions": results, "pageErrors": errors}
        try:
            data = page.screenshot(path=str(output / f"source-actions-{name}-failure.png"))
            failure["screenshotSha256"] = hashlib.sha256(data).hexdigest()
        except Exception as screenshot_error:
            failure["screenshotError"] = str(screenshot_error)
        (output / f"source-actions-{name}-partial.json").write_text(json.dumps(failure, indent=2), encoding="utf-8")
        raise
    finally:
        context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check-inputs", action="store_true")
    args = parser.parse_args()
    compiled, identities, patch = compile_variants()
    if args.check_inputs:
        print(json.dumps({"browserExecuted": False, "variants": identities, "patch": patch}, indent=2));return
    args.output.mkdir(parents=True, exist_ok=True)
    status = git("status", "--porcelain", "--untracked-files=all").splitlines()
    assert not status, "Diagnostic source must be committed and clean"
    proof = {"schema": 1, "kind": "library-source-box-diagnostic", "ok": False,
        "scope": "One source-wrapper layout box; unchanged full workload and original clocks; not release acceptance",
        "proofCommit": git("rev-parse", "HEAD"), "platform": platform.platform(), "variants": identities,
        "compiledSourcePatch": patch, "mods": MODS, "rounds": ROUNDS, "sourceStatusAtStart": status,
        "expectedPresentationDelta": "Only the source wrapper's own empty rectangle/display in compact List >=821px; every anchor, text, action, row and scroll box remains protected",
        "acceptedSelectorEvidence": {"commit": "fa86978084e29adab97a568dac9b6409a63334d9", "runId": 44014095979,
            "artifactId": 14299360016, "artifactZipSha256": "7f1aefe593c1edf83b0bd98f7fdf46dc75a6ea9c98f0b4a7778a19d640eb1de8"},
        "engine": {"path": "tests/visual_release_qa.py", "sha256": sha(Path(engine.__file__).read_text("utf-8"))},
        "diagnosticSourceSha256": {path: sha((ROOT / path).read_text("utf-8")) for path in (
            "tests/library_source_box_diagnostic.py", "tests/library_selector_diagnostic.py", "tests/library_grid_diagnostic.py",
            "tests/library_layout_diagnostic.py", "tests/test_frontend_responsiveness.py", ".github/workflows/library-source-box-diagnostic.yml")}}
    receipt = args.output / "library-source-box-diagnostic.json"
    def save():
        temporary = receipt.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(proof, indent=2), encoding="utf-8");temporary.replace(receipt)
    def checkpoint(value):
        proof["completeLibrary"] = value;save()
    for name, (html, _) in compiled.items():
        (args.output / f"{name}.html").write_text(html, encoding="utf-8")
    (args.output / "source-box-patch.json").write_text(json.dumps(patch, indent=2), encoding="utf-8")
    save()
    print("SOURCE BOX identities " + json.dumps({"proofCommit": proof["proofCommit"], "engine": proof["engine"],
        "variants": {name: {key: value for key, value in identity.items() if key != "sourceSha256"} for name, identity in identities.items()}}, sort_keys=True), flush=True)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as runtime:
        launch = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
        executable = shutil.which("chromium") or shutil.which("chromium-browser")
        if executable:launch["executable_path"] = executable
        browser = runtime.chromium.launch(**launch)
        proof["browserVersion"] = browser.version;proof["browserExecutable"] = executable or "Playwright bundled Chromium"
        try:
            for field in ("geometry", "interactionGeometry", "interactionSourceGeometry", "sourceGeometry", "sourceSemantics"):
                proof[field] = {}
            for name in ("selector013", "after"):
                html = compiled[name][0]
                proof["geometry"][name] = geometry.capture_geometry(browser, html, args.output, name);save()
                states, sources = shared.capture_interaction_geometry(browser, html, args.output, name,
                    observe=lambda page, candidate=name == "after": source_state(page, candidate), require_shared_tracks=False)
                proof["interactionGeometry"][name] = states;proof["interactionSourceGeometry"][name] = sources;save()
                proof["sourceGeometry"][name] = capture_source_geometry(browser, html, args.output, name);save()
                proof["sourceSemantics"][name] = source_semantics(browser, html, args.output, name);save()
            proof["geometryDifferences"] = {field: geometry.geometry_differences(
                protected_geometry(proof[field]["selector013"], False), protected_geometry(proof[field]["after"], True))
                for field in ("geometry", "interactionGeometry", "sourceGeometry", "interactionSourceGeometry")}
            proof["semanticsDifferences"] = geometry.geometry_differences(proof["sourceSemantics"]["selector013"], proof["sourceSemantics"]["after"])
            save()
            differences = [entry for entries in proof["geometryDifferences"].values() for entry in entries] + proof["semanticsDifferences"]
            if differences:print("SOURCE BOX mismatch " + json.dumps({"count": len(differences), "first": differences[:10]}), flush=True)
            assert not differences, "Source box changed protected geometry, text or action semantics; benchmark not run"
            print("SOURCE BOX 23 existing geometry states, 4 boundary states, source hide/restore and 18 real source actions per variant passed; starting unchanged full 11,207 benchmark", flush=True)
            proof["completeLibrary"] = engine.benchmark(browser, {name: html for name, (html, _) in compiled.items()}, MODS, ROUNDS, checkpoint)
            medians = proof["completeLibrary"]["medians"]
            proof["comparisonsPercent"] = {name: {metric: (medians["after"][metric] / value - 1) * 100 for metric, value in medians[name].items()}
                                           for name in ("v0121", "selector013")}
            assert git("rev-parse", "HEAD") == proof["proofCommit"] and not git("status", "--porcelain", "--untracked-files=all")
            proof["ok"] = True
        except BaseException as error:
            proof["error"] = str(error);raise
        finally:
            browser.close();proof["sourceStatusAtEnd"] = git("status", "--porcelain", "--untracked-files=all").splitlines()
            if proof["sourceStatusAtEnd"]:proof["ok"] = False
            save()
    print(json.dumps({"ok": proof["ok"], "receipt": str(receipt), "medians": proof["completeLibrary"]["medians"], "comparisonsPercent": proof["comparisonsPercent"]}, indent=2))


if __name__ == "__main__":
    main()
