'use strict';
(() => {
 const $ = id => document.getElementById(id);
 const api = window.__TAURI__;
 const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const text = (id, value) => { const node=$(id);if(node&&node.textContent!==String(value))node.textContent=String(value); };
 const ATTENTION = new Set(['signInRequired','challenge','unavailable','selectorMissing','tooLarge','error','interrupted']);
 const DEFAULTS = {checkOnLaunch:false,backgroundChecking:false,defaultIntervalMinutes:60,historyLimit:20,mutedOrigins:[],mutedFolders:[],hideTitles:false,privacyScreen:false,notifications:false};
 let state={revision:-1,watches:[],sessions:[],history:[],settings:{...DEFAULTS},work:{active:[],queued:[]}},view='pages',filter='all',query='',disposed=false,ready=false;
 let searchFrame=null,toastTimer=null,editWatch=null,editSession=null,detailId=null,detailRequest=0,lastDetailKey='',settingsDirty=false,lastSettingsRevision=-1;
 let prefillRevision=0,pendingPrefill=null,importDocument=null,importRevision=null,confirmation=null,dialogReturn=null,lastStateKey='',importGeneration=0,settingsGeneration=0,pageFormGeneration=0,sessionFormGeneration=0,exportGeneration=0;
 const rows=new Map(),sessionRows=new Map(),historyRows=new Map(),selected=new Set(),selectedObservations=new Map(),busy=new Set(),searchIndexes=new Map();
 let watchById=new Map(),sessionById=new Map(),workIds=new Set(),activeIds=new Set();
 function rebuildIndexes(){watchById=new Map(state.watches.map(w=>[w.id,w]));sessionById=new Map(state.sessions.map(s=>[s.id,s]));activeIds=new Set(state.work?.active||[]);workIds=new Set([...activeIds,...(state.work?.queued||[])])}
 let visible=[];
 function host(url){try{return new URL(url).host}catch{return 'Website'}}
 function origin(url){try{return new URL(url).origin}catch{return ''}}
 function displayTitle(w){return state.settings.hideTitles?host(w.url):(w.title||host(w.url))}
 function date(value){if(!value)return 'Not yet';const d=new Date(Number(value));return Number.isNaN(d.valueOf())?'Unknown':d.toLocaleString()}
 function relative(value){if(!value)return 'Not yet';const minutes=Math.floor(Math.max(0,Date.now()-Number(value))/60000);return minutes<1?'Just now':minutes<60?minutes+' min ago':minutes<1440?Math.floor(minutes/60)+' hr ago':Math.floor(minutes/1440)+' days ago'}
 function interval(value){return value<60?value+' min':value%1440===0?(value/1440)+' day'+(value===1440?'':'s'):value%60===0?(value/60)+' hr':value+' min'}
 function working(id){return workIds.has(id)}
 function stopped(w){return w.state==='checking'&&!working(w.id)}
 function needsAttention(w){return ATTENTION.has(w.state)||stopped(w)}
 function needsSession(w){return !!w.requiresSession&&(!w.sessionId||!sessionById.has(w.sessionId))}
 function detailKey(w){return w?JSON.stringify([w.generation,w.operationId,w.latest?.id,w.state,w.seenObservationId,working(w.id),state.settings.hideTitles,state.settings.historyLimit]):'missing'}
 function status(w){
  if(w.removedAt)return ['Removed',''];
  if(working(w.id))return [activeIds.has(w.id)||w.state==='checking'?'Checking':'Queued','busy'];
  if(stopped(w))return ['Check stopped','attention'];
  if(needsSession(w))return ['Choose a session','attention'];
  if(!w.enabled||w.muted)return [w.muted?'Muted':'Paused',''];
  if(needsAttention(w))return [{signInRequired:'Sign-in needed',challenge:'Verify in browser',unavailable:'Unavailable',selectorMissing:'Region not found',tooLarge:'Choose a smaller region',error:'Check failed',interrupted:'Interrupted'}[w.state],'attention'];
  if(w.needsCheck||!w.latest)return ['Awaiting first check','new'];
  if(w.hasChanges)return ['Changed','changed'];
  if(w.state==='cancelled')return ['Check cancelled',''];
  return [w.state==='baseline'?'Baseline saved':'No unread changes',''];
 }
 function enabled(){return ready&&!state.readOnly}
 function showToast(message,action,label='Undo'){
  clearTimeout(toastTimer);text('toastText',message);$('toast').hidden=false;$('toastAction').hidden=!action;$('toastAction').textContent=label;$('toastAction').onclick=action?()=>{hideToast();void action()}:null;
  toastTimer=setTimeout(hideToast,action?12000:8000);
 }
 function hideToast(){clearTimeout(toastTimer);$('toast').hidden=true}
 function formError(id,message){text(id,message||'');$(id).hidden=!message}
 function errorText(error){const message=typeof error==='string'?error:error?.message||String(error);return message.replace(/^Error:\s*/,'').slice(0,1000)}
 function button(action,id,label,extra='',disabled=false,observationId=''){return '<button type="button" data-action="'+action+'" data-id="'+esc(id)+'"'+(observationId?' data-observation-id="'+esc(observationId)+'"':'')+(extra?' class="'+extra+'"':'')+(disabled?' disabled':'')+'>'+label+'</button>'}
 function options(node,values,placeholder){
  const previous=node.value,signature=JSON.stringify(values);
  if(node.dataset.options===signature)return;node.dataset.options=signature;node.replaceChildren();
  const first=document.createElement('option');first.value='';first.textContent=placeholder;node.append(first);
  for(const value of values){const option=document.createElement('option');option.value=value;option.textContent=value;node.append(option)}
  node.value=values.includes(previous)?previous:'';
 }
 function replaceNode(old,next){
  const focused=document.activeElement,inside=old?.contains(focused),action=inside?focused.dataset.action:null,selection=inside?focused.dataset.select:null;
  if(old)old.replaceWith(next);
  if(inside){const candidate=action?[...next.querySelectorAll('[data-action]')].find(n=>n.dataset.action===action):selection!=null?next.querySelector('[data-select]'):null;(candidate||next.querySelector('[data-action="detail"]')||next.querySelector('button'))?.focus({preventScroll:true})}
 }
 function reconcile(container,items,cache,make){
  const keep=new Set(items.map(item=>item.id));for(const [id,entry] of cache)if(!keep.has(id)){entry.node.remove();cache.delete(id)}
  let at=container.firstElementChild;
  for(const item of items){const built=make(item),previous=cache.get(item.id);let node=previous?.node;
   if(!previous||previous.key!==built.key){const temp=document.createElement('template');temp.innerHTML=built.html;node=temp.content.firstElementChild;const wasCursor=previous?.node===at;replaceNode(previous?.node,node);if(wasCursor)at=node;cache.set(item.id,{key:built.key,node})}
   if(node!==at){const focused=node.contains(document.activeElement)?document.activeElement:null;container.insertBefore(node,at);focused?.focus({preventScroll:true})}at=node.nextElementSibling;
  }
 }
 function index(w){
  const key=[w.generation,w.title,w.url,w.folder,(w.tags||[]).join(','),w.notes,w.state,w.hasChanges,w.needsCheck,state.settings.hideTitles].join('|'),cached=searchIndexes.get(w.id);
  if(cached?.key===key)return cached.value;
  const value=[state.settings.hideTitles?'':w.title,w.url,w.folder,...(w.tags||[]),state.settings.hideTitles?'':w.notes,status(w)[0]].join(' ').toLocaleLowerCase();searchIndexes.set(w.id,{key,value});return value;
 }
 function matches(w){
  if((filter==='removed')!==!!w.removedAt)return false;
  if(filter==='changed'&&!w.hasChanges)return false;
  if(filter==='new'&&!w.needsCheck&&w.latest)return false;
  if(filter==='attention'&&!needsAttention(w)&&!needsSession(w))return false;
  if(filter==='paused'&&w.enabled&&!w.muted&&!needsSession(w))return false;
  if($('siteFilter').value&&origin(w.url)!==$('siteFilter').value)return false;
  if($('folderFilter').value&&w.folder!==$('folderFilter').value)return false;
  return !query||index(w).includes(query);
 }
 function rank(w){return w.hasChanges?0:needsAttention(w)||needsSession(w)?1:w.needsCheck?2:working(w.id)?3:!w.enabled||w.muted?5:4}
 function watchMarkup(w){
  const label=status(w),isBusy=working(w.id),checked=selected.has(w.id),session=sessionById.get(w.sessionId),canChange=enabled(),isRemoved=!!w.removedAt;
  const key=JSON.stringify([w.generation,w.operationId,w.title,w.folder,w.tags,w.notes,w.enabled,w.muted,w.requiresSession,w.state,w.lastError,w.latest?.id,w.lastSuccessAt,w.seenObservationId,w.hasChanges,w.needsCheck,w.removedAt,w.nextCheckAt,w.retryAt,w.intervalMinutes,session?.label,checked,isBusy,label,state.settings.hideTitles,canChange,Math.floor(Date.now()/60000)]);
  const actions=isRemoved?button('restore',w.id,'Restore','primary',!canChange):
   (isBusy||stopped(w)?button('cancel',w.id,stopped(w)?'Clear stopped check':'Cancel','quiet',!canChange):button('check',w.id,'Check','',!canChange||needsSession(w)||!w.enabled||w.muted))+button('open',w.id,'Open page','quiet',!ready)+button('detail',w.id,'Details','quiet',!ready)+(w.hasChanges?button('seen',w.id,'Mark seen','primary',!canChange,w.latest?.id):'');
  const schedule=isRemoved?'Removed '+relative(w.removedAt):!w.enabled||w.muted?'Checks paused':state.settings.backgroundChecking?'Every '+interval(w.intervalMinutes):'Manual · '+interval(w.intervalMinutes)+' due interval';
  const secondary=state.settings.hideTitles?'':w.notes;
  return {key,html:'<article class="watch-row'+(checked?' selected':'')+(isRemoved?' removed':'')+'" data-watch-id="'+esc(w.id)+'"><input class="watch-check" type="checkbox" data-select="'+esc(w.id)+'" aria-label="Select '+esc(displayTitle(w))+'"'+(checked?' checked':'')+'><div class="row-main"><div class="row-title"><button class="page-name" data-action="detail" data-id="'+esc(w.id)+'">'+esc(displayTitle(w))+'</button><span class="badge '+label[1]+'">'+esc(label[0])+'</span></div><div class="row-meta"><span class="site-label">'+esc(host(w.url))+'</span>'+(w.folder?'<span class="meta-separator">/</span><span>'+esc(w.folder)+'</span>':'')+(w.tags||[]).map(tag=>'<span class="tag">'+esc(tag)+'</span>').join('')+'</div><div class="row-detail">'+esc((w.latest?'Last success '+relative(w.lastSuccessAt):'First successful check establishes a baseline')+' · '+schedule)+(session?' · '+esc(session.label):'')+'</div>'+(w.lastError?'<div class="row-detail error">'+esc(w.lastError)+'</div>':'')+(secondary?'<div class="row-detail">'+esc(secondary)+'</div>':'')+'</div><div class="row-actions">'+actions+'</div></article>'};
 }
 function renderPages(){
  const active=state.watches.filter(w=>!w.removedAt),removed=state.watches.filter(w=>w.removedAt),changed=active.filter(w=>w.hasChanges),fresh=active.filter(w=>w.needsCheck||!w.latest),attention=active.filter(w=>needsAttention(w)||needsSession(w)),paused=active.filter(w=>!w.enabled||w.muted||needsSession(w));
  for(const [id,value] of Object.entries({navAll:active.length,navChanged:changed.length,navNew:fresh.length,navAttention:attention.length,navPaused:paused.length,navRemoved:removed.length,navSessions:state.sessions.length,summaryTotal:active.length,summaryChanged:changed.length,summaryAttention:attention.length}))text(id,value.toLocaleString());
  options($('siteFilter'),[...new Set(state.watches.map(w=>origin(w.url)))].filter(Boolean).sort(),'All sites');options($('folderFilter'),[...new Set(state.watches.map(w=>w.folder))].filter(Boolean).sort(),'All folders');
  const running=(state.work?.active||[]).length,queued=(state.work?.queued||[]).length,stalled=active.some(stopped);
  text('runStatus',running||queued?running+' checking'+(queued?' · '+queued+' queued':''):stalled?'A check stopped before its result could be saved':state.settings.backgroundChecking?'Background checks enabled · due pages only':'Manual checks · ready when you are');$('runDot').classList.toggle('busy',!!(running||queued));$('cancelAll').hidden=!(running||queued||stalled);text('cancelAll',stalled&&!running&&!queued?'Clear stopped checks':'Cancel checks');
  const sort=$('sort').value;visible=state.watches.filter(matches);visible.sort((a,b)=>sort==='name'?displayTitle(a).localeCompare(displayTitle(b)):sort==='recent'?(b.lastSuccessAt||0)-(a.lastSuccessAt||0):sort==='due'?a.nextCheckAt-b.nextCheckAt:rank(a)-rank(b)||(b.updatedAt||0)-(a.updatedAt||0));
  const exists=new Set(state.watches.map(w=>w.id));for(const id of selected)if(!exists.has(id))selected.delete(id);for(const id of searchIndexes.keys())if(!exists.has(id))searchIndexes.delete(id);
  reconcile($('watchList'),visible,rows,watchMarkup);
  text('resultsCount',visible.length.toLocaleString()+' of '+(filter==='removed'?removed.length:active.length).toLocaleString()+' pages');$('selectAll').checked=visible.length>0&&visible.every(w=>selected.has(w.id));$('selectAll').indeterminate=visible.some(w=>selected.has(w.id))&&!$('selectAll').checked;
  $('selectionBar').hidden=!selected.size;text('selectionCount',selected.size+' selected');for(const button of $('selectionBar').querySelectorAll('[data-bulk]')){button.disabled=!canMutate();button.hidden=filter==='removed'?button.dataset.bulk!=='restore':button.dataset.bulk==='restore'}
  $('emptyPages').hidden=visible.length>0;$('emptyAdd').hidden=filter!=='all'||!!query||!!$('siteFilter').value||!!$('folderFilter').value;$('emptyReset').hidden=!query&&!$('siteFilter').value&&!$('folderFilter').value&&filter==='all';
  text('emptyTitle',!active.length&&filter==='all'?'A clearer view of what changed.':filter==='changed'?'No unread page changes.':filter==='removed'?'No removed pages.':'No pages match this view.');text('emptyDescription',filter==='removed'?'Removed pages can be restored here, with their settings and previous history.':!active.length&&filter==='all'?'Add a page to establish its first baseline. Later checks show changes for you to review.':'Change the filters or check your enabled pages to refresh their observations.');
  for(const id of ['addPage','emptyAdd','checkAll','checkDue'])$(id).disabled=!canMutate();$('checkAll').disabled=!canMutate()||!active.some(w=>w.enabled&&!w.muted&&!needsSession(w)&&!working(w.id)&&!stopped(w));$('checkDue').disabled=$('checkAll').disabled;
 }
 function clearSelected(){selected.clear();selectedObservations.clear()}
 function selectWatch(id,on){if(on){selected.add(id);selectedObservations.set(id,watchById.get(id)?.latest?.id)}else{selected.delete(id);selectedObservations.delete(id)}}
 function canMutate(){return enabled()&&!busy.has('bulk')}
 function renderSessions(){
  $('emptySessions').hidden=state.sessions.length>0;$('addSession').disabled=!canMutate();$('emptyAddSession').disabled=!canMutate();
  reconcile($('sessionList'),state.sessions,sessionRows,s=>{
   const associated=state.watches.filter(w=>!w.removedAt&&w.sessionId===s.id).length,sessionState={notOpened:'Not opened yet',opened:'Opened · confirm when ready',confirmed:'Confirmed by you',reset:'Fresh session · sign in again'}[s.state]||'Review session';
   return {key:JSON.stringify([s,associated,canMutate()]),html:'<article class="session-card" data-session-id="'+esc(s.id)+'"><div class="eyebrow">'+esc(host(s.origin))+'</div><h3>'+esc(s.label)+'</h3><p>'+associated+' linked page'+(associated===1?'':'s')+' · '+(s.remember?'Saved across app restarts':'Temporary for this app run')+'</p><div class="session-state"><span class="local-dot"></span>'+esc(sessionState)+'</div><div class="session-actions">'+button('session-open',s.id,'Open sign-in','primary',!canMutate())+button('session-confirm',s.id,'Confirm & recheck','',!canMutate())+button('session-edit',s.id,'Edit','quiet',!canMutate())+button('session-reset',s.id,'Forget sign-in','quiet danger-text',!canMutate())+button('session-remove',s.id,'Remove','quiet danger-text',!canMutate())+'</div><footer>Last opened: '+esc(date(s.lastOpenedAt))+'<br>Last confirmation: '+esc(date(s.lastConfirmedAt))+'</footer></article>'};
  });
 }
 function renderHistory(){
  const list=[...(state.history||state.seenHistory||[])].sort((a,b)=>b.at-a.at);$('emptyHistory').hidden=!!list.length;
  reconcile($('historyList'),list,historyRows,e=>{const watch=watchById.get(e.watchId),title=state.settings.hideTitles?(watch?host(watch.url):'Watched page'):(e.title||watch?.title||'Watched page');return {key:JSON.stringify([e,title,canMutate()]),html:'<article class="history-entry'+(e.undone?' undone':'')+'" data-event-id="'+esc(e.id)+'"><div><h3>'+esc(title)+'</h3><p>'+(e.undone?'Acknowledgement undone':'Marked as seen')+' · '+esc(date(e.at))+'</p></div>'+button('undo-seen',e.id,e.undone?'Undone':'Undo','quiet',!canMutate()||e.undone)+'</article>'}});
 }
 function fillSettings(){
  if(settingsDirty)return;const values={...DEFAULTS,...state.settings};
  for(const id of ['checkOnLaunch','backgroundChecking','hideTitles','privacyScreen','notifications'])$(id).checked=!!values[id];
  for(const id of ['defaultIntervalMinutes','historyLimit'])setChoice($(id),values[id]);
  $('mutedOrigins').value=(values.mutedOrigins||[]).join('\n');$('mutedFolders').value=(values.mutedFolders||[]).join('\n');lastSettingsRevision=state.revision;
 }
 function setChoice(select,value){const str=String(value);if(![...select.options].some(option=>option.value===str)){const option=document.createElement('option');option.value=str;option.textContent=str;select.append(option)}select.value=str}
 function updatePrivacy(){const hidden=state.settings.privacyScreen&&!document.hasFocus();$('privacyCover').hidden=!hidden;$('app').setAttribute('aria-hidden',String(hidden));document.body.classList.toggle('privacy-active',hidden)}
 function render(){
  renderPages();renderSessions();renderHistory();if(view==='settings')fillSettings();updatePrivacy();
  $('recoveryBanner').hidden=!state.recoveryWarning;text('recoveryText',state.recoveryWarning||'');$('recoverStore').disabled=!ready||!!state.runtimeWarning;$('runtimeBanner').hidden=!state.runtimeWarning;text('runtimeBanner',state.runtimeWarning||'');
  $('importOpen').disabled=!canMutate();$('exportOpen').disabled=!ready;$('saveSettings').disabled=!canMutate()||busy.has('settings:');
  if(state.version)text('version','Rust '+state.version+' · WebView2');
  if(detailId&&!$('detailDrawer').hidden){const w=watchById.get(detailId),key=detailKey(w);if(key!==lastDetailKey){lastDetailKey=key;void loadDetail(detailId)}}
 }
 function accept(next){
  if(!next||!Array.isArray(next.watches)||!Array.isArray(next.sessions))return false;
  if(Number.isFinite(next.revision)&&next.revision<state.revision)return false;
  const fresh=next.prefill&&Number(next.prefill.revision)>prefillRevision;
  const key=JSON.stringify([next.storeRevision??next.revision,next.work,next.readOnly,next.recoveryWarning,next.runtimeWarning,next.version,next.prefill?.revision]);
  if(key===lastStateKey&&!fresh){state.revision=next.revision;return true;}
  lastStateKey=key;state={...next,settings:{...DEFAULTS,...next.settings},work:next.work||{active:[],queued:[]}};rebuildIndexes();render();
  if(fresh){prefillRevision=Number(next.prefill.revision);pendingPrefill=next.prefill;if(document.querySelector('dialog[open]'))$('prefillBanner').hidden=false;else usePrefill()}
  return true;
 }
 async function command(kind,value={},quiet=false){
  if(!api?.core?.invoke){if(!quiet)showToast('Open Watchtower from WATCH PAGES in Modhunter.exe.');return null}
  const key=kind+':'+(value.id||value.eventId||'');if(kind!=='detail'&&busy.has(key))return null;if(kind!=='detail')busy.add(key);
  try{const result=await api.core.invoke('watchtower_command',{kind,value});if(result?.ok===false)throw new Error(result.error||result.message||'The action could not be completed.');if(result?.state)accept(result.state);if(result?.message&&!quiet)showToast(result.message);return result||{ok:true}}
  catch(error){if(!quiet)showToast(errorText(error));return {ok:false,error:errorText(error)}}
  finally{if(kind!=='detail')busy.delete(key)}
 }
 function navigate(nextView,nextFilter='all'){
  view=nextView;filter=nextFilter;clearSelected();for(const name of ['pages','sessions','history','settings'])$(name+'View').hidden=name!==view;
  for(const button of $('navigation').querySelectorAll('[data-view]')){const active=button.dataset.view===view&&(view!=='pages'||button.dataset.filter===filter);button.classList.toggle('active',active);if(active)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current')}
  const names={all:'All pages',changed:'Changes to review',new:'Awaiting first check',attention:'Needs attention',paused:'Paused & muted',removed:'Removed pages'};text('viewTitle',view==='pages'?names[filter]:{sessions:'Site sign-ins',history:'Seen history',settings:'Preferences'}[view]);text('viewSubtitle',view==='pages'?'Track a release page, creator, changelog, or a page that needs your sign-in.':view==='sessions'?'Separate browser profiles, clear ownership, and a direct route back to your pages.':view==='history'?'Review and undo the page changes you acknowledged.':'Choose when to check, which sources to contact, and what stays visible.');render();
 }
 function openDialog(id){dialogReturn=document.activeElement;$(id).showModal()}
 function closeDialog(id){$(id).close();if(dialogReturn?.isConnected)dialogReturn.focus({preventScroll:true})}
 function confirmAction(title,message,label='Continue'){
  if(confirmation)return Promise.resolve(false);text('confirmTitle',title);text('confirmText',message);text('confirmProceed',label);openDialog('confirmDialog');return new Promise(resolve=>confirmation=resolve);
 }
 function finishConfirm(answer){const resolve=confirmation;confirmation=null;closeDialog('confirmDialog');resolve?.(answer)}
 function sessionChoices(selectedId){
  const select=$('pageSession'),value=selectedId===undefined?select.value:selectedId;select.replaceChildren();const first=document.createElement('option');first.value='';first.textContent='Public browser · no saved sign-in';select.append(first);
  const pageOrigin=origin(withScheme($('pageUrl').value));
  for(const s of state.sessions){const option=document.createElement('option');option.value=s.id;option.textContent=s.label+' · '+host(s.origin)+(pageOrigin&&s.origin!==pageOrigin?' (different site)':'');option.disabled=!!pageOrigin&&s.origin!==pageOrigin;select.append(option)}
  select.value=value&&state.sessions.some(s=>s.id===value&&(!pageOrigin||s.origin===pageOrigin))?value:'';
  text('sessionHint',editWatch&&needsSession(editWatch)?'This imported page needs an explicit session choice. Choose a saved session or select the public browser, then save.':'Choose a saved session for this exact site. Add one in Site sign-ins if needed.');
 }
 function withScheme(value){const input=String(value||'').trim();return /^[a-z][a-z\d+.-]*:/i.test(input)?input:'https://'+input}
 function validPage(value){const url=new URL(withScheme(value));if(url.protocol!=='https:'||url.username||url.password)throw new Error('Enter a public HTTPS page without a username or password in its address.');url.hash='';return url.href}
 function openPageForm(w=null,prefill=null){
  pageFormGeneration++;editWatch=w?{...w}:null;const source=w||prefill||{};text('pageDialogTitle',w?'Edit tracked page':'Add a page');text('savePage',w?'Save changes':'Save page');
  for(const [id,value] of Object.entries({pageUrl:source.url||'',pageTitle:source.title||'',pageFolder:source.folder||'',pageTags:(source.tags||[]).join(', '),pageSelector:source.selector||'',pageIgnore:(source.ignoreSelectors||[]).join('\n'),pageNotes:source.notes||''}))$(id).value=value;
  setChoice($('pageInterval'),source.intervalMinutes||state.settings.defaultIntervalMinutes||60);$('pageEnabled').checked=source.enabled!==false;$('checkAfterSave').checked=!w;sessionChoices(source.sessionId||'');
  const association=source.association||((source.profileId&&source.itemId)?{profileId:source.profileId,itemId:source.itemId,name:source.title||'',sourceUrl:source.url||''}:null);if(!w&&association)editWatch={association};
  $('associationNote').hidden=!association;text('associationNote',association?'Linked to '+(association.name||'a mod')+' in its MO2 profile. Page changes remain separate from reviewed update files.':'');
  const folders=[...new Set(state.watches.map(x=>x.folder))].filter(Boolean).sort();$('folderOptions').replaceChildren();for(const folder of folders){const option=document.createElement('option');option.value=folder;$('folderOptions').append(option)}
  formError('pageError','');$('savePage').disabled=!canMutate()||busy.has('saveWatch:'+(w?.id||''));openDialog('pageDialog');$('pageUrl').focus();
 }
 function usePrefill(){if(!pendingPrefill)return;const value=pendingPrefill;pendingPrefill=null;$('prefillBanner').hidden=true;openPageForm(null,value)}
 async function savePage(event){
  event.preventDefault();const generation=pageFormGeneration;formError('pageError','');let url;try{url=validPage($('pageUrl').value)}catch(error){formError('pageError',errorText(error));return}
  const payload={url,title:$('pageTitle').value.trim(),folder:$('pageFolder').value.trim(),tags:$('pageTags').value.split(',').map(x=>x.trim()).filter(Boolean),notes:$('pageNotes').value,sessionId:$('pageSession').value||null,selector:$('pageSelector').value.trim(),ignoreSelectors:$('pageIgnore').value.split('\n').map(x=>x.trim()).filter(Boolean),intervalMinutes:Number($('pageInterval').value),enabled:$('pageEnabled').checked,requiresSession:false,association:editWatch?.association||null};
  if(editWatch?.id){payload.id=editWatch.id;payload.generation=editWatch.generation}
  $('savePage').disabled=true;const check=$('checkAfterSave').checked;try{const result=await command('saveWatch',payload,true);if(!result?.ok){if(generation===pageFormGeneration&&$('pageDialog').open)formError('pageError',result?.error||'Could not save this page.');else showToast(result?.error||'The previous page could not be saved.');return}if(generation===pageFormGeneration&&$('pageDialog').open){closeDialog('pageDialog');navigate('pages','all')}showToast(result.duplicate?'This page is already tracked.':result.restored?'Page restored.':'Page saved.');if(check&&payload.enabled&&result.watchId)await command('check',{id:result.watchId})}finally{$('savePage').disabled=!canMutate()||busy.has('saveWatch:'+(editWatch?.id||''))}
 }
 function openSessionForm(s=null){sessionFormGeneration++;editSession=s?{...s}:null;text('sessionDialogTitle',s?'Edit site sign-in':'Add a site sign-in');$('sessionOrigin').value=s?.origin||'';$('sessionLabel').value=s?.label||'';$('sessionRemember').checked=s?.remember!==false;$('openAfterSave').checked=!s;formError('sessionError','');$('saveSession').disabled=!canMutate()||busy.has('saveSession:'+(s?.id||''));openDialog('sessionDialog');$('sessionOrigin').focus()}
 async function saveSession(event){
  event.preventDefault();const generation=sessionFormGeneration;formError('sessionError','');let url;try{url=validPage($('sessionOrigin').value)}catch(error){formError('sessionError',errorText(error));return}
  const payload={origin:new URL(url).origin,label:$('sessionLabel').value.trim(),remember:$('sessionRemember').checked};if(editSession?.id){payload.id=editSession.id;payload.storageGeneration=editSession.storageGeneration}
  if(editSession&&(payload.remember!==editSession.remember||payload.origin!==editSession.origin)){closeDialog('sessionDialog');const confirmed=await confirmAction('Start a fresh site session?','Changing the site or storage mode closes its browser windows and replaces its sign-in profile. Associated checks are cancelled.','Change session');if(!confirmed){openDialog('sessionDialog');return}openDialog('sessionDialog')}
  $('saveSession').disabled=true;const open=$('openAfterSave').checked;try{const result=await command('saveSession',payload,true);if(!result?.ok){if(generation===sessionFormGeneration&&$('sessionDialog').open)formError('sessionError',result?.error||'Could not save the site session.');else showToast(result?.error||'The previous session could not be saved.');return}if(generation===sessionFormGeneration&&$('sessionDialog').open){closeDialog('sessionDialog');navigate('sessions');const id=result.sessionId||result.id;if(open&&id)await command('openSession',{id})}showToast('Site session saved.')}finally{$('saveSession').disabled=!canMutate()||busy.has('saveSession:'+(editSession?.id||''))}
 }
 async function loadDetail(id){
  const request=++detailRequest;const result=await command('detail',{id},true);if(request!==detailRequest||detailId!==id||$('detailDrawer').hidden)return;if(!result?.ok){$('detailBody').textContent=result?.error||'Could not load page details.';return}
  const w=result.watch||result.detail?.watch;if(!w){$('detailBody').textContent='This page is no longer available.';return}renderDetail(w);
 }
 function openDetail(id){detailId=id;lastDetailKey=detailKey(watchById.get(id));$('detailDrawer').hidden=false;$('detailBody').textContent='Loading page history…';const w=watchById.get(id);text('detailTitle',w?displayTitle(w):'Page details');$('closeDetail').focus({preventScroll:true});void loadDetail(id)}
 function closeDetail(){detailId=null;detailRequest++;$('detailDrawer').hidden=true}
 function renderDetail(w){
  text('detailTitle',displayTitle(w));const isBusy=working(w.id),label=status(w),latest=w.latest,session=sessionById.get(w.sessionId);const privacy=state.settings.hideTitles;
  const actions=(w.removedAt?button('restore',w.id,'Restore','primary',!canMutate()):button('edit',w.id,'Edit page','',!canMutate())+(isBusy||stopped(w)?button('cancel',w.id,stopped(w)?'Clear stopped check':'Cancel check','quiet',!canMutate()):button('check',w.id,'Check now','primary',!canMutate()||!w.enabled||w.muted||needsSession(w)))+button('pause',w.id,w.enabled?'Pause':'Resume','quiet',!canMutate())+button('remove',w.id,'Remove','quiet danger-text',!canMutate()))+button('open',w.id,'Open in app','quiet',!ready)+button('external',w.id,'System browser','quiet',!ready)+(w.hasChanges&&latest?button('seen',w.id,'Mark this change seen','primary',!canMutate(),latest.id):'')+(w.sessionId&&['signInRequired','challenge'].includes(w.state)?button('session-open',w.sessionId,'Open sign-in / verify','primary',!canMutate()):'');
  const fields=[['Status',label[0]],['Page',privacy?origin(w.url):w.url],['Read using',session?session.label:'Public browser'],['Folder',w.folder||'None'],['Region',w.selector||'Readable page text'],['Ignore',(w.ignoreSelectors||[]).join(', ')||'None'],['Interval',interval(w.intervalMinutes)],['Last attempt',date(w.lastAttemptAt)],['Last success',date(w.lastSuccessAt)],['Next due',w.enabled&&!w.muted?date(Math.max(w.nextCheckAt||0,w.retryAt||0)):'Paused']];
  let html='<div class="detail-actions">'+actions+'</div><section class="detail-section"><h3>Tracking details</h3><dl>'+fields.map(([k,v])=>'<dt>'+esc(k)+'</dt><dd>'+esc(v)+'</dd>').join('')+'</dl>'+(w.lastError?'<p class="row-detail error">'+esc(w.lastError)+'</p>':'')+(!privacy&&w.notes?'<p>'+esc(w.notes)+'</p>':'')+(w.association?'<p>Linked mod: '+esc(privacy?'Name hidden':w.association.name||w.association.itemId)+'. Use the Update Workshop for release/file review.</p>':'')+'</section>';
  if(latest){html+='<section class="detail-section"><h3>Latest successful observation</h3><p>'+esc(date(latest.checkedAt))+' · '+Number(latest.textBytes||0).toLocaleString()+' bytes compared'+(latest.previewTruncated?' · preview shortened; the accepted full text was compared.':'.')+'</p>'+(privacy?'<p>Page content is hidden by your privacy preference.</p>':'<pre class="capture-preview">'+esc(latest.textPreview||'No preview stored.')+'</pre>')+'</section>'}
  const history=[...(w.history||[])].reverse();html+='<section class="detail-section"><h3>Page change history</h3>'+(history.length?history.map(entry=>'<details class="change-entry"><summary>'+esc(entry.kind==='baseline'?'First baseline':entry.summary||'Observed page change')+'<small>'+esc(date(entry.checkedAt))+'</small></summary>'+(privacy?'<p>Change text is hidden.</p>':((entry.added||[]).length?'<p>Added</p><pre class="change-lines added">'+esc(entry.added.join('\n'))+'</pre>':'')+((entry.removed||[]).length?'<p>Removed</p><pre class="change-lines removed">'+esc(entry.removed.join('\n'))+'</pre>':'')+(!(entry.added||[]).length&&!(entry.removed||[]).length?'<p>The accepted content fingerprint changed. Compare the page itself for details outside the retained preview.</p>':''))+'</details>').join(''):'<p>History begins with the first successful check.</p>')+'</section>';
  const content=$('detailBody'),scroll=content.scrollTop,focus=document.activeElement,action=content.contains(focus)?focus.dataset.action:null;content.innerHTML=html;content.scrollTop=scroll;if(action)[...content.querySelectorAll('[data-action]')].find(n=>n.dataset.action===action)?.focus({preventScroll:true});
 }
 async function action(kind,id,observationId){
  const w=watchById.get(id),s=sessionById.get(id);
  if(kind==='detail'){openDetail(id);return}if(kind==='edit'&&w){openPageForm(w);return}if(kind==='session-edit'&&s){openSessionForm(s);return}
  if(kind==='seen'&&w?.latest&&observationId){const result=await command('markSeen',{id,observationId});if(result?.ok){const eventId=result.eventId||result.seenEventId;showToast('Marked this observation as seen.',eventId?()=>command('undoSeen',{eventId}):null)}return}
  if(kind==='undo-seen'){await command('undoSeen',{eventId:id});return}
  if(kind==='remove'&&w){const result=await command('removeWatch',{id});if(result?.ok){showToast('Page moved to Removed pages.',()=>command('restoreWatch',{id}));if(detailId===id)closeDetail()}return}
  if(kind==='restore'){await command('restoreWatch',{id});return}
  if(kind==='pause'&&w){await command('pauseWatch',{id,enabled:!w.enabled});return}
  if(kind==='session-reset'&&s){if(await confirmAction('Forget this sign-in?','Close this session’s site windows, cancel its checks, and start a fresh browser profile. Your watched pages and observation history remain.','Forget sign-in'))await command('resetSession',{id});return}
  if(kind==='session-remove'&&s){if(await confirmAction('Remove this site session?','This closes its site windows and clears its saved browser profile. Linked pages are retained and will need a new session choice.','Remove session'))await command('removeSession',{id});return}
  if(kind==='session-confirm'&&s){if(await confirmAction('Ready to recheck this site?','Confirm after completing the website’s sign-in or verification. Enabled pages linked to this session will be checked again.','Confirm & recheck'))await command('confirmSession',{id});return}
  const mapping={check:'check',cancel:'cancel',open:'openPage',external:'openExternal','session-open':'openSession'};if(mapping[kind])await command(mapping[kind],{id});
 }
 async function bulk(kind){
  if(busy.has('bulk'))return;const chosen=[...selected].map(id=>watchById.get(id)).filter(Boolean);if(!chosen.length)return;
  if(kind==='remove'&&!await confirmAction('Remove '+chosen.length+' pages?','Their settings and history will remain available in Removed pages.','Remove pages'))return;
  let call,payload={},targets=chosen;
  if(kind==='check'){targets=chosen.filter(w=>w.enabled&&!w.muted&&!needsSession(w)&&!working(w.id)&&!stopped(w)&&!w.removedAt);call='check'}
  else if(kind==='seen'){targets=chosen.filter(w=>w.latest&&w.hasChanges&&selectedObservations.get(w.id));call='markSeen';payload.observations=targets.map(w=>({id:w.id,observationId:selectedObservations.get(w.id)}))}
  else if(kind==='pause'||kind==='resume'){call='pauseWatch';payload.enabled=kind==='resume'}
  else call=kind==='restore'?'restoreWatch':'removeWatch';
  if(kind!=='seen')payload.ids=targets.map(w=>w.id);if(!targets.length){showToast('No selected pages are eligible for this action.');return}
  busy.add('bulk');render();
  try{const result=await command(call,payload,true);if(result?.ok){showToast(targets.length+' page'+(targets.length===1?'':'s')+(kind==='check'?' queued.':kind==='seen'?' marked as seen.':' updated.'));clearSelected()}else showToast(result?.error||'The selection could not be changed. Your selection is retained.')}
  finally{busy.delete('bulk');render()}
 }
 function parseImport(value){const source=value.trim();if(!source)throw new Error('Paste a watchlist or at least one page address.');if(new TextEncoder().encode(source).length>8*1024*1024)throw new Error('Import is larger than 8 MiB. Split it into smaller files.');if(/^[\[{]/.test(source)){const document=JSON.parse(source);return Array.isArray(document)?{kind:'modhunter-watchtower',schemaVersion:1,watches:document.map(entry=>typeof entry==='string'?{url:withScheme(entry)}:entry)}:document}return {kind:'modhunter-watchtower',schemaVersion:1,watches:source.split(/\r?\n/).map(line=>line.trim()).filter(line=>line&&!line.startsWith('#')).map(url=>({url:withScheme(url),title:''}))}}
 function renderImportPreview(preview){
  const counts=preview.counts||{},items=preview.items||[];const invalid=Number(counts.invalid||0);$('importPreview').hidden=false;$('importPreview').innerHTML='<strong>'+Number(counts.new||0)+' new · '+Number(counts.duplicates||0)+' already tracked · '+Number(counts.restore||0)+' restored</strong>'+(invalid?'<p>'+invalid+' invalid entries. Fix them before importing.</p>':'')+(counts.sessionRequired?'<p>'+Number(counts.sessionRequired)+' pages will remain paused until you explicitly choose a session or the public browser.</p>':'')+(preview.warnings||[]).map(warning=>'<p>'+esc(warning)+'</p>').join('')+'<ul>'+items.map(item=>'<li>'+esc(item.title||item.url||'Entry '+(item.index+1))+' — '+esc(item.status)+(item.reason?': '+esc(item.reason):'')+'</li>').join('')+'</ul>';$('applyImport').disabled=invalid>0||!((counts.new||0)+(counts.restore||0));
 }
 async function previewImport(document=null){
  const generation=++importGeneration;formError('importError','');$('applyImport').disabled=true;try{const parsed=document||parseImport($('importText').value);const result=await command('import',{document:parsed,preview:true},true);if(generation!==importGeneration||!$('importDialog').open)return;if(!result?.ok)throw new Error(result?.error||'Import preview failed.');importDocument=parsed;importRevision=result.revision??state.revision;renderImportPreview(result.preview&&typeof result.preview==='object'?result.preview:result)}catch(error){if(generation!==importGeneration||!$('importDialog').open)return;formError('importError',errorText(error));importDocument=null;$('importPreview').hidden=true}
 }
 function openImport(){importGeneration++;importDocument=null;importRevision=null;$('importText').value='';$('importPreview').hidden=true;$('applyImport').disabled=true;formError('importError','');openDialog('importDialog')}
 async function applyImport(){if(!importDocument)return;$('applyImport').disabled=true;const result=await command('import',{document:importDocument,preview:false,previewRevision:importRevision},true);if(!result?.ok){formError('importError',result?.error||'Import failed. Review a fresh preview before retrying.');return}closeDialog('importDialog');navigate('pages');showToast('Watchlist imported. Check new pages to establish their baselines.')}
 async function savePreferences(event){
  event.preventDefault();const payload={};for(const id of ['checkOnLaunch','backgroundChecking','hideTitles','privacyScreen','notifications'])payload[id]=$(id).checked;payload.defaultIntervalMinutes=Number($('defaultIntervalMinutes').value);payload.historyLimit=Number($('historyLimit').value);payload.mutedOrigins=$('mutedOrigins').value.split('\n').map(x=>x.trim()).filter(Boolean).map(withScheme);payload.mutedFolders=$('mutedFolders').value.split('\n').map(x=>x.trim()).filter(Boolean);
  const generation=settingsGeneration;$('saveSettings').disabled=true;const result=await command('settings',payload,true);if(result?.ok&&generation!==settingsGeneration){settingsDirty=true;text('settingsDirty','Earlier preferences saved. Your newer edits are still unsaved.');showToast('Preferences saved; your newer edits are still in the form.')}else if(result?.ok){settingsDirty=false;lastSettingsRevision=state.revision;fillSettings();text('settingsDirty','Preferences saved on this device.');showToast('Watchtower preferences saved.');updatePrivacy()}else showToast(result?.error||'Preferences could not be saved.');$('saveSettings').disabled=!canMutate()||busy.has('settings:');
 }
 $('navigation').addEventListener('click',event=>{const target=event.target.closest('[data-view]');if(target)navigate(target.dataset.view,target.dataset.filter||'all')});
 $('watchList').addEventListener('change',event=>{const id=event.target.dataset.select;if(!id)return;selectWatch(id,event.target.checked);renderPages()});
 for(const id of ['watchList','sessionList','historyList','detailBody'])$(id).addEventListener('click',event=>{const target=event.target.closest('[data-action]');if(target&&!target.disabled)void action(target.dataset.action,target.dataset.id,target.dataset.observationId)});
 $('search').addEventListener('input',()=>{query=$('search').value.trim().toLocaleLowerCase();if(searchFrame!==null)cancelAnimationFrame(searchFrame);searchFrame=requestAnimationFrame(()=>{searchFrame=null;renderPages()})});
 for(const id of ['siteFilter','folderFilter','sort'])$(id).addEventListener('change',renderPages);
 $('selectAll').onchange=()=>{for(const w of visible){selectWatch(w.id,$('selectAll').checked)}renderPages()};$('clearSelection').onclick=()=>{clearSelected();renderPages()};$('selectionBar').addEventListener('click',event=>{const target=event.target.closest('[data-bulk]');if(target)void bulk(target.dataset.bulk)});
 $('addPage').onclick=$('emptyAdd').onclick=()=>openPageForm();$('addSession').onclick=$('emptyAddSession').onclick=()=>openSessionForm();$('emptyReset').onclick=()=>{query='';$('search').value='';$('siteFilter').value='';$('folderFilter').value='';navigate('pages')};
 $('checkAll').onclick=()=>command('checkAll',{dueOnly:false});$('checkDue').onclick=()=>command('checkAll',{dueOnly:true});$('cancelAll').onclick=()=>command('cancelAll');$('closeDetail').onclick=closeDetail;
 $('pageForm').onsubmit=savePage;$('sessionForm').onsubmit=saveSession;$('pageUrl').addEventListener('input',()=>sessionChoices());$('pageSession').addEventListener('change',()=>{if(editWatch)editWatch.requiresSession=false});
 for(const button of document.querySelectorAll('[data-close-dialog]'))button.onclick=()=>closeDialog(button.dataset.closeDialog);
 $('confirmProceed').onclick=()=>finishConfirm(true);$('confirmCancel').onclick=()=>finishConfirm(false);$('confirmDialog').addEventListener('cancel',event=>{event.preventDefault();finishConfirm(false)});
 $('importOpen').onclick=openImport;$('previewImport').onclick=()=>previewImport();$('applyImport').onclick=applyImport;$('importText').addEventListener('input',()=>{importGeneration++;importDocument=null;importRevision=null;$('applyImport').disabled=true;$('importPreview').hidden=true});
 $('importDialog').addEventListener('close',()=>{importGeneration++;importDocument=null;importRevision=null});
 $('pickImportFile').onclick=async()=>{const generation=++importGeneration;const result=await command('importFile',{},true);if(generation!==importGeneration||!$('importDialog').open||!result||result.cancelled)return;if(!result.ok){formError('importError',result.error);return}if(result.document){$('importText').value=JSON.stringify(result.document,null,2);await previewImport(result.document)}};
 $('exportOpen').onclick=()=>{exportGeneration++;$('exportFormat').value='json';$('exportHistory').checked=false;$('exportHistory').disabled=false;formError('exportError','');openDialog('exportDialog')};
 $('exportFormat').onchange=()=>{$('exportHistory').disabled=$('exportFormat').value!=='json';if($('exportHistory').disabled)$('exportHistory').checked=false};
 $('exportForm').onsubmit=async event=>{event.preventDefault();$('saveExport').disabled=true;formError('exportError','');const generation=exportGeneration,format=$('exportFormat').value;try{const result=await command('exportFile',{format,includeHistory:format==='json'&&$('exportHistory').checked},true);if(result?.ok){closeDialog('exportDialog');showToast(result.message||'Watchlist exported to Downloads.')}else formError('exportError',result?.error||'The watchlist could not be exported.')}finally{$('saveExport').disabled=false}};
$('settingsForm').onsubmit=savePreferences;$('settingsForm').addEventListener('input',()=>{settingsGeneration++;settingsDirty=true;text('settingsDirty','Unsaved preference changes.');});$('settingsForm').addEventListener('change',()=>{settingsGeneration++;settingsDirty=true;text('settingsDirty','Unsaved preference changes.');});
 $('usePrefill').onclick=usePrefill;$('toastClose').onclick=hideToast;$('recoverStore').onclick=async()=>{if(await confirmAction('Recover Watchtower storage?','The unreadable original will be preserved before a new empty store is created. Export or copy the original recovery file before trying a manual repair.','Preserve & start fresh'))await command('recover',{confirm:true})};
 window.addEventListener('blur',updatePrivacy);window.addEventListener('focus',()=>{updatePrivacy();if(ready)void command('state',{},true)});
 document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!document.querySelector('dialog[open]'))closeDetail();if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='f'&&!document.querySelector('dialog[open]')){event.preventDefault();navigate('pages',filter);$('search').focus()}if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='n'&&!document.querySelector('dialog[open]')&&canMutate()){event.preventDefault();openPageForm()}});
 const unsubscribe=[];window.addEventListener('beforeunload',()=>{disposed=true;clearTimeout(toastTimer);if(searchFrame!==null)cancelAnimationFrame(searchFrame);for(const stop of unsubscribe)stop()},{once:true});
 async function poll(){if(disposed)return;if(ready&&!document.hidden){await command('state',{},true);renderPages()}if(!disposed)setTimeout(poll,15000)}
 async function start(){
  render();if(!api?.core?.invoke||!api?.event?.listen){$('connectionBanner').hidden=false;text('connectionBanner','Open WATCH PAGES in the Rust desktop to manage real pages and site sign-ins.');return}
  unsubscribe.push(await api.event.listen('watchtower-state',event=>accept(event.payload)));ready=true;const result=await command('state',{},true);if(!result?.ok){ready=false;$('connectionBanner').hidden=false;text('connectionBanner',result?.error||'Watchtower could not connect. Reopen this window from the desktop toolbar.')}else $('connectionBanner').hidden=true;render();poll();
 }
 start().catch(error=>{$('connectionBanner').hidden=false;text('connectionBanner','Watchtower startup: '+errorText(error))});
})();
