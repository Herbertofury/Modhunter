"""Chromium end-to-end Modhunter Siege checks: authentic UI, input, evolution, exact MO2 focus and save."""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / 'mo2_update_arsenal/web/index.html').read_text(encoding='utf8')
OUT = ROOT / 'screenshots'
OUT.mkdir(exist_ok=True)
START = '''<script>window.__SIEGE_TEST__=true;window.__QA_STORE__={};Object.defineProperty(window,'localStorage',{configurable:true,value:{getItem(k){return window.__QA_STORE__[k]||null},setItem(k,v){window.__QA_STORE__[k]=String(v)},removeItem(k){delete window.__QA_STORE__[k]}}});</script>'''
MOCK = '''<script>
window.__MO2_BRIDGE_BOOT__={active:true,token:'fixture-secret'};
window.__SIEGE_FAKE__={calls:[],state:null,revision:0,profile:'MO2-fixture'};
window.fetch=async(url,opt={})=>{const f=window.__SIEGE_FAKE__,path=String(url).replace('/api/',''),body=opt.body?JSON.parse(opt.body):{};f.calls.push({path,body,method:opt.method||'GET',token:opt.headers?.['X-Arsenal-Token']});let j={ok:true};
if(path==='realm')j={ok:true,profileId:f.profile,earned:20,spent:0,available:20,verifiedUpdates:2};
else if(path==='siege/targets')j={ok:true,profileId:f.profile,targets:[{id:'live-77',name:'Engine Fixes & Armaments',mo2InternalName:'EngineFixes',installedVersion:'1.0',latestVersion:'2.0',nexusId:77,game:'skyrimspecialedition'}]};
else if(path==='siege/bounties')j={ok:true,profileId:f.profile,bounties:[]};
else if(path==='siege/state'&&(!opt.method||opt.method==='GET'))j={ok:true,profileId:f.profile,state:f.state,revision:f.revision};
else if(path==='siege/state'){if(body.profileId!==f.profile||body.revision!==f.revision)j={ok:false,error:'revision or profile mismatch'};else {f.state=body.state;f.revision++;j={ok:true,profileId:f.profile,revision:f.revision}}}
else if(path==='siege/boss/defeated'){if(body.modId!=='live-77'||body.profileId!==f.profile)j={ok:false,error:'wrong target'};else j={ok:true,bounty:{status:'defeated'},awarded:false}}
else if(path==='focus'){if(body.id!=='live-77'||body.profileId!==f.profile)j={ok:false,error:'wrong focus'};else j={ok:true,selected:'EngineFixes'}};
return{ok:j.ok!==false,status:j.ok===false?409:200,json:async()=>j}
};</script>'''

def page_open(browser, live=False, viewport=(1500,940), seed=None):
    ctx=browser.new_context(viewport={'width':viewport[0],'height':viewport[1]}, reduced_motion='no-preference')
    pg=ctx.new_page(); errors=[];pg.on('pageerror',lambda e: errors.append(str(e)))
    pg.goto('about:blank?realm=1&siege=1')
    boot = START.replace('window.__QA_STORE__={}', 'window.__QA_STORE__='+json.dumps(seed or {})) + (MOCK if live else '')
    pg.set_content(HTML.replace('<head>','<head>'+boot,1),wait_until='load')
    pg.wait_for_function('window.__SIEGE_QA__ && document.getElementById("siegeMenu").offsetWidth>0',timeout=13000)
    return ctx,pg,errors

with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox','--disable-dev-shm-usage','--disable-gpu'])
    ctx,pg,err=page_open(browser)
    assert pg.locator('#siegeRoot').is_visible()
    assert pg.locator('.shell').count()==0, 'Siege game tab must not retain dashboard'
    assert pg.locator('.siege-class').count()==5
    assert pg.locator('#siegeConnection').inner_text().startswith('OFFLINE')
    assert 'currency' in pg.locator('#siegeIntelHelp').inner_text().lower()
    pg.screenshot(path=str(OUT/'modhunter-menu.png'))
    pg.locator('[data-hero="ranger"]').click();pg.locator('#siegeDifficulty').select_option('novice')
    pg.locator('#siegeStart').click()
    assert pg.locator('#siegeGame').is_visible()
    assert pg.evaluate('window.__SIEGE_QA__.state().run.hero')=='ranger'
    start=pg.evaluate('window.__SIEGE_QA__.state().run')
    pg.locator('#siegeCanvas').focus();pg.keyboard.down('d');pg.wait_for_timeout(480);pg.keyboard.up('d')
    moved=pg.evaluate('window.__SIEGE_QA__.state().run')
    assert moved['x']>start['x']+.3,(start['x'],moved['x'])
    pg.evaluate('window.__SIEGE_QA__.tick(.04,180)')
    battle=pg.evaluate('window.__SIEGE_QA__.state().run')
    assert battle['elapsed']>7 and battle['entities'],battle
    assert battle['projectiles'] or battle['kills'] or battle['squad'][0]['cd']>0
    pg.screenshot(path=str(OUT/'modhunter-combat.png'))
    pg.evaluate('window.__SIEGE_QA__.setXP(120)')
    assert pg.locator('#siegeDialog').is_visible() and 'CHOOSE AN ASCENSION' in pg.locator('#siegeDialogTitle').inner_text()
    assert pg.locator('.siege-choice').count()==3
    pg.locator('.siege-choice').first.click()
    assert len(pg.evaluate('Object.keys(window.__SIEGE_QA__.state().run.skills)'))==1
    pg.evaluate('window.__SIEGE_QA__.setXP(140)')
    assert pg.locator('#siegeDialogTitle').inner_text()=='RECRUIT YOUR SQUAD'
    pg.screenshot(path=str(OUT/'modhunter-levelup.png'))
    pg.locator('.siege-choice').first.click()
    assert len(pg.evaluate('window.__SIEGE_QA__.state().run.squad'))==2
    pg.evaluate('window.__SIEGE_QA__.spawnBoss()')
    assert 'Unbound Warden' in pg.locator('#siegeBossName').inner_text()
    pg.evaluate('window.__SIEGE_QA__.hitBoss(100000)')
    assert pg.locator('#siegeDialog').is_visible()
    assert 'PRACTICE' in pg.locator('#siegeDialogDesc').inner_text().upper()
    assert pg.evaluate('window.__SIEGE_QA__.state().run.score')>=100
    pg.locator('#siegeDialogActions button').first.click()
    pg.evaluate('window.__SIEGE_QA__.save()');pg.wait_for_timeout(200)
    saved=pg.evaluate('window.__QA_STORE__')
    pg.reload();pg.goto('about:blank?realm=1&siege=1');pg.set_content(HTML.replace('<head>','<head>'+START.replace('window.__QA_STORE__={}','window.__QA_STORE__='+json.dumps(saved)),1),wait_until='load')
    pg.wait_for_function('window.__SIEGE_QA__ && document.getElementById("siegeContinue").offsetWidth>0')
    pg.locator('#siegeContinue').click()
    assert pg.evaluate('window.__SIEGE_QA__.state().run.score')>=100
    assert not err,err
    print('OFFLINE: separate tab, movement, autoshoot, XP, evolution, recruit, boss, practice rewards, local restore PASS')
    ctx.close()

    ctx,pg,err=page_open(browser,True)
    assert pg.locator('#siegeConnection').inner_text().startswith('MO2 LIVE')
    assert 'Engine Fixes' in pg.locator('#siegeBountyRoster').inner_text() and 'title-inspired' in pg.locator('#siegeBountyRoster').inner_text()
    assert pg.evaluate('window.__SIEGE_QA__.network().targets[0].id')=='live-77'
    assert pg.evaluate('window.__SIEGE_QA__.loreFor({name:"Electron Maul - SSE"}).kind')=='weapon'
    assert pg.evaluate('window.__SIEGE_QA__.loreFor({name:"Celuna - Follower"}).kind')=='npc'
    pg.locator('#siegeStart').click()
    pg.evaluate('window.__SIEGE_QA__.tick(.02,60)')
    assert pg.evaluate('window.__SIEGE_QA__.foes().some(x=>x.modId==="live-77" && x.name.includes("Engine Fixes"))')
    pg.evaluate('window.__SIEGE_QA__.spawnBoss()')
    assert 'Engine Fixes & Armaments' in pg.locator('#siegeBossName').inner_text()
    assert pg.locator('#siegeBossSubtitle').inner_text().endswith('1.0 → 2.0')
    pg.evaluate('window.__SIEGE_QA__.hitBoss(100000)')
    pg.wait_for_function('window.__SIEGE_FAKE__.calls.some(x=>x.path==="siege/boss/defeated")')
    calls=pg.evaluate('window.__SIEGE_FAKE__.calls')
    boss=[c for c in calls if c['path']=='siege/boss/defeated'][-1]
    assert boss['body']['modId']=='live-77' and boss['body']['profileId']=='MO2-fixture'
    assert '200' in pg.locator('#siegeDialogDesc').inner_text()
    assert pg.locator('#siegeDialog').is_visible()
    pg.screenshot(path=str(OUT/'modhunter-mod-boss.png'))
    pg.locator('#siegeDialogActions [data-dialog-action="0"]').click()
    pg.wait_for_function('window.__SIEGE_FAKE__.calls.some(x=>x.path==="focus")')
    assert pg.evaluate('window.__SIEGE_FAKE__.calls.filter(x=>x.path==="focus").pop().body.id')=='live-77'
    assert pg.evaluate('window.__SIEGE_FAKE__.calls.some(x=>x.path==="realm/purchase")') is False
    assert pg.evaluate('window.__SIEGE_QA__.state().run.score')>=100
    assert not err,err
    print('LIVE BRIDGE: exact mod name, version, boss contract, native claim, one-click MO2 focus, no browser minting PASS')
    ctx.close()

    ctx,pg,err=page_open(browser,False,(430,850))
    assert pg.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth+3'),pg.evaluate('({sw:document.documentElement.scrollWidth,cw:document.documentElement.clientWidth})')
    pg.locator('#siegeStart').click()
    assert pg.locator('#siegeCanvas').is_visible()
    assert pg.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth+3')
    pg.screenshot(path=str(OUT/'modhunter-mobile.png'))
    assert not err,err
    print('MOBILE: menu, game, controls, no horizontal overflow PASS')
    ctx.close();browser.close()
