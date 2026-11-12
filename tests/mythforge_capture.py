"""Capture original live Canvas gameplay from the actual bundled HTML; no generated imagery."""
from io import BytesIO
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright
PATH=Path('/mnt/data/MO2_Update_Arsenal.html')
OUT=Path('/mnt/data/MO2_Update_Arsenal_Mythforge_Gameplay_v0.6.gif')
with sync_playwright() as p:
    b=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox','--disable-dev-shm-usage','--disable-gpu'])
    page=b.new_page(viewport={'width':1400,'height':900},device_scale_factor=1)
    errs=[];page.on('pageerror',lambda e:errs.append(str(e)))
    page.goto('about:blank?realm=1')
    page.set_content(PATH.read_text('utf8'),wait_until='load')
    page.wait_for_function('window.__REALM_QA__&&!window.__REALM_QA__.getState().loading')
    page.wait_for_timeout(1000)
    if page.locator('#realmModal').is_visible():page.locator('#realmModalClose').click()
    canvas=page.locator('#realmCanvas');canvas.focus()
    frames=[]
    # Controlled world-coordinate camera traversal, rendered by the real procedural engine.
    positions=[]
    for i in range(16):
        page.evaluate('(t)=>window.__REALM_QA__.teleport(2.0+t*.63, 4.0+t*.42)',i)
        if i==11:page.keyboard.press('f')
        positions.append(page.evaluate('Math.round(window.__REALM_QA__.getState().world.player.y*10)/10'))
        shot=canvas.screenshot(type='png')
        im=Image.open(BytesIO(shot)).convert('RGB')
        im.thumbnail((760,480),Image.Resampling.LANCZOS)
        frames.append(im.quantize(colors=128,method=Image.Quantize.FASTOCTREE))
        page.wait_for_timeout(230)
    assert not errs,errs
    frames[0].save(OUT,format='GIF',save_all=True,append_images=frames[1:],duration=125,loop=0,optimize=True,disposal=2)
    from PIL import Image as CheckImage
    print('CAPTURED',len(frames),'real Chromium frames',OUT,OUT.stat().st_size,'bytes','encoded',CheckImage.open(OUT).n_frames,'world-cells',positions)
    b.close()
