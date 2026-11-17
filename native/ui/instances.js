'use strict';
(() => {
 const ACTIVE_SCAN=new Set(['running','cancelling']);
 const ACTIVE_INSTALL=new Set(['running','stopping']);
 const PLUGIN_LABELS={missing:'Plugin missing',matching:'Version matches',different:'Different version',unknown:'Unverified',invalid:'Needs review'};
 const RESULT_LABELS={queued:'Queued',installing:'Installing…',installed:'Installed',updated:'Updated',failed:'Failed',cancelled:'Canceled'};
 const RELATION_LABELS={portable:'Portable instance',shortcut:'Linked by shortcut',availableGlobal:'Shared global instance'};
 const list=value=>Array.isArray(value)?value:[];
 const count=value=>Math.max(0,Number(value)||0).toLocaleString();
 const leaf=value=>String(value||'').replace(/[\\/]+$/,'').split(/[\\/]/).pop()||'Mod Organizer 2';
 const selectionKey=ids=>[...ids].sort().join('\u0000');
 function mergeInventory(snapshot){
  const base=snapshot.inventory||{},partial=snapshot.scan?.partialInventory||{};
  const merge=(a,b,key)=>{const seen=new Set(list(a).map(key));return [...list(a),...list(b).filter(row=>{const id=key(row);if(seen.has(id))return false;seen.add(id);return true})]};
  const accepted=new Set(list(base.installations).map(row=>row.id));
  const installations=merge(base.installations,partial.installations,row=>row.id);
  const instances=merge(base.instances,partial.instances,row=>row.id);
  const associations=merge(base.associations,partial.associations,row=>[row.installationId,row.instanceId,row.kind].join('\u0000'));
  const issues=merge(base.issues,partial.issues,row=>[row.kind,row.path,row.message].join('\u0000'));
  return {installations,instances,associations,issues,partialIds:new Set(installations.filter(row=>!accepted.has(row.id)).map(row=>row.id)),complete:base.complete===true,scope:base.scope||''};
 }
 function relatedInstances(installationId,inventory){
  const byId=new Map(inventory.instances.map(row=>[row.id,row]));
  const rows=new Map();
  for(const relation of inventory.associations){
   if(relation.installationId!==installationId||!byId.has(relation.instanceId))continue;
   let row=rows.get(relation.instanceId);
   if(!row){row={instance:byId.get(relation.instanceId),relations:[]};rows.set(relation.instanceId,row)}
   row.relations.push(relation);
  }
  return [...rows.values()];
 }
 function instanceMatches(instance,query){
  if(!query)return true;
  return [instance.name,instance.configPath,instance.baseDirectory,instance.gameName,instance.gamePath,instance.selectedProfile,instance.profilesDirectory,instance.modsDirectory,...list(instance.profiles).flatMap(profile=>[profile.name,profile.path])].some(value=>String(value||'').toLocaleLowerCase().includes(query));
 }
 function matchesInstallation(installation,inventory,query,filter){
  const status=installation.plugin?.status||'unknown';
  if(filter==='running'&&!list(installation.runningPids).length)return false;
  if(filter==='attention'&&!['unknown','invalid'].includes(status))return false;
  if(!['all','running','attention'].includes(filter)&&filter!==status)return false;
  if(!query)return true;
  return [installation.directory,installation.executable,installation.version,installation.plugin?.version,...list(installation.sources)].some(value=>String(value||'').toLocaleLowerCase().includes(query))||relatedInstances(installation.id,inventory).some(row=>instanceMatches(row.instance,query));
 }
 function expiryMillis(value){if(typeof value==='number')return value;const number=Number(value);return Number.isFinite(number)&&number>0?number:Date.parse(value)}
 function exactPreview(preview,ids,now=Date.now()){
  const targets=list(preview?.targets),unique=new Set(targets.map(row=>row.installationId));
  return !!preview?.token&&preview.canInstall===true&&Number.isFinite(expiryMillis(preview.expiresAt))&&expiryMillis(preview.expiresAt)>now&&targets.length>0&&targets.length===unique.size&&selectionKey(unique)===selectionKey(ids)&&targets.every(row=>row.canInstall===true&&!list(row.blockers).length);
 }
 function acceptsRevision(current,next){return Number.isFinite(next?.revision)&&(!Number.isFinite(current?.revision)||next.revision>=current.revision)}
 function previewIdentityChanged(prior,next,pendingPreflight=false){
  // Preflight refreshes running processes before returning its authoritative
  // result. That observation must not discard its own pending reply. A shown
  // preview still expires on process changes; native commit rechecks them too.
  const identity=snapshot=>JSON.stringify([snapshot.package?.available,snapshot.package?.valid,snapshot.package?.version,snapshot.package?.commit,list(snapshot.inventory?.installations).map(row=>[row.id,row.directory,row.executable,row.plugin,pendingPreflight?null:row.runningPids])]);
  return identity(prior)!==identity(next);
 }
 const model={mergeInventory,relatedInstances,instanceMatches,matchesInstallation,expiryMillis,exactPreview,acceptsRevision,previewIdentityChanged,selectionKey};
 if(typeof module==='object'&&module.exports){module.exports=model;return}
 if(typeof document==='undefined')return;

 const $=id=>document.getElementById(id),api=window.__TAURI__;
 let state={revision:-1,inventory:{installations:[],instances:[],associations:[],issues:[],complete:false},scan:{status:'idle'},install:{status:'idle',results:[]},sessions:[]};
 let ready=false,disposed=false,inventory=mergeInventory(state),visible=[],listKey='',issueKey='',resultKey='',preview=null,previewGeneration=0,previewSelection='',previewSubmitted=false,previewTimer=0,dialogReturn=null,pollTimer=0,searchFrame=0;
 const selected=new Set(),busy=new Set(),openDetails=new Set(),unlisten=[];
 const el=(tag,className,text)=>{const node=document.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=String(text);return node};
 const raw=(tag,className,text)=>{const node=el(tag,className,text);node.dataset.noTranslate='';return node};
 const setText=(id,value)=>{const node=$(id),text=String(value??'');if(node.textContent!==text)node.textContent=text};
 const scanActive=()=>ACTIVE_SCAN.has(state.scan?.status);
 const installActive=()=>ACTIVE_INSTALL.has(state.install?.status);
 const frozen=()=>!ready||scanActive()||installActive()||busy.has('scan')||busy.has('addFolder')||busy.has('install');
 function chip(label,tone='neutral'){const node=el('span','status-chip',label);node.dataset.tone=tone;return node}
 function path(value,className='path'){return raw('code',className,value||'—')}
 function action(label,kind,value,key){
  const button=el('button','',label);button.type='button';button.dataset.focusKey=key||kind+':'+(value.installationId||value.sessionId||'');
  button.disabled=frozen()||busy.has(commandKey(kind,value));
  button.setAttribute('aria-busy',String(busy.has(commandKey(kind,value))));
  button.addEventListener('click',()=>void perform(kind,value));return button;
 }
 function feedback(message,tone='info'){$('feedback').hidden=!message;$('feedback').dataset.tone=tone;setText('feedbackText',message);if(tone==='error')$('feedbackText').setAttribute('data-i18n-feedback','');else $('feedbackText').removeAttribute('data-i18n-feedback')}
 function errorText(error){return typeof error==='string'?error:error?.message||String(error)}
 function commandKey(kind,value={}){return kind+(value.installationId||value.sessionId?':'+(value.installationId||value.sessionId):'')}
 async function command(kind,value={}){
  if(!ready||!api?.core?.invoke)throw Error('Open MO2 Instances from Modhunter.exe to use native discovery and installation.');
  const key=commandKey(kind,value);if(busy.has(key))throw Error('This action is already in progress.');
  busy.add(key);render();
  try{const result=await api.core.invoke('instances_command',{kind,value});if(result?.ok===false)throw Error(result.error||result.message||'The action could not be completed.');if(!disposed&&result?.state)accept(result.state);return result}
  finally{busy.delete(key);if(!disposed)render()}
 }
 async function perform(kind,value={}){
  feedback('');
  try{const result=await command(kind,value);if(result?.message)feedback(result.message);return result}
  catch(error){feedback(errorText(error),'error');return null}
 }
 function accept(next){
  if(disposed||!acceptsRevision(state,next))return false;
  const prior=state;
  state=next;inventory=mergeInventory(state);
  const acceptedIds=new Set(list(state.inventory?.installations).map(row=>row.id));
  let selectionChanged=false;for(const id of selected)if(!acceptedIds.has(id)){selected.delete(id);selectionChanged=true}
  if($('installPreview').open&&!previewSubmitted&&(selectionChanged||previewIdentityChanged(prior,next,busy.has('previewInstall')&&!preview)||(next.scan?.id!==prior.scan?.id&&ACTIVE_SCAN.has(next.scan?.status))))invalidatePreview('The package or selected installations changed. Check the targets again.');
  if(state.settings&&(state.settings.locale!==window.ModhunterAppearance?.locale||state.settings.theme!==window.ModhunterAppearance?.theme))window.ModhunterI18n?.apply(state.settings);
  render();return true;
 }
 function renderPackage(){
  const packageInfo=state.package||{},valid=packageInfo.available&&packageInfo.valid;
  $('packagePanel').dataset.state=valid?'ready':ready?'error':'checking';
  setText('packageVersion',packageInfo.version?'Bundled plugin · '+packageInfo.version:ready?'Bundled plugin unavailable':'Checking bundled plugin…');
  setText('packageMessage',packageInfo.message||(valid?'Package available. Every selected target is verified before installation.':'Use a complete Modhunter installation containing its bundled MO2 plugin.'));
  $('packageDetails').hidden=!packageInfo.commit;setText('packageCommit',packageInfo.commit||'');
 }
 function renderScan(){
  const scan=state.scan||{},active=scanActive();$('scanPanel').hidden=!scan.id&&scan.status==='idle';
  const labels={idle:'Discovery ready',running:scan.mode==='deep'?'Searching selected folders':'Scanning known locations',cancelling:'Canceling discovery…',complete:'Discovery complete',cancelled:'Discovery canceled',failed:'Discovery needs attention'};
  setText('scanHeading',labels[scan.status]||'Discovery ready');$('scanProgress').hidden=!active;
  setText('scanCounts',count(scan.directoriesVisited)+' folders checked · '+count(scan.installationsFound)+' installations · '+count(scan.instancesFound)+' instances');
  setText('scanCurrentPath',scan.currentPath||'');$('scanCurrentPath').hidden=!scan.currentPath;
  setText('scanMessage',scan.message||(scan.mode==='deep'?'Searching every subfolder in the selected locations.':'Known locations include saved roots and native installation hints.'));
  $('cancelScan').hidden=!active;$('cancelScan').disabled=scan.status==='cancelling'||busy.has('cancelScan');setText('cancelScan',scan.status==='cancelling'?'Canceling…':'Cancel scan');
 }
 function sessionsFor(installation){return list(state.sessions).filter(session=>session.installationId===installation.id)}
 function instanceRow(instance,relations=[],installation=null){
  const row=el('article','instance-row');row.dataset.instanceId=instance.id;
  const top=el('div','instance-top'),main=el('div','instance-identity'),name=el('div','instance-name');
  name.append(raw('strong','',instance.name||leaf(instance.configPath)));
  const kinds=[...new Set(relations.map(relation=>relation.kind))];
  const relationText=kinds.length?kinds.map(kind=>RELATION_LABELS[kind]||kind).join(' · '):instance.kind==='global'?'Global instance':instance.kind==='portable'?'Portable instance':'External instance';
  name.append(el('span','relation-label',relationText));main.append(name);
  const facts=el('div','instance-facts');
  if(instance.gameName){const item=el('span');item.append(el('span','','Game: '),raw('b','',instance.gameName));facts.append(item)}
  if(instance.selectedProfile){const item=el('span');item.append(el('span','','Selected profile: '),raw('b','',instance.selectedProfile));facts.append(item)}
  facts.append(el('span','',count(list(instance.profiles).length)+' profiles'));main.append(facts);top.append(main);
  if(installation){const actions=el('div','installation-actions');actions.append(action('Launch instance','launch',{installationId:installation.id,instanceId:instance.id},'launch-instance:'+installation.id+':'+instance.id));top.append(actions)}
  row.append(top,path(instance.configPath));
  if(list(instance.profiles).length){const profiles=el('ul','profile-list');profiles.setAttribute('aria-label','Instance profiles');for(const profile of instance.profiles){const item=el('li',profile.selected?'current':'');item.append(raw('span','',profile.name));item.title=profile.path||'';if(profile.selected)item.append(el('em','','Selected'));profiles.append(item)}row.append(profiles)}
  const paths=[['Instance data',instance.baseDirectory],['Game folder',instance.gamePath],['Profiles folder',instance.profilesDirectory],['Mods folder',instance.modsDirectory]].filter(([,value])=>value);
  if(paths.length){const details=el('details','instance-paths');details.dataset.detailKey='paths:'+(installation?.id||'unassociated')+':'+instance.id;details.open=openDetails.has(details.dataset.detailKey);details.append(el('summary','','Folder details'));const facts=el('dl');for(const [label,value] of paths){facts.append(el('dt','',label));const dd=el('dd');dd.append(path(value));facts.append(dd)}details.append(facts);rememberDetails(details);row.append(details)}
  return row;
 }
 function rememberDetails(node){node.addEventListener('toggle',()=>{if(!node.isConnected)return;if(node.open)openDetails.add(node.dataset.detailKey);else openDetails.delete(node.dataset.detailKey)})}
 function installationCard(installation){
  const card=el('article','installation-card'+(selected.has(installation.id)?' selected':''));card.dataset.installationId=installation.id;
  const head=el('div','installation-head'),label=el('label','installation-check'),input=el('input');input.type='checkbox';input.checked=selected.has(installation.id);input.dataset.installationSelect=installation.id;input.dataset.focusKey='select:'+installation.id;input.setAttribute('aria-label','Select installation: '+installation.directory);input.disabled=frozen()||inventory.partialIds.has(installation.id);input.addEventListener('change',()=>{if(input.checked)selected.add(installation.id);else selected.delete(installation.id);changedSelection()});label.append(input);
  const main=el('div','installation-main'),title=el('div','installation-title');title.append(raw('h3','',leaf(installation.directory)));
  const status=installation.plugin?.status||'unknown',tone=status==='matching'?'positive':status==='invalid'?'error':status==='missing'||status==='different'?'review':'neutral';title.append(chip(PLUGIN_LABELS[status]||'Unverified',tone));if(list(installation.runningPids).length)title.append(chip('MO2 running','info'));main.append(title,path(installation.executable));
  const meta=el('div','installation-meta');
  const version=el('span','version-pair');version.append(el('span','','MO2 '),raw('span','',installation.version||'—'));meta.append(version);
  const plugin=el('span','version-pair');plugin.append(el('span','','Modhunter '),raw('span','',installation.plugin?.version||'—'));meta.append(plugin);
  if(installation.architecture)meta.append(raw('span','',installation.architecture));
  if(installation.portableLocked)meta.append(el('span','','Portable instance locked'));main.append(meta);
  if(inventory.partialIds.has(installation.id))main.append(el('p','partial-note','Partial finding · available after discovery stops'));
  const actions=el('div','installation-actions');actions.append(action('Open folder','openFolder',{installationId:installation.id}),action('Launch MO2','launch',{installationId:installation.id}));
  head.append(label,main,actions);card.append(head);
  const sessions=sessionsFor(installation);
  if(sessions.length){const group=el('div','session-actions');for(const session of sessions){const button=action('Connect','connect',{sessionId:session.id});button.append(raw('span','',session.profileName));group.append(button)}main.append(group)}
  else if(list(installation.runningPids).length)main.append(el('p','session-note','MO2 is running; no verified Modhunter session is associated with this installation.'));
  const related=relatedInstances(installation.id,inventory);
  if(related.length){const details=el('details','installation-details');details.dataset.detailKey='installation:'+installation.id;details.open=openDetails.has(details.dataset.detailKey);const profiles=related.reduce((total,row)=>total+list(row.instance.profiles).length,0);details.append(el('summary','',count(related.length)+' instances · '+count(profiles)+' profiles'));for(const row of related)details.append(instanceRow(row.instance,row.relations,installation));rememberDetails(details);card.append(details)}
  else{const note=el('p','instance-row scope-note','No instance configuration was found for this application.');card.append(note)}
  return card;
 }
 function renderInventory(){
  const query=$('instanceSearch').value.trim().toLocaleLowerCase(),filter=$('statusFilter').value;
  visible=inventory.installations.filter(row=>matchesInstallation(row,inventory,query,filter));
  setText('installationCount',count(inventory.installations.length));setText('instanceCount',count(inventory.instances.length));setText('profileCount',count(inventory.instances.reduce((total,row)=>total+list(row.profiles).length,0)));
  const scoped=inventory.scope==='deep'?'Selected folders and their subfolders':inventory.scope==='known'?'Known locations':inventory.scope;
  setText('inventoryScope',inventory.complete?(scoped||'Completed discovery'):(inventory.installations.length||inventory.instances.length?'Partial inventory · '+(scoped||'discovery coverage is incomplete'):'Known locations have not been scanned yet.'));
  setText('visibleCount',count(visible.length)+' of '+count(inventory.installations.length)+' installations');
  $('partialNotice').hidden=!inventory.partialIds.size;
  const associated=new Set(inventory.associations.map(relation=>relation.instanceId));
  const unassociated=inventory.instances.filter(instance=>!associated.has(instance.id)&&instanceMatches(instance,query));
  const key=JSON.stringify([inventory.installations,inventory.instances,inventory.associations,[...inventory.partialIds],state.sessions,query,filter,[...selected].sort(),frozen(),[...busy].sort()]);
  if(key!==listKey){
   listKey=key;const focused=document.activeElement?.dataset.focusKey;
   $('installationList').replaceChildren(...visible.map(installationCard));
   $('unassociatedList').replaceChildren(...unassociated.map(instance=>instanceRow(instance)));
   const knownIds=new Set(inventory.installations.map(row=>row.id));
   const looseSessions=list(state.sessions).filter(session=>!knownIds.has(session.installationId));
   $('liveSessionsSection').hidden=!looseSessions.length;
   $('liveSessionsList').replaceChildren(...looseSessions.map(session=>{const row=el('article','instance-row'),top=el('div','instance-top'),main=el('div','instance-identity');main.append(raw('strong','',session.profileName),path(session.endpoint));top.append(main,action('Connect','connect',{sessionId:session.id}));row.append(top);return row}));
   if(focused){const node=[...document.querySelectorAll('[data-focus-key]')].find(item=>item.dataset.focusKey===focused);if(node&&!node.disabled)node.focus({preventScroll:true})}
  }
  $('unassociatedSection').hidden=!unassociated.length;
  $('emptyInstallations').hidden=visible.length>0;
  const filtered=!!query||filter!=='all';
  setText('emptyTitle',filtered?'No installations match these filters.':scanActive()?'Looking for your MO2 installations…':'Bring your MO2 installations together.');
  setText('emptyDescription',filtered?'Try another path, game or profile name, or clear the filters.':scanActive()?'Findings appear as discovery progresses. You can cancel the scan at any time.':'Scan known locations or add the folder containing ModOrganizer.exe.');
  $('clearFilters').hidden=!filtered;$('emptyAddFolder').hidden=filtered;$('emptyAddFolder').disabled=frozen();
  const eligible=visible.filter(row=>!inventory.partialIds.has(row.id)),selectedVisible=eligible.filter(row=>selected.has(row.id)).length;
  $('selectVisible').disabled=frozen()||!eligible.length;$('selectVisible').checked=!!eligible.length&&eligible.length===selectedVisible;$('selectVisible').indeterminate=selectedVisible>0&&selectedVisible<eligible.length;
  const notesKey=JSON.stringify(inventory.issues);if(notesKey!==issueKey){issueKey=notesKey;$('issueList').replaceChildren(...inventory.issues.map(issue=>{const row=el('div','issue-row');row.append(el('p','',issue.message||issue.kind||'Discovery note'));if(issue.path)row.append(path(issue.path));return row}))}
  $('discoveryIssues').hidden=!inventory.issues.length;setText('issueSummary',count(inventory.issues.length)+' discovery notes');
 }
 function renderResults(){
  const install=state.install||{},results=list(install.results);$('activityPanel').hidden=install.status==='idle'&&!results.length;
  const labels={running:'Installing selected plugins',stopping:'Stopping after the current installation',complete:'Installation finished',cancelled:'Installation stopped',failed:'Installation needs attention'};setText('activityHeading',labels[install.status]||'Installation progress');
  setText('installSummary',count(install.completed)+' of '+count(install.total)+' installation targets finished');$('installProgress').max=Math.max(1,Number(install.total)||1);$('installProgress').value=Math.max(0,Number(install.completed)||0);
  $('cancelInstall').hidden=!installActive();$('cancelInstall').disabled=!!install.stopRequested||install.status==='stopping'||busy.has('cancelInstall');setText('cancelInstall',install.stopRequested||install.status==='stopping'?'Stopping after current installation…':'Stop after current installation');
  const key=JSON.stringify([results,inventory.installations.map(row=>[row.id,row.directory])]);if(key===resultKey)return;resultKey=key;
  $('installResults').replaceChildren(...results.map(result=>{
   const installRow=inventory.installations.find(row=>row.id===result.installationId),row=el('article','result-row');row.dataset.installationId=result.installationId;row.dataset.status=result.status;
   const top=el('div','result-top');top.append(raw('strong','',installRow?.directory||result.pluginDirectory||result.installationId),chip(RESULT_LABELS[result.status]||result.status,['installed','updated'].includes(result.status)?'positive':result.status==='failed'?'error':result.status==='installing'?'info':'neutral'));row.append(top);
   if(result.message)row.append(el('p','',result.message));if(result.pluginDirectory)row.append(path(result.pluginDirectory));
   if(result.version){const version=el('p');version.append(el('span','','Plugin version: '),raw('span','',(result.previousVersion?result.previousVersion+' → ':'')+result.version));row.append(version)}
   if(result.preservedData===true)row.append(el('p','','Existing plugin data preserved.'));
   if(result.backupDirectory){const details=el('details');details.append(el('summary','','Previous plugin backup'),path(result.backupDirectory));row.append(details)}
   if(result.rollback&&result.rollback!=='not_needed'&&result.rollback!=='none'){const note=el('p');note.append(el('span','','Rollback: '),raw('span','',typeof result.rollback==='string'?result.rollback:JSON.stringify(result.rollback)));row.append(note)}
   return row;
  }));
 }
 function renderControls(){
  for(const id of ['scanKnown','scanDeep','addFolder'])$(id).disabled=frozen();
  $('scanKnown').setAttribute('aria-busy',String(busy.has('scan')||scanActive()));$('addFolder').setAttribute('aria-busy',String(busy.has('addFolder')));
  const selectedOutside=[...selected].filter(id=>!visible.some(row=>row.id===id)).length;
  setText('selectionCount',selected.size?count(selected.size)+' installations selected':'No installations selected');
  setText('selectionHint',selectedOutside?count(selectedOutside)+' selected installations are outside the current filters.':scanActive()?'Discovery is running. Review targets after it stops.':installActive()?'Installation results appear above as each target finishes.':!state.package?.valid?'The bundled plugin must be available before you can install.':'Choose the application folders you want to update.');
  $('clearSelection').disabled=frozen()||!selected.size;
  $('reviewSelected').disabled=frozen()||!selected.size||!state.package?.available||!state.package?.valid||busy.has('previewInstall');$('reviewSelected').setAttribute('aria-busy',String(busy.has('previewInstall')));
  if($('installPreview').open)renderPreviewControls();
 }
 function render(){
  if(disposed)return;renderPackage();renderScan();renderInventory();renderResults();renderControls();
  $('backendError').hidden=!state.error;setText('backendError',state.error||'');
 }
 function changedSelection(){previewGeneration++;preview=null;clearTimeout(previewTimer);render()}
 function openDialog(id){dialogReturn=document.activeElement;$(id).showModal()}
 function closeDialog(id){$(id).close();if(dialogReturn?.isConnected&&!dialogReturn.disabled)dialogReturn.focus({preventScroll:true})}
 function invalidatePreview(message){previewGeneration++;preview=null;clearTimeout(previewTimer);$('previewLoading').hidden=true;setText('previewError',message);$('previewAgain').hidden=false;renderPreviewControls()}
 function renderPreviewControls(){
  const eligible=!previewSubmitted&&!frozen()&&state.package?.valid&&selectionKey(selected)===previewSelection&&exactPreview(preview,selected);
  $('confirmInstall').disabled=!eligible||busy.has('install')||busy.has('previewInstall');$('confirmInstall').setAttribute('aria-busy',String(busy.has('install')));
  $('previewAgain').disabled=frozen()||busy.has('previewInstall')||busy.has('install');
  $('closePreview').disabled=busy.has('install');$('cancelPreview').disabled=busy.has('install');
 }
 function previewTarget(target){
  const row=el('article','preview-target');row.dataset.installationId=target.installationId;row.dataset.ready=String(target.canInstall===true);
  const top=el('div','result-top'),actionLabels={install:'Install plugin',update:'Update plugin',repair:'Repair plugin',replace:'Replace plugin'};top.append(raw('strong','',leaf(target.directory)),chip(target.canInstall?(actionLabels[target.action]||'Ready to install'):'Needs attention',target.canInstall?'positive':'error'));row.append(top);
  const fields=[['Application folder',target.directory],['Plugin destination',target.pluginDirectory],['Backup location',target.backupRoot]];const facts=el('dl');for(const [label,value] of fields){facts.append(el('dt','',label));const dd=el('dd');dd.append(path(value));facts.append(dd)}facts.append(el('dt','','Plugin version'));const version=el('dd');version.append(raw('span','',(target.previousVersion||'—')+' → '+target.version));facts.append(version);row.append(facts);
  if(target.message)row.append(el('p','',target.message));
  if(list(target.blockers).length){const blockers=el('ul','blockers');for(const message of target.blockers)blockers.append(el('li','',message));row.append(blockers)}
  if(list(target.affectedInstances).length){const details=el('details');details.append(el('summary','',count(target.affectedInstances.length)+' affected instance choices'));const entries=el('ul');for(const instance of target.affectedInstances){const entry=el('li');entry.append(raw('span','',instance.name),el('span','',' · '+(RELATION_LABELS[instance.relation]||instance.kind||'Instance')));entries.append(entry)}details.append(entries);row.append(details)}
  return row;
 }
 async function reviewSelected(){
  if(frozen()||!selected.size||!state.package?.valid)return;
  const generation=++previewGeneration,ids=[...selected];previewSelection=selectionKey(ids);preview=null;previewSubmitted=false;clearTimeout(previewTimer);
  setText('previewError','');setText('previewExpiry','');$('previewPackage').replaceChildren();$('previewTargets').replaceChildren();$('previewAgain').hidden=true;$('previewLoading').hidden=false;
  if(!$('installPreview').open)openDialog('installPreview');renderPreviewControls();
  try{
   const result=await command('previewInstall',{installationIds:ids});
   if(disposed||generation!==previewGeneration||!$('installPreview').open||selectionKey(selected)!==previewSelection)return;
   const candidate=result?.preview;
   if(!candidate||selectionKey(list(candidate.targets).map(row=>row.installationId))!==previewSelection||new Set(list(candidate.targets).map(row=>row.installationId)).size!==ids.length)throw Error('The preview did not match every selected installation. Check the targets again.');
   preview=candidate;$('previewTargets').replaceChildren(...candidate.targets.map(previewTarget));
   $('previewPackage').append(el('strong','','Bundled plugin · '+candidate.package.version),raw('code','',candidate.package.commit));
   const expires=expiryMillis(candidate.expiresAt);if(Number.isFinite(expires)){setText('previewExpiry','This review expires at '+new Date(expires).toLocaleTimeString()+'. Targets are rechecked before installation.');previewTimer=setTimeout(()=>invalidatePreview('This installation review expired. Check the targets again.'),Math.max(0,expires-Date.now()))}
   if(!candidate.canInstall||candidate.targets.some(target=>!target.canInstall||list(target.blockers).length)){setText('previewError','Resolve the listed blockers or return to selection and deselect those installations.');$('previewAgain').hidden=false}
  }catch(error){if(generation===previewGeneration&&$('installPreview').open){setText('previewError',errorText(error));$('previewAgain').hidden=false}}
  finally{if(generation===previewGeneration){$('previewLoading').hidden=true;renderPreviewControls()}}
 }
 async function installReviewed(){
  if(previewSubmitted||frozen()||selectionKey(selected)!==previewSelection||!exactPreview(preview,selected))return;
  const token=preview.token;previewSubmitted=true;renderPreviewControls();
  try{await command('install',{token});closeDialog('installPreview');$('activityHeading').focus({preventScroll:true});$('activityPanel').scrollIntoView({block:'nearest',behavior:'auto'})}
  catch(error){preview=null;setText('previewError',errorText(error));$('previewAgain').hidden=false;renderPreviewControls()}
 }
 function openDeepScan(){
  $('deepRoots').replaceChildren(...list(state.roots).map(root=>{const label=el('label','root-choice'),input=el('input');input.type='checkbox';input.value=root.path;input.dataset.rootPath=root.path;input.addEventListener('change',()=>{$('startDeepScan').disabled=!$('deepRoots').querySelector('input:checked')});const info=el('span');info.append(raw('strong','',root.label||root.path),path(root.path));label.append(input,info);return label}));
  $('noRoots').hidden=!!list(state.roots).length;$('startDeepScan').disabled=true;setText('deepScanError','');openDialog('deepScanDialog');
 }
 $('scanKnown').addEventListener('click',()=>void perform('scan',{mode:'known'}));
 $('scanDeep').addEventListener('click',openDeepScan);
 for(const id of ['addFolder','emptyAddFolder'])$(id).addEventListener('click',()=>void perform('addFolder'));
 $('cancelScan').addEventListener('click',()=>void perform('cancelScan',{operationId:state.scan.id}));
 $('cancelInstall').addEventListener('click',()=>void perform('cancelInstall',{operationId:state.install.id}));
 $('deepAddFolder').addEventListener('click',()=>{closeDialog('deepScanDialog');void perform('addFolder')});
 $('closeDeepScan').addEventListener('click',()=>closeDialog('deepScanDialog'));
 $('startDeepScan').addEventListener('click',async()=>{const roots=[...$('deepRoots').querySelectorAll('input:checked')].map(input=>input.value);if(!roots.length||frozen())return;closeDialog('deepScanDialog');await perform('scan',{mode:'deep',roots})});
 $('instanceSearch').addEventListener('input',()=>{if(searchFrame)cancelAnimationFrame(searchFrame);searchFrame=requestAnimationFrame(()=>{searchFrame=0;render()})});
 $('statusFilter').addEventListener('change',render);
 $('clearFilters').addEventListener('click',()=>{$('instanceSearch').value='';$('statusFilter').value='all';render();$('instanceSearch').focus()});
 $('selectVisible').addEventListener('change',()=>{for(const installation of visible){if(inventory.partialIds.has(installation.id))continue;if($('selectVisible').checked)selected.add(installation.id);else selected.delete(installation.id)}changedSelection()});
 $('clearSelection').addEventListener('click',()=>{selected.clear();changedSelection()});
 $('reviewSelected').addEventListener('click',()=>void reviewSelected());$('previewAgain').addEventListener('click',()=>void reviewSelected());$('confirmInstall').addEventListener('click',()=>void installReviewed());
 for(const id of ['cancelPreview','closePreview'])$(id).addEventListener('click',()=>closeDialog('installPreview'));
 $('installPreview').addEventListener('close',()=>{previewGeneration++;preview=null;previewSubmitted=false;clearTimeout(previewTimer);$('previewLoading').hidden=true});
 $('installPreview').addEventListener('cancel',event=>{if(busy.has('install'))event.preventDefault()});
 $('dismissFeedback').addEventListener('click',()=>feedback(''));
 document.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='f'&&!document.querySelector('dialog[open]')){event.preventDefault();$('instanceSearch').focus()}});
 window.addEventListener('focus',()=>{if(ready&&!disposed&&!busy.has('state'))void command('state').catch(error=>feedback(errorText(error),'error'))});
 window.addEventListener('beforeunload',()=>{disposed=true;clearTimeout(pollTimer);clearTimeout(previewTimer);if(searchFrame)cancelAnimationFrame(searchFrame);for(const stop of unlisten)stop()},{once:true});
 async function poll(){if(disposed)return;if(!document.hidden&&!busy.has('state')){try{await command('state')}catch(error){feedback(errorText(error),'error')}}if(!disposed)pollTimer=setTimeout(poll,8000)}
 async function start(){
  render();if(!api?.core?.invoke||!api?.event?.listen){$('connectionBanner').hidden=false;setText('connectionBanner','Open MO2 Instances from Modhunter.exe to use native discovery and installation.');return}
  unlisten.push(await api.event.listen('modhunter-instances-state',event=>accept(event.payload)));ready=true;
  await command('state');render();pollTimer=setTimeout(poll,8000);
 }
 window.ModhunterInstances={snapshot:()=>state,selection:()=>[...selected]};
 start().catch(error=>{ready=false;$('connectionBanner').hidden=false;setText('connectionBanner',errorText(error));render()});
})();
