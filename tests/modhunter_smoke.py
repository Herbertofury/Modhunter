from pathlib import Path
from playwright.sync_api import sync_playwright
HTML=(Path(__file__).parents[1]/'mo2_update_arsenal/web/index.html').read_text()
with sync_playwright() as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
 page=b.new_page(viewport={'width':1440,'height':900}); errors=[];page.on('pageerror',lambda ex:errors.append(str(ex)))
 page.goto('about:blank?realm=1&siege=1')
 page.set_content(HTML.replace('<head>',"<head><script>window.__SIEGE_TEST__=true;window.__TEST_STORE__={};Object.defineProperty(window,'localStorage',{configurable:true,value:{getItem(k){return window.__TEST_STORE__[k]||null},setItem(k,v){window.__TEST_STORE__[k]=v},removeItem(k){delete window.__TEST_STORE__[k]}}})</script>",1),wait_until='load')
 page.wait_for_timeout(1200)
 print('errors',errors)
 print('siege menu',page.locator('#siegeRoot').is_visible(),page.locator('#siegeMenu').is_visible(),page.locator('#siegeStart').is_visible())
 print('controls',page.locator('.siege-class').count(),page.locator('#siegeTargetSummary').inner_text())
 page.screenshot(path='/mnt/data/modhunter-menu.png',full_page=False)
 page.locator('#siegeStart').click()
 page.wait_for_timeout(1000)
 print('game visible',page.locator('#siegeGame').is_visible(),page.evaluate('window.__SIEGE_QA__?.state().run?.elapsed'))
 page.screenshot(path='/mnt/data/modhunter-game.png',full_page=False)
 print('errors',errors)
 b.close()
