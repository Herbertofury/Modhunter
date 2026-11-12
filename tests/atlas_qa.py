"""Chromium acceptance: 96 authored quests, 480 progression slots, real controls, persistence."""
from pathlib import Path
from playwright.sync_api import sync_playwright
import json
ROOT=Path(__file__).resolve().parents[1]
HTML=(ROOT/'mo2_update_arsenal/web/index.html').read_text(encoding='utf8')
QALOCAL='''<script>window.__SIEGE_TEST__=true;window.__QA_STORE__=window.__QA_STORE__||{};Object.defineProperty(window,'localStorage',{configurable:true,value:{getItem(k){return window.__QA_STORE__[k]||null},setItem(k,v){window.__QA_STORE__[k]=String(v)},removeItem(k){delete window.__QA_STORE__[k]}}});</script>'''
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox','--disable-dev-shm-usage','--disable-gpu'])
    page=browser.new_page(viewport={'width':1440,'height':960});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto('about:blank?realm=1&siege=1');page.set_content(HTML.replace('<head>','<head>'+QALOCAL,1),wait_until='load')
    page.wait_for_function('window.__SIEGE_QA__ && document.querySelectorAll(".atlas-region").length===12')
    d=page.evaluate('window.__SIEGE_QA__.atlas()')
    assert len(d['regions'])==12 and len(d['patterns'])==8 and len(d['sagas'])==12 and len(d['tiers'])==5
    assert len(set(n for row in d['sagas'] for n in row))==96
    assert page.locator('#siegeAtlasProgress').inner_text().startswith('0 / 480')
    assert page.locator('.atlas-region:disabled').count()==11
    assert page.locator('.atlas-contract:disabled').count()==7
    page.screenshot(path=str(ROOT/'screenshots/atlas-map.png'))
    page.locator('#siegeAtlasStart').click()
    page.wait_for_function('window.__SIEGE_QA__.state().run?.atlas?.chapter===\'The Last Bell of Winter\'')
    run=page.evaluate('window.__SIEGE_QA__.state().run')
    assert run['atlas']['duration']==420 and run['atlas']['region']==0 and run['atlas']['contract']==0
    page.evaluate('window.__SIEGE_QA__.grantAtlasClear()')
    page.evaluate('window.__SIEGE_QA__.grantAtlasClear()')
    a=page.evaluate('window.__SIEGE_QA__.atlas().meta')
    assert a['essence']==1 and len(a['clears'])==1
    page.locator('#siegeBack').click()
    page.evaluate('window.__SIEGE_ENGINE__.open()')
    page.wait_for_function('document.querySelectorAll(".atlas-contract:disabled").length===6')
    assert '1 / 480' in page.locator('#siegeAtlasProgress').inner_text()
    page.locator('[data-atlas-legacy="vigor"]').click()
    assert page.evaluate('window.__SIEGE_QA__.atlas().meta.legacy.vigor')==1
    assert page.evaluate('window.__SIEGE_QA__.atlas().meta.spent')==1
    page.locator('[data-atlas-contract="1"]').click()
    a=page.evaluate('window.__SIEGE_QA__.atlas().meta.selected')
    assert a['contract']==1
    page.wait_for_timeout(100)
    saved=page.evaluate('window.__QA_STORE__')
    assert saved and any('siege' in k for k in saved)
    # Reload with identical saved data: progress, perk and selection must survive.
    seed='<script>window.__QA_STORE__='+json.dumps(saved)+';</script>'
    page.goto('about:blank?realm=1&siege=1');page.set_content(HTML.replace('<head>','<head>'+seed+QALOCAL,1),wait_until='load')
    page.wait_for_function('window.__SIEGE_QA__ && document.querySelectorAll(".atlas-region").length===12')
    again=page.evaluate('window.__SIEGE_QA__.atlas().meta')
    assert len(again['clears'])==1 and again['legacy']['vigor']==1 and again['selected']['contract']==1,again
    assert not errors,errors
    page.screenshot(path=str(ROOT/'screenshots/atlas-campaign.png'))
    print('ATLAS CHROMIUM: 12 unique regions, 96 authored missions, 5 tiers, unlock, first-clear dedupe, legacy purchase, reload PASS')
    browser.close()
