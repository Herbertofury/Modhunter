'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {deflateSync}=require('node:zlib');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const {spawnSync}=require('node:child_process');
const {sha,decodeOwnedScreenshot}=require('./carrier.cjs');
const {pixelWitness,compareAB,validateFirst,validateFull,memory,exactAX}=require('./accept.cjs');
const here=n=>join(__dirname,n);
function png(rgb,second){
 // Synthetic producer suffices to prove pixel extraction, not real screenshot delivery.
 const header=Buffer.alloc(13);header.writeUInt32BE(2,0);header.writeUInt32BE(1,4);header[8]=8;header[9]=2;
 const chunk=(str,b)=>{const c=Buffer.alloc(12+b.length);c.writeUInt32BE(b.length,0);c.write(str,4,'ascii');c.set(b,8);return c;};
 return Buffer.concat([Buffer.from('89504e470d0a1a0a','hex'),chunk('IHDR',header),
  chunk('IDAT',deflateSync(Buffer.from([0,...Buffer.from(rgb+second,'hex')]))),chunk('IEND',Buffer.alloc(0))]);
}
test('no detached browser driver invocation without explicit separate later authorization',()=>{
 const env={...process.env};delete env.MODHUNTER_DETACHED_BROWSER_ACCEPTANCE;
 const r=spawnSync(process.execPath,[here('accept.cjs')],{env,encoding:'utf8'});
 assert.equal(r.status,1);assert.match(r.stderr,/without separately authorized disposable-host opt-in/);
});
test('exact pinned geometry module and exact nontrivial section scope',()=>{
 assert.equal(sha(readFileSync(here('warm-geometry.mjs'))),'e93d1c1776ac1d473f7c3785c6949e17c432d13f290d1b96767baff4ab16e40e');
 let n=0,chapter=0,previous,last=11207-3;
 while(n<11207){previous=n; n+=n<last?Math.min(last-n,137+(chapter%7)*23):11207-n;chapter++;}
 assert.deepEqual([n,chapter,n-previous],[11207,56,3]);
});
test('pixel proof rejects unchanged marker, background-only and broken capture',()=>{
 const points=[{x:1,y:0}]; const m={x:0,y:0};
 assert.equal(pixelWitness(png('13795b','6c4383'),points,m).markerColor,'13795b');
 assert.throws(()=>pixelWitness(png('383950','6c4383'),points,m),/never changed/);
 assert.throws(()=>pixelWitness(png('13795b','0e1017'),points,m),/no distinct visible tile pixels/);
 assert.throws(()=>decodeOwnedScreenshot(png('13795b','0e1017').subarray(0,22)),/truncated/);
});
const first={hits:[{x:20,y:12}],retained:11263,mode:'priority',focusId:'enter',
 modelStage:'first-visible-proxy',hints:11249,reason:null,frameBoundaryAt:6,requestedAt:5};
const full={phase:'full-ready',cards:11207,sections:56,count:11263,selected:11263,hints:0,fallbackUsed:false,focused:true,
 completeLayout:true,noOptimizationHints:true,noHidden:true,sameDom:true,findable:true,allActions:true,stableMeasurement:true,
 geometryDigest:'a'.repeat(64),contentDigest:'b'.repeat(64),distinctCardHeights:300,lastBandCards:3,
 firstCardColumns:4,viewportWidth:987.25,cleanupAt:20,completeLayoutAt:25,auditEndAt:45,extent:11628,scroll:3041};
test('first viewport evidence is not accepted as complete; all-card and no-stale guard fail closed',()=>{
 assert.doesNotThrow(()=>validateFirst(first,true)); assert.throws(()=>validateFull(first),/full-ready/);
 assert.doesNotThrow(()=>validateFull(full));assert.throws(()=>validateFull({...full,count:11262}));
 assert.throws(()=>validateFull({...full,hints:1}));assert.throws(()=>validateFull({...full,stableMeasurement:false}));
 assert.throws(()=>validateFull({...full,fallbackUsed:true}),/priority rejected/);
 assert.doesNotThrow(()=>validateFirst({...first,mode:'ordinary',modelStage:'ordinary',reason:'key-or-anchor-not-current',hints:0},false,{reason:'key-or-anchor-not-current'}));
 assert.throws(()=>validateFirst({...first,mode:'ordinary',modelStage:'ordinary',reason:'key-or-anchor-not-current',hints:1},false,{reason:'key-or-anchor-not-current'}));
});
test('A/B cannot pass if model digest, last band or rendered pixels diverge',()=>{
 const a={firstPixels:{digest:'c'},completePixels:{digest:'d'},full};
 assert.doesNotThrow(()=>compareAB(a,a));
 assert.throws(()=>compareAB(a,{...a,full:{...full,geometryDigest:'z'}}),/geometryDigest/);
 assert.throws(()=>compareAB(a,{...a,full:{...full,lastBandCards:2}}),/lastBandCards/);
 assert.throws(()=>compareAB(a,{...a,firstPixels:{digest:'e'}}),/first captured viewport/);
 assert.throws(()=>compareAB(a,{...a,completePixels:{digest:'e'}}),/complete captured viewport/);
});
test('CDP helpers use actual target backend-id/role/name, keep missing probe distinct',async()=>{
 const mocked={
  'DOM.getDocument':{root:{nodeId:1}},'DOM.querySelector':{nodeId:2},'DOM.describeNode':{node:{backendNodeId:3}},
  'Accessibility.getPartialAXTree':{nodes:[{backendDOMNodeId:3,role:{value:'link'},name:{value:'tail'}}]},
  'Runtime.getHeapUsage':{usedSize:100,totalSize:200},'Memory.getDOMCounters':{documents:1,nodes:400,jsEventListeners:7},
 };
 const session={send:async(name,input)=>{
  if(name==='Accessibility.getPartialAXTree')assert.deepEqual(input,{backendNodeId:3,fetchRelatives:false});
  return mocked[name];
 }};
 assert.equal((await exactAX(session,'mod-11206-title','tail')).status,'pass');
 assert.equal((await exactAX(session,'mod-11206-title','guess')).status,'fail');
 assert.equal((await memory(session)).dom.nodes,400);
 assert.equal((await memory({send:()=>Promise.reject(Error('unsupported probe'))})).heap.status,'unavailable');
 assert.equal((await exactAX({send:()=>Promise.reject(Error("method wasn't found"))},'id','title')).status,'unavailable');
});
