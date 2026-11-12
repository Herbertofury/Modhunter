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

function catalog(profile='a',count=3){
 return {profileId:profile,catalogId:profile,profileName:profile,game:'skyrimspecialedition',gameRuntime:'1.5.97',
  groups:[{id:'group',name:'Fixtures',count,startPriority:0,color:'mint'}],
  items:Array.from({length:count},(_,i)=>({id:'item-'+i,name:'Fixture '+i,groupId:'group',group:'Fixtures',priority:i,author:'Test Author',installedVersion:'1.0',screenshotNote:'',flags:[],audit:{},nexusId:i+1,source:{type:'nexus',verified:true,url:'https://www.nexusmods.com/skyrimspecialedition/mods/'+(i+1)},update:{verdict:'candidate',latestVersion:'2.0'}}))};
}
function harness({live=true,failSnapshots=0,count=3}={}){
 const nodes=new Map(),timers=new Map(),pending=new Map(),calls=[],active=new Map(),maximum=new Map();
 let timerId=0;
 const state={catalog:catalog('a',count),generation:1,failSnapshots,score:0,scan:{state:'idle'},defer:new Set()};
 const document={hidden:false,activeElement:null,addEventListener(){},querySelectorAll(){return[]},createElement:name=>element(name),getElementById:id=>node(id),querySelector:selector=>node(selector)};
 function element(id){
  const classes=new Set();let html='';
  return{id,textContent:'',value:'',checked:false,disabled:false,hidden:true,dataset:{},writes:0,
   style:{setProperty(){}},classList:{add(...names){names.forEach(n=>classes.add(n))},remove(...names){names.forEach(n=>classes.delete(n))},contains:name=>classes.has(name),toggle(name,force){const on=force??!classes.has(name);on?classes.add(name):classes.delete(name);return on}},
   get innerHTML(){return html},set innerHTML(value){html=value;this.writes++},
   getContext(){return{setTransform(){}}},addEventListener(){},setAttribute(){},removeAttribute(){},querySelector:selector=>node(id+' '+selector),querySelectorAll(){return[]},
   append(){},appendChild(){},remove(){},scrollIntoView(){},focus(){document.activeElement=this},getBoundingClientRect(){return{left:0,top:0,width:800,height:50}},
   get children(){return[node(id+'-first'),node(id+'-second')]},get firstElementChild(){return node(id+'-first')},
  };
 }
 function node(id){if(!nodes.has(id))nodes.set(id,element(id));return nodes.get(id)}
 document.body=element('body');node('embeddedCatalog').textContent=JSON.stringify(state.catalog);node('workshopFilter').value='candidate';
 const local=new Map();
 const context={console:{log(){},warn(){},error(){}},document,structuredClone,URL,AbortController,Blob,
  innerWidth:1440,innerHeight:900,devicePixelRatio:1,performance:{now:()=>0},navigator:{},matchMedia:()=>({matches:true}),
  localStorage:{getItem:k=>local.get(k)||null,setItem:(k,v)=>local.set(k,v)},
  setTimeout(fn,ms){const id=++timerId;timers.set(id,{fn,ms});return id},clearTimeout:id=>timers.delete(id),
  setInterval(){throw Error('Async interval polling is forbidden')},clearInterval:id=>timers.delete(id),
  requestAnimationFrame(fn){const id=++timerId;timers.set(id,{fn,ms:'frame'});return id},cancelAnimationFrame:id=>timers.delete(id),
  addEventListener(){},confirm:()=>true,
  __MO2_BRIDGE_BOOT__:{active:live,token:'test'},
 };
 context.window=context;
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
   else if(endpoint==='progress')value={ok:true,profileId:c.profileId,progress:{rows:{},prefs:{}}};
   else if(endpoint==='downloads')value={ok:true,profileId:c.profileId,downloads:{}};
   else if(endpoint==='activity')value={ok:true,profileId:c.profileId,records:[]};
   else if(endpoint==='plugin-audit')value={ok:true,profileId:c.profileId,state:'running',depth:'records'};
   else if(endpoint==='mod-health'||endpoint.startsWith('mod-health?')){
    const id=body.itemId||new URL('http://fixture/'+endpoint).searchParams.get('itemId');
    const item=c.items.find(m=>m.id===id);
    value={ok:true,profileId:c.profileId,itemId:id,health:state.health||{schema:1,provider:'nexus',bugs:{state:'available',displayedCount:0,checkedAt:1800000000},page:{status:'available'},job:{state:'completed'}},localAnalysis:item?.localAnalysis||null};
   }
   else if(endpoint==='update-plan')value={ok:true,profileId:c.profileId,profileName:c.profileName,summary:{candidates:c.items.length,total:c.items.length,review:0,manual:0,current:0,risky:0},items:c.items.map(m=>({...m,lane:'candidate',latestVersion:'2.0',risks:[]}))};
   else if(endpoint==='scan'){
    if(method==='POST'){state.scan={state:'running',mode:body.mode,total:c.items.length};value={ok:true,profileId:c.profileId,state:'running'}}
    else value={ok:true,profileId:c.profileId,scan:structuredClone(state.scan)};
   }else if(endpoint==='quick-update')value={ok:true,profileId:c.profileId,state:body.all?'running':'completed',queued:[],skipped:[]};
   else if(endpoint==='quick-state')value={ok:true,profileId:c.profileId,state:'running',total:c.items.length,queued:[],skipped:[]};
   if(state.defer.has(key))await new Promise(resolve=>{if(!pending.has(key))pending.set(key,[]);pending.get(key).push(resolve)});
   return{ok:true,json:async()=>value};
  }finally{active.set(key,active.get(key)-1)}
 };
 const instrument='window.__RUNTIME__={getSrc,bridgeRequest,scanLive,fastUpdateAll,refreshWorkshop,pollLiveBridge,updatePrefs,save,counts,filtered,render,importOfflineCatalog,scheduleSearchRender,analyzePlugins,refreshModHealth,drawInspector,loadModInsights,state:()=>({connected:LIVE_CONNECTED,quick:QUICK_RUNNING,profileEpoch:PROFILE_EPOCH}),setRow:(id,value)=>{STORE.rows[id]=value;save()},setQuery:q=>{STORE.prefs.query=q;scheduleSearchRender()}};';
 const source=APP.replace('/*__POWERSHELL__*/','""').replace(/\}\)\(\);\s*$/,instrument+'\n})();');
 vm.createContext(context);vm.runInContext(INSIGHTS,context,{filename:'mod_insights.js'});vm.runInContext(source,context,{filename:'app.js'});
 return{context,state,nodes,timers,calls,active,maximum,pending,api:context.__RUNTIME__,qa:context.__ARSENAL_QA__,
  release(key){state.defer.delete(key);for(const resolve of pending.get(key)||[])resolve();pending.delete(key)},
  async settle(){for(let i=0;i<6;i++)await new Promise(resolve=>setImmediate(resolve))},
  async tick(ms){const pair=[...timers].find(([,t])=>t.ms===ms);assert.ok(pair,'Expected timer '+ms);timers.delete(pair[0]);pair[1].fn();await this.settle()},
 };
}

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
