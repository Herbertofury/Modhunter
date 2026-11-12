"""Launch real Chromium, exercise offline checklist, finishers, live profile and mobile."""
from __future__ import annotations
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
HTML=Path('/mnt/data/MO2_Update_Arsenal.html').read_text(encoding='utf8')
OUT=Path('/mnt/data/mo2-update-arsenal/screenshots');OUT.mkdir(exist_ok=True)
LIVE={"profileId":"test-prof-001","catalogId":"mo2-live-test-prof-001","profileName":"Stormcloak Run","game":"skyrimspecialedition","gameRuntime":"1.5.97","groups":[{"id":"grp-base","name":"BASE + DLL","startPriority":5,"color":"mint","count":2},{"id":"grp-spells","name":"SPELLS & QUESTS","startPriority":10,"color":"violet","count":1}],"items":[{"id":"live-1","name":"SKSE Example","nexusId":27,"groupId":"grp-base","group":"BASE + DLL","priority":6,"screenshotNote":"Hold on 1.5.97","installedVersion":"1.0","latestKnownVersion":"1.5","source":{"verified":True,"url":"https://www.nexusmods.com/skyrimspecialedition/mods/27?tab=files","label":"Nexus","type":"nexus"},"audit":{"verdict":"hold","note":"Select the SE DLL","evidence":""},"flags":["DLL"],"author":"Tester","update":{"verdict":"candidate","latestVersion":"1.5"}},{"id":"live-2","name":"Auto Output","nexusId":0,"groupId":"grp-base","group":"BASE + DLL","priority":7,"installedVersion":"?","source":{"verified":False,"url":"","type":"unknown"},"flags":[],"audit":{},"screenshotNote":"","author":""},{"id":"live-3","name":"Magic Tests","nexusId":88,"groupId":"grp-spells","group":"SPELLS & QUESTS","priority":11,"installedVersion":"2.0","source":{"verified":True,"url":"https://www.nexusmods.com/skyrimspecialedition/mods/88?tab=files","type":"nexus","label":"Nexus"},"flags":[],"audit":{},"screenshotNote":"","author":""}]}
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox','--disable-gpu','--disable-dev-shm-usage'])
 context=browser.new_context(viewport={'width':1490,'height':900},reduced_motion='no-preference',accept_downloads=True)
 page=context.new_page(); errs=[]
 page.on('pageerror',lambda e: errs.append(str(e)))
 page.set_content(HTML,wait_until='load',timeout=25000)
 print('initial',page.evaluate('window.__ARSENAL_QA__?.getSnapshot()'))
 page.screenshot(path=str(OUT/'offline-desktop.png'),full_page=False)
 assert page.locator('#modList .mod-row').count()==227
 page.locator('#search').fill('author:Ersh');assert page.locator('#resultsMeta').inner_text();page.locator('#clearSearch').click()
 page.locator('#effectSelect').select_option('slash');page.locator('#modList .complete-btn').first.click()
 assert page.locator('#impactLayer .combat-composition.slash').count()>=1
 for effect in ['shatter','burn','arrow','frost','thunder','shot','fireworks']:
  page.evaluate('(effect)=>window.__ARSENAL_QA__.finisher(effect,400,200)',effect)
  if effect in ['slash','shatter','burn','arrow','frost','thunder']:assert page.locator('#impactLayer .combat-composition.'+effect).count()>=1
 print('finishers passed')
 page.evaluate('(data)=>window.__ARSENAL_QA__.setCatalog(data)',LIVE)
 assert page.locator('#modList .mod-row').count()==3
 assert page.locator('#liveProfile').inner_text().startswith('Stormcloak Run')
 assert page.locator('#filePickerVeil').is_hidden()
 page.locator('#search').fill('SKSE');assert page.locator('#modList .mod-row').count()==1
 page.locator('#clearSearch').click()
 page.screenshot(path=str(OUT/'live-desktop.png'),full_page=False)
 print('live',page.evaluate('window.__ARSENAL_QA__.getSnapshot()'))
 mobile=context.new_page();mobile.set_viewport_size({'width':390,'height':844});mobile.on('pageerror',lambda e: errs.append(str(e)));mobile.set_content(HTML,wait_until='load');mobile.evaluate('(d)=>window.__ARSENAL_QA__.setCatalog(d)',LIVE)
 mobile.screenshot(path=str(OUT/'live-mobile.png'),full_page=False)
 print('mobile overflow',mobile.evaluate('({scroll:document.documentElement.scrollWidth,client:document.documentElement.clientWidth})'))
 assert mobile.locator('#modList .mod-row').count()==3
 print('ERRORS',errs)
 assert not errs, errs
 browser.close()
