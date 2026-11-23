// Detached owned-fixture model. This module takes observed rectangles, not
// guessed card sizes. The caller owns the actual native layout and DOM/AX.
const tokenFields=['source','catalog','projection','order','content','layout','font','locale','density','filter',
 'grouping','direction','engine','css','generation','epoch'];
const digestFields=['catalog','order','content','font','css'];
const numericFields=['innerWidth','portHeight','dpr','gapX','gapY','stickyInset','count'];
const limit=20000;
const finite=(v,min,max)=>typeof v==='number'&&Number.isFinite(v)&&v>=min&&v<=max;
const yes=(v)=>v===true;
const validToken=v=>typeof v==='string'&&v.length>0&&v.length<=256;
export function validKey(c){
 return !!c&&/^(?:[0-9a-f]{40}|[0-9a-f]{64})$/.test(c.source)&&digestFields.every(k=>/^[0-9a-f]{64}$/.test(c[k]))
  &&tokenFields.every(k=>validToken(c[k]))&&numericFields.every(k=>Object.hasOwn(c,k))
  &&c.fontsSettled===true&&c.measurement==='full-layout-rects-v1'
  && finite(c.innerWidth,1,32000)&&finite(c.portHeight,1,32000)&&finite(c.dpr,0.2,16)
  && finite(c.stickyInset,0,c.portHeight*.99)&&finite(c.gapX,0,c.innerWidth)
  && finite(c.gapY,0,c.portHeight)&&Number.isSafeInteger(c.count)&&c.count>0&&c.count<=limit
  && c.direction==='ltr'; // RTL or nonstandard/unknown flows stay ordinary.
}
export function sameKey(a,b){
 return validKey(a)&&validKey(b)&&[...tokenFields,...numericFields].every(k=>Object.is(a[k],b[k]));
}
const sortedCanonical=(x)=>typeof x==='object'&&x!==null&&validToken(x.id)&&
 (x.kind==='card'||x.kind==='section');
const intersects=(top,edge,a,b)=>top<b&&edge>a;
const completeTokens=['completeLayout','noOptimizationHints','noHidden','sameDom','findable','allActions','stableMeasurement'];

export function recordWarm({key,ordered,boxes,evidence,extent}){
 if (!validKey(key)||!Array.isArray(ordered)||!Array.isArray(boxes)||ordered.length!==key.count||
     boxes.length!==key.count||!finite(extent,1,100_000_000)||!completeTokens.every(k=>yes(evidence?.[k]))) return null;
 // 32 bytes/row for exact doubles, 4 bytes/row for the band index, and
 // compact typed band endpoints; no duplicate retained row identities.
 const values=new Float64Array(4*key.count),indices=new Uint32Array(key.count);
 const ends=[],tops=[],edges=[],sections=[],idSet=new Set();
 let current=null,column=0,section=0,header=false,previousRight=-Infinity;
 const flushBand=()=>{if(current){ends.push(current.end);tops.push(current.top);edges.push(current.bottom);sections.push(current.section);}};
 for(let i=0;i<key.count;i++){
  const entry=ordered[i],b=boxes[i];
  if(!sortedCanonical(entry)||idSet.has(entry.id)||!b||b.id!==entry.id||b.kind!==entry.kind||
     b.laidOut!==true||b.visible!==true||
     !finite(b.x,0,key.innerWidth)||!finite(b.y,0,extent)||!finite(b.w,Number.MIN_VALUE,key.innerWidth)||
     !finite(b.h,Number.MIN_VALUE,extent)||b.x+b.w>key.innerWidth||b.y+b.h>extent) return null;
  idSet.add(entry.id);
  if(entry.kind==='section'&&(b.x!==0||b.w!==key.innerWidth))return null;
  if(!current||b.y!==current.top){
   if(current&&b.y<current.bottom+key.gapY)return null;
   flushBand();
   if(entry.kind==='section')section++;
   current={top:b.y,end:i,bottom:b.y+b.h,section};
   column=0;header=entry.kind==='section';previousRight=-Infinity;
  }
  if(column&&(header||entry.kind==='section'||b.x<previousRight+key.gapX))return null;
  if(++column>128)return null;
  current.end=i+1;current.bottom=Math.max(current.bottom,b.y+b.h);
  previousRight=b.x+b.w;indices[i]=ends.length;
  const p=i*4;values[p]=b.x;values[p+1]=b.y;values[p+2]=b.w;values[p+3]=b.h;
 }
 flushBand();
 const frozenKey=Object.freeze({...key});
 const bandEnds=Uint32Array.from(ends),bandTops=Float64Array.from(tops),bandEdges=Float64Array.from(edges),bandSection=Uint32Array.from(sections);
 // Keep a reference, not another list; the binding requires canonical rows
 // immutable and replaced on any change. Verify that, not merely counts.
 if(!Object.isFrozen(ordered)||!ordered.every(Object.isFrozen))return null;
 const knownScroll=Math.max(0,extent-key.portHeight);
 const shape=(i)=>{const p=i*4;return {i,id:ordered[i].id,x:values[p],y:values[p+1],w:values[p+2],h:values[p+3],kind:ordered[i].kind,band:indices[i]}};
 return Object.freeze({
  key:frozenKey, ordered, extent, maxScroll:knownScroll, count:key.count,bands:bandEnds.length,
  storedNumericBytes:[values,indices,bandEnds,bandTops,bandEdges,bandSection].reduce((n,a)=>n+a.byteLength,0),
  rect(i){return Number.isSafeInteger(i)&&i>=0&&i<frozenKey.count?shape(i):null;},
  shapesAt(scroll,sticky=frozenKey.stickyInset){
   if(!finite(scroll,0,knownScroll)||!Object.is(sticky,frozenKey.stickyInset))return null;
   const top=scroll+sticky,bottom=scroll+frozenKey.portHeight;
   let left=0,right=bandEdges.length,probes=0;
   while(left<right){
    const mid=(left+right)>>1;probes++;
    if(bandEdges[mid]<=top)left=mid+1;else right=mid;
   }
   const fromBand=left,rows=[];
   let visited=0;
   for(let band=fromBand;band<bandEnds.length&&bandTops[band]<bottom;band++){
    for(let i=band?bandEnds[band-1]:0;i<bandEnds[band];i++){
     visited++;
     const p=i*4;if(intersects(values[p+1],values[p+1]+values[p+3],top,bottom))rows.push(shape(i));
    }
   }
   return {rows,probes,visited,fromBand,section:bandSection[fromBand]??null,top,bottom};
  },
 });
}

export function captureAnchor(warm,scroll,focusIndex=null){
 const first=warm?.shapesAt(scroll);
 if(!first)return null;
 const item=first.rows.find(r=>r.kind==='card'&&r.i===focusIndex)??first.rows.find(r=>r.kind==='card');
 return item?Object.freeze({id:item.id,i:item.i,offset:item.y-(scroll+warm.key.stickyInset)}):null;
}
function restore(warm,anchor){
 const row=warm.rect(anchor?.i);
 if(!row||row.kind!=='card'||row.id!==anchor.id||!finite(anchor.offset,-warm.extent,warm.extent))return null;
 const scroll=row.y-warm.key.stickyInset-anchor.offset;
 if(!finite(scroll,0,warm.maxScroll))return null; // no silent clamp/change
 const eligible=warm.shapesAt(scroll);
 return eligible?.rows.some(r=>r.i===row.i)?scroll:null;
}
function sameRect(exact,proved){
 return !!proved&&proved.id===exact.id&&proved.kind===exact.kind&&
  ['x','y','w','h'].every(k=>Object.is(exact[k],proved[k]));
}

export function lifecycle(){
 let active;
 return {
  start({warm,key,ordered,anchor,request,measurementEpoch}){
   const id=Object.freeze({});
   const bind=validKey(key)&&Array.isArray(ordered)&&ordered.length===key.count&&
     Object.isFrozen(ordered)&&validToken(measurementEpoch);
   let scroll=null,plan=null;
   if(bind&&request===true&&warm&&warm.ordered===ordered&&sameKey(key,warm.key)){
    scroll=restore(warm,anchor);
    if(scroll!==null) plan=warm.shapesAt(scroll);
   }
   active={id,warm:plan?warm:null,key:bind?Object.freeze({...key}):null,ordered,anchor:plan?Object.freeze({...anchor}):null,scroll,plan,
     measurementEpoch,stage:plan?'priority':'ordinary'};
   return {id,mode:active.stage,priorityRows:plan?.rows.map(({i,id})=>({i,id}))??[],
    widthProof:plan?key.innerWidth:null,scroll,retained:ordered?.length??0};
  },
  first(id,data){
   const {key,epoch,focusedUnchanged,inspected,noHidden,scroll}=data??{};
   if(active?.id!==id||active.stage!=='priority'||!sameKey(active.key,key)||epoch!==active.measurementEpoch||
      !yes(focusedUnchanged)||!yes(noHidden)||!Object.is(scroll,active.scroll)||!Array.isArray(inspected)||
      inspected.length!==active.plan.rows.length||!inspected.length||
      !active.plan.rows.every((r,i)=>sameRect(r,inspected[i])&&inspected[i].interactive===true))return false;
   active.stage='first-visible-proxy';return true;
  },
  clean(id,report){
   if(active?.id!==id||!['priority','first-visible-proxy','ordinary'].includes(active.stage))return false;
   const ok=report&&active.key&&sameKey(active.key,report.key)&&report.epoch===active.measurementEpoch&&
    report.ordered===active.ordered&&yes(report.focusAndAnchorVerified)&&
    (!active.warm||(report.scroll===active.scroll&&report.anchorId===active.anchor.id&&report.anchorOffset===active.anchor.offset&&
     report.extent===active.warm.extent));
   if(!ok){active.stage='fallback-required';return false;}
   const next=recordWarm(report);
   if(!next||active.warm&&!report.boxes.every((b,i)=>sameRect(active.warm.rect(i),b))){
    active.stage='fallback-required';return false;
   }
   active.stage='full-ready';return true;
  },
  state(id){return active?.id===id?active.stage:null},
  cancel(id){if(active?.id!==id)return false;active=null;return true;},
 };
}
