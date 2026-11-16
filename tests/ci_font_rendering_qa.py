#!/usr/bin/env python3
"""Prove observed CI glyph gaps on the actual dashboard and practice game.

Uses the existing 227-record screenshot catalog and actual game controls. This
bounded font prerequisite check is separate from complete-library performance,
Windows WebView2 and real MO2 acceptance. It does not alter product fonts/CSS.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import traceback

import visual_release_qa as visual

ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = (
    "tests/ci_font_rendering_qa.py", "tests/prepare_ci_fonts.py",
    "tests/visual_release_qa.py", "tests/test_frontend_responsiveness.py",
)
REQUIRED_CHECKS = ("mo2-locate", "runic-crest", "ember-rune", "rift-rune", "zoom-in", "zoom-out")
EXPECTED_FONTS = {
    "Noto Sans CJK TC": {
        "postscript": "NotoSansCJKtc-Regular", "bytes": 16435884,
        "sha256": "dce08bd4fd91aa8aa76ed8fea4b694c2dfb8550f67871e326843212ddbeb88b4"},
    "Noto Sans Runic": {
        "postscript": "NotoSansRunic-Regular", "bytes": 9044,
        "sha256": "49e540798dedccf24301437fb5ea5c8a3f42143b1b6c108c1d48c78a41c4ce0f"},
    "Noto Sans Symbols2": {
        "postscript": "NotoSansSymbols2-Regular", "bytes": 656852,
        "sha256": "876598db5f1e3bef2bc77c4d94c5b0d5d2c1620be4878a06f18fb5434e331688"},
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_status() -> list[str]:
    return subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"],
                                   cwd=ROOT, text=True).splitlines()


def verify_font_receipt(path: Path) -> tuple[dict, dict]:
    data = path.read_bytes()
    receipt = json.loads(data)
    assert receipt.get("ok") is True, receipt
    assert receipt.get("scriptSha256") == sha(ROOT / "tests/prepare_ci_fonts.py")
    assert receipt.get("upstreamRepository") == "notofonts/noto-cjk"
    assert receipt.get("upstreamCommit") == "f8d157532fbfaeda587e826d4cd5b21a49186f7c"
    records = [receipt, *receipt.get("supplementalFonts", [])]
    verified = {}
    for family, expected in EXPECTED_FONTS.items():
        matching = [record for record in records if record.get("fontFamily") == family]
        assert len(matching) == 1, (family, matching)
        record = matching[0]
        if family != "Noto Sans CJK TC":
            assert record.get("upstreamRepository") == "notofonts/noto-fonts"
            assert record.get("upstreamCommit") == "925ebb8364f5ec2cb0614305900782f44d32a3ab"
        assert family in record["registeredFamily"].split(",")
        assert record["registeredPostScriptName"] == expected["postscript"]
        font = Path(record["registeredFile"])
        assert font.stat().st_size == expected["bytes"] and sha(font) == expected["sha256"]
        verified[family] = {"bytes": expected["bytes"], "sha256": expected["sha256"],
                            "postScriptName": expected["postscript"]}
    assert receipt.get("supplementalGlyphCoverage", {}).get("fullwidthZoom", {}).get("text") == "＋－"
    return receipt, {"fontReceiptSha256": hashlib.sha256(data).hexdigest(),
                     "fontHelperSha256": receipt["scriptSha256"], "verifiedFontFiles": verified}


def prove_visible_font(page, name: str, selector: str, text: str, family: str,
                       *, interactive: bool = False) -> dict:
    target = page.locator(selector).first
    assert target.count() == 1, (name, selector)
    assert target.inner_text() == text, (name, target.inner_text())
    target.scroll_into_view_if_needed()
    page.evaluate("async()=>{await document.fonts.ready;await new Promise(requestAnimationFrame)}")
    state = target.evaluate("""element=>{
      const box=element.getBoundingClientRect(),style=getComputedStyle(element);
      const hit=document.elementFromPoint(box.left+box.width/2,box.top+box.height/2);
      return {text:element.textContent.trim(),visible:box.width>0&&box.height>0&&style.display!=='none'&&style.visibility==='visible',
        inViewport:box.left>=0&&box.top>=0&&box.right<=innerWidth&&box.bottom<=innerHeight,
        receivesPointer:hit===element||element.contains(hit),
        rect:{x:box.x,y:box.y,width:box.width,height:box.height},computedFontFamily:style.fontFamily};
    }""")
    assert state["visible"] and state["inViewport"], (name, state)
    if interactive:
        assert state["receivesPointer"], (name, state)
    session = page.context.new_cdp_session(page)
    try:
        session.send("DOM.enable")
        session.send("CSS.enable")
        root = session.send("DOM.getDocument")["root"]["nodeId"]
        node = session.send("DOM.querySelector", {"nodeId": root, "selector": selector})["nodeId"]
        assert node > 0, (name, selector)
        fonts = session.send("CSS.getPlatformFontsForNode", {"nodeId": node})["fonts"]
    finally:
        session.detach()
    expected = EXPECTED_FONTS[family]
    count = sum(font["glyphCount"] for font in fonts
                if font.get("familyName") == family and font.get("postScriptName") == expected["postscript"]
                and font.get("isCustomFont") is False)
    assert count == len(text) and sum(font["glyphCount"] for font in fonts) == len(text), (name, fonts)
    return {"name": name, "ok": True, "selector": selector, "nodeIndex": 0,
            "expectedText": text, "expectedFamily": family, "expectedPostScriptName": expected["postscript"],
            "matchedGlyphCount": count, "fonts": fonts, **state}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(os.environ.get(
        "MODHUNTER_QA_OUTPUT", str(ROOT / "native/target/ci-font-proof"))))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"schema": 1, "kind": "ci-font-rendering", "ok": False,
              "scope": "Actual Chromium dashboard/standalone practice game; CI font prerequisite only; no native MO2 claim",
              "candidateCommit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "sourceStatusAtStart": source_status(), "sourceFiles": {path: sha(ROOT / path) for path in SOURCE_FILES},
              "requiredChecks": list(REQUIRED_CHECKS), "checks": [], "screenshots": [], "errors": []}

    def checkpoint() -> None:
        (args.output / "ci-font-rendering.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    contexts = []
    browser = None
    runtime = None
    checkpoint()
    try:
        assert not report["sourceStatusAtStart"], "Font acceptance requires clean committed source"
        _, font_proof = verify_font_receipt(args.output / "ci-fonts.json")
        report.update(font_proof)
        html, report["assets"] = visual.compile_dashboard()
        fixture = visual.screenshot_catalog()
        report["catalogRows"] = len(fixture["items"])
        report["catalogFixtureSha256"] = hashlib.sha256(json.dumps(fixture, sort_keys=True).encode()).hexdigest()
        report["catalogScope"] = "Complete existing 227-record screenshot catalog; deterministic API/status fixture"
        from playwright.sync_api import sync_playwright
        runtime = sync_playwright().start()
        launch = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"], "timeout": 30000}
        executable = shutil.which("chromium") or shutil.which("chromium-browser")
        if executable:
            launch["executable_path"] = executable
        browser = runtime.chromium.launch(**launch)
        report["browserVersion"] = browser.version
        report["requestedExecutable"] = executable or runtime.chromium.executable_path

        context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
        contexts.append(context)
        context.on("page", lambda p: p.on("pageerror", lambda error: report["errors"].append(str(error))))
        origin = "http://glyph-library.modhunter.invalid/"
        connected_html = visual.fixture_page(html, fixture)
        context.route("**/*", lambda route: route.fulfill(content_type="text/html", body=connected_html)
                      if route.request.url.startswith(origin) else route.abort())
        page = context.new_page()
        page.set_default_timeout(15000)
        page.goto(origin, wait_until="load")
        page.wait_for_function("count=>document.querySelectorAll('#modList > .mod-row').length===count", arg=len(fixture["items"]))
        page.wait_for_function("document.getElementById('livePill').classList.contains('connected')")
        report["checks"].append(prove_visible_font(page, "mo2-locate", "#modList .mod-inline-locate", "⌖",
                                                   "Noto Sans Symbols2", interactive=True))
        page.screenshot(path=str(args.output / "ci-font-library.png"))
        report["screenshots"].append("ci-font-library.png")
        report["phase"] = "dashboard-glyph-proven"
        checkpoint()
        context.close()
        contexts.remove(context)

        game_context = browser.new_context(viewport={"width": 900, "height": 1000}, reduced_motion="reduce")
        contexts.append(game_context)
        game_context.on("page", lambda p: p.on("pageerror", lambda error: report["errors"].append(str(error))))
        game_origin = "http://glyph-games.modhunter.invalid/"
        game_context.route("**/*", lambda route: route.fulfill(content_type="text/html", body=html)
                           if route.request.url.startswith(game_origin) else route.abort())
        dashboard = game_context.new_page()
        dashboard.set_default_timeout(15000)
        dashboard.goto(game_origin, wait_until="load")
        dashboard.wait_for_function("!!window.__ARSENAL_QA__&&!!window.AetherFX")
        dashboard.locator('.workspace-tabs [data-workspace="extras"]').click()
        with game_context.expect_page() as opened:
            dashboard.locator("#realmLaunchTop").click()
        realm = opened.value
        realm.set_default_timeout(15000)
        realm.wait_for_load_state("load")
        realm.wait_for_function("window.__REALM_QA__&&!__REALM_QA__.getState().loading")
        initial = realm.evaluate("__REALM_QA__.getState()")
        assert initial["profileId"] == "practice" and not initial["connected"]
        realm.locator('button[data-panel="rift"]').click()
        realm.locator('button[data-action="start-expedition"]').click()
        realm.wait_for_function("__REALM_QA__.getState().mode==='expedition'")
        started = realm.evaluate("__REALM_QA__.getState().world.worldTime")
        realm.wait_for_function("started=>__REALM_QA__.getState().world.worldTime>=started+0.75", arg=started)
        realm.wait_for_function("""()=>{const canvas=document.querySelector('#realmCanvas');return canvas.width>0&&canvas.height>0&&
          canvas.getContext('2d').getImageData(Math.floor(canvas.width/2),Math.floor(canvas.height/2),1,1).data[3]>0}""")
        probes = (
            ("runic-crest", ".realm-brand-rune", "ᛟ", "Noto Sans Runic", False),
            ("ember-rune", '.realm-actionbar [data-action="spell"] > b', "ᚠ", "Noto Sans Runic", False),
            ("rift-rune", '.realm-run-card:has([data-action="start-rift"]) .realm-run-icon', "ᛟ", "Noto Sans Runic", False),
            ("zoom-in", 'button[data-action="zoom-in"]', "＋", "Noto Sans CJK TC", True),
            ("zoom-out", 'button[data-action="zoom-out"]', "－", "Noto Sans CJK TC", True),
        )
        for name, selector, text, family, interactive in probes:
            report["checks"].append(prove_visible_font(realm, name, selector, text, family, interactive=interactive))
        visual.scroll_dashboard_to_top(realm)
        visual.assert_no_overflow(realm, "ci-font-expedition")
        realm.screenshot(path=str(args.output / "ci-font-expedition.png"))
        report["screenshots"].append("ci-font-expedition.png")
        original_zoom = realm.evaluate("Number(localStorage.getItem('shiverfell-zoom-v1')||48)")
        realm.locator('button[data-action="zoom-in"]').click()
        larger_zoom = realm.evaluate("Number(localStorage.getItem('shiverfell-zoom-v1'))")
        assert larger_zoom > original_zoom
        realm.locator('button[data-action="zoom-out"]').click()
        restored_zoom = realm.evaluate("Number(localStorage.getItem('shiverfell-zoom-v1'))")
        assert restored_zoom == original_zoom
        report["zoomInteraction"] = {"original": original_zoom, "increased": larger_zoom, "restored": restored_zoom}
        report["game"] = {"profileId": initial["profileId"], "connected": initial["connected"], "mode": "expedition",
                           "actualCanvasDrawn": True, "enteredThroughUi": True}
        assert [check["name"] for check in report["checks"]] == list(REQUIRED_CHECKS)
        assert not report["errors"], report["errors"]
        report["sourceStatusAtEnd"] = source_status()
        assert not report["sourceStatusAtEnd"]
        assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip() == report["candidateCommit"]
        report.update(ok=True, phase="complete", sourceDirty=False)
        checkpoint()
        game_context.close()
        contexts.remove(game_context)
        browser.close()
        browser = None
    except Exception:
        report["errors"].append(traceback.format_exc())
        report["sourceStatusAtEnd"] = source_status()
        report["sourceDirty"] = bool(report["sourceStatusAtStart"] or report["sourceStatusAtEnd"])
        report["ok"] = False
        for index, context in enumerate(contexts):
            for page_index, page in enumerate(context.pages):
                try:
                    page.screenshot(path=str(args.output / f"ci-font-failure-{index}-{page_index}.png"), timeout=5000)
                except Exception:
                    pass
        checkpoint()
        raise
    finally:
        for context in contexts:
            try:
                context.close()
            except Exception:
                pass
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        if runtime:
            runtime.stop()
    print(json.dumps({"ok": report["ok"], "checks": len(report["checks"]), "screenshots": report["screenshots"]}))


if __name__ == "__main__":
    main()
