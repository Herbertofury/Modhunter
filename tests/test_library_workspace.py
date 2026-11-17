"""Full-window Library, bottom search, real evidence columns and native colors.

Runs the actual shared frontend in Chromium with deterministic MO2 API replies.
No records are virtualized or capped; Windows/MO2 receipts remain separate.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest
from browser_receipt import EvidenceResult, chromium_identity, screenshot_identity, source_identity

import test_frontend_responsiveness as harness

ROOT=harness.ROOT
OUTPUT=Path(os.environ.get("MODHUNTER_QA_OUTPUT",ROOT/"native/target/library-workspace"))
SOURCES=("mo2_update_arsenal/web/app.js","mo2_update_arsenal/web/app.template.html","mo2_update_arsenal/web/atelier.css",
         "mo2_update_arsenal/web/mod_insights.js","mo2_update_arsenal/web/source_insights.js","native/ui/locales/library.json",
         "native/ui/locale-catalogs.js","native/ui/preferences.js","tests/test_library_workspace.py","tests/test_frontend_responsiveness.py")
SOURCES += tuple("mo2_update_arsenal/web/"+name for name in (
    "style.css","aether_fx.css","realm.css","siege.css","mod_insights.css","source_insights.css",
    "catalog.json","aether_fx.js","realm.js","siege.js","realm_layout.html","siege_layout.html"))
SOURCES += ("tests/browser_receipt.py","native/crates/modhunter-desktop/tauri.conf.json")


class LibraryWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.captured_screenshots={}
        cls.browser_identity=None
        harness.FrontendResponsivenessTests.setUpClass()
        cls.browser=harness.FrontendResponsivenessTests.browser
        cls.html=harness.build_page()
        OUTPUT.mkdir(parents=True,exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        harness.FrontendResponsivenessTests.tearDownClass()

    def setUp(self):
        harness.FrontendResponsivenessTests.setUp(self)
        type(self).browser_identity=chromium_identity(self.browser,self.page)
    connected=harness.FrontendResponsivenessTests.connected

    def tearDown(self):
        result=self._outcome.result
        if self.errors or any(test is self for test,_ in result.errors+result.failures):
            try:self.capture("library-failed-"+self._testMethodName.removeprefix("test_")+".png")
            except Exception:pass
        self.context.close();self.assertEqual(self.errors,[])

    def open(self,count=60):
        catalog=harness.catalog(count=count)
        catalog["groups"][0].update(separatorColor="#3873d4cc",separatorColorSource="mo2-meta")
        for item in catalog["items"]:
            item["modHealth"]={"provider":"nexus","bugs":{"state":"unknown","displayedCount":None}}
        catalog["items"][0]["modHealth"]["bugs"]={"state":"available","displayedCount":0,"checkedAt":1800000000}
        catalog["items"][1]["modHealth"]["bugs"]={"state":"available","displayedCount":12,"checkedAt":1800000000}
        catalog["items"][2]["modHealth"]["bugs"]={"state":"available","displayedCount":3,"checkedAt":1800000000,"freshness":{"state":"stale"}}
        catalog["items"][3]["modHealth"]["bugs"]={"state":"disabled","displayedCount":None}
        catalog["items"][0]["screenshotNote"]="Preserve my optional file and installation choices."
        catalog["items"][0]["localAnalysis"]={"inventoryComplete":True,"plugins":[{"name":"Observed.esp","winner":True,"active":True,"winnerKnown":True,"header":{"lightEffective":True},"eligibility":{"status":"already-light"},"errors":[],"masterIssues":[]}]}
        catalog["items"][0]["update"]={"verdict":"candidate","provider":"nexus","latestVersion":"1.1","checkedAt":1800000000,"expiresAt":9999999999,"fileEvidence":{"status":"matched","basis":"installed-file-id","installedFileIds":[144],"installedFileName":"Fishing patch 1.0.zip","targetFileId":155,"targetFileName":"Fishing patch 1.1.zip","targetVersion":"1.1","updated":True,"reason":"Matched the installed optional-file lineage."}}
        boot=harness.BOOT.replace("__CATALOG__",json.dumps(catalog)).replace("__FAIL__","0").replace("prefs:{}","prefs:{autoCheckOnLaunch:false}")
        boot=boot.replace("else if(path==='scan')", "else if(path.startsWith('source-details?'))result={ok:true,profileId:c.profileId,update:c.items.find(item=>item.id===new URL('http://fixture/'+path).searchParams.get('itemId'))?.update||{}};else if(path==='scan')")
        html=self.html.replace("<!--__MO2_BOOT__-->",boot)
        html=re.sub(r"<style\b",'<style nonce="library-workspace-fixture"',html)
        self.context.route("http://library.workspace.test/",lambda route:route.fulfill(content_type="text/html",body=html,headers={"Content-Security-Policy":"style-src 'nonce-library-workspace-fixture'"}))
        self.page.goto("http://library.workspace.test/",wait_until="load")
        self.connected();self.page.wait_for_function("n=>document.querySelectorAll('#modList>.mod-row').length===n",arg=count)
        return catalog

    def capture(self,name):
        data=self.page.screenshot(path=str(OUTPUT/name),full_page=False)
        type(self).captured_screenshots[name]=screenshot_identity(data)

    def geometry(self):
        return self.page.evaluate("""() => {
          const box=id=>document.getElementById(id).getBoundingClientRect().toJSON();
          const viewport=document.getElementById('libraryViewport');
          return {width:innerWidth,height:innerHeight,documentWidth:document.documentElement.scrollWidth,
            documentHeight:document.documentElement.scrollHeight,viewport:box('libraryViewport'),controls:box('controls'),search:box('search'),
            footer:document.querySelector('.list-footer').getBoundingClientRect().toJSON(),bodypad:document.querySelector('.bodypad').getBoundingClientRect().toJSON(),
            scrollHeight:viewport.scrollHeight,clientHeight:viewport.clientHeight,scrollTop:viewport.scrollTop,
            rows:[...document.querySelectorAll('#modList>.mod-row')].map(row=>({id:row.dataset.id,box:row.getBoundingClientRect().toJSON(),visibility:getComputedStyle(row).contentVisibility}))};
        }""")

    def test_full_viewport_and_pinned_search_do_not_cover_last_row_or_status(self):
        self.page.set_viewport_size({"width":1920,"height":1080});self.open(count=307)
        for width,height in [(1920,1080),(2560,1440),(390,844)]:
            self.page.set_viewport_size({"width":width,"height":height})
            before=self.geometry()
            self.assertLessEqual(before["documentWidth"],width+1)
            self.assertLessEqual(before["documentHeight"],height+1)
            self.assertAlmostEqual(before["controls"]["bottom"],height,delta=1)
            self.assertGreaterEqual(before["search"]["top"],before["controls"]["top"])
            self.assertLessEqual(before["footer"]["bottom"],before["controls"]["top"]+1)
            self.assertGreater(before["viewport"]["height"],height*.48)
            self.assertEqual(len(before["rows"]),307)
            self.assertTrue(all(row["box"]["width"]>0 and row["box"]["height"]>0 and row["visibility"]=="visible" for row in before["rows"]))
            if width>=1920:self.assertGreater(before["bodypad"]["width"],width-100)
            self.page.evaluate("document.getElementById('libraryViewport').scrollTop=1e9")
            after=self.geometry()
            self.assertAlmostEqual(after["search"]["top"],before["search"]["top"],delta=1)
            self.assertLessEqual(after["rows"][-1]["box"]["bottom"],after["viewport"]["bottom"]+1)
            self.capture(f"library-bottom-{width}.png")
            self.page.locator("#backToTop").click();self.page.wait_for_function("document.getElementById('libraryViewport').scrollTop===0")
        self.page.set_viewport_size({"width":1920,"height":1080});self.capture("library-full-window-oled.png")

    def test_bugs_column_sorts_filters_and_keeps_unknown_distinct_from_zero(self):
        self.page.set_viewport_size({"width":1920,"height":1080});self.open(count=8)
        self.assertTrue(self.page.locator("#row-item-0 .mod-bugs").is_visible())
        self.assertEqual(self.page.locator("#row-item-0 .mod-bugs").inner_text(),"0 displayed")
        self.assertEqual(self.page.locator("#row-item-4 .mod-bugs").inner_text(),"Bugs unverified")
        self.assertIn("stale",self.page.locator("#row-item-2 .mod-bugs").inner_text())
        self.page.locator('[data-library-sort="bugs"]').click()
        self.assertEqual(self.page.locator("#modList>.mod-row").evaluate_all("rows=>rows.map(row=>row.dataset.id)"),["item-1","item-2","item-0","item-3","item-4","item-5","item-6","item-7"])
        for value,ids in [("bugs-zero",["item-0"]),("bugs-reported",["item-1","item-2"]),("bugs-disabled",["item-3"])]:
            self.page.locator("#libraryHealthFilter").select_option(value)
            self.page.wait_for_function("ids=>JSON.stringify([...document.querySelectorAll('#modList>.mod-row')].map(row=>row.dataset.id))===JSON.stringify(ids)",arg=ids)
        self.page.locator("#clearFilters").click();self.assertEqual(self.page.locator("#modList>.mod-row").count(),8)

    def test_health_notes_and_exact_optional_file_details_are_visible(self):
        self.page.set_viewport_size({"width":1920,"height":1080});self.open(count=8)
        self.assertTrue(self.page.locator("#row-item-0 .mod-note").is_visible())
        self.assertIn("Preserve my optional file",self.page.locator("#row-item-0 .mod-note").inner_text())
        self.assertTrue(self.page.locator("#row-item-0 .mod-health").is_visible())
        self.assertIn("1 light",self.page.locator("#row-item-0 .mod-health").inner_text())
        self.assertIn("Installed file matched",self.page.locator("#row-item-0 .mod-health").inner_text())
        self.page.locator("#row-item-0 .mod-health [data-source-act]").last.click()
        self.page.wait_for_selector("#detailDrawer.open")
        self.assertIn("Fishing patch 1.0.zip",self.page.locator(".source-file-lineage").inner_text())
        self.assertIn("Fishing patch 1.1.zip",self.page.locator(".source-file-lineage").inner_text())
        for key in ["POST download","POST install","POST quick-update"]:
            self.assertEqual(self.page.evaluate("key=>__RESP__.counts[key]||0",key),0)

    def test_columns_persist_without_deleting_data_or_hiding_gallery_evidence(self):
        self.open(count=8);self.page.locator("#libraryColumns").click()
        health=self.page.locator('[data-library-column="health"]');health.uncheck()
        self.assertTrue(self.page.evaluate("document.activeElement.dataset.libraryColumn==='health'"))
        self.page.locator("#libraryColumnsDone").click()
        self.assertFalse(self.page.locator("#row-item-0 .mod-health").is_visible())
        self.assertIn("1 light",self.page.locator("#row-item-0 .mod-health").text_content())
        self.page.locator("#libraryGallery").click();self.assertTrue(self.page.locator("#row-item-0 .mod-health").is_visible())
        self.page.locator("#libraryList").click();self.assertFalse(self.page.locator("#row-item-0 .mod-health").is_visible())
        self.page.reload(wait_until="load");self.connected()
        self.assertFalse(self.page.locator("#row-item-0 .mod-health").is_visible());self.assertEqual(self.page.locator("#modList>.mod-row").count(),8)
        self.page.locator("#libraryColumns").click();self.page.locator("#libraryColumnsReset").click();self.page.keyboard.press("Escape")
        self.assertTrue(self.page.locator("#row-item-0 .mod-health").is_visible());self.assertEqual(self.page.evaluate("document.activeElement.id"),"libraryColumns")

    def test_actual_rgba_separators_survive_theme_search_gallery_and_csp(self):
        catalog=self.open(count=8)
        color=lambda:self.page.locator(".chapterbar").first.evaluate("node=>node.style.getPropertyValue('--chapter-color')")
        self.assertEqual(color(),"#3873d4cc")
        self.page.locator("#railCollections").click()
        self.assertEqual(self.page.locator(".chapter-link").first.evaluate("node=>node.style.getPropertyValue('--group-clr')"),"#3873d4cc")
        self.page.keyboard.press("Escape");self.page.locator("#railTheme").click();self.assertEqual(color(),"#3873d4cc")
        self.assertEqual(self.page.locator("body").get_attribute("data-theme"),"snow")
        self.page.locator("#search").fill("Unique Fixture 7");self.page.wait_for_function("document.querySelectorAll('#modList>.mod-row').length===1");self.assertEqual(color(),"#3873d4cc")
        self.page.locator("#clearSearch").click();self.page.locator("#libraryGallery").click();self.page.locator("#galleryGrouping").select_option("chapters");self.assertEqual(color(),"#3873d4cc")
        catalog["groups"][0]["separatorColor"]="red;position:fixed";awaitable="async catalog=>{__RESP__.catalog=catalog;await __ARSENAL_QA__.setCatalog(catalog)}"
        self.page.evaluate(awaitable,catalog);self.assertNotEqual(color(),"red;position:fixed")
        self.assertFalse(self.page.evaluate("[...document.querySelectorAll('style')].some(node=>!node.nonce)"))

    def test_ctrl_f_and_escape_keep_search_and_dialog_focus_predictable(self):
        self.open(count=30);self.page.locator('[data-workspace="tools"]').first.click();self.page.keyboard.press("Control+f")
        self.assertEqual(self.page.locator("body").get_attribute("data-workspace"),"mods")
        self.assertEqual(self.page.evaluate("document.activeElement.id"),"search")
        self.page.locator("#search").fill('"Unique Fixture 29"');self.page.wait_for_function("document.querySelectorAll('#modList>.mod-row').length===1")
        self.page.keyboard.press("Escape");self.assertNotEqual(self.page.evaluate("document.activeElement.id"),"search")
        self.assertEqual(self.page.locator("#search").input_value(),'"Unique Fixture 29"')
        self.page.keyboard.press("Control+k");self.assertEqual(self.page.evaluate("document.activeElement.id"),"search")
        self.page.locator("#libraryColumns").click();self.page.keyboard.press("Escape")
        self.assertFalse(self.page.locator("#libraryColumnsDialog").is_visible());self.assertEqual(self.page.evaluate("document.activeElement.id"),"libraryColumns")

    def test_every_workspace_and_library_view_remains_reachable(self):
        self.page.set_viewport_size({"width":1920,"height":1080});self.open(count=30)
        for workspace in ["updates","downloads","history","tools","extras","mods"]:
            self.page.locator('.workspace-tabs [data-workspace="'+workspace+'"]').click()
            self.assertEqual(self.page.locator("body").get_attribute("data-workspace"),workspace)
            self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"),1921)
            self.capture("library-workspace-"+workspace+".png")
        self.page.locator("#libraryGallery").click();self.assertEqual(self.page.locator("#modList>.mod-row").count(),30)
        self.capture("library-gallery-details.png")
        self.page.locator("#railTheme").click();self.assertEqual(self.page.locator("body").get_attribute("data-theme"),"snow");self.capture("library-gallery-light.png")
        self.page.set_viewport_size({"width":390,"height":844})
        source=self.page.locator("#row-item-0 .mod-source").bounding_box()
        actions=self.page.locator("#row-item-0 .inspect-col").bounding_box()
        self.assertGreaterEqual(actions["y"],source["y"]+source["height"]-1)
        self.capture("library-gallery-narrow-light.png")
        self.page.set_viewport_size({"width":1920,"height":1080})
        self.page.locator("#libraryList").click();self.capture("library-list-light.png")
        self.assertEqual(self.page.locator("#modList>.mod-row").count(),30)

    def test_four_locales_and_first_use_setup_preserve_real_data_and_entry_points(self):
        self.open(count=8)
        for name in ("locale-catalogs.js","preferences.js"):
            self.page.add_script_tag(content=(ROOT/"native/ui"/name).read_text(encoding="utf-8"))
        for locale,columns,setup,bugs in [("es","Columnas","Configurar MO2","0 mostradas"),("it","Colonne","Configura MO2","0 visualizzati"),("zh-TW","欄位","設定 MO2","顯示 0 筆"),("en","Columns","Set up MO2","0 displayed")]:
            self.page.evaluate("locale=>ModhunterI18n.apply({locale,theme:'dark'})",locale)
            self.page.wait_for_function("text=>document.getElementById('libraryColumns').textContent===text",arg=columns)
            self.assertIn(setup,self.page.locator('#nativeWelcome a[href="modhunter-shell://instances"]').inner_text())
            self.assertEqual(self.page.locator("#row-item-0 .mod-bugs").inner_text(),bugs)
            self.assertEqual(self.page.locator("#row-item-0 .mod-title").first.text_content().replace("↗",""),"Unique Fixture 0")
            self.assertEqual(self.page.locator("#modList>.mod-row").count(),8)
        for action in ("instances","discover","import"):
            self.assertEqual(self.page.locator('#nativeWelcome a[href="modhunter-shell://'+action+'"]').count(),1)
        self.assertNotIn("Install_MO2_Plugin.bat",self.page.locator("#nativeWelcome").text_content())


if __name__=="__main__":
    OUTPUT.mkdir(parents=True,exist_ok=True)
    before=source_identity(ROOT,SOURCES)
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(LibraryWorkspaceTests);expected=suite.countTestCases()
    result=unittest.TextTestRunner(verbosity=2,resultclass=EvidenceResult).run(suite)
    after=source_identity(ROOT,SOURCES)
    unchanged=before["commit"]==after["commit"] and before["sourceSha256"]==after["sourceSha256"]
    browser_identity=getattr(LibraryWorkspaceTests,"browser_identity",None)
    proof={"schema":1,"kind":"library-workspace","ok":result.wasSuccessful() and result.testsRun==expected and not result.skipped and unchanged,
           "version":json.loads((ROOT/"native/crates/modhunter-desktop/tauri.conf.json").read_text())["version"],
           "commit":after["commit"],"commitAtStart":before["commit"],"sourceUnchanged":unchanged,
           "dirty":before["dirty"] or after["dirty"],
           "testsRun":result.testsRun,"requiredTests":expected,"failures":len(result.failures),"errors":len(result.errors),"skipped":len(result.skipped),
           "realChromium":bool(browser_identity and browser_identity["engine"]=="chromium"),"browserIdentity":browser_identity,
           "nativeMO2":False,"nativeWebView2":False,"checks":result.passed_checks,
           "scope":"Actual shared frontend in Chromium with deterministic MO2 API replies; no real account or native installation proof.",
           "sourceSha256":after["sourceSha256"],"sourceSha256AtStart":before["sourceSha256"],
           "screenshots":sorted(getattr(LibraryWorkspaceTests,"captured_screenshots",{})),
           "screenshotEvidence":getattr(LibraryWorkspaceTests,"captured_screenshots",{})}
    (OUTPUT/"library-workspace.json").write_text(json.dumps(proof,indent=2)+"\n",encoding="utf-8")
    sys.exit(0 if proof["ok"] else 1)
