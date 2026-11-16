"""Render the exact release UI and its v0.11.0 baseline in Chromium.

Screenshots are actual application HTML/CSS/JS with deterministic, clearly
identified API fixtures. They are not native Windows, provider account, or user
profile proof. The Windows workflow supplies separate WebView2/MO2 receipts.
The benchmark keeps all 11,207 mod records and measures complete source variants.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
WEB = "mo2_update_arsenal/web/"
BASELINE = "1a99a66d346b1593273afc7c2b12e2d493c8d5e0"
ASSETS = ("app.template.html", "style.css", "aether_fx.css", "realm.css", "siege.css", "atelier.css", "mod_insights.css", "source_insights.css", "source_insights.js", "mod_insights.js", "app.js", "catalog.json", "aether_fx.js", "realm.js", "siege.js", "realm_layout.html", "siege_layout.html", "MO2_Source_Exporter.ps1")
sys.path.insert(0, str(ROOT / "tests"))
import test_frontend_responsiveness as dashboard  # noqa: E402
import test_watchtower_browser as watchtower  # noqa: E402


def read(path: str, ref: str | None = None) -> str:
    if ref:
        return subprocess.check_output(["git", "show", f"{ref}:{path}"], cwd=ROOT, text=True)
    return (ROOT / path).read_text("utf-8")


def compile_dashboard(ref: str | None = None) -> tuple[str, dict]:
    files = {name: read(WEB + name, ref) for name in ASSETS}
    replacements = {
        "/*__STYLE__*/": "\n".join(files[name] for name in ("style.css", "aether_fx.css", "realm.css", "siege.css", "atelier.css", "mod_insights.css", "source_insights.css")),
        "/*__SCRIPT__*/": files["source_insights.js"] + "\n" + files["mod_insights.js"] + "\n" + files["app.js"].replace("/*__POWERSHELL__*/", json.dumps(files["MO2_Source_Exporter.ps1"], ensure_ascii=False)),
        "/*__CATALOG__*/": json.dumps(json.loads(files["catalog.json"]), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"),
        "/*__AETHER_SCRIPT__*/": files["aether_fx.js"],
        "/*__REALM_SCRIPT__*/": files["realm.js"],
        "/*__SIEGE_SCRIPT__*/": files["siege.js"],
        "<!--__REALM_LAYOUT__-->": files["realm_layout.html"].replace("<!--__SIEGE_LAYOUT__-->", files["siege_layout.html"]),
    }
    html = files["app.template.html"]
    for marker, value in replacements.items():
        if marker not in html:
            raise AssertionError(f"Missing compiled dashboard marker: {marker}")
        html = html.replace(marker, value)
    return html, {WEB + name: hashlib.sha256(value.encode()).hexdigest() for name, value in files.items()}


def screenshot_catalog() -> dict:
    value = json.loads(read(WEB + "catalog.json", BASELINE))
    value.update(profileId="visual-acceptance", profileName="Skyrim SE · visual acceptance fixture", game="skyrimspecialedition")
    now = int(time.time())
    for index, item in enumerate(value["items"]):
        # The frozen standalone catalog stores the authentic Nexus identity in
        # its URL. A connected snapshot supplies nexusId too; derive exactly
        # that ID so screenshots exercise the existing exact-file control.
        source_url = item.get("source", {}).get("url", "")
        nexus = re.match(r"https://www\.nexusmods\.com/[^/]+/mods/(\d+)(?:[/?#]|$)", source_url)
        if nexus:
            item["nexusId"] = int(nexus[1])
        if index % 5 in (0, 1):
            current = item.get("installedVersion") or "1.0"
            item["update"] = {"verdict": "candidate" if index % 5 == 0 else "current", "latestVersion": "2.6" if index % 5 == 0 else current, "checkedAt": now, "expiresAt": now + 14400, "source": "visual-acceptance-fixture"}
    return value


def fixture_page(html: str, catalog: dict) -> str:
    boot = dashboard.BOOT.replace("__CATALOG__", json.dumps(catalog).replace("</", "<\\/")).replace("__FAIL__", "0").replace("prefs:{}", "prefs:{autoCheckOnLaunch:false}")
    return html.replace("<!--__MO2_BOOT__-->", boot)


def settle(page):
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")


def dimensions(page) -> dict:
    return page.evaluate("""() => ({width:innerWidth, scrollWidth:document.documentElement.scrollWidth,scrollY,
      height:innerHeight, rows:document.querySelectorAll('#modList .mod-row').length,
      firstRow:document.querySelector('#modList .mod-row')?.getBoundingClientRect().toJSON(),
      libraryView:document.body.dataset.libraryView, density:document.body.dataset.density})""")


def assert_no_overflow(page, description):
    state = dimensions(page)
    if state["scrollWidth"] > state["width"] + 1:
        raise AssertionError(f"{description}: document overflows: {state}")


def assert_library_geometry(page, description):
    """Check local control/text collisions that document width cannot detect."""
    state = page.evaluate("""() => {
      const box=selector=>document.querySelector(selector)?.getBoundingClientRect().toJSON();
      const headings=[...document.querySelectorAll('.list-header>span')].filter(element=>
        element.getBoundingClientRect().width>0&&element.getBoundingClientRect().height>0
      ).map(element=>{const rectangles=[...element.querySelectorAll('svg')].map(icon=>icon.getBoundingClientRect());
        const walker=document.createTreeWalker(element,NodeFilter.SHOW_TEXT);let text;
        while(text=walker.nextNode())if(text.textContent.trim()&&!text.parentElement.closest('.mh-visually-hidden')){
          const range=document.createRange();range.selectNode(text);rectangles.push(range.getBoundingClientRect());}
        const box=rectangles.length?{left:Math.min(...rectangles.map(rect=>rect.left)),right:Math.max(...rectangles.map(rect=>rect.right))}:element.getBoundingClientRect().toJSON();
        return {text:element.textContent,box};});
      return {width:innerWidth,headings,sort:box('#sortSelect'),density:box('#densityToggle'),
        provider:box('.library-provider-control')};
    }""")
    headings = sorted(state["headings"], key=lambda heading: heading["box"]["left"])
    for previous, current in zip(headings, headings[1:]):
        assert previous["box"]["right"] <= current["box"]["left"] + 0.5, f"{description}: heading text overlaps: {previous} / {current}"
    if state["width"] <= 400:
        assert state["sort"]["width"] >= 100, f"{description}: sort label is unreadably narrow: {state}"
        density, provider = state["density"], state["provider"]
        overlap_x = min(density["right"], provider["right"]) - max(density["left"], provider["left"])
        overlap_y = min(density["bottom"], provider["bottom"]) - max(density["top"], provider["top"])
        assert overlap_x <= 0.5 or overlap_y <= 0.5, f"{description}: source and density controls overlap: {state}"
    return state


def assert_picker_footer_visible(page, description):
    state = page.evaluate("""() => ({width:innerWidth,height:innerHeight,
      dialog:document.querySelector('#filePickerVeil .file-picker').getBoundingClientRect().toJSON(),
      footer:document.querySelector('#filePickerVeil .picker-tail').getBoundingClientRect().toJSON()})""")
    dialog, footer = state["dialog"], state["footer"]
    assert footer["width"] > 0 and footer["height"] > 0, f"{description}: picker footer disappeared: {state}"
    assert footer["top"] >= max(0, dialog["top"]) - 1 and footer["bottom"] <= min(state["height"], dialog["bottom"]) + 1, f"{description}: picker footer clipped vertically: {state}"
    assert footer["left"] >= max(0, dialog["left"]) - 1 and footer["right"] <= min(state["width"], dialog["right"]) + 1, f"{description}: picker footer clipped horizontally: {state}"
    return state


def assert_gallery_actions(page, description):
    state = page.evaluate("""() => [...document.querySelectorAll('#modList .mod-row')]
      .filter(row=>row.querySelector('.file-download-btn'))
      .map(row=>({id:row.dataset.id,box:row.getBoundingClientRect().toJSON(),
        controls:[...row.querySelectorAll('.source-btn,.file-download-btn,.favorite-btn,.inspect-btn')]
          .map(control=>({name:control.className,text:control.textContent,box:control.getBoundingClientRect().toJSON()}))}))""")
    assert state, f"{description}: fixture never exercised a connected Nexus card"
    for row in state:
        box = row["box"]
        for control in row["controls"]:
            bounds = control["box"]
            assert bounds["width"] > 0 and bounds["height"] > 0, f"{description}: hidden gallery action: {row}"
            assert bounds["left"] >= box["left"] - 1 and bounds["right"] <= box["right"] + 1 and bounds["top"] >= box["top"] - 1 and bounds["bottom"] <= box["bottom"] + 1, f"{description}: gallery action exceeds card: {row}"
        for index, first in enumerate(row["controls"]):
            for second in row["controls"][index + 1:]:
                left, right = first["box"], second["box"]
                overlap_x = min(left["right"], right["right"]) - max(left["left"], right["left"])
                overlap_y = min(left["bottom"], right["bottom"]) - max(left["top"], right["top"])
                assert overlap_x <= 0.5 or overlap_y <= 0.5, f"{description}: gallery actions overlap: {row}"
    return {"nexusCards": len(state), "firstCard": state[0], "minimumCardWidth": min(row["box"]["width"] for row in state)}


def scroll_dashboard_to_top(page):
    # A focused control can restore an ancestor's scroll offset after a theme
    # switch. Reset actual scroll containers, not just the window, and wait for
    # layout before recording the top of the application.
    page.evaluate("""() => {document.activeElement?.blur();scrollTo({top:0,left:0,behavior:'instant'});
      for(const element of document.querySelectorAll('.shell,.maincol,.bodypad,.workbench'))
        if(element.scrollTop)element.scrollTo({top:0,left:0,behavior:'instant'});}""")
    settle(page)
    page.wait_for_function("scrollY===0")


def set_dashboard_theme(page, theme):
    if page.locator("body").get_attribute("data-theme") == theme:
        return
    previous = page.locator("body").get_attribute("data-workspace")
    if not page.locator("#themeToggle").is_visible():
        page.locator('.workspace-tabs [data-workspace="extras"]').click()
    page.locator("#themeToggle").click()
    if page.locator("body").get_attribute("data-workspace") != previous:
        page.locator('.workspace-tabs [data-workspace="'+previous+'"]').click()
    assert page.locator("body").get_attribute("data-theme") == theme


def capture_dashboard(browser, html, fixture, output, candidate):
    context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("http://visual.modhunter.invalid/", lambda route: route.fulfill(content_type="text/html", body=fixture_page(html, fixture)))
    page.goto("http://visual.modhunter.invalid/", wait_until="load")
    page.wait_for_selector("#modList .mod-row")
    page.wait_for_function("document.getElementById('livePill').classList.contains('connected')")
    page.wait_for_function("n=>document.querySelectorAll('#modList .mod-row').length===n", arg=len(fixture["items"]))
    states = []
    for width in (1440, 900, 390):
        page.set_viewport_size({"width": width, "height": 1000 if width > 400 else 844})
        for theme in ("night", "snow"):
            set_dashboard_theme(page, theme)
            scroll_dashboard_to_top(page)
            shot = f"library-{width}-{'dark' if theme == 'night' else 'light'}.png"
            page.screenshot(path=str(output / shot))
            state = dimensions(page)
            state.update(screenshot=shot, theme=theme)
            states.append(state)
            if candidate:
                assert_no_overflow(page, shot)
                state["geometry"] = assert_library_geometry(page, shot)
    page.set_viewport_size({"width": 1440, "height": 1000})
    set_dashboard_theme(page, "night")
    page.locator(".mod-row").first.locator(".inspect-btn").click()
    settle(page)
    page.screenshot(path=str(output / "inspector-1440-dark.png"))
    page.keyboard.press("Escape")
    page.locator('.workspace-tabs [data-workspace="updates"]').click()
    page.wait_for_selector(".workshop-item")
    scroll_dashboard_to_top(page)
    page.screenshot(path=str(output / "updates-1440-dark.png"))
    for width in (900, 390):
        page.set_viewport_size({"width": width, "height": 1000 if width > 400 else 844})
        scroll_dashboard_to_top(page)
        page.screenshot(path=str(output / f"updates-{width}-dark.png"))
        if candidate:
            assert_no_overflow(page, f"updates/{width}")
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.locator('.workspace-tabs [data-workspace="mods"]').click()
    scroll_dashboard_to_top(page)
    page.keyboard.press("Control+k")
    if candidate:
        assert page.locator("#search").evaluate("e=>e===document.activeElement"), "Search shortcut lost focus"
    page.screenshot(path=str(output / "library-focus-1440-dark.png"))
    if candidate:
        page.locator("#libraryGallery").click()
        settle(page)
        assert page.locator("body").get_attribute("data-library-view") == "gallery"
        assert page.locator("#modList .mod-row").count() == len(fixture["items"])
        page.screenshot(path=str(output / "library-gallery-1440-dark.png"))
        gallery_geometry = assert_gallery_actions(page, "library-gallery-1440-dark")
        review = page.locator('#modList .mod-row').filter(has=page.locator('.file-download-btn', has_text='Review')).first
        review.scroll_into_view_if_needed();settle(page)
        page.screenshot(path=str(output / "library-gallery-nexus-controls-1440-dark.png"))
        states.append({"screenshot":"library-gallery-nexus-controls-1440-dark.png","theme":"night","geometry":gallery_geometry,**dimensions(page)})
        scroll_dashboard_to_top(page)
        set_dashboard_theme(page, "snow")
        page.screenshot(path=str(output / "library-gallery-1440-light.png"))
        assert_gallery_actions(page, "library-gallery-1440-light")
        page.locator("#densityToggle").click()
        page.screenshot(path=str(output / "library-gallery-comfortable.png"))
        page.locator("#libraryList").click()
        page.locator("#densityToggle").click()
        for selector in ("#railToggle",):
            if page.locator(selector).count():
                page.locator(selector).click()
                settle(page)
                page.screenshot(path=str(output / "library-navigation-compact.png"))
                page.locator(selector).click()
        # The actual existing item and every metadata/action cell must tolerate
        # long names; no third-party library is substituted for the app renderer.
        page.evaluate("() => {__RESP__.catalog.items[0].name='Long filename acceptance — '+('Detailed Mesh and Texture Compatibility Edition ').repeat(8)+'.package';__RESP__.generation++;}")
        page.wait_for_function("document.querySelector('.mod-title').textContent.includes('Long filename acceptance')")
        for width in (1440, 900, 390):
            page.set_viewport_size({"width": width, "height": 1000 if width > 400 else 844})
            scroll_dashboard_to_top(page)
            assert_no_overflow(page, f"long-filename/{width}")
            page.screenshot(path=str(output / f"long-filename-{width}.png"))
    if errors:
        raise AssertionError(f"Dashboard JavaScript errors: {errors}")
    context.close()
    return states


def capture_public_artwork(browser, html, output, candidate):
    manifest = json.loads((ROOT / "tests/fixtures/visual_public_catalog.json").read_text("utf-8"))
    sources = manifest["sources"]
    fixture = dashboard.catalog(profile="public-artwork-fixture", count=len(sources))
    fixture.update(profileName="Official project artwork · synthetic installed-version fixture", game="minecraft", gameRuntime="QA fixture")
    fixture["groups"][0]["name"] = "Public Modrinth projects / artwork acceptance"
    now = int(time.time())
    for index, (item, source) in enumerate(zip(fixture["items"], sources)):
        item.update(name=source["name"], group=fixture["groups"][0]["name"], installedVersion="QA 1.0", author="", nexusId=0, screenshotNote=source["description"], audit={}, source={"type": "modrinth", "verified": True, "url": source["projectUrl"]}, update={"verdict": "candidate" if index % 3 == 0 else "current", "latestVersion": "QA 2.0" if index % 3 == 0 else "QA 1.0", "checkedAt": now, "expiresAt": now + 14400, "source": "visual-acceptance-fixture", "thumbnailUrl": source["thumbnailUrl"], "thumbnailKind": "icon"})
    context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
    page = context.new_page();errors=[]
    page.on("pageerror", lambda error: errors.append(str(error)))
    assets = {source["thumbnailUrl"]: source for source in sources}
    def route(request):
        url = request.request.url
        if url == "http://artwork.modhunter.invalid/":
            request.fulfill(content_type="text/html", body=fixture_page(html, fixture))
        elif url in assets:
            source = assets[url]
            raw = (ROOT / "tests/fixtures" / source["thumbnailFile"]).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == source["sha256"]
            request.fulfill(content_type=source["mimeType"], body=raw)
        else:
            request.abort()
    context.route("**/*", route)
    page.goto("http://artwork.modhunter.invalid/", wait_until="load")
    page.wait_for_function("n=>document.querySelectorAll('#modList .mod-row').length===n", arg=len(sources))
    for theme in ("night", "snow"):
        set_dashboard_theme(page, theme);scroll_dashboard_to_top(page)
        page.screenshot(path=str(output / f"public-projects-list-1440-{'dark' if theme=='night' else 'light'}.png"))
    if candidate:
        page.locator("#libraryGallery").click()
        for image in page.locator("#modList .mod-preview-image").all():
            image.scroll_into_view_if_needed()
            page.wait_for_function("image=>image.complete&&image.naturalWidth>0", arg=image.element_handle(), timeout=10000)
        scroll_dashboard_to_top(page)
        assert page.locator("#modList .mod-preview-image").count() == len(sources)
        for width in (1440, 900, 390):
            page.set_viewport_size({"width": width, "height": 1000 if width > 400 else 844})
            for theme in ("night", "snow"):
                set_dashboard_theme(page, theme);scroll_dashboard_to_top(page);assert_no_overflow(page, f"official-artwork/{width}/{theme}")
                page.screenshot(path=str(output / f"public-projects-gallery-{width}-{'dark' if theme=='night' else 'light'}.png"))
        page.set_viewport_size({"width":1440,"height":1000});set_dashboard_theme(page,"night")
        page.locator("#row-item-0 .mod-art").click();settle(page)
        page.screenshot(path=str(output / "public-projects-inspector-1440-dark.png"))
    if errors:
        raise AssertionError(f"Public artwork UI errors: {errors}")
    context.close()
    return {"projects":len(sources),"metadata":"tests/fixtures/visual_public_catalog.json","fixtureSha256":hashlib.sha256(json.dumps(fixture,sort_keys=True).encode()).hexdigest(),"scope":manifest["scope"],"sources":[{"projectUrl":source["projectUrl"],"apiUrl":source["apiUrl"],"thumbnailUrl":source["thumbnailUrl"],"sha256":source["sha256"],"retrievedAt":source["retrievedAt"]} for source in sources]}


def updater_fixture_namespace(html):
    """Reuse the actual updater suite's fixture setup without running its suite."""
    path = ROOT / "tests/updater_sss_browser_qa.py"
    module = ast.parse(path.read_text("utf-8"), filename=str(path))
    setup = []
    for statement in module.body:
        if isinstance(statement, ast.With):
            break
        setup.append(statement)
    else:
        raise AssertionError("Could not locate the updater fixture/runtime boundary")
    namespace = {"__file__": str(path), "__name__": "modhunter_visual_update_fixture"}
    exec(compile(ast.Module(body=setup, type_ignores=[]), str(path), "exec"), namespace)
    namespace["raw_html"] = html
    return namespace


def capture_operational_views(browser, html, output, candidate):
    fixture = updater_fixture_namespace(html)
    captured = []
    for theme in ("night", "snow"):
        context = browser.new_context(viewport={"width":1440,"height":1000}, reduced_motion="reduce")
        page = context.new_page();page.set_default_timeout(15000);errors=[]
        page.on("pageerror",lambda error:errors.append(str(error)))
        page.on("dialog",lambda dialog:dialog.accept())
        page.set_content(fixture["fixture_html"](fixture["mock"]), wait_until="load")
        set_dashboard_theme(page, theme)
        def show(panel):
            page.locator('.workspace-tabs [data-workspace="'+panel+'"]').click()
        def capture(name):
            settle(page)
            filename=f"operation-{name}-1440-{'dark' if theme=='night' else 'light'}.png"
            page.screenshot(path=str(output/filename));captured.append(filename)
            if candidate:
                assert_no_overflow(page,filename)
                if name == "exact-file":
                    assert_picker_footer_visible(page,filename)
                if name in ("exact-file","install-brief"):
                    for width in (900,390):
                        page.set_viewport_size({"width":width,"height":1000 if width>400 else 844});settle(page)
                        narrow=f"operation-{name}-{width}-{'dark' if theme=='night' else 'light'}.png"
                        page.screenshot(path=str(output/narrow));captured.append(narrow)
                        assert_no_overflow(page,narrow)
                        if name == "exact-file":
                            assert_picker_footer_visible(page,narrow)
                    page.set_viewport_size({"width":1440,"height":1000})
        show("updates")
        page.locator("#workshopResults .workshop-item").first.wait_for()
        page.locator("#workshopReviewNext").click()
        page.locator("#filePickerVeil").wait_for(state="visible")
        page.locator(".picker-suggested").first.wait_for()
        assert page.locator("#filePickerMatchCount").inner_text()=="3 / 3 files"
        capture("exact-file")
        page.locator(".picker-suggested button").click()
        page.wait_for_function("document.getElementById('workshopCount').textContent.includes('1 REVIEWED')")
        page.locator("#workshopInspectQueue").click()
        page.locator("#queueReviewVeil").wait_for(state="visible")
        assert page.locator("#queueReviewRows .queue-review-row").count()==1
        capture("queue-review")
        page.locator("#queueReviewCancel").click()
        page.locator("#workshopQueue").click()
        show("downloads")
        page.locator("#liveDownloadList .download-ticket").wait_for()
        capture("downloads")
        # The existing updater fixture alone receives this command. No real
        # account, download, installation, or filesystem operation is involved.
        page.evaluate("Object.values(__SSS_MOCK__.tickets).forEach(ticket=>ticket.status='complete')")
        show("updates");page.locator("#workshopRefreshDownloads").click();show("downloads")
        page.locator("button[data-download-details]").wait_for()
        page.locator("button[data-download-details]").click()
        page.wait_for_function("document.getElementById('installBriefTitle').textContent==='Arcane Example'")
        assert page.locator("#installPreviewConfirm").is_enabled()
        capture("install-brief")
        page.keyboard.press("Escape")
        show("history")
        page.locator("#activityResults summary").first.wait_for()
        page.locator("#activityResults summary").first.click()
        capture("history")
        assert not errors, errors
        context.close()
    return {"screenshots":captured,"scope":"Actual exact-file/queue/download/install-brief/history controls driven by the existing SSS deterministic browser fixture. No native operation is claimed."}


def capture_preserved_games(browser, html, output, candidate):
    """Follow existing controls into the original practice games and FX Studio."""
    context = browser.new_context(viewport={"width":1440,"height":1000}, reduced_motion="reduce")
    origin = "http://games.modhunter.invalid/"
    errors, captured = [], []
    context.on("page", lambda page: page.on("pageerror", lambda error: errors.append(str(error))))
    context.route("**/*", lambda route: route.fulfill(content_type="text/html",body=html) if route.request.url.startswith(origin) else route.abort())
    page = context.new_page()
    page.goto(origin,wait_until="load")
    page.wait_for_function("!!window.__ARSENAL_QA__&&!!window.AetherFX")
    page.locator('.workspace-tabs [data-workspace="extras"]').click()
    page.locator("#gameBoard").wait_for(state="visible")
    page.locator("#gameBoard").evaluate("element=>element.scrollIntoView({block:'start',behavior:'instant'})")
    settle(page)
    page.screenshot(path=str(output/"extras-progression-1440-dark.png"));captured.append("extras-progression-1440-dark.png")
    if candidate:
        assert_no_overflow(page,"extras-progression")
    page.set_viewport_size({"width":900,"height":1000})
    set_dashboard_theme(page,"snow");scroll_dashboard_to_top(page)
    page.locator("#fxSettingsToggle").click()
    page.locator("#fxStudio").wait_for(state="visible")
    page.locator("#fxIntensity").select_option("subtle")
    assert page.evaluate("AetherFX.getState().options.intensity") == "subtle"
    assert page.locator("#fxSettingsToggle").get_attribute("aria-expanded") == "true"
    settle(page)
    page.screenshot(path=str(output/"extras-fx-studio-900-light.png"));captured.append("extras-fx-studio-900-light.png")
    if candidate:
        assert_no_overflow(page,"extras-fx-studio")
    page.locator("#fxStudioDone").click()
    assert page.locator("#fxStudio").is_hidden()
    page.locator('.workspace-tabs [data-workspace="mods"]').click()
    assert page.locator("#modList .mod-row").count() == 227
    page.locator('.workspace-tabs [data-workspace="extras"]').click()
    with context.expect_page() as opened:
        page.locator("#realmLaunchTop").click()
    realm = opened.value
    realm.wait_for_load_state("load")
    realm.wait_for_function("window.__REALM_QA__&&!__REALM_QA__.getState().loading")
    assert "realm=1" in realm.url
    assert realm.locator(".shell").count() == 0
    initial = realm.evaluate("__REALM_QA__.getState()")
    assert initial["profileId"] == "practice" and not initial["connected"] and initial["wallet"]["available"] == 30, initial

    def capture_game(name,width=1440,canvas="#realmCanvas"):
        realm.set_viewport_size({"width":width,"height":1000})
        scroll_dashboard_to_top(realm)
        if canvas:
            realm.locator(canvas).wait_for(state="visible")
            realm.wait_for_function("selector=>{const canvas=document.querySelector(selector);return canvas.width>0&&canvas.height>0&&canvas.getContext('2d').getImageData(Math.floor(canvas.width/2),Math.floor(canvas.height/2),1,1).data[3]>0}",arg=canvas)
        filename=f"game-{name}-{width}.png"
        realm.screenshot(path=str(output/filename));captured.append(filename)
        if candidate:
            assert_no_overflow(realm,filename)

    capture_game("shiverfell")
    realm.locator('button[data-panel="forge"]').click()
    realm.locator('button[data-buy="rift-key"]').click()
    realm.wait_for_function("__REALM_QA__.getState().wallet.available===26")
    assert realm.evaluate("__REALM_QA__.getState().world.inventory.riftKeys") == 1
    realm.locator('button[data-panel="rift"]').click()
    realm.locator('button[data-action="start-rift"]').click()
    realm.wait_for_function("__REALM_QA__.getState().mode==='rift'")
    assert realm.evaluate("__REALM_QA__.getState().enemies") >= 3
    capture_game("rift")
    realm.locator('button[data-action="exit-run"]').click()
    realm.wait_for_function("__REALM_QA__.getState().mode==='overworld'")
    realm.locator('button[data-action="start-expedition"]').click()
    realm.wait_for_function("__REALM_QA__.getState().mode==='expedition'")
    capture_game("expedition",900)
    realm.locator('button[data-action="exit-run"]').click()
    realm.wait_for_function("__REALM_QA__.getState().mode==='overworld'")
    realm.locator('button[data-action="start-siege"]').click()
    realm.locator("#siegeMenu").wait_for(state="visible")
    assert "PRACTICE" in realm.locator("#siegeConnection").inner_text()
    assert realm.locator("#siegeRegionMap [data-atlas-region]").count() == 12
    capture_game("siege-menu",canvas=None)
    realm.locator("#siegeStart").click()
    realm.locator("#siegeGame").wait_for(state="visible")
    capture_game("siege-arena",900,canvas="#siegeCanvas")
    realm.locator("#siegePause").click()
    realm.locator("#siegeDialog").wait_for(state="visible")
    realm.keyboard.press("Escape")
    assert realm.locator("#siegeDialog").is_hidden()
    realm.locator("#siegeBack").click()
    assert realm.locator("#siegeRoot").is_hidden()
    assert realm.locator("#realmCanvas").is_visible()
    assert realm.evaluate("__REALM_QA__.getState().wallet.available") == 26
    with context.expect_page() as returned:
        realm.locator("#realmReturn").click()
    dashboard_page = returned.value
    dashboard_page.wait_for_function("!!window.__ARSENAL_QA__")
    dashboard_page.locator('.workspace-tabs [data-workspace="mods"]').click()
    dashboard_page.wait_for_selector("#modList .mod-row")
    assert dashboard_page.locator("#modList .mod-row").count() == 227
    assert not errors, errors
    context.close()
    return {"screenshots":captured,"scope":"Original games entered through existing UI links and controls in isolated offline practice. Actual Forge purchase, Rift/Ironman/Siege entry, pause and return navigation; no ranked currency, live MO2 progression, fabricated canvas or test state grants."}


def capture_watchtower(browser, ref, output, candidate):
    context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
    state = watchtower.snapshot(8)
    state["version"] = json.loads(read("native/crates/modhunter-desktop/tauri.conf.json", ref))["version"]
    titles = ["SKSE release notes", "Address Library — runtime compatibility", "SkyUI release page", "PapyrusUtil — installation notes", "Community Shaders", "SSE Engine Fixes", "Open Animation Replacer", "Long creator page name / compatibility and version history"]
    for item, title in zip(state["watches"], titles):
        item["title"] = title
        item["latest"]["title"] = title
    boot = read("tests/fixtures/watchtower_native_fixture.js", ref)
    context.add_init_script(boot.replace("__WATCHTOWER_INITIAL_STATE__", json.dumps(state)))
    allowed = {name for name in (ROOT / "native/ui").iterdir() if name.suffix in (".html", ".css", ".js")}
    names = {name.name for name in allowed}
    def route(request):
        name = request.request.url.split("/")[-1]
        if request.request.url.startswith("http://watchtower.invalid/") and name in names:
            content_type = "text/css" if name.endswith(".css") else "text/javascript" if name.endswith(".js") else "text/html"
            request.fulfill(body=read("native/ui/" + name, ref), content_type=content_type)
        else:
            request.abort()
    context.route("**/*", route)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto("http://watchtower.invalid/watchtower.html", wait_until="networkidle")
    page.wait_for_function("!document.querySelector('#addPage').disabled")
    for width in (1440, 900, 390):
        page.set_viewport_size({"width": width, "height": 1000 if width > 400 else 844})
        for theme in ("dark", "light"):
            page.evaluate("theme=>ModhunterI18n.apply({theme,locale:'en'})", theme)
            settle(page)
            name = f"watchtower-{width}-{theme}.png"
            page.screenshot(path=str(output / name))
            if candidate:
                assert_no_overflow(page, name)
    if candidate:
        page.set_viewport_size({"width":1440,"height":1000})
        page.evaluate("ModhunterI18n.apply({theme:'dark',locale:'en'})")
        page.locator("#toggleNavigation").click();settle(page)
        page.screenshot(path=str(output / "watchtower-navigation-compact.png"))
        page.locator("#toggleNavigation").click()
    page.set_viewport_size({"width": 900, "height": 1000})
    page.evaluate("ModhunterI18n.apply({theme:'light',locale:'en'})")
    page.locator("#addPage").click()
    page.locator("#pageTitle").fill("A deliberately long release title — compatibility and install notes")
    settle(page)
    page.screenshot(path=str(output / "watchtower-add-dialog-900-light.png"))
    page.keyboard.press("Escape")
    page.locator('#navigation [data-view="settings"]').click()
    settle(page)
    page.screenshot(path=str(output / "watchtower-settings-900-light.png"))
    if errors:
        raise AssertionError(f"Watchtower JavaScript errors: {errors}")
    context.close()


SHELL_BOOT = """window.__SHELL_ACTIONS__=[];window.__TAURI__={event:{async listen(){return ()=>{}}},core:{async invoke(command,args){__SHELL_ACTIONS__.push({command,...args});return {ok:true,state:{revision:1,epoch:1,tabs:[{id:1,title:'Mod library',url:'http://modhunter.invalid/Modhunter.html',mode:'dashboard',loading:false},{id:2,title:'A long source title — compatibility and exact files',url:'https://example.invalid/release',mode:'browser',loading:false}],activeId:1,connected:false,offlineProfile:'Skyrim SE · visual acceptance fixture',offlineModCount:11207,stats:{mods:11207,elapsedMs:254.6,metadataCacheHits:11207}}}}}};"""


def capture_shell(browser, ref, output, candidate):
    html = read("native/ui/shell.html", ref)
    import re
    html = re.sub(r'<link rel="stylesheet" href="([^"]+)">', lambda match: "<style>" + read("desktop/chrome.css" if match[1] == "chrome.css" else "native/ui/" + match[1], ref) + "</style>", html)
    html = re.sub(r'<script src="([^"]+)"></script>', lambda match: "<script>" + read("native/ui/" + match[1], ref) + "</script>", html)
    html = html.replace("<head>", "<head><script>" + SHELL_BOOT + "</script>", 1) if "<head>" in html else html.replace("<head", "<head", 1)
    # shell.html places attributes after the head on a single line; the boot is
    # inserted before its first executable script independent of formatting.
    if "window.__SHELL_ACTIONS__" not in html:
        html = html.replace("<script>", "<script>" + SHELL_BOOT + "</script><script>", 1)
    context = browser.new_context(viewport={"width": 1440, "height": 240}, reduced_motion="reduce")
    page = context.new_page(); errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_content(html, wait_until="load")
    page.wait_for_selector(".tab")
    for width in (1440, 900):
        page.set_viewport_size({"width": width, "height": 240})
        for theme in ("dark", "light"):
            page.evaluate("theme=>ModhunterI18n.apply({theme,locale:'en'})", theme)
            settle(page)
            page.screenshot(path=str(output / f"desktop-chrome-{width}-{theme}.png"))
            if candidate:
                assert_no_overflow(page, f"desktop-chrome/{width}/{theme}")
    if errors:
        raise AssertionError(f"Desktop chrome JavaScript errors: {errors}")
    context.close()


def benchmark(browser, variants, count, rounds):
    fixture = dashboard.catalog(count=count)
    fixture["offline"] = True
    result = {"mods": count, "rounds": rounds, "fixtureSha256": hashlib.sha256(json.dumps(fixture, sort_keys=True).encode()).hexdigest(), "method": "Complete baseline and candidate source variants; one warmup then alternating variant order, new contexts, complete import + frame/layout, exact last-item search, all DOM results preserved. Search/import timing method is unchanged from candidate1. Additional view/navigation timings use real clicks through frame/layout, after search completes. Browser fixture only; excludes native import and provider I/O.", "variants": {name: [] for name in variants}}
    for run in range(rounds + 1):
        for name in (list(variants) if run % 2 == 0 else list(reversed(variants))):
            context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
            page = context.new_page(); page.set_default_timeout(60000)
            page.set_content(variants[name], wait_until="load")
            page.wait_for_function("!!window.__ARSENAL_QA__")
            sample = page.evaluate("""async ({fixture,count})=>{
              const start=performance.now();window.modhunterImportCatalog(fixture);
              await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
              const renderMs=performance.now()-start;
              const rows=[...document.querySelectorAll('#modList .mod-row')];
              const complete=rows.length===count && rows.at(-1)?.dataset.id==='item-'+(count-1);
              const allLaidOut=rows.every(row=>row.getClientRects().length>0);
              const sample=[rows[0],rows[Math.floor(count/2)],rows.at(-1)].filter(Boolean);
              const noContentVisibility=sample.every(row=>getComputedStyle(row).contentVisibility==='visible');
              window.__VISUAL_SURVIVOR__=rows.at(-1);
              return {renderMs,rows:rows.length,complete,allLaidOut,noContentVisibility};
            }""", {"fixture": fixture, "count": count})
            assert sample["complete"] and sample["allLaidOut"] and sample["noContentVisibility"], sample
            start = time.perf_counter()
            page.locator("#search").fill(f"Unique Fixture {count-1}")
            page.wait_for_function("document.querySelectorAll('#modList .mod-row').length===1")
            assert page.locator("#modList .mod-row").get_attribute("data-id") == f"item-{count-1}"
            sample["lastItemSearchMs"] = (time.perf_counter() - start) * 1000
            sample["survivingRowReusedDuringSearch"] = page.evaluate("document.querySelector('#modList .mod-row')===window.__VISUAL_SURVIVOR__")
            page.locator("#search").fill("")
            page.wait_for_function("n=>document.querySelectorAll('#modList .mod-row').length===n", arg=count)
            sample["survivingRowReusedAfterRestore"] = page.evaluate("id=>document.getElementById('row-'+id)===window.__VISUAL_SURVIVOR__", f"item-{count-1}")
            sample["nodes"] = page.evaluate("""() => ({document:document.querySelectorAll('*').length,
              library:document.querySelector('#modList').querySelectorAll('*').length,
              row:document.querySelector('#modList .mod-row').querySelectorAll('*').length,
              previewImages:document.querySelectorAll('#modList .mod-preview-image').length})""")
            start = time.perf_counter()
            page.locator("#sortSelect").select_option("name");settle(page)
            sample["sortMs"] = (time.perf_counter() - start) * 1000
            assert page.locator("#modList .mod-row").count() == count
            sample["survivingRowReusedAfterSort"] = page.evaluate("id=>document.getElementById('row-'+id)===window.__VISUAL_SURVIVOR__", f"item-{count-1}")
            if name == "after":
                assert all(sample[key] for key in ("survivingRowReusedDuringSearch", "survivingRowReusedAfterRestore", "survivingRowReusedAfterSort")), sample
            page.locator("#sortSelect").select_option("priority");settle(page)

            def timed_click(selector):
                started = time.perf_counter()
                page.locator(selector).click()
                settle(page)
                return (time.perf_counter() - started) * 1000

            sample["navigationUpdatesMs"] = timed_click('.workspace-tabs [data-workspace="updates"]')
            assert page.locator("body").get_attribute("data-workspace") == "updates"
            sample["navigationLibraryMs"] = timed_click('.workspace-tabs [data-workspace="mods"]')
            assert page.locator("body").get_attribute("data-workspace") == "mods"
            assert page.locator("#modList .mod-row").count() == count
            sample["densityMs"] = timed_click("#densityToggle")
            assert page.locator("#modList .mod-row").count() == count
            page.locator("#densityToggle").click();settle(page)
            if name == "after":
                sample["galleryMs"] = timed_click("#libraryGallery")
                assert page.locator("#modList .mod-row").count() == count
                sample["galleryDensityMs"] = timed_click("#densityToggle")
                assert page.locator("#modList .mod-row").count() == count
                sample["listMs"] = timed_click("#libraryList")
                assert page.locator("#modList .mod-row").count() == count
                page.locator("#densityToggle").click();settle(page)
            if run:
                result["variants"][name].append(sample)
            context.close()
    metrics = ("renderMs", "lastItemSearchMs", "sortMs", "navigationUpdatesMs", "navigationLibraryMs", "densityMs", "galleryMs", "galleryDensityMs", "listMs")
    result["medians"] = {name: {metric: statistics.median(row[metric] for row in samples) for metric in metrics if all(metric in row for row in samples)} for name, samples in result["variants"].items()}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(os.environ.get("MODHUNTER_QA_OUTPUT", ROOT / "native/target/visual-proof")) / "visual-release")
    parser.add_argument("--mods", type=int, default=11207)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--skip-benchmark", action="store_true")
    args = parser.parse_args()
    if args.mods < 10000 or args.rounds < 3:
        parser.error("Visual release acceptance requires >=10,000 mods and >=3 measured runs")
    args.output.mkdir(parents=True, exist_ok=True)
    start_status = subprocess.check_output(["git","status","--porcelain","--untracked-files=all"], cwd=ROOT, text=True).splitlines()
    if start_status:
        receipt = {"ok":False,"sourceDirty":True,"sourceStatusAtStart":start_status,"error":"Visual release acceptance requires a clean committed source tree."}
        (args.output / "visual-release.json").write_text(json.dumps(receipt,indent=2),encoding="utf-8")
        raise AssertionError(receipt["error"])
    fixture = screenshot_catalog()
    compiled = {"before": compile_dashboard(BASELINE), "after": compile_dashboard()}
    proof = {"ok": False, "sourceDirty": False, "sourceStatusAtStart": start_status, "runtime": "Actual Chromium-rendered release sources with deterministic API fixtures", "scope": "Shared HTML dashboard, Watchtower and native shell presentation; Windows WebView2, actual MO2 and real accounts are separately tested", "baselineCommit": BASELINE, "candidateCommit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(), "platform": platform.platform(), "catalogSource": "227 real mod names/source links from repository catalog; statuses are deterministic visual fixtures, not live provider verdicts", "fixtureSha256": hashlib.sha256(json.dumps(fixture, sort_keys=True).encode()).hexdigest(), "assets": {name: values[1] for name, values in compiled.items()}, "layout": {}}
    from playwright.sync_api import sync_playwright
    with sync_playwright() as runtime:
        launch = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
        executable = shutil.which("chromium") or shutil.which("chromium-browser")
        if executable:
            launch["executable_path"] = executable
        browser = runtime.chromium.launch(**launch)
        proof["browserVersion"] = browser.version
        try:
            for name, (html, _) in compiled.items():
                output = args.output / name; output.mkdir(exist_ok=True)
                print(f"Capturing {name} exact UI source…", flush=True)
                proof["layout"][name] = capture_dashboard(browser, html, fixture, output, name == "after")
                capture_watchtower(browser, BASELINE if name == "before" else None, output, name == "after")
                capture_shell(browser, BASELINE if name == "before" else None, output, name == "after")
                proof.setdefault("publicArtwork", {})[name] = capture_public_artwork(browser, html, output, name == "after")
                proof.setdefault("operationalViews", {})[name] = capture_operational_views(browser, html, output, name == "after")
                proof.setdefault("preservedGames", {})[name] = capture_preserved_games(browser, html, output, name == "after")
            if not args.skip_benchmark:
                print(f"Measuring complete {args.mods:,}-mod source variants…", flush=True)
                proof["completeLibrary"] = benchmark(browser, {name: value[0] for name, value in compiled.items()}, args.mods, args.rounds)
            proof["sourceStatusAtEnd"] = subprocess.check_output(["git","status","--porcelain","--untracked-files=all"],cwd=ROOT,text=True).splitlines()
            proof["sourceDirty"] = bool(proof["sourceStatusAtStart"] or proof["sourceStatusAtEnd"])
            end_commit = subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
            if proof["sourceDirty"] or end_commit != proof["candidateCommit"]:
                raise AssertionError("Source changed during visual acceptance; receipt cannot approve a release.")
            proof["ok"] = True
        finally:
            browser.close()
            proof["sourceStatusAtEnd"] = subprocess.check_output(["git","status","--porcelain","--untracked-files=all"],cwd=ROOT,text=True).splitlines()
            proof["sourceDirty"] = bool(proof["sourceStatusAtStart"] or proof["sourceStatusAtEnd"])
            if proof["sourceDirty"]:
                proof["ok"] = False
            (args.output / "visual-release.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "baseline": BASELINE, "candidate": proof["candidateCommit"], "screenshots": len(list(args.output.rglob("*.png"))), "medians": proof.get("completeLibrary", {}).get("medians")}, indent=2))


if __name__ == "__main__":
    main()
