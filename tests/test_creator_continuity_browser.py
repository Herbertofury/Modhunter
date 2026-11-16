"""Actual Chromium workflow; fixture transport, production dashboard assets."""
import json
import os
from pathlib import Path
import unittest

import test_frontend_responsiveness as dashboard


class CreatorContinuityBrowserTests(unittest.TestCase):
    setUpClass = classmethod(dashboard.FrontendResponsivenessTests.setUpClass.__func__)
    tearDownClass = classmethod(dashboard.FrontendResponsivenessTests.tearDownClass.__func__)
    setUp = dashboard.FrontendResponsivenessTests.setUp
    tearDown = dashboard.FrontendResponsivenessTests.tearDown
    connected = dashboard.FrontendResponsivenessTests.connected

    def open_fixture(self):
        data = dashboard.catalog()
        for item, number in zip(data["items"], (196696, 194692, 99)):
            item.update(author="SethSaidThat" if number != 99 else "Other", nexusId=number)
            item["source"]["url"] = f"https://www.nexusmods.com/skyrimspecialedition/mods/{number}"
        boot = dashboard.BOOT.replace("__CATALOG__", json.dumps(data)).replace("__FAIL__", "0")
        boot = boot.replace("let result={ok:true};", """let result={ok:true};
 if(x.progress)for(const item of c.items){const row=x.progress.rows?.[item.id];if(row?.url)item.source={...item.source,url:row.url,type:'modhold',expectedAuthor:row.creatorContinuity?.expectedAuthor};}""")
        boot = boot.replace(
            "else if(path==='progress')result={ok:true,profileId:c.profileId,progress:{rows:{},prefs:{}}};",
            """else if(path==='progress'){
 if(method==='POST'){
  const before=x.progress?.rows||{},changed=Object.entries(body.rows||{}).some(([id,row])=>(before[id]?.url||'')!==(row.url||''));
  x.progress={rows:structuredClone(body.rows||{}),prefs:structuredClone(body.prefs||{})};
  result={ok:true,profileId:c.profileId,sourceLinksChanged:changed};
 }else result={ok:true,profileId:c.profileId,progress:structuredClone(x.progress||{rows:{},prefs:{}})};
 }else if(path==='creator-status'){
 result={ok:true,profileId:c.profileId,itemId:body.itemId,handle:body.handle,observation:{handle:body.handle,profileUrl:'https://www.nexusmods.com/profile/'+body.handle,accountId:'199502561',state:'banned',verified:true,checkedAt:Date.now()/1000,reason:'Profile header explicitly marks this author banned.'}};
 }""")
        html = self.html.replace("<!--__MO2_BOOT__-->", boot)
        self.page.route("http://creator.fixture.test/", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
        self.page.goto("http://creator.fixture.test/")
        self.connected()
        self.page.wait_for_function("__RESP__.counts['GET progress']>=1")
        self.page.locator('#row-item-0 [data-act="inspect"]').click()
        self.page.locator(".creator-panel").wait_for()
        return data

    def test_review_source_check_pause_resume_and_undo_preserve_original(self):
        self.open_fixture()
        panel = self.page.locator("#creatorContinuityDetails")
        self.assertIn("SethSaidThat", panel.inner_text())
        approve = panel.locator('[data-creator-action="approve"]')
        self.assertTrue(approve.is_disabled())
        self.assertIn("196696", panel.inner_text())
        panel.locator("[data-creator-confirm]").check()
        approve.click()
        self.page.wait_for_function("__ARSENAL_QA__.verifyRow('item-0').state.creatorContinuity?.state==='tracking'")
        self.page.wait_for_function("(__RESP__.counts['POST scan']||0)>=1")
        row = self.page.evaluate("__ARSENAL_QA__.verifyRow('item-0').state")
        self.assertIn("/mods/11/", row["url"])
        self.assertIn("/mods/196696", row["creatorContinuity"]["originalUrl"])
        self.assertEqual(row["creatorContinuity"]["expectedAuthor"], "SethSaidThat")
        panel.locator('[data-creator-action="pause"]').click()
        self.page.wait_for_function("__ARSENAL_QA__.verifyRow('item-0').state.creatorContinuity?.state==='paused'")
        panel.locator('[data-creator-action="resume"]').click()
        self.page.wait_for_function("__ARSENAL_QA__.verifyRow('item-0').state.creatorContinuity?.state==='tracking'")
        panel.locator('[data-creator-action="undo"]').click()
        self.assertEqual(self.page.evaluate("__ARSENAL_QA__.verifyRow('item-0').state.url"), "")
        self.assertEqual(self.page.evaluate("__ARSENAL_QA__.verifyRow('item-0').state.creatorContinuity.state"), "undone")
        self.assertFalse(self.page.evaluate("Object.keys(__RESP__.counts).some(x=>/^POST (download|install)/.test(x))"))

    def test_primary_status_and_profile_switch_keep_author_evidence_isolated(self):
        data = self.open_fixture()
        self.page.locator('[data-creator-action="status"]').click()
        self.page.wait_for_function("__ARSENAL_QA__.verifyRow('item-0').state.creatorObservation?.state==='banned'")
        self.assertIn("Latest direct profile check", self.page.locator("#creatorContinuityDetails").inner_text())
        self.page.evaluate("__RESP__.defer['creator-status']=true")
        self.page.locator('[data-creator-action="status"]').click()
        self.page.wait_for_function("__RESP__.pending.some(p=>p.path==='creator-status')")
        other = dashboard.catalog("profile-b")
        self.page.evaluate("c=>{__RESP__.catalog=c;__RESP__.progress={rows:{},prefs:{}};__ARSENAL_QA__.setCatalog(c)}", other)
        self.page.wait_for_function("__ARSENAL_QA__.getCatalog().profileId==='profile-b'")
        self.page.evaluate("__RESP__.release('creator-status')")
        self.page.wait_for_function("!__RESP__.pending.some(p=>p.path==='creator-status')")
        self.assertIsNone(self.page.evaluate("__ARSENAL_QA__.verifyRow('item-0').state.creatorObservation||null"))

    def test_registry_import_requires_review_and_rejects_malformed_replacement(self):
        self.open_fixture()
        registry = json.loads((dashboard.ROOT / "shared/creator-continuity.v1.json").read_text())
        self.page.locator("#creatorRegistryFile").set_input_files(
            {"name":"creator.json", "mimeType":"application/json", "buffer":json.dumps(registry).encode()})
        self.page.wait_for_function("document.querySelector('#creatorRegistryStatus').textContent.includes('Imported 1')")
        self.assertTrue(self.page.locator('[data-creator-action="approve"]').is_disabled())
        before = self.page.evaluate("JSON.parse(localStorage.getItem('mo2-update-arsenal:v2:profile-a')).prefs.creatorRegistry")
        registry["migrations"][0]["to"]["url"] = "https://modholdmods.com/u/SethSaidThat"
        self.page.locator("#creatorRegistryFile").set_input_files(
            {"name":"invalid.json", "mimeType":"application/json", "buffer":json.dumps(registry).encode()})
        self.page.wait_for_function("document.querySelector('#creatorRegistryStatus').textContent.includes('disagree')")
        self.assertEqual(self.page.evaluate("JSON.parse(localStorage.getItem('mo2-update-arsenal:v2:profile-a')).prefs.creatorRegistry"), before)

    def test_layout_evidence(self):
        self.open_fixture()
        output = Path(os.environ.get("MODHUNTER_QA_OUTPUT", "/tmp/modhunter-creator-proof"))
        output.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(output / "creator-continuity.png"), full_page=False)
        panel = self.page.locator("#creatorContinuityDetails")
        self.assertTrue(panel.locator('[data-creator-action="status"]').is_visible())
        self.assertTrue(panel.locator('[data-creator-confirm]').is_visible())
        self.assertEqual(panel.locator("a").count(), 4)


if __name__ == "__main__":
    unittest.main()
