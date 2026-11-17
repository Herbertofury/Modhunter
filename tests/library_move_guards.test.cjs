'use strict';
// The diagnostic passes its exact injected helper source on stdin. These are
// ownership guard tests; actual Chromium focus/range/animation tests are separate.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(0,'utf8');
let activeSelection;
const context=vm.createContext({window:{getSelection:()=>activeSelection}});
vm.runInContext(source+'\nthis.api={captureLibraryTextSelection,restoreLibraryTextSelection};',context);
const api=context.api,checks=[];
function fixture(){
 const list={},children=new Set(),row={nodeType:1,isConnected:true,parentElement:list,contains:node=>children.has(node),closest:()=>row,getBoundingClientRect(){throw Error('No layout reads permitted')}};
 const anchor={nodeType:3,isConnected:true,parentElement:row,length:20},focus={nodeType:3,isConnected:true,parentElement:row,length:12};children.add(anchor);children.add(focus);
 const calls=[],selection={rangeCount:1,isCollapsed:false,anchorNode:anchor,anchorOffset:14,focusNode:focus,focusOffset:2,
  setBaseAndExtent(a,ao,f,fo){calls.push([a,ao,f,fo]);Object.assign(this,{anchorNode:a,anchorOffset:ao,focusNode:f,focusOffset:fo})}};
 activeSelection=selection;
 return {list,row,children,anchor,focus,calls,selection,retained:new Set([row]),capture(){return api.captureLibraryTextSelection(list,this.retained)},lose(){Object.assign(selection,{anchorNode:list,anchorOffset:0,focusNode:list,focusOffset:0})}};
}
function check(name,run){run();checks.push({name,passed:true})}
check('backward same-row endpoints and offsets restore only after a change',()=>{
 const f=fixture();f.selection.focusNode=f.anchor;f.selection.focusOffset=2;
 const saved=f.capture();assert.ok(saved);api.restoreLibraryTextSelection(f.list,saved);assert.equal(f.calls.length,0);
 f.lose();api.restoreLibraryTextSelection(f.list,saved);assert.deepEqual(f.calls,[[f.anchor,14,f.anchor,2]]);
 api.restoreLibraryTextSelection(f.list,saved);assert.equal(f.calls.length,1);
});
check('collapsed and multiple ranges are left alone',()=>{
 const f=fixture();f.selection.isCollapsed=true;assert.equal(f.capture(),null);f.selection.isCollapsed=false;f.selection.rangeCount=2;assert.equal(f.capture(),null);
});
check('outside or nonretained row selections are left alone',()=>{
 const f=fixture();f.retained.clear();assert.equal(f.capture(),null);f.retained.add(f.row);f.row.parentElement={};assert.equal(f.capture(),null);
});
check('cross-row ranges are not synthesized after sorting',()=>{
 const f=fixture(),other={nodeType:1,isConnected:true,parentElement:f.list,closest:()=>other};
 f.focus.parentElement=other;f.retained.add(other);assert.equal(f.capture(),null);
});
check('detached row or endpoint cannot be restored',()=>{
 const f=fixture(),saved=f.capture();f.lose();f.anchor.isConnected=false;api.restoreLibraryTextSelection(f.list,saved);assert.equal(f.calls.length,0);
 f.anchor.isConnected=true;f.row.isConnected=false;api.restoreLibraryTextSelection(f.list,saved);assert.equal(f.calls.length,0);
 Object.assign(f.selection,{anchorNode:f.anchor,anchorOffset:14,focusNode:f.focus,focusOffset:2});assert.equal(f.capture(),null);
});
check('replaced endpoints are never redirected to lookalike nodes',()=>{
 const f=fixture(),saved=f.capture();f.lose();f.children.delete(f.anchor);f.children.add({...f.anchor});api.restoreLibraryTextSelection(f.list,saved);assert.equal(f.calls.length,0);
});
check('a retained row moved outside its original library is not restored',()=>{
 const f=fixture(),saved=f.capture();f.lose();f.row.parentElement={};api.restoreLibraryTextSelection(f.list,saved);assert.equal(f.calls.length,0);
});
check('shortened endpoints cannot receive an invalid offset',()=>{
 const f=fixture(),saved=f.capture();f.lose();f.anchor.length=3;api.restoreLibraryTextSelection(f.list,saved);assert.equal(f.calls.length,0);
});
check('missing selection restoration support leaves the original path intact',()=>{
 const f=fixture();f.selection.setBaseAndExtent=undefined;assert.equal(f.capture(),null);activeSelection=null;assert.equal(f.capture(),null);
});
process.stdout.write(JSON.stringify({ok:true,node:process.version,checks}));
