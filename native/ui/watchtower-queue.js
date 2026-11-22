'use strict';
(() => {
 const api=()=>window.ModhunterWatchtower,$=id=>document.getElementById(id);let queue=null,busy=false,inFlight=null,poll=null,context={revision:-1,scope:null},generation=0,refreshWanted=false,disposed=false;
 const isOpen=()=>!disposed&&!$('downloadsView').hidden;
 const current=request=>isOpen()&&request===generation;
 const matches=row=>!!context.scope&&row?.profileId===context.scope.profileId&&row.executionEpoch===context.scope.executionEpoch;
 function clear(message){
  clearTimeout(poll);queue=null;
  $('installQueueStatus').removeAttribute('data-i18n-feedback');$('installQueueStatus').textContent=message;
  $('installQueuePending').replaceChildren();$('installQueueHistory').replaceChildren();
  $('installQueueStop').disabled=true;$('installQueueResume').disabled=true;$('installQueueCancel').disabled=busy;
 }
 const unavailable='Connect to MO2 with the current Modhunter plugin to inspect its shared queue.';
 const checking='Checking the current MO2 install queue…';

 const section=document.createElement('section');section.className='settings-card native-workspace';section.innerHTML=`<div class="eyebrow">SHARED INSTALL QUEUE</div><h2>API and page installations</h2><p>Approved API downloads and reviewed page archives use the same native installation queue. Each ambiguous item stops for review while other eligible items can continue.</p><div class="row-actions"><button id="installQueueRefresh">Refresh install queue</button><button id="installQueueStop" disabled>Stop after current</button><button id="installQueueCancel">Cancel preparation</button><button id="installQueueResume" disabled>Resume approved queue</button></div><p id="installQueueStatus" role="status">Connect to MO2 to inspect the shared queue.</p><p>Stop after current keeps queued approvals and lets the active operation finish. Cancel preparation disarms waiting installs and cancels unfinished downloads and reviews. A native installer already open in MO2 keeps its lease until its actual result.</p><ol id="installQueuePending"></ol><details><summary>Recent queue outcomes</summary><p class="scope-note">Read-only archive evidence for recent operations in this session. Installer choices are not captured.</p><div id="installQueueHistory"></div></details>`;$('downloadsView').prepend(section);
 async function call(kind,value={}){const result=await api().command(kind,value,true);if(!result?.ok)throw Error(result?.error||result?.message||'Queue state is unavailable.');return result}
 function rowParts(row,hidden){const identity=document.createElement('span');identity.textContent=hidden?'Native operation':row.modName||row.itemId||row.jobId||'Native operation';if(!hidden)identity.dataset.noTranslate='';return [identity,document.createTextNode(' · '+(row.kind==='page'?'Page archive':'API download')+' · '+(row.state||'queued')+(row.needsReview?' · Needs review':'')+(row.nativeStarted?' · Native installer active':''))]}
 function receipt(row,hidden){
  const evidence=row.sourceArchive||{}, line=document.createElement('span');line.className='scope-note';
  if(hidden){line.textContent='Archive identifiers hidden';return line}
  const original=evidence.state==='verifiedOriginalPage'&&Number.isSafeInteger(evidence.bytes)&&evidence.bytes>=0&&/^[0-9a-f]{64}$/.test(evidence.sha256||'');
  const reviewed=evidence.state==='reviewedApiSelection';
  const name=typeof evidence.filename==='string'&&evidence.filename.length<=240&&/\.(zip|7z|rar)$/i.test(evidence.filename)&&!/[<>:"/\\|?*\x00-\x1f\x7f-\x9f]/.test(evidence.filename)?evidence.filename:'';
  if(original||reviewed){
   const pieces=[original?'Verified original page archive':'Reviewed API archive selection'];line.dataset.noTranslate='';
   if(name)pieces.push(name);
   if(reviewed&&evidence.provider==='nexus'&&Number.isSafeInteger(evidence.modId)&&evidence.modId>0&&Number.isSafeInteger(evidence.fileId)&&evidence.fileId>0)pieces.push('Nexus project '+evidence.modId+' / file '+evidence.fileId);
   if(original)pieces.push(evidence.bytes+' bytes','SHA-256 '+evidence.sha256);
   line.textContent=pieces.join(' · ');
  }else line.textContent='Archive evidence unavailable';
  return line;
 }
 function historyRow(row,hidden){
  const p=document.createElement('p');p.append(...rowParts(row,hidden));
  if(row.error){const error=document.createElement('span');error.dataset.i18nFeedback='';error.textContent=hidden?'Operation needs review':row.error;p.append(document.createTextNode(' · '),error)}
  p.append(document.createElement('br'),receipt(row,hidden),document.createTextNode(' · FOMOD choices not captured'));
  return p;
 }
 function render(){
  if(!queue)return;
  const counts=queue.counts||{},hidden=!!api().state().settings?.hideTitles,active=queue.active,other=queue.activeInOtherProfile===true;
  $('installQueueStatus').removeAttribute('data-i18n-feedback');
  $('installQueueStatus').replaceChildren(document.createTextNode((queue.mode==='paused'?'Queue paused':'Queue running')+' · '+(counts.pending||0)+' waiting · '+(counts.awaitingDownload||0)+' approved downloads pending'+(active?' · ':other?' · Installation active in another profile':' · No active installation')),...(active?rowParts(active,hidden):[]));
  $('installQueuePending').replaceChildren(...(queue.pending||[]).map(row=>{const li=document.createElement('li');li.append(...rowParts(row,hidden));return li}));
  $('installQueueHistory').replaceChildren(...(queue.history||[]).slice().reverse().map(row=>historyRow(row,hidden)));
  $('installQueueStop').disabled=busy||queue.mode==='paused';$('installQueueResume').disabled=busy||queue.mode!=='paused';$('installQueueCancel').disabled=busy;
  clearTimeout(poll);if(isOpen()&&(active||other||counts.pending||counts.awaitingDownload))poll=setTimeout(()=>void refresh(),3000);
 }
 async function refresh(){
  if(!isOpen())return;
  if(!context.scope){clear(unavailable);return}
  if(inFlight){if(inFlight.generation!==generation)refreshWanted=true;return}
  const request=generation;inFlight={generation:request};
  try{
   const result=await call('installQueue');
   if(!current(request))return;
   if(!matches(result.installQueue)){clear('MO2 changed. Waiting for the current profile.');return}
   queue=result.installQueue;render();
  }catch(error){
   if(current(request))clear(api().state().settings?.hideTitles?'Queue state is unavailable.':error.message);
  }finally{
   inFlight=null;
   if(refreshWanted){refreshWanted=false;void refresh()}
  }
 }
 async function control(action){
  if(busy||!isOpen())return;
  const request=generation;
  if(action==='cancelPreparation'&&!await api().confirmAction('Cancel preparation now?','Disarm pending API installs, cancel pending page jobs and unfinished downloads and reviews. Existing completed archives remain available. An open native installer must finish or be cancelled in its own MO2 dialog.','Cancel preparation'))return;
  if(!current(request)||busy)return;
  busy=true;render();$('installQueueCancel').disabled=true;
  try{
   const result=await call('installQueueControl',{action});
   if(!current(request))return;
   if(matches(result.installQueue)){queue=result.installQueue;api().showToast(result.message||'Queue control applied.')}
   else clear('MO2 changed. Waiting for the current profile.');
  }catch(error){
   if(current(request)){api().showToast(api().state().settings?.hideTitles?'Queue control was not confirmed.':error.message,null,'Undo',true);await refresh()}
  }finally{busy=false;$('installQueueCancel').disabled=false;render()}
 }
 function invalidate(){
  generation++;refreshWanted=false;clear(context.scope?checking:unavailable);
  if(isOpen())void refresh();
 }
 $('installQueueRefresh').onclick=()=>void refresh();$('installQueueStop').onclick=()=>void control('stopAfterCurrent');$('installQueueCancel').onclick=()=>void control('cancelPreparation');$('installQueueResume').onclick=()=>void control('resume');
 new MutationObserver(invalidate).observe($('downloadsView'),{attributes:true,attributeFilter:['hidden']});
 window.addEventListener('beforeunload',()=>{disposed=true;clearTimeout(poll)});
 window.addEventListener('modhunter-native-state',()=>{if(queue)render()});
 function acceptContext(next){
  if(!Number.isSafeInteger(next?.revision)||next.revision<=context.revision)return;
  const scope=next.scope;
  context={revision:next.revision,scope:typeof scope?.profileId==='string'&&scope.profileId&&Number.isSafeInteger(scope.executionEpoch)&&scope.executionEpoch>=0?scope:null};
  invalidate();
 }
 window.addEventListener('modhunter-install-queue-context',event=>acceptContext(event.detail));
 acceptContext(api()?.installQueueContext?.());
})();
