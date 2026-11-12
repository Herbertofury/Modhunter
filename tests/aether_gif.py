"""Capture real Chromium frames of the production UI, then encode a simple preview GIF."""
from pathlib import Path
from io import BytesIO
from playwright.sync_api import sync_playwright
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
HTML=Path('/mnt/data/MO2_Update_Arsenal.html').read_text('utf8')
OUT=Path('/mnt/data/MO2_Update_Arsenal_AetherFX_Glass_Preview.gif')
frames=[]
with sync_playwright() as p:
 b=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox','--disable-gpu','--disable-dev-shm-usage'])
 page=b.new_page(viewport={'width':1440,'height':940},reduced_motion='no-preference')
 page.set_content(HTML,wait_until='load')
 page.locator('.mod-row').first.scroll_into_view_if_needed()
 page.locator('#effectSelect').select_option('shatter')
 page.wait_for_timeout(80)
 for _ in range(3):
  img=Image.open(BytesIO(page.screenshot())).convert('RGB').crop((293,388,1408,765));frames.append(img.resize((895,303)))
 page.locator('.mod-row').first.locator('.complete-btn').click()
 for i in range(18):
  page.wait_for_timeout(58)
  img=Image.open(BytesIO(page.screenshot())).convert('RGB').crop((293,388,1408,765))
  frames.append(img.resize((895,303)))
 b.close()
frames[0].save(OUT,save_all=True,append_images=frames[1:],duration=[100]*3+[80]*18,loop=0,optimize=True)
print(OUT,OUT.stat().st_size,'bytes',len(frames),'frames')
