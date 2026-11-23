import {recordWarm,captureAnchor,lifecycle,sameKey} from './warm-geometry.mjs';
import {config} from './fixture-config.mjs';

const $=id=>document.getElementById(id);
const root=$('grid'),port=$('port'),ent=$('enter'),marker=$('marker'),status=$('status');
const nextFrame=()=>new Promise(resolve=>requestAnimationFrame(resolve));
const paintBoundary=async()=>{await nextFrame();await nextFrame();};
const insist=(ok,why)=>{if(!ok)throw Error(why);};
const sha=async x=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(x))),a=>a.toString(16).padStart(2,'0')).join('');
const digestPromise=Object.fromEntries(Object.entries(config.fixtureHashes).map(([key,value])=>[key,value]));
const LAST='ModHunterFixtureLastCardZQNF2026';
const model=lifecycle(),entries=[],fragment=document.createDocumentFragment(),hinted=[];
let allSlots=[],slots,ordered,prof=0,lang=0,filtered=false,epoch=0,generation=0,warm=null,anchor=null,desired=0;
let current,keyDigests,setting='cold',preferred=0,observedEvent=null,error=null,pending=false;
document.body.dataset.mode='list';

const make=(tag,text,cls)=>{const node=document.createElement(tag);if(text)node.textContent=text;if(cls)node.className=cls;return node;};
const add=(node,item)=>{
 const id=item.id;node.id=id;node.classList.add('entry');node.dataset.entry=id;
 const original=Object.freeze(item);entries.push(original);allSlots.push({node,item:original});fragment.append(node);
};
let n=0,chapter=0;
while(n<config.cards){
 const last= config.cards-3;
 const span=n<last?Math.min(last-n,137+(chapter%7)*23):config.cards-n;
 const sectionId='chapter-'+chapter;
 add(make('h2','Chapter '+chapter,'chapter'),{id:sectionId,kind:'section',title:'Chapter '+chapter});
 for(let j=0;j<span;j++,n++){
   const id='mod-'+n, title=n===config.cards-1?LAST:'Fixture mod '+n;
   const article=make('article',null,'mod');article.style.setProperty('--art-variation',((n*37)%137/2+n%9/64)+'px');
   article.setAttribute('aria-labelledby',id+'-title');
   article.append(make('div',null,'art'));
   const link=make('a',title,'primary');link.id=id+'-title';link.href='#source-'+n;
   article.append(link,make('strong',n%2?'Nexus':'Local','badge'),make('span',n%3?'Verified 1.2':'Search source','pill'));
   if(n%3===0)article.append(make('span','Extra author note for version '+n+(n%6===0?' on official fixture page.':''),'extra'));
   const note=make('input',null,'note');note.setAttribute('aria-label','Notes for '+title);note.value=n%7===0?'Review a change':'';
   const button=make('button',n%13===0?'Remove bookmark':'Bookmark','footer');button.setAttribute('aria-pressed',String(n%13===0));
   button.addEventListener('click',()=>{
    if(pending)return;const saved=button.getAttribute('aria-pressed')==='true';button.setAttribute('aria-pressed',String(!saved));
    button.textContent=saved?'Bookmark':'Remove bookmark';changed();
   });
   note.addEventListener('change',()=>{if(!pending)changed();});
   article.append(note,button);
   add(article,{id,kind:'card',title,provider:n%2?'Nexus':'Local',bookmark:n%13===0});
 }
 chapter++;
}
root.append(fragment);Object.freeze(entries); allSlots=Object.freeze(allSlots);
function setProjection(){
 slots=allSlots.filter(s=>s.item.kind==='section'||!filtered||s.node.querySelector('button').getAttribute('aria-pressed')==='true');
 const selected=new Set(slots);for(const slot of allSlots){
  if(selected.has(slot)){if(slot.node.hidden)slot.node.hidden=false;}
  else slot.node.hidden=true;
 }
 ordered=Object.freeze(slots.map(s=>s.item));keyDigests=null;
}
function changed(){generation++;setProjection();}
setProjection();
function configuration(){
 const keyRef=ordered;
 return Promise.all([sha(keyRef.map(x=>x.id).join('\0')),sha(keyRef.map(x=>x.title).join('\0'))]).then(([order,catalog])=>{
  insist(ordered===keyRef,'projection superseded before request');keyDigests={order,catalog};
 });
}
const fontCanvas=document.createElement('canvas').getContext('2d');
function staticGeometry(){
 const p=port.getBoundingClientRect(),g=root.getBoundingClientRect(),s=$('sticky').getBoundingClientRect();
 const listStyle=getComputedStyle(root),bodyStyle=getComputedStyle(document.body);
 fontCanvas.font=bodyStyle.font;
 return {p,g,s,bodyStyle,gapX:parseFloat(listStyle.columnGap),gapY:parseFloat(listStyle.rowGap),
 fontTag:JSON.stringify([bodyStyle.font,document.fonts.status,fontCanvas.measureText('Az WĒ 0/19 🦊').width,devicePixelRatio])};
}
let fontsKey,previousFontTag;
async function initialize(){
 await document.fonts.ready;await configuration();await paintBoundary();
 const geom=staticGeometry();previousFontTag=geom.fontTag;fontsKey=await sha(previousFontTag);
}
const ready=initialize().catch(e=>{error=String(e);throw e;});
function exactKey(g=staticGeometry()){
 insist(keyDigests,'no current projection digest');
 return {source:config.referenceSource,catalog:keyDigests.catalog,order:keyDigests.order,
 content:digestPromise.content,css:digestPromise.css,font:fontsKey,projection:'owned-ordered-flex-v1',
 layout:'gallery',locale:document.documentElement.lang,density:'fixture-compact',filter:filtered?'bookmark':'all',
 grouping:'chapters',direction:g.bodyStyle.direction,engine:navigator.userAgent,generation:String(generation),epoch:String(epoch)+':'+prof,
 innerWidth:g.g.width,portHeight:port.clientHeight,dpr:devicePixelRatio,gapX:g.gapX,gapY:g.gapY,stickyInset:g.s.height,
 count:ordered.length,fontsSettled:document.fonts.status==='loaded'&&g.fontTag===previousFontTag,
 measurement:'full-layout-rects-v1'};
}
function box(s,g,computed=false){
 const n=s.node,r=n.getBoundingClientRect(),c=computed?getComputedStyle(n):null;
 const action=n.querySelector('a');
 return {id:s.item.id,kind:s.item.kind,x:r.left-g.g.left,y:r.top-g.p.top+port.scrollTop,w:r.width,h:r.height,
   visible:n.isConnected&&!n.hidden&&(!c||(c.visibility==='visible'&&c.display!=='none'&&c.contentVisibility==='visible')),
   laidOut:r.width>0&&r.height>0&&n.getClientRects().length===1, interactive:s.item.kind==='section'||!!action&&action.tabIndex===0};
}
function collect(){
 const g=staticGeometry(),key=exactKey(g),extent=port.scrollHeight;
 const full=slots.map(s=>box(s,g,true));
 const noHidden=slots.every(s=>!s.node.hidden&&!s.node.inert&&!s.node.hasAttribute('aria-hidden'));
 const noOptimizationHints=hinted.length===0&&!root.querySelector('[data-hint]');
 const findable=noHidden&&slots.every(s=>s.item.kind==='section'||s.node.querySelector('a')?.textContent===s.item.title);
 const sameDom=root.children.length===allSlots.length&&allSlots.every((s,i)=>root.children[i]===s.node);
 const allActions=slots.every(({node,item})=>item.kind==='section'||node.querySelector('a')?.getAttribute('href')==='#source-'+item.id.slice(4)
  &&!!node.querySelector('.badge')&&!!node.querySelector('.pill')&&!!node.querySelector('.note')&&!!node.querySelector('button'));
 const evidence={completeLayout:full.every(b=>b.laidOut&&b.visible),noOptimizationHints,noHidden,sameDom,findable,allActions,stableMeasurement:false};
 return {key,ordered,boxes:full,extent,evidence,scroll:port.scrollTop,
  fullContent:slots.map(({node,item})=>[item.id,node.innerText,node.querySelector('a')?.getAttribute('href')??null,
   node.querySelector('.note')?.value??null,node.querySelector('button')?.getAttribute('aria-pressed')??null])};
}
const gcStamp=()=>({count:root.children.length,cards:entries.filter(x=>x.kind==='card').length,
 sections:entries.filter(x=>x.kind==='section').length,selected:slots.length,hints:hinted.length});
function hit(shape,g,expected){
 const visibleBounds={top:Math.max(g.p.top+g.s.height,shape.y-port.scrollTop+g.p.top),
  bottom:Math.min(g.p.bottom,shape.y-port.scrollTop+shape.h+g.p.top)};
 if(visibleBounds.bottom-visibleBounds.top<1)return null;
 const x=shape.x+g.g.left+shape.w/2, y=(visibleBounds.top+visibleBounds.bottom)/2;
 return document.elementFromPoint(x,y)?.closest('.entry')===expected?{x,y}:null;
}
function clearHints(){
 for(const node of hinted){node.removeAttribute('data-hint');node.style.removeProperty('--exact-content-h');}
 hinted.length=0;
}
function applyHints(keep){
 insist(hinted.length===0,'hints already active');
 for(let i=0;i<slots.length;i++){
  if(keep.has(i))continue;
  const {node,item}=slots[i],r=warm.rect(i);node.style.setProperty('--exact-content-h',(r.h-(item.kind==='section'?10.25:16))+'px');
  node.dataset.hint='';hinted.push(node);
 }
}
function fallback(reason){
 clearHints();return reason;
}
function handle(fn){fn().catch(e=>{clearHints();pending=false;error=String(e?.stack||e);});}
ent.addEventListener('click',ev=>{if(!pending)handle(()=>enter(ev));});
$('leave').addEventListener('click',()=>{
 if(pending)return;
 document.body.dataset.mode='list';port.scrollTop=0;status.textContent='List';observedEvent=null;current=null;
});
$('profile').addEventListener('click',()=>{if(!pending){prof^=1;epoch++;}});
$('locale').addEventListener('click',()=>{if(!pending){
 lang^=1;document.documentElement.lang=lang?'fr-FR':'en-US';$('sticky').textContent=lang?'Catalogue de mods':'Fixture catalog';
 for(const {node,item} of allSlots)if(item.kind==='card')node.querySelector('.pill').textContent=lang?'Vérifier la version publiée pour cet élément':item.id.slice(4)%3?'Verified 1.2':'Search source';
 changed();
}});
$('filter').addEventListener('click',()=>{if(!pending){filtered=!filtered;changed();}});
$('jump').addEventListener('click',()=>{
 const last=slots.findLast(s=>s.item.kind==='card')?.node.querySelector('a');
 last?.focus({preventScroll:true});last?.scrollIntoView({block:'center',behavior:'instant'});
});
async function enter(ev){
 insist(ev.isTrusted&&document.body.dataset.mode==='list','requires actual fresh List click');
 pending=true;observedEvent=null;current=null; const requestedAt=performance.now();await ready;if(!keyDigests)await configuration();
 document.body.dataset.mode='gallery';status.textContent='Opening Gallery';
 // No conditional early row omission. Every card already exists and stays in DOM.
 const key=exactKey(),ticket=model.start({warm:setting==='warm'?warm:null,key,ordered,anchor,request:setting==='warm',
 measurementEpoch:'real-request-'+(++preferred)});
 const epochTag='real-request-'+preferred,focus=document.activeElement;
 port.scrollTop=ticket.mode==='priority'?ticket.scroll:desired;
 let failure=null;
 if(ticket.mode==='priority'){
  applyHints(new Set(ticket.priorityRows.map(x=>x.i)));
 }
 await paintBoundary();
 const before=performance.now(),g=staticGeometry(),m=marker.getBoundingClientRect();
 let firstPoints=[];
 if(ticket.mode==='priority'){
  const inspected=ticket.priorityRows.map(({i})=>{
   const b=box(slots[i],g),p=hit(b,g,slots[i].node);if(p)firstPoints.push(p);return {...b,interactive:!!p};
  });
  if(!model.first(ticket.id,{key:exactKey(g),epoch:epochTag,focusedUnchanged:focus===document.activeElement,
    noHidden:!filtered&&root.children.length===config.count&&!root.querySelector('[hidden],[inert],[aria-hidden=true]'),scroll:port.scrollTop,inspected})){
   failure=fallback('visible-measurement-rejected');
   await paintBoundary();firstPoints=[];
  }
 }else if(setting==='warm'&&warm){failure='key-or-anchor-not-current';}
 // Ordinary arm supplies the same viewport pixels and hit witnesses, not model-derived evidence.
 if(!firstPoints.length){
  const fg=staticGeometry();
  for(const s of slots){
   const b=box(s,fg);
   if(b.y+b.h>port.scrollTop+fg.s.height&&b.y<port.scrollTop+port.clientHeight){
    const p=hit(b,fg,s.node);if(p)firstPoints.push(p);
   }else if(b.y>=port.scrollTop+port.clientHeight)break;
  }
 }
 current={ticket,epochTag,focus,key,full:null,first:{requestedAt,frameBoundaryAt:before,
  modelStage:model.state(ticket.id),mode:ticket.mode,reason:failure,differingKey:warm?Object.keys(key).filter(k=>!Object.is(key[k],warm.key[k])):[],
  retained:root.children.length,hints:hinted.length,scroll:port.scrollTop,focusId:focus.id,hits:firstPoints,
  marker:{x:m.left+m.width/2,y:m.top+m.height/2}}};
 if(failure==='visible-measurement-rejected')model.cancel(ticket.id);
 observedEvent='first';
}
async function prepare(kind){
 await ready;insist(!pending&&document.body.dataset.mode==='list','must prepare in list while idle');setting=kind==='warm-empty'?'warm':kind;
 if(kind==='cold'||kind==='warm-empty'){warm=null;anchor=null;desired=0;return null;}
 if(kind==='warm'){
   document.body.dataset.mode='gallery';await paintBoundary();
   await configuration();port.scrollTop=slots[Math.floor(slots.length*.55)].node.offsetTop-132;
   const report=collect();await nextFrame();const repeat=collect();
   report.evidence.stableMeasurement=report.extent===repeat.extent&&report.scroll===repeat.scroll&&sameKey(report.key,repeat.key)
    &&JSON.stringify(report.boxes)===JSON.stringify(repeat.boxes);
   warm=recordWarm(report);insist(warm,'native full geometry does not fit exact warm model');
   desired=port.scrollTop;anchor=captureAnchor(warm,desired);insist(anchor?.id,'native anchor unavailable');
   document.body.dataset.mode='list';port.scrollTop=0;await paintBoundary();
   return {bytes:warm.storedNumericBytes,count:warm.count,bands:warm.bands,
    width:warm.key.innerWidth,extent:warm.extent,anchor,baselineDOM:gcStamp(),...report.evidence};
 }
 insist(kind==='normal','invalid fixture arm');
 return null; // Same requested scroll and same native prewarm, hints/selection alone disabled.
}
async function finish(){
 insist(pending&&observedEvent==='first'&&current,'no real click/first frame');
 const c=current,cleanupAt=performance.now();clearHints();
 status.textContent='Gallery';await paintBoundary();
 // Force exact post-removal layout before the completion timestamp; audit the
 // complete geometry separately and prove it stays stable through another frame.
 const last=slots.at(-1).node.getBoundingClientRect(),extent=port.scrollHeight;
 const completeLayoutAt=performance.now();
 const report=collect();await nextFrame();
 const recheck=collect();
 const focused=document.activeElement===c.focus;
 report.evidence.stableMeasurement=extent===report.extent&&last.height===report.boxes.at(-1).h&&
  sameKey(report.key,recheck.key)&&report.extent===recheck.extent&&report.scroll===recheck.scroll&&
  JSON.stringify(report.boxes)===JSON.stringify(recheck.boxes);
 const actualAnchor=c.ticket.mode==='priority'&&anchor?report.boxes[anchor.i]:null;
 const finalProof={...report,epoch:c.epochTag,focusAndAnchorVerified:focused&&(!actualAnchor||
  actualAnchor.id===anchor.id&&actualAnchor.y-(report.scroll+report.key.stickyInset)===anchor.offset),
  anchorId:actualAnchor?.id,anchorOffset:actualAnchor?actualAnchor.y-(report.scroll+report.key.stickyInset):null};
 let accepted=model.clean(c.ticket.id,finalProof),fallbackUsed=false;
 // A hinted bound failure is never declared successful warm. Report a separately
 // verified ordinary render instead (the hint-free geometry already observed).
 if(!accepted&&c.ticket.mode==='priority'){
  const ordinary=model.start({key:report.key,ordered,request:false,measurementEpoch:c.epochTag});
  accepted=model.clean(ordinary.id,{...finalProof,focusAndAnchorVerified:focused});fallbackUsed=true;
 }
 insist(accepted,'ordinary exact full proof failed');
 const geometryDigest=await sha(JSON.stringify(report.boxes)),contentDigest=await sha(JSON.stringify(report.fullContent));
 c.full={cleanupAt,completeLayoutAt,auditEndAt:performance.now(),origin:c.first.requestedAt,
  viewportWidth:report.key.innerWidth,portHeight:report.key.portHeight,dpr:report.key.dpr,extent:report.extent,
  ...gcStamp(),...report.evidence,focused,fallbackUsed,phase:fallbackUsed?'ordinary-fallback':model.state(c.ticket.id),
  scroll:report.scroll,geometryDigest,contentDigest,
  distinctCardHeights:new Set(report.boxes.filter(b=>b.kind==='card').map(b=>b.h)).size,
  lastBandCards:report.boxes.filter(b=>b.kind==='card'&&b.y===report.boxes.at(-1).y).length,
  firstCardColumns:report.boxes.filter(b=>b.kind==='card'&&b.y===report.boxes[1].y).length};
 observedEvent='full';
 pending=false;return c.full;
}
function engineFind(){
 if(typeof window.find!=='function')return {status:'unavailable',surface:'window.find'};
 const selection=window.getSelection();selection.removeAllRanges();port.scrollTop=0;
 const found=window.find(LAST,true,false,true,false,false,false),
 anchorNode=selection.anchorNode,focusNode=selection.focusNode;
 return {status:found&&selection.toString()===LAST&&anchorNode?.parentElement?.closest('.entry')?.id==='mod-'+(config.cards-1)
  &&focusNode?.parentElement?.closest('.entry')?.id==='mod-'+(config.cards-1)?'pass':'fail',
  surface:'window.find selection endpoint (engine search, not Ctrl+F chrome)',found};
}
Object.defineProperty(window,'fixture',{value:Object.freeze({ready,prepare,finish,engineFind,clearHints,
 status:()=>({first:observedEvent==='first'?current?.first:null,full:current?.full,error,mode:document.body.dataset.mode}),
 hintForRetention:()=>{insist(!!warm&&!pending&&warm.ordered===ordered&&sameKey(warm.key,exactKey()),'retention requires exact warm');
  const rows=warm.shapesAt(port.scrollTop);applyHints(new Set(rows.rows.map(r=>r.i)));return hinted.length;},
 skipLast:()=>{const tail=$('mod-'+(config.cards-1));
  if(typeof tail.checkVisibility!=='function'||!CSS.supports('content-visibility','auto'))return {status:'unavailable'};
  const declared=tail.hasAttribute('data-hint');
  return {status:declared&&!tail.checkVisibility({contentVisibilityAuto:true})?'pass':'fail'};},
 scope:()=>gcStamp()
})});
