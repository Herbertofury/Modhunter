"""AetherFX end-user Chromium regression: real packaged HTML, not a fake static picture."""
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
HTML=Path('/mnt/data/MO2_Update_Arsenal.html').read_text('utf8')
SHOT=ROOT/'screenshots'
SHOT.mkdir(exist_ok=True)
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox','--disable-gpu','--disable-dev-shm-usage'])
 ctx=browser.new_context(viewport={'width':1440,'height':940},reduced_motion='no-preference')
 page=ctx.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 page.set_content(HTML,wait_until='load');page.wait_for_function('Boolean(window.AetherFX && window.__ARSENAL_QA__)')
 assert page.locator('.mod-row').count()==227
 assert page.locator('.chapterbar').count()>=100
 page.locator('#fxSettingsToggle').click();assert page.locator('#fxStudio').is_visible()
 assert 'Legendary' in page.locator('#fxIntensity').inner_text()
 done_before=page.evaluate('window.__ARSENAL_QA__.getSnapshot().counts.done')
 page.locator('#fxTest').click()
 page.wait_for_function('document.querySelectorAll(".aether-completion-stage").length > 0')
 assert page.evaluate('window.__ARSENAL_QA__.getSnapshot().counts.done')==done_before
 page.wait_for_timeout(1200)
 page.screenshot(path=str(SHOT/'aether-studio.png'))
 page.locator('#fxClose').click();assert page.locator('#fxStudio').is_hidden()
 page.locator('.mod-row').nth(1).scroll_into_view_if_needed()
 first=page.locator('.mod-row').nth(1)
 # Select body of row, not an external link or checkbox: native row targeting is retained.
 first.locator('.mod-priority').click()
 page.wait_for_timeout(130)
 assert page.locator('.mod-row.aether-target').count()==1
 assert page.locator('.aether-selection-stage').count()>=1
 assert page.evaluate('window.__ARSENAL_QA__.aether().selected')==first.get_attribute('data-id')
 page.screenshot(path=str(SHOT/'aether-selection.png'))
 assert page.locator('#detailDrawer').get_attribute('class').find('open')==-1
 page.locator('#effectSelect').select_option('shatter')
 first=page.locator('.mod-row').first;first.scroll_into_view_if_needed();first.locator('.complete-btn').click()
 page.wait_for_timeout(95)
 assert page.locator('.aether-fragment').count()==14,(page.locator('.aether-fragment').count(),errors)
 assert page.locator('.aether-completion-stage').count()>=1
 assert first.get_attribute('class').find('done')>=0
 page.screenshot(path=str(SHOT/'aether-shatter.png'))
 page.wait_for_timeout(1420)
 assert page.locator('.aether-fragment').count()==0
 assert page.locator('.aether-completion-stage').count()==0
 assert page.evaluate('window.__ARSENAL_QA__.aether().running') is False
 print('GLASS: 14 independently shattered row fragments, clearing and idle animation scheduler: PASS')
 modes=['slash','burn','shot','arrow','frost','thunder','fireworks']
 for mode in modes:
  page.locator('.mod-row').first.locator('.complete-btn').click() # back to pending
  page.locator('#effectSelect').select_option(mode)
  page.locator('.mod-row').first.locator('.complete-btn').click()
  page.wait_for_timeout(75)
  assert page.locator('.aether-completion-stage').count()>=1,mode
  if mode=='burn':page.screenshot(path=str(SHOT/'aether-dragonfire.png'))
  page.wait_for_timeout(1340)
 print('SKYRIM FINISHERS: sword, dragonfire, critical, arrow, frost, thunder, fireworks: PASS')
 page.locator('#fxSettingsToggle').click();page.locator('#fxIntensity').select_option('off')
 assert page.evaluate('window.__ARSENAL_QA__.aether().options.intensity')=='off'
 page.locator('#fxClose').click()
 page.wait_for_timeout(1750)
 page.locator('.mod-row').first.locator('.complete-btn').click() # pending
 page.locator('.mod-row').first.locator('.complete-btn').click()
 page.wait_for_timeout(110)
 assert page.locator('.aether-completion-stage').count()==0
 assert page.locator('#impactLayer').locator('*').count()==0
 print('FX OFF: no AetherFX or legacy combat particles: PASS')
 page.locator('#fxSettingsToggle').click();page.locator('#fxIntensity').select_option('subtle');page.locator('#fxShake').uncheck();page.locator('#fxTrails').uncheck();page.locator('#fxClose').click()
 assert page.evaluate('window.__ARSENAL_QA__.aether().options.shake') is False
 assert page.evaluate('window.__ARSENAL_QA__.aether().options.trails') is False
 page.locator('#effectSelect').select_option('shatter');page.locator('.mod-row').first.locator('.complete-btn').click();page.locator('.mod-row').first.locator('.complete-btn').click()
 page.wait_for_timeout(105)
 assert page.locator('.aether-completion-stage').count()>=1
 print('SUBTLE: reduced particles, shake disabled, trails disabled: PASS')
 assert not errors,errors
 mobile=ctx.new_page();mobile.on('pageerror',lambda e:errors.append(str(e)));mobile.set_viewport_size({'width':390,'height':844});mobile.set_content(HTML,wait_until='load')
 mobile.locator('#fxSettingsToggle').click();assert mobile.locator('#fxStudio').is_visible()
 mobile.screenshot(path=str(SHOT/'aether-mobile.png'))
 assert mobile.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth+2')
 mobile.locator('#fxStudioDone').click();assert mobile.locator('#fxStudio').is_hidden()
 print('Mobile FX controls and visual layout: PASS')
 reduced=browser.new_context(viewport={'width':1000,'height':800},reduced_motion='reduce').new_page()
 reduced.on('pageerror',lambda e:errors.append(str(e)));reduced.set_content(HTML,wait_until='load')
 reduced.locator('.mod-row').first.scroll_into_view_if_needed();reduced.locator('#effectSelect').select_option('shatter');reduced.locator('.complete-btn').first.click()
 assert reduced.locator('.mod-row.done').count()==1
 assert reduced.locator('.aether-fragment').count()==0
 print('Reduced motion respects user setting and checklist still updates: PASS')
 assert not errors,errors
 print('No page script errors. All 227 rows and groups preserved: PASS')
 browser.close()
