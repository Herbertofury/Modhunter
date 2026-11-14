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
 replaceChildren(...children){this.children=[...children]}
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
