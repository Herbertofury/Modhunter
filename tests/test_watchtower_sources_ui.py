"""Chromium source/account interaction fixtures; native/provider login has separate proof."""
import importlib.util
import json
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location("watchtower_base_sources", Path(__file__).with_name("test_watchtower_browser.py"))
base = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(base)

OVERRIDE = r"""(() => {
 const original=window.__TAURI__.core.invoke;
 window.__TAURI__.core.invoke=async(command,args)=>{
  const f=window.__WATCH_FIX__,v=args.value||{},kind=args.kind;
  if(!['saveSourceBinding','saveCreator','decideOffer','clearOfferDecision','clearFileDecision'].includes(kind))return original(command,args);
  f.calls.push(structuredClone(args));
  const watch=f.state.watches.find(w=>w.id===(v.watchId||f.state.acquisitions?.offers.find(o=>o.id===v.id)?.watchId));
  if(kind==='saveSourceBinding'){
   if(watch.generation!==v.generation)throw Error('Stale source generation');
   watch.sourceBinding={...v,association:watch.association,sourceUrl:watch.url};watch.generation++;
   f.state.sourcePreferences.bindings=[watch.sourceBinding];f.change(()=>{});return {ok:true,watchId:watch.id};
  }
  if(kind==='saveCreator'){
   const creator={...v,id:v.id||'creator-1000',revision:(v.revision||0)+1};
   f.state.sourcePreferences.creators=f.state.sourcePreferences.creators.filter(c=>c.id!==creator.id);f.state.sourcePreferences.creators.push(creator);f.change(()=>{});return {ok:true,creatorId:creator.id};
  }
  if(kind==='clearFileDecision'){watch.sourceFiles=watch.sourceFiles.filter(row=>row.sourceFileKey!==v.sourceFileKey);watch.generation++;f.change(()=>{});return {ok:true};}
  const offer=f.state.acquisitions.offers.find(o=>o.id===v.id);
  offer.classification=kind==='clearOfferDecision'?'unclassified':v.kind;offer.ignored=kind==='clearOfferDecision'?false:v.ignored;offer.explicitDecision=kind!=='clearOfferDecision';watch.generation++;f.state.acquisitions.revision++;f.change(()=>{});return {ok:true,watchId:watch.id};
 };
})();"""

class SourceAccountUiTests(base.WatchtowerBrowserTests):
    def open_sources(self):
        snapshot = base.snapshot(1)
        watch = snapshot["watches"][0]
        watch["association"] = {"profileId": "profile-a", "itemId": "exact-mod", "name": "Exact installed mod", "sourceUrl": watch["url"]}
        watch["sourceBinding"] = {"watchId": watch["id"], "sourceUrl": watch["url"], "association": watch["association"], "creatorId": None, "game": "SkyrimSE", "runtime": "1.6.1170", "variant": "CBBE", "compatibilityConfirmed": True, "contentIntent": "update", "preferred": True}
        watch["sourceFiles"] = [{"watchId": watch["id"], "sourceUrl": watch["url"], "sourceFileKey": "a" * 64, "name": "Optional.zip", "ignored": True, "kind": "update", "association": watch["association"]}]
        snapshot["sourcePreferences"] = {"creators": [], "bindings": [watch["sourceBinding"]], "accounts": [], "fileDecisionCount": 1}
        snapshot["acquisitions"] = {"revision": 1, "jobs": [], "offers": [{"id": "offer-1", "watchId": watch["id"], "name": "New variant.zip", "kind": "attachment", "origin": "https://mods.example.com", "sourceFileKey": "b" * 64, "current": True, "supported": True, "related": True, "newContentOptIn": False, "ignored": False, "classification": "unclassified", "explicitDecision": False, "canUpdate": True}]}
        html = (base.UI / "watchtower.html").read_text("utf-8")
        html = html.replace('<link rel="stylesheet" href="watchtower.css">', '<style>' + (base.UI / "watchtower.css").read_text("utf-8") + '</style>')
        scripts = base.BOOT.replace("__WATCHTOWER_INITIAL_STATE__", json.dumps(snapshot)) + OVERRIDE + (base.UI / "watchtower.js").read_text("utf-8")
        html = html.replace('<script src="watchtower.js"></script>', '<script>' + scripts + '</script>')
        self.page.set_content(html, wait_until="domcontentloaded")
        self.page.wait_for_function('!document.getElementById("addPage").disabled')

    def source_dialog(self):
        self.page.locator('[data-action="detail"][data-id="watch-0"]').first.click()
        self.page.locator('[data-action="source-options"]').click()

    def test_source_choices_submit_exact_identity_and_compatibility(self):
        self.open_sources()
        self.source_dialog()
        self.page.locator('#sourceRuntime').fill('1.6.1170')
        self.page.locator('#sourceCompatibility').check()
        self.page.locator('#saveSource').click()
        self.page.wait_for_function("__WATCH_FIX__.calls.some(c=>c.kind==='saveSourceBinding')")
        value = self.calls('saveSourceBinding')[0]['value']
        self.assertEqual(value['watchId'], 'watch-0')
        self.assertEqual(value['generation'], 1)
        self.assertEqual(value['game'], 'SkyrimSE')
        self.assertEqual(value['runtime'], '1.6.1170')
        self.assertEqual(value['variant'], 'CBBE')
        self.assertTrue(value['compatibilityConfirmed'])
        self.assertEqual(self.calls('saveWatch'), [])

    def test_source_form_refuses_a_changed_generation(self):
        self.open_sources()
        self.source_dialog()
        self.page.evaluate("__WATCH_FIX__.change(s=>{s.watches[0].generation++})")
        self.page.locator('#saveSource').click()
        self.assertIn('changed while the form was open', self.page.locator('#sourceError').inner_text())
        self.assertEqual(self.calls('saveSourceBinding'), [])

    def test_creator_choices_return_to_source_form_and_require_explicit_binding_save(self):
        self.open_sources()
        self.source_dialog()
        self.page.locator('#manageCreator').click()
        self.page.locator('#creatorName').fill('A creator')
        self.page.locator('#creatorNewContent').check()
        self.page.locator('#creatorMutedOrigins').fill('https://www.patreon.com')
        self.page.locator('#saveCreator').click()
        self.page.wait_for_function('document.getElementById("sourceDialog").open')
        self.assertEqual(self.page.locator('#sourceCreator').input_value(), 'creator-1000')
        self.assertEqual(self.calls('saveSourceBinding'), [])
        self.page.locator('#saveSource').click()
        self.page.wait_for_function("__WATCH_FIX__.calls.some(c=>c.kind==='saveSourceBinding')")
        self.assertEqual(self.calls('saveSourceBinding')[0]['value']['creatorId'], 'creator-1000')
        self.assertTrue(self.calls('saveCreator')[0]['value']['offerNewContent'])

    def test_unknown_and_ignored_files_cannot_open_or_install_automatically(self):
        self.open_sources()
        self.navigate('downloads')
        self.assertTrue(self.page.locator('[data-action="download-offer"]').is_disabled())
        self.page.locator('[data-action="download-classify-update"]').click()
        self.page.locator('#confirmCancel').click()
        self.assertEqual(self.calls('decideOffer'), [])
        self.page.locator('[data-action="download-classify-new"]').click()
        self.page.wait_for_function("__WATCH_FIX__.state.acquisitions.offers[0].classification==='newContent'")
        self.assertEqual(self.calls('decideOffer')[0]['value']['kind'], 'newContent')
        self.assertEqual(self.calls('openOffer'), [])
        self.assertEqual(self.calls('approveDownload'), [])
        self.assertEqual(self.calls('install'), [])
        self.page.locator('[data-action="download-ignore"]').click()
        self.page.wait_for_function("__WATCH_FIX__.state.acquisitions.offers[0].ignored")
        self.assertTrue(self.page.locator('[data-action="download-offer"]').is_disabled())

    def test_saved_file_ignore_can_be_reset_without_discovering_or_downloading_a_file(self):
        self.open_sources()
        self.page.locator('[data-action="detail"][data-id="watch-0"]').first.click()
        self.page.locator('summary', has_text='Saved exact file choices').click()
        self.page.locator('[data-action="source-forget-file"]').click()
        self.page.wait_for_function("__WATCH_FIX__.calls.some(c=>c.kind==='clearFileDecision')")
        self.assertEqual(self.calls('clearFileDecision')[0]['value'], {'watchId': 'watch-0', 'generation': 1, 'sourceFileKey': 'a' * 64})
        self.assertEqual(self.calls('resolveFiles'), [])
        self.assertEqual(self.calls('openOffer'), [])

    def test_observed_login_and_restricted_access_remain_separate_from_user_confirmation(self):
        self.open_sources()
        self.page.evaluate("""__WATCH_FIX__.change(s=>{
          s.sessions=[{id:'session-1',label:'Patreon',origin:'https://www.patreon.com',storageGeneration:1,remember:true,state:'confirmed'}];
          s.sourcePreferences.accounts=[{sessionId:'session-1',storageGeneration:1,loginState:'signedIn',status:'restricted',verificationHold:false,guidance:'',observedAt:Date.now()}];
        })""")
        self.navigate('sessions')
        text = self.page.locator('.session-card').inner_text()
        self.assertIn('Confirmed by you', text)
        self.assertIn('Sign-in indicator observed', text)
        self.assertIn('restricted access', text)
        self.assertIn('File access is checked separately', text)

for name in list(base.WatchtowerBrowserTests.__dict__):
    if name.startswith('test_') and name not in SourceAccountUiTests.__dict__:
        setattr(SourceAccountUiTests, name, None)
if __name__ == '__main__':
    unittest.main()
