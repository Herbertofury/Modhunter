"""Actual Chromium UI with deterministic API replies, not native account proof."""
from __future__ import annotations
import json
import os
from pathlib import Path
import unittest
import test_frontend_responsiveness as harness


class CompactWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        harness.FrontendResponsivenessTests.setUpClass()
        cls.browser=harness.FrontendResponsivenessTests.browser
        cls.html=harness.build_page()
    @classmethod
    def tearDownClass(cls):harness.FrontendResponsivenessTests.tearDownClass()
    setUp=harness.FrontendResponsivenessTests.setUp
    tearDown=harness.FrontendResponsivenessTests.tearDown
    connected=harness.FrontendResponsivenessTests.connected

    def open_workspace(self, *, auto=True, running=False, fail=False, deferred=False):
        catalog=harness.catalog(count=6);catalog['executionEpoch']=7
        for item in catalog['items']:item.pop('update',None)
        boot=harness.BOOT.replace('__CATALOG__',json.dumps(catalog)).replace('__FAIL__','0')
        boot=boot.replace("else if(path==='progress')result={ok:true,profileId:c.profileId,progress:{rows:{},prefs:{}}};",
            "else if(path==='progress')result={ok:true,profileId:c.profileId,progress:{rows:{'item-1':{status:'done',note:'Keep my install choices'}},prefs:{autoCheckOnLaunch:"+str(auto).lower()+"}}};")
        addition="""else if(path==='startup-check'){
          x.startupBody=body;
          if(__FAIL_START__)result={ok:false,error:'Connect the matching plugin to start automatic checking'};
          else {
            const checkedAt=Date.now()/1000;
            const base={checkedAt,expiresAt:checkedAt+14400,source:'nexus-api'};
            x.catalog.items[0].update={...base,verdict:'current',latestVersion:'1.0'};
            x.catalog.items[1].update={...base,verdict:'candidate',latestVersion:'2.0'};
            x.catalog.items[2].update={verdict:'unknown',availability:'authentication-required',reason:'Sign in to Nexus'};
            x.catalog.items[3].source={verified:true,type:'patreon',url:'https://www.patreon.com/fixture'};
            x.catalog.items[3].update={verdict:'review',capabilities:{checkMode:'manual'},reason:'Use page tracking'};
            x.catalog.items[4].update={...base,verdict:'review',latestVersion:'unusual-build'};
            x.catalog.items[5].update={...base,verdict:'current',latestVersion:'1.0'};
            x.catalog.items[5].localAnalysis={depth:'headers',plugins:[{name:'Changed.esp',provenance:{state:'source-changed'},eligibility:{status:'unknown'}}]};
            x.generation++;
            x.scan={state:__RUNNING__?'running':'completed',mode:'startup',jobId:3,checked:6,total:6,fromCache:2};
            result={ok:true,started:true,profileId:c.profileId,scan:structuredClone(x.scan)};
          }
        }else if(path==='cancel-scan'){
          x.cancelBody=body;x.scan.state='cancelled';result={ok:true,scan:structuredClone(x.scan)};
        }else if(path==='downloads')""".replace('__FAIL_START__',str(fail).lower()).replace('__RUNNING__',str(running).lower())
        boot=boot.replace("else if(path==='downloads')",addition)
        boot=boot.replace("window.__RESP__.release=",("window.__RESP__.defer.progress=true;\n" if deferred else '')+"window.__RESP__.release=")
        self.page.set_content(self.html.replace('<!--__MO2_BOOT__-->',boot),wait_until='domcontentloaded')
        self.page.wait_for_function('!!window.__ARSENAL_QA__');self.connected()
        if auto and not deferred:
            self.page.wait_for_function("__RESP__.counts['POST startup-check']===1")
        if auto and not fail and not running and not deferred:
            self.page.wait_for_function("document.querySelector('[data-id=\"item-1\"].mod-row')?.textContent.includes('Update available')")

    def test_launch_resolves_detected_status_without_overwriting_manual_notes(self):
        self.open_workspace()
        expected=['Up to date','Update available','Sign-in required','Page review','Version review','Up to date']
        for i,label in enumerate(expected):
            self.assertIn(label,self.page.locator(f'.mod-row[data-id="item-{i}"]').inner_text())
        self.assertIn('Maintenance',self.page.locator('.mod-row[data-id="item-5"]').inner_text())
        self.assertEqual(self.page.evaluate('__RESP__.startupBody'),{'profileId':'profile-a','executionEpoch':7})
        self.assertEqual(self.page.evaluate('__ARSENAL_QA__.verifyRow("item-1").state.note'),'Keep my install choices')
        self.assertEqual(self.page.evaluate('__ARSENAL_QA__.verifyRow("item-1").state.status'),'done')
        self.assertFalse(self.page.locator('.mod-row[data-id="item-1"] .mod-title').evaluate("e=>getComputedStyle(e).textDecorationLine.includes('line-through')"))
        for key in ('POST download','POST install','POST quick-update','POST update-queue'):
            self.assertEqual(self.page.evaluate('k=>__RESP__.counts[k]||0',key),0)
        self.assertEqual(self.page.locator('#detectedUpdates').inner_text(),'1')
        self.assertEqual(self.page.locator('#detectedMaintenance').inner_text(),'1')

    def test_compact_default_and_narrow_layout_keep_real_actions_reachable(self):
        self.open_workspace()
        self.assertEqual(self.page.locator('body').get_attribute('data-workspace'),'mods')
        self.assertFalse(self.page.locator('#unifiedCommand').is_visible())
        self.assertTrue(self.page.locator('#workbench').is_visible())
        self.assertLess(self.page.locator('.mod-row').first.bounding_box()['y'],450)
        self.assertLess(self.page.locator('.mod-row').first.bounding_box()['height'],80)
        output=Path(os.environ.get('MODHUNTER_QA_OUTPUT','/mnt/data/compact-workspace-proof'));output.mkdir(parents=True,exist_ok=True)
        self.page.screenshot(path=str(output/'compact-workspace.png'))
        for width in (768,390,1440):
            self.page.set_viewport_size({'width':width,'height':900})
            self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),width+1)
            self.assertTrue(self.page.locator('#unifiedScan').is_visible())
            self.assertTrue(self.page.locator('.mod-row').first.locator('[data-act="inspect"]').is_visible())
            if width==390:self.page.screenshot(path=str(output/'compact-workspace-mobile.png'))
        for name in ('updates','downloads','history','tools','extras','mods'):
            self.page.locator('.workspace-tabs [data-workspace="'+name+'"]').click()
            self.assertEqual(self.page.locator('body').get_attribute('data-workspace'),name)
        self.page.locator('.workspace-tabs [data-workspace="tools"]').click()
        self.page.keyboard.press('Control+k')
        self.assertEqual(self.page.locator('body').get_attribute('data-workspace'),'mods')
        self.assertTrue(self.page.locator('#search').evaluate('e=>e===document.activeElement'))
        self.assertEqual(self.page.evaluate('__RESP__.counts["POST startup-check"]'),1)

    def test_recent_results_do_not_trigger_another_automatic_request_on_poll(self):
        self.open_workspace()
        self.page.wait_for_function("__RESP__.counts['GET revision']>=3",timeout=5000)
        self.assertEqual(self.page.evaluate('__RESP__.counts["POST startup-check"]'),1)

    def test_auto_check_opt_out_is_hydrated_before_any_request(self):
        self.open_workspace(auto=False)
        self.page.wait_for_function("__RESP__.counts['GET revision']>=2",timeout=5000)
        self.assertEqual(self.page.evaluate('__RESP__.counts["POST startup-check"]||0'),0)
        self.assertIn('Automatic checks off',self.page.locator('#workspaceCheckStatus').inner_text())
        self.page.locator('.workspace-tabs [data-workspace="tools"]').click()
        self.page.locator('#autoCheckOnLaunch').check()
        self.page.wait_for_function("__RESP__.counts['POST startup-check']===1")

    def test_saved_preferences_must_load_before_automatic_checks_start(self):
        self.open_workspace(deferred=True)
        self.page.wait_for_function("__RESP__.pending.some(p=>p.path==='progress')")
        self.assertEqual(self.page.evaluate('__RESP__.counts["POST startup-check"]||0'),0)
        self.page.evaluate("__RESP__.release('progress')")
        self.page.wait_for_function("__RESP__.counts['POST startup-check']===1")

    def test_startup_error_remains_actionable_and_does_not_become_current(self):
        self.open_workspace(fail=True)
        self.page.wait_for_function("document.getElementById('workspaceCheckDetail').textContent.includes('matching plugin')")
        self.page.locator('#search').fill('Unique')
        self.page.wait_for_function("__RESP__.counts['GET revision']>=2",timeout=5000)
        self.assertIn('matching plugin',self.page.locator('#workspaceCheckDetail').inner_text())
        self.assertEqual(self.page.locator('#detectedCurrent').inner_text(),'0')
        self.assertEqual(self.page.evaluate('__RESP__.counts["POST startup-check"]'),1)

    def test_cancel_targets_the_observed_job_without_installing(self):
        self.open_workspace(running=True)
        self.page.locator('#workspaceCancel').click()
        self.page.wait_for_function("__RESP__.cancelBody?.jobId===3")
        self.assertEqual(self.page.evaluate('__RESP__.cancelBody'),{'profileId':'profile-a','jobId':3,'executionEpoch':7})
        self.assertTrue(self.page.locator('#workspaceCancel').is_hidden())
        self.assertIn('cancelled',self.page.locator('#workspaceCheckStatus').inner_text().lower())

if __name__=='__main__':unittest.main()
