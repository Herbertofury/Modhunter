'use strict';
/** Equivalent-work benchmark for full-list MO2 download-ticket joins.
 * No viewport culling, result cap, removed rows, or artificial data reduction.
 * Pass/fail checks semantic equivalence; times are informative, not flaky gates.
 */
const {performance}=require('node:perf_hooks');
const assert=require('node:assert/strict');
const mods=Array.from({length:2500},(_,i)=>({id:'mod-'+i}));
const tickets=Object.fromEntries(Array.from({length:800},(_,i)=>[i+200,{
  itemId:'mod-'+(i*3),status:i%71===0?'failed':'complete'
}]));
function before(){return mods.map(m=>Object.values(tickets).reverse()
  .find(x=>x.itemId===m.id&&!['failed','removed'].includes(x.status))?.itemId||null);}
function after(){const byMod=new Map();for(const ticket of Object.values(tickets)){
 if(ticket?.itemId&&!['failed','removed'].includes(ticket.status))byMod.set(ticket.itemId,ticket);
}return mods.map(m=>byMod.get(m.id)?.itemId||null);}
assert.deepEqual(after(),before(),'Indexed ticket join changed full-list results');
function bench(fn){const times=[];for(let i=0;i<7;i++){
 const t=performance.now();const output=fn();assert.equal(output.length,mods.length);
 times.push(performance.now()-t);
}return times.sort((a,b)=>a-b)[3];}
const baseline=bench(before),indexed=bench(after);
console.log(JSON.stringify({installedMods:mods.length,tickets:Object.keys(tickets).length,
 equivalent:true,baselineMedianMs:+baseline.toFixed(3),indexedMedianMs:+indexed.toFixed(3),
 speedup:+(baseline/indexed).toFixed(1)},null,2));
