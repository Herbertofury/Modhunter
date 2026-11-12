/* AetherFX / Questbound v0.4: all effects are original vector/DOM compositions.
   Decorative only: never changes update state, MO2 selection, ranked XP or downloads. */
(()=>{
 'use strict';
 if(window.__ARSENAL_REALM_ROUTE__)return;
 const MODES=['shatter','slash','burn','shot','arrow','frost','thunder','fireworks'];
 const PALETTE={select:['#b2f9e7','#72d7d7','#ffe6a3'],shatter:['#f2fbff','#70d3ee','#e8c68f'],slash:['#ffffff','#87e9e8','#ffdc93'],burn:['#ffefb2','#ff9d4d','#ef4645'],shot:['#fff4d0','#ffbc65','#fcae8b'],arrow:['#ffe0a0','#d7fef5','#8cdcf0'],frost:['#f0feff','#a0f1ff','#4995ea'],thunder:['#fffdde','#c9cbff','#85a7ff'],fireworks:['#ffe5a0','#ed7d8d','#79e0d7'],undo:['#9ca9e8','#eecfa2','#7de6db']};
 const easing=(x)=>1-Math.pow(1-x,3),rnd=(a,b)=>a+Math.random()*(b-a),clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
 const reduced=()=>window.matchMedia('(prefers-reduced-motion: reduce)').matches;
 const container=document.createElement('div');container.id='aetherStage';container.setAttribute('aria-hidden','true');document.body.append(container);
 const surface=document.createElement('canvas');surface.id='aetherCanvas';surface.setAttribute('aria-hidden','true');document.body.append(surface);
 const ctx=surface.getContext('2d',{alpha:true});
 let dpr=1,w=0,h=0,particles=[],beams=[],rings=[],frame=0,lastFrame=0,selected='',lastSelect=0,hovered='',lastHover=0;
 let options={intensity:'legendary',shake:true,trails:true,sound:false};
 let lastCheck=0,chain=0,audio=null;
 const quality=()=>options.intensity==='off'?0:options.intensity==='subtle'?.25:options.intensity==='vivid'?.65:1;
 function syncCanvas(){const ratio=clamp(window.devicePixelRatio||1,1,2),iw=window.innerWidth,ih=window.innerHeight;
  if(iw===w&&ih===h&&ratio===dpr)return;w=iw;h=ih;dpr=ratio;surface.width=Math.round(iw*ratio);surface.height=Math.round(ih*ratio);ctx.setTransform(ratio,0,0,ratio,0,0);}
 window.addEventListener('resize',syncCanvas,{passive:true});syncCanvas();
 const point=(rect)=>({x:rect.left+rect.width*.5,y:rect.top+rect.height*.5});
 function validRect(rect){return rect&&rect.width>2&&rect.height>2&&rect.bottom>0&&rect.top<h;}
 function play(kind){if(!options.sound||reduced())return;try{const Ctx=window.AudioContext||window.webkitAudioContext;if(!Ctx)return;
  if(!audio||audio.state==='closed')audio=new Ctx();if(audio.state==='suspended')audio.resume().catch(()=>{});
  const now=audio.currentTime,osc=audio.createOscillator(),amp=audio.createGain();osc.type=kind==='select'?'sine':kind==='burn'?'sawtooth':'triangle';
  const start=kind==='select'?380:kind==='undo'?260:kind==='slash'?740:kind==='shot'?170:600;
  osc.frequency.setValueAtTime(start,now);osc.frequency.exponentialRampToValueAtTime(kind==='select'?680:kind==='shot'?45:125,now+.22);
  amp.gain.setValueAtTime(.0001,now);amp.gain.exponentialRampToValueAtTime(.035,now+.012);amp.gain.exponentialRampToValueAtTime(.0001,now+.25);osc.connect(amp);amp.connect(audio.destination);osc.start(now);osc.stop(now+.27);
 }catch(e){console.debug('AetherFX audio unavailable:',e)}}
 function burst(x,y,mode,count,spread=2){const p=PALETTE[mode]||PALETTE.select;
  for(let i=0;i<count;i++){const t=rnd(0,Math.PI*2),s=rnd(.7,4.2)*spread,ttl=rnd(.48,1.2);
   particles.push({x,y,ox:x,oy:y,vx:Math.cos(t)*s,vy:Math.sin(t)*s-rnd(0,2),gravity:mode==='burn'?-.065:rnd(.05,.16),
    size:rnd(1.2,mode==='shatter'?9:4.2),angle:rnd(-Math.PI,Math.PI),spin:rnd(-.21,.21),color:p[i%p.length],age:0,ttl,
    type:mode==='shatter'?'glass':mode==='frost'?'ice':mode==='burn'?'ember':mode==='slash'?'blade':i%9===0?'rune':'spark'});
  }
  start();
 }
 function ring(x,y,mode,radius=50){rings.push({x,y,r:radius,age:0,ttl:mode==='select'?.62:1.06,mode});start();}
 function beam(x1,y1,x2,y2,mode){beams.push({x1,y1,x2,y2,mode,age:0,ttl:.46});start();}
 function start(){if(!frame){lastFrame=performance.now();frame=requestAnimationFrame(tick)}}
 function tick(now){const dt=Math.min(.045,Math.max(.001,(now-lastFrame)/1000));lastFrame=now;ctx.clearRect(0,0,w,h);
  for(const b of beams){b.age+=dt;const p=clamp(b.age/b.ttl,0,1),c=PALETTE[b.mode]||PALETTE.select;
   ctx.save();ctx.globalAlpha=(1-p)*.9;ctx.strokeStyle=c[0];ctx.shadowBlur=18;ctx.shadowColor=c[1];ctx.lineWidth=2+(1-p)*5;ctx.lineCap='round';
   ctx.beginPath();ctx.moveTo(b.x1,b.y1);ctx.quadraticCurveTo((b.x1+b.x2)/2,(b.y1+b.y2)/2-24,b.x1+(b.x2-b.x1)*easing(p),b.y1+(b.y2-b.y1)*easing(p));ctx.stroke();ctx.restore();}
  for(const r of rings){r.age+=dt;const p=clamp(r.age/r.ttl,0,1);const c=PALETTE[r.mode]||PALETTE.select;
   ctx.save();ctx.globalAlpha=Math.sin(Math.PI*p)*.88;ctx.strokeStyle=c[0];ctx.shadowColor=c[1];ctx.shadowBlur=20*(1-p);ctx.lineWidth=1.2+3*(1-p);
   ctx.beginPath();ctx.ellipse(r.x,r.y,(12+r.r*easing(p))*(r.mode==='select'?1.8:1),8+r.r*easing(p)*.65,-.04,0,Math.PI*2);ctx.stroke();ctx.restore();}
  for(const p of particles){p.age+=dt;const f=dt*60;p.x+=p.vx*f;p.y+=p.vy*f;p.vy+=p.gravity*f;p.vx*=Math.pow(.985,f);p.angle+=p.spin*f;
   const alpha=clamp(1-p.age/p.ttl,0,1);if(alpha<=0)continue;ctx.save();ctx.globalAlpha=alpha;ctx.translate(p.x,p.y);ctx.rotate(p.angle);ctx.fillStyle=p.color;ctx.strokeStyle=p.color;ctx.shadowColor=p.color;ctx.shadowBlur=12*alpha;
   if(p.type==='glass'||p.type==='ice'){ctx.beginPath();ctx.moveTo(-p.size,-p.size);ctx.lineTo(p.size*1.2,-p.size*.2);ctx.lineTo(-p.size*.25,p.size*1.4);ctx.closePath();ctx.globalAlpha=alpha*.55;ctx.fill();ctx.globalAlpha=alpha;ctx.lineWidth=.8;ctx.stroke()}
   else if(p.type==='blade'){ctx.fillRect(-p.size*2,-.65,p.size*4,1.3)}
   else if(p.type==='rune'){ctx.font='14px Georgia, serif';ctx.textAlign='center';ctx.fillText(['ᚠ','ᛉ','ᚱ','ᛟ'][Math.abs((p.angle*11)|0)%4],0,0)}
   else if(p.type==='ember'){ctx.beginPath();ctx.moveTo(0,-p.size*2);ctx.quadraticCurveTo(p.size*1.3,0,0,p.size);ctx.quadraticCurveTo(-p.size*1.3,0,0,-p.size*2);ctx.fill()}
   else{ctx.beginPath();ctx.arc(0,0,p.size*.7,0,Math.PI*2);ctx.fill()}ctx.restore();}
  particles=particles.filter(p=>p.age<p.ttl);rings=rings.filter(p=>p.age<p.ttl);beams=beams.filter(p=>p.age<p.ttl);
  if(particles.length||rings.length||beams.length)frame=requestAnimationFrame(tick);else{frame=0;ctx.clearRect(0,0,w,h)}}
 function ephemeral(className,html,rect,duration=1000){const el=document.createElement('div');el.className=className;el.style.left=rect.left+'px';el.style.top=rect.top+'px';el.style.width=rect.width+'px';el.style.height=rect.height+'px';el.innerHTML=html;container.append(el);setTimeout(()=>el.remove(),duration+90);return el;}
 function relabel(rect,text,kind){const p=point(rect),tag=document.createElement('div');tag.className='aether-floating-text '+kind;tag.style.left=p.x+'px';tag.style.top=(rect.top-10)+'px';tag.textContent=text;container.append(tag);setTimeout(()=>tag.remove(),1150);}
 function shock(row,mode){if(!options.shake||reduced()||!row)return;row.classList.remove('aether-shake');void row.offsetWidth;row.classList.add('aether-shake');setTimeout(()=>row.classList.remove('aether-shake'),470)}
 function cloneGhost(row,rect,mode){if(!row||!validRect(rect))return;
  const stage=ephemeral('aether-ghost-stage aether-'+mode,'',rect,1300);
  if(['burn','frost','thunder','arrow','fireworks'].includes(mode)){const film=document.createElement('div');film.className='aether-elemental-film';stage.append(film);return}
  const pieceCount=mode==='shatter'?14:mode==='shot'?10:mode==='slash'?2:1;
  const n=Math.max(1,pieceCount);
  for(let i=0;i<n;i++){const ghost=row.cloneNode(true);ghost.removeAttribute('id');ghost.classList.add('aether-ghost-row');ghost.removeAttribute('data-id');ghost.querySelectorAll('[id]').forEach(x=>x.removeAttribute('id'));
   ghost.setAttribute('aria-hidden','true');ghost.style.margin='0';ghost.style.width='100%';ghost.style.height='100%';ghost.style.minHeight='0';ghost.style.pointerEvents='none';ghost.style.position='absolute';ghost.style.inset='0';
   const fragment=document.createElement('div');fragment.className='aether-fragment';fragment.style.setProperty('--frag-index',i);
   const col=i%4,rowIndex=Math.floor(i/4),x0=col*25,y0=rowIndex*25,x1=(col+1)*25,y1=(rowIndex+1)*25;
   if(mode==='shatter'||mode==='shot'){const mid=x0+rnd(8,15),ym=y0+rnd(8,15);fragment.style.clipPath=`polygon(${x0}% ${y0}%, ${x1}% ${y0}%, ${x1}% ${y1}%, ${mid}% ${ym}%, ${x0}% ${y1}%)`}
   else if(mode==='slash')fragment.style.clipPath=i===0?'polygon(0 0,100% 0,0 100%)':'polygon(100% 0,100% 100%,0 100%)';
   else fragment.style.clipPath='inset(0)';
   fragment.style.setProperty('--dx',(mode==='slash'?(i===0?-20:22):rnd(-90,90))+'px');fragment.style.setProperty('--dy',(mode==='burn'?rnd(-100,-35):rnd(-42,85))+'px');fragment.style.setProperty('--rotation',rnd(-46,46)+'deg');fragment.append(ghost);stage.append(fragment);
  }
 }
 function anchoredSelection(row){
  if(!row)return;const stage=document.createElement('div');stage.className='aether-selection-stage aether-anchored';
  stage.innerHTML='<i class="aether-sweep"></i><i class="aether-sigil">ᛟ</i><i class="aether-sigil aether-sigil-b">ᚱ</i><i class="aether-selection-border"></i>';
  // Every spark belongs to the actual row, so the animation follows row scrolling,
  // sorting and resizing rather than becoming a detached viewport flash.
  const embers=document.createDocumentFragment();
  const count=Math.round(18*quality());
  for(let i=0;i<count;i++){const n=document.createElement('i');n.className='aether-selection-spark';
   n.style.setProperty('--sx',rnd(10,94)+'%');n.style.setProperty('--sy',rnd(12,88)+'%');
   n.style.setProperty('--dx',rnd(-25,25)+'px');n.style.setProperty('--dy',rnd(-16,16)+'px');
   n.style.setProperty('--delay',rnd(0,.28)+'s');n.style.setProperty('--glow',i%3===0?'#ffe3a3':'#8ff7dc');
   embers.append(n)}stage.append(embers);
  row.append(stage);setTimeout(()=>stage.remove(),1040);
 }
 function setSelected(row){document.querySelectorAll('.mod-row.aether-target').forEach(x=>{if(x!==row)x.classList.remove('aether-target')});if(row)row.classList.add('aether-target')}
 function select(row,{force=false,source='app'}={}){if(!row||!row.classList.contains('mod-row'))return;
  const id=row.dataset.id||'',rect=row.getBoundingClientRect();if(!validRect(rect))return;
  const old=document.querySelector('.mod-row.aether-target');
  const changed=selected!==id;selected=id;setSelected(row);
  if(!quality()||reduced()||(!changed&&!force)||performance.now()-lastSelect<105)return;
  lastSelect=performance.now();anchoredSelection(row);play('select');
 }
 function complete(row,{mode='random',verified=false}={}){if(!row)return;let rect=row.getBoundingClientRect();if(!validRect(rect)||reduced()||!quality())return;
  if(mode==='random')mode=MODES[Math.floor(Math.random()*MODES.length)];if(mode==='off')return;
  cloneGhost(row,rect,mode);const c=point(rect);const intensity=quality();
  const impactX=rect.left+Math.min(rect.width*.43,460);const impactY=c.y;
  shock(row,mode);ring(impactX,impactY,mode,Math.min(130,rect.width*.33));burst(impactX,impactY,mode,Math.round(55*intensity),mode==='fireworks'?2.3:1.5);
  ephemeral('aether-completion-stage aether-'+mode,`<div class="aether-strike-graphic"><span></span><span></span></div><div class="aether-crest">${mode==='burn'?'ᚠ':mode==='frost'?'❄':mode==='thunder'?'ϟ':mode==='arrow'?'➶':'ᛟ'}</div>`,rect,1050);
  relabel(rect,verified?'UPDATE VERIFIED':'QUEST CLEARED',verified?'verified':'complete');
  if(performance.now()-lastCheck<9000)chain++;else chain=1;lastCheck=performance.now();if(chain>1)combo(c.x,Math.max(74,rect.top-15),chain);
  play(mode);
  if(mode==='slash'||mode==='arrow'||mode==='shot')beam(rect.left-55,c.y-20,impactX,impactY,mode);
  if(mode==='thunder'){for(let i=0;i<3;i++)beam(impactX+rnd(-45,45),rect.top-130,impactX+rnd(-25,25),impactY,'thunder')}
 }
 function combo(x,y,n){const el=document.createElement('div');el.className='aether-combo';el.style.left=clamp(x,70,w-70)+'px';el.style.top=clamp(y,35,h-35)+'px';el.innerHTML=`<strong>×${n} CHAIN</strong><small>FORGE MOMENTUM</small>`;container.append(el);setTimeout(()=>el.remove(),1300)}
 function undo(row){if(!row||!quality()||reduced())return;const rect=row.getBoundingClientRect();if(!validRect(rect))return;const p=point(rect);ring(p.x,p.y,'undo',65);burst(p.x,p.y,'undo',Math.round(18*quality()),.6);relabel(rect,'ENCHANTMENT UNDONE','select')}
 function favorite(button){if(!button||!quality()||reduced())return;const r=button.getBoundingClientRect(),p=point(r);ring(p.x,p.y,'fireworks',29);burst(p.x,p.y,'fireworks',Math.round(26*quality()),1.0)}
 function hover(row){if(!options.trails||!quality()||reduced()||!row||!row.classList.contains('mod-row'))return;
  const now=performance.now();if(now-lastHover<90||hovered===row.dataset.id)return;lastHover=now;hovered=row.dataset.id;
  const rect=row.getBoundingClientRect();if(!validRect(rect))return;
  row.classList.remove('aether-hover-anchored');void row.offsetWidth;row.classList.add('aether-hover-anchored');
  setTimeout(()=>row.classList.remove('aether-hover-anchored'),550)}
 function reveal(row){if(!row)return;select(row,{force:true,source:'mo2'});const r=row.getBoundingClientRect();if(quality()&&!reduced())relabel(r,'REVEALED IN MO2','select')}
 function verified(row){if(row)complete(row,{verified:true,mode:'fireworks'});else if(quality()&&!reduced()){const x=window.innerWidth*.58,y=Math.min(360,window.innerHeight*.34);ring(x,y,'fireworks',135);burst(x,y,'fireworks',Math.round(140*quality()),2.4);}}
 function config(p){options={...options,...p};if(!quality()){particles.length=0;rings.length=0;beams.length=0;ctx.clearRect(0,0,w,h);container.replaceChildren()};document.body.dataset.aether=options.intensity;document.body.dataset.aetherShake=options.shake?'on':'off';}
 function preview(){const r=document.querySelector('.mod-row:not(.done)')||document.querySelector('.mod-row');if(!r)return;r.scrollIntoView({block:'center',behavior:'instant'});requestAnimationFrame(()=>{if(!r.isConnected)return;select(r,{force:true});complete(r,{mode:document.getElementById('effectSelect')?.value||'random'});});}
 window.AetherFX=Object.freeze({config,select,complete,undo,favorite,hover,reveal,verified,preview,getState:()=>({particles:particles.length,rings:rings.length,beams:beams.length,selected,options:{...options},running:!!frame})});
})();
