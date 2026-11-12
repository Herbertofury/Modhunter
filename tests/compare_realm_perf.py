"""Equivalent Chromium frame work baseline vs current (headless, no caps)."""
from pathlib import Path
from playwright.sync_api import sync_playwright
import json
BASE=Path('/mnt/data/MO2_Update_Arsenal_Shiverfell_v0.5.html')
HEAD=Path('/mnt/data/MO2_Update_Arsenal.html')
with sync_playwright() as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
 ctx=b.new_context(viewport={'width':1500,'height':980},reduced_motion='reduce')
 out={}
 for title,src in [('v0.5',BASE),('mythforge',HEAD)]:
  page=ctx.new_page();page.goto('about:blank?realm=1');page.set_content(src.read_text('utf-8'),wait_until='load');page.wait_for_function('window.__REALM_QA__&&!window.__REALM_QA__.getState().loading')
  bench=page.evaluate('''()=>{const qa=window.__REALM_QA__,r=[];for(let i=0;i<180;i++){const t=performance.now();qa.render();r.push(performance.now()-t)}r.sort((a,b)=>a-b);return {p50:r[90],p90:r[162],p95:r[171],mean:r.reduce((a,b)=>a+b,0)/180,tiles:qa.metrics?.()?.tiles??null,worldVersion:qa.getState().world.v}}''')
  out[title]=bench;page.close()
 print(json.dumps(out,indent=2))
 b.close()
