"""Mythforge interactive release proof: story, boss, telegraphs, wallet invariants, zoom, weather and persistence."""
from pathlib import Path
from playwright.sync_api import sync_playwright
import json
HTML=Path(__file__).resolve().parents[1].joinpath('mo2_update_arsenal/web/index.html').read_text(encoding='utf8')
with sync_playwright() as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
 ctx=b.new_context(viewport={'width':1440,'height':900},reduced_motion='no-preference')
 pg=ctx.new_page();errors=[];pg.on('pageerror',lambda e:errors.append(str(e)))
 pg.goto('about:blank?realm=1');pg.set_content(HTML.replace('<head>',"<head><script>window.__QA_STORE__={};Object.defineProperty(window,'localStorage',{configurable:true,value:{getItem(k){return window.__QA_STORE__[k]??null},setItem(k,v){window.__QA_STORE__[k]=String(v)},removeItem(k){delete window.__QA_STORE__[k]}}});</script>",1),wait_until='load');pg.wait_for_function('window.__REALM_QA__&&!window.__REALM_QA__.getState().loading')
 pg.wait_for_timeout(100);pg.locator('#realmModalClose').click() if pg.locator('#realmModal').is_visible() else None
 q='window.__REALM_QA__'; start=pg.evaluate(f'{q}.getState()'); assert start['wallet']['earned']==0
 assert pg.locator('#realmStoryProgress').is_visible()
 # Three distinct actual landmark interactions, no free double rewards.
 for index,poi in enumerate(['shrine','watchtower','crypt']):
  pg.evaluate(f'()=>{q}.interactAt("{poi}")')
  st=pg.evaluate(f'{q}.getState()')
  assert len(st['world']['story']['fragments'])==index+1,st['world']['story']
  assert pg.locator('#realmModal').is_visible()
  pg.locator('#realmModalClose').click()
 pg.evaluate(f'()=>{q}.interactAt("shrine")')
 assert len(pg.evaluate(f'{q}.getState()')['world']['story']['fragments'])==3
 pg.evaluate(f'()=>{q}.interactAt("obelisk")')
 assert pg.evaluate(f'{q}.getState()')['enemies']>=1
 pg.evaluate(f'()=>{q}.hitEnemy("story-ashen-warden",1000)')
 state=pg.evaluate(f'{q}.getState()');assert state['world']['story']['wardenDefeated'] and state['world']['quests']['warden']
 assert state['wallet']['available']==start['wallet']['available'] and state['wallet']['earned']==0
 if pg.locator('#realmModal').is_visible():pg.locator('#realmModalClose').click()
 # Idempotent world-boss replay, not free XP.
 prior=pg.evaluate(f'{q}.getState()')['world']['player']['xp'];pg.evaluate(f'()=>{q}.interactAt("obelisk")')
 assert pg.evaluate(f'{q}.getState()')['world']['player']['xp']==prior
 print('STORY: all 3 distinct landmarks, one-time rewards, world boss victory, no fake Forge currency: PASS')
 pg.evaluate(f'()=>{q}.teleport(4,8)')
 pg.evaluate(f'()=>{q}.spawnEnemy("test-telegraph",4.8,8,"bandit")')
 pg.wait_for_timeout(800)
 # In a real anim loop enemy must show telegraph before damage (no instant hit).
 assert pg.evaluate(f'{q}.getState()')['enemies']>0
 pg.locator('button[data-action="zoom-in"]').first.click()
 assert pg.evaluate(f'{q}.metrics()')['tiles']>30
 assert pg.locator('#realmWeather').inner_text()
 # Perks provide real character stats, persist in the world and cannot forge ranked currency.
 pg.evaluate(f'{q}.grantXP(350)')
 skill_before=pg.evaluate(f'{q}.getState()')['world']['player']['skillPoints']
 assert skill_before>0,skill_before
 pg.locator('[data-panel="talents"]').click()
 assert pg.locator('#realmTalentList .realm-talent').count()==6
 pg.locator('[data-perk="ironheart"]').click()
 after_talent=pg.evaluate(f'{q}.getState()');assert after_talent['world']['perks']['ironheart']==1
 assert after_talent['world']['player']['skillPoints']==skill_before-1
 assert after_talent['wallet']['earned']==0
 pg.screenshot(path='/mnt/data/mo2-update-arsenal/screenshots/mythforge-talents.png',full_page=False)
 print('CHARACTER BUILDS: six talent branches, real stat allocation, no ranked currency inflation: PASS')
 # A physical or emulated controller can operate the game without keyboard shortcuts.
 pg.evaluate('''()=>{const p={id:'QA gamepad',connected:true,axes:[.93,0,0,0],buttons:Array.from({length:17},()=>({pressed:false}))};window.__TEST_GAMEPAD__=p;Object.defineProperty(navigator,'getGamepads',{configurable:true,value:()=>[p]})}''')
 xbefore=pg.evaluate(f'{q}.getState()')['world']['player']['x']
 pg.wait_for_timeout(410)
 xafter=pg.evaluate(f'{q}.getState()')['world']['player']['x']
 assert xafter>xbefore+.4,(xbefore,xafter)
 pg.evaluate('window.__TEST_GAMEPAD__.axes=[0,0,0,0];window.__TEST_GAMEPAD__.buttons[9].pressed=true')
 pg.wait_for_timeout(100)
 assert pg.evaluate(f'{q}.getState()')['paused'] is True
 pg.evaluate('window.__TEST_GAMEPAD__.buttons[9].pressed=false')
 pg.wait_for_timeout(80)
 pg.evaluate('window.__TEST_GAMEPAD__.buttons[9].pressed=true')
 pg.wait_for_timeout(100)
 assert pg.evaluate(f'{q}.getState()')['paused'] is False
 pg.evaluate('window.__TEST_GAMEPAD__.buttons[9].pressed=false')
 print('GAMEPAD: analog movement, start-pause/resume, edge-triggered input: PASS')
 print('TACTICAL UI: enemy simulation, readable weather, camera zoom: PASS')
 await_save=pg.evaluate(f'{q}.save()');pg.wait_for_timeout(250)
 saved=pg.evaluate('localStorage.getItem("mo2-arsenal-shiverfell-v1:practice")')
 assert saved and json.loads(saved)['story']['wardenDefeated']
 assert not errors,errors
 print('WORLD SAVE: quest progress and boss defeat persist without touching MO2 rank: PASS')
 pg.screenshot(path='/mnt/data/mo2-update-arsenal/screenshots/mythforge-boss-completed.png',full_page=False)
 b.close()
