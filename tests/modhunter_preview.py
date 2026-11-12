"""Render only genuine Chromium app frames as an annotated animated preview."""
from pathlib import Path
import ast
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
from playwright.sync_api import sync_playwright

root=Path(__file__).resolve().parents[1]
source=(root/'tests/modhunter_lore_qa.py').read_text(encoding='utf8')
namespace={'__file__':str(root/'tests/modhunter_lore_qa.py')}
exec(source.split('with sync_playwright() as p:')[0],namespace)
html=namespace['HTML'];boot=namespace['JS_TEST']
frames=[]
with sync_playwright() as pw:
 browser=pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox','--disable-dev-shm-usage','--disable-gpu'])
 context=browser.new_context(viewport={'width':1400,'height':840},device_scale_factor=1)
 page=context.new_page();page.goto('about:blank?realm=1&siege=1')
 page.set_content(html.replace('<head>','<head>'+boot,1),wait_until='load')
 page.wait_for_function('window.__SIEGE_QA__ && document.querySelectorAll("#siegeBestiaryMenu button").length===3')
 def grab():
  img=Image.open(BytesIO(page.screenshot(type='png'))).convert('RGB')
  img.thumbnail((1000,600),Image.Resampling.LANCZOS)
  frames.append(img)
 page.locator('#siegeBestiaryMenu').scroll_into_view_if_needed();grab()
 page.locator('#siegeStart').click();page.evaluate('window.__SIEGE_QA__.spawnBoss()')
 for i in range(10):
  page.evaluate('window.__SIEGE_QA__.tick(.03,18)')
  grab()
 page.evaluate('window.__SIEGE_QA__.hitBoss(100000)');page.wait_for_timeout(160);grab()
 assert len(frames)==12
 browser.close()
out=Path('/mnt/data/MO2_Update_Arsenal_ModHunter_v0.7_Gameplay.gif')
frames[0].save(out,save_all=True,append_images=frames[1:],duration=[1000]+[250]*10+[1250],loop=0,optimize=True)
print('Created',out,'bytes',out.stat().st_size,'frames',len(frames))
