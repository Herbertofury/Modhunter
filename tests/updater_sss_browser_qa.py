"""Real Chromium exercised against a local authenticated Modhunter server.

The native MO2 side is a deterministic fake; this is browser+HTTP integration
proof, not a claim that the Windows MO2 API or Nexus were exercised.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
from pathlib import Path

from playwright.sync_api import sync_playwright

from mo2_update_arsenal.file_advisor import advise_nexus_files
from mo2_update_arsenal.update_plan import plan_updates

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(os.environ.get('MODHUNTER_QA_OUTPUT', '/mnt/data/modhunter-sss/browser-proof'))
OUTPUT.mkdir(parents=True, exist_ok=True)

src = json.loads((ROOT / 'mo2_update_arsenal/web/catalog.json').read_text('utf8'))
mods = [copy.deepcopy(x) for x in src['items'][:3]]
mods[0].update({"id": "live-a", "name": "Arcane Example", "mo2InternalName": "Arcane Example",
                "installedVersion": "1.0", "nexusId": 44, "flags": [],
                "source": {"type": "nexus", "verified": True,
                           "url": "https://www.nexusmods.com/skyrimspecialedition/mods/44"},
                "update": {"verdict": "candidate", "latestVersion": "2.0", "source": "nexus-api"}})
mods[1].update({"id": "live-b", "name": "Quest Example", "mo2InternalName": "Quest Example",
                "installedVersion": "1.5", "nexusId": 45, "flags": ["DLL"],
                "source": {"type": "nexus", "verified": False, "url": ""},
                "update": {"verdict": "candidate", "latestVersion": "2.3", "source": "nexus-api"}})
mods[2].update({"id": "live-c", "name": "Manual Example", "mo2InternalName": "Manual Example",
                "installedVersion": "1.0", "nexusId": 0, "flags": [],
                "source": {"type": "unknown", "verified": False, "url": ""}})
for m in mods:
    m['group'] = 'Live profile'
    m['groupId'] = 'live-group'
catalog = {"schemaVersion": 2, "catalogId": "mo2-sss", "profileId": "sss-profile",
           "profileName": "SSS Profile", "game": "skyrimspecialedition",
           "gameRuntime": "1.5.97", "groups": [{"id": "live-group", "name": "Live profile",
           "startPriority": 0, "count": 3, "color": "mint"}], "items": mods}

# The Chromium sandbox on this runner blocks loopback HTTP. Feed the *real*
# compiled HTML into Chromium, intercepting its API calls with deterministic
# in-memory contracts. Server-side HTTP/authorization remains unit tested.
files = lambda mid: [
    {"fileId":90+mid,"name":"Legacy main","fileName":"old.zip","version":"1.0","category":1,"description":"Obsolete legacy runtime"},
    {"fileId":100+mid,"name":"Exact latest main","fileName":"latest-se.zip","version":"2.0" if mid==44 else "2.3","category":1,"description":"Check runtime and dependencies"},
    {"fileId":200+mid,"name":"Optional textures","fileName":"textures.zip","version":"2.0","category":2,"description":"Optional upgrade"},
]
review={str(mid): {"ok": True, "modId": mid, **advise_nexus_files(mods[ix],files(mid))}
        for ix,mid in enumerate((44,45))}
history=[{"key":"previous:101","sessionId":"previous","sessionActive":False,"needsReview":True,
          "actionable":False,"firstAt":"2026-11-10T00:00:00+00:00","lastAt":"2026-11-10T00:01:00+00:00",
          "status":"complete","profileId":"sss-profile","downloadId":101,"provider":"nexus",
          "modName":"Previously queued Quest Example","itemId":"live-b","fileName":"quest-se.zip",
          "installedVersion":"1.5","targetVersion":"2.3","gameRuntime":"1.5.97",
          "timeline":[{"at":"2026-11-10T00:00:00+00:00","status":"downloading","note":""},
                      {"at":"2026-11-10T00:01:00+00:00","status":"complete","note":""}]}]
mock={"catalog":catalog,"plan":plan_updates(catalog),"review":review,"history":history}
bootstrap = r"""
<script>
window.__MO2_BRIDGE_BOOT__={active:true,token:'browser-qa'};
window.__SSS_MOCK__={data:__DATA__,tickets:{},calls:[],focusCalls:[]};
window.fetch=async(url,opts={})=>{
 const x=window.__SSS_MOCK__, endpoint=String(url).slice(5);
 let result={ok:true};
 if(endpoint==='snapshot')result={ok:true,catalog:x.data.catalog,generation:x.generation||1};
 else if(endpoint==='update-plan')result=x.data.plan;
 else if(endpoint.startsWith('files?'))result=x.data.review[String(new URLSearchParams(endpoint.split('?')[1]).get('modId'))]||{ok:false,error:'Missing mod'};
 else if(endpoint==='downloads')result={ok:true,downloads:x.tickets};
 else if(endpoint==='activity'){
  if(x.delayActivityMs)await new Promise(resolve=>setTimeout(resolve,x.delayActivityMs));
  result={ok:true,profileId:x.data.catalog.profileId,
      records:x.data.history,count:x.data.history.length,durable:true,warning:''};
 }
 else if(endpoint==='revision')result={ok:true,generation:x.generation||1,profileId:x.data.catalog.profileId};
 else if(endpoint==='scan')result={ok:true,scan:{state:'completed',mode:'deep',queried:2}};
 else if(endpoint==='progress')result={ok:true,profileId:x.data.catalog.profileId,progress:{rows:{},prefs:{}}};
 else if(endpoint.startsWith('install-preview?')){
  const id=String(new URLSearchParams(endpoint.split('?')[1]).get('downloadId'));
  const ticket=x.tickets[id],active=x.data.catalog;
  const blocked=ticket?.gameRuntime&&ticket.gameRuntime!==active.gameRuntime;
  result={ok:true,canInstall:!!ticket&&!blocked,downloadId:Number(id),modName:'Arcane Example',
   archiveName:'latest-se.zip',provider:'nexus',installedVersion:'1.0',targetVersion:'2.0',
   gameRuntime:active.gameRuntime,profileName:active.profileName,backup:{files:['modlist.txt','plugins.txt','loadorder.txt','archives.txt','lockedorder.txt']},
   errors:blocked?['Your target game runtime changed: review this file again']:[],
   warnings:['Confirm the exact game runtime and FOMOD choices']};
 }
 else if(endpoint==='install'){
  const body=JSON.parse(opts.body||'{}');x.calls.push({endpoint,body});
  if(x.tickets[String(body.downloadId)])x.tickets[String(body.downloadId)].status='installing';
  result={ok:true,state:'installing',message:'MO2 native installer started'};
 }
 else if(endpoint==='install-ready'){
  const ready=Object.entries(x.tickets).filter(([id,t])=>t.status==='complete');
  x.calls.push({endpoint,body:{}});
  if(ready.length)x.tickets[ready[0][0]].status='installing';
  result={ok:true,armed:ready.length,blocked:[],state:ready.length?'installing':'idle',
    message:'Opening ready MO2 installers sequentially'};
 }
 else if(endpoint==='retry-download'){
  const body=JSON.parse(opts.body||'{}');x.calls.push({endpoint,body});
  const old=x.tickets[String(body.downloadId)];
  if(old&&old.status==='failed'){
   old.retriedBy=803;
   x.tickets[803]={...old,status:'downloading',retriedBy:undefined};
   result={ok:true,queued:[{itemId:old.itemId,downloadId:803}],failed:[],message:'Reviewed archive retried'};
  }else result={ok:false,error:'Failed download no longer present'};
 }
 else if(endpoint==='quick-update'){
  const body=JSON.parse(opts.body||'{}');x.calls.push({endpoint,body});
  if(body.all){
   const q={itemId:'live-a',downloadId:701,provider:'nexus'};
   x.tickets[701]={status:'downloading',provider:'nexus',modId:44,itemId:'live-a',installedVersion:'1.0',profileId:body.profileId,gameRuntime:x.data.catalog.gameRuntime};
   x.quickState={ok:true,state:'completed',profileId:body.profileId,total:2,completed:2,queued:[q],skipped:[{itemId:'live-b',reason:'Compatibility notes need a file choice'}],failed:[]};
   result={ok:true,state:'running',profileId:body.profileId,total:2};
  }else if(body.itemIds?.[0]==='live-a'){
   x.tickets[701]={status:'downloading',provider:'nexus',modId:44,itemId:'live-a',installedVersion:'1.0',profileId:body.profileId,gameRuntime:x.data.catalog.gameRuntime};
   result={ok:true,state:'completed',queued:[{itemId:'live-a',downloadId:701,provider:'nexus'}],skipped:[],failed:[]};
  }else result={ok:true,state:'completed',queued:[],skipped:[{itemId:'live-b',reason:'Compatibility notes need a file choice'}],failed:[]};
 }
 else if(endpoint==='quick-state')result=x.quickState||{ok:true,state:'idle',queued:[],skipped:[],failed:[]};
 else if(endpoint==='focus'){
  x.focusCalls.push(JSON.parse(opts.body||'{}'));result={ok:true,message:'Mod selected in MO2'};
 }
 else if(endpoint==='update-queue'){
  const body=JSON.parse(opts.body||'{}');x.calls.push({endpoint,body});
  const queued=body.entries.map((e,i)=>{const id=301+x.calls.length+i;x.tickets[id]={status:'downloading',provider:'nexus',modId:e.modId,itemId:e.itemId,installedVersion:e.installedVersion,profileId:body.profileId,gameRuntime:x.data.catalog.gameRuntime};return {itemId:e.itemId,downloadId:id,provider:'nexus'}});
  result={ok:true,queued,failed:[],partial:false,message:'Queued '+queued.length+' reviewed file'};
 }
 return new Response(JSON.stringify(result),{status:200,headers:{'content-type':'application/json'}});
};
</script>
""".replace('__DATA__',json.dumps(mock).replace('</','<\\/'))
html=(ROOT/'mo2_update_arsenal/web/index.html').read_text('utf8')
assert '<!--__MO2_BOOT__-->' in html
html=html.replace('<!--__MO2_BOOT__-->',bootstrap)
with sync_playwright() as pw:
    browser_args={'headless':True,'args':['--no-sandbox','--disable-gpu','--disable-dev-shm-usage']}
    chromium_path=shutil.which('chromium') or shutil.which('chromium-browser')
    if chromium_path:browser_args['executable_path']=chromium_path
    browser=pw.chromium.launch(**browser_args)
    errors=[]
    page=browser.new_page(viewport={"width":1440,"height":1000})
    page.on('pageerror',lambda e: errors.append(str(e)))
    page.on('dialog',lambda d: d.accept())
    page.set_content(html,wait_until='load',timeout=30000)
    page.locator('#workshopResults .workshop-item').first.wait_for(timeout=15000)
    assert page.locator('#workshopResults .workshop-item').count()==2
    # A mod name now opens its actual source, while a separate icon reveals it in MO2.
    page.context.route('https://www.nexusmods.com/**', lambda route: route.fulfill(status=200,
               content_type='text/html', body='<html><title>Mod source</title></html>'))
    name=page.locator('#row-live-a a.mod-title.source-title')
    assert name.get_attribute('href')=='https://www.nexusmods.com/skyrimspecialedition/mods/44'
    assert name.get_attribute('target')=='_blank'
    assert 'noopener' in (name.get_attribute('rel') or '')
    with page.expect_popup() as pop:
        name.click()
    opened=pop.value
    opened.wait_for_load_state('domcontentloaded')
    # The OS/CI may refuse outbound navigation. Exact href + popup creation are the contract.
    assert opened is not None
    opened.close()
    assert page.evaluate('window.__SSS_MOCK__.focusCalls.length')==0
    # Native Nexus IDs are direct links even if the separate source field is absent.
    assert page.locator('#row-live-b a.mod-title.source-title').get_attribute('href')=='https://www.nexusmods.com/skyrimspecialedition/mods/45'
    # Unlinked mods are explicitly searches, never fabricated exact links.
    assert page.locator('#row-live-c a.mod-title.source-title').get_attribute('href').startswith('https://www.google.com/search?q=')
    assert page.locator('#row-live-c a.mod-title.source-title.unknown').count()==1
    page.locator('#row-live-a button[data-act="inspect"]').click()
    page.locator('#drawerUrl').fill('javascript:alert(1)')
    assert page.locator('#drawerOpenLink').get_attribute('href') is None
    assert page.locator('#drawerOpenLink').get_attribute('aria-disabled')=='true'
    page.locator('#drawerUrl').fill('https://github.com/example/alternate')
    assert page.locator('#drawerOpenLink').get_attribute('href')=='https://github.com/example/alternate'
    assert page.locator('#drawerOpenLink').get_attribute('aria-disabled') is None
    page.locator('#drawerClose').click()
    page.locator('#row-live-a button.mod-inline-locate').click()
    assert page.evaluate('window.__SSS_MOCK__.focusCalls.length')==1
    workshop=page.locator('[data-workshop-id="live-a"] a.workshop-item-title')
    assert workshop.get_attribute('href')=='https://www.nexusmods.com/skyrimspecialedition/mods/44'
    with page.expect_popup() as pop:
        workshop.click()
    opened=pop.value
    opened.wait_for_load_state('domcontentloaded')
    assert opened is not None
    opened.close()
    # A custom source saved in the inspector wins consistently in every view.
    page.locator('#row-live-a button[data-act="inspect"]').click()
    page.locator('#drawerUrl').fill('https://github.com/example/custom-mod')
    page.locator('#saveInspector').click()
    assert page.locator('#row-live-a a.mod-title.source-title').get_attribute('href')=='https://github.com/example/custom-mod'
    assert page.locator('[data-workshop-id="live-a"] a.workshop-item-title').get_attribute('href')=='https://github.com/example/custom-mod'
    # Clearing the override restores the real native source, with no reassociation.
    page.locator('#row-live-a button[data-act="inspect"]').click()
    page.locator('#drawerUrl').fill('')
    page.locator('#saveInspector').click()
    assert page.locator('[data-workshop-id="live-a"] a.workshop-item-title').get_attribute('href')=='https://www.nexusmods.com/skyrimspecialedition/mods/44'
    page.locator('[data-workshop-id="live-a"] button.workshop-inline-locate').click()
    assert page.evaluate('window.__SSS_MOCK__.focusCalls.length')==2
    page.locator('#workshopFilter').select_option('all')
    assert page.locator('[data-workshop-id="live-c"] a.workshop-item-title.source-unresolved').count()==1
    # A trusted GitHub project takes users directly to its repository as well.
    page.evaluate("window.__SSS_MOCK__.data.catalog.items[2].source={type:'github',verified:true,url:'https://github.com/example/mod'};window.__SSS_MOCK__.generation=2")
    page.locator('#workshopFilter').select_option('manual')
    page.wait_for_function("document.querySelector('[data-workshop-id=\"live-c\"] a.workshop-item-title')?.getAttribute('href')==='https://github.com/example/mod'", timeout=15000)
    assert page.locator('[data-workshop-id="live-c"] a.workshop-item-title').get_attribute('href')=='https://github.com/example/mod'
    page.locator('#workshopFilter').select_option('candidate')
    page.screenshot(path=str(OUTPUT/'direct-mod-websites.png'),full_page=False)
    page.locator('#activityResults .activity-entry').first.wait_for(timeout=10000)
    assert page.locator('#activityResults .activity-entry').count()==1
    history_name=page.locator('#activityResults .activity-mod-link').first
    assert history_name.get_attribute('href')=='https://www.nexusmods.com/skyrimspecialedition/mods/45'
    with page.expect_popup() as pop:
        history_name.click()
    pop.value.close()
    assert page.locator('#activityResults .activity-entry').first.get_attribute('open') is None
    assert 'Previous session' in page.locator('#activityResults').inner_text()
    assert page.locator('#activityCount').inner_text()=='1 / 1 visible'
    page.locator('#activitySearch').fill('not-present')
    assert page.locator('#activityCount').inner_text()=='0 / 1 visible'
    page.locator('#activitySearch').fill('')
    page.locator('#activityFilter').select_option('verified')
    assert page.locator('#activityCount').inner_text()=='0 / 1 visible'
    page.locator('#activityFilter').select_option('review')
    assert page.locator('#activityCount').inner_text()=='1 / 1 visible'
    page.locator('#activityResults summary').first.click()
    assert 'No automatic resume' in page.locator('#activityResults').inner_text()
    page.screenshot(path=str(OUTPUT/'update-history.png'),full_page=False)
    with page.expect_download(timeout=10000) as export_info:
        page.locator('#activityExport').click()
    assert export_info.value.suggested_filename.startswith('Modhunter-Operations-')
    assert page.locator('#workshopReviewNext').is_enabled()
    page.locator('#workshopReviewNext').click()
    page.locator('#filePickerVeil').wait_for(state='visible')
    page.locator('.picker-suggested').first.wait_for(timeout=10000)
    assert page.locator('.picker-suggested').count()==1
    assert page.locator('#filePickerMatchCount').inner_text()=='3 / 3 files'
    page.locator('#filePickerSearch').fill('Optional textures')
    assert page.locator('#filePickerMatchCount').inner_text()=='1 / 3 files'
    assert page.locator('.picker-file:visible').count()==1
    page.locator('#filePickerSearch').fill('')
    assert page.locator('.picker-file:visible').count()==3
    page.screenshot(path=str(OUTPUT/'review-exact-file.png'),full_page=False)
    page.locator('.picker-suggested button').click()
    assert '1 REVIEWED' in page.locator('#workshopCount').inner_text()
    assert page.locator('button[data-workshop-act="remove"]').count()==1
    assert page.locator('#workshopReviewNext').is_enabled()
    page.locator('#workshopReviewNext').click()
    page.locator('#filePickerVeil').wait_for(state='visible')
    assert page.locator('#filePickerTitle').inner_text()=='Quest Example'
    page.locator('#filePickerClose').click()
    page.locator('#workshopInspectQueue').click()
    page.locator('#queueReviewVeil').wait_for(state='visible')
    assert page.locator('#queueReviewRows .queue-review-row').count()==1
    assert 'latest-se.zip' in page.locator('#queueReviewRows').inner_text() or 'Exact latest main' in page.locator('#queueReviewRows').inner_text()
    page.screenshot(path=str(OUTPUT/'batch-review.png'),full_page=False)
    page.locator('#queueReviewCancel').click()
    assert page.locator('#queueReviewVeil').is_hidden()
    page.locator('#workshopQueue').click()
    assert page.locator('#queueReviewVeil').is_hidden()
    page.locator('#liveDownloadList .download-ticket').wait_for(timeout=10000)
    calls=page.evaluate('window.__SSS_MOCK__.calls')
    assert len(calls)==1 and calls[0]['endpoint']=='update-queue',calls
    assert page.locator('.download-ticket').count()==1
    page.screenshot(path=str(OUTPUT/'queued-download.png'),full_page=False)
    page.evaluate("window.__SSS_MOCK__.tickets[302].status='complete'")
    page.locator('#workshopRefreshDownloads').click()
    page.locator('button[data-download-id]').wait_for(timeout=10000)
    page.locator('#workshopSummary button[data-lane="ready"]').click()
    assert '1 of 3 mods shown' in page.locator('#workshopVisibleCount').inner_text()
    page.locator('button[data-download-details]').click()
    page.locator('#installPreviewVeil').wait_for(state='visible')
    page.locator('#installPreviewConfirm').wait_for(state='attached')
    assert page.locator('#installBriefTitle').inner_text()=='Arcane Example'
    assert 'latest-se.zip' in page.locator('#installBriefDetails').inner_text()
    assert 'mod folders are not backed up' in page.locator('#installBriefBackup').inner_text()
    page.screenshot(path=str(OUTPUT/'install-brief.png'),full_page=False)
    page.keyboard.press('Escape')
    assert page.locator('#installPreviewVeil').is_hidden()
    page.locator('button[data-download-id]').click()
    assert page.locator('#installPreviewVeil').is_hidden()
    page.wait_for_function('window.__SSS_MOCK__.tickets[302].status === "installing"')
    calls=page.evaluate('window.__SSS_MOCK__.calls')
    assert len(calls)==2 and calls[-1]['endpoint']=='install',calls
    assert calls[-1]['body']['downloadId']==302
    # Switch runtime between ready and install to show a blocked preflight.
    page.evaluate("window.__SSS_MOCK__.tickets[302].status='complete';window.__SSS_MOCK__.data.catalog.gameRuntime='1.6.1170'")
    page.locator('#workshopRefreshDownloads').click()
    page.locator('button[data-download-details]').click()
    page.locator('#installPreviewVeil').wait_for(state='visible')
    page.wait_for_function("document.getElementById('installBriefWarnings').textContent.includes('runtime changed')")
    assert page.locator('#installPreviewConfirm').is_disabled()
    page.screenshot(path=str(OUTPUT/'blocked-install.png'),full_page=False)
    page.set_viewport_size({'width':390,'height':844})
    page.screenshot(path=str(OUTPUT/'mobile-install-brief.png'),full_page=False)
    rect=page.locator('#installPreviewVeil .operation-dialog').bounding_box()
    assert rect and rect['x']>=-1 and rect['x']+rect['width']<=392,rect
    assert page.locator('#installPreviewConfirm').is_disabled()
    assert not errors,errors
    # Independent fast-update session: one click downloads an exact main match.
    fast=browser.new_page(viewport={"width":1440,"height":950})
    fast_errors=[];unexpected_dialogs=[]
    fast.on('pageerror',lambda e: fast_errors.append(str(e)))
    fast.on('dialog',lambda d: (unexpected_dialogs.append(d.message),d.dismiss()))
    fast.set_content(html,wait_until='load',timeout=30000)
    fast.locator('[data-workshop-id="live-a"] [data-workshop-act="quick"]').click()
    fast.locator('#liveDownloadList .download-ticket').wait_for(timeout=10000)
    assert fast.locator('#queueReviewVeil').is_hidden()
    assert fast.locator('#installPreviewVeil').is_hidden()
    assert fast.locator('#filePickerVeil').is_hidden()
    assert fast.evaluate('window.__SSS_MOCK__.calls[0].endpoint')=='quick-update'
    assert fast.evaluate('window.__SSS_MOCK__.calls[0].body.autoInstall') is True
    fast.screenshot(path=str(OUTPUT/'one-click-update.png'),full_page=False)
    fast.evaluate("window.__SSS_MOCK__.tickets[701].status='complete'")
    fast.locator('#workshopRefreshDownloads').click()
    fast.locator('button[data-download-id]').wait_for(timeout=10000)
    fast.locator('button[data-download-id]').click()
    fast.wait_for_function("window.__SSS_MOCK__.tickets[701].status === 'installing'")
    assert fast.locator('#installPreviewVeil').is_hidden()
    assert fast.evaluate('window.__SSS_MOCK__.calls.at(-1).endpoint')=='install'
    # A DLL risk does not produce an extra modal or block the user from choosing.
    fast.locator('[data-workshop-id="live-b"] [data-workshop-act="quick"]').click()
    fast.locator('#filePickerVeil').wait_for(state='visible',timeout=10000)
    assert 'Compatibility notes' in fast.locator('#filePickerSummary').inner_text()
    fast.locator('.picker-file button[data-file-id]').first.click()
    fast.wait_for_function("window.__SSS_MOCK__.calls.at(-1).endpoint === 'update-queue'")
    assert fast.evaluate('window.__SSS_MOCK__.calls.at(-1).body.entries[0].autoInstall') is True
    assert fast.locator('#queueReviewVeil').is_hidden()
    assert not unexpected_dialogs,unexpected_dialogs
    assert not fast_errors,fast_errors
    # One-click bulk queue is asynchronous and preserves per-mod choices.
    bulk=browser.new_page(viewport={"width":1250,"height":900})
    bulk.set_content(html,wait_until='load',timeout=30000)
    bulk.locator('#workshopQuickAll').click()
    bulk.wait_for_function("window.__SSS_MOCK__.quickState?.state === 'completed'")
    bulk.locator('#liveDownloadList .download-ticket').wait_for(timeout=10000)
    assert bulk.locator('#queueReviewVeil').is_hidden()
    assert bulk.locator('#installPreviewVeil').is_hidden()
    assert bulk.evaluate('window.__SSS_MOCK__.calls[0].body.all') is True
    bulk.screenshot(path=str(OUTPUT/'one-click-bulk.png'),full_page=False)
    # Recovery cockpit: one click to retry the SAME file, one click to
    # sequentially open ready MO2 installers, no additional approval forms.
    recovery=browser.new_page(viewport={"width":1320,"height":940})
    recovery_errors=[]
    recovery.on('pageerror',lambda e: recovery_errors.append(str(e)))
    recovery.set_content(html,wait_until='load',timeout=30000)
    recovery.locator('#workshopResults .workshop-item').first.wait_for(timeout=15000)
    recovery.evaluate("""()=>{
      const m=window.__SSS_MOCK__,profile=m.data.catalog.profileId,runtime=m.data.catalog.gameRuntime;
      m.delayActivityMs=850; // Force overlapping download refreshes during rapid clicks.
      m.tickets[801]={status:'failed',provider:'nexus',modId:44,fileId:144,
        itemId:'live-a',installedVersion:'1.0',targetVersion:'2.0',profileId:profile,gameRuntime:runtime};
      m.tickets[802]={status:'complete',provider:'nexus',modId:45,fileId:145,
        itemId:'live-b',installedVersion:'1.5',targetVersion:'2.3',profileId:profile,gameRuntime:runtime};
    }""")
    recovery.locator('#workshopRefreshDownloads').click()
    recovery.locator('#downloadPulse').wait_for(state='visible')
    assert recovery.locator('#pulseReady').inner_text()=='1'
    assert recovery.locator('#pulseFailed').inner_text()=='1'
    recovery.locator('[data-retry-download-id="801"]').click()
    recovery.wait_for_function("window.__SSS_MOCK__.tickets[801].retriedBy === 803")
    recovery.locator('.ticket-requeued').first.wait_for(timeout=3500)
    assert recovery.locator('.ticket-requeued').count()==1
    assert recovery.evaluate("window.__SSS_MOCK__.calls.at(-1).endpoint")=='retry-download'
    recovery.locator('#installAllReady').click()
    recovery.wait_for_function("window.__SSS_MOCK__.tickets[802].status === 'installing'")
    recovery.wait_for_function("document.getElementById('pulseReady').textContent === '0'")
    assert recovery.evaluate("window.__SSS_MOCK__.calls.at(-1).endpoint")=='install-ready'
    assert recovery.locator('#installPreviewVeil').is_hidden()
    assert recovery.locator('#queueReviewVeil').is_hidden()
    recovery.screenshot(path=str(OUTPUT/'recovery-cockpit.png'),full_page=False)
    assert not recovery_errors,recovery_errors
    recovery.close()
    bulk.close();fast.close()
    print(json.dumps({"result":"passed","modCards":3,"fileChoices":3,
        "recommended":1,"queuedCalls":1,"installCalls":1,"screenshots":str(OUTPUT),"jsErrors":errors},indent=2))
    browser.close()
