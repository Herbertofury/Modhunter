#!/usr/bin/env python3
"""Actual C7/flat-fill state comparison before any complete-library benchmark.

This is a small diagnostic, not release or native Windows acceptance. It uses
the shipped controls/closure, real pointer input, normal animation frames and
the existing deterministic bridge and public artwork fixtures. No product CSS
is injected and screenshots are never normalized, cropped or animation-frozen.
Pixel-only differences remain strict failures, deferred until the complete state
and interaction matrix is recorded; non-pixel failures still stop immediately.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import traceback

import test_frontend_responsiveness as dashboard
import visual_release_qa as visual
from visual_surface_comparison import compare_pixels, decoded_png

ROOT = Path(__file__).resolve().parents[1]
CONTROL = "18883b32e0683dad8aed1dc87c6fe9ec577e12f2"
VIEWPORT = {"width": 1028, "height": 633}
SOURCES = ("tests/visual_gallery_paint_qa.py", "tests/visual_surface_comparison.py",
           "tests/visual_release_qa.py", "tests/test_frontend_responsiveness.py")
CHANGED = {"mo2_update_arsenal/web/app.js", "mo2_update_arsenal/web/atelier.css"}
STATES = ("ordinary", "hover", "focus", "native-selected", "aether-selected")

PROBE = r"""(() => {
 const probe=window.__PAINT_QA__={transitions:[],pointer:[]};
 document.addEventListener('transitionrun',event=>{
  if(event.target.parentElement?.id==='modList'&&event.target.classList.contains('mod-row')&&
     event.propertyName==='background-color'&&!event.pseudoElement)
   probe.transitions.push({id:event.target.dataset.id,property:event.propertyName});
 },true);
 probe.clear=()=>{probe.transitions=[];probe.pointer=[]};
 probe.observePointer=()=>document.getElementById('modList').addEventListener('pointerover',event=>{
  const row=event.target.closest('.mod-row'),list=document.getElementById('modList');
  if(row?.parentElement===list)probe.pointer.push({id:row.dataset.id,trusted:event.isTrusted,
   owner:list.dataset.layoutChange||null,rowToken:row.dataset.layoutRowChange||null,
   transition:getComputedStyle(row).transitionProperty});
 });
 const rect=element=>{const b=element.getBoundingClientRect();return [b.x,b.y,b.width,b.height]};
 const rgba=value=>{const m=value.match(/^rgba?\(([^)]+)\)$/);if(!m)throw Error('Unexpected computed color '+value);
  const parts=m[1].split(/[, /]+/).map(Number);return [parts[0],parts[1],parts[2],parts.length>3?parts[3]:1]};
 const over=(front,back)=>{const alpha=front[3]+back[3]*(1-front[3]);
  return [0,1,2].map(i=>alpha?(front[i]*front[3]+back[i]*back[3]*(1-front[3]))/alpha:0).concat(alpha)};
 const fill=element=>{
  const style=getComputedStyle(element);let color=rgba(style.backgroundColor);
  if(style.backgroundImage!=='none'){
   const m=style.backgroundImage.match(/^linear-gradient\((rgba?\([^)]+\)),\s*(rgba?\([^)]+\))\)$/);
   if(!m||m[1]!==m[2])throw Error('Unexpected non-flat row background '+style.backgroundImage);
   color=over(rgba(m[1]),color);
  }
  if(color[3]<1&&element.parentElement)color=over(color,fill(element.parentElement));
  return color.map(value=>Math.round(value*1000000)/1000000);
 };
 probe.snapshot=()=>{
  const list=document.getElementById('modList'),rows=[...list.querySelectorAll(':scope > .mod-row')];
  return {view:document.body.dataset.libraryView,theme:document.body.dataset.theme,density:document.body.dataset.density,
   owner:list.dataset.layoutChange||null,themeToken:list.dataset.layoutThemeChange||null,
   rowTokens:rows.filter(row=>row.hasAttribute('data-layout-row-change')).map(row=>row.dataset.id),
   active:{act:document.activeElement?.dataset.act||'',id:document.activeElement?.dataset.id||'',tag:document.activeElement?.tagName||''},
   transitions:[...probe.transitions],pointer:[...probe.pointer],scroll:[scrollX,scrollY],
   viewport:[innerWidth,innerHeight],overflow:document.documentElement.scrollWidth>innerWidth+1,
   rows:rows.map(row=>{const style=getComputedStyle(row);return {id:row.dataset.id,rect:rect(row),
    children:[...row.children].map(child=>[child.className,rect(child)]),fill:fill(row),
    color:style.backgroundColor,image:style.backgroundImage,transition:[style.transitionProperty,style.transitionDuration],
    contentVisibility:style.contentVisibility,hover:row.matches(':hover'),focus:row.matches(':focus-within'),
    classes:row.className,markup:row.outerHTML}})};
 };
 probe.endpoint=()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(probe.snapshot()))));
})()"""

IDLE = """() => !document.querySelector('#modList .aether-selection-stage,#modList .aether-hover-anchored,.aether-floating-text,#toastWrap .toast') &&
 !document.getAnimations().some(a=>a.playState==='running'&&a.effect?.target?.closest?.('#modList'))"""


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_status() -> list[str]:
    return subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"], cwd=ROOT, text=True).splitlines()


def fixture() -> tuple[dict, dict]:
    catalog = dashboard.catalog(profile="paint-fixture", count=12)
    catalog["profileName"] = "Paint parity · deterministic bridge fixture"
    catalog["items"][3]["name"] = "A long installed mod filename with multiple optional compatibility patches and preserved source identity"
    manifest = json.loads((ROOT / "tests/fixtures/visual_public_catalog.json").read_text())
    artwork = {}
    for index, entry in enumerate(manifest["sources"][:2]):
        item = catalog["items"][index + 1]
        item.update(name=entry["name"], author="", nexusId=0,
                    source={"type": "modrinth", "verified": True, "url": entry["projectUrl"]})
        item["update"].update(thumbnailUrl=entry["thumbnailUrl"], thumbnailKind="icon")
        data = (ROOT / "tests/fixtures" / entry["thumbnailFile"]).read_bytes()
        assert sha(data) == entry["sha256"]
        artwork[entry["thumbnailUrl"]] = {"bytes": data, "mime": entry["mimeType"]}
    return catalog, artwork


def open_page(browser, html: str, theme: str, catalog: dict, artwork: dict):
    context = browser.new_context(viewport=VIEWPORT, reduced_motion="no-preference", color_scheme="light" if theme == "snow" else "dark")
    errors = []
    context.on("page", lambda page: page.on("pageerror", lambda error: errors.append(str(error))))
    context.add_init_script(PROBE)
    prefs = "prefs:" + json.dumps({"autoCheckOnLaunch": False, "theme": theme, "libraryView": "list", "sound": False})
    boot = dashboard.BOOT.replace("__CATALOG__", json.dumps(catalog)).replace("__FAIL__", "0").replace("prefs:{}", prefs)
    revision = "else if(path==='revision')result={ok:true,profileId:c.profileId,generation:x.generation};"
    assert revision in boot
    boot = boot.replace(revision, "else if(path==='revision')result={ok:true,profileId:c.profileId,generation:x.generation,selectedModId:x.selectedModId||''};"
        "else if(path==='focus'){x.selectedModId=body.id;result={ok:true,profileId:c.profileId,id:body.id,message:'Selected fixture mod'};}")
    content = html.replace("<!--__MO2_BOOT__-->", boot)
    origin = "http://paint-parity.modhunter.invalid/"

    def route(request):
        url = request.request.url
        if url == origin:
            request.fulfill(content_type="text/html", body=content)
        elif url in artwork:
            request.fulfill(content_type=artwork[url]["mime"], body=artwork[url]["bytes"])
        else:
            request.abort()

    context.route("**/*", route)
    page = context.new_page()
    page.set_default_timeout(10000)
    page.goto(origin, wait_until="load")
    page.wait_for_function("document.getElementById('livePill').classList.contains('connected')&&__RESP__.counts['GET progress']>=1")
    page.wait_for_function("expected=>document.querySelectorAll('#modList > .mod-row').length===12&&document.body.dataset.theme===expected&&document.body.dataset.libraryView==='list'", arg=theme)
    page.wait_for_function("[...document.querySelectorAll('#modList .mod-preview-image')].every(image=>image.complete&&image.naturalWidth>0)")
    page.evaluate("__PAINT_QA__.observePointer()")
    page.locator("#workspaceTitle").hover()
    idle(page)
    return context, page, errors


def idle(page) -> None:
    # A bounded condition wait for the application's natural effect lifetime;
    # this helper is never used inside any benchmark clock.
    page.wait_for_function(IDLE)
    visual.settle(page)


def assert_endpoint(state: dict, count: int = 12) -> None:
    assert state["owner"] is None and state["themeToken"] is None and state["rowTokens"] == [], state
    assert len(state["rows"]) == count and len({row["id"] for row in state["rows"]}) == count
    assert state["viewport"] == [VIEWPORT["width"], VIEWPORT["height"]] and not state["overflow"]
    for row in state["rows"]:
        assert row["transition"] == ["background", "0.12s"], row
        assert row["contentVisibility"] == "visible" and row["rect"][2] > 0 and row["rect"][3] > 0
        assert all(math.isfinite(value) for value in row["rect"] + row["fill"])


def comparable(state: dict, *, markup: bool) -> dict:
    # color/image are the intentional paint representation change. Compare its
    # effective flat fill and actual pixels instead; never rewrite screenshots.
    return {"view": state["view"], "theme": state["theme"], "density": state["density"],
            "active": state["active"], "scroll": state["scroll"], "viewport": state["viewport"],
            "rows": [{key: value for key, value in row.items()
                      if key not in ({"color", "image"} if markup else {"color", "image", "markup", "children", "classes"})}
                     for row in state["rows"]]}


def transition_control(page, selector: str) -> dict:
    # Activates the actual control handler without changing the hot row's
    # pointer/focus state; actual pointer hover is verified separately below.
    page.evaluate("__PAINT_QA__.clear()")
    state = page.evaluate("""selector=>{document.querySelector(selector).click();return __PAINT_QA__.endpoint()}""", selector)
    assert_endpoint(state)
    assert state["transitions"] == [], state["transitions"]
    return state


def set_state(page, state: str) -> None:
    if state == "hover":
        page.locator("#row-item-0").hover()
    elif state == "focus":
        page.locator('#row-item-0 [data-act="inspect"]').focus()
    elif state == "native-selected":
        page.locator('#row-item-0 [data-act="locate"]').click()
        page.wait_for_function("document.getElementById('row-item-0').classList.contains('native-selected')")
        page.locator("#search").focus()
        page.locator("#workspaceTitle").hover()
    elif state == "aether-selected":
        page.evaluate("AetherFX.select(document.getElementById('row-item-0'),{source:'app'})")
        page.wait_for_function("document.getElementById('row-item-0').classList.contains('aether-target')")
    idle(page)
    selector = {"hover": ":hover", "focus": ":focus-within", "native-selected": ".native-selected", "aether-selected": ".aether-target"}.get(state)
    if selector:
        assert page.locator("#row-item-0").evaluate("(row,selector)=>row.matches(selector)", selector), state


def prove_hover(page) -> dict:
    page.locator("#workspaceTitle").hover()
    idle(page)
    page.evaluate("__PAINT_QA__.clear()")
    page.locator("#row-item-4").hover()
    page.wait_for_function("__PAINT_QA__.transitions.some(row=>row.id==='item-4')")
    idle(page)
    entered = page.evaluate("__PAINT_QA__.snapshot()")
    page.evaluate("__PAINT_QA__.clear()")
    page.locator("#workspaceTitle").hover()
    page.wait_for_function("__PAINT_QA__.transitions.some(row=>row.id==='item-4')")
    idle(page)
    exited = page.evaluate("__PAINT_QA__.snapshot()")
    assert_endpoint(entered)
    assert_endpoint(exited)
    assert {row["id"] for row in entered["transitions"]} == {"item-4"}
    assert {row["id"] for row in exited["transitions"]} == {"item-4"}
    assert entered["rows"][4]["fill"] != exited["rows"][4]["fill"]
    return {"entered": entered["transitions"], "exited": exited["transitions"],
            "enteredFill": entered["rows"][4]["fill"], "exitedFill": exited["rows"][4]["fill"]}


def prove_new_hover(page, candidate: bool) -> dict:
    page.locator("#workspaceTitle").hover()
    idle(page)
    # One real frame is allowed to apply the layout, then genuine pointer input
    # enters a row while the original second-frame cleanup is still pending.
    page.evaluate("""() => {
      __PAINT_QA__.clear();document.getElementById('libraryGallery').click();
      window.__PAINT_FIRST__=new Promise(resolve=>requestAnimationFrame(()=>{
        const row=document.getElementById('row-item-0'),b=row.getBoundingClientRect();
        resolve({x:b.x+b.width/2,y:b.y+24,owner:document.getElementById('modList').dataset.layoutChange||null});
      }));
      window.__PAINT_END__=__PAINT_QA__.endpoint();
    }""")
    first = page.evaluate("__PAINT_FIRST__")
    assert first["owner"], "New-hover coverage missed the owned first-frame window"
    page.mouse.move(first["x"], first["y"])
    endpoint = page.evaluate("__PAINT_END__")
    events = page.evaluate("__PAINT_QA__.pointer")
    guarded = [event for event in events if event["id"] == "item-0" and event["trusted"] and event["owner"] == first["owner"]]
    assert guarded, {"error": "Real pointer input did not reach the active owned batch", "first": first, "events": events}
    assert all(event["transition"] == "none" for event in guarded), guarded
    if candidate:
        assert all(event["rowToken"] == first["owner"] for event in guarded), guarded
    assert_endpoint(endpoint)
    assert endpoint["transitions"] == [], endpoint["transitions"]
    idle(page)
    transition_control(page, "#libraryList")
    return {"firstFrame": first, "trustedGuardedEvents": guarded, "endpointRestored": True}


def prove_cleanup(page, catalog: dict) -> dict:
    page.locator("#workspaceTitle").hover()
    page.locator('#row-item-0 [data-act="inspect"]').focus()
    idle(page)
    overlap = page.evaluate("""async()=>{
      const list=document.getElementById('modList');__PAINT_QA__.clear();
      document.getElementById('libraryGallery').click();const first=list.dataset.layoutChange;
      document.getElementById('themeToggle').click();const second=list.dataset.layoutChange;
      document.getElementById('libraryList').click();const third=list.dataset.layoutChange;
      const endpoint=await __PAINT_QA__.endpoint();return {first,second,third,endpoint};
    }""")
    assert all(overlap[key] for key in ("first", "second", "third"))
    assert len({overlap[key] for key in ("first", "second", "third")}) == 3
    assert_endpoint(overlap["endpoint"])
    replacement = json.loads(json.dumps(catalog))
    replacement.update(profileId="paint-replacement", catalogId="paint-replacement", profileName="Replacement fixture")
    for index, item in enumerate(replacement["items"]):
        item["id"] = f"replacement-{index}"
    profile = page.evaluate("""async catalog=>{
      const old=[...document.querySelectorAll('#modList > .mod-row')];
      document.getElementById('libraryGallery').click();const token=document.getElementById('modList').dataset.layoutChange;
      __RESP__.catalog=catalog;__RESP__.generation++;__RESP__.selectedModId='';
      await __ARSENAL_QA__.setCatalog(catalog);
      const endpoint=await __PAINT_QA__.endpoint();
      return {token,oldDetached:old.every(row=>!row.isConnected),oldClean:old.every(row=>!row.hasAttribute('data-layout-row-change')),
        profile:__ARSENAL_QA__.getCatalog().profileId,endpoint};
    }""", replacement)
    assert profile["token"] and profile["oldDetached"] and profile["oldClean"]
    assert profile["profile"] == "paint-replacement"
    assert_endpoint(profile["endpoint"])
    return {"overlappingTokensOwned": True, "detachedRowsClean": True, "replacementProfile": profile["profile"]}



def validate_comparison(entry: dict) -> None:
    if entry["variant"] == "after":
        comparison = entry["comparison"]
        assert comparison["endpointEqual"] and comparison["settledStateEqual"], {
            "state": entry["state"], "comparison": comparison}


def require_strict_pixels(report: dict) -> None:
    expected = {f"{theme}-{state}-{view}" for theme in ("night", "snow")
                for state in STATES for view in ("list", "gallery", "list-return")}
    expected.update(f"{theme}-{extra}" for theme in ("night", "snow")
                    for extra in ("theme-change-gallery", "forced-colors-gallery", "forced-colors-list"))
    for variant in ("before", "after"):
        keys = [entry["state"] for entry in report["states"] if entry["variant"] == variant]
        assert len(keys) == len(expected) and set(keys) == expected, (variant, keys)
    groups = [(entry["variant"], entry["theme"]) for entry in report["interactions"]]
    assert len(groups) == 4 and set(groups) == {(variant, theme) for variant in ("before", "after")
                                              for theme in ("night", "snow")}, groups
    failures = [{key: entry[key] for key in ("variant", "state", "file", "pngSha256", "decodedRgbaSha256", "comparison")}
                for entry in report["states"]
                if entry["variant"] == "after" and not entry["comparison"]["pixels"]["identical"]]
    report["checksComplete"] = True
    report["strictPixelFailures"] = failures
    report["strictComparisonPassed"] = not failures
    if failures:
        report["phase"] = "complete-with-strict-pixel-failures"
    assert not failures, ("Strict zero-tolerance pixels differ after the complete state and interaction matrix", len(failures))


def self_test() -> None:
    good = {"variant": "after", "state": "night-ordinary-list", "comparison": {
        "endpointEqual": True, "settledStateEqual": True,
        "pixels": {"identical": False, "changedPixels": 1, "bounds": [0, 0, 1, 1], "tolerance": 0}}}
    validate_comparison(good)
    assert good["comparison"]["pixels"]["identical"] is False
    for field in ("endpointEqual", "settledStateEqual"):
        bad = {**good, "comparison": {**good["comparison"], field: False}}
        try:
            validate_comparison(bad)
        except AssertionError:
            pass
        else:
            raise AssertionError("Deferred a non-pixel failure: " + field)
    keys = [f"{theme}-{state}-{view}" for theme in ("night", "snow")
            for state in STATES for view in ("list", "gallery", "list-return")]
    keys += [f"{theme}-{extra}" for theme in ("night", "snow")
             for extra in ("theme-change-gallery", "forced-colors-gallery", "forced-colors-list")]
    report = {"states": [{"variant": variant, "state": key, "file": f"{variant}-{key}.png",
                         "pngSha256": "test", "decodedRgbaSha256": "test",
                         "comparison": {"endpointEqual": True, "settledStateEqual": True,
                                        "pixels": {"identical": True}}}
                         for variant in ("before", "after") for key in keys],
              "interactions": [{"variant": variant, "theme": theme}
                               for variant in ("before", "after") for theme in ("night", "snow")]}
    require_strict_pixels(report)
    assert report["checksComplete"] and report["strictComparisonPassed"]
    report["states"][-1]["comparison"]["pixels"] = good["comparison"]["pixels"]
    try:
        require_strict_pixels(report)
    except AssertionError:
        assert report["checksComplete"] and report["strictComparisonPassed"] is False
        assert report["phase"] == "complete-with-strict-pixel-failures"
        assert len(report["strictPixelFailures"]) == 1
        assert report["strictPixelFailures"][0]["comparison"]["pixels"] == good["comparison"]["pixels"]
    else:
        raise AssertionError("A pixel difference passed final comparison")
    partial = {"states": report["states"][:-1], "interactions": report["interactions"]}
    try:
        require_strict_pixels(partial)
    except AssertionError:
        assert not partial.get("checksComplete")
    else:
        raise AssertionError("An incomplete matrix passed completion")
    print("Gallery paint comparison self-tests passed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--control-ref", default=CONTROL)
    parser.add_argument("--executable-path")
    parser.add_argument("--output", type=Path, default=ROOT / "native/target/gallery-paint-qa")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    assert args.control_ref == CONTROL
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"schema": 1, "ok": False, "checksComplete": False, "strictComparisonPassed": False,
              "pixelFailurePolicy": "Every decoded RGBA comparison remains zero-tolerance. Pixel-only failure is retained per state and rejected after the complete matrix; endpoint, layout, markup, interaction, source and runtime failures remain immediate.",
              "scope": __doc__, "controlCommit": CONTROL,
              "candidateCommit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "sourceStatusAtStart": source_status(), "sourceFiles": {name: sha((ROOT / name).read_bytes()) for name in SOURCES},
              "viewport": VIEWPORT, "motion": "no-preference", "states": [], "interactions": [], "errors": []}

    def checkpoint():
        (args.output / "gallery-paint-qa.json").write_text(json.dumps(report, indent=2) + "\n")

    references = {}
    contexts = []
    runtime = browser = None
    checkpoint()
    try:
        assert not report["sourceStatusAtStart"]
        before, before_assets = visual.compile_dashboard(CONTROL)
        after, after_assets = visual.compile_dashboard()
        changed = {name for name in before_assets if before_assets[name] != after_assets[name]}
        assert changed == CHANGED, changed
        report["assets"] = {"before": before_assets, "after": after_assets}
        report["compiledHtmlSha256"] = {"before": sha(before.encode()), "after": sha(after.encode())}
        catalog, artwork = fixture()
        report["fixtureSha256"] = sha(json.dumps(catalog, sort_keys=True).encode())
        report["records"] = len(catalog["items"])
        from playwright.sync_api import sync_playwright
        runtime = sync_playwright().start()
        executable = args.executable_path or shutil.which("chromium") or shutil.which("chromium-browser")
        launch = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"], "timeout": 30000}
        if executable:
            launch["executable_path"] = executable
        browser = runtime.chromium.launch(**launch)
        report.update(browserVersion=browser.version, requestedExecutable=executable or runtime.chromium.executable_path)

        def capture(page, variant, key, endpoint=None):
            if endpoint is not None:
                assert_endpoint(endpoint)
            idle(page)
            state = page.evaluate("__PAINT_QA__.snapshot()")
            assert_endpoint(state)
            path = args.output / f"{variant}-{key}.png"
            data = page.screenshot(path=str(path), full_page=False, animations="allow", caret="hide")
            pixels = decoded_png(data)
            entry = {"variant": variant, "state": key, "file": path.name, "pngSha256": sha(data),
                     "decodedRgbaSha256": sha(pixels[1]), "endpoint": endpoint, "settled": state}
            if variant == "before":
                references[key] = {"endpoint": comparable(endpoint, markup=False) if endpoint else None,
                                   "state": comparable(state, markup=True), "pixels": pixels}
                entry["comparison"] = {"reference": True, "identical": True}
            else:
                old = references[key]
                entry["comparison"] = {"pixels": compare_pixels(old["pixels"], pixels),
                    "endpointEqual": old["endpoint"] == (comparable(endpoint, markup=False) if endpoint else None),
                    "settledStateEqual": old["state"] == comparable(state, markup=True)}
            report["states"].append(entry)
            checkpoint()
            validate_comparison(entry)

        for variant, html in (("before", before), ("after", after)):
            for theme in ("night", "snow"):
                for selected in STATES:
                    report["phase"] = f"{variant}/{theme}/{selected}"
                    checkpoint()
                    context, page, errors = open_page(browser, html, theme, catalog, artwork)
                    contexts.append(context)
                    set_state(page, selected)
                    capture(page, variant, f"{theme}-{selected}-list")
                    gallery = transition_control(page, "#libraryGallery")
                    if selected in ("hover", "focus"):
                        set_state(page, selected)
                    capture(page, variant, f"{theme}-{selected}-gallery", gallery)
                    listed = transition_control(page, "#libraryList")
                    if selected in ("hover", "focus"):
                        set_state(page, selected)
                    capture(page, variant, f"{theme}-{selected}-list-return", listed)
                    if selected == "ordinary":
                        hover = prove_hover(page)
                        new_hover = prove_new_hover(page, variant == "after")
                        transition_control(page, "#libraryGallery")
                        theme_changed = transition_control(page, "#themeToggle")
                        capture(page, variant, f"{theme}-theme-change-gallery", theme_changed)
                        page.emulate_media(forced_colors="active")
                        capture(page, variant, f"{theme}-forced-colors-gallery")
                        listed = transition_control(page, "#libraryList")
                        capture(page, variant, f"{theme}-forced-colors-list", listed)
                        page.emulate_media(forced_colors="none")
                        cleanup = prove_cleanup(page, catalog)
                        report["interactions"].append({"variant": variant, "theme": theme,
                                                       "hover": hover, "newHover": new_hover, "cleanup": cleanup})
                    assert not errors, errors
                    context.close()
                    contexts.remove(context)
        report["sourceStatusAtEnd"] = source_status()
        assert not report["sourceStatusAtEnd"]
        assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip() == report["candidateCommit"]
        assert all(sha((ROOT / path).read_bytes()) == digest for path, digest in report["sourceFiles"].items())
        assert visual.compile_dashboard()[1] == after_assets
        report["sourceDirty"] = False
        require_strict_pixels(report)
        report.update(ok=True, phase="complete")
        checkpoint()
    except Exception:
        report["errors"].append(traceback.format_exc())
        report["sourceStatusAtEnd"] = source_status()
        report["sourceDirty"] = bool(report["sourceStatusAtStart"] or report["sourceStatusAtEnd"])
        checkpoint()
        for index, context in enumerate(contexts):
            for page_index, page in enumerate(context.pages):
                try:
                    page.screenshot(path=str(args.output / f"failure-{index}-{page_index}.png"), timeout=5000)
                except Exception:
                    pass
        raise
    finally:
        for context in contexts:
            context.close()
        if browser:
            browser.close()
        if runtime:
            runtime.stop()
    print(json.dumps({"ok": report["ok"], "states": len(report["states"]), "interactionGroups": len(report["interactions"])}))


if __name__ == "__main__":
    main()
