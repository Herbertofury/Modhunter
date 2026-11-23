'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const test=require('node:test');
const vm=require('node:vm');
const app=fs.readFileSync(path.join(__dirname,'../mo2_update_arsenal/web/app.js'),'utf8');
const start=app.indexOf('function setHTML('),end=app.indexOf('function sourceLinksPending()',start);
assert.ok(start>=0&&end>start);
const live=app.slice(start,end);
function owned({id='modList',support=true,connected=true}={}){
 const operations=[],document={activeElement:null,documentElement:{lang:'en'},getAnimations:()=>[]},movedCycles=new Set();
 function row(key,html){
  const value={id:key.startsWith('mod:')?'row-'+key.slice(4):key,dataset:{id:key.slice(4)},parentElement:null,
   classList:{contains:name=>name==='mod-row',length:1},querySelector:()=>null,querySelectorAll:()=>[],html,selection:'selection-state',animation:119};
  value.input={parentElement:value,focus(opts){operations.push(['focus',key,opts?.preventScroll]);document.activeElement=this;},
   hasAttribute(){return false;}};
  Object.defineProperties(value,{nextElementSibling:{get(){const p=value.parentElement;return p?.children[p.children.indexOf(value)+1]??null;}},
   isConnected:{get(){return Boolean(value.parentElement?.isConnected);}}});
  Object.defineProperty(value.input,'isConnected',{get(){return value.isConnected;}});
  value.contains=x=>x===value.input;
  value.remove=()=>{if(value.parentElement){operations.push(['remove',key]);unlink(value,false);}};
  value.replaceWith=other=>{const p=value.parentElement,index=p.children.indexOf(value);unlink(other,false);unlink(value,false);p.children.splice(index,0,other);other.parentElement=p;operations.push(['replace',key]);};
  return value;
 }
 function unlink(node,atomic){
  if(!node.parentElement)return;
  const p=node.parentElement,index=p.children.indexOf(node);assert.ok(index>=0);
  p.children.splice(index,1);node.parentElement=null;
  if(!atomic&&p.isConnected&&p===list){
   if(document.activeElement===node.input)document.activeElement=null;
   node.selection=null;node.animation=0;
  }
 }
 function container(name,isConnected){
  const c={id:name,isConnected,children:[],querySelector:()=>null,contains(x){return this.children.some(child=>child.contains(x));},
   insertBefore(node,cursor){operations.push(['legacy',node.id]);move(node,cursor,false);},
   replaceChildren(part){operations.push(['bulk',part.children.length]);for(const x of [...c.children])unlink(x,false);for(const x of [...part.children])move(x,null,false);},
   append(node){move(node,null,false);}};
  const move=(node,cursor,atomic)=>{unlink(node,atomic);const at=cursor===null?c.children.length:c.children.indexOf(cursor);assert.ok(at>=0);c.children.splice(at,0,node);node.parentElement=c;};
  if(support)c.moveBefore=(node,cursor)=>{
   operations.push(['atomic',node.id]);
   if(movedCycles.has('error'))throw Object.assign(new Error('owned mock rejection'),{name:'HierarchyRequestError'});
   if(movedCycles.has('unexpected'))throw new Error('owned unexpected failure');
   assert.equal(node.parentElement,c);assert.ok(c.isConnected&&node.isConnected);move(node,cursor,true);
  };
  Object.defineProperties(c,{firstElementChild:{get(){return c.children[0]??null;}},
   innerHTML:{set(html){for(const r of [...c.children])unlink(r,false);
    for(const m of html.matchAll(/<article data-key="([^"]+)">([^<]*)<\/article>/g))move(row(m[1],m[0]),null,false);
   }}});
  return c;
 }
 const list=container(id,connected);
 document.createElement=tag=>{
  assert.equal(tag,'template');
  const content=container('template',false);
  return {content,set innerHTML(v){content.innerHTML=v;}};
 };
 document.createDocumentFragment=()=>container('fragment',false);
 const epoch={PROFILE_EPOCH:1,CATALOG_RENDER_REVISION:1,DATA:{profileId:'profile-a'},document,
  HTML_CACHE:new WeakMap(),ROW_RENDER_CACHE:new WeakMap(),LIBRARY_DETACHED_ROWS:new Map(),
  LIBRARY_ROW_CACHE_OWNER:'',LIBRARY_ROW_CACHE_LIST:null,ITEM_MAP:new Map(),focusedRowId:'',NATIVE_SELECTED:'',CONTEXT_MOD:'',openedId:'',
  classFor:()=>''};
 const ctx=vm.createContext(epoch);vm.runInContext(live,ctx);
 const entry=(key,html='unchanged')=>({key:'mod:'+key,html:'<article data-key="mod:'+key+'">'+html+'</article>'});
 const draw=(records,preserveMoves=id==='liveDownloadList')=>{
  const items=records.map(x=>Array.isArray(x)?entry(...x):entry(x));for(const r of items)epoch.ITEM_MAP.set(r.key.slice(4),{});
  ctx.setRows(list,items,'',preserveMoves);
 };
 const state=()=>list.children.map(x=>x.id);
 return {epoch,operations,list,state,draw,movedCycles,document,focus:key=>{document.activeElement=list.children.find(r=>r.id==='row-'+key).input;}};
}
for(const kind of ['modList','liveDownloadList']){
 test(kind+': current keyed move retains original focused control, selection and animation',()=>{
  const w=owned({id:kind});w.draw(['a','b','c']);
  const [a,b,c]=w.list.children;w.focus('c');
  w.draw(['c','a','b']);
  assert.deepEqual(w.state(),['row-c','row-a','row-b']);
  assert.equal(w.list.children[0],c);assert.equal(w.list.children[1],a);assert.equal(w.list.children[2],b);
  assert.deepEqual(w.operations,[['atomic','row-c']]);
  assert.equal(w.document.activeElement,c.input);assert.equal(c.selection,'selection-state');assert.equal(c.animation,119);
 });
 test(kind+': unsupported/rejected atomic retains legacy order and focused-control repair',()=>{
  for(const support of [false,true]){
   const w=owned({id:kind,support});w.draw(['a','b','c']);w.focus('c');
   const oldActive=w.document.activeElement;w.movedCycles.add('error');w.draw(['c','a','b']);
   assert.deepEqual(w.operations,[...(support?[['atomic','row-c']]:[]),['legacy','row-c'],['focus','mod:c',true]]);
   assert.equal(w.document.activeElement,oldActive);
   assert.deepEqual(w.state(),['row-c','row-a','row-b']);
  }
 });
 test(kind+': disconnected parent or stale epoch use legacy move; a new owner can move only subsequently',()=>{
  const disconnected=owned({id:kind,connected:false});disconnected.draw(['a','b','c']);disconnected.draw(['c','a','b']);
  assert.deepEqual(disconnected.operations,[['legacy','row-c']]);
  const ownedCurrent=owned({id:kind});ownedCurrent.draw(['a','b','c']);ownedCurrent.epoch.PROFILE_EPOCH++;
  ownedCurrent.draw(['c','a','b']);assert.deepEqual(ownedCurrent.operations,[['legacy','row-c']]);
  ownedCurrent.operations.length=0;ownedCurrent.draw(['b','c','a']);assert.deepEqual(ownedCurrent.operations,[['atomic','row-b']]);
 });
 test(kind+': new, replaced and previously detached nodes stay on existing ordinary paths',()=>{
  const w=owned({id:kind});w.draw(['a','b','c']);const c=w.list.children[2];
  w.draw([['c','changed'],'a','d','b']);
  assert.deepEqual(w.state(),['row-c','row-a','row-d','row-b']);
  assert.notEqual(w.list.children[0],c);assert.deepEqual(w.operations,
   kind==='modList'?[['remove','mod:c'],['legacy','row-c'],['legacy','row-d']]:[['replace','mod:c'],['legacy','row-c'],['legacy','row-d']]);
  w.operations.length=0;
  const prior=w.list.children[2];w.draw(['a','b']);w.operations.length=0;w.draw(['d','a','b']);
  assert.deepEqual(w.operations,[['legacy','row-d']]);assert.equal(w.list.children[0]===prior,kind==='modList');
 });
}
test('library catalog/filter owner fence and bulk contraction do not move via new API',()=>{
 const w=owned();w.draw(['a','b','c']);w.epoch.CATALOG_RENDER_REVISION++;
 w.draw(['c','a','b']);assert.deepEqual(w.operations,[['legacy','row-c']]);w.operations.length=0;
 w.document.documentElement.lang='fr';w.draw(['b','a','c']);
 assert.deepEqual(w.operations,[['legacy','row-b'],['legacy','row-a']]);
 const many=owned();const items=Array.from({length:120},(_,i)=>String(i));many.draw(items);
 many.draw(items.slice(100));
 assert.equal(many.operations.some(([x])=>x==='atomic'),false);
 assert.deepEqual(many.operations.find(([x])=>x==='bulk'),['bulk',20]);
 assert.deepEqual(many.state(),items.slice(100).map(id=>'row-'+id));
});
test('do not hide unexpected engine/host exceptions',()=>{
 const w=owned();w.draw(['a','b','c']);w.movedCycles.add('unexpected');
 assert.throws(()=>w.draw(['c','a','b']),/unexpected failure/);
 assert.deepEqual(w.operations,[['atomic','row-c']]);assert.deepEqual(w.state(),['row-a','row-b','row-c']);
});
