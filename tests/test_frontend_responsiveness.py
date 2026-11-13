"""Real Chromium checks for polling, profile races and large-list responsiveness.

The HTTP contract is deliberately delayed in memory. These tests exercise the
shipped frontend, not native Windows MO2 or a real Nexus account.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import unittest
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "mo2_update_arsenal" / "web"


def build_page():
    """Compile source in memory; do not rewrite another worker's artifacts."""
    replacements = {
        "/*__STYLE__*/": "\n".join((WEB / name).read_text("utf8") for name in
            ("style.css", "aether_fx.css", "realm.css", "siege.css", "atelier.css", "mod_insights.css", "source_insights.css")),
        "/*__SCRIPT__*/": (WEB / "source_insights.js").read_text("utf8") + "\n" + (WEB / "mod_insights.js").read_text("utf8") + "\n" + (WEB / "app.js").read_text("utf8").replace(
            "/*__POWERSHELL__*/", json.dumps("")),
        "/*__CATALOG__*/": (WEB / "catalog.json").read_text("utf8").replace("</", "<\\/"),
        "/*__AETHER_SCRIPT__*/": (WEB / "aether_fx.js").read_text("utf8"),
        "/*__REALM_SCRIPT__*/": (WEB / "realm.js").read_text("utf8"),
        "/*__SIEGE_SCRIPT__*/": (WEB / "siege.js").read_text("utf8"),
        "<!--__REALM_LAYOUT__-->": (WEB / "realm_layout.html").read_text("utf8").replace(
            "<!--__SIEGE_LAYOUT__-->", (WEB / "siege_layout.html").read_text("utf8")),
    }
    page = (WEB / "app.template.html").read_text("utf8")
    for marker, content in replacements.items():
        page = page.replace(marker, content)
    return page


def catalog(profile="profile-a", count=3):
    seed = json.loads((WEB / "catalog.json").read_text("utf8"))["items"][0]
    items = []
    for index in range(count):
        item = copy.deepcopy(seed)
        item.update(id=f"item-{index}", name=f"Unique Fixture {index}", groupId="group",
                    group="Fixtures", priority=index, installedVersion="1.0", nexusId=index+1,
                    flags=[], audit={}, screenshotNote="", author="Test Author",
                    source={"type":"nexus", "verified":True,
                            "url":f"https://www.nexusmods.com/skyrimspecialedition/mods/{index+1}"},
                    update={"verdict":"candidate", "latestVersion":"2.0"})
        items.append(item)
    return {"catalogId":profile, "profileId":profile, "profileName":profile,
            "game":"skyrimspecialedition", "gameRuntime":"1.5.97", "items":items,
            "groups":[{"id":"group", "name":"Fixtures", "count":count,
                       "startPriority":0, "color":"mint"}]}


BOOT = r"""
<script>
window.__MO2_BRIDGE_BOOT__={active:true,token:'test-only'};
const realTimeout=window.setTimeout.bind(window);
// Only scale background polling. Request deadlines and animation frames retain
// their real timing, and delayed fetches are explicitly released by each test.
window.setTimeout=(fn,ms,...args)=>realTimeout(fn,[1100,1200,4200].includes(ms)?ms/10:ms,...args);
window.__RESP__={catalog:__CATALOG__,generation:1,counts:{},active:{},max:{},defer:{},pending:[],failSnapshots:__FAIL__,scan:{state:'idle'},quick:{state:'idle'},gameScore:0};
window.__RESP__.release=path=>{const x=window.__RESP__;x.defer[path]=false;for(const pending of x.pending.filter(p=>p.path===path))pending.resolve();x.pending=x.pending.filter(p=>p.path!==path)};
window.fetch=async(url,options={})=>{
 const x=window.__RESP__,path=String(url).slice(5),method=options.method||'GET',key=method+' '+path;
 x.counts[key]=(x.counts[key]||0)+1;x.active[key]=(x.active[key]||0)+1;x.max[key]=Math.max(x.max[key]||0,x.active[key]);
 const c=structuredClone(x.catalog),body=JSON.parse(options.body||'{}');
 let result={ok:true};
 try{
  if(path==='snapshot'){
   if(x.failSnapshots>0){x.failSnapshots--;throw Error('MO2 has not started yet');}
   result={ok:true,catalog:c,generation:x.generation};
  }else if(path==='revision')result={ok:true,profileId:c.profileId,generation:x.generation};
  else if(path==='update-plan')result={ok:true,profileId:c.profileId,profileName:c.profileName,
   summary:{candidates:c.items.length,total:c.items.length,review:0,manual:0,current:0,risky:0},
   items:c.items.map(m=>({...m,lane:'candidate',latestVersion:'2.0',risks:[]}))};
  else if(path==='progress')result={ok:true,profileId:c.profileId,progress:{rows:{},prefs:{}}};
  else if(path==='game?compact=1')result={ok:true,profileId:c.profileId,score:x.gameScore,feed:[],updates:0};
  else if(path==='downloads')result={ok:true,profileId:c.profileId,downloads:x.downloads||{}};
  else if(path==='activity')result={ok:true,profileId:c.profileId,records:[]};
  else if(path==='scan'){
   if(method==='POST'){x.scan={state:'running',mode:body.mode,checked:0,total:c.items.length};result={ok:true,state:'running',profileId:c.profileId};}
   else result={ok:true,profileId:c.profileId,scan:structuredClone(x.scan)};
  }else if(path==='quick-update')result={ok:true,profileId:c.profileId,state:body.all?'running':'completed',queued:[],skipped:[{itemId:'item-0',reason:'Choose a file'}]};
  else if(path==='quick-state')result={ok:true,profileId:c.profileId,state:'running',completed:0,total:3,queued:[],skipped:[]};
  if(x.defer[path])await new Promise(resolve=>x.pending.push({path,resolve}));
  return new Response(JSON.stringify(result),{status:200,headers:{'Content-Type':'application/json'}});
 }finally{x.active[key]--;}
};
</script>
"""


class FrontendResponsivenessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        required = os.environ.get("MODHUNTER_REQUIRE_BROWSER") == "1"
        if sync_playwright is None:
            if required:
                raise RuntimeError("MODHUNTER_REQUIRE_BROWSER=1 but Playwright is unavailable")
            raise unittest.SkipTest("Playwright unavailable; browser checks not executed")
        cls.runtime = sync_playwright().start()
        # CI installs Playwright's managed Chromium. A system installation is
        # optional; omitting executable_path selects the managed browser.
        browser_args = {"headless":True, "args":["--no-sandbox", "--disable-dev-shm-usage"]}
        chromium_path = shutil.which("chromium") or shutil.which("chromium-browser")
        if chromium_path:
            browser_args["executable_path"] = chromium_path
        try:
            cls.browser = cls.runtime.chromium.launch(**browser_args)
        except Exception as error:
            cls.runtime.stop()
            if required:
                raise
            if "socket() failed: Operation not permitted" in str(error):
                raise unittest.SkipTest("Runner blocks Chromium IPC sockets; browser checks not executed") from error
            if "Executable doesn't exist" in str(error):
                raise unittest.SkipTest("Playwright Chromium is not installed; browser checks not executed") from error
            raise
        cls.html = build_page()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width":1440,"height":900},
                                                reduced_motion="reduce")
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [])

    def open_live(self, fail=0):
        boot = BOOT.replace("__CATALOG__", json.dumps(catalog())).replace("__FAIL__", str(fail))
        self.page.set_content(self.html.replace("<!--__MO2_BOOT__-->", boot), wait_until="domcontentloaded")
        self.page.wait_for_function("!!window.__ARSENAL_QA__")

    def connected(self):
        self.page.wait_for_function("document.getElementById('livePill').classList.contains('connected')")
        self.page.wait_for_function("window.__ARSENAL_QA__.game()?.profileId==='profile-a'")

    def test_initial_connection_failure_recovers_without_reload(self):
        self.open_live(fail=1)
        self.page.wait_for_function("document.getElementById('livePill').textContent.includes('DISCONNECTED')")
        self.assertFalse(self.page.locator("#livePill").evaluate("e=>e.classList.contains('connected')"))
        self.connected()
        self.assertGreaterEqual(self.page.evaluate("__RESP__.counts['GET snapshot']"), 2)

    def test_concurrent_game_reads_coalesce_and_old_profile_results_are_ignored(self):
        self.open_live(); self.connected()
        self.page.evaluate("""()=>{const x=__RESP__;x.defer['game?compact=1']=true;x.gameScore=9000;
            window.firstRead=__ARSENAL_QA__.refreshGame();window.secondRead=__ARSENAL_QA__.refreshGame();}""")
        self.page.wait_for_function("__RESP__.pending.some(p=>p.path==='game?compact=1')")
        other = catalog("profile-b")
        self.page.evaluate("c=>{__RESP__.catalog=c;__RESP__.gameScore=12;__RESP__.defer['game?compact=1']=false;__ARSENAL_QA__.setCatalog(c);}", other)
        self.page.wait_for_function("__ARSENAL_QA__.game()?.profileId==='profile-b'")
        self.page.evaluate("__RESP__.release('game?compact=1')")
        self.page.evaluate("Promise.all([firstRead,secondRead])")
        self.assertEqual(self.page.evaluate("__ARSENAL_QA__.game().score"), 12)
        self.assertEqual(self.page.evaluate("__ARSENAL_QA__.game().profileId"), "profile-b")
        # One old-context request plus one new-context request can coexist; two
        # callers in the same context must share their single fetch.
        self.assertEqual(self.page.evaluate("__RESP__.max['GET game?compact=1']"), 2)

    def test_scan_polling_does_not_overlap_when_mo2_is_slow(self):
        self.open_live(); self.connected()
        self.page.evaluate("""()=>{__RESP__.defer.scan=true;
            document.getElementById('liveAll').onclick();document.getElementById('liveAll').onclick();}""")
        self.page.wait_for_function("__RESP__.pending.some(p=>p.path==='scan')")
        self.assertEqual(self.page.evaluate("__RESP__.counts['POST scan']"), 1)
        self.page.evaluate("__RESP__.release('scan')")
        self.page.wait_for_function("document.getElementById('liveScanProgress').textContent.includes('Beginning')")
        self.page.evaluate("__RESP__.defer.scan=true")
        self.page.wait_for_function("__RESP__.pending.some(p=>p.path==='scan')")
        self.page.wait_for_timeout(450)
        self.assertEqual(self.page.evaluate("__RESP__.max['GET scan']"), 1)
        self.page.evaluate("__RESP__.scan={state:'completed',mode:'all',checked:3,total:3};__RESP__.release('scan')")
        self.page.wait_for_function("!document.getElementById('liveAll').disabled")

    def test_quick_polling_does_not_overlap_and_profile_switch_stops_old_job(self):
        self.open_live(); self.connected()
        self.page.evaluate("__RESP__.defer['quick-state']=true")
        self.page.locator("#workshopQuickAll").click()
        self.page.wait_for_function("__RESP__.pending.some(p=>p.path==='quick-state')")
        self.page.wait_for_timeout(400)
        self.assertEqual(self.page.evaluate("__RESP__.max['GET quick-state']"), 1)
        self.page.evaluate("c=>{__RESP__.catalog=c;__ARSENAL_QA__.setCatalog(c);}", catalog("profile-b"))
        self.page.evaluate("__RESP__.release('quick-state')")
        self.page.wait_for_function("!document.getElementById('workshopQuickAll').disabled")
        self.assertTrue(self.page.locator("#filePickerVeil").is_hidden())
        self.assertEqual(self.page.evaluate("__ARSENAL_QA__.getCatalog().profileId"), "profile-b")

    def test_appearance_changes_preserve_catalog_nodes_and_keyboard_focus(self):
        self.open_live(); self.connected()
        self.page.evaluate("window.originalRow=document.querySelector('#modList .mod-row')")
        self.page.locator("#densityToggle").click()
        self.assertTrue(self.page.evaluate("originalRow===document.querySelector('#modList .mod-row')"))
        self.page.evaluate("window.originalLink=document.querySelector('#modList .source-title');originalLink.focus()")
        self.page.evaluate("__ARSENAL_QA__.refreshGame()")
        self.assertTrue(self.page.evaluate("document.activeElement===originalLink"))

    def test_one_changed_row_preserves_the_full_list_and_unrelated_focus(self):
        self.page.set_content(self.html, wait_until="domcontentloaded")
        data = catalog(count=2500); data["offline"] = True
        self.page.evaluate("c=>window.modhunterImportCatalog(c)", data)
        self.page.evaluate("""()=>{
          window.unchangedRow=document.getElementById('row-item-0');
          window.unchangedLink=unchangedRow.querySelector('.source-title');unchangedLink.focus();
          window.changedRow=document.getElementById('row-item-2499');
          window.rowMutations=[];
          new MutationObserver(rs=>rowMutations.push(...rs.map(r=>({added:r.addedNodes.length,removed:r.removedNodes.length}))))
            .observe(document.getElementById('modList'),{childList:true});
          changedRow.querySelector('[data-act="favorite"]').click();
        }""")
        self.assertEqual(self.page.locator("#modList .mod-row").count(), 2500)
        self.assertTrue(self.page.evaluate("unchangedRow===document.getElementById('row-item-0')"))
        self.assertTrue(self.page.evaluate("document.activeElement===unchangedLink"))
        self.assertTrue(self.page.evaluate("changedRow!==document.getElementById('row-item-2499')"))
        self.assertEqual(self.page.evaluate("rowMutations"), [{"added":1, "removed":1}])
        self.assertIn("active", self.page.locator("#row-item-2499 .favorite-btn").get_attribute("class"))
        # Removing a previously patched row and restoring the whole list must
        # not resurrect the pre-edit HTML cache.
        self.page.locator("#search").fill('"Unique Fixture 0"')
        self.page.wait_for_function("document.querySelectorAll('#modList .mod-row').length===1")
        self.page.locator("#clearSearch").click()
        self.assertEqual(self.page.locator("#modList .mod-row").count(), 2500)
        self.assertIn("active", self.page.locator("#row-item-2499 .favorite-btn").get_attribute("class"))
        self.page.evaluate("""()=>{const button=document.querySelector('#row-item-2499 .favorite-btn');button.focus();button.click();}""")
        self.assertTrue(self.page.evaluate("document.activeElement===document.querySelector('#row-item-2499 .favorite-btn')"))
        self.assertNotIn("active", self.page.locator("#row-item-2499 .favorite-btn").get_attribute("class"))

    def test_one_download_update_preserves_unrelated_workshop_controls(self):
        self.open_live(); self.connected()
        self.page.wait_for_function("document.querySelectorAll('#workshopResults .workshop-item').length===3")
        self.page.evaluate("""async()=>{
          window.unchangedWorkshop=document.querySelector('[data-workshop-id="item-1"]');
          window.unchangedWorkshopLink=unchangedWorkshop.querySelector('.workshop-item-title');unchangedWorkshopLink.focus();
          window.workshopMutations=[];
          new MutationObserver(rs=>workshopMutations.push(...rs.map(r=>({added:r.addedNodes.length,removed:r.removedNodes.length}))))
            .observe(document.getElementById('workshopResults'),{childList:true});
          __RESP__.downloads={'73':{itemId:'item-0',status:'downloading',installedVersion:'1.0',targetVersion:'2.0'}};
          await document.getElementById('workshopRefreshDownloads').onclick();
        }""")
        self.page.wait_for_function("document.querySelector('[data-workshop-id=\"item-0\"] .workshop-ticket-note')")
        self.assertTrue(self.page.evaluate("unchangedWorkshop===document.querySelector('[data-workshop-id=\"item-1\"]')"))
        self.assertTrue(self.page.evaluate("document.activeElement===unchangedWorkshopLink"))
        self.assertEqual(self.page.evaluate("workshopMutations"), [{"added":1, "removed":1}])
        self.assertEqual(self.page.locator("#workshopResults .workshop-item").count(), 3)

    def test_external_row_removal_and_reordering_are_repaired_on_render(self):
        self.page.set_content(self.html, wait_until="domcontentloaded")
        data = catalog(count=12); data["offline"] = True
        self.page.evaluate("c=>window.modhunterImportCatalog(c)", data)
        self.page.evaluate("""()=>{
          document.getElementById('row-item-5').remove();
          const list=document.getElementById('modList');
          list.insertBefore(document.getElementById('row-item-10'),document.getElementById('row-item-0'));
          document.querySelector('#row-item-11 [data-act="favorite"]').click();
        }""")
        self.assertEqual(self.page.locator("#modList .mod-row").evaluate_all(
            "rows=>rows.map(row=>row.dataset.id)"), [f"item-{i}" for i in range(12)])
        self.assertIn("active", self.page.locator("#row-item-11 .favorite-btn").get_attribute("class"))
        # Even an otherwise unchanged render must replace a structurally stale
        # DOM; the whole-list HTML cache cannot suppress that repair.
        self.page.evaluate("""()=>{
          document.getElementById('row-item-6').remove();
          document.getElementById('clearSearch').click();
        }""")
        self.assertEqual(self.page.locator("#modList .mod-row").count(), 12)
        self.assertIn("active", self.page.locator("#row-item-11 .favorite-btn").get_attribute("class"))

    def test_large_catalog_search_coalesces_input_without_dropping_records(self):
        self.page.set_content(self.html, wait_until="domcontentloaded")
        data = catalog(count=2500); data["offline"] = True
        self.page.evaluate("c=>window.modhunterImportCatalog(c)", data)
        self.assertEqual(self.page.locator("#modList .mod-row").count(), 2500)
        self.page.evaluate("""()=>{window.searchMutations=0;
          new MutationObserver(rs=>searchMutations+=rs.length).observe(document.getElementById('modList'),{childList:true});
          const input=document.getElementById('search');
          for(const value of ['Unique','Unique Fixture','Unique Fixture 2','Unique Fixture 2499']){
            input.value=value;input.dispatchEvent(new Event('input',{bubbles:true}));
          }}""")
        self.page.wait_for_function("document.querySelectorAll('#modList .mod-row').length===1")
        self.assertEqual(self.page.evaluate("searchMutations"), 1)
        self.assertEqual(self.page.evaluate("__ARSENAL_QA__.getCatalog().items.length"), 2500)
        self.page.locator("#clearSearch").click()
        self.assertEqual(self.page.locator("#modList .mod-row").count(), 2500)
        self.page.evaluate("""()=>{window.workshopSearchMutations=0;
          new MutationObserver(rs=>workshopSearchMutations+=rs.length).observe(document.getElementById('workshopResults'),{childList:true});
          const input=document.getElementById('workshopSearch');
          for(const value of ['Unique','Unique Fixture','Unique Fixture 2','Unique Fixture 2499']){
            input.value=value;input.dispatchEvent(new Event('input',{bubbles:true}));
          }}""")
        self.page.wait_for_function("document.querySelectorAll('#workshopResults .workshop-item').length===1")
        self.assertEqual(self.page.evaluate("workshopSearchMutations"), 1)
        self.assertEqual(self.page.evaluate("__ARSENAL_QA__.getCatalog().items.length"), 2500)
        self.page.locator("#workshopSearch").fill("")
        self.page.wait_for_function("document.querySelectorAll('#workshopResults .workshop-item').length===2500")


if __name__ == "__main__":
    unittest.main()
