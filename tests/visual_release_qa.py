"""Render the exact release UI and its v0.11.0 baseline in Chromium.

Screenshots are actual application HTML/CSS/JS with deterministic, clearly
identified API fixtures. They are not native Windows, provider account, or user
profile proof. The Windows workflow supplies separate WebView2/MO2 receipts.
The benchmark keeps all 11,207 mod records and measures complete source variants.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
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
    return page.evaluate("""() => ({width:innerWidth, scrollWidth:document.documentElement.scrollWidth,
      height:innerHeight, rows:document.querySelectorAll('#modList .mod-row').length,
      firstRow:document.querySelector('#modList .mod-row')?.getBoundingClientRect().toJSON(),
      libraryView:document.body.dataset.libraryView, density:document.body.dataset.density})""")


def assert_no_overflow(page, description):
    state = dimensions(page)
    if state["scrollWidth"] > state["width"] + 1:
        raise AssertionError(f"{description}: document overflows: {state}")


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
            settle(page)
            shot = f"library-{width}-{'dark' if theme == 'night' else 'light'}.png"
            page.screenshot(path=str(output / shot))
            state = dimensions(page)
            state.update(screenshot=shot, theme=theme)
            states.append(state)
            if candidate:
                assert_no_overflow(page, shot)
    page.set_viewport_size({"width": 1440, "height": 1000})
    set_dashboard_theme(page, "night")
    page.locator(".mod-row").first.locator(".inspect-btn").click()
    settle(page)
    page.screenshot(path=str(output / "inspector-1440-dark.png"))
    page.keyboard.press("Escape")
    page.locator('.workspace-tabs [data-workspace="updates"]').click()
    page.wait_for_selector(".workshop-item")
    settle(page)
    page.screenshot(path=str(output / "updates-1440-dark.png"))
    for width in (900, 390):
        page.set_viewport_size({"width": width, "height": 1000 if width > 400 else 844})
        settle(page)
        page.screenshot(path=str(output / f"updates-{width}-dark.png"))
        if candidate:
            assert_no_overflow(page, f"updates/{width}")
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.locator('.workspace-tabs [data-workspace="mods"]').click()
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
        set_dashboard_theme(page, "snow")
        page.screenshot(path=str(output / "library-gallery-1440-light.png"))
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
            settle(page)
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
        set_dashboard_theme(page, theme);settle(page)
        page.screenshot(path=str(output / f"public-projects-list-1440-{'dark' if theme=='night' else 'light'}.png"))
    if candidate:
        page.locator("#libraryGallery").click()
        for image in page.locator("#modList .mod-preview-image").all():
            image.scroll_into_view_if_needed()
            page.wait_for_function("image=>image.complete&&image.naturalWidth>0", arg=image.element_handle(), timeout=10000)
        page.evaluate("scrollTo(0,0)")
        assert page.locator("#modList .mod-preview-image").count() == len(sources)
        for width in (1440, 900, 390):
            page.set_viewport_size({"width": width, "height": 1000 if width > 400 else 844})
            for theme in ("night", "snow"):
                set_dashboard_theme(page, theme);settle(page);assert_no_overflow(page, f"official-artwork/{width}/{theme}")
                page.screenshot(path=str(output / f"public-projects-gallery-{width}-{'dark' if theme=='night' else 'light'}.png"))
        page.set_viewport_size({"width":1440,"height":1000});set_dashboard_theme(page,"night")
        page.locator("#row-item-0 .mod-art").click();settle(page)
        page.screenshot(path=str(output / "public-projects-inspector-1440-dark.png"))
    if errors:
        raise AssertionError(f"Public artwork UI errors: {errors}")
    context.close()
    return {"projects":len(sources),"metadata":"tests/fixtures/visual_public_catalog.json","fixtureSha256":hashlib.sha256(json.dumps(fixture,sort_keys=True).encode()).hexdigest(),"scope":manifest["scope"],"sources":[{"projectUrl":source["projectUrl"],"apiUrl":source["apiUrl"],"thumbnailUrl":source["thumbnailUrl"],"sha256":source["sha256"],"retrievedAt":source["retrievedAt"]} for source in sources]}


def capture_watchtower(browser, ref, output, candidate):
    context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
    state = watchtower.snapshot(8)
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
    result = {"mods": count, "rounds": rounds, "fixtureSha256": hashlib.sha256(json.dumps(fixture, sort_keys=True).encode()).hexdigest(), "method": "Complete baseline and candidate source variants; one warmup then alternating variant order, new contexts, complete import + frame/layout, exact last-item search, all DOM results preserved. Browser fixture only; excludes native import and provider I/O.", "variants": {name: [] for name in variants}}
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
              return {renderMs,rows:rows.length,complete,allLaidOut,noContentVisibility};
            }""", {"fixture": fixture, "count": count})
            assert sample["complete"] and sample["allLaidOut"] and sample["noContentVisibility"], sample
            start = time.perf_counter()
            page.locator("#search").fill(f"Unique Fixture {count-1}")
            page.wait_for_function("document.querySelectorAll('#modList .mod-row').length===1")
            assert page.locator("#modList .mod-row").get_attribute("data-id") == f"item-{count-1}"
            sample["lastItemSearchMs"] = (time.perf_counter() - start) * 1000
            page.locator("#search").fill("")
            page.wait_for_function("n=>document.querySelectorAll('#modList .mod-row').length===n", arg=count)
            if name == "after":
                page.locator("#libraryGallery").click();settle(page)
                assert page.locator("#modList .mod-row").count() == count
                page.locator("#densityToggle").click();settle(page)
                assert page.locator("#modList .mod-row").count() == count
                page.locator("#libraryList").click();settle(page)
                assert page.locator("#modList .mod-row").count() == count
            if run:
                result["variants"][name].append(sample)
            context.close()
    result["medians"] = {name: {metric: statistics.median(row[metric] for row in samples) for metric in ("renderMs", "lastItemSearchMs")} for name, samples in result["variants"].items()}
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
    fixture = screenshot_catalog()
    compiled = {"before": compile_dashboard(BASELINE), "after": compile_dashboard()}
    proof = {"ok": False, "runtime": "Actual Chromium-rendered release sources with deterministic API fixtures", "scope": "Shared HTML dashboard, Watchtower and native shell presentation; Windows WebView2, actual MO2 and real accounts are separately tested", "baselineCommit": BASELINE, "candidateCommit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(), "platform": platform.platform(), "catalogSource": "227 real mod names/source links from repository catalog; statuses are deterministic visual fixtures, not live provider verdicts", "fixtureSha256": hashlib.sha256(json.dumps(fixture, sort_keys=True).encode()).hexdigest(), "assets": {name: values[1] for name, values in compiled.items()}, "layout": {}}
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
            if not args.skip_benchmark:
                print(f"Measuring complete {args.mods:,}-mod source variants…", flush=True)
                proof["completeLibrary"] = benchmark(browser, {name: value[0] for name, value in compiled.items()}, args.mods, args.rounds)
            proof["ok"] = True
        finally:
            browser.close()
            (args.output / "visual-release.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "baseline": BASELINE, "candidate": proof["candidateCommit"], "screenshots": len(list(args.output.rglob("*.png"))), "medians": proof.get("completeLibrary", {}).get("medians")}, indent=2))


if __name__ == "__main__":
    main()
