/* Original procedural sound palette. No downloaded samples or tracking. */
(()=>{'use strict';
const KEY='modhunter-audio-v1', voices=new Set(), recent=new Map();
let prefs={enabled:false,volume:.35}, ctx=null, master=null, unlocked=false;
try{const saved=JSON.parse(localStorage.getItem(KEY)||'null');if(saved){prefs.enabled=saved.enabled===true;prefs.volume=Number.isFinite(saved.volume)?Math.max(0,Math.min(1,saved.volume)):.35}}catch{}
// Soft, short envelopes: low impact, glass reward, warm major-chord triumph.
const palette={select:[[440,0,.09],[660,.025,.10]],hit:[[110,0,.09]],sword:[[180,0,.12]],spell:[[523,0,.18],[784,.04,.16]],loot:[[659,0,.16],[988,.07,.22]],success:[[392,0,.25],[494,.07,.27],[587,.14,.35]],victory:[[262,0,.35],[330,.10,.4],[392,.20,.45],[523,.32,.6]],warning:[[220,0,.22],[196,.14,.25]],dash:[[330,0,.12],[660,.04,.10]]};
function stop(){for(const v of voices){try{v.stop()}catch{}}voices.clear()}
function settings(){return {...prefs}}
function configure(next){if(typeof next.enabled==='boolean')prefs.enabled=next.enabled;if(Number.isFinite(next.volume))prefs.volume=Math.max(0,Math.min(1,next.volume));if(!prefs.enabled||!prefs.volume)stop();if(master)master.gain.setValueAtTime(prefs.enabled?prefs.volume:0,ctx.currentTime);try{localStorage.setItem(KEY,JSON.stringify(prefs))}catch{};window.dispatchEvent(new CustomEvent('modhunter-audio-settings',{detail:settings()}));return settings()}
function unlock(event){if(!event.isTrusted)return;unlocked=true;if(!prefs.enabled)return;try{const C=window.AudioContext||window.webkitAudioContext;if(!C)return;if(!ctx||ctx.state==='closed'){ctx=new C();recent.clear();master=ctx.createGain();master.gain.value=prefs.volume;master.connect(ctx.destination)}if(ctx.state==='suspended')Promise.resolve(ctx.resume()).catch(()=>{})}catch{ctx=null;master=null}}
function play(kind='select'){
 if(!prefs.enabled||!prefs.volume||!unlocked||document.hidden||!ctx||ctx.state!=='running')return false;
 const notes=palette[kind]||palette.select, now=ctx.currentTime;
 if(now-(recent.get(kind)??-Infinity)<(kind==='hit'?.15:.09)||voices.size+notes.length>12)return false;
 recent.set(kind,now);
 try{for(const [hz,delay,duration] of notes){const o=ctx.createOscillator(),g=ctx.createGain(),at=now+delay;o.type='sine';o.frequency.setValueAtTime(hz,at);o.frequency.exponentialRampToValueAtTime(hz*(kind==='hit'?.55:1),at+duration);g.gain.setValueAtTime(.0001,at);g.gain.exponentialRampToValueAtTime(.075/notes.length,at+.012);g.gain.exponentialRampToValueAtTime(.0001,at+duration);o.connect(g);g.connect(master);voices.add(o);o.onended=()=>{voices.delete(o);o.disconnect();g.disconnect()};o.start(at);o.stop(at+duration+.01)}return true}catch{stop();return false}
}
window.ModhunterAudio={play,configure,settings};
window.addEventListener('pointerdown',unlock,true);window.addEventListener('keydown',unlock,true);
document.addEventListener('visibilitychange',()=>{if(document.hidden)stop()});window.addEventListener('pagehide',()=>{stop();if(ctx)Promise.resolve(ctx.close()).catch(()=>{})});
// One keyboard-accessible control shared by dashboard and all game modes.
const panel=document.createElement('details');panel.id='modhunterAudioControls';panel.style.cssText='position:fixed;right:12px;bottom:12px;z-index:100100;background:#08090b;color:#eee;border:1px solid #555;border-radius:10px;padding:8px;font:13px system-ui;box-shadow:0 4px 20px #0008';
panel.innerHTML='<summary>Audio feedback</summary><label style="display:block;padding:8px 0"><input type="checkbox" data-audio-enabled> Enable sounds</label><label>Volume <input type="range" data-audio-volume min="0" max="100" step="1" aria-label="Audio feedback volume"></label><button type="button" data-audio-preview>Preview</button><small style="display:block;max-width:240px">Optional sound cues. All information remains available visually.</small>';
document.body.append(panel);const enabled=panel.querySelector('[data-audio-enabled]'),volume=panel.querySelector('[data-audio-volume]');
function sync(){enabled.checked=prefs.enabled;volume.value=Math.round(prefs.volume*100)}sync();window.addEventListener('modhunter-audio-settings',sync);
enabled.addEventListener('change',event=>{configure({enabled:enabled.checked});unlock(event)});volume.addEventListener('input',()=>configure({volume:Number(volume.value)/100}));panel.querySelector('[data-audio-preview]').addEventListener('click',()=>play('success'));
})();
