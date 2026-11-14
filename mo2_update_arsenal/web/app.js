(()=>{if(window.__ARSENAL_REALM_ROUTE__)return;
'use strict';
let DATA=JSON.parse(document.getElementById('embeddedCatalog').textContent);
const MO2_EXPORTER=/*__POWERSHELL__*/;
let ITEM_MAP=new Map(DATA.items.map(m=>[m.id,m]));
let GROUP_MAP=new Map(DATA.groups.map(g=>[g.id,g]));
const PALETTE={gold:'#e2b975',stone:'#92959d',purple:'#b78cde',violet:'#a26cfa',mint:'#87d9bf',ochre:'#d5b77a',slate:'#8291af',sky:'#75b9d5',blue:'#6eabdc',green:'#86d58c',red:'#e16a6d',pink:'#df98b5',rose:'#e3b8c5',cyan:'#72d8e4',yellow:'#f2dc8b'};
let STORAGE='dragonborn-mod-update-arsenal-v1';
const BRIDGE=window.__MO2_BRIDGE_BOOT__||{active:false};
const INSIGHTS=window.ModhunterInsights;
const SOURCES=window.ModhunterSources;
let LIVE_SCAN=null,LIVE_CATALOG=null,SCAN_TIMER=null,FILE_PICKER_MOD=null,LIVE_GENERATION=0,LIVE_REFRESH_BUSY=false;
let WORKSHOP_PLAN=null,WORKSHOP_CHOICES=new Map(),WORKSHOP_BUSY=false,WORKSHOP_REVIEW_ID=null,FAST_PICKER_ID=null;
let QUICK_RUNNING=false,QUICK_POLL=null,QUICK_SKIPPED=new Map();
let QUEUE_PENDING=null,INSTALL_PREVIEW_ID=null,INSTALL_PREVIEW_PROFILE='',PREVIEW_RETURN_FOCUS=null;
let WORKSHOP_TICKETS={},WORKSHOP_TICKET_SIGNATURE='',DOWNLOAD_REFRESH_BUSY=false,DOWNLOAD_REFRESH_AGAIN=false;
let ACTIVITY_RECORDS=[],ACTIVITY_GENERATION=0,ACTIVITY_WARNING='';
// Requests may finish after MO2 switches profiles, including A -> B -> A.
// An epoch, rather than the profile name alone, keeps those responses isolated.
let PROFILE_EPOCH=0,CATALOG_REQUEST=0,WORKSHOP_REQUEST=0,GAME_REQUEST=0,BRIDGE_READ_REVISION=0;
let SCAN_OPERATION=0,SCAN_START_BUSY=false,QUICK_OPERATION=0,WORKSHOP_OPERATION=0;
let LIVE_CONNECTED=false,LIVE_POLL_TIMER=null,LIVE_INIT_PROMISE=null,GAME_SIGNATURE='';
let STARTUP_REQUEST=null,STARTUP_ACCEPTED='',STARTUP_RETRY_AT=0,STARTUP_ERROR='';
const DETECTED_CACHE=new WeakMap();
let SEARCH_FRAME=null,WORKSHOP_SEARCH_FRAME=null,SAVE_TIMER=null,SAVE_REVISION=0,PROGRESS_SYNC_CHAIN=Promise.resolve();
let RENDER_COUNTS=null,LOCAL_PLAN_CACHE=null,CATALOG_RENDER_REVISION=0;
let SOURCE_EDIT_REVISION=0,SOURCE_SYNCED_REVISION=0;
const BRIDGE_READS=new Map(),SOURCE_CACHE=new WeakMap(),SEARCH_CACHE=new WeakMap(),HTML_CACHE=new WeakMap();
const ROW_RENDER_CACHE=new WeakMap();
const MOD_MARKUP_CACHE=new WeakMap(),WORKSHOP_MARKUP_CACHE=new WeakMap();
const INSIGHT_REQUESTS=new Map();
const SOURCE_DETAIL_READS=new WeakMap(),SOURCE_DETAILS=new WeakMap();
let GAME=null,GAME_SEEN=-1,PROGRESS_READY=false,PROGRESS_TIMER=null,NATIVE_SELECTED='',VICTORY_ACTIVE=null,VICTORY_TIMERS=[],PRACTICE_CROWNED=false;
const BADGE_META=[['first-blood','First Blood','⚔',1],['apprentice','Apprentice Smith','🔨',5],['veteran','Veteran of the Forge','🛡',10],['archmage','Master Enchanter','✦',25],['dragonborn','Dragonborn','🐉',50],['legend','Legendary Smith','♛',100],['world-eater','World-Eater Defeated','👑',null]];

const BASE={rows:{},prefs:{filter:'all',insight:'all',query:'',sort:'priority',theme:'night',density:'compact',effect:'random',sound:false,focus:false,collapsed:[],autoCheckOnLaunch:true,vfxIntensity:'off',vfxShake:false,vfxTrails:false},lastSelected:null,verifiedRuntime:null};
let STORE=structuredClone(BASE),persistOK=true,focusedRowId='',lastUndo=null,openedId=null,toastTimer=null,activeFx=[],MO2_PENDING=[];
const $=(id)=>document.getElementById(id);
const txt=(v)=>String(v??'');
const escapeHTML=(v)=>txt(v).replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const validUrl=(s)=>{try{const u=new URL(s);return ['https:','http:'].includes(u.protocol)?u.href:''}catch{return''}};
const getRow=(id)=>STORE.rows[id]||{};
const getStatus=(m)=>getRow(m.id).status||'pending';
const getAudit=(m)=>{const a=m.audit||{};return{verdict:getRow(m.id).compat||a.verdict||'unknown',note:a.note||'',evidence:a.evidence||''}};
const resolveSrc=(m)=>{
 const custom=validUrl(getRow(m.id).url),src=m.source||{};
 if(custom)return{url:custom,verified:true,custom:true,type:'custom',label:'Your saved link'};
 const official=validUrl(src.url);
 if(src.verified&&official)return{url:official,verified:true,custom:false,type:src.type||'official',label:src.label||'Linked mod source'};
 // MO2's native Nexus project ID identifies an exact project even when the
 // separate source record was not populated. Never invent a GitHub identity.
 const game=String(Object.hasOwn(m,'sourceGame')?(m.sourceGame||'unknown'):(LIVE_CATALOG?.game||DATA?.game||'unknown')).toLowerCase();
 const knownGame=game!=='unknown'&&/^[a-z0-9_-]+$/.test(game);
 const nexusId=Number(m.nexusId);
 if(m.updateEligible!==false&&Number.isSafeInteger(nexusId)&&nexusId>0&&knownGame)
  return{url:'https://www.nexusmods.com/'+game+'/mods/'+nexusId,verified:true,custom:false,type:'nexus',label:'MO2 Nexus project ID'};
 if(official)return{url:official,verified:false,custom:false,type:src.type||'website',label:src.label||'Unverified source link'};
 return{url:'https://www.google.com/search?q='+encodeURIComponent((m.group==='Verified Creations'?'site:creations.bethesda.net/en/skyrim/details ':knownGame?'site:nexusmods.com/'+game+'/mods/ ':'')+'"'+m.name+'"'),verified:false,custom:false,type:'lookup',label:'Search for original source'};
};
const getSrc=(m)=>{const custom=getRow(m.id).url||'',game=LIVE_CATALOG?.game||DATA?.game||'',cached=SOURCE_CACHE.get(m);if(cached&&cached.revision===CATALOG_RENDER_REVISION&&cached.custom===custom&&cached.game===game)return cached.source;const source=resolveSrc(m);SOURCE_CACHE.set(m,{revision:CATALOG_RENDER_REVISION,custom,game,source});return source};
// Only the native-owned dashboard receives this capability marker. The native
// navigation handler still validates the originating view and the public URL.
const nativeWatchAvailable=()=>window.__MODHUNTER_NATIVE_WATCHTOWER__===true;
function trackPageIntent(mod,typedSource=''){
 if(!nativeWatchAvailable()||!mod)return '';
 const typed=String(typedSource||'').trim(),source=getSrc(mod),candidate=typed||(source.verified?source.url:'');
 if(!candidate)return '';
 try{const page=new URL(candidate);if(page.protocol!=='https:'||page.username||page.password)return '';page.hash='';
  const intent=new URL('modhunter-watch://track');intent.searchParams.set('url',page.href);intent.searchParams.set('title',mod.name||'');
  const profileId=LIVE_CATALOG?.profileId||DATA.profileId;
  if(profileId&&mod.id){intent.searchParams.set('profileId',profileId);intent.searchParams.set('itemId',mod.id)}
  return intent.href;
 }catch{return ''}
}
function trackModPage(mod,typedSource=''){
 const intent=trackPageIntent(mod,typedSource);if(!intent){toast('Enter the exact public HTTPS page you want to track.');return false}
 window.location.assign(intent);return true;
}
const isRisk=(m)=>{const {verdict}=getAudit(m);return ['hold','check','avoid','ae-only'].includes(verdict)||(m.flags||[]).some(f=>['HARD_ESP','HARD_ESM','DLL','NEMESIS'].includes(f))};
const isUpdate=(m)=>m.update&&m.update.verdict==='candidate'&&!(sourceTimePhase(m)&1)&&!(Number(m.update.retryAt)>Date.now()/1000);
const hasReleaseAssets=m=>SOURCES?SOURCES.hasAssets(m):Boolean(m.update?.releaseAssets?.length||m.releaseAssets?.length);
const providerLabel=p=>SOURCES?.label(p)||p||'Original provider';

function detectedStatus(mod){
 const u=mod.update||{},source=getSrc(mod),phase=sourceTimePhase(mod);
 const checking=LIVE_SCAN?.state==='running',context=[u,mod.localAnalysis,mod.modHealth,source.url,source.verified,getRow(mod.id).compat,phase,checking,BRIDGE.active,LIVE_CONNECTED,CATALOG_RENDER_REVISION];
 const cached=DETECTED_CACHE.get(mod);if(cached&&context.every((v,i)=>Object.is(v,cached.context[i])))return cached.value;
 const capabilities=u.capabilities||mod.sourceCapabilities||{},provider=SOURCES?.provider(mod)||source.type;
 const health=INSIGHTS?.summary(mod)||{},maintenance=Boolean(health.stale||health.masterIssues||health.invalid||['hold','avoid','ae-only'].includes(getAudit(mod).verdict));
 let kind='not-checked',label='Not checked',reason=u.reason||'No completed source check for this installed mod.';
 const unavailable=['unavailable','not-found','deleted','blocked','error'].includes(u.availability)||u.outcome==='unavailable';
 const manual=['manual','api-key'].includes(capabilities.checkMode)||(!['nexus','github','gitlab','modrinth','thunderstore'].includes(provider)&&source.verified);
 const remote=Boolean(u.checkedAt)&&u.source!=='mo2-cached-metadata';
 if(!source.verified){kind='source-needed';label='Source needed';reason='Set the exact original source in Details to enable update checks.'}
 else if(u.availability==='authentication-required'){kind='sign-in';label='Sign-in required'}
 else if(Number(u.retryAt)>Date.now()/1000||u.availability==='rate-limited'){kind='blocked';label='Provider cooldown'}
 else if(unavailable){kind='blocked';label='Check failed'}
 else if(manual){kind='manual';label=capabilities.checkMode==='api-key'?'Provider key needed':'Page review';reason=u.reason||'This source needs page tracking or original-page review; an automatic release API is not available.'}
 else if(phase&1){kind=checking?'checking':'stale';label=checking?'Rechecking':'Recheck needed';reason='Previous source evidence expired. '+reason}
 else if(u.verdict==='candidate'){kind='update';label=remote?'Update available':'Update hinted';reason=u.reason||'A newer project version was found. Review its exact files and requirements.'}
 else if(remote&&u.verdict==='current'){kind='current';label='Up to date';reason='Installed project version matches the latest checked metadata. File/runtime compatibility is separate.'}
 else if(remote&&u.verdict==='newer-installed'){kind='ahead';label='Installed is newer'}
 else if(u.verdict==='review'){kind='review';label='Version review'}
 else if(u.verdict==='unknown'&&(u.checkedAt||u.reason)){kind='review';label='Needs review'}
 else if(checking&&BRIDGE.active){kind='checking';label='Checking'}
 else if(!BRIDGE.active){kind='offline';label='Read-only';reason='Connect MO2 for remote update and live maintenance checks.'}
 else if(!LIVE_CONNECTED){kind='offline';label='Disconnected';reason='Last known profile. Reconnect MO2 to verify updates.'}
 const value={kind,label,reason,maintenance,updateAvailable:kind==='update',current:kind==='current',attention:['not-checked','source-needed','sign-in','blocked','manual','stale','review','offline'].includes(kind)};
 DETECTED_CACHE.set(mod,{context,value});return value;
}
function detectedMarkup(mod){const d=detectedStatus(mod);return '<span class="detected-status '+d.kind+'" title="'+escapeHTML(d.reason)+'">'+escapeHTML(d.label)+'</span>'+(d.maintenance?'<button class="maintenance-status" type="button" data-act="inspect" data-id="'+escapeHTML(mod.id)+'" title="Detected plugin, conversion, master or explicit compatibility issue. Open Details for evidence.">Maintenance</button>':'')}
function selectWorkspace(panel){
 if(!['mods','updates','downloads','history','tools','extras'].includes(panel))return;
 document.body.dataset.workspace=panel;
 $all('.workspace-tabs [data-workspace]').forEach(button=>{const on=button.dataset.workspace===panel;button.classList.toggle('active',on);button.setAttribute('aria-pressed',String(on))});
 if(panel==='downloads')void refreshDownloads();if(panel==='history')void refreshActivity();
}
function renderWorkspace(){
 const aggregate={all:DATA.items.length,updates:0,maintenance:0,attention:0,current:0};
 for(const mod of DATA.items){const d=detectedStatus(mod);aggregate.updates+=Number(d.updateAvailable);aggregate.maintenance+=Number(d.maintenance);aggregate.attention+=Number(d.attention);aggregate.current+=Number(d.current)}
 for(const [key,value] of Object.entries(aggregate))SET('detected'+key[0].toUpperCase()+key.slice(1),value.toLocaleString());
 SET('workspaceProfile',LIVE_CATALOG?LIVE_CATALOG.profileName+' / '+(LIVE_CATALOG.game||'MO2')+' / '+DATA.items.length.toLocaleString()+' mods':DATA.offline?(DATA.profileName||'Imported profile')+' / read only':'Demo library / not your installed mods');
 const state=LIVE_SCAN?.state,active=state==='running';
 let status=!BRIDGE.active?'Read-only library':!LIVE_CONNECTED?'MO2 disconnected':active?'Checking sources':state==='error'?'Check failed':state==='cancelled'?'Check cancelled':state==='partial'?'Check finished with issues':state==='completed'?'Check complete':STARTUP_ERROR?'Automatic check unavailable':!PROGRESS_READY?'Loading saved preferences':sourceLinksPending()?'Syncing source edits':STORE.prefs.autoCheckOnLaunch?'Preparing automatic check':'Automatic checks off';
 SET('workspaceCheckStatus',status);
 SET('workspaceCheckDetail',active?(LIVE_SCAN.checked||0)+' / '+(LIVE_SCAN.total||'?')+' checked'+(LIVE_SCAN.fromCache?' / '+LIVE_SCAN.fromCache+' cached':''):LIVE_SCAN?.error||STARTUP_ERROR||((LIVE_SCAN?.errors||[]).length?(LIVE_SCAN.errors.length+' source errors; see affected mods'):'Updates never install without approval'));
 const button=$('unifiedScan');if(button){button.disabled=BRIDGE.active&&(!LIVE_CONNECTED||SCAN_START_BUSY||active);button.textContent=active?'Checking...':BRIDGE.active?'Check for updates':'Connect MO2'}
 const dot=$('workspaceDot');if(dot)dot.dataset.state=active?'busy':BRIDGE.active&&LIVE_CONNECTED?'connected':'offline';
 const cancel=$('workspaceCancel');if(cancel)cancel.hidden=!active;
 const toggle=$('autoCheckOnLaunch');if(toggle)toggle.checked=STORE.prefs.autoCheckOnLaunch!==false;
}
async function ensureStartupCheck(){
 if(!BRIDGE.active||!LIVE_CONNECTED||!LIVE_CATALOG||!PROGRESS_READY||sourceLinksPending()||STORE.prefs.autoCheckOnLaunch===false)return;
 const context=profileContext(),key=context.epoch+':'+context.profile+':'+LIVE_CATALOG.executionEpoch;
 if(STARTUP_REQUEST||STARTUP_ACCEPTED===key||Date.now()<STARTUP_RETRY_AT)return;
 STARTUP_REQUEST=key;
 try{
  const result=await bridgeRequest('POST','startup-check',{profileId:context.profile,executionEpoch:LIVE_CATALOG.executionEpoch});
  if(!contextIsCurrent(context))return;
  STARTUP_ACCEPTED=key;STARTUP_RETRY_AT=0;STARTUP_ERROR='';
  if(result.scan){updateScanUI(result.scan);if(result.scan.state==='running')startScanPolling('startup',context);else if(result.started)await requestLiveCatalog()}
 }catch(error){if(contextIsCurrent(context)){STARTUP_RETRY_AT=Date.now()+60000;STARTUP_ERROR='Automatic check could not start: '+error.message;renderWorkspace();console.warn('Startup check:',error.message)}}
 finally{if(STARTUP_REQUEST===key)STARTUP_REQUEST=null}
}

const isDone=(m)=>getStatus(m)==='done';
const isReview=(m)=>getStatus(m)==='review';
const isFav=(m)=>Boolean(getRow(m.id).favorite);
const $all=(s,base=document)=>[...base.querySelectorAll(s)];
function load(){try{const x=JSON.parse(localStorage.getItem(STORAGE)||'null');if(x&&typeof x==='object'){
 for(const [id,raw] of Object.entries(x.rows||{})){if(ITEM_MAP.has(id)&&raw&&typeof raw==='object')STORE.rows[id]={...raw}}
 for(const k of Object.keys(BASE.prefs))if(x.prefs&&k in x.prefs)STORE.prefs[k]=x.prefs[k];
 STORE.lastSelected=x.lastSelected||null;STORE.verifiedRuntime=x.verifiedRuntime||null;
}}catch(e){persistOK=false;console.warn('Local save unavailable:',e)}
 if(!Array.isArray(STORE.prefs.collapsed))STORE.prefs.collapsed=[];
}
function save(invalidatePlan=true){clearTimeout(SAVE_TIMER);SAVE_TIMER=null;SAVE_REVISION++;if(invalidatePlan)LOCAL_PLAN_CACHE=null;try{localStorage.setItem(STORAGE,JSON.stringify(STORE));persistOK=true}catch(e){persistOK=false;console.warn('Local storage failed:',e)}updateStorageIndicator();if(BRIDGE.active&&LIVE_CATALOG&&PROGRESS_READY)scheduleProgressSync()}
function saveSoon(){clearTimeout(SAVE_TIMER);SAVE_TIMER=setTimeout(()=>save(false),180)}
function profileContext(){return{profile:LIVE_CATALOG?.profileId,epoch:PROFILE_EPOCH}}
function contextIsCurrent(context){return context.epoch===PROFILE_EPOCH&&context.profile===LIVE_CATALOG?.profileId}
function setHTML(node,html){if(node&&HTML_CACHE.get(node)!==html){node.innerHTML=html;HTML_CACHE.set(node,html)}}
// A catalog replacement invalidates all presentation metadata. Local row edits
// and asynchronously replaced analysis objects invalidate only their own row.
// Provider deadlines remain live even when no response or object has changed.
function sourceTimePhase(mod){const update=mod.update||{},now=Date.now()/1000;return(Number(update.expiresAt)>0&&Number(update.expiresAt)<=now?1:0)|(Number(update.retryAt)>now?2:0)}
function cachedMarkup(cache,key,dependencies,build){
 const cached=cache.get(key);
 if(cached&&cached.dependencies.length===dependencies.length&&dependencies.every((value,index)=>Object.is(value,cached.dependencies[index])))return cached.html;
 const html=build();cache.set(key,{dependencies,html});return html;
}
// Keep the complete list in the DOM. A ticket or checklist change replaces
// only its changed rows, preserving unrelated links, focus and scroll state.
// Structural changes (filtering, sorting, profile replacement) use one parse.
function replaceRenderedRow(previous,entry,template){
 const old=previous.node,focused=document.activeElement;
 const action=old.contains(focused)?['data-act','data-workshop-act','data-insight-act','data-source-act','data-download-id','data-download-details','data-retry-download-id','data-retry-item-id'].find(name=>focused.hasAttribute(name)):null;
 const actionValue=action?focused.getAttribute(action):null;
 template.innerHTML=entry.html;
 const next=template.content.firstElementChild;
 old.replaceWith(next);
 if(action){const control=[...next.querySelectorAll('['+action+']')].find(el=>el.getAttribute(action)===actionValue);if(control&&!control.disabled)control.focus({preventScroll:true});}
 return {...entry,node:next};
}
function setRows(node,entries,empty,preserveMoves=false){
 if(!node)return;
 const previous=ROW_RENDER_CACHE.get(node);
 // Download status changes can reorder tickets. Retain their controls through
 // moves as well as replacements, so a background poll cannot steal focus.
 const intact=previous&&node.children.length===previous.length&&previous.every((entry,i)=>entry.node===node.children[i]);
 if(preserveMoves&&entries.length&&intact&&new Set(entries.map(entry=>entry.key)).size===entries.length){
  const byKey=new Map(previous.map(entry=>[entry.key,entry])),template=document.createElement('template');
  const next=entries.map(entry=>{
   const prior=byKey.get(entry.key);byKey.delete(entry.key);
   if(prior)return prior.html===entry.html?prior:replaceRenderedRow(prior,entry,template);
   template.innerHTML=entry.html;return {...entry,node:template.content.firstElementChild};
  });
  const focused=document.activeElement;
  let cursor=node.firstElementChild;
  for(const entry of next){if(entry.node!==cursor)node.insertBefore(entry.node,cursor);cursor=entry.node.nextElementSibling;}
  for(const entry of byKey.values())entry.node.remove();
  if(focused?.isConnected&&node.contains(focused)&&document.activeElement!==focused)focused.focus({preventScroll:true});
  ROW_RENDER_CACHE.set(node,next);HTML_CACHE.delete(node);return;
 }
 if(intact&&previous.length===entries.length&&entries.every((entry,i)=>entry.key===previous[i].key)){
  const changed=[];
  for(let i=0;i<entries.length;i++)if(entries[i].html!==previous[i].html)changed.push(i);
  if(!changed.length)return;
  if(changed.length<=Math.max(8,entries.length/3)){
   const template=document.createElement('template');
   for(const i of changed){
    previous[i]=replaceRenderedRow(previous[i],entries[i],template);
   }
   HTML_CACHE.delete(node);
   return;
  }
 }
 if(previous)HTML_CACHE.delete(node);
 setHTML(node,entries.length?entries.map(entry=>entry.html).join(''):empty);
 const children=[...node.children];
 // Do not retain an index if a host extension altered the generated structure.
 if(entries.length&&children.length===entries.length)ROW_RENDER_CACHE.set(node,entries.map((entry,i)=>({...entry,node:children[i]})));
 else ROW_RENDER_CACHE.delete(node);
}
function sourceLinksPending(){return BRIDGE.active&&SOURCE_EDIT_REVISION!==SOURCE_SYNCED_REVISION}
function markSourceEdit(previous,next){if(BRIDGE.active&&txt(previous)!==txt(next))SOURCE_EDIT_REVISION++;}
function scheduleProgressSync(){clearTimeout(PROGRESS_TIMER);const context=profileContext();if(!context.profile)return;PROGRESS_TIMER=setTimeout(()=>{
 // Serialize writes and clone only when sending, rather than cloning every
 // row on each search keystroke. The final write always contains latest state.
 PROGRESS_SYNC_CHAIN=PROGRESS_SYNC_CHAIN.then(async()=>{
  if(!contextIsCurrent(context))return;
  const sourceRevision=SOURCE_EDIT_REVISION;
  const snapshot={profileId:context.profile,rows:structuredClone(STORE.rows),prefs:structuredClone(STORE.prefs),verifiedRuntime:STORE.verifiedRuntime};
  try{
   const result=await bridgeRequest('POST','progress',snapshot);
   if(!contextIsCurrent(context))return;
   let acceptedContext=context;
   if(result.sourceLinksChanged){
    const snap=await requestLiveCatalog('GET','snapshot',undefined,true);if(!snap)return;
    acceptedContext=profileContext();await snap.ready;
   }
   const sameExecution=!Number.isSafeInteger(result.executionEpoch)||LIVE_CATALOG?.executionEpoch===result.executionEpoch;
   if(contextIsCurrent(acceptedContext)&&sameExecution&&sourceRevision===SOURCE_EDIT_REVISION){
    const wasPending=sourceLinksPending();SOURCE_SYNCED_REVISION=sourceRevision;
    if(wasPending)renderWorkshop();
   }
  }catch(ex){if(contextIsCurrent(context)){console.warn('MO2 Chronicle save:',ex.message);SET('liveState','Local save retained. MO2 Chronicle sync: '+ex.message)}}
 });
},260)}
async function hydrateProgress(profile){const context=profileContext(),revision=SAVE_REVISION,query=STORE.prefs.query,rowsBefore=structuredClone(STORE.rows);let hydrated=false;try{const r=await bridgeRequest('GET','progress');if(!contextIsCurrent(context)||r.profileId!==profile)return;const stored=r.progress||{};
 if(stored.rows&&typeof stored.rows==='object'){for(const [id,row]of Object.entries(stored.rows)){if(ITEM_MAP.has(id)&&row&&typeof row==='object'){
  const local=getRow(id),incoming=Date.parse(row.updatedAt||0)||0,existing=Date.parse(local.updatedAt||0)||0;
  const merged=incoming>=existing?{...local,...row}:{...local},before=rowsBefore[id]||{};
  // Preserve fields edited while this read was in flight, including favorites
  // and source links whose older saved records may have no timestamp.
  for(const key of new Set([...Object.keys(before),...Object.keys(local)])){
   if(JSON.stringify(before[key])!==JSON.stringify(local[key])){
    if(Object.hasOwn(local,key))merged[key]=local[key];else delete merged[key];
   }
  }
  STORE.rows[id]=merged;
 }}}
 if(revision===SAVE_REVISION&&query===STORE.prefs.query&&stored.prefs&&typeof stored.prefs==='object'){for(const k of Object.keys(BASE.prefs))if(k in stored.prefs)STORE.prefs[k]=stored.prefs[k]}
 for(const [id,row] of Object.entries(STORE.rows))markSourceEdit(stored.rows?.[id]?.url,row.url);
 if(stored.verifiedRuntime)STORE.verifiedRuntime=stored.verifiedRuntime;
 // Server-origin progress still must be invalidated if the actual installed
 // version/runtime changed since the last save (including after MO2 restart).
 const currentRuntime=LIVE_CATALOG.gameRuntime||'unknown';
 if(STORE.verifiedRuntime&&STORE.verifiedRuntime!==currentRuntime){for(const row of Object.values(STORE.rows)){if(row.compat==='compatible'){delete row.compat;if(row.status==='done')row.status='review'}}}
 for(const mod of LIVE_CATALOG.items){const row=STORE.rows[mod.id];if(row?.status==='done'&&row.observedInstalledVersion&&row.observedInstalledVersion!==mod.installedVersion)row.status='review'}
 STORE.verifiedRuntime=currentRuntime;
 STORE.prefs.collapsed=Array.isArray(STORE.prefs.collapsed)?STORE.prefs.collapsed.filter(g=>GROUP_MAP.has(g)):[];
 hydrated=true;
} catch(ex){console.warn('MO2 profile save unavailable, local fallback:',ex.message);if(contextIsCurrent(context))SET('liveState','Local notes retained. MO2 Chronicle loading will retry: '+ex.message)}finally{if(contextIsCurrent(context)){PROGRESS_READY=hydrated;LOCAL_PLAN_CACHE=null;applyAppearance();$('search').value=STORE.prefs.query;render();if(hydrated)save(false)}}}

function updateStorageIndicator(){const b=$('storageStatus');if(!b)return;b.textContent=persistOK?'◉ AUTO-SAVED LOCALLY':'⚠ SESSION ONLY · EXPORT JSON';b.style.color=persistOK?'var(--teal)':'var(--red)'}
function updatePrefs(p){STORE.prefs={...STORE.prefs,...p};save(false);applyAppearance();if(Object.keys(p).some(k=>['filter','insight','query','sort','collapsed'].includes(k)))render()}
function applyAppearance(){document.body.dataset.theme=STORE.prefs.theme==='snow'?'snow':'night';document.body.dataset.density=STORE.prefs.density==='comfy'?'comfy':'compact';$('effectSelect').value=STORE.prefs.effect;$('soundToggle').textContent=STORE.prefs.sound?'♫':'♪̸';$('soundToggle').title=STORE.prefs.sound?'Mute sounds':'Enable sounds';$('soundToggle').setAttribute('aria-label',STORE.prefs.sound?'Effect sounds on':'Effect sounds off');$('themeToggle').textContent=STORE.prefs.theme==='snow'?'☾':'◐';document.body.dataset.focus=STORE.prefs.focus?'yes':'no';$('focusToggle').textContent=STORE.prefs.focus?'◉ Exit Focus':'◎ Focus';$('focusToggle').setAttribute('aria-pressed',String(Boolean(STORE.prefs.focus)));$('focusTop').textContent=STORE.prefs.focus?'◉':'▦';$('focusTop').setAttribute('aria-pressed',String(Boolean(STORE.prefs.focus)));$('fxIntensity').value=STORE.prefs.vfxIntensity||'legendary';$('fxShake').checked=STORE.prefs.vfxShake!==false;$('fxTrails').checked=STORE.prefs.vfxTrails!==false;window.AetherFX?.config({intensity:STORE.prefs.vfxIntensity||'legendary',shake:STORE.prefs.vfxShake!==false,trails:STORE.prefs.vfxTrails!==false,sound:STORE.prefs.sound})}
function counts(){if(RENDER_COUNTS)return RENDER_COUNTS;const c={total:DATA.items.length,done:0,review:0,risky:0,unlinked:0,favorites:0,pending:0,updates:0};for(const m of DATA.items){const status=getStatus(m);if(status==='done')c.done++;if(status==='review')c.review++;if(status==='pending')c.pending++;if(isRisk(m))c.risky++;if(!getSrc(m).verified)c.unlinked++;if(isFav(m))c.favorites++;if(isUpdate(m))c.updates++;}return{...c,left:c.total-c.done,percent:c.total?Math.round(c.done/c.total*100):0}}
const SET=(id,val)=>{const el=$(id);if(el)el.textContent=val};
function renderStats(){const c=counts();SET('sidePercent',c.percent+'%');$('sideProgressFill').style.width=c.percent+'%';SET('sideDone',c.done);SET('sideRemaining',c.left);SET('sideDanger',c.risky);SET('heroPercent',c.percent+'%');SET('heroProgressLabel',`${c.done} / ${c.total} mods`);$('heroMotivation').textContent=c.percent===100?'SOVNGARDE IS YOURS':c.percent>=75?'THE FINAL ASSAULT':c.percent>=40?'THE TIDE TURNS':'THE JOURNEY BEGINS';document.querySelector('.orb-outer').style.setProperty('--percent',c.percent+'%');SET('metricTotal',c.total);SET('metricSections',DATA.groups.length+' hand-sorted sections');SET('metricDone',c.done);SET('metricRemaining',c.left+' left in the quest');SET('metricWatch',c.risky);SET('metricLinks',c.total-c.unlinked);SET('metricLinksSub',c.unlinked+' still need source confirmation');
 for(const [k,v] of Object.entries({All:c.total,Pending:c.pending,Done:c.done,Review:c.review,Risk:c.risky,Unlinked:c.unlinked,Favorite:c.favorites,Updates:c.updates}))SET('nav'+k+'Count',v);
 $all('[data-filter]').forEach(el=>{const on=el.dataset.filter===STORE.prefs.filter;el.classList.toggle('active',on);if(el.tagName==='BUTTON')el.setAttribute('aria-pressed',String(on))});
}
function tokenizeQuery(raw){const out=[];const re=/"([^"]+)"|(\S+)/g;let m;while((m=re.exec(raw)))out.push((m[1]||m[2]).trim().toLowerCase());return out}
function healthFilterMatches(mod,filter){return filter==='all'||(filter.startsWith('source-')?Boolean(SOURCES?.matches(mod,filter)):!INSIGHTS||INSIGHTS.matches(mod,filter))}
function textMatches(m,query,terms){if(!query)return true;const dependencies=[CATALOG_RENDER_REVISION,m.localAnalysis,m.modHealth,m.update,sourceTimePhase(m)],cached=SEARCH_CACHE.get(m);let base=cached&&dependencies.every((value,index)=>Object.is(value,cached.dependencies[index]))?cached.base:null;if(!base){base={text:[m.name,m.installedVersion,m.screenshotNote,m.author,m.group,m.priority,(m.flags||[]).join(' '),INSIGHTS?.searchText(m)||'',SOURCES?.searchText(m)||''].join(' ').toLowerCase(),author:txt(m.author).toLowerCase(),group:txt(m.group).toLowerCase(),flags:(m.flags||[]).join(' ').toLowerCase(),version:txt(m.installedVersion).toLowerCase()};SEARCH_CACHE.set(m,{dependencies,base})}const row=getRow(m.id),str=base.text+' '+txt(row.note).toLowerCase()+' '+txt(row.installed).toLowerCase();return(terms||tokenizeQuery(query)).every(part=>{const ix=part.indexOf(':');if(ix>0){const p=part.slice(0,ix),v=part.slice(ix+1);if(p==='health'||p==='esl')return (INSIGHTS?.searchText(m)||'').includes(v);if(p==='source'||p==='provider')return (SOURCES?.searchText(m)||'').includes(v);if(p==='author')return (base.author+' '+(SOURCES?.searchText(m)||'')).includes(v);if(p==='section'||p==='group')return base.group.includes(v);if(p==='flag')return base.flags.includes(v);if(p==='priority')return txt(m.priority).includes(v);if(p==='status')return getStatus(m).includes(v);if(p==='version')return base.version.includes(v);if(p==='note')return (m.screenshotNote+' '+txt(getRow(m.id).note)).toLowerCase().includes(v);}return str.includes(part)})}
function filtered(){const f=STORE.prefs.filter,terms=tokenizeQuery(STORE.prefs.query);return DATA.items.filter(m=>healthFilterMatches(m,STORE.prefs.insight||'all')&&textMatches(m,STORE.prefs.query,terms)&&(
 f==='all'||(f==='maintenance'&&detectedStatus(m).maintenance)||(f==='attention'&&detectedStatus(m).attention)||(f==='current'&&detectedStatus(m).current)||(f==='pending'&&getStatus(m)==='pending')||(f==='done'&&isDone(m))||(f==='review'&&isReview(m))||(f==='risky'&&isRisk(m))||(f==='unlinked'&&!getSrc(m).verified)||(f==='favorites'&&isFav(m))||(f==='updates'&&isUpdate(m))));}
function classFor(m){const s=getStatus(m);return s==='done'?'done':s==='review'?'needs-review':s==='skipped'?'skipped':''}
function assessmentHTML(m){const audit=getAudit(m),out=[];
 const profileMode=Boolean(BRIDGE.active||DATA.offline);const map={'safe-file':[profileMode?'✓ FILE LISTED':'✓ SE FILE','safe-file'],hold:['⚠ HOLD','hold'],avoid:[profileMode?'⛔ AVOID':'⛔ AE ONLY','avoid'],check:['? VERIFY','check'],compatible:['✓ COMPATIBLE','compatible'],'ae-only':[profileMode?'⛔ WRONG RUNTIME':'⛔ AE ONLY','ae-only']};
 if(map[audit.verdict])out.push(`<span class="tag ${map[audit.verdict][1]}" title="${escapeHTML(audit.note||audit.verdict)}">${map[audit.verdict][0]}</span>`);
 if(m.flags.includes('HARD_ESP')||m.flags.includes('HARD_ESM'))out.push(`<span class="tag hard" title="Master/plugin caution; verify masters and load order">HARD ${m.flags.includes('HARD_ESM')?'ESM':'ESP'}</span>`);
 if(m.flags.includes('ESL')&&!m.localAnalysis)out.push('<span class="tag esl" title="Legacy catalog annotation; actual winning plugin state has not been checked">ESL · annotation</span>');
 if(m.flags.includes('DLL')&&!map[audit.verdict])out.push('<span class="tag dll" title="SKSE DLL: verify game/runtime build">DLL CHECK</span>');
 if(m.flags.includes('NEMESIS'))out.push('<span class="tag check" title="Rerun Nemesis / animation behavior generator if affected">NEMESIS</span>');
 if(m.update&&m.update.verdict==='candidate')out.unshift('<span class="tag safe-file" title="Nexus indicates a newer version; verify runtime and file choice">ϟ UPDATE</span>');
 if(m.update&&m.update.verdict==='review')out.unshift('<span class="tag check" title="Version ordering ambiguous; review original files">? REVIEW VERSION</span>');
 if(m.update&&m.update.verdict==='current')out.unshift('<span class="tag compatible">✓ CURRENT</span>');
 if(m.update&&m.update.verdict==='newer-installed')out.unshift('<span class="tag compatible">✓ AHEAD OF SOURCE</span>');
 if(!out.length)out.push('<span class="tag unknown" title="No file-specific compatibility proof yet">NOT VERIFIED</span>');return out.join('');}
function modHTML(m){const row=getRow(m.id);return cachedMarkup(MOD_MARKUP_CACHE,m,[CATALOG_RENDER_REVISION,BRIDGE.active,DATA.offline,row.status,row.favorite,row.installed,row.note,row.compat,row.url,m.localAnalysis,m.modHealth,m.update,sourceTimePhase(m),LIVE_SCAN?.state==='running',LIVE_CONNECTED],()=>buildModHTML(m))}
function buildModHTML(m){const s=getSrc(m),sourceText=s.verified?(SOURCES?.label(s.type)||'Source'):'Find source',note=getRow(m.id).note||m.screenshotNote||'',stat=getStatus(m),statusLabel=stat==='done'?'Clear completed status':stat==='review'?'Mark mod cleared':'Mark mod cleared';
 return `<article class="mod-row ${classFor(m)}" id="row-${escapeHTML(m.id)}" data-id="${escapeHTML(m.id)}">
 <div class="mod-status"><button class="complete-btn" data-act="complete" data-id="${m.id}" aria-label="${escapeHTML(statusLabel+' for '+m.name)}" title="${escapeHTML(statusLabel)}">${stat==='done'?'✓':stat==='review'?'!':''}</button></div>
 <div class="mod-priority">${m.priority}</div>
 <div class="mod-primary" data-priority="${m.priority}"><div class="mod-name-actions"><a class="mod-title source-title ${s.verified?'':'unknown'}" href="${escapeHTML(s.url)}" target="_blank" rel="noopener noreferrer" aria-label="${escapeHTML((s.verified?'Open source page for ':'Search for original source of ')+m.name)}" title="${escapeHTML(s.verified?'Open the original mod website in a new tab':'Source unknown: search for this mod in a new tab')}" data-act="source" data-id="${escapeHTML(m.id)}">${escapeHTML(m.name)}<span class="source-open-glyph" aria-hidden="true">↗</span></a>${BRIDGE.active?`<button class="mod-inline-locate" type="button" title="Reveal this mod in Mod Organizer 2" aria-label="Reveal ${escapeHTML(m.name)} in MO2" data-act="locate" data-id="${escapeHTML(m.id)}">⌖</button>`:''}</div>${note?`<div class="mod-note ${/do not|ctd|crash|hard esp|incompati|needs update|bugs/i.test(note)?'warning-note':''}" title="${escapeHTML(note)}">${escapeHTML(note)}</div>`:''}${INSIGHTS?.chips(m)||''}${SOURCES?.chips(m)||''}</div>
 <div class="mod-version" title="Installed version recorded by source">${escapeHTML(m.installedVersion||'Unknown')}${m.update?.latestVersion&&m.update.latestVersion!==m.installedVersion?`<small class="newer-version" title="Latest metadata, not necessarily compatible">→ ${escapeHTML(m.update.latestVersion)}</small>`:''}</div>
 <div class="mod-assessment">${detectedMarkup(m)}<span class="legacy-assessment">${assessmentHTML(m)}</span></div>
 <div class="mod-source"><a class="source-btn ${s.verified?'':'lookup'}" href="${escapeHTML(s.url)}" target="_blank" rel="noopener noreferrer" data-act="source" data-id="${m.id}" title="${escapeHTML(s.type==='creations'?'Exact Bethesda Creation listing. Install from in-game Creations menu.':s.verified?'Exact mod files page':'Search for the correct mod project; source not yet verified')}">${sourceText}</a></div>
 <div class="inspect-col">${BRIDGE.active&&m.nexusId?`<button class="file-download-btn" title="Choose an exact Nexus file in MO2" aria-label="Choose Nexus files for ${escapeHTML(m.name)}" data-act="files" data-id="${m.id}">${isUpdate(m)?'Review':'⇩'}</button>`:''}<button class="favorite-btn ${isFav(m)?'active':''}" title="${isFav(m)?'Remove bookmark':'Bookmark mod'}" aria-label="Bookmark ${escapeHTML(m.name)}" data-act="favorite" data-id="${m.id}">${isFav(m)?'★':'☆'}</button><button class="inspect-btn" title="Inspect mod, notes, compatibility and source" aria-label="Inspect ${escapeHTML(m.name)}" data-act="inspect" data-id="${m.id}">Details</button></div>
 </article>`}
let GROUP_PROGRESS=new Map();function groupProgress(){GROUP_PROGRESS=new Map(DATA.groups.map(g=>[g.id,{done:0,total:0}]));for(const m of DATA.items){if(!GROUP_PROGRESS.has(m.groupId))GROUP_PROGRESS.set(m.groupId,{done:0,total:0});const o=GROUP_PROGRESS.get(m.groupId);o.total++;if(isDone(m))o.done++}}
function renderChapters(){let needle=$('chapterSearch').value.trim().toLowerCase();const html=DATA.groups.filter(g=>!needle||g.name.toLowerCase().includes(needle)).map(g=>{const num=GROUP_PROGRESS.get(g.id)?.done||0;return `<button class="chapter-link" style="--group-clr:${PALETTE[g.color]||PALETTE.slate}" data-chapter="${g.id}" title="Jump to ${escapeHTML(g.name)}"><i></i><span>${escapeHTML(g.name)}</span><em>${num}/${g.count}</em></button>`}).join('');setHTML($('chapters'),html||'<div class="drawer-micro" style="padding:12px">No chapter found.</div>')}
function renderList(){const visible=filtered(),byGroup=new Map();for(const m of visible){if(!byGroup.has(m.groupId))byGroup.set(m.groupId,[]);byGroup.get(m.groupId).push(m)}
 const entries=[];for(const g of DATA.groups){const ms=byGroup.get(g.id);if(!ms||!ms.length)continue;let sorted=[...ms];if(STORE.prefs.sort==='name')sorted.sort((a,b)=>a.name.localeCompare(b.name));else if(STORE.prefs.sort==='version')sorted.sort((a,b)=>a.installedVersion.localeCompare(b.installedVersion));else sorted.sort((a,b)=>a.priority-b.priority);
 const gd=GROUP_PROGRESS.get(g.id)?.done||0;const collapsed=STORE.prefs.collapsed.includes(g.id),pct=Math.round((gd/(g.count||1))*100);
 entries.push({key:'group:'+g.id,html:`<button class="chapterbar ${collapsed?'closed':''}" id="section-${g.id}" data-chapter-toggle="${g.id}" style="--chapter-color:${PALETTE[g.color]||PALETTE.slate}" aria-expanded="${!collapsed}"><span class="diamond">◆</span><strong>${escapeHTML(g.name)}</strong><span class="chapter-count">${gd}/${g.count}</span><span class="chapter-mini-progress"><i style="width:${pct}%"></i></span><span class="chapter-priority">#${g.startPriority}</span></button>`});
 if(!collapsed)for(const m of sorted)entries.push({key:'mod:'+m.id,html:modHTML(m)});}
 setRows($('modList'),entries,`<div class="empty-state"><div class="empty-icon">ᛟ</div><h3>No foes in this chamber.</h3><p>Nothing matches your current search and filters.</p><button id="emptyReset">Clear filters</button></div>`);
 const listCounts=counts(),parts=[visible.length+' of '+DATA.items.length+' mods',byGroup.size+' chapters'];if(STORE.prefs.query)parts.push('query: '+STORE.prefs.query);SET('resultsMeta',parts.join(' · '));SET('listFooter',`YOU'VE REACHED THE END · ${visible.length} MATCHING MODS · ${listCounts.total-listCounts.unlinked} EXACT ORIGINAL SOURCES`);$('sortSelect').value=STORE.prefs.sort;}
function render(){if(SEARCH_FRAME!==null){cancelAnimationFrame(SEARCH_FRAME);SEARCH_FRAME=null}RENDER_COUNTS=counts();try{groupProgress();renderStats();renderList();renderChapters();renderQuest();paintNativeSelection();renderWorkshop();renderInsightToolbar();renderSourceCoverage();renderWorkspace()}finally{RENDER_COUNTS=null}}
function renderSearch(){SEARCH_FRAME=null;renderList();paintNativeSelection()}
function scheduleSearchRender(){if(DATA.items.length<1000){renderSearch();return}if(SEARCH_FRAME===null)SEARCH_FRAME=requestAnimationFrame(renderSearch)}
function setStatus(m,status,withEffect=false,center){if(!['pending','done','review','skipped'].includes(status))return;const old=getStatus(m);if(old===status)return;const previousRow=$('row-'+m.id);const effectMode=STORE.prefs.effect==='random'?['shatter','fireworks','shot','slash','burn','arrow','frost','thunder'][Math.floor(Math.random()*8)]:STORE.prefs.effect;if(withEffect&&status==='done')window.AetherFX?.complete(previousRow,{mode:effectMode});else if(withEffect&&status==='pending')window.AetherFX?.undo(previousRow);getRow(m.id).status=old;STORE.rows[m.id]={...getRow(m.id),status,observedInstalledVersion:status==='done'?m.installedVersion:null,updatedAt:new Date().toISOString()};lastUndo={id:m.id,previous:old,next:status};save(false);render();if(withEffect&&status==='done')spawnFinisher(center||centerOfRow(m.id),effectMode);toast(status==='done'?'✦ '+m.name+' — quest cleared!'+(BRIDGE.active?' (manual, no ranked XP)':''):'Updated '+m.name,()=>undoLast(),'UNDO');if(!BRIDGE.active&&counts().total>0&&counts().done===counts().total&&!PRACTICE_CROWNED){PRACTICE_CROWNED=true;setTimeout(()=>launchVictory({verifiedMods:DATA.items.length,bonus:0,updates:DATA.items.length},true),420)}}
function undoLast(){if(!lastUndo)return;const action=lastUndo;lastUndo=null;window.AetherFX?.undo($('row-'+action.id));STORE.rows[action.id]={...getRow(action.id),status:action.previous};save(false);render();toast('↶ Last change undone.');}
function centerOfRow(id){const el=$('row-'+id);if(!el)return{x:window.innerWidth*.5,y:window.innerHeight*.45};const r=el.getBoundingClientRect();return{x:Math.max(20,r.left+Math.min(r.width*.42,420)),y:r.top+r.height*.5}}
function toggleDone(id,el){const m=ITEM_MAP.get(id);if(!m)return;const r=el?.closest('.mod-row')?.getBoundingClientRect();const pos=r?{x:r.left+Math.min(r.width*.44,450),y:r.top+r.height*.5}:centerOfRow(id);setStatus(m,isDone(m)?'pending':'done',true,pos);}
function toast(message,action,actionLabel){const w=$('toastWrap');const el=document.createElement('div');el.className='toast';el.innerHTML=`<span>${escapeHTML(message)}</span>`;if(action){const b=document.createElement('button');b.textContent=actionLabel||'UNDO';b.addEventListener('click',()=>{action();el.remove()});el.append(b)}w.append(el);while(w.children.length>3)w.firstChild.remove();setTimeout(()=>{el.style.opacity='0';el.style.transition='opacity .2s';setTimeout(()=>el.remove(),220)},3600)}
function nextPending(){const filteredPending=DATA.items.filter(m=>getStatus(m)==='pending');if(!filteredPending.length){toast('Everything is cleared or marked for review. Skyrim belongs to you.');return}let target=filteredPending.find(m=>m.priority>Number((ITEM_MAP.get(focusedRowId)||{}).priority||0))||filteredPending[0];STORE.prefs.filter='all';STORE.prefs.query='';$('search').value='';if(STORE.prefs.collapsed.includes(target.groupId))STORE.prefs.collapsed=STORE.prefs.collapsed.filter(g=>g!==target.groupId);save();render();focusRow(target.id);}
function focusRow(id){const row=$('row-'+id);if(!row)return;focusedRowId=id;row.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'center'});row.classList.remove('focus-signal');void row.offsetWidth;row.classList.add('focus-signal');window.AetherFX?.select(row,{source:'app'});const b=row.querySelector('[data-act="complete"]');if(b)b.focus({preventScroll:true});}
function jumpChapter(id){STORE.prefs.filter='all';STORE.prefs.query='';$('search').value='';if(STORE.prefs.collapsed.includes(id))STORE.prefs.collapsed=STORE.prefs.collapsed.filter(x=>x!==id);save();render();const el=$('section-'+id);el?.scrollIntoView({behavior:'smooth',block:'start'});$('sidebar').classList.remove('open')}
function resetFilters(){STORE.prefs.filter='all';STORE.prefs.insight='all';STORE.prefs.query='';$('search').value='';save();render()}
function toggleAllChapters(){const allClosed=STORE.prefs.collapsed.length===DATA.groups.length;STORE.prefs.collapsed=allClosed?[]:DATA.groups.map(g=>g.id);$('collapseAll').textContent=allClosed?'Collapse':'Expand';save();render();}
function drawInspector(m){openedId=m.id;STORE.lastSelected=m.id;save(false);const row=getRow(m.id),audit=getAudit(m),source=getSrc(m);SET('drawerTitle',m.name);
 const selected=(v)=>row.compat===v?'selected':'';const status=getStatus(m);
 const profileMode=Boolean(BRIDGE.active||DATA.offline),runtime=profileMode?txt(DATA.gameRuntime).trim():'1.5.97';
 const verifiedLabel=runtime&&runtime.toLowerCase()!=='unknown'?'Verified working on '+runtime:'Verified working in this setup';
 $('drawerContent').innerHTML=`<div class="drawer-priority"><span>MO2 PRIORITY #${m.priority}</span><span>${escapeHTML(m.group)}</span><span>${BRIDGE.active?'LIVE PROFILE':DATA.offline?'IMPORTED PROFILE':'SCREENSHOT '+m.screenshot}</span></div>
 <div class="drawer-section drawer-alert" data-alert="${escapeHTML(audit.verdict)}"><span class="drawer-label" style="margin:0">${(BRIDGE.active||DATA.offline)?'RUNTIME / FILE COMPATIBILITY CHECK':'1.5.97 COMPATIBILITY ADVISORY'}</span><strong>${escapeHTML(audit.verdict==='avoid'?'AVOID LATEST RELEASE':audit.verdict==='hold'?'HOLD / PICK LEGACY FILE':audit.verdict==='safe-file'?(profileMode?'BUILD LISTED':'SE BUILD LISTED'):audit.verdict==='check'?'VERIFY SPECIFIC BUILD':'NOT YET VERIFIED')}</strong><p>${escapeHTML(audit.note||((BRIDGE.active||DATA.offline)?'This source has not established complete compatibility. Check the required game runtime, script extender, dependencies and installation choices.':'The provided screenshots do not establish compatibility for a new release. Check the required game executable, SKSE, Address Library, BEES and masters before updating.'))}</p>${audit.evidence?`<a class="drawer-link" href="${escapeHTML(audit.evidence)}" target="_blank" rel="noopener noreferrer">↗ View supporting source</a>`:''}</div>
 <div id="modSourceDetails">${SOURCES?.details(sourceDetailMod(m),{live:BRIDGE.active})||''}</div>
 <div id="modInsightDetails">${INSIGHTS?.details(m,{live:BRIDGE.active,game:DATA.game,auditState:DATA.pluginAuditState||{}})||''}</div>
 <label class="drawer-label" for="drawerStatus">CHECKLIST STATUS</label><select class="drawer-field" id="drawerStatus"><option value="pending" ${status==='pending'?'selected':''}>◷ Not checked</option><option value="done" ${status==='done'?'selected':''}>✓ Updated / cleared</option><option value="review" ${status==='review'?'selected':''}>⚠ Needs investigation</option><option value="skipped" ${status==='skipped'?'selected':''}>⤼ Deliberately skipped</option></select>
 <div class="drawer-row"><div><label class="drawer-label" for="drawerInstalled">INSTALLED VERSION</label><input class="drawer-field" id="drawerInstalled" value="${escapeHTML(row.installed||m.installedVersion)}" placeholder="version"></div><div><label class="drawer-label" for="drawerVerifiedVersion">FILE YOU TESTED</label><input class="drawer-field" id="drawerVerifiedVersion" value="${escapeHTML(row.testedVersion||'')}" placeholder="${profileMode?'e.g. release version and FOMOD option':'e.g. SE 1.5.97 FOMOD'}"></div></div>
 <label class="drawer-label" for="drawerCompat">YOUR COMPATIBILITY VERDICT</label><select class="drawer-field" id="drawerCompat"><option value="" ${!row.compat?'selected':''}>Use evidence / unverified</option><option value="compatible" ${selected('compatible')}>✓ ${escapeHTML(verifiedLabel)}</option><option value="check" ${selected('check')}>? Must check specific file</option><option value="hold" ${selected('hold')}>⚠ Hold pinned version</option><option value="ae-only" ${selected('ae-only')}>⛔ ${profileMode?'Wrong runtime or edition':'AE-only or 1.7+ build'}</option></select>
 <label class="drawer-label" for="drawerUrl">EXACT MOD FILE DOWNLOAD PAGE</label><input class="drawer-field" id="drawerUrl" type="url" spellcheck="false" value="${escapeHTML(row.url||m.source.url||'')}" placeholder="https://www.nexusmods.com/skyrimspecialedition/mods/12345?tab=files"><div class="drawer-micro">${source.type==='creations'?'✓ Official Bethesda listing; downloads/installations are handled through in-game Creations.':source.verified?'✓ This mod has an exact source page.':'⚠ No verified original source ID yet. Use the lookup link and paste its exact Files page.'}</div>
 <div class="drawer-section" style="margin-top:10px">${BRIDGE.active?`<button class="button-bright drawer-m2-focus" id="drawerLocate" type="button" title="Reveal this exact mod in Mod Organizer 2">⌖ REVEAL IN MO2 · SELECT + SCROLL</button>`:''}<a class="drawer-link" id="drawerOpenLink" href="${escapeHTML(source.url)}" target="_blank" rel="noopener noreferrer">↗ ${source.type==='creations'?'OPEN OFFICIAL CREATION LISTING':source.verified?'OPEN EXACT FILE DOWNLOAD PAGE':'FIND ORIGINAL MOD PAGE (UNVERIFIED)'}</a><button class="quiet-button" style="height:29px;margin-top:7px" id="copySource">⧉ Copy source URL</button>${nativeWatchAvailable()?'<button class="quiet-button" style="height:29px;margin-top:7px" id="trackSource">◉ Track page</button>':''}</div>
 <label class="drawer-label" for="drawerNote">YOUR UPDATE NOTES / TEST RESULTS</label><textarea class="drawer-field" id="drawerNote" placeholder="Selections, dependencies, before/after, crashes, load order conflicts…">${escapeHTML(row.note||'')}</textarea>
 ${m.screenshotNote?`<div class="drawer-section"><span class="drawer-label" style="margin:0">ORIGINAL SCREENSHOT NOTES</span><p>${escapeHTML(m.screenshotNote)}</p></div>`:''}
 <div class="drawer-check"><input type="checkbox" id="drawerFavorite" ${row.favorite?'checked':''}><label for="drawerFavorite">Bookmark this mod in the sidebar</label></div>
 <div class="drawer-action-bar"><button id="saveInspector">✦ Save inspection</button><button id="markInspectorDone">✓ Mark cleared</button></div>
 <p class="drawer-micro" style="margin-top:15px">${BRIDGE.active?'Your notes and verdicts are persisted in the MO2 Chronicle for this profile. Manual clearing does not award ranked XP.':'Your notes and verdicts are local to this browser. Back up JSON before moving machines.'} This dashboard does not directly modify installed mod files.</p>`;
 $('drawerVeil').hidden=false;$('detailDrawer').classList.add('open');$('detailDrawer').setAttribute('aria-hidden','false');setTimeout(()=>$('drawerStatus').focus(),130);
 // A pasted URL is immediately previewable/copyable, even before saving notes.
 // Never leave the old link clickable while an invalid replacement is typed.
 const sourceInput=$('drawerUrl'),sourceLink=$('drawerOpenLink');
 function syncDrawerSource(){
  const typed=sourceInput.value.trim(),url=typed?validUrl(typed):getSrc(m).url;
  const track=$('trackSource');if(nativeWatchAvailable()&&track){track.disabled=!trackPageIntent(m,typed);track.title=track.disabled?'Enter an exact HTTPS source page first':'Add this page to Watchtower without changing your update notes'}
  if(url){sourceLink.href=url;sourceLink.removeAttribute('aria-disabled');sourceLink.textContent=typed?'↗ OPEN THIS SOURCE PAGE':'↗ '+(source.verified?'OPEN ORIGINAL MOD PAGE':'FIND ORIGINAL SOURCE');}
  else{sourceLink.removeAttribute('href');sourceLink.setAttribute('aria-disabled','true');sourceLink.textContent='Enter a valid http(s) source URL';}
 }
 sourceInput.addEventListener('input',syncDrawerSource);
 syncDrawerSource();
 $('copySource').onclick=()=>{const typed=sourceInput.value.trim(),url=typed?validUrl(typed):getSrc(m).url;if(url)copyToClipboard(url);else toast('Enter a valid http(s) source URL first.');};
 if(nativeWatchAvailable()&&$('trackSource'))$('trackSource').onclick=()=>trackModPage(m,sourceInput.value);
 if(BRIDGE.active&&$('drawerLocate'))$('drawerLocate').onclick=()=>locateInMO2(m,$('drawerLocate'));
 $('saveInspector').onclick=()=>saveInspection(false);
 $('markInspectorDone').onclick=()=>saveInspection(true);
 if(BRIDGE.active){void loadModInsights(m);void loadSourceDetails(m);}
}
// The native marker may arrive after an inspector is already open. Attach only
// its tracking control so website ownership changes never replace a user's draft.
function attachNativeTracking(){
 if(!nativeWatchAvailable()||!openedId)return;
 const mod=ITEM_MAP.get(openedId),input=$('drawerUrl'),copy=$('copySource');if(!mod||!input||!copy)return;
 let control=$('trackSource');if(!control){control=document.createElement('button');control.id='trackSource';control.type='button';control.className='quiet-button';control.style.cssText='height:29px;margin-top:7px';control.textContent='◉ Track page';copy.insertAdjacentElement('afterend',control)}
 const sync=()=>{control.disabled=!trackPageIntent(mod,input.value);control.title=control.disabled?'Enter an exact HTTPS source page first':'Add this page to Watchtower without changing your update notes'};
 control.onclick=()=>trackModPage(mod,input.value);sync();
 if(control.dataset.boundInput!==mod.id){control.dataset.boundInput=mod.id;input.addEventListener('input',sync)}
}
window.addEventListener('modhunter-watchtower-ready',attachNativeTracking);
function refreshInspectorInsights(){
 refreshSourceDetails();
 const panel=$('modInsightDetails'),mod=ITEM_MAP.get(openedId);
 if(panel&&mod&&INSIGHTS){
  const expanded=new Map([...panel.querySelectorAll('details[data-plugin-name]')].map(el=>[el.dataset.pluginName,el.open]));
  setHTML(panel,INSIGHTS.details(mod,{live:BRIDGE.active,game:DATA.game,auditState:DATA.pluginAuditState||{}}));
  for(const el of panel.querySelectorAll('details[data-plugin-name]'))if(expanded.has(el.dataset.pluginName))el.open=expanded.get(el.dataset.pluginName);
 }
}
function sourceDetailMod(mod){const detail=SOURCE_DETAILS.get(mod);return detail?.update?{...mod,update:{...mod.update,...detail.update}}:mod}
function refreshSourceDetails(){
 const panel=$('modSourceDetails'),mod=ITEM_MAP.get(openedId);if(!panel||!mod||!SOURCES)return;
 const detail=SOURCE_DETAILS.get(mod)||{};
 const expanded=new Map([...panel.querySelectorAll('details[data-source-section]')].map(el=>[el.dataset.sourceSection,el.open]));
 setHTML(panel,SOURCES.details(sourceDetailMod(mod),{live:BRIDGE.active,loading:detail.loading,error:detail.error}));
 for(const el of panel.querySelectorAll('details[data-source-section]'))if(expanded.has(el.dataset.sourceSection))el.open=expanded.get(el.dataset.sourceSection);
}
async function loadSourceDetails(mod){
 if(!BRIDGE.active||!mod)return null;
 if(SOURCE_DETAILS.get(mod)?.update)return SOURCE_DETAILS.get(mod).update;
 if(SOURCE_DETAIL_READS.has(mod))return SOURCE_DETAIL_READS.get(mod);
 const context=profileContext();SOURCE_DETAILS.set(mod,{loading:true});if(openedId===mod.id)refreshSourceDetails();
 const task=(async()=>{
  try{
   const result=await bridgeRequest('GET','source-details?itemId='+encodeURIComponent(mod.id)+'&profileId='+encodeURIComponent(context.profile));
   if(!contextIsCurrent(context)||result.profileId!==context.profile||ITEM_MAP.get(mod.id)!==mod)return null;
   if(!result.update||typeof result.update!=='object')throw Error('Source details are not available. Refresh the source check.');
   SOURCE_DETAILS.set(mod,{update:result.update});if(openedId===mod.id)refreshSourceDetails();return result.update;
  }catch(ex){if(contextIsCurrent(context)&&ITEM_MAP.get(mod.id)===mod){SOURCE_DETAILS.set(mod,{error:ex.message});if(openedId===mod.id)refreshSourceDetails();}return null;}
  finally{SOURCE_DETAIL_READS.delete(mod);}
 })();SOURCE_DETAIL_READS.set(mod,task);return task;
}
function renderSourceCoverage(){const section=$('sourceCoverage'),rows=$('sourceCoverageRows');if(section?.open&&rows&&SOURCES)setHTML(rows,SOURCES.coverage(DATA.items));}
if($('sourceCoverage'))$('sourceCoverage').addEventListener('toggle',renderSourceCoverage);
document.addEventListener('click',async e=>{
 const button=e.target.closest('[data-source-act]');if(!button)return;e.preventDefault();
 const mod=ITEM_MAP.get(button.dataset.id||openedId);if(!mod)return;
 if(button.dataset.sourceAct==='inspect'){if(openedId===mod.id){refreshSourceDetails();void loadSourceDetails(mod);}else drawInspector(mod);}
 if(button.dataset.sourceAct==='scan')await scanLive('all');
 if(button.dataset.sourceAct==='files')await reviewWorkshopFile(mod.id);
});
function renderInsightToolbar(){
 const select=$('insightFilter'),button=$('analyzePlugins');if(!select||!button)return;
 select.value=STORE.prefs.insight||'all';
 const job=DATA.pluginAuditState||{},analysis=DATA.pluginAnalysis||{},running=['queued','running'].includes(job.state);
 button.disabled=running||(BRIDGE.active&&!LIVE_CONNECTED);
 button.textContent=running?'Analyzing…':'Analyze plugins';
 let message='Inspect a mod for ESL state, masters, source availability and Nexus bug evidence.';
 if(running)message='Rust '+(job.depth==='records'?'record + hash':'header')+' analysis is running in the background.';
 else if(job.error)message=job.error;
 else if(analysis.checkedAt){
  const stats=analysis.stats||{},when=new Date(/^\d+$/.test(String(analysis.checkedAt))?Number(analysis.checkedAt)*1000:analysis.checkedAt);
  message=(analysis.depth==='records'?'Record + hash scan':'Header scan')+' · '+(stats.pluginCopies??0)+' plugin copies · '+(Number.isFinite(Number(stats.elapsedMs))?Math.round(Number(stats.elapsedMs))+' ms · ':'')+(Number.isFinite(when.getTime())?when.toLocaleTimeString():'checked');
  if(analysis.inventoryComplete===false)message+=' · incomplete inventory: review warnings';
 }
 else if(!BRIDGE.active&&DATA.offline)message='Local profile imported. Use the desktop Plugin audit control for record and ESLifier checks.';
 SET('pluginAuditStatus',message);
}
async function analyzePlugins(){
 if(!BRIDGE.active){toast('Use Plugin audit in the native desktop toolbar for this imported profile.');return;}
 const context=profileContext();
 try{
  const result=await bridgeRequest('POST','plugin-audit',{profileId:context.profile,deep:true});
  if(!contextIsCurrent(context))return;
  DATA.pluginAuditState=result;renderInsightToolbar();refreshInspectorInsights();
  toast('Rust plugin analysis started. The mod rows will refresh when it completes.');
 }catch(ex){if(contextIsCurrent(context)){SET('pluginAuditStatus',ex.message);toast(ex.message);}}
}
async function refreshModHealth(mod,force=true){
 if(!BRIDGE.active){toast('Connect the MO2 plugin to check public Nexus health.');return;}
 const context=profileContext(),key=PROFILE_EPOCH+':'+CATALOG_RENDER_REVISION+':'+mod.id;
 if(INSIGHT_REQUESTS.has(key))return INSIGHT_REQUESTS.get(key);
 const task=(async()=>{
  try{
   const result=await bridgeRequest('POST','mod-health',{profileId:context.profile,itemId:mod.id,force});
   if(!contextIsCurrent(context)||result.profileId!==context.profile)return;
   const current=ITEM_MAP.get(mod.id);if(!current||current!==mod)return;
   current.modHealth=result.health;
   if(Object.hasOwn(result,'localAnalysis'))current.localAnalysis=result.localAnalysis;
   if(result.pluginAuditState)DATA.pluginAuditState=result.pluginAuditState;
   SEARCH_CACHE.delete(current);LOCAL_PLAN_CACHE=null;
   refreshInspectorInsights();renderList();renderWorkshop();
   if(force&&result.health?.job?.state==='running')toast('Checking the public Nexus page. Results will appear on this mod.');
  }catch(ex){if(force&&contextIsCurrent(context))toast(ex.message);}
 })();
 INSIGHT_REQUESTS.set(key,task);
 try{return await task}finally{if(INSIGHT_REQUESTS.get(key)===task)INSIGHT_REQUESTS.delete(key)}
}
async function loadModInsights(mod){
 if(!BRIDGE.active||!mod)return;
 if(INSIGHTS?.isNexus(mod))return refreshModHealth(mod,false);
 const context=profileContext(),key=PROFILE_EPOCH+':'+CATALOG_RENDER_REVISION+':'+mod.id;
 if(INSIGHT_REQUESTS.has(key))return INSIGHT_REQUESTS.get(key);
 const task=(async()=>{
  try{
   const result=await bridgeRequest('GET','mod-health?itemId='+encodeURIComponent(mod.id)+'&profileId='+encodeURIComponent(context.profile));
   if(!contextIsCurrent(context)||result.profileId!==context.profile)return;
   const current=ITEM_MAP.get(mod.id);if(!current||current!==mod)return;
   current.modHealth=result.health;current.localAnalysis=result.localAnalysis;
   if(result.pluginAuditState)DATA.pluginAuditState=result.pluginAuditState;
   SEARCH_CACHE.delete(current);refreshInspectorInsights();renderList();renderWorkshop();
  }catch(ex){if(contextIsCurrent(context)&&openedId===mod.id)toast('Mod details: '+ex.message);}
 })();
 INSIGHT_REQUESTS.set(key,task);try{return await task}finally{if(INSIGHT_REQUESTS.get(key)===task)INSIGHT_REQUESTS.delete(key)}
}
document.addEventListener('click',async e=>{
 const button=e.target.closest('[data-insight-act]');if(!button)return;
 e.preventDefault();
 const action=button.dataset.insightAct,mod=ITEM_MAP.get(button.dataset.id||openedId);
 if(action==='inspect'&&mod){if(openedId===mod.id)refreshInspectorInsights();else drawInspector(mod);return;}
 if(action==='related'){
  const related=ITEM_MAP.get(button.dataset.owner);if(!related){toast('The winning copy is outside an installed mod row. Review Game Data or Overwrite in MO2.');return;}
  if(BRIDGE.active)void locateInMO2(related,button);drawInspector(related);return;
 }
 if(action==='audit'){await analyzePlugins();return;}
 if(action==='health'&&mod){await refreshModHealth(mod,true);return;}
 if(action==='eslifier'&&BRIDGE.active){
  const context=profileContext();button.disabled=true;
  try{const result=await bridgeRequest('POST','eslifier-data',{profileId:context.profile});if(contextIsCurrent(context)){DATA.pluginAuditState=result;renderInsightToolbar();refreshInspectorInsights();toast('ESLifier records connected; plugin analysis queued.');}}
  catch(ex){if(contextIsCurrent(context))toast(ex.message);}
  finally{if(button.isConnected)button.disabled=false;}
 }
});
if($('insightFilter'))$('insightFilter').addEventListener('change',e=>updatePrefs({insight:e.target.value}));
if($('analyzePlugins'))$('analyzePlugins').addEventListener('click',()=>void analyzePlugins());
function saveInspection(forceDone){const m=ITEM_MAP.get(openedId);if(!m)return;const url=$('drawerUrl').value.trim();if(url&&!validUrl(url)){toast('Please enter a valid http(s) mod URL.');$('drawerUrl').focus();return}
 const old=getStatus(m);const selected=$('drawerStatus').value;const next=forceDone?'done':selected;
 markSourceEdit(getRow(m.id).url,url);STORE.rows[m.id]={...getRow(m.id),status:next,installed:$('drawerInstalled').value.trim(),testedVersion:$('drawerVerifiedVersion').value.trim(),compat:$('drawerCompat').value,url:url,note:$('drawerNote').value,favorite:$('drawerFavorite').checked,updatedAt:new Date().toISOString()};save();closeInspector();render();if(next==='done'&&old!=='done')spawnFinisher(centerOfRow(m.id));toast('✦ Inspection recorded for '+m.name);}
function closeInspector(){$('drawerVeil').hidden=true;$('detailDrawer').classList.remove('open');$('detailDrawer').setAttribute('aria-hidden','true');openedId=null}
function help(open){$('helpVeil').hidden=!open;}
async function copyToClipboard(value){try{await navigator.clipboard.writeText(value);toast('⧉ Source URL copied.')}catch{const t=document.createElement('textarea');t.value=value;document.body.append(t);t.select();let success=false;try{success=document.execCommand('copy')}catch{}t.remove();toast(success?'Source URL copied.':'Copy unavailable. Select the URL in the inspector.')}}
function exportFilename(label,extension){const raw=String(DATA.profileName||LIVE_CATALOG?.profileName||'Standalone').replace(/[<>:"\/\\|?*\u0000-\u001f]/g,'_').trim();const profile=Array.from(raw).slice(0,80).join('')||'Profile';return 'Modhunter - '+profile+' - '+label+'.'+extension;}
function exportJSON(){const obj={format:'Mod Update Arsenal Backup',version:1,createdAt:new Date().toISOString(),catalogId:DATA.catalogId,sourceRevision:DATA.sourceRevision,items:DATA.items.length,rows:STORE.rows,prefs:STORE.prefs,lastSelected:STORE.lastSelected,verifiedRuntime:STORE.verifiedRuntime};saveFile(exportFilename('Progress','json'),'application/json',JSON.stringify(obj,null,2));toast('↓ Progress backup download requested.');}
function saveFile(name,type,body){const blob=new Blob([body],{type:type+';charset=utf-8'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=name;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),3500)}
function csvCell(v){return'"'+txt(v).replace(/"/g,'""')+'"'}
function exportCSV(){const cols=['Priority','Mod','Section','Installed version','Status',(BRIDGE.active||DATA.offline)?'Compatibility Verdict':'1.5.97 Verdict','Type Flags','Exact Source URL','Source Verified','Source Provider','Screenshot Note','User Note','Tested Version','Author'];const csv=[cols.map(csvCell).join(',')];cols.push('Winning light plugins','Winning full plugins','Light overrides','Flag-only candidates','Compaction review','ESLifier changes','Master issues','Plugin checked at','Bugs tab state','Nexus displayed bug count','Bugs checked at');cols.push(...(SOURCES?.csvHeaders||[]));csv[0]=cols.map(csvCell).join(',');for(const m of filtered()){const r=getRow(m.id),s=getSrc(m);const c=[m.priority,m.name,m.group,r.installed||m.installedVersion,getStatus(m),getAudit(m).verdict,m.flags.join(';'),s.verified?s.url:'',s.verified?'yes':'NO',s.label,m.screenshotNote,r.note||'',r.testedVersion||'',m.author];c.push(...(INSIGHTS?.csv(m)||Array(11).fill('')),...(SOURCES?.csv(m)||[]));csv.push(c.map(csvCell).join(','))}saveFile(exportFilename('Visible Mods','csv'),'text/csv','\ufeff'+csv.join('\r\n'));toast('⇩ CSV download requested for '+filtered().length+' visible mods.');}
async function importJSON(file){
 if(!file)return;
 const catalogId=DATA.catalogId,epoch=PROFILE_EPOCH;
 try{
  const obj=JSON.parse(await file.text());
  if(DATA.catalogId!==catalogId||PROFILE_EPOCH!==epoch)throw Error('The active profile changed while reading the backup. Choose the file again for the current profile.');
  const record=v=>v&&typeof v==='object'&&!Array.isArray(v);
  if(!record(obj)||obj.catalogId!==catalogId||!record(obj.rows))throw Error('This is not an Update Arsenal progress backup for the current catalog.');
  const imported=Object.entries(obj.rows).filter(([id])=>ITEM_MAP.has(id));
  if(imported.some(([,row])=>!record(row)))throw Error('A saved mod record is malformed. Current progress was not changed.');
  const prefs={};
  if(obj.prefs!==undefined){
   if(!record(obj.prefs))throw Error('Backup preferences are malformed. Current progress was not changed.');
   for(const k of Object.keys(BASE.prefs))if(Object.hasOwn(obj.prefs,k)){
    const value=obj.prefs[k],base=BASE.prefs[k];
    const valid=Array.isArray(base)?Array.isArray(value)&&value.every(id=>typeof id==='string'):typeof value===typeof base;
    if(!valid)throw Error('Invalid backup preference: '+k+'. Current progress was not changed.');
    prefs[k]=value;
   }
  }
  // Validate the entire merge before touching rows, source revisions or storage.
  if(!confirm('Restore '+imported.length+' saved mod records? This merges records from the backup into this browser.'))return;
  for(const[id,value]of imported){const row={...STORE.rows[id],...value};markSourceEdit(getRow(id).url,row.url);STORE.rows[id]=row;}
  Object.assign(STORE.prefs,prefs);save();$('search').value=STORE.prefs.query;applyAppearance();render();toast('✓ Restored '+imported.length+' saved mod records.');
 }catch(e){toast('⚠ Restore failed: '+e.message)}finally{$('importFile').value=''}
}
// The optional PowerShell bridge is embedded as text, downloaded on demand and never executed by the browser.
function mo2Open(){ $('mo2Veil').hidden=false; if(MO2_PENDING.length)drawMO2Matcher();else $('mo2Results').textContent=''; }
function mo2Close(){ $('mo2Veil').hidden=true; }
const mo2Key=(str)=>txt(str).normalize('NFKC').toLowerCase().replace(/[‐‑–—]/g,'-').replace(/[^\p{L}\p{N}]+/gu,' ').trim().replace(/\s+/g,' ');
const mo2Link=(id)=>'https://www.nexusmods.com/skyrimspecialedition/mods/'+id+'?tab=files';
const mo2ValidId=(id)=>/^[1-9]\d{0,9}$/.test(txt(id).trim());
function mo2ApplyLink(m,input){const id=txt(input.modId).trim();if(!mo2ValidId(id))return false;const prev=getRow(m.id);if(prev.url||m.source.verified)return false;
 const files=Array.isArray(input.fileIds)?input.fileIds.filter(x=>mo2ValidId(x)).slice(0,25):[];
 markSourceEdit(prev.url,mo2Link(id));STORE.rows[m.id]={...prev,url:mo2Link(id),mo2Source:true,mo2FileIds:files,mo2FolderName:input.name};return true;
}
function drawMO2Matcher(){const panel=$('mo2Resolver');panel.hidden=!MO2_PENDING.length;if(panel.hidden)return;
 const q=mo2Key($('mo2Lookup').value);$('mo2PendingCount').textContent=MO2_PENDING.length+' folder'+(MO2_PENDING.length===1?'':'s')+' need review';
 const sources=q?MO2_PENDING.filter(x=>mo2Key(x.name).includes(q)):[];
 const from=$('mo2From'),oldFrom=from.value;from.replaceChildren();
 const add=(sel,val,label)=>{const opt=new Option(label,val);sel.add(opt)};
 if(!q)add(from,'','Search above to choose a folder');
 else if(!sources.length)add(from,'','No matching imported folders');
 else for(const input of sources)add(from,input._key,input.name+'  →  Nexus #'+input.modId);
 if(sources.some(x=>x._key===oldFrom))from.value=oldFrom;
 const target=$('mo2To'),oldTarget=target.value;target.replaceChildren();
 const unresolved=DATA.items.filter(x=>!getSrc(x).verified);
 if(!unresolved.length)add(target,'','Every visible mod now has a link');
 else for(const m of unresolved)add(target,m.id,'#'+m.priority+'  '+m.name);
 if(unresolved.some(x=>x.id===oldTarget))target.value=oldTarget;
 mo2UpdatePreview();
}
function mo2UpdatePreview(){const entry=MO2_PENDING.find(x=>x._key===$('mo2From').value),m=ITEM_MAP.get($('mo2To').value);
 const okay=!!entry&&!!m&&!getSrc(m).verified;
 $('mo2Bind').disabled=!okay;
 $('mo2MatchPreview').textContent=okay?`MO2: ${entry.name} • Nexus #${entry.modId}  →  ${m.name} • priority ${m.priority} • installed ${m.installedVersion||'unknown'} • ${m.group}`:'Type a folder name, then choose the matching checklist mod before binding.';
}
async function importMO2(file){if(!file)return;try{
 const data=JSON.parse(await file.text());
 if(data.format!=='MO2 Mod Update Arsenal Sources'||!Array.isArray(data.mods))throw Error('Wrong JSON. Choose the file created by the MO2 Source Exporter, not a progress backup.');
 const names=new Map();for(const m of DATA.items){const k=mo2Key(m.name);if(!names.has(k))names.set(k,[]);names.get(k).push(m)}
 let linked=0,present=0,unmatched=0,missingId=0,ambiguous=0;
 MO2_PENDING=[];
 for(const input of data.mods){if(!input||typeof input.name!=='string')continue;
  const found=names.get(mo2Key(input.name))||[];
  const id=txt(input.modId).trim();
  if(!found.length){unmatched++;if(mo2ValidId(id))MO2_PENDING.push({...input,_key:'folder-'+MO2_PENDING.length,modId:id});continue}
  if(found.length!==1){ambiguous++;continue}
  if(!mo2ValidId(id)){missingId++;continue}
  const m=found[0];if(getRow(m.id).url||m.source.verified){present++;continue}
  if(mo2ApplyLink(m,input))linked++;
 }
 save();render();$('mo2Lookup').value='';drawMO2Matcher();
 const report=`✦ ${linked} newly linked · ${present} already linked · ${unmatched} unmatched names · ${missingId} missing IDs · ${ambiguous} ambiguous matches`;
 $('mo2Results').textContent=report;
 toast('⌁ Linked '+linked+' mod pages from your MO2 data.');
}catch(e){$('mo2Results').textContent='⚠ '+e.message;toast('⚠ MO2 import failed: '+e.message)}finally{$('mo2File').value=''}}
function bindMO2Match(){const entry=MO2_PENDING.find(x=>x._key===$('mo2From').value),m=ITEM_MAP.get($('mo2To').value);if(!entry||!m)return;
 if(!mo2ApplyLink(m,entry)){toast('That mod already has a link. No changes made.');return}
 MO2_PENDING=MO2_PENDING.filter(x=>x!==entry);save();render();drawMO2Matcher();
 $('mo2Results').textContent=`✦ Linked ${m.name} to Nexus #${entry.modId} from your MO2 folder “${entry.name}”.`;
 toast('✦ Matched '+m.name+'!');
}
function drawCombatFinisher(mode,{x,y}){
 const layer=$('impactLayer'),d=document.createElement('div');d.className='combat-composition '+mode;d.style.left=x+'px';d.style.top=y+'px';d.style.setProperty('--spin',(Math.random()>.5?'1':'-1'));layer.append(d);setTimeout(()=>d.remove(),1300);
 const svg=(markup)=>{const el=document.createElementNS('http://www.w3.org/2000/svg','svg');el.setAttribute('viewBox','0 0 300 220');el.setAttribute('class','combat-svg');el.innerHTML=markup;d.append(el)};
 if(mode==='slash'){
  svg('<path class="sword-core" d="M10 195 Q120 80 285 11"/><path class="sword-echo" d="M11 204 Q128 104 288 28"/><path class="sword-cut" d="M255 17 Q143 95 22 199"/>');
  for(let i=0;i<12;i++){const p=document.createElement('i');p.className='slash-spark';p.style.setProperty('--a',(i*30)+'deg');p.style.setProperty('--travel',(32+Math.random()*105)+'px');d.append(p)}
 } else if(mode==='burn'){
  for(let i=0;i<20;i++){let p=document.createElement('i');p.className='fire-tongue';p.style.setProperty('--dx',(Math.random()*150-75)+'px');p.style.setProperty('--dy',(-Math.random()*165-25)+'px');p.style.setProperty('--delay',(Math.random()*180)+'ms');p.style.setProperty('--scale',(.5+Math.random()*1.7));d.append(p)}
  svg('<path class="fire-ring" d="M4 111 Q150 -25 296 111 Q150 250 4 111Z"/>');
 } else if(mode==='arrow'){
  svg('<path class="arrow-trail" d="M0 219 L152 109"/><path class="arrow-shaft" d="M4 205 L155 107"/><path class="arrow-head" d="M130 112 L169 99 L151 138"/><circle class="arrow-target" cx="157" cy="108" r="38"/>');
 } else if(mode==='frost'){
  svg('<circle class="ice-ring" cx="150" cy="110" r="34"/><circle class="ice-ring second" cx="150" cy="110" r="60"/>');
  for(let i=0;i<18;i++){let el=document.createElement('i');el.className='ice-crystal';el.style.setProperty('--a',(i*20)+'deg');el.style.setProperty('--distance',(45+Math.random()*90)+'px');d.append(el)}
 } else if(mode==='thunder'){
  const pts='156,-50 139,35 170,35 115,118 150,112 132,215';svg(`<polyline class="thunder-glow" points="${pts}"/><polyline class="thunder-core" points="${pts}"/><circle class="thunder-burst" cx="132" cy="213" r="14"/>`);
 } else if(mode==='shatter'){
  const lines=Array.from({length:13},(_,i)=>{const a=i*Math.PI*2/13;const x1=150+Math.cos(a)*19,y1=110+Math.sin(a)*19,x2=150+Math.cos(a)*115,y2=110+Math.sin(a)*105;return`<path class="crack-line" d="M ${x1} ${y1} L ${150+Math.cos(a+.12)*54} ${110+Math.sin(a+.12)*55} L ${x2} ${y2}"/>`}).join('');svg('<circle class="glass-impact" cx="150" cy="110" r="22"/>'+lines);
 }
}
function playClick(){if(!STORE.prefs.sound)return;try{const Audio=window.AudioContext||window.webkitAudioContext;if(!Audio)return;const ctx=new Audio();const oscillator=ctx.createOscillator(),gain=ctx.createGain();oscillator.type='triangle';oscillator.frequency.setValueAtTime(490,ctx.currentTime);oscillator.frequency.exponentialRampToValueAtTime(80,ctx.currentTime+.14);gain.gain.setValueAtTime(.055,ctx.currentTime);gain.gain.exponentialRampToValueAtTime(.001,ctx.currentTime+.17);oscillator.connect(gain);gain.connect(ctx.destination);oscillator.start();oscillator.stop(ctx.currentTime+.17);setTimeout(()=>ctx.close(),340)}catch(e){console.warn('Sound unavailable:',e)}}
const canvas=$('fxCanvas'),ctx=canvas.getContext('2d');let frame=0,canvasDpr=1;
function resizeFx(){canvasDpr=Math.min(2,devicePixelRatio||1);canvas.width=Math.round(innerWidth*canvasDpr);canvas.height=Math.round(innerHeight*canvasDpr);ctx.setTransform(canvasDpr,0,0,canvasDpr,0,0)}resizeFx();window.addEventListener('resize',resizeFx);
function spawnFinisher({x,y},forcedMode=null){const motion=matchMedia('(prefers-reduced-motion: reduce)').matches;const mode=forcedMode|| (STORE.prefs.effect==='random'?['shatter','fireworks','shot','slash','burn','arrow','frost','thunder'][Math.floor(Math.random()*8)]:STORE.prefs.effect);if(mode==='off'||motion||STORE.prefs.vfxIntensity==='off'){playClick();return}playClick();const layer=$('impactLayer');const flash=document.createElement('span');flash.className='impact-flash '+mode;flash.style.left=x+'px';flash.style.top=y+'px';layer.appendChild(flash);setTimeout(()=>flash.remove(),520);
 const word=document.createElement('span');word.className='impact-word';word.style.left=x+'px';word.style.top=y+'px';word.textContent=({shot:'CRITICAL HIT!',shatter:'SHATTERED!',slash:'SWORD STRIKE!',burn:'DRAGONFIRE!',arrow:'BULLSEYE!',frost:'FROSTBITE!',thunder:'LIGHTNING!',fireworks:'QUEST CLEARED!'})[mode]||'QUEST CLEARED!';layer.appendChild(word);setTimeout(()=>word.remove(),820);
 if(mode==='shatter'||mode==='shot'){for(let i=0;i<Math.round((mode==='shatter'?38:15)*(STORE.prefs.vfxIntensity==='subtle'?.25:STORE.prefs.vfxIntensity==='vivid'?.60:1));i++){const a=Math.random()*Math.PI*2,r=50+Math.random()*180,shard=document.createElement('span');shard.className='glass-shard';const values={'--x':x+'px','--y':y+'px','--w':(5+Math.random()*18)+'px','--h':(7+Math.random()*21)+'px','--dx':Math.cos(a)*r+'px','--dy':Math.sin(a)*r*.75+'px','--rot':(Math.random()*940-470)+'deg','--dur':(650+Math.random()*400)+'ms'};for(const [k,v]of Object.entries(values))shard.style.setProperty(k,v);layer.append(shard);setTimeout(()=>shard.remove(),1100)} }
 if(mode==='shot'){const el=document.elementFromPoint(Math.max(0,x),Math.max(0,y))?.closest('.mod-row');if(el){el.classList.add('shot-hit');setTimeout(()=>el.classList.remove('shot-hit'),800)}}
 if(['slash','burn','arrow','frost','thunder','shatter'].includes(mode))drawCombatFinisher(mode,{x,y});
 const density=STORE.prefs.vfxIntensity==='subtle'?.24:STORE.prefs.vfxIntensity==='vivid'?.60:1;const n=Math.ceil((mode==='fireworks'?125:mode==='shatter'?68:mode==='burn'?110:75)*density);for(let i=0;i<n;i++){const theta=Math.random()*Math.PI*2,speed=(mode==='fireworks'?1.1:1.7)+Math.random()*(mode==='fireworks'?6.1:4.1);activeFx.push({x,y,vx:Math.cos(theta)*speed,vy:Math.sin(theta)*speed-((mode==='fireworks')?1.5:0),life:1,loss:.012+Math.random()*.017,size:1+Math.random()*3,hue:(mode==='burn'?[9,20,38,52]:mode==='frost'?[186,192,208,227]:mode==='thunder'?[44,55,190,268]:mode==='slash'?[171,190,211,345]:[42,163,191,352])[Math.floor(Math.random()*4)],shape:Math.random()>.7?'square':'circle',gravity:mode==='burn'?-0.025-Math.random()*.052:.035+Math.random()*.06})}if(!frame)frame=requestAnimationFrame(animateFx)}
function animateFx(){ctx.clearRect(0,0,innerWidth,innerHeight);activeFx=activeFx.filter(p=>p.life>0);for(const p of activeFx){p.x+=p.vx;p.y+=p.vy;p.vy+=p.gravity;p.vx*=.992;p.life-=p.loss;ctx.globalAlpha=Math.max(0,p.life);ctx.fillStyle=`hsl(${p.hue} 95% ${58+20*p.life}%)`;ctx.shadowColor=ctx.fillStyle;ctx.shadowBlur=12*p.life;if(p.shape==='square')ctx.fillRect(p.x,p.y,p.size*1.4,p.size*1.4);else{ctx.beginPath();ctx.arc(p.x,p.y,p.size,0,Math.PI*2);ctx.fill()}}ctx.globalAlpha=1;ctx.shadowBlur=0;if(activeFx.length)frame=requestAnimationFrame(animateFx);else{frame=0;ctx.clearRect(0,0,innerWidth,innerHeight)}}
// Full-profile boss finale. The visual spectacle never grants progress or XP.
const victoryReducedMotion=()=>matchMedia('(prefers-reduced-motion: reduce)').matches;
function victoryDecorate(){const sky=$('victorySky'),runes=$('victoryRunes');sky.replaceChildren();runes.replaceChildren();const count=victoryReducedMotion()?0:150;for(let i=0;i<count;i++){const a=Math.random()*Math.PI*2,d=110+Math.random()*Math.max(innerWidth,innerHeight)*.8;const shard=document.createElement('i');shard.className='victory-shard';shard.style.setProperty('--dx',Math.round(Math.cos(a)*d)+'px');shard.style.setProperty('--dy',Math.round(Math.sin(a)*d)+'px');shard.style.setProperty('--rot',Math.round(Math.random()*1500-750)+'deg');shard.style.setProperty('--w',(3+Math.random()*15)+'px');shard.style.setProperty('--h',(8+Math.random()*30)+'px');shard.style.setProperty('--dur',(850+Math.random()*900)+'ms');shard.style.setProperty('--delay',(Math.random()*900)+'ms');sky.append(shard)}
 const glyphs='ᛟᚨᛝᚱᚷᛉᛞᛠᛤᚺᛦ';for(let i=0;i<(victoryReducedMotion()?0:44);i++){const rune=document.createElement('i');rune.textContent=glyphs[Math.floor(Math.random()*glyphs.length)];rune.style.left=Math.round(Math.random()*100)+'%';rune.style.top=Math.round(Math.random()*100)+'%';rune.style.animationDelay=(Math.random()*2)+'s';rune.style.fontSize=(15+Math.random()*24)+'px';runes.append(rune)} }
function playVictoryFanfare(){if(!STORE.prefs.sound)return;try{const A=window.AudioContext||window.webkitAudioContext;if(!A)return;const context=new A();const notes=[196,294,392,494,587,784,987,1174];let t=context.currentTime+.12;notes.forEach((hz,i)=>{const osc=context.createOscillator(),gain=context.createGain();osc.type=i===notes.length-1?'sine':'triangle';osc.frequency.value=hz;const at=t+i*.15;gain.gain.setValueAtTime(.0001,at);gain.gain.exponentialRampToValueAtTime(.052,at+.04);gain.gain.exponentialRampToValueAtTime(.0001,at+(i===notes.length-1?.7:.24));osc.connect(gain);gain.connect(context.destination);osc.start(at);osc.stop(at+(i===notes.length-1?.74:.25))});setTimeout(()=>context.close().catch(()=>{}),2800)}catch(ex){console.warn('Optional celebration audio not available',ex.message)}}
function victoryBlast(){VICTORY_TIMERS.forEach(clearTimeout);VICTORY_TIMERS=[];victoryDecorate();const ov=$('victoryOverlay');ov.classList.remove('victory-restart');void ov.offsetWidth;ov.classList.add('victory-restart');if(victoryReducedMotion())return;
 const types=['thunder','shatter','slash','burn','frost','shot','fireworks','arrow','fireworks','burn','shatter','thunder','fireworks','fireworks'];
 types.forEach((mode,i)=>{VICTORY_TIMERS.push(setTimeout(()=>{if($('victoryOverlay').hidden)return;const old=STORE.prefs.effect;STORE.prefs.effect=mode;spawnFinisher({x:innerWidth*(.15+Math.random()*.7),y:innerHeight*(.18+Math.random()*.65)});STORE.prefs.effect=old},i*190+300))})}
function launchVictory(record,practice){if(VICTORY_ACTIVE)return;VICTORY_ACTIVE={record,practice};$('victoryOverlay').hidden=false;document.body.classList.add('victory-open');$('victoryTier').textContent=practice?'PRACTICE · A TASTE OF SOVNGARDE':'WORLD-EATER DEFEATED · VERIFIED FULL CLEAR';$('victoryTitle').innerHTML=practice?'A LEGEND <em>AWAKENS.</em>':'SOVNGARDE <em>IS YOURS.</em>';
 $('victorySubtitle').textContent=practice?'This is a cinematic preview. Rank and trophies require a verified MO2 update and fresh completed deep Nexus audit.':`A flawless expedition! ${Number(record.verifiedMods||0)} Nexus-linked mods cleared in a fresh full audit. Your legend enters the Chronicle.`;
 SET('victoryMods',Number(record.verifiedMods||0).toLocaleString());SET('victoryXP','+'+Number(record.bonus||0).toLocaleString()+' XP');SET('victoryScore',practice?'PRACTICE':Number(GAME?.score||0).toLocaleString());SET('victoryEvidence',practice?'NO XP, RANK, OR TROPHIES AWARDED · PREVIEW ONLY':'MO2 update evidence + full Nexus audit · saved permanently to the profile');$('victoryClaim').focus({preventScroll:true});playVictoryFanfare();victoryBlast();}
async function closeVictory(){if(!VICTORY_ACTIVE)return;const record=VICTORY_ACTIVE.record,practice=VICTORY_ACTIVE.practice;VICTORY_ACTIVE=null;VICTORY_TIMERS.forEach(clearTimeout);VICTORY_TIMERS=[];$('victoryOverlay').hidden=true;document.body.classList.remove('victory-open');$('victorySky').replaceChildren();$('victoryRunes').replaceChildren();if(!practice&&BRIDGE.active&&record?.id&&LIVE_CATALOG){try{await bridgeRequest('POST','victory/ack',{profileId:LIVE_CATALOG.profileId,victoryId:record.id});await refreshGame()}catch(ex){toast('Victory stored in MO2; acknowledgment pending: '+ex.message)}}}
$('previewVictory').onclick=()=>launchVictory({verifiedMods:BRIDGE.active?(DATA.items.filter(x=>x.nexusId).length):DATA.items.length,bonus:0,updates:counts().done},true);
$('raidAction').onclick=()=>BRIDGE.active&&LIVE_CATALOG?scanLive('deep'):launchVictory({verifiedMods:DATA.items.length,bonus:0,updates:counts().done},true);
$('victoryClaim').onclick=closeVictory;$('victoryReplay').onclick=()=>VICTORY_ACTIVE&&victoryBlast();
// Centralized controls: the list is fully rendered, with event delegation and no viewport record caps.
$('modList').addEventListener('pointerover',e=>{const r=e.target.closest('.mod-row');if(r)window.AetherFX?.hover(r)});
$('modList').addEventListener('click',e=>{if(e.target.closest('[data-insight-act]'))return;const togg=e.target.closest('[data-chapter-toggle]');if(togg){const id=togg.dataset.chapterToggle;STORE.prefs.collapsed=STORE.prefs.collapsed.includes(id)?STORE.prefs.collapsed.filter(x=>x!==id):[...STORE.prefs.collapsed,id];save();render();return}
 const row=e.target.closest('.mod-row');const act=e.target.closest('[data-act]');if(!act){if(e.target.id==='emptyReset')resetFilters();if(row){const hit=ITEM_MAP.get(row.dataset.id);if(hit){focusedRowId=hit.id;window.AetherFX?.select(row,{source:'app'});if(BRIDGE.active)locateInMO2(hit,row)}}return}const m=ITEM_MAP.get(act.dataset.id);if(!m)return;if(row&&act.dataset.act!=='complete'){focusedRowId=m.id;window.AetherFX?.select(row,{source:'app'})}
 if(act.dataset.act==='complete'){e.preventDefault();focusedRowId=m.id;toggleDone(m.id,act)}
 if(act.dataset.act==='locate'){e.preventDefault();locateInMO2(m,act);return}
 if(act.dataset.act==='files'){e.preventDefault();openFilePicker(m);return}
 if(act.dataset.act==='inspect'){e.preventDefault();drawInspector(m)}
 if(act.dataset.act==='favorite'){e.preventDefault();window.AetherFX?.favorite(act);STORE.rows[m.id]={...getRow(m.id),favorite:!isFav(m)};save(false);render();toast(isFav(m)?'★ Bookmarked '+m.name:'Bookmark removed')}
 if(act.dataset.act==='source'){STORE.rows[m.id]={...getRow(m.id),lastOpened:new Date().toISOString()};save(false)}
});
$('chapters').addEventListener('click',e=>{const b=e.target.closest('[data-chapter]');if(b)jumpChapter(b.dataset.chapter)});
$('chapterSearch').addEventListener('input',renderChapters);
$all('[data-filter]').forEach(b=>b.addEventListener('click',()=>{STORE.prefs.filter=b.dataset.filter;save();render()}));
$('sortSelect').addEventListener('change',e=>updatePrefs({sort:e.target.value}));
$('search').value=STORE.prefs.query;$('search').addEventListener('input',e=>{STORE.prefs.query=e.target.value;saveSoon();scheduleSearchRender()});
window.addEventListener('pagehide',()=>{if(SAVE_TIMER!==null)save()});
$('clearSearch').addEventListener('click',()=>{STORE.prefs.query='';$('search').value='';save();render();$('search').focus()});
$('clearFilters').onclick=resetFilters;
$('collapseAll').onclick=toggleAllChapters;
$('densityToggle').onclick=()=>updatePrefs({density:STORE.prefs.density==='compact'?'comfy':'compact'});
$('focusToggle').onclick=()=>updatePrefs({focus:!STORE.prefs.focus});
$('focusTop').onclick=()=>updatePrefs({focus:!STORE.prefs.focus});
$('effectSelect').onchange=e=>updatePrefs({effect:e.target.value});$('fxSettingsToggle').onclick=()=>{const panel=$('fxStudio');panel.hidden=!panel.hidden;$('fxSettingsToggle').setAttribute('aria-expanded',String(!panel.hidden))};$('fxIntensity').onchange=e=>updatePrefs({vfxIntensity:e.target.value});$('fxShake').onchange=e=>updatePrefs({vfxShake:e.target.checked});$('fxTrails').onchange=e=>updatePrefs({vfxTrails:e.target.checked});$('fxTest').onclick=()=>{if(STORE.prefs.vfxIntensity==='off'||matchMedia('(prefers-reduced-motion: reduce)').matches){toast('✧ Preview unavailable while visual effects are disabled or Reduced Motion is enabled.');return}window.AetherFX?.preview();toast('✧ Visual preview · no progress or ranked XP')};$('fxClose').onclick=()=>{$('fxStudio').hidden=true;$('fxSettingsToggle').setAttribute('aria-expanded','false')};$('fxStudioDone').onclick=$('fxClose').onclick;
$('soundToggle').onclick=()=>updatePrefs({sound:!STORE.prefs.sound});
$('themeToggle').onclick=()=>updatePrefs({theme:STORE.prefs.theme==='night'?'snow':'night'});
$('nextPending').onclick=nextPending;$('showCritical').onclick=()=>{STORE.prefs.filter='risky';STORE.prefs.query='';$('search').value='';STORE.prefs.collapsed=[];save();render();$('workbench')?.scrollIntoView({behavior:'smooth'})};
$('backToTop').onclick=()=>scrollTo({top:0,behavior:'smooth'});
$('advisoryToggle').onclick=()=>{const s=document.querySelector('.advisory');s.classList.toggle('collapsed');$('advisoryBody').hidden=s.classList.contains('collapsed');$('advisoryToggle').setAttribute('aria-expanded',String(!s.classList.contains('collapsed')))};
$('exportTop').onclick=exportJSON;$('backupBtn').onclick=exportJSON;$('sidebarBackup').onclick=exportJSON;$('csvBtn').onclick=exportCSV;
$('importBtn').onclick=()=>$('importFile').click();$('importFile').onchange=e=>importJSON(e.target.files?.[0]);
$('mo2Btn').onclick=mo2Open;$('mo2Close').onclick=mo2Close;$('mo2Veil').addEventListener('click',e=>{if(e.target===$('mo2Veil'))mo2Close()});
$('mo2Download').onclick=()=>saveFile('MO2_Source_Exporter.ps1','text/plain',MO2_EXPORTER);
$('mo2Import').onclick=()=>$('mo2File').click();$('mo2File').onchange=e=>importMO2(e.target.files?.[0]);$('mo2Lookup').oninput=drawMO2Matcher;$('mo2From').onchange=mo2UpdatePreview;$('mo2To').onchange=mo2UpdatePreview;$('mo2Bind').onclick=bindMO2Match;
$('drawerClose').onclick=closeInspector;$('drawerVeil').onclick=closeInspector;
$('helpToggle').onclick=()=>help(true);$('helpClose').onclick=()=>help(false);$('helpOK').onclick=()=>help(false);$('helpVeil').addEventListener('click',e=>{if(e.target===$('helpVeil'))help(false)});
$('menuToggle').onclick=()=>$('sidebar').classList.toggle('open');document.addEventListener('click',e=>{if(!$('fxStudio').hidden&&!e.target.closest('#fxStudio')&&!e.target.closest('#fxSettingsToggle')){$('fxStudio').hidden=true;$('fxSettingsToggle').setAttribute('aria-expanded','false')}if($('sidebar').classList.contains('open')&&!e.target.closest('#sidebar')&&!e.target.closest('#menuToggle'))$('sidebar').classList.remove('open')});
document.addEventListener('keydown',e=>{
 if(!$('nativeWelcome').hidden||$('connectionDetails').open)return;const inField=e.target.matches('input,textarea,select,[contenteditable]');if(e.key==='Escape'){$('fxStudio').hidden=true;$('fxSettingsToggle').setAttribute('aria-expanded','false');closeInspector();help(false);mo2Close();closeFilePicker();closeInstallPreview();closeQueuePreview();closeVictory();$('sidebar').classList.remove('open');if(inField)e.target.blur();return}
 if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();closeInspector();selectWorkspace('mods');$('search').focus();$('search').select();return}
 if(inField||!$('helpVeil').hidden||!$('mo2Veil').hidden||!$('installPreviewVeil').hidden||!$('queueReviewVeil').hidden||!$('filePickerVeil').hidden||$('detailDrawer').classList.contains('open'))return;
 if(e.altKey&&e.key.toLowerCase()==='u'){e.preventDefault();unifiedScroll('updateWorkshop');$('workshopSearch').focus();return;}
 if(e.altKey&&e.key.toLowerCase()==='r'&&BRIDGE.active){e.preventDefault();reviewNextWorkshopCandidate();return;}
 if(e.key.toLowerCase()==='f'){e.preventDefault();updatePrefs({focus:!STORE.prefs.focus});return}if(e.key.toLowerCase()==='n'){e.preventDefault();nextPending();return}if(e.key==='?'||e.key==='F1'){e.preventDefault();help(true);return}
 if(e.key.toLowerCase()==='j'||e.key.toLowerCase()==='k'){e.preventDefault();const ids=filtered().filter(m=>!STORE.prefs.collapsed.includes(m.groupId)).map(m=>m.id);if(!ids.length)return;let i=ids.indexOf(focusedRowId);i=(i+(e.key.toLowerCase()==='j'?1:-1)+ids.length)%ids.length;focusRow(ids[i]);return}
 if(e.code==='Space'&&focusedRowId){e.preventDefault();toggleDone(focusedRowId,$('row-'+focusedRowId)?.querySelector('[data-act="complete"]'))}
});
// No remote privileges in the browser; the plugin serves a tokenized same-origin loopback endpoint.
async function bridgeRequest(method,path,body){
 if(!BRIDGE.active)throw Error('Launch the app from the MO2 plugin to connect');
 if(method==='POST'&&!LIVE_CONNECTED)throw Error('MO2 is disconnected; wait for the connection to recover');
 if(method==='POST'&&sourceLinksPending()&&['quick-update','update-queue','download','install','install-ready','retry-download','scan','startup-check'].includes(path))throw Error('Source links are still syncing with MO2. Wait for the save to finish, then retry.');
 if(method==='POST'&&body?.profileId===LIVE_CATALOG?.profileId&&Number.isSafeInteger(LIVE_CATALOG?.executionEpoch))body={...body,executionEpoch:body.executionEpoch??LIVE_CATALOG.executionEpoch};
 const key=PROFILE_EPOCH+':'+BRIDGE_READ_REVISION+':'+path;
 if(method==='GET'&&BRIDGE_READS.has(key))return BRIDGE_READS.get(key);
 const pending=(async()=>{const controller=new AbortController(),tm=setTimeout(()=>controller.abort(),method==='GET'?24000:195000);
  try{const r=await fetch('/api/'+path,{method,headers:{'X-Arsenal-Token':BRIDGE.token,...(method==='POST'?{'Content-Type':'application/json'}:{})},body:method==='POST'?JSON.stringify(body||{}):undefined,cache:'no-store',signal:controller.signal});const j=await r.json();if(!r.ok||j.ok===false)throw Error(j.error||'Bridge request rejected');return j}finally{clearTimeout(tm)}
 })();
 if(method==='GET')BRIDGE_READS.set(key,pending);
 try{return await pending}finally{if(BRIDGE_READS.get(key)===pending)BRIDGE_READS.delete(key)}
}
function setBridgeConnection(connected,message){
 const changed=LIVE_CONNECTED!==connected;LIVE_CONNECTED=connected;
 const pill=$('livePill');pill.textContent=connected?'● MO2 CONNECTED':'⚠ DISCONNECTED';pill.classList.toggle('connected',connected);
 if(message)SET('liveState',message);
 for(const id of ['liveAll','liveQuick','liveDeep','liveLocal','liveRefresh','liveRuntime','liveConnect','liveBackup'])$(id).disabled=!connected||(['liveAll','liveQuick','liveDeep','liveLocal'].includes(id)&&(SCAN_START_BUSY||LIVE_SCAN?.state==='running'));
 if(!connected){if(changed){renderWorkshop();renderDownloadPulse(WORKSHOP_TICKETS)}SET('liveSync','Connection lost · retrying automatically');SET('unifiedEdition',LIVE_CATALOG?'⚠ MO2 DISCONNECTED · LAST SNAPSHOT':'⚠ MO2 DISCONNECTED · RETRYING');}
}
async function requestLiveCatalog(method='GET',path='snapshot',body,fresh=false){
 // A completed source edit must not reuse a snapshot started before that edit.
 if(fresh)BRIDGE_READ_REVISION++;
 const request=++CATALOG_REQUEST,context=profileContext(),result=await bridgeRequest(method,path,body);
 if(request!==CATALOG_REQUEST||!contextIsCurrent(context))return null;
 const ready=activateLiveCatalog(result.catalog);
 if(Number.isFinite(result.generation))LIVE_GENERATION=result.generation;
 return{...result,ready};
}
function activateLiveCatalog(c){
 if(!c||!Array.isArray(c.items)||!Array.isArray(c.groups)||!c.profileId)throw Error('Invalid profile snapshot');
 const newProfile=LIVE_CATALOG?.profileId!==c.profileId;
 const newExecutionContext=newProfile||(Number.isSafeInteger(LIVE_CATALOG?.executionEpoch)&&Number.isSafeInteger(c.executionEpoch)&&LIVE_CATALOG.executionEpoch!==c.executionEpoch);
 CATALOG_REQUEST++;WORKSHOP_REQUEST++;GAME_REQUEST++;CATALOG_RENDER_REVISION++;LOCAL_PLAN_CACHE=null;
 if(newProfile&&(SAVE_TIMER!==null||(STORAGE.startsWith('mo2-update-arsenal:')&&PROGRESS_READY)))save();
 LIVE_CATALOG=c;DATA=c;ITEM_MAP=new Map(c.items.map(m=>[m.id,m]));GROUP_MAP=new Map(c.groups.map(g=>[g.id,g]));STORAGE='mo2-update-arsenal:v2:'+c.profileId;
 if(newExecutionContext){STARTUP_RETRY_AT=0;STARTUP_ERROR='';PROFILE_EPOCH++;SCAN_OPERATION++;QUICK_OPERATION++;WORKSHOP_OPERATION++;clearTimeout(SCAN_TIMER);SCAN_TIMER=null;SCAN_START_BUSY=false;LIVE_SCAN=null;WORKSHOP_BUSY=false;}
 if(newProfile){SOURCE_EDIT_REVISION=0;SOURCE_SYNCED_REVISION=0;clearTimeout(SAVE_TIMER);SAVE_TIMER=null;clearTimeout(PROGRESS_TIMER);PROGRESS_READY=false;GAME=null;GAME_SIGNATURE='';GAME_SEEN=-1;NATIVE_SELECTED='';focusedRowId='';STORE=structuredClone(BASE);load();for(const row of Object.values(STORE.rows))if(row.url)markSourceEdit('',row.url);}
 STORE.prefs.collapsed=(Array.isArray(STORE.prefs.collapsed)?STORE.prefs.collapsed:[]).filter(id=>GROUP_MAP.has(id));$('search').value=STORE.prefs.query;
 const currentRuntime=c.gameRuntime||'unknown';let changedRuntime=false,changedMods=0;
 if(STORE.verifiedRuntime&&STORE.verifiedRuntime!==currentRuntime){for(const [id,row] of Object.entries(STORE.rows)){if(row.compat==='compatible'){delete row.compat;if(row.status==='done')row.status='review';changedRuntime=true}}}
 for(const m of c.items){const row=STORE.rows[m.id];if(row?.status==='done'&&row.observedInstalledVersion&&row.observedInstalledVersion!==m.installedVersion){row.status='review';changedMods++}}
 STORE.verifiedRuntime=currentRuntime;
 if(changedRuntime||changedMods)save();
 const r=currentRuntime;$('runtimeBadge').innerHTML=`<span class="status-light"></span><div><strong>${escapeHTML(c.game||'ACTIVE GAME')} / ${escapeHTML(c.profileName)}</strong><span>Target runtime: <b>${escapeHTML(r)}</b> · ${c.items.length} installed mods</span></div><span class="runtime-lock">LIVE MO2</span>`;
 $('referenceLinks').innerHTML=`<a href="https://www.nexusmods.com/${encodeURIComponent(c.game||'skyrimspecialedition')}" target="_blank" rel="noopener noreferrer">Nexus ${escapeHTML(c.game||'Game')} ↗</a><span>•</span><a href="https://www.nexusmods.com/users/myaccount?tab=api" target="_blank" rel="noopener noreferrer">Nexus account ↗</a>`;
 $('heroSummary').innerHTML=`Your active <strong>${escapeHTML(c.profileName)}</strong> profile in <strong>${escapeHTML(c.game||'MO2')}</strong>. Navigate installed mods, inspect verified metadata, queue files into MO2, and celebrate every safe update. Runtime ${escapeHTML(r)}; double-check native DLLs and requirements.`;
 $('advisoryToggle').querySelector('b').textContent='LIVE UPDATE SAFETY PROTOCOL';
 $('advisoryBody').innerHTML='<div><span>01 / RUNTIME</span><strong>Match executable + script extender.</strong><p>Native DLL releases must match this game runtime. Check each file description.</p></div><div><span>02 / DEPENDENCIES</span><strong>Check masters and requirements.</strong><p>Verify mod dependencies, ESL records, plugins and update steps.</p></div><div><span>03 / SAVE SAFETY</span><strong>Read changelogs before upgrades.</strong><p>Save-breaking changes, FOMOD variants, mid-save warnings and generated behaviors may require special handling.</p></div><div><span>04 / CONTROLLED INSTALL</span><strong>MO2 stays in charge.</strong><p>Back up the profile, download through MO2 and install with normal prompts. Nothing silently overwrites your load order.</p></div>';
 $('riskSubtitle').textContent='DLLs + runtime + risky masters';document.querySelector('.pagefooter').children[1].textContent='LOCAL-FIRST ARSENAL · LIVE MO2 + NEXUS INTELLIGENCE · PROFILE SAVE';document.querySelector('.pagefooter').firstElementChild.textContent=`ᛟ MO2 LIVE / ${c.game} / ${c.profileName}`;
 SET('liveProfile',c.profileName+' / '+c.game);SET('liveCount',c.items.length+' mods · '+c.groups.length+' chapters');setBridgeConnection(true,'Live session. Your active MO2 profile is authoritative; the checklist is saved per profile.');
 $('liveSync').textContent='Snapshot: '+new Date().toLocaleTimeString();
 const chosen=STORE.prefs.filter;if(chosen==='updates'&&!c.items.some(isUpdate))STORE.prefs.filter='all';
 if(newProfile){closeInspector();}else{refreshInspectorInsights();if(openedId&&ITEM_MAP.has(openedId)){void loadModInsights(ITEM_MAP.get(openedId));void loadSourceDetails(ITEM_MAP.get(openedId));}}
 if(newExecutionContext||changedRuntime||changedMods){WORKSHOP_PLAN=null;WORKSHOP_CHOICES.clear();QUEUE_PENDING=null;$('queueReviewVeil').hidden=true;INSTALL_PREVIEW_ID=null;$('installPreviewVeil').hidden=true;closeFilePicker();}
 if(newExecutionContext){if(QUICK_POLL){clearTimeout(QUICK_POLL);QUICK_POLL=null;}QUICK_RUNNING=false;FAST_PICKER_ID=null;QUICK_SKIPPED.clear();}
 if(newProfile){WORKSHOP_TICKETS={};WORKSHOP_TICKET_SIGNATURE='';$('liveDownloadList').innerHTML='';renderDownloadPulse({});ACTIVITY_RECORDS=[];ACTIVITY_WARNING='';ACTIVITY_GENERATION++;renderActivity();refreshActivity();}
 applyAppearance();updateStorageIndicator();render();renderLiveStats();if(changedRuntime||changedMods)toast('⚠ Previous cleared checks need review: runtime or installed version changed.');
 if(newExecutionContext&&!newProfile&&PROGRESS_READY)scheduleProgressSync();
 const context=profileContext();return Promise.allSettled([...((newProfile||(newExecutionContext&&!PROGRESS_READY))?[hydrateProgress(c.profileId)]:[]),refreshGame(),refreshWorkshop().catch(ex=>{if(contextIsCurrent(context))workshopSetStatus('Plan unavailable: '+ex.message)})]);
}

function refreshUnifiedCommand(){
 const el=$('unifiedEdition');if(!el)return;
 const live=BRIDGE.active&&LIVE_CONNECTED&&!!LIVE_CATALOG;
 if(BRIDGE.active&&!LIVE_CONNECTED){el.textContent=LIVE_CATALOG?'⚠ MO2 DISCONNECTED · LAST SNAPSHOT':'⚠ MO2 DISCONNECTED · RETRYING';SET('unifiedUpdateState','Waiting for MO2 connection; retrying automatically');return;}
 const imported=!!DATA?.offline&&!live;
 const count=DATA?.items?.length||0;
 el.textContent=live?'● LIVE MO2 · '+LIVE_CATALOG.profileName:imported?'◈ IMPORTED MO2 · READ ONLY':'◌ STANDALONE · PRACTICE';
 const plan=typeof currentWorkshopPlan==='function'?currentWorkshopPlan():null;
 const candidates=plan?.summary?.candidates||0;
 const checked=live&&WORKSHOP_PLAN?candidates+' candidate'+(candidates===1?'':'s')+' · '+count+' installed mods':imported?count+' imported mods · remote status unverified':'Sample archive · live updates unavailable';
 SET('unifiedUpdateState',checked);
 $('unifiedScan').textContent=live?'Check for updates':'Connect MO2';
}
function unifiedScroll(id){selectWorkspace(({updateWorkshop:'updates',workbench:'mods',gameBoard:'extras',liveHub:'tools',liveDownloadList:'downloads',activityPanel:'history'})[id]||'mods');const target=$(id);if(target){target.scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'});}}
document.addEventListener('click',event=>{if(event.target.closest?.('[data-filter]'))selectWorkspace('mods')});
$all('.workspace-tabs [data-workspace]').forEach(button=>button.addEventListener('click',()=>selectWorkspace(button.dataset.workspace)));
$('autoCheckOnLaunch').onchange=()=>{STORE.prefs.autoCheckOnLaunch=$('autoCheckOnLaunch').checked;save(false);renderWorkspace();void ensureStartupCheck()};
$('workspaceCancel').onclick=async()=>{
 const context=profileContext(),jobId=LIVE_SCAN?.jobId;if(!context.profile||!Number.isSafeInteger(jobId))return;
 $('workspaceCancel').disabled=true;
 try{const response=await bridgeRequest('POST','cancel-scan',{profileId:context.profile,jobId});if(contextIsCurrent(context)&&response.scan){updateScanUI(response.scan);await requestLiveCatalog()}}
 catch(error){toast('Could not cancel this check: '+error.message)}finally{$('workspaceCancel').disabled=false}
};
$('workspaceDetails').onclick=()=>window.modhunterDesktopAction?.('diagnostics');
$('unifiedScan').onclick=()=>{if(BRIDGE.active&&LIVE_CATALOG)scanLive('all');else{unifiedScroll('liveHub');workshopSetStatus(DATA?.offline?'Imported profile is read only. Launch the MO2 plugin for real update checks.':'Standalone sample mode. Install or launch the optional MO2 plugin to verify and download updates.')}};
$('unifiedReview').onclick=()=>{unifiedScroll('updateWorkshop');$('workshopFilter').value='candidate';renderWorkshop()};
$('unifiedQuest').onclick=()=>unifiedScroll('gameBoard');
$('unifiedFX').onclick=()=>{$('fxSettingsToggle').click()};

function workshopSource(item){
 const u=getSrc(item),link=validUrl(u.url);
 return link&&u.verified?link:'';
}
function workshopLocalPlan(){
 const summary={candidates:0,review:0,manual:0,current:0,risky:0,total:DATA.items.length};
 const items=DATA.items.map(m=>{
  const verdict=m.update?.verdict||'unverified';
  const lane=verdict==='candidate'?'candidate':['current','newer-installed'].includes(verdict)?'current':!Number(m.nexusId||0)?'manual':'review';
  summary[({candidate:'candidates',review:'review',manual:'manual',current:'current'})[lane]]++;
  const risky=isRisk(m);if(risky)summary.risky++;
  return{id:m.id,name:m.name,nexusId:Number(m.nexusId||0),installedVersion:m.installedVersion||'',latestVersion:m.update?.latestVersion||'',verdict,lane,priority:m.priority,group:m.group,risks:risky?['Review compatibility and installation prerequisites']:[],sourceUrl:workshopSource(m),sourceVerified:!!workshopSource(m),provider:m.update?.provider||m.source?.type||'unknown',releaseAssets:m.update?.releaseAssets||[],releaseAssetCount:m.update?.releaseAssetCount??m.update?.releaseAssets?.length??0};
 });
 return{ok:true,profileId:LIVE_CATALOG?.profileId||'',profileName:LIVE_CATALOG?.profileName||'Standalone sample',game:DATA.game||'skyrimspecialedition',gameRuntime:DATA.gameRuntime||'unknown',summary,items};
}
function currentWorkshopPlan(){return WORKSHOP_PLAN||(LOCAL_PLAN_CACHE??=workshopLocalPlan())}
function workshopSetStatus(value){SET('workshopStatus',value)}
function renderWorkshopJourney(){
 const p=currentWorkshopPlan(),s=p.summary;
 const selected=[...WORKSHOP_CHOICES.values()].filter(x=>x.profileId===p.profileId).length;
 const tickets=Object.values(WORKSHOP_TICKETS);
 const ready=tickets.filter(t=>t.status==='complete').length;
 const installing=tickets.filter(t=>t.status==='installing').length;
 const downloading=tickets.filter(t=>['downloading','paused'].includes(t.status)).length;
 const installed=tickets.filter(t=>t.status==='installed').length;
 const scanned=BRIDGE.active&&['completed','partial'].includes(LIVE_SCAN?.state);
 const steps=[
  ['01','DISCOVER',scanned?'Scan complete':BRIDGE.active?'Check sources':'Connect MO2',scanned],
  ['02','REVIEW',selected?selected+' reviewed':s.candidates+' candidates',selected>0||tickets.length>0],
  ['03','INSTALL',installing?installing+' in MO2':ready?ready+' ready':installed?installed+' installed':downloading?downloading+' downloading':'Native installer',installed>0]
 ];
 setHTML($('workshopJourney'),steps.map(([n,label,detail,done])=>'<div class="workshop-journey-step'+(done?' is-done':'')+'"><b>'+n+'</b><div><strong>'+label+'</strong><small>'+escapeHTML(detail)+'</small></div></div>').join(''));
 let action='scan',title='Find updates from your mod manager',hint='Scan every installed mod and known public release source.',label='Scan all sources';
 if(!BRIDGE.active){action='connect';title=DATA?.offline?'MO2 profile imported · read only':'Standalone preview';hint='Open the MO2 plugin for live downloads and installs. This checklist stays available offline.';label='Connection options';}
 else if(ready){action='install';title=ready+' archive'+(ready===1?' is':'s are')+' ready to install';hint='Use MO2’s normal installer. Review FOMOD choices and overrides before confirming.';label='Go to ready downloads';}
 else if(installing){action='downloads';title='Complete the installer in MO2';hint='The dashboard stays responsive during installation and reports when MO2 finishes.';label='View install progress';}
 else if(selected){action='queue';title=selected+' exact file'+(selected===1?' is':'s are')+' reviewed';hint='Send the approved archives to MO2 Downloads. Installation is always a separate action.';label='Queue reviewed downloads';}
 else if(downloading){action='downloads';title=downloading+' download'+(downloading===1?' is':'s are')+' in MO2';hint='Watch download status here, then install when complete.';label='View downloads';}
 else if(s.candidates){action='quick';title=s.candidates+' update candidate'+(s.candidates===1?'':'s')+' found';hint='Matched releases download in MO2 immediately. Ambiguous editions remain available for file selection.';label='Update matched mods';}
 else if(scanned){action='scan';title='No verified updates to queue';hint=(s.review+s.manual)+' mod'+(s.review+s.manual===1?'':'s')+' still need manual verification.';label='Run scan again';}
 $('workshopNextTitle').textContent=title;$('workshopNextHint').textContent=hint;
 const btn=$('workshopNextAction');btn.dataset.action=action;btn.textContent=label;
 btn.disabled=WORKSHOP_BUSY||(BRIDGE.active&&!LIVE_CONNECTED)||(LIVE_SCAN?.state==='running'&&action==='scan')||(sourceLinksPending()&&['quick','queue','install','scan'].includes(action));
}
function blocksNextUpdate(ticket){
 if(!ticket?.itemId||['failed','removed'].includes(ticket.status))return false;
 const mod=ITEM_MAP.get(ticket.itemId);
 return ticket.status!=='installed'||!mod||String(mod.installedVersion||'')===String(ticket.installedVersion||'');
}
function workshopEntry(m,ticket){
  const choice=WORKSHOP_CHOICES.get(m.id);
  const match=ITEM_MAP.get(m.id)||{id:m.id,name:m.name,group:m.group,nexusId:m.nexusId,source:{}};
  const quickReason=QUICK_SKIPPED.get(m.id),row=getRow(m.id),sourcePending=sourceLinksPending();
  return {key:m.id,html:cachedMarkup(WORKSHOP_MARKUP_CACHE,m,[CATALOG_RENDER_REVISION,BRIDGE.active,DATA.offline,match,match.localAnalysis,match.modHealth,match.update,row.url,choice,choice?.fileName,choice?.displayName,choice?.fileId,choice?.assetId,ticket?.status,quickReason,sourceTimePhase(match),sourcePending],()=>{
  const source=getSrc(match),official=validUrl(m.sourceUrl),src=source.custom?source.url:(official||source.url);
  const exactSource=Boolean(official||source.verified);
  const release=validUrl(m.releaseUrl),hasAlternateRelease=Boolean(release&&release!==src);
  const sourceReady=!['github','gitlab','modrinth','thunderstore'].includes(SOURCES?.provider(match))||SOURCES.canReview(match);
  const compatible=m.lane==='candidate',warning=m.risks?.length;
  const quickHint=quickReason&&!ticket?'<span class="workshop-choice-hint" title="'+escapeHTML(quickReason)+'">Choose edition · '+escapeHTML(quickReason)+'</span>':'';
  const ticketTag=ticket?'<span class="workshop-ticket-note">MO2 · '+escapeHTML(String(ticket.status||'').toUpperCase())+'</span>':'';
  return '<article class="workshop-item" data-workshop-id="'+escapeHTML(m.id)+'"><div class="workshop-item-main"><div class="workshop-line"><span class="workshop-lane lane-'+escapeHTML(m.lane)+'">'+escapeHTML(m.lane.toUpperCase())+'</span><a class="workshop-item-title'+(exactSource?'':' source-unresolved')+'" href="'+escapeHTML(src)+'" target="_blank" rel="noopener noreferrer" data-workshop-act="source" data-id="'+escapeHTML(m.id)+'" title="'+escapeHTML(exactSource?'Open original project website':'Find the original project; source not verified')+'" aria-label="'+escapeHTML((exactSource?'Open source website for ':'Search for source of ')+m.name)+'">'+escapeHTML(m.name)+'<span class="source-open-glyph" aria-hidden="true">↗</span></a>'+(BRIDGE.active?'<button type="button" class="workshop-inline-locate" data-workshop-act="locate" data-id="'+escapeHTML(m.id)+'" aria-label="Reveal '+escapeHTML(m.name)+' in MO2" title="Reveal this mod in Mod Organizer 2">⌖</button>':'')+'</div><small>'+escapeHTML(m.group||'MO2 profile')+' · '+escapeHTML(m.installedVersion||'?')+' → '+escapeHTML(m.latestVersion||'?')+'</small>'+(INSIGHTS?.chips(match)||'')+(SOURCES?.chips(match)||'')+'<div class="workshop-tags">'+(warning?'<span class="workshop-warning">⚠ '+escapeHTML(m.risks.join(' · '))+'</span>':'')+(choice?'<span class="workshop-selected">✓ '+escapeHTML(choice.fileName||choice.displayName||('Reviewed file #'+(choice.fileId||choice.assetId||'')))+'</span><button type="button" class="workshop-remove" data-workshop-act="remove" data-id="'+escapeHTML(m.id)+'" aria-label="Remove reviewed file for '+escapeHTML(m.name)+'">× Remove file</button>':'')+ticketTag+quickHint+'</div></div><div class="workshop-item-actions"><a target="_blank" rel="noopener noreferrer" href="'+escapeHTML(src)+'" title="'+escapeHTML(exactSource?'Open the source website':'Find the original source')+'">'+(exactSource?'↗ Project website':'⌕ Find project')+'</a>'+(hasAlternateRelease?'<a target="_blank" rel="noopener noreferrer" href="'+escapeHTML(release)+'" title="Open the published release page">↗ Latest release</a>':'')+(BRIDGE.active&&compatible&&(INSIGHTS?.isNexus(match)||hasReleaseAssets(m))?ticket?'<button type="button" class="quiet-button" data-workshop-act="downloads" data-id="'+escapeHTML(m.id)+'">View download</button>':!sourceReady?'<button type="button" class="quiet-button" data-source-act="inspect" data-id="'+escapeHTML(m.id)+'">Review source status</button>':'<button type="button" class="button-bright quick-update"'+(sourcePending?' disabled':'')+' data-workshop-act="quick" data-id="'+escapeHTML(m.id)+'">ϟ Update now</button><button type="button" class="quiet-button"'+(sourcePending?' disabled':'')+' data-workshop-act="review" data-id="'+escapeHTML(m.id)+'">Choose file</button>':'')+'</div></article>';
  })};
}
function renderWorkshop(){
 if(WORKSHOP_SEARCH_FRAME!==null){cancelAnimationFrame(WORKSHOP_SEARCH_FRAME);WORKSHOP_SEARCH_FRAME=null;}
 const area=$('workshopResults');if(!area)return;
 const plan=currentWorkshopPlan(),summary=plan.summary,filter=$('workshopFilter').value||'candidate',query=$('workshopSearch').value.trim().toLowerCase();
 const selected=[...WORKSHOP_CHOICES.values()].filter(x=>x.profileId===plan.profileId);
 SET('workshopCount',summary.candidates+' CANDIDATES · '+selected.length+' REVIEWED');
 // Index tickets once: keep constant-time lookups for arbitrarily large profiles.
 const ticketsByMod=new Map();
 for(const ticket of Object.values(WORKSHOP_TICKETS))if(blocksNextUpdate(ticket))ticketsByMod.set(ticket.itemId,ticket);
 const ready=[...ticketsByMod.values()].filter(x=>x.status==='complete').length;
 const metrics=[['Candidates',summary.candidates,'candidate'],['Investigate',summary.review,'review'],['Manual sources',summary.manual,'manual'],['Current',summary.current,'current'],['Compatibility watch',summary.risky,'risky'],['Ready to install',ready,'ready']];
 setHTML($('workshopSummary'),metrics.map(([name,n,key])=>'<button type="button" class="workshop-metric'+(filter===key?' is-active':'')+'" data-lane="'+key+'" aria-pressed="'+(filter===key)+'"><strong>'+escapeHTML(n)+'</strong><small>'+escapeHTML(name)+'</small></button>').join(''));
 const visible=plan.items.filter(m=>(filter==='all'||m.lane===filter||filter==='risky'&&!!m.risks?.length||filter==='ready'&&ticketsByMod.get(m.id)?.status==='complete')&&(!query||[m.name,m.group,m.installedVersion,m.latestVersion,m.verdict,SOURCES?.searchText(ITEM_MAP.get(m.id)||m)||''].join(' ').toLowerCase().includes(query)));
 SET('workshopVisibleCount',visible.length+' of '+plan.items.length+' mods shown');
 const entries=visible.map(m=>workshopEntry(m,ticketsByMod.get(m.id)));
 setRows(area,entries,'<div class="workshop-empty">No mods in this lane. Change the filter or run a deeper audit.</div>');
 $('workshopQueue').disabled=!BRIDGE.active||!LIVE_CONNECTED||!selected.length||WORKSHOP_BUSY||sourceLinksPending();
 $('workshopQuickAll').disabled=!BRIDGE.active||!LIVE_CONNECTED||!summary.candidates||QUICK_RUNNING||sourceLinksPending();
 $('workshopInspectQueue').disabled=!BRIDGE.active||!LIVE_CONNECTED||!selected.length||WORKSHOP_BUSY||sourceLinksPending();
 $('workshopRefresh').disabled=WORKSHOP_BUSY;
 $('workshopExport').disabled=WORKSHOP_BUSY;
 $('workshopReviewNext').disabled=WORKSHOP_BUSY||!LIVE_CONNECTED||!nextWorkshopCandidate();
 renderWorkshopJourney();refreshUnifiedCommand();
}
async function refreshWorkshop(){
 if(!BRIDGE.active){WORKSHOP_PLAN=workshopLocalPlan();renderWorkshop();workshopSetStatus('Standalone checklist ready. No live MO2 installation or download actions are available.');return}
 const profile=LIVE_CATALOG?.profileId,context=profileContext(),request=++WORKSHOP_REQUEST;
 if(!profile)return;
 const p=await bridgeRequest('GET','update-plan');
 if(!contextIsCurrent(context)||request!==WORKSHOP_REQUEST||p.profileId!==profile)return;
 WORKSHOP_PLAN=p;
 const candidates=new Map(p.items.filter(m=>m.lane==='candidate').map(m=>[m.id,m]));
 for(const [id,choice] of WORKSHOP_CHOICES){const m=candidates.get(id);if(choice.profileId!==profile||!m||!(m.installedVersion===choice.installedVersion&&String(m.latestVersion||'')===String(choice.targetVersion||'')))WORKSHOP_CHOICES.delete(id);}
 renderWorkshop();workshopSetStatus('Ready: use Update now for a one-click download and interactive MO2 install, or choose any file manually.');
}
async function exportWorkshop(){
 const context=profileContext();let plan=currentWorkshopPlan();
 if(BRIDGE.active){
  try{plan=await bridgeRequest('GET','update-plan?details=full');if(!contextIsCurrent(context)||plan.profileId!==context.profile)return;}
  catch(ex){workshopSetStatus('Could not export complete source evidence: '+ex.message);return;}
 }
 const data={format:'Modhunter Reviewed Update Plan',version:2,exportedAt:new Date().toISOString(),profileId:plan.profileId,profileName:plan.profileName,game:plan.game,gameRuntime:plan.gameRuntime,summary:plan.summary,items:plan.items,reviewedFiles:[...WORKSHOP_CHOICES.values()].filter(x=>x.profileId===plan.profileId)};
 saveFile('Modhunter-Update-Plan-'+(plan.profileName||'Standalone').replace(/[^\w-]+/g,'-')+'.json','application/json',JSON.stringify(data,null,2));
 workshopSetStatus('Download requested for the complete plan with source evidence, manual/unlinked mods and exact file selections.');
}
async function reviewWorkshopFile(id){
 const m=ITEM_MAP.get(id);if(!m||!BRIDGE.active||(!INSIGHTS?.isNexus(m)&&!hasReleaseAssets(m)))return;
 const plan=currentWorkshopPlan(),record=plan.items.find(x=>x.id===id);
 if(!record||record.lane!=='candidate'||(['github','gitlab','modrinth','thunderstore'].includes(SOURCES?.provider(m))&&!SOURCES.canReview(m))){toast('This mod needs a fresh source check or compatibility review.');return}
 WORKSHOP_REVIEW_ID=id;
 await openFilePicker(m);
 if(FILE_PICKER_MOD!==m||WORKSHOP_REVIEW_ID!==id)return;
 const panel=$('filePickerFiles');
 panel.querySelectorAll('button[data-file-id]').forEach(b=>b.textContent=FAST_PICKER_ID===id?'ϟ Download & install':'✓ Add for download');
 if(record.risks?.length)$('filePickerSummary').textContent+=' Compatibility notes: '+record.risks.join(' · ')+'.';
 $('filePickerSummary').textContent+=' Choose the file you want; no extra confirmation screens.';
}
function closeQueuePreview(){
 $('queueReviewVeil').hidden=true;QUEUE_PENDING=null;
 if(PREVIEW_RETURN_FOCUS?.isConnected)PREVIEW_RETURN_FOCUS.focus({preventScroll:true});
 PREVIEW_RETURN_FOCUS=null;
}
function inspectQueue(){
 if(WORKSHOP_BUSY||!BRIDGE.active||!LIVE_CATALOG)return;
 const plan=currentWorkshopPlan(),profile=plan.profileId;
 const entries=[...WORKSHOP_CHOICES.values()].filter(x=>x.profileId===profile);
 if(!entries.length){workshopSetStatus('Choose and review exact files before queueing.');return;}
 QUEUE_PENDING={profile,entries};PREVIEW_RETURN_FOCUS=document.activeElement;
 const warnings=entries.reduce((n,x)=>n+(plan.items.find(m=>m.id===x.itemId)?.risks?.length?1:0),0);
 SET('queueReviewTitle',entries.length+' approved archive'+(entries.length===1?'':'s'));
 SET('queueReviewSummary',plan.profileName+' · '+(plan.gameRuntime||'Unknown runtime')+' · '+warnings+' with compatibility advisories');
 $('queueReviewRows').innerHTML=entries.map((x,i)=>{
  const m=plan.items.find(row=>row.id===x.itemId)||{};
  return '<article class="queue-review-row"><span class="queue-review-index">'+String(i+1).padStart(2,'0')+'</span><div><strong>'+escapeHTML(m.name||x.itemId)+'</strong><span>'+escapeHTML(x.displayName&&x.displayName!==x.fileName?x.displayName+' · '+x.fileName:x.fileName||('File #'+(x.fileId||x.assetKey||x.assetId)))+'</span><small>'+escapeHTML(x.kind==='source'?providerLabel(x.provider||m.provider):'Nexus Mods')+' · '+escapeHTML(x.installedVersion)+' → '+escapeHTML(x.targetVersion||'?')+(m.risks?.length?' · COMPATIBILITY WATCH':'')+'</small></div></article>';
 }).join('');
 $('queueReviewConfirm').textContent='Queue '+entries.length+' approved download'+(entries.length===1?'':'s');
 $('queueReviewVeil').hidden=false;$('queueReviewConfirm').focus({preventScroll:true});
}
function queueWorkshop(){
 if(WORKSHOP_BUSY||!BRIDGE.active||!LIVE_CATALOG)return;
 const plan=currentWorkshopPlan(),entries=[...WORKSHOP_CHOICES.values()].filter(x=>x.profileId===plan.profileId);
 if(!entries.length){workshopSetStatus('Choose a file, or use Update now for an automatic verified match.');return;}
 // A download does not replace any installed files: dispatch immediately.
 return performQueuedWorkshop(entries,plan.profileId);
}
async function fastUpdateOne(id){
 if(!BRIDGE.active||!LIVE_CONNECTED||QUICK_RUNNING||WORKSHOP_BUSY||!LIVE_CATALOG)return;
 const context=profileContext(),profile=context.profile,operation=++QUICK_OPERATION;
 const active=()=>contextIsCurrent(context)&&operation===QUICK_OPERATION;
 QUICK_RUNNING=true;renderWorkshop();workshopSetStatus('Looking for the exact latest file in MO2…');
 try{
  const result=await bridgeRequest('POST','quick-update',{profileId:profile,itemIds:[id],autoInstall:true});
  if(!active())return;
  await refreshDownloads();if(!active())return;
  if(result.queued?.length){
   for(const queued of result.queued){WORKSHOP_CHOICES.delete(queued.itemId);QUICK_SKIPPED.delete(queued.itemId)}
   workshopSetStatus('Update started. MO2 will open its installer as soon as the download completes; use its standard FOMOD choices.');
   return;
  }
  const reason=(result.skipped||[])[0]?.reason||(result.failed||[])[0]?.error||'Choose an exact file';
  workshopSetStatus(reason);QUICK_SKIPPED.set(id,reason);
  if(!reason.toLowerCase().includes('already in mo2')){FAST_PICKER_ID=id;await reviewWorkshopFile(id);}
 }catch(e){if(active()){workshopSetStatus('Could not start download: '+e.message);FAST_PICKER_ID=id;await reviewWorkshopFile(id)}}
 finally{if(active()){QUICK_RUNNING=false;renderWorkshop()}}
}
async function fastUpdateAll(){
 if(!BRIDGE.active||!LIVE_CONNECTED||QUICK_RUNNING||WORKSHOP_BUSY||!LIVE_CATALOG)return;
 const context=profileContext(),profile=context.profile,operation=++QUICK_OPERATION;
 const active=()=>contextIsCurrent(context)&&operation===QUICK_OPERATION;
 const complete=async(state)=>{
  if(!active())return;
  for(const queued of state.queued||[]){WORKSHOP_CHOICES.delete(queued.itemId);QUICK_SKIPPED.delete(queued.itemId)}
  for(const skipped of state.skipped||[])if(skipped.itemId)QUICK_SKIPPED.set(skipped.itemId,skipped.reason||'Choose an exact file');
  clearTimeout(QUICK_POLL);QUICK_POLL=null;QUICK_RUNNING=false;
  await refreshDownloads();if(!active())return;renderWorkshop();
  const failed=(state.failed||[]).length;
  workshopSetStatus((state.queued||[]).length+' downloads queued through MO2. '+(state.skipped||[]).length+' mods need file choice.'+(failed?' '+failed+' requests failed; review their sources.':'')+' Complete the native MO2 installer when prompted.');
 };
 QUICK_RUNNING=true;renderWorkshop();
 try{
  const result=await bridgeRequest('POST','quick-update',{profileId:profile,all:true});
  if(!active())return;
  if(result.state!=='running'){await complete(result);return;}
  workshopSetStatus('Finding verified matches across your MO2 mods; the app remains responsive…');
  clearTimeout(QUICK_POLL);
  const poll=async()=>{
   if(!active())return;
   try{
    const state=await bridgeRequest('GET','quick-state');if(!active())return;
    if(state.profileId&&state.profileId!==profile){clearTimeout(QUICK_POLL);QUICK_POLL=null;QUICK_RUNNING=false;renderWorkshop();workshopSetStatus('MO2 profile changed. Refresh the active profile before queueing more files.');return;}
    workshopSetStatus((state.completed||0)+' / '+(state.total||'?')+' checked · '+(state.queued||[]).length+' queued · '+(state.skipped||[]).length+' need file choice');
    if(state.state!=='running'){await complete(state)}else{QUICK_POLL=setTimeout(poll,1100)}
   }catch(e){if(active()){clearTimeout(QUICK_POLL);QUICK_POLL=null;QUICK_RUNNING=false;renderWorkshop();workshopSetStatus('MO2 quick queue connection interrupted: '+e.message)}}
  };
  QUICK_POLL=setTimeout(poll,1100);
 }catch(e){if(active())workshopSetStatus('Quick queue: '+e.message)}
 finally{if(active()&&!QUICK_POLL){QUICK_RUNNING=false;renderWorkshop()}}
}

async function performQueuedWorkshop(entries,profile){
 if(WORKSHOP_BUSY||!BRIDGE.active||!LIVE_CONNECTED)return;
 const context=profileContext(),operation=++WORKSHOP_OPERATION,active=()=>contextIsCurrent(context)&&operation===WORKSHOP_OPERATION;
 WORKSHOP_BUSY=true;renderWorkshop();
 let sent=0;const problems=[];
 try{
  const latest=await bridgeRequest('GET','update-plan');
  if(!active()||latest.profileId!==profile||LIVE_CATALOG?.profileId!==profile)throw Error('Active profile changed. Reopen the Workshop.');
  const response=await bridgeRequest('POST','update-queue',{profileId:profile,confirm:'QUEUE_REVIEWED_DOWNLOADS',entries});
  if(!active())return;
  for(const item of response.queued||[]){sent++;WORKSHOP_CHOICES.delete(item.itemId)}
  for(const item of response.failed||[])problems.push(item.itemId+': '+item.error);
  await refreshDownloads();if(!active())return;
  workshopSetStatus('Queued '+sent+' reviewed file(s). '+(problems.length?'Needs attention: '+problems.join(' | '):'Install each completed file using the MO2 installer.'));
 }catch(e){if(active())workshopSetStatus('Queue halted: '+e.message)}
 finally{if(active()){WORKSHOP_BUSY=false;renderWorkshop()}}
}
$('workshopQuickAll').onclick=fastUpdateAll;
$('workshopInspectQueue').onclick=inspectQueue;
$('queueReviewCancel').onclick=closeQueuePreview;
$('queueReviewVeil').addEventListener('click',e=>{if(e.target===$('queueReviewVeil'))closeQueuePreview()});
$('queueReviewConfirm').onclick=()=>{
 if(!QUEUE_PENDING)return;
 const {entries,profile}=QUEUE_PENDING;
 closeQueuePreview();performQueuedWorkshop(entries,profile);
};
$('workshopNextAction').onclick=()=>{
 const action=$('workshopNextAction').dataset.action;
 if(action==='queue')return queueWorkshop();
 if(action==='scan')return scanLive('all');
 if(action==='connect')return unifiedScroll('liveHub');
 if(action==='install'||action==='downloads'){
  const area=$('liveDownloadList');area.scrollIntoView({behavior:'smooth',block:'center'});
  const ready=area.querySelector('button[data-download-id]');
  if(action==='install'&&ready){ready.click();return;}
  (ready||area.querySelector('button[data-retry-item-id]'))?.focus({preventScroll:true});return;
 }
 if(action==='quick')return fastUpdateAll();
 if(action==='review')return reviewNextWorkshopCandidate();
};
function nextWorkshopCandidate(){
 if(!BRIDGE.active||!LIVE_CATALOG)return null;
 const plan=currentWorkshopPlan(),busyIds=new Set(Object.values(WORKSHOP_TICKETS)
   .filter(blocksNextUpdate).map(t=>t.itemId));
 return plan.items.find(row=>row.lane==='candidate'&&!WORKSHOP_CHOICES.has(row.id)
   &&!busyIds.has(row.id)&&(INSIGHTS?.isNexus(ITEM_MAP.get(row.id)||row)||hasReleaseAssets(row)))||null;
}
function reviewNextWorkshopCandidate(){
 const row=nextWorkshopCandidate();
 if(!row){workshopSetStatus('All available update candidates have been reviewed or queued. Check other lanes for manual sources.');return;}
 $('workshopFilter').value='candidate';$('workshopSearch').value='';renderWorkshop();
 const target=[...$('workshopResults').querySelectorAll('[data-workshop-id]')].find(el=>el.dataset.workshopId===row.id);
 target?.scrollIntoView({behavior:'smooth',block:'center'});
 reviewWorkshopFile(row.id).catch(ex=>workshopSetStatus('Review failed: '+ex.message));
}
$('workshopReviewNext').onclick=reviewNextWorkshopCandidate;
$('workshopRefreshDownloads').onclick=async()=>{await refreshDownloads();workshopSetStatus('MO2 download tickets refreshed.');};
$('workshopRefresh').onclick=()=>refreshWorkshop().catch(e=>workshopSetStatus('Plan failed: '+e.message));
$('workshopExport').onclick=exportWorkshop;
$('workshopQueue').onclick=queueWorkshop;
$('workshopFilter').onchange=renderWorkshop;
$('workshopSummary').addEventListener('click',e=>{const choice=e.target.closest('button[data-lane]');if(!choice)return;$('workshopFilter').value=choice.dataset.lane;renderWorkshop();});
$('workshopSearch').oninput=()=>{if(DATA.items.length<1000){renderWorkshop();return;}if(WORKSHOP_SEARCH_FRAME===null)WORKSHOP_SEARCH_FRAME=requestAnimationFrame(()=>{WORKSHOP_SEARCH_FRAME=null;renderWorkshop()});};
$('workshopResults').addEventListener('click',e=>{
 const b=e.target.closest('[data-workshop-act]');if(!b)return;
 if(b.dataset.workshopAct==='source'){
  const m=ITEM_MAP.get(b.dataset.id);
  if(m){STORE.rows[m.id]={...getRow(m.id),lastOpened:new Date().toISOString()};save(false);}
  return; // Native anchor behavior: ctrl-click, middle-click and keyboard Enter keep working.
 }
 if(b.dataset.workshopAct==='locate'){
  const m=ITEM_MAP.get(b.dataset.id);if(m)locateInMO2(m,b);
  return;
 }
 if(b.dataset.workshopAct==='downloads'){
  const ticket=[...$('liveDownloadList').querySelectorAll('[data-ticket-item-id]')].find(x=>x.dataset.ticketItemId===b.dataset.id);
  (ticket||$('liveDownloadList')).scrollIntoView({behavior:'smooth',block:'center'});return;
 }
 if(b.dataset.workshopAct==='remove'){
  const existing=WORKSHOP_CHOICES.get(b.dataset.id);
  if(existing){WORKSHOP_CHOICES.delete(b.dataset.id);renderWorkshop();workshopSetStatus('Removed reviewed file from queue. No download was started.');}
  return;
 }
 if(b.dataset.workshopAct==='quick')fastUpdateOne(b.dataset.id);
 if(b.dataset.workshopAct==='review'){FAST_PICKER_ID=null;reviewWorkshopFile(b.dataset.id).catch(ex=>workshopSetStatus('Review failed: '+ex.message));}
});

function renderLiveStats(){if(!BRIDGE.active||!LIVE_CATALOG)return;const items=DATA.items;const found=items.filter(isUpdate).length;const reviewed=items.filter(m=>m.update?.verdict==='review').length;SET('liveUpdateCount',found+' update candidates');SET('liveCached',reviewed+' need version review · not installation approval');}
let PAINTED_NATIVE='',PAINTED_TARGET='';
function paintNativeSelection(){const target=focusedRowId||NATIVE_SELECTED;for(const id of new Set([PAINTED_NATIVE,PAINTED_TARGET,NATIVE_SELECTED,target])){const row=id&&$('row-'+id);if(row){row.classList.toggle('native-selected',BRIDGE.active&&id===NATIVE_SELECTED);row.classList.toggle('aether-target',Boolean(target&&id===target))}}PAINTED_NATIVE=NATIVE_SELECTED;PAINTED_TARGET=target}
async function locateInMO2(m,el){if(!BRIDGE.active||!LIVE_CATALOG){toast('Open from MO2 to reveal a mod.');return}const profile=LIVE_CATALOG.profileId;el.disabled=true;
 try{const r=await bridgeRequest('POST','focus',{id:m.id,profileId:profile});if(LIVE_CATALOG?.profileId!==profile)return;NATIVE_SELECTED=m.id;paintNativeSelection();window.AetherFX?.reveal($('row-'+m.id));toast('⌖ '+(r.message||'MO2 jumped to '+m.name));}
 catch(ex){toast('MO2 navigation: '+ex.message)}finally{el.disabled=false}}
function questRank(score){if(score>=20000)return'LEGENDARY DRAGONBORN';if(score>=9000)return'ARCHMAGE OF THE FORGE';if(score>=3500)return'EBONY SENTINEL';if(score>=1000)return'SKYFORGE KNIGHT';if(score>=250)return'IRON VANGUARD';return'NOVICE ADVENTURER'}
function renderQuest(){if(!$('gameBoard'))return;const c=counts(),isLive=Boolean(BRIDGE.active&&LIVE_CATALOG),score=isLive?Number(GAME?.score||0):c.done*10,updates=isLive?Number(GAME?.updates||0):c.done;
 SET('questLevel',isLive?(GAME?.level||1):(1+Math.floor(c.done/15)));SET('questRank',questRank(score));SET('questScore',score.toLocaleString()+' XP');SET('questUpdates',isLive?`${updates} verified MO2 updates`:`${updates} practice checklist clears`);SET('questRankHint',isLive?'NATIVE MO2 VERIFICATION · PERSISTENT':'PRACTICE ONLY · NO VERIFIED SCORE');SET('questCombo',isLive?(GAME?.bestCombo||0)+' ×':'0 ×');SET('questCrowns',isLive?(GAME?.victories||0)+' ♛':'0 ♛');
 const badges=isLive?(GAME?.achievements||BADGE_META.map(([id,name,glyph,target])=>({id,name,glyph,target,unlocked:false}))):BADGE_META.map(([id,name,glyph,target])=>({id,name,glyph,target,unlocked:false}));
 $('achievementGrid').innerHTML=badges.map(b=>`<div class="achievement ${b.unlocked?'unlocked':'locked'}" title="${escapeHTML(b.description||((b.target||0)+' verified updates'))}"><span class="achievement-glyph">${escapeHTML(b.glyph)}</span><div><strong>${escapeHTML(b.name)}</strong><small>${b.unlocked?'UNLOCKED':b.target===null?'Defeat the World-Eater':b.target+' verified updates'}</small></div><i>${b.unlocked?'✦':'🔒'}</i></div>`).join('');SET('achievementCount',badges.filter(b=>b.unlocked).length+' / '+badges.length);
 const feed=isLive?(GAME?.feed||[]):[];$('questFeed').innerHTML=feed.length?feed.slice().reverse().map(ev=>`<div class="quest-feed-line"><span>⚔ ${escapeHTML(ev.name)}</span><strong>+${ev.xp} XP</strong><small>${escapeHTML(ev.evidence==='mo2-install-complete'?'MO2 installed':'Version verified')}</small></div>`).join(''):'<span class="quest-empty">Verified update events appear here.</span>';
 const ranks=isLive?(GAME?.leaderboard||[]):[];$('questLeaderboard').innerHTML=ranks.length?ranks.map((g,i)=>`<div class="quest-leaderboard-line"><span>${i===0?'♛':'#'+(i+1)} ${escapeHTML(g.name)}</span><strong>${Number(g.score).toLocaleString()}</strong><small>${g.victories} crowns</small></div>`).join(''):'<span class="quest-empty">No ranked expeditions yet.</span>';
 const linked=DATA.items.filter(x=>Number(x.nexusId||0)>0),foes=linked.filter(x=>x.update?.verdict==='candidate').length,needs=linked.filter(x=>!['current','newer-installed'].includes(x.update?.verdict)).length,pct=isLive&&LIVE_SCAN?.state==='completed'&&LIVE_SCAN?.mode==='deep'&&linked.length?Math.max(0,Math.round(100*(linked.length-needs)/linked.length)):0;
 $('raidBossFill').style.width=(isLive?pct:c.percent)+'%';SET('raidStatus',isLive?(LIVE_SCAN?.mode==='deep'&&LIVE_SCAN?.state==='completed'?(needs?`${needs} unverified or outdated. Clear the update queue and rerun the full audit.`:'All tracked Nexus mods verified by a full sweep.'): `Complete updates in MO2, then run a deep Nexus audit to challenge the World-Eater.`):`Your offline practice campaign: ${c.done} of ${c.total} quests checked.`);
 SET('raidNumbers',isLive?`${foes} UPDATE CANDIDATES · ${needs} REQUIRE REVIEW · ${linked.length} NEXUS-LINKED`:`${c.done} / ${c.total} PRACTICE CLEAR`);$('raidAction').textContent=isLive?'⚔ Verify the full profile':'✧ Practice finale';}
async function refreshGame(){
 if(!BRIDGE.active||!LIVE_CATALOG)return;
 const context=profileContext(),request=++GAME_REQUEST;
 try{const r=await bridgeRequest('GET','game?compact=1');if(!contextIsCurrent(context)||request!==GAME_REQUEST||r.profileId!==context.profile)return;
  const signature=JSON.stringify(r);if(signature===GAME_SIGNATURE)return;GAME_SIGNATURE=signature;
  const last=GAME_SEEN;GAME=r;const sigils=r.realmWallet?.available;const badge=$('realmBalanceBadge');if(badge)badge.textContent='✧ '+(Number.isFinite(sigils)?sigils:0);
  const latest=(r.feed||[]).at(-1);if(last>=0&&latest&&Number(latest.sequence)>last){toast(`⚔ ${latest.name} +${latest.xp} verified XP`);window.AetherFX?.verified(null);spawnFinisher({x:Math.max(180,innerWidth*.55),y:Math.min(innerHeight*.4,320)})}
  GAME_SEEN=Math.max(last,Number(latest?.sequence||0));renderQuest();if(r.unseenVictory&&!VICTORY_ACTIVE)launchVictory(r.unseenVictory,false);
 }catch(ex){if(contextIsCurrent(context))console.warn('Chronicle:',ex.message)}
}
async function initLiveBridge(){
 if(LIVE_INIT_PROMISE)return LIVE_INIT_PROMISE;
 LIVE_INIT_PROMISE=(async()=>{try{
  const res=await requestLiveCatalog();if(!res)return false;
  const context=profileContext();
  const results=await Promise.allSettled([res.ready,bridgeRequest('GET','scan'),refreshDownloads()]);
  if(!contextIsCurrent(context))return false;
  const scan=results[1];if(scan.status==='fulfilled'&&scan.value.scan&&(!scan.value.profileId||scan.value.profileId===context.profile)){updateScanUI(scan.value.scan);if(scan.value.scan.state==='running')startScanPolling(scan.value.scan.mode||'all',context)}
  await ensureStartupCheck();return true;
 }catch(ex){setBridgeConnection(false,'Could not reach MO2: '+ex.message);return false}})();
 try{return await LIVE_INIT_PROMISE}finally{LIVE_INIT_PROMISE=null}
}
function updateScanUI(state){if(!state)return;LIVE_SCAN=state;const status=state.state||'idle',progress=state.checked||0,total=state.total||0;SET('liveScanProgress',status==='running'?`Scanning ${progress}/${total||'?'}…`:status==='completed'?'✓ Scan complete':status==='partial'?'⚠ Partial results':status==='error'?'⚠ Scan failed':'Awaiting action');if(status==='error')toast('Scan: '+(state.error||'error'));if(status==='completed'||status==='partial'){SET('liveSync',state.mode+' · '+(state.queried||0)+' queried · '+(state.fromCache||0)+' cached');renderLiveStats();renderQuest()}for(const id of ['liveQuick','liveDeep','liveLocal','liveAll'])$(id).disabled=!LIVE_CONNECTED||SCAN_START_BUSY||status==='running';renderWorkshopJourney();renderWorkspace();renderList()}
function startScanPolling(mode,context=profileContext()){
 clearTimeout(SCAN_TIMER);const operation=++SCAN_OPERATION;
 const active=()=>operation===SCAN_OPERATION&&contextIsCurrent(context);
 const finish=()=>{if(!active())return;SCAN_TIMER=null;SCAN_START_BUSY=false;for(const id of ['liveQuick','liveDeep','liveLocal','liveAll'])$(id).disabled=!LIVE_CONNECTED;};
 const poll=async()=>{
  if(!active())return;
  try{const v=await bridgeRequest('GET','scan');if(!active())return;if(v.profileId&&v.profileId!==context.profile){finish();return;}
   updateScanUI(v.scan);
   if(v.scan.state==='completed'||v.scan.state==='partial'){
    finish();const snap=await requestLiveCatalog();if(!snap||!active())return;
    await snap.ready;if(active())toast('ϟ '+mode+' sweep complete!');
   }else if(v.scan.state==='running'){SCAN_TIMER=setTimeout(poll,1200)}else{finish()}
  }catch(ex){if(active()){finish();LIVE_SCAN={...LIVE_SCAN,state:'unknown'};SET('liveScanProgress','Scan status unavailable · retry to reconnect');toast('Scan interrupted: '+ex.message)}}
 };
 SCAN_TIMER=setTimeout(poll,1200);
}
async function scanLive(mode){
 if(!BRIDGE.active||!LIVE_CATALOG||!LIVE_CONNECTED||SCAN_START_BUSY||LIVE_SCAN?.state==='running')return;
 const context=profileContext();SCAN_START_BUSY=true;
 for(const id of ['liveQuick','liveDeep','liveLocal','liveAll'])$(id).disabled=true;
 try{const r=await bridgeRequest('POST','scan',{mode,profileId:context.profile});if(!contextIsCurrent(context))return;
  if(r.state==='running'){SCAN_START_BUSY=false;updateScanUI({...r,mode,state:'running'});SET('liveScanProgress','Beginning '+mode+' scan…');startScanPolling(mode,context)}
  else{SCAN_START_BUSY=false;updateScanUI(r.scan||r);}
 }catch(ex){if(contextIsCurrent(context)){SCAN_START_BUSY=false;toast(ex.message);for(const id of ['liveQuick','liveDeep','liveLocal','liveAll'])$(id).disabled=!LIVE_CONNECTED}}
}
async function pollLiveBridge(){
 clearTimeout(LIVE_POLL_TIMER);LIVE_POLL_TIMER=null;
 if(LIVE_REFRESH_BUSY)return;
 if(document.hidden){LIVE_POLL_TIMER=setTimeout(pollLiveBridge,4200);return;}
 LIVE_REFRESH_BUSY=true;
 try{
  if(!LIVE_CATALOG||!LIVE_CONNECTED){await initLiveBridge();return;}
  const context=profileContext(),revision=await bridgeRequest('GET','revision');
  if(!contextIsCurrent(context))return;
  if(revision.generation!==LIVE_GENERATION||revision.profileId!==LIVE_CATALOG.profileId){const snap=await requestLiveCatalog();if(!snap)return;SET('liveSync','Profile synchronized');}
  if(!contextIsCurrent(context))return;
  if(revision.profileId===LIVE_CATALOG?.profileId&&NATIVE_SELECTED!==revision.selectedModId){NATIVE_SELECTED=revision.selectedModId||'';paintNativeSelection();if(NATIVE_SELECTED)window.AetherFX?.select($('row-'+NATIVE_SELECTED),{source:'mo2'})}
  // Each endpoint coalesces concurrent readers. The next poll is scheduled only
  // after this turn completes, so a slow MO2 callback cannot pile up requests.
  if(!PROGRESS_READY)await hydrateProgress(LIVE_CATALOG.profileId);
  await ensureStartupCheck();await refreshGame();await refreshDownloads();
 }catch(ex){setBridgeConnection(false,'MO2 connection interrupted: '+ex.message)}
 finally{LIVE_REFRESH_BUSY=false;LIVE_POLL_TIMER=setTimeout(pollLiveBridge,4200)}
}

function activityStatus(record){
 if(record.needsReview)return ['Previous session · review in MO2','needs-review'];
 if(record.status==='installed')return record.verifiedUpdate?['Version verified','verified']:['Installed · version unverified','review'];
 const labels={downloading:'Downloading in MO2',paused:'Paused in MO2',complete:'Ready in current MO2 session',installing:'Interactive installer open',failed:'Download failed',removed:'Removed from MO2',attention:'Manual attention'};
 return [labels[record.status]||String(record.status||'Unknown'),record.status||'unknown'];
}
function renderActivity(){
 const panel=$('activityResults');if(!panel)return;
 const query=$('activitySearch').value.trim().toLowerCase(),filter=$('activityFilter').value||'all';
 const list=ACTIVITY_RECORDS.filter(rec=>{
  if(filter==='review'&&!rec.needsReview&&rec.status!=='failed'&&!(rec.status==='installed'&&!rec.verifiedUpdate))return false;
  if(filter==='verified'&&!(rec.status==='installed'&&rec.verifiedUpdate))return false;
  if(filter==='active'&&!rec.sessionActive)return false;
  return !query||[rec.modName,rec.itemId,rec.fileName,rec.provider,rec.installedVersion,rec.targetVersion,rec.status].join(' ').toLowerCase().includes(query);
 });
 const pending=ACTIVITY_RECORDS.filter(x=>x.needsReview).length;
 const verified=ACTIVITY_RECORDS.filter(x=>x.status==='installed'&&x.verifiedUpdate).length;
 SET('activityStats',ACTIVITY_RECORDS.length+' operations · '+verified+' verified updates · '+pending+' previous-session reviews');
 SET('activityCount',list.length+' / '+ACTIVITY_RECORDS.length+' visible');
 SET('activityWarning',!BRIDGE.active?'Connect through MO2 to view your real, profile-scoped update history. Offline mode cannot read or modify installations.':(ACTIVITY_WARNING||'This is read-only history. Previous MO2 sessions cannot automatically resume downloads or installations.'));
 panel.innerHTML=list.map(rec=>{
  const state=activityStatus(rec),date=new Date(rec.lastAt),dateText=Number.isNaN(date.valueOf())?'Time unknown':date.toLocaleString();
  const meta=[rec.installedVersion||'?',rec.targetVersion||'?'].join(' → ');
  const headline=rec.modName||rec.itemId||rec.fileName||'Unlinked MO2 download';
  const original=ITEM_MAP.get(rec.itemId)||{id:rec.itemId||rec.key||'',name:headline,nexusId:rec.modId||0,group:'',source:{}};
  const source=getSrc(original);
  const sourceTitle=source.verified?'Open original mod project in a new tab':'Find the original mod project';
  const timeline=(rec.timeline||[]).map(entry=>{
   const at=new Date(entry.at),time=Number.isNaN(at.valueOf())?'Unknown time':at.toLocaleString();
   return '<li><span>'+escapeHTML(entry.status||'')+'</span><small>'+escapeHTML(time)+'</small>'+(entry.note?'<small>'+escapeHTML(entry.note)+'</small>':'')+'</li>';
  }).join('');
  return '<details class="activity-entry activity-'+escapeHTML(state[1])+'"><summary><span class="activity-dot" aria-hidden="true"></span><span class="activity-head"><a class="activity-mod-link'+(source.verified?'':' source-unresolved')+'" href="'+escapeHTML(source.url)+'" target="_blank" rel="noopener noreferrer" title="'+escapeHTML(sourceTitle)+'" aria-label="'+escapeHTML(sourceTitle+' for '+headline)+'">'+escapeHTML(headline)+'<span class="source-open-glyph" aria-hidden="true">↗</span></a><small>'+escapeHTML(meta)+' · '+escapeHTML(rec.provider||'MO2')+' · '+escapeHTML(rec.fileName||'Archive unknown')+'</small></span><span class="activity-state"><b>'+escapeHTML(state[0])+'</b><small>'+escapeHTML(dateText)+'</small></span></summary><div class="activity-detail"><div><strong>Source:</strong> '+escapeHTML(rec.provider||'Unknown')+' · <strong>Download:</strong> #'+escapeHTML(rec.downloadId)+'</div><div><strong>Runtime:</strong> '+escapeHTML(rec.gameRuntime||'Unknown')+' · <strong>Profile backup:</strong> '+(rec.hasProfileBackup?'Profile text files recorded':'Not recorded')+'</div>'+(rec.needsReview?'<p class="activity-safety">Previous session only. Open MO2 Downloads, verify the archive exists and rescan before taking further action. No automatic resume is available.</p>':'')+'<ol class="activity-timeline">'+timeline+'</ol></div></details>';
 }).join('')||'<div class="workshop-empty">No operations match this view. Queue an update from the MO2 plugin to begin recording real update activity.</div>';
 $('activityExport').disabled=!BRIDGE.active||!LIVE_CATALOG||!ACTIVITY_RECORDS.length;
 $('activityRefresh').disabled=!BRIDGE.active;
}
async function refreshActivity(){
 if(!BRIDGE.active||!LIVE_CATALOG)return;
 const profile=LIVE_CATALOG.profileId,generation=++ACTIVITY_GENERATION;
 try{
  const result=await bridgeRequest('GET','activity');
  if(generation!==ACTIVITY_GENERATION||LIVE_CATALOG?.profileId!==profile||result.profileId!==profile)return;
  ACTIVITY_RECORDS=Array.isArray(result.records)?result.records:[];
  ACTIVITY_WARNING=result.durable===false?(result.warning||'The operation journal could not be saved. Check MO2 plugin storage permissions.'):'';
  renderActivity();
 }catch(ex){
  if(generation===ACTIVITY_GENERATION&&LIVE_CATALOG?.profileId===profile){
   ACTIVITY_WARNING='Could not read updater history: '+ex.message;renderActivity();
  }
 }
}
function exportActivity(){
 if(!BRIDGE.active||!LIVE_CATALOG)return;
 const profile=LIVE_CATALOG.profileId;
 const data={format:'Modhunter MO2 Operation History',version:1,exportedAt:new Date().toISOString(),
   profileId:profile,profileName:LIVE_CATALOG.profileName,
   important:'This is read-only history. It cannot restore overwritten mod files or resume previous-session downloads.',
   operations:ACTIVITY_RECORDS};
 const title=String(LIVE_CATALOG.profileName||'MO2').replace(/[^a-z0-9_-]+/gi,'-');
 saveFile('Modhunter-Operations-'+title+'.json','application/json',JSON.stringify(data,null,2));
 workshopSetStatus('Download requested for '+ACTIVITY_RECORDS.length+' profile-scoped MO2 operation receipts.');
}
$('activityResults').addEventListener('click',e=>{if(e.target.closest('a.activity-mod-link'))e.stopPropagation();});
$('activityRefresh').onclick=()=>refreshActivity();
$('activityExport').onclick=exportActivity;
$('activitySearch').oninput=renderActivity;
$('activityFilter').onchange=renderActivity;

function renderDownloadPulse(downloads){
 const node=$('downloadPulse');if(!node)return;
 const values=Object.values(downloads||{});
 const ready=values.filter(t=>t.status==='complete').length;
 const active=values.filter(t=>['downloading','paused','installing'].includes(t.status)).length;
 const failed=values.filter(t=>t.status==='failed'&&!t.retriedBy).length;
 const verified=values.filter(t=>t.status==='installed'&&t.verifiedUpdate).length;
 node.hidden=!BRIDGE.active||!values.length;
 SET('pulseReady',ready);SET('pulseMoving',active);SET('pulseFailed',failed);SET('pulseVerified',verified);
 $('installAllReady').disabled=!ready||!LIVE_CATALOG||!LIVE_CONNECTED;
 $('pulseReviewFailures').disabled=!failed;
 $('downloadPulseHint').textContent=ready?'Install ready archives in MO2, one after another, without extra Modhunter prompts.':failed?'Retry failed archives using their already reviewed file, without choosing it again.':active?'MO2 is handling your downloads and installation dialogs.':'Every visible ticket is resolved or needs a version verification.';
}
$('installAllReady').onclick=async()=>{
 if(!BRIDGE.active||!LIVE_CONNECTED||!LIVE_CATALOG)return;
 const button=$('installAllReady');button.disabled=true;
 try{
  const r=await bridgeRequest('POST','install-ready',{});
  toast(r.message||'Ready installers are opening sequentially in MO2.');
  if(r.blocked?.length)workshopSetStatus(r.blocked.length+' archive(s) need a fresh review. Other ready installers can continue.');
  await refreshDownloads();
 }catch(ex){toast('Install ready: '+ex.message)}
 finally{button.disabled=false;renderDownloadPulse(WORKSHOP_TICKETS)}
};
$('pulseReviewFailures').onclick=()=>{
 const failed=[...$('liveDownloadList').querySelectorAll('.download-ticket.ticket-failed')].find(x=>x.querySelector('[data-retry-download-id]'));
 (failed||$('liveDownloadList')).scrollIntoView({behavior:'smooth',block:'center'});
 failed?.querySelector('[data-retry-download-id]')?.focus({preventScroll:true});
};

async function refreshDownloads(){
 if(!BRIDGE.active||!LIVE_CATALOG)return;
 if(DOWNLOAD_REFRESH_BUSY){DOWNLOAD_REFRESH_AGAIN=true;return;}
 DOWNLOAD_REFRESH_BUSY=true;
 try{
  const context=profileContext(),profile=context.profile,r=await bridgeRequest('GET','downloads');
  if(!contextIsCurrent(context)||(r.profileId&&r.profileId!==profile))return;
  const next=r.downloads||{},signature=JSON.stringify(next);
  if(signature===WORKSHOP_TICKET_SIGNATURE)return; // Preserve buttons/focus when no state changed.
  WORKSHOP_TICKET_SIGNATURE=signature;WORKSHOP_TICKETS=next;
  const box=$('liveDownloadList'),order={complete:0,installing:1,downloading:2,paused:3,failed:4,removed:5,installed:6};
  const tickets=Object.entries(next).sort(([a,x],[b,y])=>(order[x.status]??8)-(order[y.status]??8)||Number(b)-Number(a));
  renderDownloadPulse(next);
  const entries=tickets.map(([id,d])=>{
   const mod=ITEM_MAP.get(d.itemId),name=mod?.name||d.name||(d.provider==='nexus'?'Nexus mod #'+d.modId:'Reviewed source archive');
   const state=String(d.status||'unknown'),safeState=state.replace(/[^a-z-]/g,'');
   const title=state==='complete'?'Ready to install':state==='installing'?'MO2 installer open':state==='installed'?(d.verifiedUpdate?'Update confirmed':'Installed · needs verification'):state==='downloading'?'Downloading':state==='paused'?'Paused in MO2':state==='failed'?'Download failed':state==='removed'?'Removed from MO2':state;
   const detail=state==='complete'?'Profile snapshot and interactive FOMOD/overwrite review before installing':state==='installing'?'Finish the installer prompts inside MO2':state==='installed'?(d.verifiedUpdate?'Version change confirmed in MO2':'Rescan and verify the installed version and overwrite order'):state==='paused'?'Resume in the MO2 Downloads pane':state==='failed'?'Review an exact file to try again':state==='downloading'?'MO2 is handling this archive':'Check MO2 Downloads';
   const buttons=state==='complete'?'<button class="button-bright" data-download-id="'+escapeHTML(id)+'">ϟ Install now</button><button class="quiet-button" data-download-details="'+escapeHTML(id)+'">Details</button>':state==='failed'&&Number.isInteger(d.retriedBy)&&d.retriedBy>=0?'<span class="ticket-requeued">Requeued as #'+escapeHTML(d.retriedBy)+'</span>':state==='failed'&&mod?'<button class="button-bright" data-retry-download-id="'+escapeHTML(id)+'">↻ Retry now</button><button class="quiet-button" data-retry-item-id="'+escapeHTML(d.itemId)+'">Choose another file</button>':'';
   return {key:'ticket:'+id,html:'<article data-ticket-id="'+escapeHTML(id)+'" class="download-ticket ticket-'+escapeHTML(safeState)+'" data-ticket-item-id="'+escapeHTML(d.itemId||'')+'"><i class="ticket-status-dot" aria-hidden="true"></i><div class="ticket-copy"><strong>'+escapeHTML(name)+'</strong><span>'+escapeHTML(title)+' · '+(providerLabel(d.provider||'nexus'))+' · #'+escapeHTML(id)+'</span><small>'+escapeHTML(d.lastError||detail)+'</small></div>'+buttons+'</article>'};
  });
  if(tickets.length)entries.unshift({key:'heading',html:'<div class="workshop-downloads-heading"><div><strong>MO2 Downloads & Installations</strong><small>Real MO2 download manager · every installation is interactive</small></div><span>'+tickets.length+' ticket'+(tickets.length===1?'':'s')+'</span></div>'});
  setRows(box,entries,'',true);
  renderWorkshop();
  await refreshActivity();
 }catch(ex){SET('liveSync','Download status unavailable: '+ex.message)}
 finally{
  DOWNLOAD_REFRESH_BUSY=false;
  if(DOWNLOAD_REFRESH_AGAIN){DOWNLOAD_REFRESH_AGAIN=false;void refreshDownloads();}
 }
}
function filterFilePicker(){
 const query=$('filePickerSearch').value.trim().toLowerCase();
 let visible=0,total=0;
 $('filePickerFiles').querySelectorAll('.picker-file').forEach(row=>{
  const haystack=row.textContent.toLowerCase();
  row.hidden=Boolean(query&&!haystack.includes(query));
  total++;if(!row.hidden)visible++;
 });
 SET('filePickerMatchCount',visible+' / '+total+' files');
}
$('filePickerSearch').oninput=filterFilePicker;
async function openFilePicker(m){
 const nativeNexus=Boolean(INSIGHTS?.isNexus(m)&&(!SOURCES||SOURCES.provider(m)==='nexus'));
 if(!BRIDGE.active||(!nativeNexus&&!hasReleaseAssets(m))){toast('No verified download assets for this source.');return}
 const context=profileContext();FILE_PICKER_MOD=m;
 $('filePickerSearch').value='';$('filePickerMatchCount').textContent='Loading…';
 $('filePickerTitle').textContent=m.name;
 $('filePickerSummary').textContent='Installed: '+m.installedVersion+' · Target runtime: '+(DATA.gameRuntime||'unknown')+'. Inspect file version, requirements and FOMOD instructions.';
 $('filePickerVeil').hidden=false;$('filePickerFiles').innerHTML='<div class="picker-loading">Loading exact file choices…</div>';
 if(m.update?.detailsAvailable||(!m.update?.releaseAssets?.length&&hasReleaseAssets(m))){
  await loadSourceDetails(m);
  if(FILE_PICKER_MOD!==m||!contextIsCurrent(context)||ITEM_MAP.get(m.id)!==m)return;
 }
 const detailed=sourceDetailMod(m),assets=Array.isArray(detailed.update?.releaseAssets)?detailed.update.releaseAssets:[];
 const releaseFiles=assets.length?'<div class="picker-source-title">'+escapeHTML(providerLabel(SOURCES?.provider(detailed)||detailed.update?.provider))+' · PUBLISHED RELEASE FILES</div>'+(SOURCES?.assetCards(detailed)||''):'';
 if(!nativeNexus){
  $('filePickerFiles').innerHTML=releaseFiles||'<div class="picker-error">'+escapeHTML(SOURCE_DETAILS.get(m)?.error||'Release files are unavailable. Refresh the source check and inspect the original release.')+'</div>';
  filterFilePicker();return;
 }
 try{
  const j=await bridgeRequest('GET','files?modId='+m.nexusId+'&itemId='+encodeURIComponent(m.id));
  if(!j.ok)throw Error(j.error||'Nexus file review unavailable');
  if(FILE_PICKER_MOD!==m||!contextIsCurrent(context))return;
  const nexus=(j.files||[]).map(f=>{
   const tag=f.suggested?'◈ VERSION-MATCHED MAIN FILE':f.releaseVersionMatch?'✓ PROJECT VERSION MATCH':f.category===1?'MAIN FILE':'OPTIONAL / OTHER';
   return '<div class="picker-file'+(f.suggested?' picker-suggested':'')+'"><div class="picker-file-main"><span class="picker-match-tag">'+escapeHTML(tag)+'</span><strong>'+escapeHTML(f.name)+'</strong><span>v'+escapeHTML(f.version||'?')+' · '+escapeHTML(f.fileName||'')+' · '+escapeHTML(f.sizeInKB||'?')+' KB '+(f.category===1?' · MAIN FILE':'')+'</span>'+(f.reviewHint?'<p class="picker-hint">'+escapeHTML(f.reviewHint)+'</p>':'')+(f.description?'<p class="picker-file-desc">'+escapeHTML(f.description)+'</p>':'')+'</div><button class="button-bright" data-file-id="'+Number(f.fileId)+'" data-file-version="'+escapeHTML(f.version||'')+'" data-mod-id="'+Number(m.nexusId)+'" data-archive-name="'+escapeHTML(f.fileName||f.name)+'">Select Nexus file</button></div>';
  }).join('');
  if(!nexus&&!releaseFiles)throw Error('No files available for this project');
  $('filePickerFiles').innerHTML=(nexus?'<div class="picker-source-title">NEXUS MODS · EXACT FILES</div>'+nexus:'')+releaseFiles;
  if(j.reviewAdvice?.hasUniqueMainMatch)$('filePickerSummary').textContent='One main Nexus file matches the published project version. Verify game/runtime, required files, and FOMOD options.';
  else if(j.reviewAdvice?.matchingMainFiles>1)$('filePickerSummary').textContent='Several main files share the published version. Compare the variants carefully for your runtime and mod configuration.';
  filterFilePicker();
 }catch(ex){
  if(FILE_PICKER_MOD!==m||!contextIsCurrent(context))return;
  $('filePickerFiles').innerHTML='<div class="picker-error">'+escapeHTML(ex.message)+'<p>Use the original Nexus Files page and Mod Manager Download if required by your account.</p></div>'+releaseFiles;
  filterFilePicker();
 }
}
function closeFilePicker(){$('filePickerVeil').hidden=true;FILE_PICKER_MOD=null;WORKSHOP_REVIEW_ID=null;FAST_PICKER_ID=null}
$('filePickerClose').onclick=closeFilePicker;$('filePickerCancel').onclick=closeFilePicker;$('filePickerVeil').addEventListener('click',e=>{if(e.target===$('filePickerVeil'))closeFilePicker()});
$('filePickerNexus').onclick=()=>{if(FILE_PICKER_MOD)window.open(getSrc(FILE_PICKER_MOD).url,'_blank','noopener,noreferrer')};
$('filePickerFiles').addEventListener('click',async e=>{const b=e.target.closest('button[data-file-id]');if(!b)return;const m=FILE_PICKER_MOD;if(!m)return;
 if(WORKSHOP_REVIEW_ID){
  const plan=currentWorkshopPlan(),record=plan.items.find(x=>x.id===WORKSHOP_REVIEW_ID);
  if(!record||record.lane!=='candidate'){closeFilePicker();toast('Candidate is no longer current.');return}
  const picked=b.closest('.picker-file'),label=picked?.querySelector('strong')?.textContent||'Selected file';
  // Exact archive selection is the user action; compatibility hints stay visible.
  const riskAcknowledged=true;
  const sourceChoice=b.dataset.sourceKind==='release';
  const exactFile={fileName:b.dataset.archiveName||label,displayName:label,fileVersion:b.dataset.fileVersion||''};
  const fast=FAST_PICKER_ID===WORKSHOP_REVIEW_ID;
  const selection=sourceChoice?{kind:'source',itemId:WORKSHOP_REVIEW_ID,assetKey:b.dataset.assetKey||'',assetProof:b.dataset.assetProof||'',...(Number(b.dataset.fileId)>0?{assetId:Number(b.dataset.fileId)}:{}),provider:b.dataset.sourceProvider||record.provider,installedVersion:record.installedVersion,targetVersion:record.latestVersion,profileId:plan.profileId,reviewed:true,riskAcknowledged,autoInstall:fast,...exactFile}:{kind:'nexus',itemId:WORKSHOP_REVIEW_ID,modId:Number(m.nexusId),fileId:Number(b.dataset.fileId),installedVersion:record.installedVersion,targetVersion:record.latestVersion,profileId:plan.profileId,reviewed:true,riskAcknowledged,autoInstall:fast,...exactFile};
  WORKSHOP_CHOICES.set(WORKSHOP_REVIEW_ID,selection);
  FAST_PICKER_ID=null;closeFilePicker();renderWorkshop();
  if(fast)return performQueuedWorkshop([selection],plan.profileId);
  workshopSetStatus('File selected for '+record.name+'. Queue selected downloads whenever you like.');return;
 }
 if(currentWorkshopPlan().items.some(row=>row.id===m.id&&row.lane==='candidate')){
  WORKSHOP_REVIEW_ID=m.id;
  b.click();return;
 }
 // Release assets require the active reviewed-candidate flow.
 if(b.dataset.sourceKind==='release'){toast('Run a fresh source check before selecting this release file.');return;}
 // The Nexus file itself was explicitly chosen.
 b.disabled=true;
 try{
  const r=await bridgeRequest('POST','download',{modId:Number(b.dataset.modId),fileId:Number(b.dataset.fileId),confirm:'DOWNLOAD'});
  toast('⇩ '+(r.message||'Sent to MO2 Downloads'));closeFilePicker();await refreshDownloads();
 }catch(ex){toast('Download: '+ex.message);b.disabled=false}
 });
function closeInstallPreview(){
 $('installPreviewVeil').hidden=true;INSTALL_PREVIEW_ID=null;INSTALL_PREVIEW_PROFILE='';
 if(PREVIEW_RETURN_FOCUS?.isConnected)PREVIEW_RETURN_FOCUS.focus({preventScroll:true});
 PREVIEW_RETURN_FOCUS=null;
}
async function showInstallPreview(downloadId){
 if(!BRIDGE.active||!LIVE_CATALOG)return;
 const context=profileContext();INSTALL_PREVIEW_ID=Number(downloadId);INSTALL_PREVIEW_PROFILE=LIVE_CATALOG.profileId;
 PREVIEW_RETURN_FOCUS=document.activeElement;
 $('installPreviewVeil').hidden=false;
 SET('installBriefTitle','Preparing installation briefing…');
 $('installBriefDetails').innerHTML='<div class="picker-loading">Checking the active MO2 profile and reviewed download…</div>';
 $('installBriefWarnings').innerHTML='';
 $('installPreviewConfirm').disabled=true;
 $('installPreviewCancel').focus({preventScroll:true});
 try{
  const brief=await bridgeRequest('GET','install-preview?downloadId='+encodeURIComponent(downloadId));
  if(!contextIsCurrent(context)||INSTALL_PREVIEW_ID!==Number(downloadId)||$('installPreviewVeil').hidden)return;
  const details=[
   ['TARGET MOD',brief.modName||'Unknown'],
   ['INSTALL FILE',brief.archiveName||'Unknown archive'],
   ['FROM',providerLabel(brief.provider||'nexus')],
   ['MO2 PROFILE',brief.profileName||'Unknown'],
   ['GAME RUNTIME',brief.gameRuntime||'Unknown'],
   ['DOWNLOAD ID','#'+brief.downloadId]
  ];
  SET('installBriefTitle',brief.modName||'Install reviewed archive');
  $('installBriefVersions').innerHTML='<span>Installed <strong>'+escapeHTML(brief.installedVersion||'?')+'</strong></span><b>→</b><span>Target <strong>'+escapeHTML(brief.targetVersion||'?')+'</strong></span>';
  $('installBriefDetails').innerHTML=details.map(([k,v])=>'<div class="install-brief-detail"><small>'+k+'</small><strong title="'+escapeHTML(v)+'">'+escapeHTML(v)+'</strong></div>').join('');
  const errors=brief.errors||[],warnings=brief.warnings||[];
  $('installBriefWarnings').innerHTML=errors.map(x=>'<div class="brief-alert brief-block"><strong>BLOCKED</strong><span>'+escapeHTML(x)+'</span></div>').join('')+warnings.map(x=>'<div class="brief-alert brief-watch"><strong>VERIFY</strong><span>'+escapeHTML(x)+'</span></div>').join('');
  SET('installBriefBackup','Profile text/load-order files only ('+((brief.backup?.files||[]).length)+' files). Original mod folders are not backed up. MO2’s standard installer controls file replacement.');
  $('installPreviewConfirm').disabled=!brief.canInstall||LIVE_CATALOG?.profileId!==INSTALL_PREVIEW_PROFILE;
  $('installPreviewConfirm').textContent=brief.canInstall?'Open interactive MO2 installer':'Resolve blocking changes first';
  if(brief.canInstall)$('installPreviewConfirm').focus({preventScroll:true});
 }catch(ex){
  if(!contextIsCurrent(context)||INSTALL_PREVIEW_ID!==Number(downloadId)||$('installPreviewVeil').hidden)return;
  SET('installBriefTitle','Could not verify this download');
  $('installBriefDetails').innerHTML='<div class="brief-alert brief-block">'+escapeHTML(ex.message)+'</div>';
  $('installPreviewConfirm').disabled=true;
 }
}
$('installPreviewCancel').onclick=closeInstallPreview;
$('installPreviewClose').onclick=closeInstallPreview;
$('installPreviewVeil').addEventListener('click',e=>{if(e.target===$('installPreviewVeil'))closeInstallPreview()});
$('installPreviewConfirm').onclick=async()=>{
 if(!Number.isSafeInteger(INSTALL_PREVIEW_ID)||INSTALL_PREVIEW_ID<0||!BRIDGE.active)return;
 const id=INSTALL_PREVIEW_ID,profile=INSTALL_PREVIEW_PROFILE;
 if(LIVE_CATALOG?.profileId!==profile){closeInstallPreview();workshopSetStatus('MO2 profile changed. Reload and review this archive again.');return;}
 $('installPreviewConfirm').disabled=true;
 try{
  const response=await bridgeRequest('POST','install',{downloadId:id,confirm:'INSTALL'});
  closeInstallPreview();toast(response.message||'MO2 installer requested. Complete its prompts inside MO2.');
  await refreshDownloads();
 }catch(ex){
  SET('installBriefTitle','Installation did not start');
  $('installBriefWarnings').innerHTML='<div class="brief-alert brief-block">'+escapeHTML(ex.message)+'</div>';
  $('installPreviewConfirm').disabled=true;
 }
};
$('liveDownloadList').addEventListener('click',async e=>{
 const immediate=e.target.closest('button[data-retry-download-id]');
 if(immediate){
  immediate.disabled=true;
  try{
   const result=await bridgeRequest('POST','retry-download',{downloadId:Number(immediate.dataset.retryDownloadId)});
   toast(result.message||'Reviewed archive requeued in MO2.');
   await refreshDownloads();
  }catch(ex){toast('Retry: '+ex.message+'. Choose another file if the release changed.')}
  finally{immediate.disabled=false}
  return;
 }
 const retry=e.target.closest('button[data-retry-item-id]');
 if(retry){$('workshopFilter').value='candidate';renderWorkshop();reviewWorkshopFile(retry.dataset.retryItemId).catch(ex=>workshopSetStatus('Review failed: '+ex.message));return;}
 const details=e.target.closest('button[data-download-details]');
 if(details){showInstallPreview(Number(details.dataset.downloadDetails));return;}
 const b=e.target.closest('button[data-download-id]');if(!b)return;
 b.disabled=true;
 try{const result=await bridgeRequest('POST','install',{downloadId:Number(b.dataset.downloadId),confirm:'INSTALL'});
  toast(result.message||'MO2 installer opening.');await refreshDownloads();}
 catch(ex){toast('MO2 installer: '+ex.message);b.disabled=false;}
});
$('liveQuick').onclick=()=>scanLive('quick');$('liveDeep').onclick=()=>scanLive('deep');$('liveLocal').onclick=()=>scanLive('local');$('liveAll').onclick=()=>scanLive('all');
$('liveRefresh').onclick=async()=>{const button=$('liveRefresh');if(button.disabled)return;button.disabled=true;try{const r=await requestLiveCatalog('POST','refresh');if(!r)return;await r.ready;toast('⟳ Live profile refreshed')}catch(ex){toast(ex.message)}finally{button.disabled=!LIVE_CONNECTED}};
$('liveBackup').onclick=async()=>{if(!confirm('Create a timestamped backup of this MO2 profile’s text load-order state?'))return;try{const r=await bridgeRequest('POST','backup',{confirm:'BACKUP'});toast('⛨ Backed up '+r.files.length+' profile files.')}catch(ex){toast('Backup: '+ex.message)}};
$('liveRuntime').onclick=async()=>{const button=$('liveRuntime');if(button.disabled)return;button.disabled=true;try{const r=await requestLiveCatalog('POST','runtime');if(!r)return;await r.ready;toast('⚙ Target runtime updated')}catch(ex){toast(ex.message)}finally{button.disabled=!LIVE_CONNECTED}};
$('liveConnect').onclick=async()=>{try{const r=await bridgeRequest('POST','nexus-connect');toast(r.message)}catch(ex){toast(ex.message)}};
window.addEventListener('storage',e=>{if(e.key===STORAGE){STORE=structuredClone(BASE);load();$('search').value=STORE.prefs.query;applyAppearance();render();toast('↻ Changes synchronized from another Arsenal tab.')}});
load();applyAppearance();updateStorageIndicator();render();renderActivity();
// Development/test trace, harmless in release; does not expose filesystem privileges.
if(BRIDGE.active){void pollLiveBridge();document.addEventListener('visibilitychange',()=>{if(!document.hidden)void pollLiveBridge()})}
/**
 * Native desktop offline profile import. The web page cannot read files or
 * invoke privileged desktop APIs; the desktop passes a sanitized data snapshot
 * after an explicit native file chooser. No server trust or verified XP.
 */
function importOfflineCatalog(c){
 if(BRIDGE.active)throw Error('Disconnect live MO2 before importing a local profile');
 if(!c||c.offline!==true||!c.profileId||!Array.isArray(c.items)||!Array.isArray(c.groups))
  throw Error('Not a valid local MO2 snapshot');
 const previousProfile=DATA.profileId;
 if(SAVE_TIMER!==null)save();PROFILE_EPOCH++;CATALOG_REQUEST++;WORKSHOP_REQUEST++;GAME_REQUEST++;CATALOG_RENDER_REVISION++;LOCAL_PLAN_CACHE=null;
 DATA=c;
 hideNativeWelcome();
 ITEM_MAP=new Map(c.items.map(m=>[m.id,m]));
 GROUP_MAP=new Map(c.groups.map(g=>[g.id,g]));
 LIVE_CATALOG=null;GAME=null;GAME_SEEN=-1;NATIVE_SELECTED='';focusedRowId='';
 WORKSHOP_PLAN=null;WORKSHOP_CHOICES.clear();
 ACTIVITY_RECORDS=[];ACTIVITY_WARNING='';ACTIVITY_GENERATION++;renderActivity();
 STORAGE='modhunter-offline-profile:v1:'+c.profileId;
 STORE=structuredClone(BASE);load();
 $('search').value=STORE.prefs.query||'';
 $('livePill').textContent='◈ LOCAL MO2 SNAPSHOT';
 $('livePill').classList.remove('connected');
 $('liveProfile').textContent=c.profileName+' / '+c.game;
 $('liveCount').textContent=c.items.length+' imported mods · '+c.groups.length+' sections';
 $('liveUpdateCount').textContent='Not remotely verified';
 $('liveCached').textContent='Source links are advisory until scanned through MO2';
 $('liveSync').textContent='On-demand read-only profile import';
 $('liveState').textContent='Imported your real MO2 modlist.txt and installed metadata. Re-import to refresh from disk; connect the optional MO2 plugin for native Nexus checks and downloads.';
 $('heroSummary').textContent='Imported '+c.items.length+' actual mods from MO2 profile '+c.profileName+'. This desktop-only snapshot is read-only. Review exact sources, versions, and compatibility notes; native updating requires the MO2 plugin.';
 $('runtimeBadge').innerHTML='<span class="status-light"></span><div><strong>'+escapeHTML(c.game)+' / '+escapeHTML(c.profileName)+'</strong><span>Local snapshot · target runtime '+escapeHTML(c.gameRuntime||'unknown')+'</span></div><span class="runtime-lock">IMPORTED</span>';
 $('referenceLinks').textContent='Profile runtime: '+(c.gameRuntime||'unknown')+' · Check requirements on each mod source.';
 $('advisoryToggle').querySelector('b').textContent='IMPORTED PROFILE CHECKS';
 $('advisoryBody').innerHTML='<div><span>01 / RUNTIME</span><strong>Target runtime: '+escapeHTML(c.gameRuntime||'unknown')+'</strong><p>Confirm the game executable, script extender and native DLL requirements before installing updates.</p></div><div><span>02 / DEPENDENCIES</span><strong>Check masters and requirements.</strong><p>Review missing or inactive masters and required dependency versions in each mod inspector.</p></div><div><span>03 / CONVERSIONS</span><strong>Recheck converted plugins after updates.</strong><p>Compare the current original and converted plugins. A candidate result does not establish save, script or asset compatibility.</p></div><div><span>04 / LIVE UPDATES</span><strong>Connect MO2 for verified updates.</strong><p>This snapshot is read only. The MO2 plugin checks remote versions and runs native downloads and interactive installations.</p></div>';
 $('riskSubtitle').textContent='Runtime + masters + conversions';
 const footer=document.querySelector('.pagefooter');
 if(footer?.firstElementChild)footer.firstElementChild.textContent='ᛟ MO2 SNAPSHOT / '+(c.game||'Game')+' / '+c.profileName;
 if(footer?.children?.[1])footer.children[1].textContent='READ-ONLY PROFILE · SOURCE STATUS UNVERIFIED · SAVE LOCALLY';
 const footerLink=footer?.querySelector('a');
 if(footerLink){footerLink.href='https://github.com/Herbertofury/Modhunter/wiki/Updater-Source-Coverage';footerLink.textContent='SOURCE COVERAGE ↗';}
 for(const id of ['liveAll','liveQuick','liveDeep','liveLocal','liveRefresh','liveRuntime','liveConnect','liveBackup'])$(id).disabled=true;
 applyAppearance();updateStorageIndicator();render();
 if(previousProfile!==c.profileId)closeInspector();else refreshInspectorInsights();
 refreshWorkshop().catch(e=>workshopSetStatus('Local profile: '+e.message));
 return{ok:true,count:c.items.length,profileId:c.profileId};
}

// The native shell can request only these existing user actions. Credentials
// remain inside this same-origin page, never passed through native IPC.
window.modhunterDesktopAction=async function(action){
 if(!BRIDGE.active||!LIVE_CONNECTED){toast('Connect to the active MO2 profile first.');return {ok:false};}
 if(action==='refresh'){await $('liveRefresh').onclick();return {ok:true};}
 if(action==='audit'){await analyzePlugins();return {ok:true};}
 if(action==='diagnostics'){
  const dialog=$('connectionDetails'),content=$('connectionDetailsText');
  content.textContent='Reading the live plugin status...';dialog.showModal();
  try{const report=await bridgeRequest('GET','diagnostics');content.textContent=JSON.stringify(report,null,2);}
  catch(error){content.textContent='Could not read plugin status: '+error.message;}
  return {ok:true};
 }
 return {ok:false};
};
$('connectionDetailsClose').onclick=()=>$('connectionDetails').close();
function hideNativeWelcome(){
 $('nativeWelcome').hidden=true;
 const shell=document.querySelector('.shell');if(shell)shell.inert=false;
}
$('nativeWelcomeDemo').onclick=hideNativeWelcome;
const nativeAsset=(location.hostname==='tauri.localhost'&&location.protocol==='http:')||
 (location.hostname==='localhost'&&location.protocol==='tauri:');
if(nativeAsset&&!BRIDGE.active&&!DATA.offline&&!window.__ARSENAL_REALM_ROUTE__){
 $('nativeWelcome').hidden=false;
 const shell=document.querySelector('.shell');if(shell)shell.inert=true;
}
window.modhunterImportCatalog=importOfflineCatalog;

window.__ARSENAL_QA__={game:()=>GAME,refreshGame,previewVictory:()=>launchVictory({verifiedMods:DATA.items.length,bonus:0,updates:counts().done},true),getVictory:()=>({active:!$('victoryOverlay').hidden,title:$('victoryTitle').innerText}),closeVictory,locate:locateInMO2,live:BRIDGE.active,setCatalog:activateLiveCatalog,finisher:(name,x,y)=>{STORE.prefs.effect=name;spawnFinisher({x,y})},count:DATA.items.length,groups:DATA.groups.length,getSnapshot:()=>({counts:counts(),search:STORE.prefs.query,filter:STORE.prefs.filter}),aether:()=>window.AetherFX?.getState(),previewAether:()=>window.AetherFX?.preview(),getCatalog:()=>DATA,verifyRow:(id)=>({id,exists:ITEM_MAP.has(id),state:getRow(id)})};
})();
