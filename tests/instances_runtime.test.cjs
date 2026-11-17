'use strict';
const assert=require('node:assert/strict');
const test=require('node:test');
const model=require('../native/ui/instances.js');

function snapshot(){
 return {revision:10,inventory:{complete:true,scope:'known',installations:[{id:'app-a',directory:'D:/MO2/Aurora',executable:'D:/MO2/Aurora/ModOrganizer.exe',plugin:{status:'missing'},runningPids:[]},{id:'app-b',directory:'E:/MO2/Ranger',executable:'E:/MO2/Ranger/ModOrganizer.exe',plugin:{status:'matching',version:'0.13.0'},runningPids:[123]}],instances:[{id:'global',name:'Shared setup',kind:'global',configPath:'C:/Users/Fixture/AppData/Local/ModOrganizer/Shared/ModOrganizer.ini',gameName:'Skyrim Special Edition',profiles:Array.from({length:1205},(_,i)=>({name:'Profile '+i,path:'F:/Profiles/'+i}))},{id:'portable',name:'Portable',kind:'portable',configPath:'D:/MO2/Aurora/ModOrganizer.ini',gameName:'Fallout 4',profiles:[]}],associations:[{installationId:'app-a',instanceId:'global',kind:'availableGlobal'},{installationId:'app-b',instanceId:'global',kind:'availableGlobal'},{installationId:'app-a',instanceId:'portable',kind:'portable'}],issues:[]},scan:{status:'complete'}};
}
test('shared global instances remain one identity and never become extra installation targets',()=>{
 const inventory=model.mergeInventory(snapshot());
 assert.equal(inventory.installations.length,2);assert.equal(inventory.instances.length,2);
 assert.equal(model.relatedInstances('app-a',inventory).length,2);
 const shared=model.relatedInstances('app-b',inventory)[0];
 assert.equal(shared.instance.id,'global');assert.equal(shared.relations[0].kind,'availableGlobal');
 assert.equal(shared.instance.profiles.length,1205);
});
test('search reaches the final profile and split paths without dropping any records',()=>{
 const inventory=model.mergeInventory(snapshot()),app=inventory.installations[0];
 assert(model.matchesInstallation(app,inventory,'profile 1204','all'));
 assert(model.matchesInstallation(app,inventory,'f:/profiles/1204','missing'));
 assert(!model.matchesInstallation(app,inventory,'profile 1204','matching'));
 assert(model.matchesInstallation(inventory.installations[1],inventory,'','running'));
 assert.equal(inventory.instances[0].profiles.length,1205);
});
test('partial progress adds findings while retaining the complete inventory and ownership',()=>{
 const state=snapshot();
 state.scan={id:'scan-new',status:'running',partialInventory:{installations:[{id:'app-a',directory:'UNCONFIRMED CHANGE'},{id:'app-c',directory:'G:/NewMO2'}],instances:[{id:'new-instance',name:'New',profiles:[]}],associations:[{installationId:'app-c',instanceId:'new-instance',kind:'portable'}],issues:[{kind:'access',path:'G:/Private',message:'Access denied'}]}};
 const merged=model.mergeInventory(state);
 assert.equal(merged.installations.length,3);assert.equal(merged.installations[0].directory,'D:/MO2/Aurora');
 assert.deepEqual([...merged.partialIds],['app-c']);assert.equal(merged.associations.length,4);
 assert.equal(merged.issues[0].path,'G:/Private');assert.equal(merged.instances[0].profiles.length,1205);
 // Host acceptance after cancel preserves the finding, making it reviewable.
 state.inventory={...merged,complete:false};state.scan.status='cancelled';
 assert.equal(model.mergeInventory(state).partialIds.size,0);
});
test('multiple evidence relations retain their meaning without duplicating instance rows',()=>{
 const state=snapshot();state.inventory.associations.push({installationId:'app-a',instanceId:'global',kind:'shortcut',evidence:'A real shortcut'});
 const rows=model.relatedInstances('app-a',model.mergeInventory(state));
 assert.equal(rows.length,2);assert.deepEqual(rows[0].relations.map(row=>row.kind),['availableGlobal','shortcut']);
});
test('installation authority requires an unexpired exact complete preview with no blocked targets',()=>{
 const now=1_000_000,ids=['app-a','app-b'];
 const preview={token:'native-only-token',expiresAt:now+1000,canInstall:true,targets:ids.map(installationId=>({installationId,canInstall:true,blockers:[]}))};
 assert(model.exactPreview(preview,ids,now));assert(model.exactPreview(preview,[...ids].reverse(),now));
 for(const changed of [{...preview,token:''},{...preview,expiresAt:now},{...preview,canInstall:false},{...preview,targets:preview.targets.slice(0,1)},{...preview,targets:[...preview.targets,preview.targets[0]]},{...preview,targets:[preview.targets[0],{...preview.targets[1],blockers:['MO2 is running']}]},{...preview,targets:[preview.targets[0],{...preview.targets[1],canInstall:false}]}])assert.equal(model.exactPreview(changed,ids,now),false);
 assert.equal(model.exactPreview(preview,['app-a','unreviewed-app'],now),false);
});
test('stale or malformed snapshots cannot replace a newer operation result',()=>{
 const current={revision:20};
 assert.equal(model.acceptsRevision(current,{revision:19}),false);
 assert.equal(model.acceptsRevision(current,{}),false);
 assert.equal(model.acceptsRevision(current,{revision:'21'}),false);
 assert.equal(model.acceptsRevision(current,{revision:20}),true);
 assert.equal(model.acceptsRevision(current,{revision:21}),true);
});
test('process refresh keeps a pending authoritative preflight but invalidates an already shown preview',()=>{
 const before=snapshot(),after=structuredClone(before);
 after.inventory.installations[1].runningPids=[];
 assert.equal(model.previewIdentityChanged(before,after,true),false);
 assert.equal(model.previewIdentityChanged(before,after,false),true);
 assert.equal(model.previewIdentityChanged(after,after,true),false);
});
test('pending preflight never bypasses target path, executable, plugin or package identity changes',()=>{
 const before=snapshot();before.package={available:true,valid:true,version:'0.13.0',commit:'a'.repeat(40)};
 for(const mutate of [
  state=>state.inventory.installations[0].id='another-app',
  state=>state.inventory.installations[0].directory='D:/Different MO2',
  state=>state.inventory.installations[0].executable='D:/Different MO2/ModOrganizer.exe',
  state=>state.inventory.installations[0].plugin={status:'different',version:'0.12.1'},
  state=>state.package.available=false,
  state=>state.package.valid=false,
  state=>state.package.version='0.14.0',
  state=>state.package.commit='b'.repeat(40),
 ]){
  const after=structuredClone(before);after.inventory.installations[1].runningPids=[];mutate(after);
  assert.equal(model.previewIdentityChanged(before,after,true),true);
  assert.equal(model.previewIdentityChanged(before,after,false),true);
 }
});
