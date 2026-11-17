"""Measure native connected-row moves independently of CSS candidates."""
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
import library_grid_diagnostic as shared  # noqa: E402
import library_layout_diagnostic as geometry  # noqa: E402
import visual_release_qa as engine  # noqa: E402

git, sha = shared.git, shared.sha
RELEASED, UNCHANGED = shared.RELEASED, shared.UNCHANGED
MODS, ROUNDS = 11207, 3
BEFORE = "    for(const entry of next){if(entry.node!==cursor)node.insertBefore(entry.node,cursor);cursor=entry.node.nextElementSibling;}"
AFTER = "    for(const entry of next){if(entry.node!==cursor){if(typeof node.moveBefore==='function'&&node.isConnected&&entry.node.parentElement===node&&entry.node.getRootNode()===node.getRootNode())node.moveBefore(entry.node,cursor);else node.insertBefore(entry.node,cursor);}cursor=entry.node.nextElementSibling;}"
RETAINED = "   const focused=document.activeElement,retained=new Set(next.map(entry=>entry.node));"
CAPTURE = RETAINED + "\n   const savedSelection=captureLibraryTextSelection(node,retained);"
FOCUS = "\n   if(focused?.isConnected&&node.contains(focused)&&document.activeElement!==focused)focused.focus({preventScroll:true});"
RESTORE = FOCUS + "\n   restoreLibraryTextSelection(node,savedSelection);"
HELPERS = """function captureLibraryTextSelection(list,retained){
 const selection=window.getSelection?.();
 if(!selection||selection.rangeCount!==1||selection.isCollapsed||typeof selection.setBaseAndExtent!=='function')return null;
 const {anchorNode,anchorOffset,focusNode,focusOffset}=selection;
 const rowFor=node=>(node?.nodeType===1?node:node?.parentElement)?.closest?.('.mod-row');
 const row=rowFor(anchorNode);
 if(!row||!row.isConnected||row!==rowFor(focusNode)||!retained.has(row)||row.parentElement!==list||!row.contains(anchorNode)||!row.contains(focusNode))return null;
 return{selection,row,anchorNode,anchorOffset,focusNode,focusOffset};
}
function restoreLibraryTextSelection(list,saved){
 if(!saved)return;
 const {selection,row,anchorNode,anchorOffset,focusNode,focusOffset}=saved;
 if(!row.isConnected||row.parentElement!==list||!anchorNode.isConnected||!focusNode.isConnected||!row.contains(anchorNode)||!row.contains(focusNode))return;
 if(selection.anchorNode===anchorNode&&selection.anchorOffset===anchorOffset&&selection.focusNode===focusNode&&selection.focusOffset===focusOffset)return;
 const length=node=>node.nodeType===3||node.nodeType===4||node.nodeType===8?node.length:node.childNodes.length;
 if(anchorOffset>=0&&focusOffset>=0&&anchorOffset<=length(anchorNode)&&focusOffset<=length(focusNode))selection.setBaseAndExtent(anchorNode,anchorOffset,focusNode,focusOffset);
}
"""


def moved_source(source):
    assert source.count(BEFORE) == 1, "Only the retained Library reorder loop may change"
    assert source.count(RETAINED) == source.count(FOCUS) == source.count("function setRows(") == 1
    result = source.replace(BEFORE, AFTER, 1)
    result = result.replace(RETAINED, CAPTURE, 1).replace(FOCUS, RESTORE, 1)
    result = result.replace("function setRows(", HELPERS + "function setRows(", 1)
    assert result.replace(HELPERS, "", 1).replace(CAPTURE, RETAINED, 1).replace(RESTORE, FOCUS, 1).replace(AFTER, BEFORE, 1) == source
    return result


def compile_variants(released_ref, unchanged_ref):
    refs = {"v0121": git("rev-parse", released_ref + "^{commit}"),
            "current013": git("rev-parse", unchanged_ref + "^{commit}")}
    compiled = {name: engine.compile_dashboard(ref) for name, ref in refs.items()}
    current, sources = compiled["current013"]
    compiled["after"] = (moved_source(current), dict(sources))
    raw = engine.read(engine.WEB + "app.js", refs["current013"])
    patch = {"before": BEFORE, "after": AFTER, "beforeCapture": RETAINED, "afterCapture": CAPTURE,
             "beforeRestore": FOCUS, "afterRestore": RESTORE, "helpers": HELPERS,
             "originalAppSha256": sha(raw), "modifiedAppSha256": sha(moved_source(raw))}
    identities = {name: {"sourceCommit": refs.get(name, refs["current013"]),
                         "sourceSha256": files, "compiledHtmlSha256": sha(html),
                         "compiledSourcePatchSha256": sha(json.dumps(patch, sort_keys=True)) if name == "after" else None}
                  for name, (html, files) in compiled.items()}
    return compiled, identities, patch


def selection_guard_checks():
    result = subprocess.check_output(["node", str(ROOT / "tests/library_move_guards.test.cjs")], input=HELPERS, text=True, cwd=ROOT)
    checks = json.loads(result)
    assert checks["ok"] and all(check["passed"] for check in checks["checks"]), checks
    return checks


def preservation_probe(browser, html, output, name, *, candidate=False, force_fallback=False):
    """Actual Chromium focus, CSS animation, ranges and connection callbacks."""
    from playwright.sync_api import expect
    fixture = engine.dashboard.catalog(count=307)
    fixture["offline"] = True
    fixture["items"][-1]["name"] = "Excluded by search"
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="no-preference")
    page = context.new_page()
    page.set_default_timeout(15000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.set_content(html, wait_until="load")
        support = page.evaluate("typeof Element.prototype.moveBefore==='function'")
        if force_fallback:
            page.evaluate("Object.defineProperty(Element.prototype,'moveBefore',{value:undefined,writable:true,configurable:true})")
        page.evaluate("value=>window.modhunterImportCatalog(value)", fixture)
        engine.settle(page)
        # Select through the actual application interaction, then retain focus
        # while invoking the actual sort control's change handler below.
        page.locator("#row-item-19 .mod-priority").click()
        page.evaluate("""() => {
          const row=document.getElementById('row-item-19'),list=document.getElementById('modList');
          const counts={connected:0,disconnected:0,connectedMoves:0,nativeMoves:0,selectedNativeMoves:0,guardViolations:0,detachedInsertions:0,blur:0};
          const nativeMove=Element.prototype.moveBefore,nativeInsert=Element.prototype.insertBefore;
          if(typeof nativeMove==='function')Element.prototype.moveBefore=function(node,before){
            if(this===list){counts.nativeMoves++;if(node===row)counts.selectedNativeMoves++;
              if(!this.isConnected||node.parentElement!==this||node.getRootNode()!==this.getRootNode())counts.guardViolations++}
            return nativeMove.call(this,node,before);
          };
          Element.prototype.insertBefore=function(node,before){
            if(this===list&&node.id==='row-item-306'&&node.parentElement!==this)counts.detachedInsertions++;
            return nativeInsert.call(this,node,before);
          };
          customElements.define('mh-move-probe',class extends HTMLElement{
            connectedCallback(){counts.connected++}
            disconnectedCallback(){counts.disconnected++}
            connectedMoveCallback(){counts.connectedMoves++}
          });
          const sentinel=document.createElement('mh-move-probe');sentinel.hidden=true;row.append(sentinel);
          const style=document.createElement('style'),nonce=document.querySelector('style[nonce]')?.nonce;
          if(nonce)style.nonce=nonce;
          style.textContent='@keyframes mh-move-preservation{from{opacity:.99}to{opacity:1}}#row-item-19{animation:mh-move-preservation 60s linear infinite!important}';
          document.head.append(style);
          const rows=[...list.querySelectorAll('.mod-row')],button=row.querySelector('.inspect-btn');
          window.__MOVE_PROBE__={row,list,rows,button,counts,last:rows.at(-1),initialIds:rows.map(node=>node.dataset.id)};
        }""")
        engine.settle(page)
        before = page.evaluate("""() => {
          const p=window.__MOVE_PROBE__,animation=p.row.getAnimations().find(value=>value.animationName==='mh-move-preservation');
          if(!animation)throw Error('The real CSS animation did not start');
          animation.currentTime=12000;p.animation=animation;p.animationTime=Number(animation.currentTime);
          for(const key of Object.keys(p.counts))p.counts[key]=0;
          p.button.focus({preventScroll:true});p.button.addEventListener('blur',()=>p.counts.blur++);
          return {focused:document.activeElement===p.button,selected:p.row.classList.contains('aether-target'),
            rows:p.rows.length,animationTime:p.animationTime,animationState:animation.playState};
        }""")
        assert before["focused"] and before["selected"] and before["rows"] == 307 and before["animationState"] == "running", before
        page.evaluate("""() => {const control=document.getElementById('sortSelect');control.value='name';control.dispatchEvent(new Event('change',{bubbles:true}))}""")
        engine.settle(page)
        focus_state = page.evaluate("""() => {
          const p=window.__MOVE_PROBE__,rows=[...p.list.querySelectorAll('.mod-row')];
          const animation=p.row.getAnimations().find(value=>value.animationName==='mh-move-preservation');
          return {focused:document.activeElement===p.button,selected:p.row.classList.contains('aether-target'),
            identity:p.rows.every(row=>document.getElementById(row.id)===row),rows:rows.length,
            allLaidOut:rows.every(row=>row.getClientRects().length>0),ids:rows.map(row=>row.dataset.id),
            sameAnimation:animation===p.animation,animationTime:Number(animation?.currentTime||0),
            animationState:animation?.playState,counts:{...p.counts}};
        }""")
        expected = page.evaluate("value=>[...value.items].sort((a,b)=>a.name.localeCompare(b.name)).map(item=>item.id)", fixture)
        assert focus_state["ids"] == expected and focus_state["identity"] and focus_state["allLaidOut"] and focus_state["rows"] == 307, focus_state
        assert focus_state["focused"] and focus_state["selected"], focus_state
        selection_before = page.evaluate("""() => {
          const p=window.__MOVE_PROBE__,text=p.row.querySelector('.mod-title').firstChild,range=document.createRange(),selection=getSelection();
          range.selectNodeContents(text);selection.removeAllRanges();selection.addRange(range);
          p.selection={anchor:selection.anchorNode,focus:selection.focusNode,anchorOffset:selection.anchorOffset,focusOffset:selection.focusOffset,text:selection.toString()};
          return p.selection.text;
        }""")
        assert selection_before == "Unique Fixture 19", selection_before
        page.evaluate("""() => {const control=document.getElementById('sortSelect');control.value='priority';control.dispatchEvent(new Event('change',{bubbles:true}))}""")
        engine.settle(page)
        selection_state = page.evaluate("""() => {
          const p=window.__MOVE_PROBE__,selection=getSelection(),rows=[...p.list.querySelectorAll('.mod-row')];
          return {text:selection.toString(),sameAnchor:selection.anchorNode===p.selection.anchor,sameFocus:selection.focusNode===p.selection.focus,
            sameOffsets:selection.anchorOffset===p.selection.anchorOffset&&selection.focusOffset===p.selection.focusOffset,
            selected:p.row.classList.contains('aether-target'),identity:p.rows.every(row=>document.getElementById(row.id)===row),
            orderRestored:rows.map(row=>row.dataset.id).join('|')===p.initialIds.join('|'),counts:{...p.counts}};
        }""")
        assert selection_state["selected"] and selection_state["identity"] and selection_state["orderRestored"], selection_state
        page.evaluate("""() => {
          const p=window.__MOVE_PROBE__,text=p.row.querySelector('.mod-title').firstChild,selection=getSelection();
          selection.setBaseAndExtent(text,text.length,text,0);
          p.backward={anchor:selection.anchorNode,focus:selection.focusNode,anchorOffset:selection.anchorOffset,focusOffset:selection.focusOffset,text:selection.toString()};
          const control=document.getElementById('sortSelect');control.value='name';control.dispatchEvent(new Event('change',{bubbles:true}));
        }""")
        engine.settle(page)
        backward_state = page.evaluate("""() => {
          const p=window.__MOVE_PROBE__,selection=getSelection();return {text:selection.toString(),expected:p.backward.text,
            sameAnchor:selection.anchorNode===p.backward.anchor,sameFocus:selection.focusNode===p.backward.focus,
            sameOffsets:selection.anchorOffset===p.backward.anchorOffset&&selection.focusOffset===p.backward.focusOffset,
            anchorOffset:selection.anchorOffset,focusOffset:selection.focusOffset,
            selected:p.row.classList.contains('aether-target'),identity:p.rows.every(row=>document.getElementById(row.id)===row)};
        }""")
        assert backward_state["selected"] and backward_state["identity"], backward_state
        if candidate:
            assert selection_state["text"] == selection_before and selection_state["sameAnchor"] and selection_state["sameFocus"] and selection_state["sameOffsets"], selection_state
            assert backward_state["text"] == backward_state["expected"] == selection_before and backward_state["sameAnchor"] and backward_state["sameFocus"] and backward_state["sameOffsets"], backward_state
            assert backward_state["anchorOffset"] > backward_state["focusOffset"], backward_state
        if candidate and not force_fallback:
            assert support, "Runtime must expose the actual native move API for this experiment"
            assert focus_state["sameAnimation"] and focus_state["animationTime"] >= before["animationTime"] and focus_state["animationState"] == "running", focus_state
            assert focus_state["counts"]["blur"] == 0, focus_state
            assert selection_state["counts"]["nativeMoves"] > 0 and selection_state["counts"]["selectedNativeMoves"] > 0 and selection_state["counts"]["connectedMoves"] > 0, selection_state
            assert selection_state["counts"]["disconnected"] == 0 and selection_state["counts"]["connected"] == 0 and selection_state["counts"]["guardViolations"] == 0, selection_state
        else:
            assert selection_state["counts"]["nativeMoves"] == 0 and selection_state["counts"]["disconnected"] > 0, selection_state
        page.evaluate("""() => {const control=document.getElementById('sortSelect');control.value='priority';control.dispatchEvent(new Event('change',{bubbles:true}))}""")
        engine.settle(page)
        # Existing cold-row restoration must still insert detached rows, even
        # when native atomic moves are available for connected retained rows.
        page.locator("#search").fill("Unique Fixture")
        expect(page.locator("#modList .mod-row")).to_have_count(306)
        detached = page.evaluate("!window.__MOVE_PROBE__.last.isConnected")
        assert detached
        page.locator("#search").fill("")
        expect(page.locator("#modList .mod-row")).to_have_count(307)
        engine.settle(page)
        restoration = page.evaluate("""() => {const p=window.__MOVE_PROBE__;return {sameNode:document.getElementById('row-item-306')===p.last,connected:p.last.isConnected,detachedInsertions:p.counts.detachedInsertions,guardViolations:p.counts.guardViolations}}""")
        assert restoration["sameNode"] and restoration["connected"] and restoration["detachedInsertions"] > 0 and restoration["guardViolations"] == 0, restoration
        image = page.screenshot(path=str(output / f"move-preservation-{name}.png"))
        assert not errors, errors
        return {"nativeApiAvailable": support, "forceUnsupportedFallback": force_fallback,
                "fixtureSha256": sha(json.dumps(fixture, sort_keys=True)), "before": before,
                "focusedSort": focus_state, "textSelectionSort": selection_state,
                "backwardSelectionSort": backward_state,
                "detachedRowRestoration": restoration, "screenshotSha256": hashlib.sha256(image).hexdigest()}
    except BaseException as error:
        try:
            page.screenshot(path=str(output / f"move-preservation-{name}-failed.png"))
            state = page.evaluate("""() => {const p=window.__MOVE_PROBE__;return {
              sort:document.getElementById('sortSelect')?.value,rows:document.querySelectorAll('#modList .mod-row').length,
              activeElement:document.activeElement?.outerHTML,selection:getSelection()?.toString(),counts:p?.counts,
              selected:p?.row.className,animations:p?.row.getAnimations().map(value=>({name:value.animationName,time:value.currentTime,state:value.playState}))}}""")
            (output / f"move-preservation-{name}-failed.json").write_text(json.dumps({"error": str(error), "state": state, "pageErrors": errors}, indent=2), encoding="utf-8")
        except Exception:
            pass
        raise
    finally:
        context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--released-ref", default=RELEASED)
    parser.add_argument("--unchanged-ref", default=UNCHANGED)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check-inputs", action="store_true")
    args = parser.parse_args()
    compiled, identities, patch = compile_variants(args.released_ref, args.unchanged_ref)
    guard_checks = selection_guard_checks()
    if args.check_inputs:
        print(json.dumps({"browserExecuted": False, "variants": identities, "patch": patch, "selectionGuardChecks": guard_checks}, indent=2))
        return
    args.output.mkdir(parents=True, exist_ok=True)
    status = git("status", "--porcelain", "--untracked-files=all").splitlines()
    assert not status, "Diagnostic source must be committed and clean"
    proof = {"schema": 1, "kind": "library-move-diagnostic", "ok": False,
             "scope": "Actual Chromium state preservation and full-content import/actions; does not replace native release acceptance",
             "proofCommit": git("rev-parse", "HEAD"), "platform": platform.platform(),
             "variants": identities, "compiledSourcePatch": patch, "selectionGuardChecks": guard_checks,
             "engine": {"path": "tests/visual_release_qa.py", "sha256": sha(Path(engine.__file__).read_text("utf-8"))},
             "diagnosticSourceSha256": {path: sha((ROOT / path).read_text("utf-8")) for path in (
                 "tests/library_move_diagnostic.py", "tests/library_move_guards.test.cjs", "tests/library_grid_diagnostic.py", "tests/library_layout_diagnostic.py",
                 "tests/test_frontend_responsiveness.py", ".github/workflows/library-move-diagnostic.yml")},
             "sourceStatusAtStart": status, "mods": MODS, "rounds": ROUNDS}
    receipt = args.output / "library-move-diagnostic.json"
    def save():
        temporary = receipt.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(proof, indent=2), encoding="utf-8")
        temporary.replace(receipt)
    def checkpoint(value):
        proof["completeLibrary"] = value
        save()
    (args.output / "move-diagnostic-patch.json").write_text(json.dumps(patch, indent=2), encoding="utf-8")
    save()
    print("MOVE source identity " + json.dumps({"proofCommit": proof["proofCommit"], "engine": proof["engine"],
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
            proof["interactionGeometry"] = {name: shared.capture_interaction_geometry(browser, compiled[name][0], args.output, name,
                require_shared_tracks=False)[0] for name in ("current013", "after")}
            proof["geometryDifferences"] = geometry.geometry_differences(proof["geometry"]["current013"], proof["geometry"]["after"])
            proof["interactionGeometryDifferences"] = geometry.geometry_differences(proof["interactionGeometry"]["current013"], proof["interactionGeometry"]["after"])
            save()
            differences = proof["geometryDifferences"] + proof["interactionGeometryDifferences"]
            if differences:
                print("MOVE geometry mismatch " + json.dumps({"count": len(differences), "first": differences[:10]}, sort_keys=True), flush=True)
            assert not differences, "Connected moves changed protected geometry; benchmark not run"
            proof["preservation"] = {}
            for name in ("current013", "after"):
                proof["preservation"][name] = preservation_probe(browser, compiled[name][0], args.output, name, candidate=name == "after")
                save()
            proof["preservation"]["fallback"] = preservation_probe(browser, compiled["after"][0], args.output, "fallback", candidate=True, force_fallback=True)
            save()
            print("MOVE geometry and actual focus/animation/selection/identity/no-detach/fallback checks passed; starting unchanged full11,207 benchmark", flush=True)
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
