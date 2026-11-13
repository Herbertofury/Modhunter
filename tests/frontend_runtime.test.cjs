'use strict';

// Execute the actual app closure with deterministic timers, transport and a
// minimal DOM. This proves async/state contracts; it is not browser rendering
// or native Windows/MO2 acceptance evidence.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {test}=require('node:test');
const APP=fs.readFileSync(path.join(__dirname,'../mo2_update_arsenal/web/app.js'),'utf8');
const INSIGHTS=fs.readFileSync(path.join(__dirname,'../mo2_update_arsenal/web/mod_insights.js'),'utf8');
const SOURCES=fs.readFileSync(path.join(__dirname,'../mo2_update_arsenal/web/source_insights.js'),'utf8');

function catalog(profile='a',count=3){
 return {executionEpoch:1,profileId:profile,catalogId:profile,profileName:profile,game:'skyrimspecialedition',gameRuntime:'1.5.97',
  groups:[{id:'group',name:'Fixtures',count,startPriority:0,color:'mint'}],
  items:Array.from({length:count},(_,i)=>({id:'item-'+i,name:'Fixture '+i,groupId:'group',group:'Fixtures',priority:i,author:'Test Author',installedVersion:'1.0',screenshotNote:'',flags:[],audit:{},nexusId:i+1,source:{type:'nexus',verified:true,url:'https://www.nexusmods.com/skyrimspecialedition/mods/'+(i+1)},update:{verdict:'candidate',latestVersion:'2.0'}}))};
}
function harness({live=true,failSnapshots=0,count=3,storedRows={},progressRows={},progressReadFailures=0,nativeWatch=false}={}){
 const nodes=new Map(),timers=new Map(),pending=new Map(),calls=[],active=new Map(),maximum=new Map(),nativeNavigations=[];
 let timerId=0;
 const state={catalog:catalog('a',count),generation:1,failSnapshots,progressRows,progressReadFailures,score:0,scan:{state:'idle'},defer:new Set()};
 const document={hidden:false,activeElement:null,addEventListener(){},querySelectorAll(){return[]},createElement:name=>element(name),getElementById:id=>node(id),querySelector:selector=>node(selector)};
 function element(id){
  const classes=new Set();let html='';
  return{id,textContent:'',value:'',checked:false,disabled:false,hidden:true,dataset:{},writes:0,
   style:{setProperty(){}},classList:{add(...names){names.forEach(n=>classes.add(n))},remove(...names){names.forEach(n=>classes.delete(n))},contains:name=>classes.has(name),toggle(name,force){const on=force??!classes.has(name);on?classes.add(name):classes.delete(name);return on}},
   get innerHTML(){return html},set innerHTML(value){html=value;this.writes++},
   getContext(){return{setTransform(){}}},addEventListener(){},setAttribute(){},removeAttribute(){},querySelector:selector=>node(id+' '+selector),querySelectorAll(){return[]},
   append(){},appendChild(){},remove(){},scrollIntoView(){},focus(){document.activeElement=this},getBoundingClientRect(){return{left:0,top:0,width:800,height:50}},
   // This transport harness does not parse HTML. Only the static footer has
   // modeled children; actual row replacement is covered in Chromium tests.
   get children(){return id==='.pagefooter'?[node(id+'-first'),node(id+'-second')]:[]},get firstElementChild(){return node(id+'-first')},
  };
 }
 function node(id){if(!nodes.has(id))nodes.set(id,element(id));return nodes.get(id)}
 document.body=element('body');node('embeddedCatalog').textContent=JSON.stringify(state.catalog);node('workshopFilter').value='candidate';
 const local=new Map([['mo2-update-arsenal:v2:a',JSON.stringify({rows:storedRows,prefs:{}})]]);
 class FixtureDate extends Date{static now(){return state.now??Date.now()}}
 const context={Date:FixtureDate,console:{log(){},warn(){},error(){}},document,structuredClone,URL,AbortController,Blob,
  innerWidth:1440,innerHeight:900,devicePixelRatio:1,performance:{now:()=>0},navigator:{},matchMedia:()=>({matches:true}),
  localStorage:{getItem:k=>local.get(k)||null,setItem:(k,v)=>local.set(k,v)},
  setTimeout(fn,ms){const id=++timerId;timers.set(id,{fn,ms});return id},clearTimeout:id=>timers.delete(id),
  setInterval(){throw Error('Async interval polling is forbidden')},clearInterval:id=>timers.delete(id),
  requestAnimationFrame(fn){const id=++timerId;timers.set(id,{fn,ms:'frame'});return id},cancelAnimationFrame:id=>timers.delete(id),
  addEventListener(){},confirm:()=>true,
  __MO2_BRIDGE_BOOT__:{active:live,token:'test'},
 };
 context.window=context;context.__MODHUNTER_NATIVE_WATCHTOWER__=nativeWatch;context.location={assign:value=>nativeNavigations.push(value)};
 context.fetch=async(url,options={})=>{
  const endpoint=String(url).slice(5),method=options.method||'GET',key=method+' '+endpoint;
  const body=JSON.parse(options.body||'{}'),c=structuredClone(state.catalog);
  calls.push({endpoint,method,body});active.set(key,(active.get(key)||0)+1);maximum.set(key,Math.max(maximum.get(key)||0,active.get(key)));
  try{
   let value={ok:true};
   if(endpoint==='snapshot'){
    if(state.failSnapshots-- >0)throw Error('Organizer unavailable');
    value={ok:true,catalog:c,generation:state.generation};
   }else if(endpoint==='revision')value={ok:true,profileId:c.profileId,generation:state.generation};
   else if(endpoint==='game?compact=1')value={ok:true,profileId:c.profileId,score:state.score,updates:0,feed:[]};
   else if(endpoint==='progress'&&method==='GET'&&state.progressReadFailures-- >0)throw Error('Fixture progress read failed');
   else if(endpoint==='progress'&&method==='POST'&&state.progressError)throw Error(state.progressError);
   else if(endpoint==='progress')value=method==='POST'&&state.progressReply?{...state.progressReply}:{ok:true,profileId:c.profileId,progress:{rows:structuredClone(state.progressRows),prefs:{}}};
   else if(endpoint==='downloads')value={ok:true,profileId:c.profileId,downloads:state.downloads||{}};
   else if(endpoint==='activity')value={ok:true,profileId:c.profileId,records:[]};
   else if(endpoint==='plugin-audit')value={ok:true,profileId:c.profileId,state:'running',depth:'records'};
   else if(endpoint==='mod-health'||endpoint.startsWith('mod-health?')){
    const id=body.itemId||new URL('http://fixture/'+endpoint).searchParams.get('itemId');
    const item=c.items.find(m=>m.id===id);
    value={ok:true,profileId:c.profileId,itemId:id,health:state.health||{schema:1,provider:'nexus',bugs:{state:'available',displayedCount:0,checkedAt:1800000000},page:{status:'available'},job:{state:'completed'}},localAnalysis:item?.localAnalysis||null};
   }
   else if(endpoint.startsWith('source-details?')){
    const id=new URL('http://fixture/'+endpoint).searchParams.get('itemId');
    value={ok:true,profileId:c.profileId,itemId:id,update:state.sourceDetails||c.items.find(m=>m.id===id)?.update||{}};
   }
   else if(endpoint==='update-plan'||endpoint==='update-plan?details=full')value={ok:true,profileId:c.profileId,profileName:c.profileName,summary:{candidates:c.items.length,total:c.items.length,review:0,manual:0,current:0,risky:0},items:c.items.map(m=>({...m,lane:'candidate',latestVersion:'2.0',risks:[]}))};
   else if(endpoint==='scan'){
    if(method==='POST'){state.scan={state:'running',mode:body.mode,total:c.items.length};value={ok:true,profileId:c.profileId,state:'running'}}
    else value={ok:true,profileId:c.profileId,scan:structuredClone(state.scan)};
   }else if(endpoint==='quick-update')value={ok:true,profileId:c.profileId,state:body.all?'running':'completed',queued:[],skipped:[]};
   else if(endpoint==='quick-state')value={ok:true,profileId:c.profileId,state:'running',total:c.items.length,queued:[],skipped:[]};
   if(state.defer.has(key))await new Promise(resolve=>{if(!pending.has(key))pending.set(key,[]);pending.get(key).push(resolve)});
   return{ok:true,json:async()=>value};
  }finally{active.set(key,active.get(key)-1)}
 };
 const instrument='window.__RUNTIME__={importJSON,attachNativeTracking,trackPageIntent,trackModPage,getSrc,modHTML,renderWorkshop,sourceLinksPending,requestLiveCatalog,bridgeRequest,scanLive,fastUpdateAll,refreshWorkshop,refreshDownloads,nextWorkshopCandidate,pollLiveBridge,updatePrefs,save,counts,filtered,render,importOfflineCatalog,scheduleSearchRender,analyzePlugins,refreshModHealth,drawInspector,loadModInsights,loadSourceDetails,sourceDetailMod,openFilePicker,closeFilePicker,exportWorkshop,state:()=>({connected:LIVE_CONNECTED,quick:QUICK_RUNNING,profileEpoch:PROFILE_EPOCH}),setRow:(id,value,invalidatePlan=true)=>{markSourceEdit(getRow(id).url,value.url);STORE.rows[id]=value;save(invalidatePlan)},setQuery:q=>{STORE.prefs.query=q;scheduleSearchRender()}};';
 const source=APP.replace('/*__POWERSHELL__*/','""').replace(/\}\)\(\);\s*$/,instrument+'\n})();');
 vm.createContext(context);vm.runInContext(SOURCES,context,{filename:'source_insights.js'});vm.runInContext(INSIGHTS,context,{filename:'mod_insights.js'});vm.runInContext(source,context,{filename:'app.js'});
 return{context,state,nodes,timers,calls,active,maximum,pending,nativeNavigations,api:context.__RUNTIME__,qa:context.__ARSENAL_QA__,
  release(key){state.defer.delete(key);for(const resolve of pending.get(key)||[])resolve();pending.delete(key)},
  async settle(){for(let i=0;i<6;i++)await new Promise(resolve=>setImmediate(resolve))},
  async tick(ms){const pair=[...timers].find(([,t])=>t.ms===ms);assert.ok(pair,'Expected timer '+ms);timers.delete(pair[0]);pair[1].fn();await this.settle()},
 };
}

test('release details coalesce and a different profile cannot receive a late result',async()=>{
 const h=harness();await h.settle();const mod=h.qa.getCatalog().items[0];
 const endpoint='GET source-details?itemId=item-0&profileId=a';h.state.defer.add(endpoint);
 h.state.sourceDetails={provider:'gitlab',changelog:'Profile A release notes'};
 const a=h.api.loadSourceDetails(mod),b=h.api.loadSourceDetails(mod);await h.settle();
 assert.equal(h.active.get(endpoint),1);
 h.state.catalog=catalog('b');await h.qa.setCatalog(h.state.catalog);h.release(endpoint);await Promise.all([a,b]);
 assert.equal(h.api.sourceDetailMod(h.qa.getCatalog().items[0]).update.changelog,undefined);
});
test('compact external source file picker fetches exact details and ignores an old Nexus ID',async()=>{
 const h=harness();await h.settle();const data=catalog('a');
 data.items[0].source={type:'gitlab',verified:true,url:'https://gitlab.com/team/project'};
 data.items[0].update={provider:'gitlab',verdict:'candidate',latestVersion:'2.0',releaseAssetCount:1,detailsAvailable:true};
 h.state.catalog=data;h.state.sourceDetails={...data.items[0].update,releaseAssets:[{assetKey:'gitlab:team/project:2:4',assetProof:'f'.repeat(64),provider:'gitlab',name:'mod.zip',size:55}]};
 await h.qa.setCatalog(data);const mod=h.qa.getCatalog().items[0];await h.api.openFilePicker(mod);
 assert.match(h.nodes.get('filePickerFiles').innerHTML,/GitLab/);assert.match(h.nodes.get('filePickerFiles').innerHTML,/data-asset-key="gitlab:team\/project:2:4"/);
 assert.ok(h.nodes.get('filePickerFiles').innerHTML.includes('data-asset-proof="'+'f'.repeat(64)+'"'));
 assert.equal(h.calls.filter(c=>c.endpoint.startsWith('files?')).length,0);
});
test('loading source evidence preserves unsaved inspector notes',async()=>{
 const h=harness();await h.settle();const mod=h.qa.getCatalog().items[0];h.api.drawInspector(mod);
 h.context.document.getElementById('drawerNote').value='Keep my unsaved setup notes';await h.api.loadSourceDetails(mod);await h.settle();
 assert.equal(h.nodes.get('drawerNote').value,'Keep my unsaved setup notes');
});
test('expired public source evidence replaces update actions with an actionable source review',async()=>{
 const h=harness({count:1});await h.settle();const data=catalog('a',1);
 data.items[0].source={type:'gitlab',verified:true,url:'https://gitlab.com/team/project'};
 data.items[0].update={provider:'gitlab',verdict:'candidate',latestVersion:'2.0',releaseAssetCount:1,expiresAt:1};
 h.state.catalog=data;await h.qa.setCatalog(data);
 const html=h.nodes.get('workshopResults').innerHTML;
 assert.match(html,/Review source status/);assert.match(html,/Refresh needed/);
 assert.doesNotMatch(html,/data-workshop-act="quick"|data-workshop-act="review"/);
});

test('initial connection failure retries automatically and restores connected status',async()=>{
 const h=harness({failSnapshots:1});await h.settle();assert.equal(h.api.state().connected,false);
 assert.match(h.nodes.get('livePill').textContent,/DISCONNECTED/);
 await h.tick(4200);assert.equal(h.api.state().connected,true);assert.equal(h.qa.getCatalog().profileId,'a');
});
test('startup fetches each expensive auxiliary endpoint once',async()=>{
 const h=harness();await h.settle();
 for(const endpoint of ['snapshot','update-plan','game?compact=1','downloads'])assert.equal(h.calls.filter(c=>c.endpoint===endpoint&&c.method==='GET').length,1,endpoint);
});
test('concurrent reads coalesce and stale profile results cannot replace current game',async()=>{
 const h=harness();await h.settle();h.state.score=9000;h.state.defer.add('GET game?compact=1');
 const a=h.qa.refreshGame(),b=h.qa.refreshGame();await h.settle();assert.equal(h.active.get('GET game?compact=1'),1);
 h.state.catalog=catalog('b');h.state.score=12;h.state.defer.delete('GET game?compact=1');await h.qa.setCatalog(h.state.catalog);
 h.release('GET game?compact=1');await Promise.all([a,b]);assert.equal(h.qa.game().profileId,'b');assert.equal(h.qa.game().score,12);
});
test('A -> B -> A profile changes still invalidate earlier results',async()=>{
 const h=harness();await h.settle();h.state.score=9000;h.state.defer.add('GET game?compact=1');
 const old=h.qa.refreshGame();await h.settle();h.state.defer.delete('GET game?compact=1');
 h.state.catalog=catalog('b');await h.qa.setCatalog(h.state.catalog);h.state.catalog=catalog('a');h.state.score=7;await h.qa.setCatalog(h.state.catalog);
 h.release('GET game?compact=1');await old;assert.equal(h.qa.game().score,7);
});
test('scan uses one request at a time and duplicate clicks cannot start another scan',async()=>{
 const h=harness();await h.settle();await Promise.all([h.api.scanLive('all'),h.api.scanLive('all')]);
 assert.equal(h.calls.filter(c=>c.endpoint==='scan'&&c.method==='POST').length,1);
 h.state.defer.add('GET scan');await h.tick(1200);assert.equal(h.active.get('GET scan'),1);
 assert.equal([...h.timers.values()].filter(t=>t.ms===1200).length,0,'No timer while the scan request is pending');
 h.state.scan={state:'completed',mode:'all'};h.release('GET scan');await h.settle();await h.tick(1200);
 assert.equal(h.nodes.get('liveAll').disabled,false);assert.equal(h.maximum.get('GET scan'),1);
});
test('quick queue polling stops at profile switch without resetting the new session',async()=>{
 const h=harness();await h.settle();await h.api.fastUpdateAll();h.state.defer.add('GET quick-state');await h.tick(1100);
 assert.equal([...h.timers.values()].filter(t=>t.ms===1100).length,0);
 h.state.catalog=catalog('b');await h.qa.setCatalog(h.state.catalog);h.release('GET quick-state');await h.settle();
 assert.equal(h.api.state().quick,false);assert.equal(h.qa.getCatalog().profileId,'b');assert.equal(h.maximum.get('GET quick-state'),1);
});
test('progress saves are serialized and the last save carries newest row state',async()=>{
 const h=harness();await h.settle();h.state.defer.add('POST progress');h.api.setRow('item-0',{note:'first'});await h.tick(260);
 h.api.setRow('item-0',{note:'latest'});await h.tick(260);assert.equal(h.active.get('POST progress'),1);
 h.release('POST progress');await h.settle();const writes=h.calls.filter(c=>c.endpoint==='progress'&&c.method==='POST');
 assert.equal(h.maximum.get('POST progress'),1);assert.equal(writes.at(-1).body.rows['item-0'].note,'latest');
});
test('cosmetic settings leave catalog DOM intact; large searches keep all records and coalesce frames',async()=>{
 const h=harness({live:false,count:2500});await h.settle();const writes=h.nodes.get('modList').writes;
 h.api.updatePrefs({theme:'snow'});assert.equal(h.nodes.get('modList').writes,writes);
 for(const query of ['Fixture','Fixture 2','Fixture 2499'])h.api.setQuery(query);
 assert.equal([...h.timers.values()].filter(t=>t.ms==='frame').length,1);await h.tick('frame');
 assert.equal(h.api.filtered().length,1);assert.equal(h.qa.getCatalog().items.length,2500);
 assert.equal(h.nodes.get('modList').writes,writes+1);
});

test('a completed prior release does not hide the next update or manual file selection',async()=>{
 const h=harness({count:1});await h.settle();
 h.state.downloads={'41':{itemId:'item-0',status:'installed',installedVersion:'1.0',targetVersion:'2.0'}};
 await h.api.refreshDownloads();
 assert.equal(h.api.nextWorkshopCandidate(),null,'Unconfirmed installation must still block a duplicate');
 assert.match(h.nodes.get('workshopResults').innerHTML,/View download/);
 const data=catalog('a',1);data.items[0].installedVersion='2.0';data.items[0].update.latestVersion='3.0';
 h.state.catalog=data;await h.qa.setCatalog(data);
 assert.equal(h.api.nextWorkshopCandidate().id,'item-0');
 assert.match(h.nodes.get('workshopResults').innerHTML,/Update now/);
 assert.match(h.nodes.get('workshopResults').innerHTML,/Choose file/);
 assert.equal(h.state.downloads['41'].status,'installed','History is retained');
});

test('active downloads still block duplicate updates after an installed-version change',async()=>{
 const h=harness({count:1});await h.settle();
 for(const status of ['downloading','paused','complete','installing']){
  h.state.downloads={'42':{itemId:'item-0',status,installedVersion:'0.9'}};
  await h.api.refreshDownloads();
  assert.equal(h.api.nextWorkshopCandidate(),null,status);
  assert.doesNotMatch(h.nodes.get('workshopResults').innerHTML,/data-workshop-act="quick"/,status);
 }
});

test('profile commands carry the observed execution epoch and never replace an explicit older review',async()=>{
 const h=harness();await h.settle();const data=catalog('a');data.executionEpoch=7;
 h.state.catalog=data;await h.qa.setCatalog(data);
 await h.api.bridgeRequest('POST','quick-update',{profileId:'a',itemIds:['item-0']});
 assert.equal(h.calls.at(-1).body.executionEpoch,7);
 await h.api.bridgeRequest('POST','queue-reviewed',{profileId:'a',executionEpoch:3,entries:[]});
 assert.equal(h.calls.at(-1).body.executionEpoch,3);
 await h.api.bridgeRequest('POST','progress',{profileId:'old-profile',rows:{}});
 assert.equal(h.calls.at(-1).body.executionEpoch,undefined,'Another profile must not receive the current session epoch');
});

test('large Workshop searches render the newest query once per animation frame',async()=>{
 const h=harness({live:false,count:2500});await h.settle();
 const input=h.nodes.get('workshopSearch'),results=h.nodes.get('workshopResults'),writes=results.writes;
 for(const query of ['Fixture','Fixture 2','Fixture 2499']){input.value=query;input.oninput();}
 assert.equal([...h.timers.values()].filter(t=>t.ms==='frame').length,1);
 assert.equal(results.writes,writes);
 await h.tick('frame');
 assert.equal(results.writes,writes+1);
 assert.equal((results.innerHTML.match(/data-workshop-id=/g)||[]).length,1);
 assert.match(results.innerHTML,/Fixture 2499/);
 assert.equal(h.qa.getCatalog().items.length,2500);
});

test('an explicit unknown or foreign game never becomes a fabricated active-game Nexus link',async()=>{
 const h=harness({live:false});await h.settle();
 const data=catalog('imported');data.offline=true;
 Object.assign(data.items[0],{sourceGame:'unknown',updateEligible:false,source:{type:'unknown',verified:false,url:''}});
 Object.assign(data.items[1],{sourceGame:'fallout4',updateEligible:false,source:{type:'nexus',verified:true,url:'https://www.nexusmods.com/fallout4/mods/2'}});
 Object.assign(data.items[2],{sourceGame:'unknown',updateEligible:false,source:{type:'loverslab',verified:false,url:'https://www.loverslab.com/index.php?/topic/123-fixture/'}});
 h.api.importOfflineCatalog(data);
 const [unknown,other,advisory]=h.qa.getCatalog().items.map(h.api.getSrc);
 assert.equal(unknown.verified,false);assert.match(unknown.url,/google\.com\/search/);assert.ok(!decodeURIComponent(unknown.url).includes('site:nexusmods.com/skyrimspecialedition'));
 assert.equal(other.url,'https://www.nexusmods.com/fallout4/mods/2');assert.equal(other.verified,true);
 assert.equal(advisory.url,'https://www.loverslab.com/index.php?/topic/123-fixture/');assert.equal(advisory.verified,false);
});

test('health updates preserve unsaved inspector notes and show observed zero on the actual row',async()=>{
 const h=harness();await h.settle();h.state.defer.add('POST mod-health');
 h.api.drawInspector(h.qa.getCatalog().items[0]);await h.settle();
 h.context.document.getElementById('drawerNote').value='Keep these unsaved install choices';
 h.release('POST mod-health');await h.settle();
 assert.equal(h.nodes.get('drawerNote').value,'Keep these unsaved install choices');
 assert.match(h.nodes.get('modInsightDetails').innerHTML,/Bugs 0/);
 assert.match(h.nodes.get('modList').innerHTML,/Bugs 0/);
 assert.match(h.nodes.get('workshopResults').innerHTML,/Bugs 0/);
});
test('a late audit response cannot attach to a later A -> B -> A profile epoch',async()=>{
 const h=harness();await h.settle();h.state.defer.add('POST plugin-audit');
 const pending=h.api.analyzePlugins();await h.settle();
 h.state.catalog=catalog('b');await h.qa.setCatalog(h.state.catalog);
 h.state.catalog=catalog('a');await h.qa.setCatalog(h.state.catalog);
 h.release('POST plugin-audit');await pending;
 assert.equal(h.qa.getCatalog().pluginAuditState,undefined);
});
test('real checklist filters include stale conversion evidence and scoped inspection respects provider identity',async()=>{
 const h=harness();await h.settle();const data=catalog('a');
 data.items[0].localAnalysis={depth:'records',plugins:[{name:'Source.esp',winner:false,winnerKnown:true,active:true,lightOverride:true,winnerLight:true,provenance:{state:'source-changed'},eligibility:{status:'unknown'}}]};
 data.items[0].modHealth={provider:'github',bugs:{state:'unsupported'}};
 data.items[0].source={verified:true,type:'github',url:'https://github.com/example/project'};
 h.state.catalog=data;await h.qa.setCatalog(data);
 h.api.updatePrefs({insight:'stale-conversion'});
 assert.equal(h.api.filtered().length,1);
 assert.match(h.nodes.get('modList').innerHTML,/ESLifier needs review/);
 const before=h.calls.filter(c=>c.endpoint==='mod-health'&&c.method==='POST').length;
 h.state.health={provider:'github',bugs:{state:'unsupported'},page:{status:'unknown'}};
 h.api.drawInspector(h.qa.getCatalog().items[0]);await h.settle();
 assert.equal(h.calls.filter(c=>c.endpoint==='mod-health'&&c.method==='POST').length,before);
 assert.ok(h.calls.some(c=>c.endpoint.startsWith('mod-health?itemId=item-0')));
});

test('one checklist edit formats one mod and preserves every other cached row',async()=>{
 const h=harness({live:false,count:2500});await h.settle();
 const formatted=[],original=h.context.ModhunterInsights.chips;
 h.context.ModhunterInsights.chips=mod=>{formatted.push(mod.id);return original(mod)};
 h.api.setRow('item-2499',{favorite:true},false);h.api.render();
 assert.deepEqual(formatted,['item-2499']);
 assert.equal((h.nodes.get('modList').innerHTML.match(/class="mod-row /g)||[]).length,2500);
 assert.equal((h.nodes.get('workshopResults').innerHTML.match(/data-workshop-id=/g)||[]).length,2500);
 assert.match(h.api.modHTML(h.qa.getCatalog().items[2499]),/favorite-btn active/);
 formatted.length=0;h.api.render();assert.deepEqual(formatted,[],'An unchanged render must reuse presentation work');
 h.api.setRow('item-2499',{favorite:true,status:'done',note:'Keep this installation choice'},false);h.api.render();
 assert.deepEqual(formatted,['item-2499']);assert.equal(h.api.counts().done,1);
 assert.match(h.api.modHTML(h.qa.getCatalog().items[2499]),/Keep this installation choice/);
});

test('one changed download formats one Workshop row and health results invalidate their own rows',async()=>{
 const h=harness({count:1500});await h.settle();
 const formatted=[],original=h.context.ModhunterInsights.chips;
 h.context.ModhunterInsights.chips=mod=>{formatted.push(mod.id);return original(mod)};
 h.state.downloads={'7':{itemId:'item-1499',status:'downloading',installedVersion:'1.0'}};
 await h.api.refreshDownloads();assert.deepEqual(formatted,['item-1499']);
 assert.match(h.nodes.get('workshopResults').innerHTML,/MO2 · DOWNLOADING/);
 formatted.length=0;
 await h.api.refreshModHealth(h.qa.getCatalog().items[0],false);
 assert.deepEqual(formatted,['item-0','item-0']);
 assert.match(h.nodes.get('modList').innerHTML,/Bugs 0/);
 assert.match(h.nodes.get('workshopResults').innerHTML,/Bugs 0/);
 assert.equal(h.qa.getCatalog().items.length,1500);
});

test('cached source badges, search and update controls follow expiry without a new response',async()=>{
 const h=harness({count:1});await h.settle();h.state.now=1000*1000;
 const data=catalog('a',1);data.items[0].source={type:'github',verified:true,url:'https://github.com/example/project'};
 data.items[0].update={provider:'github',verdict:'candidate',latestVersion:'2.0',freshness:'fresh',expiresAt:1002,releaseAssetCount:1};
 h.state.catalog=data;await h.qa.setCatalog(data);
 h.api.setQuery('"fresh api"');assert.equal(h.api.filtered().length,1);
 assert.match(h.nodes.get('workshopResults').innerHTML,/data-workshop-act="quick"/);
 h.state.now=1003*1000;h.api.render();
 assert.equal(h.api.filtered().length,0,'Expired freshness must leave the search index');
 assert.match(h.nodes.get('workshopResults').innerHTML,/Refresh needed/);
 assert.doesNotMatch(h.nodes.get('workshopResults').innerHTML,/data-workshop-act="quick"|data-workshop-act="review"/);
 h.api.setQuery('"refresh needed"');assert.equal(h.api.filtered().length,1);
 const current=h.qa.getCatalog().items[0];
 current.update={...current.update,expiresAt:2000,retryAt:1005};h.api.render();
 h.api.setQuery('"provider cooldown"');assert.equal(h.api.filtered().length,1);
 h.state.now=1006*1000;h.api.render();
 assert.equal(h.api.filtered().length,0);
 assert.match(h.nodes.get('workshopResults').innerHTML,/data-workshop-act="quick"/);
});

test('same-profile execution changes cancel old UI work and keep unsynced notes',async()=>{
 const h=harness();await h.settle();const before=catalog('a');before.executionEpoch=1;
 h.state.catalog=before;await h.qa.setCatalog(before);
 h.api.setRow('item-0',{note:'Keep my chosen options'},false);
 h.state.defer.add('POST mod-health');const late=h.api.refreshModHealth(h.qa.getCatalog().items[0],false);await h.settle();
 await h.api.fastUpdateAll();assert.equal(h.api.state().quick,true);
 const epoch=h.api.state().profileEpoch,after=catalog('a');after.executionEpoch=2;
 h.state.catalog=after;await h.qa.setCatalog(after);
 assert.ok(h.api.state().profileEpoch>epoch);assert.equal(h.api.state().quick,false);
 assert.equal(h.qa.verifyRow('item-0').state.note,'Keep my chosen options');
 h.release('POST mod-health');await late;
 assert.equal(h.qa.getCatalog().items[0].modHealth,undefined,'Old source health cannot decorate the new execution context');
 await h.tick(260);
 const saved=h.calls.filter(c=>c.endpoint==='progress'&&c.method==='POST').at(-1);
 assert.equal(saved.body.executionEpoch,2);assert.equal(saved.body.rows['item-0'].note,'Keep my chosen options');
});

test('a source-link save fetches a fresh snapshot while an older snapshot is still in flight',async()=>{
 const h=harness();await h.settle();const before=catalog('a');before.executionEpoch=1;
 h.state.catalog=before;await h.qa.setCatalog(before);
 h.state.defer.add('GET snapshot');const oldRead=h.api.requestLiveCatalog();await h.settle();
 const after=catalog('a');after.executionEpoch=2;after.items[0].source={type:'github',verified:true,url:'https://github.com/example/replacement'};
 h.state.catalog=after;h.state.generation=2;h.state.defer.delete('GET snapshot');
 h.state.progressReply={ok:true,profileId:'a',sourceLinksChanged:true,generation:2,executionEpoch:2};
 h.api.setRow('item-0',{url:'https://github.com/example/replacement',note:'New source'});await h.tick(260);await h.settle();
 assert.equal(h.qa.getCatalog().executionEpoch,2);
 assert.equal(h.api.getSrc(h.qa.getCatalog().items[0]).url,'https://github.com/example/replacement');
 assert.equal(h.maximum.get('GET snapshot'),2,'A new revision must not coalesce with the old snapshot');
 h.release('GET snapshot');await oldRead;
 assert.equal(h.qa.getCatalog().executionEpoch,2,'The older response must not restore obsolete source evidence');
});

test('a new execution context restarts unfinished Chronicle hydration',async()=>{
 const h=harness();h.state.defer.add('GET progress');await h.settle();
 assert.equal(h.active.get('GET progress'),1);
 h.api.setRow('item-0',{note:'Written while the profile loads'},false);
 const after=catalog('a');after.executionEpoch=2;h.state.catalog=after;
 const activation=h.qa.setCatalog(after);await h.settle();
 assert.equal(h.active.get('GET progress'),2,'The replacement context needs its own progress read');
 h.release('GET progress');await activation;await h.settle();
 h.api.setRow('item-0',{note:'Chronicle remains writable'},false);await h.tick(260);
 const saved=h.calls.filter(c=>c.endpoint==='progress'&&c.method==='POST').at(-1);
 assert.ok(saved,'Hydration must leave the new context able to save');
 assert.equal(saved.body.executionEpoch,2);assert.equal(saved.body.rows['item-0'].note,'Chronicle remains writable');
});

test('source edits block execution until the newest edit is acknowledged',async()=>{
 const h=harness();await h.settle();
 h.api.setRow('item-0',{url:'https://github.com/example/first'});h.api.render();
 assert.equal(h.api.sourceLinksPending(),true);
 assert.equal(h.nodes.get('workshopQuickAll').disabled,true);
 await assert.rejects(h.api.bridgeRequest('POST','quick-update',{profileId:'a',itemIds:['item-0']}),/Source links are still syncing/);
 assert.equal(h.calls.filter(c=>c.endpoint==='quick-update').length,0);
 h.state.defer.add('POST progress');await h.tick(260);
 h.api.setRow('item-0',{url:'https://github.com/example/second'});
 h.release('POST progress');await h.settle();
 assert.equal(h.api.sourceLinksPending(),true,'An older save cannot unlock a newer edit');
 await h.tick(260);
 assert.equal(h.api.sourceLinksPending(),false);
 assert.equal(h.calls.filter(c=>c.endpoint==='progress'&&c.method==='POST').at(-1).body.rows['item-0'].url,'https://github.com/example/second');
 await h.api.bridgeRequest('POST','quick-update',{profileId:'a',itemIds:['item-0']});
 assert.equal(h.calls.filter(c=>c.endpoint==='quick-update').length,1);
});

test('failed source saves retain the execution guard and ordinary notes never acquire it',async()=>{
 const h=harness();await h.settle();
 h.api.setRow('item-0',{note:'Only a note'},false);assert.equal(h.api.sourceLinksPending(),false);
 h.state.progressError='Fixture Chronicle failure';h.api.setRow('item-0',{url:'https://github.com/example/project'});await h.tick(260);
 assert.equal(h.api.sourceLinksPending(),true);
 await assert.rejects(h.api.bridgeRequest('POST','download',{modId:1,fileId:2,confirm:'DOWNLOAD'}),/Source links are still syncing/);
 assert.match(h.nodes.get('liveState').textContent,/Fixture Chronicle failure/);
 h.state.progressError=null;h.api.save();await h.tick(260);
 assert.equal(h.api.sourceLinksPending(),false);
});

test('restored source overrides stay guarded until Chronicle hydration and save finish',async()=>{
 const h=harness({storedRows:{'item-0':{url:'https://github.com/example/restored'}}});
 h.state.defer.add('GET progress');await h.settle();
 assert.equal(h.api.getSrc(h.qa.getCatalog().items[0]).url,'https://github.com/example/restored');
 assert.equal(h.api.sourceLinksPending(),true);
 await assert.rejects(h.api.bridgeRequest('POST','quick-update',{profileId:'a',itemIds:['item-0']}),/Source links are still syncing/);
 h.release('GET progress');await h.settle();assert.equal(h.api.sourceLinksPending(),true);
 await h.tick(260);assert.equal(h.api.sourceLinksPending(),false);
});

test('failed Chronicle reads never replace unseen progress and polling retries the merge',async()=>{
 const h=harness({progressReadFailures:1,storedRows:{'item-0':{url:'https://github.com/example/restored'}},
  progressRows:{'item-1':{note:'Server-only saved instructions',favorite:true}}});
 await h.settle();
 assert.equal(h.calls.filter(c=>c.endpoint==='progress'&&c.method==='POST').length,0);
 assert.equal([...h.timers.values()].filter(t=>t.ms===260).length,0,'No overwrite may be queued after a failed read');
 assert.equal(h.api.sourceLinksPending(),true);
 h.api.setRow('item-0',{url:'https://github.com/example/restored',note:'New local instructions'});
 await h.api.pollLiveBridge();await h.tick(260);
 const saved=h.calls.filter(c=>c.endpoint==='progress'&&c.method==='POST').at(-1);
 assert.equal(saved.body.rows['item-0'].note,'New local instructions');
 assert.equal(saved.body.rows['item-1'].note,'Server-only saved instructions');
 assert.equal(saved.body.rows['item-1'].favorite,true);
 assert.equal(h.api.sourceLinksPending(),false);
});

test('edits made during delayed hydration win while untouched server fields merge',async()=>{
 const h=harness({storedRows:{'item-0':{favorite:false,note:'Old local note',updatedAt:'2020-01-01T00:00:00Z'}},
  progressRows:{'item-0':{favorite:false,note:'Saved server note',compat:'compatible',updatedAt:'2021-01-01T00:00:00Z'}}});
 h.state.defer.add('GET progress');await h.settle();
 h.api.setRow('item-0',{...h.qa.verifyRow('item-0').state,favorite:true,note:'Typed while loading'},false);
 h.release('GET progress');await h.settle();
 const row=h.qa.verifyRow('item-0').state;
 assert.equal(row.favorite,true);assert.equal(row.note,'Typed while loading');assert.equal(row.compat,'compatible');
 await h.tick(260);
 const saved=h.calls.filter(c=>c.endpoint==='progress'&&c.method==='POST').at(-1);
 assert.equal(saved.body.rows['item-0'].note,'Typed while loading');
});

test('a source acknowledgment cannot unlock an intervening server execution context',async()=>{
 const h=harness();await h.settle();
 const chosen='https://github.com/example/chosen',intervening='https://github.com/example/other';
 h.api.setRow('item-0',{url:chosen});
 h.state.progressReply={ok:true,profileId:'a',sourceLinksChanged:true,executionEpoch:2,generation:2};
 const third=catalog('a');third.executionEpoch=3;third.items[0].source={type:'github',verified:true,url:intervening};h.state.catalog=third;
 await h.tick(260);await h.settle();
 assert.equal(h.qa.getCatalog().executionEpoch,3);assert.equal(h.api.sourceLinksPending(),true);
 await assert.rejects(h.api.bridgeRequest('POST','quick-update',{profileId:'a',itemIds:['item-0']}),/Source links are still syncing/);
 const fourth=catalog('a');fourth.executionEpoch=4;fourth.items[0].source={type:'github',verified:true,url:chosen};h.state.catalog=fourth;
 h.state.progressReply={ok:true,profileId:'a',sourceLinksChanged:true,executionEpoch:4,generation:4};
 await h.tick(260);await h.settle();
 assert.equal(h.qa.getCatalog().executionEpoch,4);assert.equal(h.api.sourceLinksPending(),false);
});

test('late row health cannot replace a newer catalog in the same execution context',async()=>{
 const h=harness();await h.settle();h.state.defer.add('POST mod-health');
 const old=h.api.refreshModHealth(h.qa.getCatalog().items[0],false);await h.settle();
 const newer=catalog('a');newer.items[0].modHealth={provider:'nexus',bugs:{state:'available',displayedCount:7}};
 h.state.catalog=newer;await h.qa.setCatalog(newer);
 h.release('POST mod-health');await old;
 assert.equal(h.qa.getCatalog().items[0].modHealth.bugs.displayedCount,7);
 assert.match(h.nodes.get('modList').innerHTML,/Bugs 7/);
 const current=h.qa.getCatalog().items[0];await h.api.refreshModHealth(current,false);
 assert.equal(current.modHealth.bugs.displayedCount,0,'A fresh request for the new row must still work');
});


test('page tracking requires the native dashboard and never tracks a fallback search',async()=>{
 const browser=harness();await browser.settle();const mod=browser.qa.getCatalog().items[0];
 assert.equal(browser.api.trackPageIntent(mod,'https://example.com/release'), '');
 const native=harness({nativeWatch:true});await native.settle();
 const unknown={...native.qa.getCatalog().items[0],nexusId:0,source:{verified:false,url:''}};
 assert.equal(native.api.trackPageIntent(unknown), '', 'No manufactured search address becomes a watch');
 for(const url of ['http://example.com/page','javascript:alert(1)','https://user:secret@example.com/page'])assert.equal(native.api.trackPageIntent(unknown,url),'');
 assert.equal(native.nativeNavigations.length,0);
});

test('native page tracking preserves exact URL identity and MO2 ownership without changing notes',async()=>{
 const h=harness({nativeWatch:true});await h.settle();const mod=h.qa.getCatalog().items[0];
 h.api.drawInspector(mod);await h.settle();h.context.document.getElementById('drawerNote').value='Keep these unsaved notes';
 h.nodes.get('drawerUrl').value='https://Example.com/Release/Case?Token=A%2BB&branch=Main#changelog';
 const before=h.calls.length;h.nodes.get('trackSource').onclick();
 assert.equal(h.nativeNavigations.length,1);const intent=new URL(h.nativeNavigations[0]);
 assert.equal(intent.protocol,'modhunter-watch:');assert.equal(intent.host,'track');
 assert.equal(intent.searchParams.get('url'),'https://example.com/Release/Case?Token=A%2BB&branch=Main');
 assert.equal(intent.searchParams.get('profileId'),'a');assert.equal(intent.searchParams.get('itemId'),mod.id);assert.equal(intent.searchParams.get('title'),mod.name);
 assert.equal(h.nodes.get('drawerNote').value,'Keep these unsaved notes');assert.equal(h.calls.length,before);
 assert.equal(h.api.sourceLinksPending(),false,'Opening a watch draft must not mutate update execution context');
});


test('a late native tracking capability preserves an already open inspector draft',async()=>{
 const h=harness();await h.settle();h.api.drawInspector(h.qa.getCatalog().items[0]);
 h.context.document.getElementById('drawerNote').value='Unsaved MO2 install choices';
 h.context.document.getElementById('drawerUrl').value='https://example.com/CaseSensitive';
 const writes=h.nodes.get('drawerContent').writes;
 h.context.__MODHUNTER_NATIVE_WATCHTOWER__=true;h.api.attachNativeTracking();
 assert.equal(h.nodes.get('drawerContent').writes,writes);assert.equal(h.nodes.get('drawerNote').value,'Unsaved MO2 install choices');
 h.nodes.get('trackSource').onclick();assert.equal(new URL(h.nativeNavigations[0]).searchParams.get('url'),'https://example.com/CaseSensitive');
});

test('malformed backup preferences cannot partially replace existing progress',async()=>{
 const h=harness();await h.settle();h.api.setRow('item-0',{note:'Keep my progress',status:'review'});
 const before=JSON.stringify(h.qa.verifyRow('item-0').state),saved=h.context.localStorage.getItem('mo2-update-arsenal:v2:a');
 await h.api.importJSON({text:async()=>JSON.stringify({catalogId:'a',rows:{'item-0':{note:'Damaged replacement',status:'done'}},prefs:{collapsed:7}})});
 assert.equal(JSON.stringify(h.qa.verifyRow('item-0').state),before);
 assert.equal(h.context.localStorage.getItem('mo2-update-arsenal:v2:a'),saved);
 assert.doesNotThrow(()=>h.api.render());
});
test('valid backup merges matching rows and preferences without losing other progress',async()=>{
 const h=harness();await h.settle();h.api.setRow('item-0',{note:'Keep note',status:'review'});h.api.setRow('item-1',{note:'Untouched'});
 await h.api.importJSON({text:async()=>JSON.stringify({catalogId:'a',rows:{'item-0':{status:'done'},missing:{note:'not in catalog'}},prefs:{query:'Fixture',collapsed:[],sound:false}})});
 assert.equal(h.qa.verifyRow('item-0').state.note,'Keep note');assert.equal(h.qa.verifyRow('item-0').state.status,'done');assert.equal(h.qa.verifyRow('item-1').state.note,'Untouched');assert.equal(h.qa.getSnapshot().search,'Fixture');
});
test('backup read from an earlier profile epoch cannot restore after A to B to A',async()=>{
 const h=harness();await h.settle();let release;
 const pending=h.api.importJSON({text:()=>new Promise(resolve=>{release=resolve})});
 h.state.catalog=catalog('b');await h.qa.setCatalog(h.state.catalog);h.state.catalog=catalog('a');await h.qa.setCatalog(h.state.catalog);h.api.setRow('item-0',{note:'Current profile work'});
 release(JSON.stringify({catalogId:'a',rows:{'item-0':{note:'Stale file'}}}));await pending;
 assert.equal(h.qa.verifyRow('item-0').state.note,'Current profile work');
});
test('canceling valid backup restore leaves progress and preferences intact',async()=>{
 const h=harness();await h.settle();h.api.setRow('item-0',{note:'Keep'});const saved=h.context.localStorage.getItem('mo2-update-arsenal:v2:a');h.context.confirm=()=>false;
 await h.api.importJSON({text:async()=>JSON.stringify({catalogId:'a',rows:{'item-0':{note:'Replace'}},prefs:{query:'Changed'}})});
 assert.equal(h.qa.verifyRow('item-0').state.note,'Keep');assert.equal(h.qa.getSnapshot().search,'');assert.equal(h.context.localStorage.getItem('mo2-update-arsenal:v2:a'),saved);
});
