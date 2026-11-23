'use strict';
const assert=require('node:assert/strict');
const {createCarrier,decodeOwnedScreenshot}=require('./carrier.cjs');
const CARD_COUNT=11207, SECTION_COUNT=56, EXPECTED_LAST='ModHunterFixtureLastCardZQNF2026';
const LIMIT=30000;
const ZERO_HINTS={contentVisibility:'visible',hintProperty:''};
const checkValue=(x,key)=>assert.ok(Number.isFinite(x)&&x>=0,`invalid ${key}`);
function validateFirst(data,warm,{filtered=false,reason=null}={}){
 assert.ok(data&&data.hits.length>0,'no headed viewport hit-target proof');
 assert.equal(data.retained,CARD_COUNT+SECTION_COUNT);
 assert.equal(data.mode,warm?'priority':'ordinary');
 assert.equal(data.reason,reason);
 assert.equal(data.focusId,'enter','focus moved during actual click');
 if(warm){assert.equal(data.modelStage,'first-visible-proxy');assert.ok(data.hints>0&&data.hints<data.retained);}
 else {assert.equal(data.modelStage,'ordinary');assert.equal(data.hints,0);}
 checkValue(data.frameBoundaryAt-data.requestedAt,'first frame opportunity');
 if(filtered)assert.ok(data.differingKey.includes('filter'));
}
function validateFull(full,{widthLimit=null,filtered=false}={}){
 assert.equal(full.phase,'full-ready');assert.equal(full.cards,CARD_COUNT);assert.equal(full.sections,SECTION_COUNT);
 assert.equal(full.count,CARD_COUNT+SECTION_COUNT);assert.equal(full.hints,0);
 assert.equal(full.fallbackUsed,false,'priority rejected at the final exact witness');
 assert.equal(full.focused,true);
 for(const k of ['completeLayout','noOptimizationHints','noHidden','sameDom','findable','allActions','stableMeasurement'])assert.equal(full[k],true,`lack of ${k}`);
 assert.match(full.geometryDigest,/^[a-f0-9]{64}$/);assert.match(full.contentDigest,/^[a-f0-9]{64}$/);
 assert.ok(full.distinctCardHeights>4,'not a variable-height witness');
 assert.equal(full.lastBandCards,filtered?expectedLastBand(full):full.firstCardColumns===2?1:3);
 if(filtered)assert.ok(full.selected>full.sections&&full.selected<full.count);
 else assert.equal(full.selected,full.count);
 if(widthLimit)assert.ok(full.viewportWidth<=widthLimit);
 checkValue(full.completeLayoutAt-full.cleanupAt,'full frame/layout interval');
 checkValue(full.auditEndAt-full.completeLayoutAt,'complete coverage audit');
}
function expectedLastBand(full){assert.ok(full.lastBandCards>=1&&full.lastBandCards<=full.firstCardColumns);return full.lastBandCards;}
function compareAB(a,b){
 for(const k of ['geometryDigest','contentDigest','count','cards','sections','selected','viewportWidth','portHeight','dpr','extent','scroll','lastBandCards','distinctCardHeights','firstCardColumns'])
  assert.equal(a.full[k],b.full[k],`same ordered A/B output mismatch: ${k}`);
 assert.equal(a.firstPixels.digest,b.firstPixels.digest,'first captured viewport differs');
 assert.equal(a.completePixels.digest,b.completePixels.digest,'complete captured viewport differs');
}
function pixelWitness(bytes,points,marker){
 const decoded=decodeOwnedScreenshot(bytes);
 const markerColor=decoded.pixel(marker),hitColors=points.map(p=>decoded.pixel(p));
 assert.equal(markerColor,'13795b','actual captured viewport never changed to Gallery');
 assert.ok(hitColors.some(c=>c!=='0e1017'&&c!=='21283e'&&c!=='13795b'),'no distinct visible tile pixels');
 return {w:decoded.w,h:decoded.h,digest:decoded.pixelsSha,markerColor,hits:hitColors.length};
}
const take=async(page,first)=>pixelWitness(await page.screenshot({type:'png',scale:'css',timeout:LIMIT}),first.hits,first.marker);
function ordinalError(value){return value?.replace(/[\r\n]+/g,' ').slice(0,280)??'unknown';}
async function memory(session){
 const [heap,dom]=await Promise.allSettled([session.send('Runtime.getHeapUsage'),session.send('Memory.getDOMCounters')]);
 function checked(resp,fields){
  if(resp.status!=='fulfilled')return {status:'unavailable',detail:ordinalError(resp.reason?.message)};
  const values=Object.fromEntries(fields.filter(k=>Number.isFinite(resp.value[k])&&resp.value[k]>=0).map(k=>[k,resp.value[k]]));
  return fields.slice(0,2).every(k=>Object.hasOwn(values,k))?{status:'measured',...values}:{status:'unsupported',reason:'required counters unavailable'};
 }
 return {heap:checked(heap,['usedSize','totalSize','embedderHeapUsedSize','backingStorageSize']),dom:checked(dom,['documents','nodes','jsEventListeners'])};
}
async function exactAX(session,id,title){
 function unavailable(e){return {status:'unavailable',reason:ordinalError(e.message)};}
 try{
  const {root}=await session.send('DOM.getDocument',{depth:0});
  const {nodeId}=await session.send('DOM.querySelector',{nodeId:root.nodeId,selector:'#'+id});
  assert.ok(nodeId,'fixture AX DOM node missing');
  const {node}=await session.send('DOM.describeNode',{nodeId});
  const {nodes}=await session.send('Accessibility.getPartialAXTree',{backendNodeId:node.backendNodeId,fetchRelatives:false});
  const link=nodes.find(x=>!x.ignored&&x.backendDOMNodeId===node.backendNodeId&&x.role?.value==='link');
  return {status:link?.name?.value===title?'pass':'fail',observedRole:link?.role?.value??null};
 }catch(e){
  if(/wasn't found|not supported|method not found/i.test(e.message))return unavailable(e);
  throw e;
 }
}
async function stableReady(page){
 await page.evaluate(async()=>await window.fixture.ready);
}
async function realClick(page,warm,expect){
 await page.locator('#enter').click();
 await page.waitForFunction(()=>window.fixture.status().first||window.fixture.status().error,{},{timeout:LIMIT});
 const {first,error}=await page.evaluate(()=>window.fixture.status());assert.equal(error,null);
 validateFirst(first,warm,expect);
 const firstPixels=await take(page,first);
 // Host screenshot waiting is explicitly EXCLUDED from the independently
 // reported cleanup interval; total includes this manual experimental pause.
 const full=await page.evaluate(()=>window.fixture.finish());validateFull(full,expect);
 const completePixels=await take(page,first);
 return {first,full,firstPixels,completePixels,spans:{
   frameOpportunityMs:first.frameBoundaryAt-first.requestedAt,
   observedCompleteAfterReleaseMs:full.completeLayoutAt-full.cleanupAt,
   manualWitnessDelayMs:full.cleanupAt-first.frameBoundaryAt,
   elapsedIncludingWitnessDelayMs:full.completeLayoutAt-first.requestedAt,
   auditMs:full.auditEndAt-full.completeLayoutAt}};
}
async function assertInteraction(page){
 await page.locator('#jump').click();
 const tail=page.locator('#mod-'+(CARD_COUNT-1)+'-title');
 assert.equal(await tail.evaluate(el=>document.activeElement===el&&el.getBoundingClientRect().top>=126.75
  &&el.getBoundingClientRect().bottom<=innerHeight),true,'last exact link not visible/focused');
 await page.keyboard.press('Tab');
 const tailNote=page.locator('#mod-'+(CARD_COUNT-1)+' .note');
 assert.equal(await tailNote.evaluate(el=>document.activeElement===el),true,'native Tab did not reach existing last-card note');
 await tailNote.fill('Edited in detached browser fixture');await tailNote.press('Tab');
 assert.equal(await tailNote.inputValue(),'Edited in detached browser fixture');
 const bookmark=page.locator('#mod-'+(CARD_COUNT-1)+' button'),before=await bookmark.getAttribute('aria-pressed');
 await page.keyboard.press('Space');
 assert.notEqual(await bookmark.getAttribute('aria-pressed'),before,'native keyboard bookmark action did not dispatch');
}
async function inPage(browser,carrier,opts,doWork){
 const context=await browser.newContext({viewport:{width:opts.width,height:713},deviceScaleFactor:1.25,locale:'en-US',
   colorScheme:'dark',reducedMotion:'reduce',serviceWorkers:'block',acceptDownloads:false});
 try{
  await context.route('**/*',route=>{
   const url=route.request().url();
   return url===carrier.origin||['fixture.css','fixture.mjs','fixture-config.mjs','warm-geometry.mjs'].some(x=>url===carrier.origin+x)
    ?route.continue():route.abort('blockedbyclient');
  });
  const page=await context.newPage();page.setDefaultTimeout(LIMIT);
  const errors=[];page.on('pageerror',error=>errors.push(ordinalError(error.message)));
  await page.goto(carrier.origin,{waitUntil:'load',timeout:LIMIT});await stableReady(page);
  const cdp=await context.newCDPSession(page);
  const checked=await doWork(page,cdp);assert.deepEqual(errors,[]);return checked;
 }finally{await context.close();}
}
async function mayFind(page){
 const proof=await page.evaluate(()=>window.fixture.engineFind());
 assert.notEqual(proof.status,'fail','available native engine search did not select actual last card');
 return proof;
}
async function paired(browser,carrier,width){
 return inPage(browser,carrier,{width},async(page,cdp)=>{
  // Both arms get the same single-page ordinary oracle warmup at identical
  // recorded anchor; neither hides work by avoiding the normal cold fallback.
  const oracle=await page.evaluate(()=>window.fixture.prepare('warm'));
  await page.evaluate(()=>window.fixture.prepare('normal'));
  const memBefore=await memory(cdp);
  const normal=await realClick(page,false,{widthLimit:width});
  const memNormal=await memory(cdp);
  await page.locator('#leave').click();
  await page.evaluate(()=>window.fixture.prepare('warm'));
  const priority=await realClick(page,true,{widthLimit:width});
  const memWarmClean=await memory(cdp);
  compareAB(normal,priority);
  // Opt-in second observation: probe real native engine semantics WHILE
  // hints are active, without moving anchor of the timed pair.
  const hints=await page.evaluate(()=>window.fixture.hintForRetention());
  assert.ok(hints>0);
  const tailId='mod-'+(CARD_COUNT-1)+'-title';
  await page.waitForFunction(()=>window.fixture.skipLast().status!=='fail',{},{timeout:LIMIT});
  const skipBeforeAX=await page.evaluate(()=>window.fixture.skipLast());assert.notEqual(skipBeforeAX.status,'fail');
  const axWhileHinted=await exactAX(cdp,tailId,EXPECTED_LAST);assert.notEqual(axWhileHinted.status,'fail');
  const memHints=await memory(cdp);
  await page.evaluate(()=>{window.fixture.clearHints();window.fixture.hintForRetention();});
  await page.waitForFunction(()=>window.fixture.skipLast().status!=='fail',{},{timeout:LIMIT});
  const skipBeforeFind=await page.evaluate(()=>window.fixture.skipLast());
  const findWhileHinted=await mayFind(page);
  await page.evaluate(()=>window.fixture.clearHints());
  const tailStyle=await page.locator('#mod-'+(CARD_COUNT-1)).evaluate(el=>({contentVisibility:getComputedStyle(el).contentVisibility,hintProperty:el.style.getPropertyValue('--exact-content-h')}));
  assert.deepEqual(tailStyle,ZERO_HINTS);
  const axAfterCleanup=await exactAX(cdp,tailId,EXPECTED_LAST);assert.notEqual(axAfterCleanup.status,'fail');
  const findAfterCleanup=await mayFind(page);
  await assertInteraction(page);
  return {width,oracle,normal,priority,retention:{hints,skipBeforeAX,skipBeforeFind,axWhileHinted,axAfterCleanup,findWhileHinted,findAfterCleanup,chromeCtrlF:'unverified'},
   memory:{memBefore,memNormal,memWarmClean,memHints},lastJumpAndNativeKeyboard:true};
 });
}
async function stale(browser,carrier,scenario,{width=1028}={}){
 return inPage(browser,carrier,{width},async page=>{
  const pre=await page.evaluate(()=>window.fixture.prepare('warm'));
  if(scenario==='profile'){
    // A → B → A: same visible profile, different exact chooser epoch.
    await page.locator('#profile').click();await page.locator('#profile').click();
  }else if(scenario==='locale'){await page.locator('#locale').click();}
  else if(scenario==='filter'){await page.locator('#filter').click();}
  else if(scenario==='width'){await page.setViewportSize({width:551,height:713});}
  else throw Error('unknown intervention');
  const trial=await realClick(page,false,{reason:'key-or-anchor-not-current',filtered:scenario==='filter',widthLimit:width});
  for(const field of {profile:['epoch'],locale:['locale'],filter:['filter','order','count'],width:['innerWidth']}[scenario])
   assert.ok(trial.first.differingKey.includes(field),`missed actual ${field} invalidation`);
  if(scenario==='filter'){
    await page.locator('#leave').click();await page.locator('#filter').click();
    const restored=await realClick(page,false,{reason:'key-or-anchor-not-current',widthLimit:width});
    assert.equal(restored.full.sameDom,true);assert.equal(restored.full.selected,CARD_COUNT+SECTION_COUNT);
    trial.clearedFilterRestoredAllNativeNodes=true;
  }
  return {scenario,previousWidth:pre.width,mode:trial.first.mode,differingKey:trial.first.differingKey,full:trial.full,clearedFilterRestoredAllNativeNodes:trial.clearedFilterRestoredAllNativeNodes};
 });
}
const identity=()=>{
 const env=process.env;
 const firstNode=Number(process.versions.node.split('.')[0]);
 if(env.MODHUNTER_DETACHED_BROWSER_ACCEPTANCE!=='explicit-authorized-disposable-host')
  throw Error('no browser launch without separately authorized disposable-host opt-in');
 assert.ok(env.GITHUB_ACTIONS==='true'&&env.RUNNER_ENVIRONMENT==='github-hosted'&&env.RUNNER_OS==='Windows'&&
  process.platform==='win32'&&process.arch==='x64'&&firstNode>=22&&firstNode<=26);
 assert.equal(env.GITHUB_REPOSITORY.toLowerCase(),'herbertofury/modhunter');
 assert.equal(env.GITHUB_EVENT_NAME,'push');assert.equal(env.GITHUB_RUN_ATTEMPT,'1');
 assert.equal(env.GITHUB_REF,'refs/heads/diagnostics/gallery-edge-fixture-1d50');
 assert.match(env.GITHUB_SHA,/^[0-9a-f]{40}$/);assert.match(env.GITHUB_RUN_ID,/^[1-9]\d{0,19}$/);
 assert.ok(!Object.keys(env).some(k=>/^(?:SELENIUM_REMOTE|PW_TEST_|PLAYWRIGHT_WS_ENDPOINT|PWDEBUG)/i.test(k)));
 return {commit:env.GITHUB_SHA,run:env.GITHUB_RUN_ID,attempt:1,node:process.versions.node};
};
async function main(){
 const provenance=identity();
 const fs=require('node:fs/promises');
 const path=require('node:path');
 const {launchOptions,checkBrowser}=require('./edge-boundary.cjs');
 const report={schema:1,scope:'synthetic-headless-Edge-acceptance-only',...provenance,status:'unusable',stage:'known-inputs'};
 let carrier,browser,code=1;
 try{
  assert.equal(require('playwright/package.json').version,'1.57.0');
  assert.equal(require('playwright-core/package.json').version,'1.57.0');
  carrier=await createCarrier();report.exactInputs=carrier.inputsSha;
  report.stage='normal-edge-launch';
  const {chromium}=require('playwright');
  browser=await chromium.launch(launchOptions());
  report.stage='normal-edge-command-verification';
  report.browser=await checkBrowser(browser);
  report.stage='cold-fallback';
  const cold=await inPage(browser,carrier,{width:1028},async page=>{
   await page.evaluate(()=>window.fixture.prepare('warm-empty'));
   return realClick(page,false,{widthLimit:1028});
  });
  const widths=[];
  for(const width of [1028,551]){
   report.stage=width===1028?'wide-fresh-and-warm':'narrow-fresh-and-warm';
   widths.push(await paired(browser,carrier,width));
  }
  assert.ok(widths[0].priority.full.firstCardColumns>widths[1].priority.full.firstCardColumns);
  const staleArms=[];
  for(const variant of ['profile','filter','locale','width']){
   report.stage='ordinary-stale-'+variant;
   staleArms.push(await stale(browser,carrier,variant));
  }
  const unknown=widths.some(x=>Object.values(x.retention).some(v=>v?.status==='unavailable')||
   Object.values(x.memory).some(m=>Object.values(m).some(d=>d.status!=='measured')));
  report.stage='bounded-evidence';
  report.acceptance={cold:stripTrial(cold),
   paired:widths.map(x=>({width:x.width,cachedGeometryBytes:x.oracle.bytes,fullCards:x.priority.full.cards,sections:x.priority.full.sections,
    routine:stripTrial(x.normal),warm:stripTrial(x.priority),nativeCapabilities:Object.fromEntries(Object.entries(x.retention)
     .filter(([,v])=>v?.status).map(([k,v])=>[k,v.status])),memorySnapshots:stripMemory(x.memory),
    lastJumpAndNativeKeyboard:x.lastJumpAndNativeKeyboard})),
   stale:staleArms.map(x=>({scenario:x.scenario,mode:x.mode,cards:x.full.cards,selected:x.full.selected,
    hintFree:x.full.noOptimizationHints,fullDigest:x.full.geometryDigest,restoredAll:x.clearedFilterRestoredAllNativeNodes})),
   unverified:['browser-chrome Ctrl+F','full AX/screen-reader UI','OS/steady-state memory','hardware presentation timing','production WebView2/full Gallery release criteria']};
  report.status=unknown?'incomplete':'accepted-fixture'; code=unknown?2:0;
 }catch {
  // Captured browser/client errors can possess local paths and Chromium logs.
  // Publish only the fixed last-stage classification, not the exception.
 }finally{
  try{try{await browser?.close();}finally{await carrier?.close();}}catch {
    code=1;report.status='unusable';report.stage='cleanup';delete report.acceptance;
  }
 }
 if(code===1)delete report.acceptance;
 const bytes=JSON.stringify(report,null,2)+'\n';
 assert.ok(Buffer.byteLength(bytes)<=32*1024);
 await fs.writeFile(path.join(process.env.RUNNER_TEMP,'modhunter-gallery-edge-fixture','report.json'),bytes,{encoding:'utf8',flag:'wx'});
 process.exitCode=code;
}
function stripTrial(x){
 assert.ok(x.full.cleanupAt>=x.first.frameBoundaryAt&&x.first.frameBoundaryAt>=x.first.requestedAt&&x.full.completeLayoutAt>=x.full.cleanupAt);
 return {first:{frameOpportunityMs:x.spans.frameOpportunityMs,screenshotPixelsSHA256:x.firstPixels.digest,retained:x.first.retained},
  completion:{fromHintReleaseToFullLayoutMs:x.spans.observedCompleteAfterReleaseMs,auditMs:x.spans.auditMs,
   separateScreenshotWaitMs:x.spans.manualWitnessDelayMs,layoutSHA256:x.full.geometryDigest,controlsSHA256:x.full.contentDigest,
   screenshotPixelsSHA256:x.completePixels.digest,cards:x.full.cards,sections:x.full.sections,columns:x.full.firstCardColumns,
   cardHeights:x.full.distinctCardHeights,lastFlexBand:x.full.lastBandCards,noHints:x.full.noOptimizationHints,focus:x.full.focused}};
}
function stripMemory(memorySnapshots){
 return Object.fromEntries(Object.entries(memorySnapshots).map(([phase,counters])=>[phase,Object.fromEntries(
  Object.entries(counters).map(([kind,v])=>[kind,Object.fromEntries(Object.entries(v).filter(([k])=>k!=='detail'&&k!=='reason'))]))]));
}
if(require.main===module)main().catch(e=>{
 // Only the first verification message is allowed to leave this process.
 if(e?.message==='no browser launch without separately authorized disposable-host opt-in')console.error(e.message);
 else console.error('Detached diagnostic preflight or report delivery not available');
 process.exitCode=1;
});
module.exports={pixelWitness,compareAB,validateFirst,validateFull,memory,exactAX,stripTrial,stripMemory};
