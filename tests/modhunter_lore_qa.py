"""Playwright proof: metadata names, correct archetypes, exact-mod buttons, mobile layout."""
from pathlib import Path
from playwright.sync_api import sync_playwright
import json

ROOT=Path(__file__).resolve().parents[1]
HTML=(ROOT/'mo2_update_arsenal/web/index.html').read_text(encoding='utf8')
OUT=ROOT/'screenshots'
OUT.mkdir(exist_ok=True)
FIXTURES=[
 {'id':'live-weapon','name':'Electron Maul - SSE','installedVersion':'1','latestVersion':'2','nexusId':11,'game':'skyrimspecialedition',
  'lore':{'kind':'weapon','subject':'Electron Maul','glyph':'⚔','color':'#edca90','role':'WIELDER','foes':['Electron Maul Reaver','Rustbound Electron Maul Duelist'], 'bossTitle':'The Electron Maul Blade Tyrant','provenance':'title-inference','sourceMod':'Electron Maul - SSE'}},
 {'id':'live-armor','name':'Iron Rose Armor 2k Complex Material','installedVersion':'1','latestVersion':'2','nexusId':12,'game':'skyrimspecialedition',
  'lore':{'kind':'armor','subject':'Iron Rose Armor','glyph':'⛨','color':'#e3b8a1','role':'IRONCLAD','foes':['Dented Iron Rose Armor Sentinel','Iron Rose Armor Clanker'], 'bossTitle':'The Ironclad Iron Rose Armor Colossus','provenance':'title-inference','sourceMod':'Iron Rose Armor 2k Complex Material'}},
 {'id':'live-npc','name':'Celuna - Dark Elf Follower','installedVersion':'1','latestVersion':'2','nexusId':13,'game':'skyrimspecialedition',
  'lore':{'kind':'npc','subject':'Celuna','glyph':'♟','color':'#d7a7ef','role':'ECHO','foes':['Echo of Celuna',"Celuna's Lost Doppelgänger"], 'bossTitle':'Celuna, the Unfinished','provenance':'content-metadata','sourceMod':'Celuna - Dark Elf Follower'}},
]
JS_TEST='''<script>window.__SIEGE_TEST__=true;window.__MO2_BRIDGE_BOOT__={active:true,token:'qa-token'};window.__SIEGE_FAKE__={calls:[],targets:__TARGETS__,rev:0,save:null};window.fetch=async(url,opt={})=>{const f=window.__SIEGE_FAKE__,path=String(url).replace('/api/',''),body=opt.body?JSON.parse(opt.body):{};f.calls.push({path,body});let data={ok:true};if(path==='realm')data={ok:true,profileId:'qa-profile',available:20};if(path==='siege/targets')data={ok:true,profileId:'qa-profile',targets:f.targets};if(path==='siege/bounties')data={ok:true,bounties:[]};if(path==='siege/state'&&(!opt.method||opt.method==='GET'))data={ok:true,profileId:'qa-profile',revision:f.rev,state:f.save};if(path==='siege/state'&&opt.method==='POST'){f.rev++;f.save=body.state;data={ok:true,revision:f.rev}};if(path==='focus')data={ok:true,selected:body.id};if(path==='siege/boss/defeated')data={ok:true,awarded:false};return{ok:true,json:async()=>data}};</script>'''.replace('__TARGETS__',json.dumps(FIXTURES))

with sync_playwright() as p:
 browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox','--disable-dev-shm-usage','--disable-gpu'])
 ctx=browser.new_context(viewport={'width':1512,'height':900});page=ctx.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 page.goto('about:blank?realm=1&siege=1')
 page.set_content(HTML.replace('<head>','<head>'+JS_TEST,1),wait_until='load')
 page.wait_for_function('window.__SIEGE_QA__ && document.querySelectorAll("#siegeBestiaryMenu .siege-bestiary-entry").length===3',timeout=15000)
 roster=page.locator('#siegeBountyRoster').inner_text();bestiary=page.locator('#siegeBestiary').inner_text()
 for expected in ['Electron Maul','Iron Rose Armor','Celuna']:
  assert expected in roster and expected in bestiary,(expected,roster,bestiary)
 assert 'title-inspired' in roster and 'named content' in roster
 page.screenshot(path=str(OUT/'modhunter-lore-bestiary.png'))
 page.locator('#siegeBestiaryMenu [data-siege-mod="live-armor"]').click()
 page.wait_for_function('window.__SIEGE_FAKE__.calls.some(c=>c.path==="focus")')
 assert page.evaluate('window.__SIEGE_FAKE__.calls.filter(c=>c.path==="focus").pop().body.id')=='live-armor'
 page.locator('#siegeStart').click()
 relative=page.evaluate('window.__SIEGE_QA__.placeFoe(1)')
 viewport=page.locator('#siegeCanvas').bounding_box()
 page.mouse.move(viewport['x']+relative['x'],viewport['y']+relative['y'])
 page.wait_for_function('!document.getElementById("siegeFoeInspect").hidden',timeout=2000)
 tooltip=page.locator('#siegeFoeInspect')
 assert 'Iron Rose Armor' in tooltip.inner_text()
 r=tooltip.bounding_box()
 assert r['x']>=viewport['x'] and r['y']>=viewport['y'] and r['x']+r['width']<=viewport['x']+viewport['width']+4
 page.mouse.move(viewport['x']+5,viewport['y']+5)
 page.evaluate('window.__SIEGE_QA__.tick(.02,90)')
 names=page.evaluate('window.__SIEGE_QA__.foes()')
 assert names and all(x['modId'] in ('live-weapon','live-armor','live-npc') for x in names),names
 assert all(x['name'] and x['source'] in ('title-inference','content-metadata') for x in names)
 for bossIndex,expected in enumerate(['Electron Maul Blade Tyrant','Ironclad Iron Rose Armor Colossus','Celuna, the Unfinished']):
  page.evaluate('window.__SIEGE_QA__.spawnBoss()')
  title=page.locator('#siegeBossName').inner_text()
  assert expected in title,(bossIndex,title)
  if bossIndex==2:
   page.screenshot(path=str(OUT/'modhunter-lore-npc-boss.png'))
  page.evaluate('window.__SIEGE_QA__.hitBoss(100000)')
  page.wait_for_timeout(80)
  assert page.locator('#siegeDialog').is_visible()
  page.locator('#siegeDialogActions button').last.click()
  # Starting a new boss is only eligible after removing the previous boss.
 assert page.evaluate('window.__SIEGE_QA__.state().run.score')>=300
 # New unfounded claims must not mutate the MO2 ledger from the game screen.
 assert not page.evaluate('window.__SIEGE_FAKE__.calls.some(c=>c.path==="realm/purchase")')
 assert not errors,errors
 print('LIVE LORE: exact weapon/armor/NPC names, all enemies themed, boss+bonus flow, exact MO2 focus PASS')
 ctx.close()
 ctx=browser.new_context(viewport={'width':420,'height':820});page=ctx.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 page.goto('about:blank?realm=1&siege=1');page.set_content(HTML.replace('<head>','<head>'+JS_TEST,1),wait_until='load');page.wait_for_function('document.querySelectorAll("#siegeBestiaryMenu .siege-bestiary-entry").length===3')
 assert page.evaluate('document.documentElement.scrollWidth<=document.documentElement.clientWidth+3')
 page.screenshot(path=str(OUT/'modhunter-lore-mobile.png'))
 assert not errors,errors
 print('LORE MOBILE: full bestiary, no sideways overflow PASS')
 ctx.close();browser.close()
