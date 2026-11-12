from pathlib import Path
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from functools import partial
from threading import Thread
from playwright.sync_api import sync_playwright
ROOT=Path('/mnt/data')
class Quiet(SimpleHTTPRequestHandler):
 def log_message(self,*args):pass
server=ThreadingHTTPServer(('127.0.0.1',0),partial(Quiet,directory=str(ROOT)))
t=Thread(target=server.serve_forever,daemon=True);t.start()
url=f'http://127.0.0.1:{server.server_port}/MO2_Update_Arsenal.html'
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox','--disable-dev-shm-usage'])
 ctx=browser.new_context(viewport={'width':1500,'height':980},reduced_motion='no-preference')
 page=ctx.new_page();err=[];logs=[];page.on('pageerror',lambda e:err.append(str(e)));page.on('console',lambda m:logs.append(m.text) if m.type=='error' else None)
 page.goto('about:blank?realm=1');page.set_content((ROOT/'MO2_Update_Arsenal.html').read_text('utf-8'),wait_until='load');page.wait_for_timeout(2600)
 print('state',page.evaluate('window.__REALM_QA__&&window.__REALM_QA__.getState()'))
 print('title',page.title(),'realmVisible',page.locator('#realmRoot').is_visible(),'canvasSize',page.locator('#realmCanvas').evaluate('(x)=>[x.width,x.height]'))
 print('errors',err,'console',logs[:15]);page.screenshot(path=str(ROOT/'realm-smoke.png'),full_page=True)
 page.locator('#realmModalClose').click() if page.locator('#realmModal').is_visible() else None
 page.locator('button[data-panel="forge"]').click()
 print('shop balance',page.locator('#realmShopBalance').inner_text());page.screenshot(path=str(ROOT/'realm-shop-smoke.png'),full_page=True)
 browser.close()
server.shutdown();server.server_close()
