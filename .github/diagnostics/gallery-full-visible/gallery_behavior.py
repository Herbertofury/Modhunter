"""Detached opt-in Playwright test. Requires an already-approved disposable
browser supplied by the caller. No browser launcher, HTTP server, or installs.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent / "modhunter-idle-89b-compose.g0xUDpET" / "exact"
ORIGIN = "http://gallery-track-proof.fixture.test/"
PROFILE = "gallery-track-proof-owned"
NONCE = "Z2FsbGVyeS10cmFjay1wcm9vZg"
OFFLINE_GUARD = """  if(!window.__ARSENAL_QA__||window.__ARSENAL_QA__.live!==false||
     window.__ARSENAL_QA__.getCatalog().items.length!==count)
   throw Error('Expected exact owned offline test catalog');"""
MOCK_GUARD = f"""  if(location.href!=={json.dumps(ORIGIN)}||
     !window.__ARSENAL_QA__||window.__ARSENAL_QA__.live!==true||
     window.__MO2_BRIDGE_BOOT__?.token!=='test-only'||
     window.__RESP__?.catalog?.profileId!=={json.dumps(PROFILE)}||
     window.__ARSENAL_QA__.getCatalog().profileId!=={json.dumps(PROFILE)}||
     window.__ARSENAL_QA__.getCatalog().items.length!==count)
   throw Error('Expected only the exact isolated mock bridge catalog');"""
EXPECTED = {
    HERE / "full-visible-browser-proof.js": "68c095f20ded5c4b34b89184f92fc24f3389ecf2cb089970c396c24f4120799a",
    ROOT / "tests/test_frontend_responsiveness.py": "9dd5cc7cd0b1c491669e4e139c0a3a481e3a578652f3700c16569213d0b5c8a5",
    ROOT / "mo2_update_arsenal/web/app.js": "f6871a167e5ccbcbf8de31c16f7806c514989f6264506b6529fe1fc807ba9a99",
    ROOT / "mo2_update_arsenal/web/atelier.css": "82ea1fe2712b3b834bd50381e301f86448efb22adf8a7747edfef42550625009",
}

def require(condition, description):
    if not condition:
        raise AssertionError(description)

def exact_inputs():
    # Git manifest was read from verified 1d50 tree f1e0257…, not from its
    # staged worktree. Pin every production input used by the standard builder.
    manifest = (HERE / "FROZEN_INPUTS.tsv").read_text("utf8").splitlines()
    require(len(manifest) == 20, "Incomplete frozen input manifest")
    for entry in manifest:
        digest, relative = entry.split("\t")
        path = ROOT / relative
        data = path.read_bytes()
        header = ("blob " + str(len(data)) + "\0").encode("ascii")
        require(hashlib.sha1(header + data).hexdigest() == digest, f"Git input drift: {path.name}")
    for path, digest in EXPECTED.items():
        require(hashlib.sha256(path.read_bytes()).hexdigest() == digest, f"Input drift: {path.name}")

def proof(live):
    source = (HERE / "full-visible-browser-proof.js").read_text("utf8")
    require(source.count(OFFLINE_GUARD) == 1, "Original ownership guard changed")
    # This is a test-only adapter of the isolated diagnostic, never an edit to
    # app.js or to the frozen offline probe. No DOM markup is synthesized here.
    return source.replace(OFFLINE_GUARD, MOCK_GUARD, 1) if live else source

def harness_module():
    exact_inputs()
    sys.path.insert(0, str(ROOT / "tests"))
    try:
        import test_frontend_responsiveness as harness
        require(Path(harness.__file__).resolve() == ROOT / "tests/test_frontend_responsiveness.py", "Harness shadowed")
        return harness
    finally:
        sys.path.pop(0)

def fixture(count):
    require(9 <= count <= 11207, "Owned fixture count outside supported bound")
    harness = harness_module()
    data = harness.catalog(profile=PROFILE, count=count)
    for i, mod in enumerate(data["items"]):
        mod.update(name=f"Owned track fixture {i}", thumbnailUrl="", previewUrl="", imageUrl="", iconUrl="")
        if i % 4 == 0:  # exact product Review control (650 weight)
            continue
        if i % 4 == 1:  # exact product direct file selector glyph
            mod["update"] = {"verdict": "current", "latestVersion": "1.0"}
        elif i % 4 == 2:  # known wide provider rendered by source_insights.js
            mod.update(nexusId=0, source={"type": "creations", "verified": True,
                "url": f"https://creations.bethesda.net/en/skyrim/details/{i:032x}"})
        else:
            mod.update(nexusId=0, source={})
    return data

def build(live, data):
    harness = harness_module()
    boot = harness.BOOT.replace("__CATALOG__", json.dumps(data)).replace("__FAIL__", "0").replace(
        "prefs:{}", "prefs:{autoCheckOnLaunch:false}") if live else ""
    html = harness.build_page().replace("<!--__MO2_BOOT__-->", boot)
    html = re.sub(r"<style\b", '<style nonce="' + NONCE + '"', html)
    return html

ORACLE = r"""() => {
 const list=document.getElementById('modList');
 const cards=[...list.querySelectorAll(':scope > .mod-row')];
 const box=r=>[r.x,r.y,r.width,r.height];
 const rect=e=>box(e.getBoundingClientRect());
 const hintCount=list.querySelectorAll('[data-mh-proof-track]').length;
 const originalActive=document.activeElement;
 const snapshot=cards.map(row=>{
  const direct=Array.from(row.children);
  const r=row.getBoundingClientRect(),style=getComputedStyle(row);
  if(!row.isConnected||r.width<=0||r.height<=0||style.display!=='grid'||
     style.contentVisibility!=='visible'||style.visibility!=='visible'||style.contain!=='none')
     throw Error('card not fully laid out');
  const link=row.querySelector(':scope > .mod-source > .source-btn');
  const footer=row.querySelector(':scope > .inspect-col');
  const range=document.createRange();range.selectNodeContents(link);
  return {id:row.dataset.id,row:rect(row),direct:direct.map(rect),
   tracks:style.gridTemplateColumns,focusWithin:row.contains(originalActive),
   source:[link.textContent,link.getAttribute('href'),link.clientWidth,link.scrollWidth,
   getComputedStyle(link).textOverflow,rect(link),Array.from(range.getClientRects(),box)],
   labels:[getComputedStyle(row.querySelector('.mod-art'),'::after').content,
           getComputedStyle(row.querySelector('.mod-primary'),'::before').content],
   controls:Array.from(footer.children,b=>[b.dataset.act,b.textContent,b.className,b.getAttribute('aria-pressed'),getComputedStyle(b).fontWeight,rect(b)])};
 });
 if(!window.__trackDriverOracle)window.__trackDriverOracle={cards,snapshot,originalActive};
 const before=window.__trackDriverOracle;
 return {count:cards.length,hintCount,truncated:snapshot.filter(r=>r.source[3]>r.source[2]&&r.source[4]==='ellipsis').length,
   updateAction:snapshot.some(r=>r.controls.some(b=>b[0]==='files'&&b[1]==='Review'&&b[4]==='650')),
   fileGlyph:snapshot.some(r=>r.controls.some(b=>b[0]==='files'&&b[1]==='⇩')),
   twoActions:snapshot.some(r=>r.controls.length===2),threeActions:snapshot.some(r=>r.controls.length===3),
   sameRows:cards.length===before.cards.length&&cards.every((r,i)=>r===before.cards[i]),
   sameBoxesAndEvidence:JSON.stringify(snapshot)===JSON.stringify(before.snapshot),
   sameActive:document.activeElement===before.originalActive};
}"""

def call(page, live, phase, count):
    return page.evaluate(f"([phase,count])=>{proof(live)}(phase,count)", [phase, count])

def settled(page):
    page.evaluate("""async()=>{await document.fonts.ready;await new Promise(resolve=>
       requestAnimationFrame(()=>requestAnimationFrame(resolve)))}""")

def refuse(fn, expected, error_type):
    try:
        fn()
    except error_type as exc:
        require(expected in str(exc), "Unexpected rejection: " + str(exc))
    else:
        raise AssertionError("Expected proof refusal: " + expected)

def open_fixture(browser, live, count, width):
    data = fixture(count)
    html = build(live, data)
    context = browser.new_context(viewport={"width": width, "height": 900},
                                  reduced_motion="reduce", service_workers="block")
    try:
        def route(request_route):
            req = request_route.request
            if req.url == ORIGIN and req.method == "GET" and req.resource_type == "document":
                request_route.fulfill(content_type="text/html", body=html, headers={
                    "Content-Security-Policy": f"style-src 'nonce-{NONCE}'; connect-src 'none'"})
            else:
                request_route.abort()
        context.route("**/*", route)
        page = context.new_page()
        page.goto(ORIGIN, wait_until="load")
        page.wait_for_function("!!window.__ARSENAL_QA__")
        if live:
            page.wait_for_function("document.getElementById('livePill').classList.contains('connected')")
            page.wait_for_function("__RESP__.counts['GET snapshot']>=1&&__RESP__.counts['GET progress']>=1")
        else:
            data["offline"] = True
            report = page.evaluate("data=>modhunterImportCatalog(data)", data)
            require(report == {"ok": True, "count": count, "profileId": PROFILE}, "Offline import not accepted")
        require(page.evaluate("__ARSENAL_QA__.live") is live, "Incorrect production bridge mode")
        require(page.evaluate("__ARSENAL_QA__.getCatalog().profileId") == PROFILE, "Incorrect fixture ownership")
        for name in ("locale-catalogs.js", "preferences.js"):
            page.add_script_tag(content=(ROOT / "native/ui" / name).read_text("utf8"))
        page.locator("#libraryGallery").click()
        page.locator("#galleryGrouping").select_option("all")
        page.wait_for_function("n=>document.querySelectorAll('#modList > .mod-row').length===n", arg=count)
        settled(page)
        return context, page
    except BaseException:
        context.close()
        raise

def run_case(browser, live, density, count, width, *, interaction=False, require_truncation=False):
    """Actual mode entry and same-document comparison. All phases are separate."""
    from playwright.sync_api import Error
    ctx, page = open_fixture(browser, live, count, width)
    try:
        if density == "comfy":
            page.locator("#densityToggle").click()
            settled(page)
        require(page.locator("body").get_attribute("data-density") == density, "Requested density did not apply")
        inspect = page.locator("#row-item-0 .inspect-col [data-act=inspect]")
        inspect.focus()
        require(inspect.evaluate("node=>node===document.activeElement"), "Real inspector did not receive focus")
        if width <= 580:
            require(page.evaluate("""()=>matchMedia('(max-width:580px)').matches&&
              getComputedStyle(document.querySelector('#row-item-0 .inspect-col')).gridColumnEnd==='-1'"""),
                "Narrow footer did not span both tracks")
            before = page.evaluate(ORACLE)
            refuse(lambda: call(page, live, "apply", count), "Unproven measured action-column geometry", Error)
            after = page.evaluate(ORACLE)
            require(before["hintCount"] == after["hintCount"] == 0 and after["sameBoxesAndEvidence"] and after["sameActive"],
                    "Refusal was not inert")
            return {"mode":"narrow-refused", "records":after["count"]}
        before = page.evaluate(ORACLE)
        require(before["count"] == count and before["hintCount"] == 0 and before["twoActions"], "Incomplete baseline")
        require(before["threeActions"] is live and before["updateAction"] is live and before["fileGlyph"] is live,
                "Real bridge action variants not witnessed")
        require(not require_truncation or before["truncated"] > 0, "No actual source truncation witnessed")
        report = call(page, live, "apply", count)
        require(report["fullRecords"] == count and report["allFullyRendered"], "Incomplete diagnostic")
        armed = page.evaluate(ORACLE)
        require(armed["hintCount"] == count and armed["sameRows"] and armed["sameActive"] and armed["sameBoxesAndEvidence"],
                "Exact card/control/source geometry, semantics or focus changed")
        require(call(page, live, "audit", count) == report, "Stable-state audit drift")
        if interaction:
            page.keyboard.press("Enter")
            page.wait_for_function("document.getElementById('detailDrawer').getAttribute('aria-hidden')==='false'")
            require("Owned track fixture 0" in page.locator("#drawerTitle").inner_text(),
                    "Existing focused inspector did not operate on exact card")
        require(call(page, live, "restore", count) is True, "Diagnostic not removed")
        if not interaction:  # actual inspector opening intentionally changes UI state
            after = page.evaluate(ORACLE)
            require(after["hintCount"] == 0 and after["sameRows"] and after["sameBoxesAndEvidence"] and after["sameActive"],
                    "Complete restored geometry, semantics or focus changed")
        return {"mode":"live-mock" if live else "offline", "records":count, "density":density,
                "sharedRules":report["sharedRules"], "truncated":before["truncated"]}
    finally:
        ctx.close()

def lifetime_case(browser, reason):
    from playwright.sync_api import Error
    count = 12
    ctx, page = open_fixture(browser, False, count, 1300)
    try:
        call(page, False, "apply", count)
        if reason == "locale":
            page.evaluate("()=>ModhunterI18n.apply({locale:'it'})")
            page.wait_for_function("document.querySelector('#row-item-0 .inspect-btn').textContent==='Dettagli'")
            message = "live evidence owner changed"
        elif reason == "density":
            page.locator("#densityToggle").click()
            message = "live evidence owner changed"
        elif reason == "font-membership":
            page.evaluate("""()=>{document.fonts.add(new FontFace('Unused QA font', 'local("Arial")'));}""")
            message = "live evidence owner changed"
        elif reason == "footer-css":
            page.evaluate("""()=> {
              const s=document.createElement('style');s.nonce=document.querySelector('style[nonce]').nonce;
              s.textContent='body[data-layout="compact"][data-library-view="gallery"] #row-item-0 .inspect-col{gap:29px}';
              document.head.append(s);if(!s.sheet)throw Error('CSP rejected explicit fixture stylesheet');
            }""")
            message = "complete row, grid or interactive control geometry differs"
        else:
            raise ValueError("Unknown lifetime challenge")
        refuse(lambda: call(page, False, "audit", count), message, Error)
        require(call(page, False, "restore", count) is True and
                page.locator("[data-mh-proof-track]").count() == 0, "Stale style not removed")
        return reason
    finally:
        ctx.close()

def run(browser, full_count=11207):
    """Opt-in from an approved owned Playwright integration only."""
    return {
      "full": [run_case(browser, live, density, full_count, 1300)
               for live in (False, True) for density in ("compact", "comfy")],
      "interaction": [run_case(browser, live, "compact", 12, 900, interaction=True)
                      for live in (False, True)],
      "narrow": [run_case(browser, live, "compact", 12, 580) for live in (False, True)],
      "truncation": run_case(browser, True, "compact", 12, 1250, require_truncation=True),
      "lifetime": [lifetime_case(browser, reason) for reason in ("locale", "density", "font-membership", "footer-css")],
    }
