(() => {
 const clone=value=>structuredClone(value),listeners=new Map();
 const f=window.__INSTANCES_FIX__={state:__INSTANCES_INITIAL_STATE__,calls:[],defer:{},pending:[],blocked:[],tokens:new Map(),serial:0,failAfterInstall:false};
 f.snapshot=()=>clone(f.state);
 f.publish=patch=>{if(patch)Object.assign(f.state,clone(patch));f.state.revision++;for(const callback of listeners.get('modhunter-instances-state')||[])callback({payload:f.snapshot()})};
 f.publishRaw=state=>{for(const callback of listeners.get('modhunter-instances-state')||[])callback({payload:clone(state)})};
 f.resolve=kind=>{const pending=f.pending.find(row=>row.kind===kind);if(!pending)throw Error('No deferred '+kind);f.pending.splice(f.pending.indexOf(pending),1);pending.resolve()};
 f.completeInstall=(statuses=['updated','installed'])=>{f.state.install.status='complete';f.state.install.completed=f.state.install.total;f.state.install.currentId=null;f.state.install.results.forEach((row,index)=>{row.status=statuses[index]||'installed';row.message='Verified plugin files installed.';row.version=f.state.version;row.previousVersion=index===0?'0.12.0':null;row.pluginDirectory=f.state.inventory.installations.find(app=>app.id===row.installationId).directory+'/plugins/mo2_update_arsenal';row.backupDirectory=index===0?'D:/MO2/Aurora/ModhunterBackups/exact-receipt':null;row.preservedData=index===0;row.rollback='not_needed'});f.publish()};
 const finish=extra=>({ok:true,state:f.snapshot(),...extra});
 const exactKeys=(value,keys)=>{if(Object.keys(value).sort().join(',')!==[...keys].sort().join(','))throw Error('Unexpected renderer authority: '+JSON.stringify(value))};
 window.__TAURI__={event:{listen:async(name,callback)=>{const rows=listeners.get(name)||[];rows.push(callback);listeners.set(name,rows);return()=>listeners.set(name,rows.filter(row=>row!==callback))}},core:{invoke:async(name,args)=>{
  if(name!=='instances_command')throw Error('Wrong privileged command');
  const {kind,value={}}=args;f.calls.push(clone({kind,value}));
  if(f.defer[kind])await new Promise(resolve=>f.pending.push({kind,resolve}));
  if(kind==='state')return finish();
  if(kind==='scan'){
   exactKeys(value,value.mode==='deep'?['mode','roots']:['mode']);
   if(value.mode==='deep'&&value.roots.some(path=>!f.state.roots.some(root=>root.path===path)))throw Error('Unknown native discovery root');
   f.state.scan={id:'scan-'+(++f.serial),status:'running',mode:value.mode,directoriesVisited:0,installationsFound:0,instancesFound:0,currentPath:'',roots:value.roots||[],elapsedMs:0,message:'Read-only fixture discovery.'};f.publish();return finish();
  }
  if(kind==='cancelScan'){exactKeys(value,['operationId']);if(value.operationId!==f.state.scan.id)throw Error('Wrong scan operation');f.state.scan.status='cancelling';f.publish();return finish()}
  if(kind==='addFolder'){exactKeys(value,[]);return finish()}
  if(kind==='previewInstall'){
   exactKeys(value,['installationIds']);
   const targets=value.installationIds.map(id=>{const app=f.state.inventory.installations.find(row=>row.id===id);if(!app)throw Error('Unknown installation');return {installationId:id,directory:app.directory,pluginDirectory:app.directory+'/plugins/mo2_update_arsenal',backupRoot:app.directory+'/ModhunterBackups',previousVersion:app.plugin.version||null,version:f.state.version,action:app.plugin.status==='missing'?'install':'update',canInstall:!f.blocked.includes(id),blockers:f.blocked.includes(id)?['Close this MO2 installation before replacing its plugin.']:[],affectedInstances:f.state.inventory.associations.filter(row=>row.installationId===id).map(row=>{const instance=f.state.inventory.instances.find(item=>item.id===row.instanceId);return {id:instance.id,name:instance.name,kind:instance.kind,relation:row.kind}}),message:'Exact native preflight fixture.'}});
   const preview={token:'preview-'+(++f.serial),expiresAt:Date.now()+(f.previewLifetime??60000),package:{version:f.state.version,commit:'a'.repeat(40)},canInstall:targets.every(row=>row.canInstall),targets};f.tokens.set(preview.token,clone(preview));return finish({preview});
  }
  if(kind==='install'){
   exactKeys(value,['token']);const preview=f.tokens.get(value.token);f.tokens.delete(value.token);if(!preview||!preview.canInstall||preview.expiresAt<=Date.now())throw Error('Expired or already used native preview');
   f.state.install={id:'install-'+(++f.serial),status:'running',total:preview.targets.length,completed:0,currentId:preview.targets[0].installationId,stopRequested:false,results:preview.targets.map((row,index)=>({installationId:row.installationId,status:index?'queued':'installing',message:index?'Waiting for the current transaction.':'Verifying and staging the plugin.'}))};f.publish();if(f.failAfterInstall)throw Error('The native reply was interrupted. Check the operation result.');return finish();
  }
  if(kind==='cancelInstall'){exactKeys(value,['operationId']);if(value.operationId!==f.state.install.id)throw Error('Wrong install operation');f.state.install.stopRequested=true;f.state.install.status='stopping';f.publish();return finish()}
  if(kind==='connect'){exactKeys(value,['sessionId']);if(!f.state.sessions.some(row=>row.id===value.sessionId))throw Error('Unknown live session');return finish()}
  if(kind==='launch'||kind==='openFolder'){exactKeys(value,value.instanceId?['installationId','instanceId']:['installationId']);if(!f.state.inventory.installations.some(row=>row.id===value.installationId))throw Error('Unknown application');if(value.instanceId&&!f.state.inventory.associations.some(row=>row.installationId===value.installationId&&row.instanceId===value.instanceId))throw Error('Unassociated instance');return finish()}
  throw Error('Unhandled fixture command '+kind);
 }}};
})();
