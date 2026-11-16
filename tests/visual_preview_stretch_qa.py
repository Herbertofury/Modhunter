"""Actual-browser geometry/pixel preflight for the isolated preview-stretch CSS.

This declared 16-case fixture exercises every record at three viewport widths
and both densities. It is a sizing diagnostic, not a performance benchmark or
release acceptance. The separate surface ABBA still measures all 11,207 mods.
No runtime CSS override, image generation, record sampling or pixel tolerance.
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
CONTROL_REF = "18883b32e0683dad8aed1dc87c6fe9ec577e12f2"
ORIGIN = "http://preview-stretch.modhunter.invalid/"
CSS_PATH = "mo2_update_arsenal/web/atelier.css"
RULE = ('\n/* Isolated sizing diagnostic: preserve preview margins and full grid area. */\n'
        'body[data-layout="compact"][data-library-view="gallery"] #modList > .mod-row > .mod-art'
        '{width:auto;justify-self:stretch}\n')
WIDTHS = (1440, 900, 390)
DENSITIES = ("compact", "comfy")
SOURCES = (
    "tests/visual_preview_stretch_qa.py", "tests/visual_surface_comparison.py",
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
            kind = "long-name-and-note-missing-art"
            item["name"] += " — Diagnostic long filename " + "Compatibility patch and localized installation notes " * 4
            item["screenshotNote"] = "Synthetic long-name sizing case; installed identity and source remain explicit. 外觀、隱私與維護"
        fixture["items"][index] = item
        cases.append({"id": item["id"], "kind": kind, "sourceUrl": item.get("source", {}).get("url")})
    assert len(fixture["items"]) == len(cases) == 16
    assert [item["id"] for item in fixture["items"]] == [f"item-{index}" for index in range(16)]
    return fixture, assets, cases


def capture(browser, html: str, fixture: dict, assets: dict, output: Path,
            variant: str, width: int, density: str) -> tuple[dict, dict, tuple]:
    label = f"{width}-{density}-{variant}"
    context = browser.new_context(viewport={"width": width, "height": 844 if width == 390 else 1000},
                                  reduced_motion="reduce")
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
        acceptance.set_dashboard_theme(page, "snow")
        page.locator("#libraryGallery").click()
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
        acceptance.assert_no_overflow(page, label)
        geometry = profile.complete_geometry(page, len(fixture["items"]))
        assert [row[0] for row in geometry["rowGeometry"]] == [item["id"] for item in fixture["items"]]
        session = context.new_cdp_session(page)
        data = {"geometry": geometry, "deepRows": profile.deep_row_geometry(page, session),
                "previews": page.evaluate(PREVIEW_JS),
                "markup": profile.record_markup_fingerprint(page, len(fixture["items"]))}
        assert all(preview["image"] is None or (preview["image"]["complete"]
                   and preview["image"]["naturalWidth"] > 0) for preview in data["previews"])
        payload = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        geometry_path = output / f"{label}.geometry.json.gz"
        geometry_path.write_bytes(gzip.compress(payload, mtime=0))
        image_path = output / f"{label}.png"
        png = page.screenshot(path=str(image_path), full_page=True, animations="allow", caret="hide", timeout=30000)
        decoded = decoded_png(png)
        assert decoded[0][0] == width and decoded[0][1] > 0
        after = profile.complete_geometry(page, len(fixture["items"]))
        assert all(geometry[key] == after[key] for key in ("listGeometry", "rowGeometry", "chapterGeometry", "emptyGeometry")), "Screenshot changed layout"
        assert not errors, errors
        receipt = {"variant": variant, "width": width, "density": density, "theme": "snow",
                   "rows": geometry["rows"], "allLaidOut": geometry["allLaidOut"],
                   "noContentVisibility": geometry["noContentVisibility"], "errors": errors,
                   "geometryFile": geometry_path.name, "geometrySha256": sha(geometry_path.read_bytes()),
                   "screenshot": image_path.name, "screenshotSha256": sha(png),
                   "pixelSize": list(decoded[0]), "decodedRgbaSha256": sha(decoded[1])}
        return receipt, data, decoded
    finally:
        if session:
            session.detach()
        context.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--executable-path", type=Path, required=True)
    parser.add_argument("--font-receipt", type=Path, required=True)
    parser.add_argument("--control-ref", default=CONTROL_REF)
    args = parser.parse_args()
    assert args.control_ref == CONTROL_REF, "This probe compares the exact accepted C7 source checkpoint"
    assert args.executable_path.is_file(), "The requested actual Chromium executable is missing"
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"schema": 1, "kind": "preview-stretch-preflight", "ok": False,
              "scope": "Declared complete16-case geometry/pixel diagnostic only; full11207 performance and native acceptance are separate",
              "controlCommit": CONTROL_REF, "candidateCommit": git("rev-parse", "HEAD"),
              "sourceStatusAtStart": source_status(),
              "sources": {name: sha((ROOT / name).read_bytes()) for name in SOURCES},
              "requestedExecutable": str(args.executable_path.resolve()),
              "motionPreference": "reduce", "theme": "snow", "states": [], "errors": []}

    def checkpoint():
        (args.output / "preview-stretch-preflight.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

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
        assert acceptance.read(CSS_PATH) == original_css + RULE, "Candidate CSS contains changes beyond the single scoped sizing rule"
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
                for width in WIDTHS:
                    for density in DENSITIES:
                        report["phase"] = f"{width}-{density}:control"
                        checkpoint()
                        control, reference, pixels = capture(browser, before, fixture, assets, args.output, "control", width, density)
                        report["states"].append(control)
                        report["phase"] = f"{width}-{density}:candidate"
                        checkpoint()
                        candidate, actual, actual_pixels = capture(browser, after, fixture, assets, args.output, "candidate", width, density)
                        comparison = {"exactGeometry": reference["geometry"] == actual["geometry"],
                                      "exactDescendantTextPseudoGeometry": reference["deepRows"] == actual["deepRows"],
                                      "exactPreviewAndActionGeometry": reference["previews"] == actual["previews"],
                                      "identicalOrderedMarkup": reference["markup"] == actual["markup"],
                                      "pixels": compare_pixels(pixels, actual_pixels)}
                        candidate["comparison"] = comparison
                        candidate["ok"] = all(comparison[key] for key in comparison if key != "pixels") and comparison["pixels"]["identical"]
                        report["states"].append(candidate)
                        checkpoint()
                        assert candidate["ok"], ("Preview sizing changed geometry, semantics or pixels", width, density, comparison)
            finally:
                browser.close()
        report["sourceStatusAtEnd"] = source_status()
        assert not report["sourceStatusAtEnd"] and git("rev-parse", "HEAD") == report["candidateCommit"]
        assert len(report["states"]) == len(WIDTHS) * len(DENSITIES) * 2
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
