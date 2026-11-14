"""Real Chromium frontend regressions; native Windows integration is a separate gate."""
from __future__ import annotations
import json
import unittest
from pathlib import Path
import test_frontend_responsiveness as harness

class RepairBrowserTests(unittest.TestCase):
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
    def open_live(self):
        boot=harness.BOOT.replace("__CATALOG__",json.dumps(harness.catalog())).replace("__FAIL__","0")
        boot=boot.replace("else if(path==='downloads')", """else if(path==='refresh')result={ok:true,catalog:c,generation:x.generation};
  else if(path==='plugin-audit')result={ok:true,state:'running',profileId:c.profileId};
  else if(path==='diagnostics')result={ok:true,native:{automaticStartup:true,qtRuntime:'6.11.1',callbackWarnings:[]}};
  else if(path==='install-preview?downloadId=0')result={ok:true,canInstall:true,downloadId:0,profileName:'Fixture A',modName:'First download',provider:'nexus',warnings:[],errors:[],backup:{files:[]}};
  else if(path==='install'){x.installed=body;result={ok:true,state:'installing'};}
  else if(path==='downloads')""")
        self.page.set_content(self.html.replace("<!--__MO2_BOOT__-->",boot),wait_until="domcontentloaded")
        self.page.wait_for_function("!!window.__ARSENAL_QA__")
        self.connected()
    def test_desktop_actions_reach_the_existing_authenticated_endpoints(self):
        self.open_live()
        self.assertTrue(self.page.locator('#nativeWelcome').is_hidden())
        for action in ('refresh','audit','diagnostics'):
            self.page.evaluate("a=>window.modhunterDesktopAction(a)",action)
        self.page.wait_for_function("document.getElementById('connectionDetailsText').textContent.includes('6.11.1')")
        self.assertEqual(self.page.evaluate("__RESP__.counts['POST refresh']"),1)
        self.assertEqual(self.page.evaluate("__RESP__.counts['POST plugin-audit']"),1)
        self.assertEqual(self.page.evaluate("__RESP__.counts['GET diagnostics']"),1)
        self.page.locator('#connectionDetailsClose').click()
        self.assertFalse(self.page.locator('#connectionDetails').is_visible())
    def test_zero_handle_preview_confirmation_dispatches_the_first_download(self):
        self.open_live()
        self.page.evaluate("__RESP__.downloads={'0':{status:'complete',provider:'nexus',itemId:'item-0',modId:7}}")
        # Opening Workshop asks the production renderer to load current tickets.
        self.page.locator('#unifiedReview').click()
        self.page.locator('#workshopRefreshDownloads').click()
        self.page.locator('button[data-download-details="0"]').wait_for()
        self.page.locator('button[data-download-details="0"]').click()
        self.page.locator('#installPreviewConfirm').wait_for()
        self.page.wait_for_function("!document.getElementById('installPreviewConfirm').disabled")
        self.page.locator('#installPreviewConfirm').click()
        self.page.wait_for_function("__RESP__.installed?.downloadId===0")
        self.assertEqual(self.page.evaluate("__RESP__.installed"),{'downloadId':0,'confirm':'INSTALL'})
    def test_connection_details_escape_untrusted_runtime_strings(self):
        self.open_live()
        self.page.evaluate("""()=>{const original=window.fetch;window.fetch=async(url,options)=>String(url)==='/api/diagnostics'?new Response(JSON.stringify({ok:true,native:{qtRuntime:'<img src=x onerror=alert(1)>'}})):original(url,options)}""")
        self.page.evaluate("window.modhunterDesktopAction('diagnostics')")
        self.page.wait_for_function("document.getElementById('connectionDetailsText').textContent.includes('<img')")
        self.assertEqual(self.page.locator('#connectionDetails img').count(),0)

if __name__=='__main__':unittest.main()
