"""Actual-browser parity preflight for Gallery layout-only containment.

All16 declared records are checked at three widths, both densities and both
Gallery groupings, plus seven focused interaction states in both themes. The
same actual Aether stage must remain row-owned through scroll and resize. Only
the declared row contain:none to contain:layout property differs; every existing
computed-style sample, rectangle, pseudo/text box, action, markup and raw pixel
remains strict. Pixel-only mismatches are retained through all cases and fail finally;
semantic or geometry failures still stop immediately. No CSS is injected and
this is not a performance or native acceptance result.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import time
import traceback

import ci_font_rendering_qa as font_qa
import visual_release_qa as acceptance
import visual_style_profile as profile
from visual_surface_comparison import compare_pixels, decoded_png

ROOT = acceptance.ROOT
CONTROL_REF = "6ca747be97b98e17ea66c0fea9ef51be9b934e30"
ORIGIN = "http://gallery-containment.modhunter.invalid/"
CSS_PATH = "mo2_update_arsenal/web/atelier.css"
RULE = '\n/* Isolated layout-containment diagnostic; preserve content-based card sizing. */\nbody[data-layout="compact"][data-library-view="gallery"] #modList > .mod-row{contain:layout}\n'
WIDTHS = (1440, 900, 390)
DENSITIES = ("compact", "comfy")
GROUPINGS = ("all", "chapters")
FOCUSED_STATES = ("ordinary", "hover", "focus", "native-selected", "aether-selected", "context-menu", "inspector")
THEMES = ("night", "snow")
SOURCES = (
    "tests/visual_gallery_containment_qa.py", "tests/visual_surface_comparison.py",
    "tests/visual_style_profile.py", "tests/visual_release_qa.py",
    "tests/ci_font_rendering_qa.py", "tests/prepare_ci_fonts.py",
    "tests/test_frontend_responsiveness.py", "tests/fixtures/visual_public_catalog.json",
)
PREVIEW_JS = """() => [...document.querySelectorAll('#modList > .mod-row')].map(row=>{
 const origin=row.getBoundingClientRect(),art=row.querySelector('.mod-art'),image=art.querySelector('img');
 const box=r=>[r.x-origin.x,r.y-origin.y,r.width,r.height];
 const style=getComputedStyle(art),textRanges=[];
 for(const child of art.childNodes)if(child.nodeType===Node.TEXT_NODE&&child.textContent.trim()){
   const range=document.createRange();range.selectNodeContents(child);
   textRanges.push(...[...range.getClientRects()].map(box));}
 const properties=['width','height','min-width','min-height','margin-left','margin-right','margin-top',
   'margin-bottom','padding-left','padding-right','padding-top','padding-bottom','box-sizing',
   'grid-column','grid-row','align-self','justify-content','align-items','display'];
 return {id:row.dataset.id,rect:box(art.getBoundingClientRect()),textRanges,
   sizing:Object.fromEntries(properties.map(key=>[key,style.getPropertyValue(key)])),
   image:image?{rect:box(image.getBoundingClientRect()),complete:image.complete,
      naturalWidth:image.naturalWidth,naturalHeight:image.naturalHeight,src:image.currentSrc,
      objectFit:getComputedStyle(image).objectFit,padding:getComputedStyle(image).padding}:null,
   actions:[...row.querySelectorAll('[data-act]')].map(element=>({
      action:element.dataset.act,text:element.textContent,label:element.getAttribute('aria-label'),
      href:element.getAttribute('href'),rect:box(element.getBoundingClientRect())}))};
})"""


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def source_status() -> list[str]:
    return git("status", "--porcelain", "--untracked-files=all").splitlines()


def make_fixture() -> tuple[dict, dict, list[dict]]:
    """Retain all 12 frozen project identities and four declared edge cases."""
    manifest = json.loads((ROOT / "tests/fixtures/visual_public_catalog.json").read_text("utf-8"))
    projects = manifest["sources"]
    assert len(projects) == 12, "The frozen public-project fixture changed"
    fixture = acceptance.dashboard.catalog(profile="preview-stretch-diagnostic", count=16)
    fixture.update(profileName="Preview sizing diagnostic · 16 complete cases", gameRuntime="QA fixture")
    fixture["groups"][0].update(name="Preview sizing: authentic project artwork and explicit QA edge cases", count=16)
    group = fixture["groups"][0]
    now = int(time.time())
    assets, cases = {}, []
    for index, source in enumerate(projects):
        mode = ("icon", "image", "missing")[index % 3]
        raw = (ROOT / "tests/fixtures" / source["thumbnailFile"]).read_bytes()
        assert len(raw) == source["bytes"] and sha(raw) == source["sha256"]
        assets[source["thumbnailUrl"]] = (source["mimeType"], raw)
        item = fixture["items"][index]
        item.update(name=source["name"], group=group["name"], groupId=group["id"],
                    installedVersion="QA 1.0", author="", nexusId=0, audit={},
                    screenshotNote="Sizing fixture: " + mode + " presentation of frozen official project artwork",
                    source={"type": "modrinth", "verified": True, "url": source["projectUrl"]},
                    update={"verdict": "candidate" if index % 2 == 0 else "current",
                            "latestVersion": "QA 2.0" if index % 2 == 0 else "QA 1.0",
                            "checkedAt": now, "expiresAt": now + 14400,
                            "source": "preview-sizing-fixture"})
        if mode != "missing":
            item["update"].update(thumbnailUrl=source["thumbnailUrl"], thumbnailKind=mode)
        cases.append({"id": item["id"], "kind": mode, "projectUrl": source["projectUrl"],
                      "artworkSha256": source["sha256"], "artworkOriginalKind": "icon",
                      "scope": "Image mode deliberately tests cover-fit using the same verified icon bytes; no project screenshot claim"})
    real = acceptance.screenshot_catalog()["items"]
    chosen = [next(item for item in real if item.get("nexusId") and item.get("update", {}).get("verdict") == "candidate"),
              next(item for item in real if item.get("nexusId") and item.get("update", {}).get("verdict") == "current"),
              next(item for item in real if item.get("source", {}).get("type") == "creations")]
    for index, original in enumerate([*chosen, chosen[0]], start=12):
        item = deepcopy(original)
        item.update(id=f"item-{index}", priority=index, groupId=group["id"], group=group["name"])
        kind = "exact-source-missing-art"
        if index == 15:
            kind = "long-name-author-version-and-note-missing-art"
            item["name"] += " — Diagnostic long filename " + "Compatibility patch and localized installation notes " * 4
            item["author"] = "QA author with a deliberately long name and a compatibility-maintenance team"
            item["installedVersion"] = "QA 1.0.0-beta+20261116.compatibility.anniversary.special-edition"
            item.setdefault("update", {})["latestVersion"] = "QA 2.0.0-rc.1+20261116.extended.compatibility"
            item["screenshotNote"] = "Synthetic long title, author and version sizing case; original source identity is retained. 外觀、隱私與維護"
        fixture["items"][index] = item
        cases.append({"id": item["id"], "kind": kind, "sourceUrl": item.get("source", {}).get("url")})
    assert len(fixture["items"]) == len(cases) == 16
    assert [item["id"] for item in fixture["items"]] == [f"item-{index}" for index in range(16)]
    return fixture, assets, cases



def compare_deep_geometry(reference: list, candidate: list) -> dict:
    """Every existing sampled style and geometry field remains exact."""
    return {"exact": reference == candidate}


def self_test() -> None:
    old = [{"id": "item-0", "computed": {"row": {"display": "grid"}},
            "rectangles": {"art": [0, 0, 100, 120]}}]
    assert compare_deep_geometry(old, deepcopy(old))["exact"]
    changed = deepcopy(old); changed[0]["rectangles"]["art"][2] += 0.015625
    assert not compare_deep_geometry(old, changed)["exact"]
    changed = deepcopy(old); changed[0]["computed"]["row"]["display"] = "flex"
    assert not compare_deep_geometry(old, changed)["exact"]
    print("Containment preflight retains exact sampled styles and fractional geometry")


IDLE = """() => !document.querySelector('#modList .aether-selection-stage,#modList .aether-hover-anchored,.aether-floating-text,#toastWrap .toast') &&
 !document.getAnimations().some(a=>a.playState==='running'&&a.effect?.target?.closest?.('#modList'))"""
HIT_JS = """() => {
 const row=document.getElementById('row-item-0');
 return ['.mod-priority','.mod-art','.mod-title','.favorite-btn','.inspect-btn'].map(selector=>{
  const element=row.querySelector(selector),b=element.getBoundingClientRect();
  const hit=document.elementFromPoint(b.x+b.width/2,b.y+b.height/2);
  return {selector,rect:[b.x,b.y,b.width,b.height],receivesPointer:!!hit&&(hit===element||element.contains(hit)),
    hitTag:hit?.tagName,hitClass:hit?.className,belongsToRow:hit?.closest?.('.mod-row')===row};
 });
}"""
OVERLAY_JS = """id => {
 const element=document.getElementById(id),b=element.getBoundingClientRect(),style=getComputedStyle(element);
 const controls=[...element.querySelectorAll('button')].filter(e=>{const r=e.getBoundingClientRect();return r.width&&r.height;});
 return {id,rect:[b.x,b.y,b.width,b.height],position:style.position,bodySibling:element.parentElement===document.body,
  outsideLibrary:!document.getElementById('modList').contains(element),withinViewport:b.x>=0&&b.y>=0&&b.right<=innerWidth&&b.bottom<=innerHeight,
  controls:controls.map(e=>{const r=e.getBoundingClientRect(),visible=r.x>=0&&r.y>=0&&r.right<=innerWidth&&r.bottom<=innerHeight;
   const hit=visible&&document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
   return {text:e.textContent,label:e.getAttribute('aria-label'),rect:[r.x,r.y,r.width,r.height],visible,
     receivesPointer:!!hit&&(hit===e||e.contains(hit))};})};
}"""
ANCHORED_RESIZE_JS = """async () => {
 await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
 const {row,stage}=window.__CONTAINMENT_STAGE_PROBE__;
 if(!row.isConnected||!stage.isConnected||stage.parentElement!==row)
   throw new Error('The original Aether stage expired or lost its row before the resize proof');
 const r=row.getBoundingClientRect(),s=stage.getBoundingClientRect(),style=getComputedStyle(row);
 const left=parseFloat(style.borderLeftWidth),right=parseFloat(style.borderRightWidth),
   top=parseFloat(style.borderTopWidth),bottom=parseFloat(style.borderBottomWidth);
 return {viewport:[innerWidth,innerHeight],row:[r.x,r.y,r.width,r.height],
   relative:[s.x-r.x,s.y-r.y,s.width,s.height],expected:[left,top,r.width-left-right,r.height-top-bottom],
   sameOwnedStage:row.querySelector('.aether-selection-stage.aether-anchored')===stage,
   position:getComputedStyle(stage).position,pointerEvents:getComputedStyle(stage).pointerEvents};
}"""


def focused_state(page, state: str) -> dict:
    # Ordinary controls and natural effect lifetimes only. No animation or
    # timestamp is rewritten; this small preflight is outside all benchmarks.
    page.wait_for_function(IDLE)
    initial_hits = page.evaluate(HIT_JS)
    assert all(hit["receivesPointer"] and hit["belongsToRow"] for hit in initial_hits), initial_hits
    proof = {"state": state, "initialHits": initial_hits}
    if state == "hover":
        page.locator("#row-item-0").hover()
    elif state == "focus":
        page.locator('#row-item-0 [data-act="inspect"]').focus()
    elif state == "native-selected":
        page.locator("#row-item-0").click(button="right")
        page.locator('#modContextMenu [data-context-act="locate"]').click()
        page.wait_for_function("document.getElementById('row-item-0').classList.contains('native-selected')")
        page.locator("#search").focus()
        page.locator("#workspaceTitle").hover()
    elif state == "aether-selected":
        proof["anchoredStage"] = page.evaluate("""() => {
          const row=document.getElementById('row-item-0'),control=document.getElementById('fxIntensity');
          control.value='subtle';control.dispatchEvent(new Event('change',{bubbles:true}));
          AetherFX.select(row,{force:true,source:'app'});
          const stage=row.querySelector('.aether-selection-stage.aether-anchored');
          if(!stage)throw new Error('Actual Aether selection did not create its owned stage');
          window.__CONTAINMENT_STAGE_PROBE__={row,stage};
          const relative=()=>{const r=row.getBoundingClientRect(),s=stage.getBoundingClientRect();
            return [s.x-r.x,s.y-r.y,s.width,s.height];};
          const before=relative(),original=scrollY;scrollBy({top:96,behavior:'instant'});const moved=scrollY-original,after=relative();scrollTo({top:original,behavior:'instant'});
          return {before,after,moved,parentIsRow:stage.parentElement===row,position:getComputedStyle(stage).position,
            pointerEvents:getComputedStyle(stage).pointerEvents,stageInsideRow:row.contains(stage),
            viewportStageOutside:document.getElementById('aetherStage')?.parentElement===document.body,
            canvasOutside:document.getElementById('aetherCanvas')?.parentElement===document.body};
        }""")
        stage = proof["anchoredStage"]
        assert stage["parentIsRow"] and stage["stageInsideRow"] and stage["viewportStageOutside"] and stage["canvasOutside"]
        assert stage["position"] == "absolute" and stage["pointerEvents"] == "none" and stage["moved"] == 96
        assert stage["before"] == stage["after"], stage
        original_viewport = page.viewport_size
        assert original_viewport == {"width": 900, "height": 1000}, original_viewport
        stage["resizeProof"] = []
        try:
            for viewport in ({"width": 760, "height": 1000}, original_viewport):
                page.set_viewport_size(viewport)
                resized = page.evaluate(ANCHORED_RESIZE_JS)
                assert resized["viewport"] == [viewport["width"], viewport["height"]]
                assert resized["sameOwnedStage"] and resized["position"] == "absolute" and resized["pointerEvents"] == "none", resized
                assert resized["relative"] == resized["expected"], resized
                stage["resizeProof"].append(resized)
        finally:
            if page.viewport_size != original_viewport:
                page.set_viewport_size(original_viewport)
            page.evaluate("delete window.__CONTAINMENT_STAGE_PROBE__")
    elif state == "context-menu":
        page.locator("#row-item-0").click(button="right")
        page.wait_for_function("!document.getElementById('modContextMenu').hidden")
    elif state == "inspector":
        page.locator('#row-item-0 [data-act="inspect"]').click()
        page.wait_for_function("document.getElementById('detailDrawer').getAttribute('aria-hidden')==='false'")
        page.wait_for_function("() => [...document.querySelectorAll('#drawerContent img')].every(image=>image.complete&&image.naturalWidth>0)")
        page.wait_for_function("""() => {
          const drawer=document.getElementById('detailDrawer'),box=drawer.getBoundingClientRect();
          return box.x>=0&&box.y>=0&&box.right<=innerWidth&&box.bottom<=innerHeight&&
            !drawer.getAnimations().some(animation=>animation.playState==='running');
        }""")
    else:
        assert state == "ordinary", state
    page.wait_for_function(IDLE)
    expected={"hover":":hover","focus":":focus-within","native-selected":".native-selected","aether-selected":".aether-target"}.get(state)
    if expected:
        assert page.locator("#row-item-0").evaluate("(row,selector)=>row.matches(selector)",expected), state
    if state in ("context-menu","inspector"):
        overlay=page.evaluate(OVERLAY_JS,"modContextMenu" if state=="context-menu" else "detailDrawer")
        assert overlay["position"]=="fixed" and overlay["outsideLibrary"] and overlay["withinViewport"], overlay
        visible=[control for control in overlay["controls"] if control["visible"]]
        assert visible and all(control["receivesPointer"] for control in visible), overlay
        proof["overlay"]=overlay
    else:
        proof["stateHits"]=page.evaluate(HIT_JS)
        assert all(hit["receivesPointer"] and hit["belongsToRow"] for hit in proof["stateHits"]), proof
    return proof


def focused_cleanup(page, state: str | None) -> dict:
    if state == "context-menu":
        page.keyboard.press("Escape")
        assert page.locator("#modContextMenu").is_hidden()
        assert page.locator('#row-item-0 [data-act="inspect"]').evaluate("e=>document.activeElement===e")
        return {"menuClosed":True,"focusReturned":True}
    if state == "inspector":
        page.locator("#drawerClose").click()
        page.wait_for_function("document.getElementById('detailDrawer').getAttribute('aria-hidden')==='true'")
        assert page.locator('#row-item-0 [data-act="inspect"]').evaluate("e=>document.activeElement===e")
        return {"inspectorClosed":True,"focusReturned":True}
    return {}


def capture(browser, html: str, fixture: dict, assets: dict, output: Path,
            variant: str, width: int, density: str, grouping: str,
            theme: str = "snow", state: str | None = None) -> tuple[dict, dict, tuple]:
    label = f"focused-{theme}-{state}-{variant}" if state else f"{width}-{density}-{grouping}-{variant}"
    context = browser.new_context(viewport={"width": width, "height": 844 if width == 390 else 1000},
                                  reduced_motion="no-preference" if state else "reduce")
    errors = []
    context.on("page", lambda page: page.on("pageerror", lambda error: errors.append(str(error))))
    compiled = acceptance.fixture_page(html, fixture)

    def route(request):
        url = request.request.url
        if url == ORIGIN:
            request.fulfill(content_type="text/html", body=compiled)
        elif url in assets:
            mime, raw = assets[url]
            request.fulfill(content_type=mime, body=raw)
        else:
            request.abort()

    context.route("**/*", route)
    session = None
    try:
        page = context.new_page()
        page.set_default_timeout(15000)
        page.goto(ORIGIN, wait_until="load")
        page.wait_for_function("n=>document.querySelectorAll('#modList > .mod-row').length===n", arg=len(fixture["items"]))
        page.wait_for_function("document.getElementById('livePill').classList.contains('connected')")
        acceptance.set_dashboard_theme(page, theme)
        page.locator("#libraryGallery").click()
        page.locator("#galleryGrouping").select_option(grouping)
        page.wait_for_function("grouping=>document.body.dataset.galleryGrouping===grouping", arg=grouping)
        if page.locator("body").get_attribute("data-density") != density:
            page.locator("#densityToggle").click()
        page.wait_for_function("density=>document.body.dataset.libraryView==='gallery'&&document.body.dataset.density===density", arg=density)
        for image in page.locator("#modList .mod-preview-image").all():
            image.scroll_into_view_if_needed()
            page.wait_for_function("image=>image.complete&&image.naturalWidth>0", arg=image.element_handle())
        assert page.locator("#modList .mod-preview-image").count() == 8
        acceptance.scroll_dashboard_to_top(page)
        page.mouse.move(0, 0)
        page.evaluate("async()=>{await document.fonts.ready;await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame)}")
        interaction=focused_state(page,state) if state else None
        acceptance.assert_no_overflow(page, label)
        geometry = profile.complete_geometry(page, len(fixture["items"]))
        assert [row[0] for row in geometry["rowGeometry"]] == [item["id"] for item in fixture["items"]]
        session = context.new_cdp_session(page)
        data = {"geometry": geometry, "deepRows": profile.deep_row_geometry(page, session),
                "previews": page.evaluate(PREVIEW_JS),
                "directChildren": page.evaluate("""() => [...document.querySelectorAll('#modList > .mod-row')].map(row=>{
                  const origin=row.getBoundingClientRect();return {id:row.dataset.id,children:[...row.children].map(element=>{
                    const b=element.getBoundingClientRect();return {tag:element.tagName,classes:element.className,
                      rect:[b.x-origin.x,b.y-origin.y,b.width,b.height]};})};})"""),
                "markup": profile.record_markup_fingerprint(page, len(fixture["items"])),
                "interaction":interaction,
                "containment":page.evaluate("() => [...document.querySelectorAll('#modList > .mod-row')].map(row=>[row.dataset.id,getComputedStyle(row).contain])")}
        assert len(data["containment"])==16 and all(value==("layout" if variant=="candidate" else "none") for _,value in data["containment"])
        assert all(preview["image"] is None or (preview["image"]["complete"]
                   and preview["image"]["naturalWidth"] > 0) for preview in data["previews"])
        payload = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        geometry_path = output / f"{label}.geometry.json.gz"
        geometry_path.write_bytes(gzip.compress(payload, mtime=0))
        image_path = output / f"{label}.png"
        png = page.screenshot(path=str(image_path), full_page=state is None, animations="allow", caret="hide", timeout=30000)
        decoded = decoded_png(png)
        assert decoded[0][0] == width and decoded[0][1] > 0
        after = profile.complete_geometry(page, len(fixture["items"]))
        assert all(geometry[key] == after[key] for key in ("listGeometry", "rowGeometry", "chapterGeometry", "emptyGeometry")), "Screenshot changed layout"
        assert not errors, errors
        receipt = {"variant": variant, "width": width, "density": density, "grouping": grouping, "theme": theme, "state":state,
                   "motionPreference":"no-preference" if state else "reduce",
                   "rows": geometry["rows"], "allLaidOut": geometry["allLaidOut"],
                   "noContentVisibility": geometry["noContentVisibility"], "errors": errors,
                   "geometryFile": geometry_path.name, "geometrySha256": sha(geometry_path.read_bytes()),
                   "screenshot": image_path.name, "screenshotSha256": sha(png),
                   "pixelSize": list(decoded[0]), "decodedRgbaSha256": sha(decoded[1])}
        receipt["cleanup"]=focused_cleanup(page,state)
        return receipt, data, decoded
    finally:
        if session:
            session.detach()
        context.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--executable-path", type=Path)
    parser.add_argument("--font-receipt", type=Path)
    parser.add_argument("--control-ref", default=CONTROL_REF)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test(); return
    if args.output is None or args.executable_path is None or args.font_receipt is None:
        parser.error("--output, --executable-path and --font-receipt are required")
    assert args.control_ref == CONTROL_REF, "This probe compares the exact accepted immutable OLED control source checkpoint"
    assert args.executable_path.is_file(), "The requested actual Chromium executable is missing"
    args.output.mkdir(parents=True, exist_ok=True)
    assert not (args.output / "gallery-layout-containment-preflight.json").exists(), "Choose a fresh evidence output directory"
    report = {"schema": 1, "kind": "gallery-layout-containment-preflight", "ok": False,
              "scope": "All16 records at1440/900/390 widths ×2densities ×2groupings under reduced motion, plus7focused states ×2themes at900 under normal motion; the same actual Aether stage is checked during96px scroll and900→760→900 resize. Every geometry/markup/pixel remains strict. Only direct-card contain none→layout changes. The full11207 comparison at1028×633 and native acceptance are separate",
              "controlCommit": CONTROL_REF, "candidateCommit": git("rev-parse", "HEAD"),
              "sourceStatusAtStart": source_status(),
              "sources": {name: sha((ROOT / name).read_bytes()) for name in SOURCES},
              "requestedExecutable": str(args.executable_path.resolve()),
              "motionPreference": "reduce matrix and no-preference focused states", "theme": "snow matrix; night/snow focused", "states": [], "errors": [],
              "semanticComparisonsPassed": False, "strictPixelFailures": []}

    def checkpoint():
        (args.output / "gallery-layout-containment-preflight.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    checkpoint()
    try:
        assert not report["sourceStatusAtStart"], "The probe requires clean committed source"
        _, report["fontProof"] = font_qa.verify_font_receipt(args.font_receipt)
        before, before_assets = acceptance.compile_dashboard(CONTROL_REF)
        after, after_assets = acceptance.compile_dashboard()
        report["assets"] = {"control": before_assets, "candidate": after_assets}
        report["changedAssets"] = [name for name in before_assets if before_assets[name] != after_assets[name]]
        assert report["changedAssets"] == [CSS_PATH], report["changedAssets"]
        original_css = acceptance.read(CSS_PATH, CONTROL_REF)
        assert acceptance.read(CSS_PATH) == original_css + RULE, "Candidate CSS contains changes beyond the exact scoped layout-containment rule"
        fixture, assets, cases = make_fixture()
        report.update(fixtureRows=len(fixture["items"]), cases=cases,
                      fixtureSha256=sha(json.dumps(fixture, sort_keys=True).encode()),
                      fixtureScope="All12 frozen official-project identities plus four explicit source/long-name edge cases; cover-fit uses genuine icon bytes, not project screenshot metadata")
        from playwright.sync_api import sync_playwright
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(executable_path=str(args.executable_path), headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"], timeout=30000)
            try:
                report["browserVersion"] = browser.version
                report["engine"] = profile.browser_identity(browser)
                assert report["engine"]["resolvedExecutable"] == report["requestedExecutable"]
                for width in WIDTHS:
                    for density in DENSITIES:
                        for grouping in GROUPINGS:
                            report["phase"] = f"{width}-{density}-{grouping}:control"
                            checkpoint()
                            control, reference, pixels = capture(browser, before, fixture, assets, args.output, "control", width, density, grouping)
                            report["states"].append(control)
                            report["phase"] = f"{width}-{density}-{grouping}:candidate"
                            checkpoint()
                            candidate, actual, actual_pixels = capture(browser, after, fixture, assets, args.output, "candidate", width, density, grouping)
                            deep = compare_deep_geometry(reference["deepRows"], actual["deepRows"])
                            comparison = {"exactGeometry": reference["geometry"] == actual["geometry"],
                                          "exactDescendantTextPseudoGeometry": deep["exact"],
                                          "exactInteraction":reference["interaction"]==actual["interaction"],
                                          "exactCleanup":control["cleanup"]==candidate["cleanup"],
                                          "exactAllDirectChildGeometry": reference["directChildren"] == actual["directChildren"],
                                          "exactPreviewAndActionGeometry": reference["previews"] == actual["previews"],
                                          "identicalOrderedMarkup": reference["markup"] == actual["markup"],
                                          "pixels": compare_pixels(pixels, actual_pixels)}
                            semantic = all(comparison[key] for key in comparison if key != "pixels")
                            candidate["comparison"] = comparison
                            candidate["semanticComparisonPassed"] = semantic
                            candidate["ok"] = semantic and comparison["pixels"]["identical"]
                            report["states"].append(candidate)
                            if not comparison["pixels"]["identical"]:
                                report["strictPixelFailures"].append({"width": width, "density": density,
                                    "grouping": grouping, "comparison": comparison["pixels"]})
                            checkpoint()
                            assert semantic, ("Layout containment changed geometry, semantics or existing sampled styles", width, density, grouping, comparison)
                for theme in THEMES:
                    for state in FOCUSED_STATES:
                        report["phase"]=f"focused-{theme}-{state}:control";checkpoint()
                        control,reference,pixels=capture(browser,before,fixture,assets,args.output,"control",900,"compact","all",theme,state)
                        report["states"].append(control)
                        report["phase"]=f"focused-{theme}-{state}:candidate";checkpoint()
                        candidate,actual,actual_pixels=capture(browser,after,fixture,assets,args.output,"candidate",900,"compact","all",theme,state)
                        comparison={"exactGeometry":reference["geometry"]==actual["geometry"],
                          "exactDescendantTextPseudoGeometry":compare_deep_geometry(reference["deepRows"],actual["deepRows"])["exact"],
                          "exactAllDirectChildGeometry":reference["directChildren"]==actual["directChildren"],
                          "exactPreviewAndActionGeometry":reference["previews"]==actual["previews"],
                          "identicalOrderedMarkup":reference["markup"]==actual["markup"],
                          "exactInteraction":reference["interaction"]==actual["interaction"],
                          "exactCleanup":control["cleanup"]==candidate["cleanup"],
                          "pixels":compare_pixels(pixels,actual_pixels)}
                        semantic=all(value for key,value in comparison.items() if key!="pixels")
                        candidate.update(comparison=comparison,semanticComparisonPassed=semantic,ok=semantic and comparison["pixels"]["identical"])
                        report["states"].append(candidate)
                        if not comparison["pixels"]["identical"]:
                            report["strictPixelFailures"].append({"theme":theme,"state":state,"comparison":comparison["pixels"]})
                        checkpoint()
                        assert semantic,("Containment changed focused control placement or behavior",theme,state,comparison)
            finally:
                browser.close()
        report["sourceStatusAtEnd"] = source_status()
        assert not report["sourceStatusAtEnd"] and git("rev-parse", "HEAD") == report["candidateCommit"]
        assert len(report["states"]) == len(WIDTHS) * len(DENSITIES) * len(GROUPINGS) * 2 + len(THEMES) * len(FOCUSED_STATES) * 2
        assert all(sha((ROOT / name).read_bytes()) == digest for name, digest in report["sources"].items())
        assert acceptance.compile_dashboard()[1] == after_assets
        report["semanticComparisonsPassed"] = True
        report["phase"] = "complete-with-strict-pixel-failures" if report["strictPixelFailures"] else "complete"
        checkpoint()
        assert not report["strictPixelFailures"], ("Strict zero-tolerance pixels differed after all complete layout cases", report["strictPixelFailures"])
        report.update(ok=True, phase="complete", sourceDirty=False)
        checkpoint()
    except Exception:
        report["errors"].append(traceback.format_exc())
        report["sourceStatusAtEnd"] = source_status()
        report["sourceDirty"] = bool(report["sourceStatusAtStart"] or report["sourceStatusAtEnd"])
        checkpoint()
        raise
    print(json.dumps({"ok": report["ok"], "states": len(report["states"]), "fixtureRows": report["fixtureRows"]}))


if __name__ == "__main__":
    main()
