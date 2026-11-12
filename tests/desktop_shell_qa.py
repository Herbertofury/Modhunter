"""Chromium UI smoke for the Electron chrome (without claiming native Electron proof)."""
from pathlib import Path
from playwright.sync_api import sync_playwright
p=Path(__file__).resolve().parents[1]/'desktop/chrome.html'
FAKE='''window.__ACTIONS__=[];window.modhunterShell={onState(f){window.__HANDLER__=f;return ()=>{}},async action(kind,value){window.__ACTIONS__.push({kind,value});if(kind==='state')return{ok:true,state:{tabs:[{id:1,title:'✦ Mod Vault',mode:'dashboard',url:'file:///test.html',loading:false}],activeId:1,connected:false}};return{ok:true}}}'''
with sync_playwright() as play:
 b=play.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox','--disable-dev-shm-usage','--disable-gpu'])
 pg=b.new_page(viewport={'width':1420,'height':480});pg.add_init_script(FAKE)
 errors=[];pg.on('pageerror',lambda e:errors.append(str(e)))
 html=p.read_text('utf8').replace('<link rel="stylesheet" href="chrome.css">','<style>'+ (p.parent/'chrome.css').read_text('utf8') +'</style>').replace('<script src="chrome.js"></script>','<script>'+(p.parent/'chrome.js').read_text('utf8')+'</script>')
 pg.goto('about:blank');pg.set_content(html,wait_until='load');pg.wait_for_function('document.querySelectorAll(".tab").length===1')
 assert pg.locator('#status').count()==1
 pg.locator('#newSiege').click();pg.locator('#newRealm').click();pg.locator('#newDash').click()
 pg.locator('#endpoint').fill('http://127.0.0.1:12345/');pg.locator('#connect').click()
 pg.locator('#disconnect').click()
 a=pg.evaluate('window.__ACTIONS__')
 assert [(x['kind'],x.get('value')) for x in a[1:]]==[('new','siege'),('new','realm'),('new','dashboard'),('connect','http://127.0.0.1:12345/'),('disconnect',None)],a
 assert not errors,errors
 pg.screenshot(path=str(p.parents[1]/'screenshots/desktop-tabbar.png'))
 print('DESKTOP CHROME: 3 content tab actions, local MO2 link, unlink, active tab render PASS (Electron runtime separately unverified)')
 b.close()
