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
                "source": {"type": "nexus", "verified": True,
                           "url": "https://www.nexusmods.com/skyrimspecialedition/mods/45"},
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
mock={"catalog":catalog,"plan":plan_updates(catalog),"review":review}
bootstrap = r"""
<script>
window.__MO2_BRIDGE_BOOT__={active:true,token:'browser-qa'};
window.__SSS_MOCK__={data:__DATA__,tickets:{},calls:[]};
window.fetch=async(url,opts={})=>{
 const x=window.__SSS_MOCK__, endpoint=String(url).slice(5);
 let result={ok:true};
 if(endpoint==='snapshot')result={ok:true,catalog:x.data.catalog,generation:1};
 else if(endpoint==='update-plan')result=x.data.plan;
 else if(endpoint.startsWith('files?'))result=x.data.review[String(new URLSearchParams(endpoint.split('?')[1]).get('modId'))]||{ok:false,error:'Missing mod'};
 else if(endpoint==='downloads')result={ok:true,downloads:x.tickets};
 else if(endpoint==='revision')result={ok:true,generation:1,profileId:x.data.catalog.profileId};
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
    page.locator('#workshopQueue').click()
    page.locator('#queueReviewVeil').wait_for(state='visible')
    assert page.locator('#queueReviewRows .queue-review-row').count()==1
    assert 'latest-se.zip' in page.locator('#queueReviewRows').inner_text() or 'Exact latest main' in page.locator('#queueReviewRows').inner_text()
    page.screenshot(path=str(OUTPUT/'batch-review.png'),full_page=False)
    page.locator('#queueReviewCancel').click()
    assert page.locator('#queueReviewVeil').is_hidden()
    page.locator('#workshopQueue').click()
    page.locator('#queueReviewConfirm').click()
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
    page.locator('button[data-download-id]').click()
    page.locator('#installPreviewVeil').wait_for(state='visible')
    page.locator('#installPreviewConfirm').wait_for(state='attached')
    assert page.locator('#installBriefTitle').inner_text()=='Arcane Example'
    assert 'latest-se.zip' in page.locator('#installBriefDetails').inner_text()
    assert 'mod folders are not backed up' in page.locator('#installBriefBackup').inner_text()
    page.screenshot(path=str(OUTPUT/'install-brief.png'),full_page=False)
    page.keyboard.press('Escape')
    assert page.locator('#installPreviewVeil').is_hidden()
    page.locator('button[data-download-id]').click()
    page.locator('#installPreviewConfirm').click()
    page.wait_for_function('window.__SSS_MOCK__.tickets[302].status === "installing"')
    calls=page.evaluate('window.__SSS_MOCK__.calls')
    assert len(calls)==2 and calls[-1]['endpoint']=='install',calls
    assert calls[-1]['body']['downloadId']==302
    # Switch runtime between ready and install to show a blocked preflight.
    page.evaluate("window.__SSS_MOCK__.tickets[302].status='complete';window.__SSS_MOCK__.data.catalog.gameRuntime='1.6.1170'")
    page.locator('#workshopRefreshDownloads').click()
    page.locator('button[data-download-id]').click()
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
    print(json.dumps({"result":"passed","modCards":3,"fileChoices":3,
        "recommended":1,"queuedCalls":1,"installCalls":1,"screenshots":str(OUTPUT),"jsErrors":errors},indent=2))
    browser.close()
