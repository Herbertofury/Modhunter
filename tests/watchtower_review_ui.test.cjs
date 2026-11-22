'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const test=require('node:test');
const source=fs.readFileSync(path.join(__dirname,'../native/ui/watchtower-review.js'),'utf8');

function functionSource(name,next){
 let begin=source.indexOf(' async function '+name+'(');
 if(begin<0)begin=source.indexOf(' function '+name+'(');
 assert.ok(begin>=0);
 const end=source.indexOf(next,begin);assert.ok(end>begin);
 return source.slice(begin,end);
}
class Element{
 constructor(tag='div'){this.tag=tag;this.children=[];this.dataset={};this.textContent='';this.events={};this.value='1';}
 append(...children){this.children.push(...children)}
 replaceChildren(...children){this.children=[...children];this.innerHTML=''}
 addEventListener(name,fn){this.events[name]=fn}
}
function makeContext(extra={}){
 const elements=new Map();
 const context={Set,Map,JSON,Number,String,Math,Promise,Error,
  terminal:new Set(['complete','failed','cancelled','unknown','interrupted']),stopped:false,
  jobs:[],duplicates:null,name:x=>x,bytes:n=>String(n)+' B',
  document:{createElement:tag=>new Element(tag)},
  $:id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id)},
  setTimeout:fn=>{fn();return 1},...extra};
 vm.createContext(context);return {context,elements};
}

test('native recovery UI waits for exact terminal result and keeps restored-file refresh failure visible',async()=>{
 const {context,elements}=makeContext();let polls=0;
 context.call=async()=>({ok:true,result:{ok:true,jobId:'review-1-1',reviewId:'review-1-1',downloadId:'',state:'queued'}});
 context.refreshJobs=async()=>{polls++;context.jobs=[{jobId:'review-1-1',reviewId:'review-1-1',downloadId:'',state:polls===1?'restoring':'failed',ok:polls===1,filesRestored:polls>1,refreshRequired:polls>1,error:polls>1?'Refresh did not complete':''}]};
 vm.runInContext(functionSource('runNative',' function failure('),context);
 await assert.rejects(context.runNative('contentRecovery',{},'result'),/Refresh did not complete/);
 assert.equal(polls,2);
 const shown=JSON.parse(elements.get('result').textContent);
 assert.equal(shown.filesRestored,true);assert.equal(shown.refreshRequired,true);assert.equal(shown.state,'failed');
});

test('duplicate undo preview waits for its own full saved result, never another job digest',async()=>{
 const {context}=makeContext();
 context.call=async()=>({ok:true,result:{ok:true,jobId:'review-1-2',reviewId:'review-1-2',downloadId:''}});
 context.refreshJobs=async()=>{context.jobs=[{jobId:'review-1-2',reviewId:'review-1-2',downloadId:'',action:'duplicate:previewRestore',state:'complete',ok:true,result:{reportAvailable:true,digest:'expected'}}]};
 context.latestDuplicates=async()=>({operationJobId:'review-other',operationResult:{digest:'other'}});
 vm.runInContext(functionSource('runNative',' function failure('),context);
 await assert.rejects(context.runNative('duplicateRecovery'),/Another duplicate review/);
 context.latestDuplicates=async()=>({operationJobId:'review-1-2',operationResult:{ok:true,digest:'expected',canRestore:true}});
 const result=await context.runNative('duplicateRecovery');
 assert.equal(result.result.digest,'expected');assert.equal(result.result.canRestore,true);
});

test('quarantine deletion uses its exact preview and never proceeds after declined confirmation',async()=>{
 const {context}=makeContext();let allow=false;const calls=[];
 context.api=()=>({confirmAction:async()=>allow});
 context.runNative=async(kind,value)=>{calls.push({kind,value});return {result:{digest:'exact-deletion-preview'}}};
 vm.runInContext(functionSource('quarantineRecordAction',' async function loadQuarantineHistory('),context);
 await context.quarantineRecordAction({id:'quarantine-owned',state:'quarantined'},'drop');
 assert.equal(calls.length,1);assert.equal(calls[0].value.action,'previewDrop');
 allow=true;await context.quarantineRecordAction({id:'quarantine-owned',state:'quarantined'},'drop');
 assert.equal(calls.length,3);
 assert.deepEqual(JSON.parse(JSON.stringify(calls[2])),{kind:'duplicateRecovery',value:{action:'drop',id:'quarantine-owned',confirm:'DELETE QUARANTINE',expectedDigest:'exact-deletion-preview'}});
});

test('complete mod groups and file groups retain every page and correct physical/profile evidence',()=>{
 const {context,elements}=makeContext();
 const mod={name:'Actual mod',enabled:false,profiles:[{id:'Saved profile',enabled:true,priority:7}]};
 context.duplicates={logicalDuplicateBytes:100,completeContentGroups:Array.from({length:101},()=>({mods:[mod],files:2,logicalBytesPerMod:5})),
  fileGroups:[{bytes:8,paths:[{modName:'Actual mod',path:'textures/a.dds',allocatedBytes:4096,hardLinks:2,owner:mod}],alreadyHardlinked:true,distinctPhysicalFiles:1}]};
 vm.runInContext(functionSource('renderDuplicates',' const downloads='),context);
 context.renderDuplicates();
 assert.equal(elements.get('nativeDuplicateList').children.length,100);
 assert.equal(elements.get('nativeDuplicatePage').max,2);
 assert.match(elements.get('nativeDuplicateSummary').textContent,/1 equal-content file groups · 101 identical-mod groups/);
 const first=elements.get('nativeDuplicateList').children[0];
 assert.equal(first.children.length,1);first.open=true;first.events.toggle();
 assert.match(first.children[1].textContent,/Actual mod · live disabled · Saved profile: enabled \(position 7\)/);
 elements.get('nativeDuplicatePage').value='2';context.renderDuplicates();
 const rest=elements.get('nativeDuplicateList').children;assert.equal(rest.length,2);
 assert.match(rest[1].children[0].textContent,/shared hard links/);
 rest[1].open=true;rest[1].events.toggle();
 assert.match(rest[1].children[1].textContent,/textures\/a.dds.*allocated 4096 B.*hard links 2.*Saved profile: enabled/);
});

function reviewRuntime(){
 const elements=new Map(),windowEvents=new Map(),documentEvents=new Map(),timers=new Map(),calls=[];
 const state={reviews:{rows:[],preparing:[]},settings:{hideTitles:false}};
 const jobs={current:[],read:null};let tick=0,sequence=0,visibilityChanged,serializations=0,scope=null;
 const element=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id)};
 const downloads=element('downloadsView'),history=element('nativeJobs');
 const document={hidden:false,body:new Element(),activeElement:null,getElementById:element,
  createElement:tag=>new Element(tag),addEventListener:(kind,callback)=>documentEvents.set(kind,callback)};
 const api={state:()=>state,installQueueContext:()=>scope,esc:value=>String(value??''),showToast(){},
  async command(kind,value){
   calls.push({kind,value});
   if(kind==='contentJobs')return {ok:true,operations:{jobs:jobs.read?await jobs.read():jobs.current}};
   if(kind==='cancelContentJob'){jobs.current=jobs.current.map(row=>({...row,state:'cancelled'}));return {ok:true}}
   throw Error('Unexpected native command: '+kind);
  }};
 const window={ModhunterWatchtower:api,addEventListener:(kind,callback)=>windowEvents.set(kind,callback)};
 const context={window,document,MutationObserver:class{constructor(callback){visibilityChanged=callback}observe(){}},
  setTimeout:(fn,delay)=>{const id=++sequence;timers.set(id,{fn,at:tick+delay});return id},
  clearTimeout:id=>timers.delete(id),
  JSON:{stringify(value){if(Array.isArray(value)&&value[0]===state.reviews.rows)serializations++;return JSON.stringify(value)}}};
 vm.runInNewContext(source,context);
 async function advance(milliseconds){
  const until=tick+milliseconds;
  for(;;){
   const next=[...timers].sort((a,b)=>a[1].at-b[1].at)[0];if(!next||next[1].at>until)break;
   const [id,timer]=next;timers.delete(id);tick=timer.at;await timer.fn();
  }
  tick=until;await new Promise(resolve=>setImmediate(resolve));
 }
 const publish=()=>windowEvents.get('modhunter-native-state')();
 const changeScope=(revision,profileId)=>{scope={revision,scope:profileId?{profileId,executionEpoch:revision}:null};windowEvents.get('modhunter-install-queue-context')?.()};
 const pane=hidden=>{downloads.hidden=hidden;visibilityChanged?.()};
 const windowHidden=hidden=>{document.hidden=hidden;documentEvents.get('visibilitychange')?.()};
 return {state,jobs,history,calls,timers,advance,publish,pane,windowHidden,element,documentEvents,changeScope,
  serializations:()=>serializations,readCount:()=>calls.filter(call=>call.kind==='contentJobs').length};
}

test('the actual review module wakes zero idle timers and renders fresh rows through native state',async()=>{
 const ui=reviewRuntime();
 ui.state.reviews.rows=Array.from({length:64},(_,id)=>({id:'retained-'+id,filename:'Synthetic '+id,state:'complete',recoveryOnly:true}));
 ui.publish();assert.match(ui.element('nativeReviewQueue').innerHTML,/Synthetic 63/);
 const before=ui.serializations();
 await ui.advance(60*60*1000);
 assert.equal(ui.serializations()-before,0,'one virtual idle hour reserializations');
 assert.equal(ui.timers.size,0);
 assert.equal(ui.readCount(),0);
});

test('external operation refresh is foreground-only and stops on the terminal exact result',async()=>{
 const ui=reviewRuntime();
 const job={jobId:'job-1',reviewId:'review-1',downloadId:'file-1',profileId:'profile-1',action:'install',state:'pending'};
 const review={id:'review-1',jobId:'job-1',downloadId:'file-1',context:{profileId:'profile-1'},state:'pending',submitted:true};
 ui.pane(true);ui.jobs.current=[job];ui.state.reviews.rows=[review];ui.publish();
 await ui.advance(12000);assert.equal(ui.readCount(),0);
 ui.pane(false);await ui.advance(0);assert.equal(ui.readCount(),1);
 assert.match(ui.history.innerHTML,/data-job-id="job-1"/);
 ui.windowHidden(true);await ui.advance(12000);assert.equal(ui.readCount(),1);
 ui.windowHidden(false);await ui.advance(0);assert.equal(ui.readCount(),2);
 ui.pane(true);await ui.advance(12000);assert.equal(ui.readCount(),2);
 ui.pane(false);await ui.advance(0);assert.equal(ui.readCount(),3);
 const button={dataset:{nativeAction:'cancel-job',jobId:'job-1',reviewId:'review-1',downloadId:'file-1'}};
 ui.documentEvents.get('click')({target:{closest:selector=>selector==='[data-native-action]'?button:null}});
 await ui.advance(0);
 assert.deepEqual(JSON.parse(JSON.stringify(ui.calls.at(-2))),{kind:'cancelContentJob',value:{jobId:'job-1',reviewId:'review-1',downloadId:'file-1'}});
 assert.match(ui.history.innerHTML,/cancelled/);
 ui.state.reviews.rows=[];ui.publish();
 ui.state.reviews.rows=[review];ui.publish(); // A late active snapshot cannot restart this exact cancelled job.
 const terminalReads=ui.readCount();
 await ui.advance(12000);assert.equal(ui.readCount(),terminalReads);
 ui.element('nativeRefreshJobs').onclick();await ui.advance(0);
 assert.equal(ui.readCount(),terminalReads+1);
});

test('a known active job makes zero fallback IPC outside Downloads',async()=>{
 const ui=reviewRuntime();
 ui.jobs.current=[{jobId:'job-cached',reviewId:'review-cached',downloadId:'file-cached',
  profileId:'profile-1',action:'install',state:'installing'}];
 ui.element('nativeRefreshJobs').onclick();await ui.advance(0);
 assert.equal(ui.readCount(),1);
 ui.pane(true);
 const before=ui.readCount();
 await ui.advance(12000);
 assert.equal(ui.readCount()-before,0,'fallback IPC in an inactive pane for 12 virtual seconds');
});

test('a committed review stays exact and pending until enumeration catches up, with bounded visible misses',async()=>{
 const ui=reviewRuntime();
 const job={jobId:'job-n',reviewId:'review-n',downloadId:'archive-n',profileId:'profile-1',modName:'Exact deferred result',action:'install',state:'installing'};
 const review={id:job.reviewId,jobId:job.jobId,downloadId:job.downloadId,context:{profileId:job.profileId},submitted:true,state:'installing'};
 ui.changeScope(1,'profile-1');
 ui.state.reviews.rows=[review];ui.publish();
 ui.jobs.current=[{...job,downloadId:'another-archive',state:'complete',modName:'Other receipt'}];
 await ui.advance(0);assert.equal(ui.readCount(),1);
 assert.doesNotMatch(ui.history.innerHTML,/Exact deferred result/);
 assert.match(ui.element('nativeOperationsStatus').textContent,/Waiting for MO2 to list/);
 ui.windowHidden(true);await ui.advance(12000);assert.equal(ui.readCount(),1);
 ui.windowHidden(false);await ui.advance(0);assert.equal(ui.readCount(),2);
 ui.jobs.current=[job];await ui.advance(2000);
 assert.equal(ui.readCount(),3);assert.match(ui.history.innerHTML,/Exact deferred result/);
 ui.jobs.current=[{...job,state:'complete'}];await ui.advance(2000);
 assert.match(ui.history.innerHTML,/complete/);
 const after=ui.readCount();await ui.advance(12000);assert.equal(ui.readCount(),after);

 const capped=reviewRuntime();capped.changeScope(1,'profile-1');
 capped.state.reviews.rows=[review];capped.publish();
 await capped.advance(12000);assert.equal(capped.readCount(),6);
 assert.match(capped.element('nativeOperationsStatus').textContent,/did not list.*Refresh native operations/);
 for(let i=0;i<10;i++)capped.publish();
 await capped.advance(30000);assert.equal(capped.readCount(),6,'unchanged native events cannot restart exhausted reads');
 capped.element('nativeRefreshJobs').onclick();await capped.advance(0);assert.equal(capped.readCount(),7);
 assert.match(capped.element('nativeOperationsStatus').textContent,/did not list.*Refresh native operations/);
});

test('a manual read after A/B/A does not reuse or render the earlier in-flight operation list',async()=>{
 const ui=reviewRuntime(),replies=[];
 ui.jobs.read=()=>new Promise(resolve=>replies.push(resolve));
 const job=(profileId,modName,state='complete')=>({jobId:'same-id',reviewId:'same-review',downloadId:'same-archive',profileId,modName,state,action:'install'});
 ui.changeScope(1,'A');
 ui.element('nativeRefreshJobs').onclick();await ui.advance(0);assert.equal(ui.readCount(),1);
 replies.shift()([job('A','Visible earlier A')]);await ui.advance(0);assert.match(ui.history.innerHTML,/Visible earlier A/);
 ui.element('nativeRefreshJobs').onclick();await ui.advance(0);assert.equal(ui.readCount(),2);
 ui.changeScope(2,'B');assert.equal(ui.history.innerHTML,'');
 ui.changeScope(3,'A');
 ui.element('nativeRefreshJobs').onclick();await ui.advance(0);assert.equal(ui.readCount(),2);
 replies.shift()([job('A','Private earlier A')]);await ui.advance(0);
 assert.equal(ui.readCount(),3);assert.doesNotMatch(ui.history.innerHTML||'',/Private earlier A/);
 replies.shift()([job('B','Private wrong-profile B'),job('A','Current A receipt')]);await ui.advance(0);
 assert.match(ui.history.innerHTML,/Current A receipt/);
 assert.doesNotMatch(ui.history.innerHTML,/Private/);
});
