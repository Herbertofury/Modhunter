from pathlib import Path
from playwright.sync_api import sync_playwright
from statistics import median
html=(Path(__file__).resolve().parents[1]/'mo2_update_arsenal/web/index.html').read_text()
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1440,'height':900},reduced_motion='reduce')
    page.goto('about:blank?realm=1');page.set_content(html,wait_until='load')
    page.wait_for_function('window.__REALM_QA__&&!window.__REALM_QA__.getState().loading')
    result=page.evaluate('''()=>{const q=window.__REALM_QA__;const r=[];for(let i=0;i<32;i++){let t=performance.now();q.render();r.push(performance.now()-t)}return {mean:r.reduce((a,b)=>a+b,0)/r.length,p50:r.sort((a,b)=>a-b)[16],first:r[0],frames:r.length}}''')
    print('FRAME_BENCH', result)
    browser.close()
