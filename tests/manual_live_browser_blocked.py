"""End-to-end Chromium → live loopback → MO2 mocked dispatcher; no real credentials."""
from pathlib import Path
import copy
import tempfile
import time
from playwright.sync_api import sync_playwright
from mo2_update_arsenal.bridge import ArsenalService
LIVE={'schemaVersion':2,'profileId':'fixture-p','catalogId':'mo2-live-fixture-p','profileName':'Actual MO2 Fixture','game':'skyrimspecialedition','gameRuntime':'1.5.97','groups':[{'id':'fixture-g','name':'Gameplay / Combat','startPriority':1,'color':'mint','count':2}],'items':[{'id':'fixture-1','name':'Dragonborn DLL','priority':2,'nexusId':77,'groupId':'fixture-g','group':'Gameplay / Combat','installedVersion':'1.0','latestKnownVersion':'2.0','flags':['DLL'],'audit':{'verdict':'check','note':'Select old SE DLL'},'source':{'verified':True,'url':'https://www.nexusmods.com/skyrimspecialedition/mods/77?tab=files','type':'nexus','label':'Nexus'},'screenshotNote':'','author':'Author1'},{'id':'fixture-2','name':'Spells and Sparks','priority':3,'nexusId':0,'groupId':'fixture-g','group':'Gameplay / Combat','installedVersion':'1.0','flags':[],'audit':{},'source':{'verified':False,'url':'','type':'unknown'},'screenshotNote':'','author':'Author2'}]}
html=Path('/mnt/data/MO2_Update_Arsenal.html').read_text(encoding='utf8')
with tempfile.TemporaryDirectory() as tmp:
 def ui(req):
  if req.cmd=='refresh':req.finish({'ok':True,'catalog':copy.deepcopy(LIVE)})
  else:req.finish({'ok':True,'files':['modlist.txt'],'message':'Native command accepted','downloadId':123})
 service=ArsenalService(html,Path(tmp)/'cache.json',ui,copy.deepcopy(LIVE))
 url=service.start();print('testing',url)
 try:
  with sync_playwright() as p:
   browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox','--disable-dev-shm-usage'])
   page=browser.new_page(viewport={'width':1440,'height':880});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
   try:
    page.goto(url,timeout=15000,wait_until='domcontentloaded')
    page.wait_for_function("document.getElementById('livePill').textContent.includes('CONNECTED')",timeout=12000)
    assert page.locator('#liveProfile').inner_text().startswith('Actual MO2 Fixture')
    assert page.locator('#modList .mod-row').count()==2
    page.locator('#liveLocal').click();page.wait_for_function("document.getElementById('liveUpdateCount').textContent.includes('1 update')",timeout=11000)
    assert 'ϟ UPDATE' in page.locator('#modList').inner_text()
    print('LIVE BROWSER PASS',page.locator('#liveState').inner_text())
   except Exception as e:
    print('LIVE BROWSER UNAVAILABLE',type(e).__name__,str(e)[:300]);raise
   finally:
    print('PAGE SCRIPT ERRORS',errors);browser.close()
 finally:service.stop()
