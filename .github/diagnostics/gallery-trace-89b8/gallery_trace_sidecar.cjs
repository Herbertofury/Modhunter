'use strict';
// Test-only observer for the existing disposable WebView2 benchmark.
// No raw trace export, selector text, trace stacks or frame/process IDs in summaries.
const fs=require('node:fs'), path=require('node:path'), crypto=require('node:crypto');
const {performance}=require('node:perf_hooks');
const CAP=8*1024*1024, EVENT_CAP=90000, OUT_CAP=24*1024;
const CATEGORIES=['devtools.timeline','disabled-by-default-devtools.timeline','toplevel','blink.user_timing',
  'disabled-by-default-blink.debug','disabled-by-default-devtools.timeline.invalidationTracking'];
const grade={
  Layout:'layout',UpdateLayoutTree:'style',RecalculateStyles:'style',Paint:'paint',PrePaint:'paint',
  RunTask:'tasks',EventDispatch:'events',FireAnimationFrame:'animationFrames',FunctionCall:'functions',
  RunMicrotasks:'microtasks',GCEvent:'garbageCollection',MinorGC:'garbageCollection',MajorGC:'garbageCollection'
};
const sane=n=>Number.isFinite(n)&&n>=0;
const tags=e=>typeof e.cat==='string'?e.cat.split(','):[];
const round=n=>Math.round(n*1000)/1000;
function union(intervals){
  let starts=-1,ends=-1,sum=0;
  for(const [start,end] of intervals.sort((a,b)=>a[0]-b[0])){
    if(start>ends){sum+=ends-starts;starts=start;ends=end;}
    else ends=Math.max(ends,end);
  }
  return sum+ends-starts;
}
function group(selector){
  if(!/\[data-library-view\s*=\s*(["']?)gallery\1\s*\]/.test(selector))return 'other';
  if(/\.mod-art\[data-provider-label\s*=.+\]::?after\s*$/.test(selector))return 'providerLiteral';
  if(/\.mod-primary\[data-chapter-label\s*=.+\]::?before\s*$/.test(selector))return 'chapterLiteral';
  if(/\.mod-art::?after\s*$/.test(selector))return 'providerPseudoShape';
  if(/\.mod-primary::?before\s*$/.test(selector))return 'chapterPseudoShape';
  return 'other';
}
function traceSummary(raw,window){
  if(!Array.isArray(raw?.traceEvents)||raw.traceEvents.length>EVENT_CAP)throw Error('Unsupported or oversized event collection');
  const marker=name=>{
    const events=raw.traceEvents.filter(e=>e?.name===name&&['R','I','i'].includes(e.ph)&&tags(e).includes('blink.user_timing'));
    if(events.length!==1||!sane(events[0].ts)||!Number.isInteger(events[0].pid)||!Number.isInteger(events[0].tid))
      throw Error('Unique renderer timing marks unavailable');
    return events[0];
  };
  const {pre,post,startMs,endMs}=window, first=marker(pre.name), last=marker(post.name);
  if(first.pid!==last.pid||first.tid!==last.tid||first.ts>=last.ts||
    !(pre.sendMs<=pre.receiveMs&&pre.receiveMs<=startMs&&startMs<endMs&&endMs<=post.sendMs&&post.sendMs<=post.receiveMs))
    throw Error('Gallery timing markers do not bind the same renderer and ordered host window');
  const offLow=Math.max(pre.sendMs*1000-first.ts,post.sendMs*1000-last.ts);
  const offHigh=Math.min(pre.receiveMs*1000-first.ts,post.receiveMs*1000-last.ts);
  const inside=[startMs*1000-offLow,endMs*1000-offHigh];
  const outside=[startMs*1000-offHigh,endMs*1000-offLow];
  if(!Number.isFinite(offLow)||!Number.isFinite(offHigh)||offHigh<offLow||inside[0]>=inside[1]||offHigh-offLow>25000)
    throw Error('Renderer/driver clock uncertainty too large or inconsistent');
  const relevant=raw.traceEvents.filter(e=>e?.pid===first.pid&&e?.tid===first.tid&&sane(e.ts)).sort((a,b)=>a.ts-b.ts);
  const stack=[],durations={},counts={};let malformed=0;
  const events=[];
  for(const e of relevant){
    if(e.ph==='X'&&sane(e.dur)){events.push(e);continue;}
    if(e.ph==='B'){stack.push(e);continue;}
    if(e.ph==='E'){
      const b=stack.pop();
      if(!b||(e.name&&b.name!==e.name)||e.ts<b.ts){if(e.ts>=inside[0]&&e.ts<=inside[1])malformed++;continue;}
      events.push({...b,args:{...b.args,...e.args},dur:e.ts-b.ts});
    }
  }
  if(stack.some(e=>e.ts>=inside[0]&&e.ts<inside[1]))malformed++;
  const valid=e=>Object.hasOwn(grade,e.name)&&tags(e).some(c=>['toplevel','devtools.timeline','disabled-by-default-devtools.timeline'].includes(c));
  const clipped=(e,[begin,end])=>[Math.max(begin,e.ts),Math.min(end,e.ts+e.dur)];
  const tasks=[],other=[],clicks=[];
  for(const e of events.filter(valid)){
    const kind=grade[e.name],outer=clipped(e,outside);
    if(kind==='tasks'&&outer[1]>outer[0])tasks.push(e);
    const band=clipped(e,inside);if(band[1]<=band[0])continue;
    (durations[kind]??=[]).push(band);counts[kind]=(counts[kind]||0)+1;
    if(kind!=='tasks')other.push(e);
    if(e.name==='EventDispatch'&&e.args?.data?.type==='click'&&e.ts>=inside[0]&&e.ts+e.dur<=inside[1])clicks.push(e);
  }
  const selectorGroups={providerLiteral:{},chapterLiteral:{},providerPseudoShape:{},chapterPseudoShape:{},other:{}};
  let selectorEvents=0,invalidSelectorRows=0;
  for(const e of relevant){
    if(e.name!=='SelectorStats'||!tags(e).includes('disabled-by-default-blink.debug')||e.ts<inside[0]||e.ts>=inside[1])continue;
    const table=e.args?.selector_stats?.selector_timings;
    if(!Array.isArray(table)||table.length>EVENT_CAP||selectorEvents>=200){invalidSelectorRows++;continue;}
    selectorEvents++;
    for(const row of table){
      const attempts=row?.match_attempts, reject=row?.fast_reject_count, matches=row?.match_count, elapsed=row?.['elapsed (us)'];
      if(typeof row?.selector!=='string'||row.selector.length>16384||
        ![attempts,reject,matches].every(Number.isSafeInteger)||![attempts,reject,matches,elapsed].every(sane)||
        matches>attempts||reject>attempts){invalidSelectorRows++;continue;}
      const bin=selectorGroups[group(row.selector)];
      bin.rows=(bin.rows||0)+1;bin.attempts=(bin.attempts||0)+attempts;
      bin.matches=(bin.matches||0)+matches;bin.fastRejects=(bin.fastRejects||0)+reject;
      bin.accumulatedMatchUs=(bin.accumulatedMatchUs||0)+elapsed;
    }
  }
  for(const v of Object.values(selectorGroups))if(Object.hasOwn(v,'accumulatedMatchUs'))v.accumulatedMatchUs=round(v.accumulatedMatchUs);
  const phases=Object.fromEntries(Object.entries(durations).map(([key,intervals])=>[key,{events:counts[key],observedUnionMs:round(union(intervals)/1000)}]));
  // Compare known captured tasks only; the remainder is not proven idle time.
  const taskCoverageMs=tasks.length?[
    round(union(tasks.map(e=>clipped(e,inside)).filter(([a,b])=>b>a))/1000),
    round(union(tasks.map(e=>clipped(e,outside)))/1000)
  ]:null;
  const click=clicks.length===1?clicks[0]:null;
  return {schemaVersion:1,driverWallMs:round(endMs-startMs),clockUncertaintyMs:round((offHigh-offLow)/1000),
    phases,rendererTaskCoverageMs:taskCoverageMs,knownEventCoverageMs:round(union(other.map(e=>clipped(e,inside)))/1000),
    driverStartToUniqueClickMs:click?[round((click.ts+offLow)/1000-startMs),round((click.ts+offHigh)/1000-startMs)]:null,
    clickEndToDriverDoneMs:click?[round(endMs-(click.ts+click.dur+offHigh)/1000),round(endMs-(click.ts+click.dur+offLow)/1000)]:null,
    selectorStats:{available:selectorEvents>0&&invalidSelectorRows===0,events:selectorEvents,invalidRows:invalidSelectorRows,
      groups:selectorEvents&&!invalidSelectorRows?selectorGroups:null},
    unattributed:{gridIntrinsicVsOtherLayout:null,finalFrameOwnership:null,uncapturedOrExternalMs:null,unbalancedEvents:malformed}};
}
async function readTraceStream(session,done){
  if(typeof done?.stream!=='string')throw Error('Missing diagnostic trace stream');
  const chunks=[];let size=0, reads=0;
  try{
    if(done.dataLossOccurred!==false||(done.traceFormat!==undefined&&done.traceFormat!=='json')||
      (done.streamCompression!==undefined&&done.streamCompression!=='none'))throw Error('Trace stream unsupported or incomplete');
    for(;;){
      if(++reads>512)throw Error('Trace stream has too many chunks');
      const r=await session.send('IO.read',{handle:done.stream,size:65536});
      if(typeof r.data!=='string'||r.data.length>131072)throw Error('Trace stream chunk invalid');
      const chunk=Buffer.from(r.data,r.base64Encoded?'base64':'utf8');
      size+=chunk.length;if(size>CAP)throw Error('Trace summary input exceeds 8 MiB');
      chunks.push(chunk);if(r.eof)break;
    }
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
  }finally{await session.send('IO.close',{handle:done.stream}).catch(()=>{});}
}
function timeout(p,msec){
  let t;
  const deadline=new Promise((_,reject)=>{t=setTimeout(()=>reject(Error('Tracing diagnostic deadline exceeded')),msec);});
  return Promise.race([p,deadline]).finally(()=>clearTimeout(t));
}
async function begin(browser,page){
  const session=await browser.newBrowserCDPSession();
  let started=false,closed=false,finish;
  const ended=new Promise(resolve=>{finish=resolve;});session.on('Tracing.tracingComplete',finish);
  async function stop(retain=false){
    if(closed)return null;
    closed=true;
    try{
      if(!started)return null;
      await timeout(session.send('Tracing.end'),5000);
      const done=await timeout(ended,15000);
      if(retain)return await timeout(readTraceStream(session,done),15000);
      if(typeof done?.stream==='string')await timeout(session.send('IO.close',{handle:done.stream}),5000);
      return null;
    }finally{session.off('Tracing.tracingComplete',finish);await session.detach().catch(()=>{});}
  }
  const nonce=crypto.randomBytes(8).toString('hex');
  async function mark(edge){
    const name='modhunter.gallery.'+nonce+'.'+edge,sendMs=performance.now();
    await timeout(page.evaluate(name=>{performance.mark(name);performance.clearMarks(name);},name),5000);
    return {name,sendMs,receiveMs:performance.now()};
  }
  try{
    const categories=(await timeout(session.send('Tracing.getCategories'),5000)).categories;
    if(!Array.isArray(categories)||!CATEGORIES.every(c=>categories.includes(c)))throw Error('Required trace categories unavailable in this WebView2');
    await timeout(session.send('Tracing.start',{transferMode:'ReturnAsStream',streamFormat:'json',streamCompression:'none',
      tracingBackend:'chrome',traceConfig:{includedCategories:CATEGORIES,traceBufferSizeInKb:6144,recordMode:'recordUntilFull'}}),5000);
    started=true;
    const pre=await mark('before');
    return {abort:stop,async finish(startMs,endMs){
      try{
        const post=await mark('after');
        return traceSummary(await stop(true),{pre,post,startMs,endMs});
      }finally{if(!closed)await stop().catch(()=>{});}
    }};
  }catch(error){await stop().catch(()=>{});throw error;}
}
function writeSummary(benchmarkReport,summary){
  const dir=path.dirname(benchmarkReport),data=JSON.stringify(summary,null,2)+'\n';
  if(Buffer.byteLength(data)>OUT_CAP)throw Error('Unexpected oversized diagnostic summary');
  fs.writeFileSync(path.join(dir,'gallery-trace-sidecar.json'),data,{flag:'wx'});
}
module.exports={begin,traceSummary,readTraceStream,writeSummary};
