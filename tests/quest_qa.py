"""Real Chromium gamification + fake scoped bridge, no live MO2 claimed."""
from __future__ import annotations
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path('/mnt/data/mo2-update-arsenal')
HTML=Path('/mnt/data/MO2_Update_Arsenal.html').read_text('utf-8')
SHOT=ROOT/'screenshots';SHOT.mkdir(parents=True,exist_ok=True)
CAT={
 'profileId':'quest-profile','profileName':'Aela - Legendary','game':'skyrimspecialedition','gameRuntime':'1.5.97','catalogId':'quest-live-1',
 'groups':[{'id':'runic','name':'RUNES & ROOTS','startPriority':1,'count':3,'color':'violet'}, {'id':'spell','name':'MAGIC & DRAGONS','startPriority':10,'count':1,'color':'gold'}],
 'items':[
 {'id':'live-aa','mo2InternalName':'Native Dragon','name':'Dragonsteel Runes','groupId':'runic','group':'RUNES & ROOTS','installedVersion':'1.0','latestKnownVersion':'2.0','nexusId':77,'priority':2,'flags':['DLL'],'author':'Forge','update':{'verdict':'candidate','latestVersion':'2.0'},'screenshotNote':'1.5.97 SE build','source':{'type':'nexus','verified':True,'url':'https://www.nexusmods.com/skyrimspecialedition/mods/77?tab=files'},'audit':{'verdict':'hold','note':'Verify exact runtime'}},
 {'id':'live-bb','mo2InternalName':'Frost','name':'Frostbound Depths','groupId':'runic','group':'RUNES & ROOTS','installedVersion':'3.0','latestKnownVersion':'3.0','nexusId':88,'priority':3,'flags':[],'author':'Mer','update':{'verdict':'current','latestVersion':'3.0'},'screenshotNote':'','source':{'type':'nexus','verified':True,'url':'https://www.nexusmods.com/skyrimspecialedition/mods/88?tab=files'},'audit':{}},
 {'id':'live-cc','mo2InternalName':'Output','name':'Nemesis Output','groupId':'runic','group':'RUNES & ROOTS','installedVersion':'?','nexusId':0,'priority':4,'flags':[],'author':'','screenshotNote':'Generated','source':{},'audit':{}},
 {'id':'live-dd','mo2InternalName':'Sky Magics','name':'Skybound Magic','groupId':'spell','group':'MAGIC & DRAGONS','installedVersion':'1.0','nexusId':0,'priority':11,'flags':[],'author':'Creator','screenshotNote':'','source':{},'audit':{}},
 ]}
GAME={'ok':True,'profileId':'quest-profile','score':540,'level':2,'bestCombo':3,'updates':3,'achievements':[{'id':'first-blood','name':'First Blood','glyph':'⚔','target':1,'unlocked':True,'description':'First install'},{'id':'apprentice','name':'Apprentice Smith','glyph':'🔨','target':5,'unlocked':False,'description':'Five'}],'feed':[{'sequence':3,'name':'Frostbound Depths','xp':140,'evidence':'version-observed'}],'victories':0,'leaderboard':[{'name':'Aela - Legendary','score':540,'victories':0}],'unseenVictory':None}
boot=r'''<script>
window.__MO2_BRIDGE_BOOT__={active:true,token:'qa-token'};
window.__qaData={catalog:/*CAT*/,game:/*GAME*/,progress:{}};window.__qaCalls=[];
window.fetch=async (url,options={})=>{
 const path=String(url).replace('/api/','');const method=options.method||'GET',body=options.body?JSON.parse(options.body):null;
 window.__qaCalls.push({path,method,body});
 let result={ok:true};
 if(method==='GET'){
  if(path==='snapshot')result={ok:true,catalog:window.__qaData.catalog,generation:4};
  else if(path==='game')result=window.__qaData.game;
  else if(path==='progress')result={ok:true,profileId:window.__qaData.catalog.profileId,progress:window.__qaData.progress};
  else if(path==='scan')result={ok:true,scan:{state:'idle'}};
  else if(path==='downloads')result={ok:true,downloads:{}};
  else if(path==='revision')result={ok:true,generation:4,profileId:window.__qaData.catalog.profileId,selectedModId:window.__qaSelected||''};
 }else if(method==='POST'){
  if(path==='focus'){window.__qaSelected=body.id;result={ok:true,message:'Revealed exact MO2 mod'}}
  if(path==='progress')window.__qaData.progress={rows:body.rows,prefs:body.prefs,verifiedRuntime:body.verifiedRuntime};
  if(path==='victory/ack'){window.__qaData.game.unseenVictory=null;result={ok:true}}
  if(path==='scan')result={ok:true,state:'running'};
 }
 return {ok:true,json:async()=>result,status:200}
};
</script>'''
boot=boot.replace('/*CAT*/',json.dumps(CAT,ensure_ascii=False)).replace('/*GAME*/',json.dumps(GAME,ensure_ascii=False))
HTML=HTML.replace('<!--__MO2_BOOT__-->',boot)

with sync_playwright() as p:
 browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox','--disable-gpu','--disable-dev-shm-usage'])
 context=browser.new_context(viewport={'width':1510,'height':980},reduced_motion='no-preference')
 page=context.new_page();errs=[];page.on('pageerror',lambda e:errs.append(str(e)))
 page.set_content(HTML,wait_until='load');page.wait_for_function('window.__ARSENAL_QA__?.getCatalog()?.profileId === "quest-profile"')
 page.wait_for_function('window.__ARSENAL_QA__?.game()?.score === 540')
 assert page.locator('.mod-title.locate-title').count()==4
 assert page.locator('#questScore').inner_text().startswith('540')
 assert page.locator('.achievement.unlocked').count()==1
 assert page.locator('.quest-leaderboard-line').count()==1
 page.locator('.mod-title.locate-title').first.click()
 assert page.locator('.mod-row.native-selected').count()==1
 assert page.evaluate('window.__qaCalls.filter(x=>x.path === "focus").at(-1).body.id')=='live-aa'
 print('MO2 exact focus, profile rank, achievements and high scores: OK')
 page.locator('.complete-btn').first.click();page.wait_for_timeout(420)
 assert page.evaluate('window.__qaData.progress.rows["live-aa"].status')=='done'
 assert page.evaluate('window.__ARSENAL_QA__.game().score')==540
 print('Manual status sync retains verified score: OK')
 page.evaluate("window.__qaData.game.unseenVictory={id:'quest-profile:3',verifiedMods:2,updates:3,bonus:2050}")
 page.evaluate('window.__ARSENAL_QA__.refreshGame()')
 page.wait_for_function('!document.getElementById("victoryOverlay").hidden')
 assert 'VERIFIED FULL CLEAR' in page.locator('#victoryTier').inner_text()
 assert page.locator('.victory-shard').count()>=145
 page.wait_for_timeout(2900)
 page.screenshot(path=str(SHOT/'victory-desktop.png'),full_page=False)
 page.locator('#victoryClaim').click();page.wait_for_function('document.getElementById("victoryOverlay").hidden')
 assert page.evaluate('window.__qaCalls.some(x=>x.path === "victory/ack")')
 print('Verified boss finale, 150 shattering shards, and acknowledgment: OK')
 page.locator('#previewVictory').click();assert page.locator('#victoryOverlay').is_visible();assert 'PRACTICE' in page.locator('#victoryTier').inner_text();page.keyboard.press('Escape');assert page.locator('#victoryOverlay').is_hidden()
 # Prevent false native-victory rank when profile switched and app rerendered.
 switched=json.loads(json.dumps(CAT));switched['profileId']='quest-profile-2';switched['profileName']='Second Journey';switched['items'][0]['installedVersion']='3.0';
 page.evaluate('(c)=>{window.__qaData.catalog=c; window.__qaData.progress={};window.__qaData.game={...window.__qaData.game,profileId:c.profileId,score:0,updates:0,feed:[],leaderboard:[],unseenVictory:null}}',switched)
 page.evaluate('(c)=>window.__ARSENAL_QA__.setCatalog(c)',switched)
 page.wait_for_function('window.__ARSENAL_QA__.getCatalog().profileId === "quest-profile-2"')
 page.wait_for_function('window.__ARSENAL_QA__.game()?.profileId === "quest-profile-2"')
 assert page.locator('#questScore').inner_text().startswith('0')
 assert page.locator('#modList .mod-row.done').count()==0
 page.screenshot(path=str(SHOT/'quest-desktop.png'),full_page=False)
 print('Cross-profile progress and rank isolation: OK')
 mobile=context.new_page();mobile.set_viewport_size({'width':390,'height':844});mobile.on('pageerror',lambda e:errs.append(str(e)));mobile.set_content(HTML,wait_until='load');mobile.wait_for_function('window.__ARSENAL_QA__?.game()?.score === 540');mobile.screenshot(path=str(SHOT/'quest-mobile.png'),full_page=False)
 assert mobile.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth+2')
 assert mobile.locator('#achievementGrid .achievement').count()>=2
 print('Mobile layout within viewport: OK')
 assert not errs,errs
 print('Browser errors:',errs)
 browser.close()
