/* Creator Continuity: evidence-bound notices and reversible source choices. */
(function(root){
'use strict';
const bundled=typeof module==='object'&&module.exports?require('../../shared/creator-continuity.v1.json'):(root.__MODHUNTER_CREATOR_REGISTRY__||{});
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const own=(object,key)=>Object.prototype.hasOwnProperty.call(object,key);
const states=new Set(['active','banned','removed','suspended','moved','unknown']);
const text=(value,max=300)=>typeof value==='string'&&value.length>0&&value.length<=max&&!/[\u0000-\u001f\u007f]/.test(value);
function safeURL(value){
 if(typeof value!=='string'||/[\u0000-\u0020\u007f\\]/.test(value))return '';
 try{const u=new URL(value);if(u.protocol!=='https:'||u.username||u.password||u.port||!u.hostname.includes('.')||/^[\d.]+$|[:\[\]]/.test(u.hostname)||/(^|\.)(localhost|local|internal|test|invalid)$/.test(u.hostname)||u.href.length>2048)return '';
  for(const key of u.searchParams.keys())if(/token|secret|auth|password|key|signature|session/i.test(key))return '';
  u.hash='';return u.href;
 }catch{return ''}
}
function identity(value){
 const safe=safeURL(value);if(!safe)return null;const u=new URL(safe),parts=u.pathname.split('/').filter(Boolean);
 if(['www.nexusmods.com','nexusmods.com'].includes(u.hostname)&&!u.pathname.includes('//')&&parts.length===3&&parts[1]==='mods'&&/^[a-z0-9_-]+$/.test(parts[0])&&/^[1-9]\d*$/.test(parts[2])&&Number.isSafeInteger(Number(parts[2])))
  return {provider:'nexus',game:parts[0],projectId:parts[2],key:'nexus:'+parts[0]+':'+parts[2]};
 if(u.hostname==='modholdmods.com'&&!u.pathname.includes('//')&&[4,5].includes(parts.length)&&parts[0]==='g'&&parts[2]==='mods'&&/^[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?$/.test(parts[1])&&/^[1-9]\d*$/.test(parts[3])&&Number.isSafeInteger(Number(parts[3]))&&(!parts[4]||/^[A-Za-z0-9_.-]{1,120}$/.test(parts[4])))
  return {provider:'modhold',game:parts[1],projectId:parts[3],key:'modhold:'+parts[1]+':'+parts[3]};
 return {provider:'website',game:'',projectId:safe,key:'page:'+safe};
}
function stamp(value){const time=typeof value==='string'?Date.parse(value):NaN;return Number.isFinite(time)&&time>0&&time<=Date.now()+300000}
function link(url,label){const safe=safeURL(url);return safe?'<a href="'+esc(safe)+'" target="_blank" rel="noopener noreferrer">'+esc(label)+' ↗</a>':''}
function validateRegistry(raw){
 if(!raw||raw.kind!=='creator-continuity-registry'||raw.schemaVersion!==1||!text(raw.revision)||!stamp(raw.updatedAt)||!Array.isArray(raw.authors)||!Array.isArray(raw.migrations)||raw.authors.length>10000||raw.migrations.length>50000)throw Error('Unsupported Creator Continuity registry.');
 const ids=new Set(),migrationIds=new Set(),projects=new Set(),authorById=new Map(),handles=new Set();
 for(const author of raw.authors){
  if(!author||!text(author.id)||ids.has(author.id)||!text(author.name)||!text(author.source?.provider,60)||!text(author.source?.handle,180)||!safeURL(author.source?.profileUrl)||!states.has(author.status?.state)||!stamp(author.status?.observedAt)||!safeURL(author.status?.evidenceUrl)||!text(author.status?.evidenceKind)||!Array.isArray(author.destinations))throw Error('Invalid creator identity or status evidence.');
  ids.add(author.id);
  if(author.source.accountId!==undefined&&!(typeof author.source.accountId==='string'&&/^[1-9][0-9]{0,19}$/.test(author.source.accountId)))throw Error('Invalid stable creator account identity.');
  const handleKey=author.source.provider+':'+author.source.handle.toLowerCase();
  if(handles.has(handleKey))throw Error('Duplicate creator handle identity.');
  handles.add(handleKey);authorById.set(author.id,author);
  if(author.destinations.length>100)throw Error('Too many creator destinations.');
  for(const d of author.destinations)if(!d||!text(d.provider,60)||!text(d.handle,180)||!safeURL(d.profileUrl)||!safeURL(d.identityEvidence?.url)||!safeURL(d.identityEvidence?.targetUrl)||!stamp(d.identityEvidence?.observedAt))throw Error('Invalid destination identity evidence.');
 }
 for(const migration of raw.migrations){
  const from=identity(migration?.from?.url),to=identity(migration?.to?.url);
  if(!migration||!text(migration.id)||migrationIds.has(migration.id)||!ids.has(migration.authorId)||!text(migration.title)||migration.reviewRequired!==true||!from||!to||from.key===to.key||projects.has(from.key)||!stamp(migration.observedAt)||!Array.isArray(migration.evidenceUrls)||!migration.evidenceUrls.length||migration.evidenceUrls.length>20||migration.evidenceUrls.some(url=>!safeURL(url)))throw Error('Invalid or conflicting migration. Every destination needs review.');
  for(const [side,parsed] of [[migration.from,from],[migration.to,to]])if((['nexus','modhold'].includes(side.provider)||parsed.provider!=='website')&&(side.provider!==parsed.provider||String(side.projectId)!==parsed.projectId||side.game!==parsed.game))throw Error('Migration URL and project identity disagree.');
  const author=authorById.get(migration.authorId);
  if(author.source.provider!==migration.from.provider||!author.destinations.some(d=>d.provider===migration.to.provider&&new URL(d.profileUrl).origin===new URL(migration.to.url).origin))throw Error('Migration is not linked to this creator destination.');
  migrationIds.add(migration.id);projects.add(from.key);
 }
 return JSON.parse(JSON.stringify(raw));
}
const base=validateRegistry(bundled);
let cachedRegistry='',cachedIndex=null;
function index(imported=''){
 if(cachedIndex&&cachedRegistry===imported)return cachedIndex;
 const authors=new Map(base.authors.map(author=>[author.id,{...author,imported:false}]));
 const migrations=new Map(base.migrations.map(migration=>[migration.id,migration]));
 if(imported){
  const extra=validateRegistry(JSON.parse(imported));
  for(const author of extra.authors){const current=authors.get(author.id);if(!current||Date.parse(author.status.observedAt)>Date.parse(current.status.observedAt))authors.set(author.id,{...author,imported:true})}
  for(const migration of extra.migrations)migrations.set(migration.id,migration);
  validateRegistry({...base,authors:[...authors.values()],migrations:[...migrations.values()]});
 }
 const bySource=new Map(),byDestination=new Map(),byHandle=new Map();
 for(const author of authors.values())byHandle.set(author.source.provider+':'+author.source.handle.toLowerCase(),author);
 for(const migration of migrations.values()){
  const from=identity(migration.from.url),to=identity(migration.to.url),key=from.key;
  if(bySource.has(key)&&bySource.get(key).id!==migration.id)throw Error('Conflicting migration records for one original project.');
  bySource.set(key,migration);byDestination.set(to.key,migration);
 }
 cachedRegistry=imported;cachedIndex={authors,migrations,bySource,byDestination,byHandle};return cachedIndex;
}
const noticeCache=new WeakMap();
function notice(mod,row={},source=mod.source||{},imported='',now=Date.now()){
 const observation=row.creatorObservation,choice=row.creatorContinuity;
 const signature=[source.url,mod.author,mod.update?.author,choice,observation,imported,Math.floor(now/60000)];
 const cached=noticeCache.get(mod);if(cached&&signature.every((value,index)=>Object.is(value,cached.signature[index])))return cached.value;
 const value=buildNotice(mod,row,source,imported,now);noticeCache.set(mod,{signature,value});return value;
}
function buildNotice(mod,row={},source=mod.source||{},imported='',now=Date.now()){
 let data;try{data=index(imported)}catch{data=index('')}
 const saved=row.creatorContinuity,effective=identity(source.url);
 const savedApplies=saved&&[identity(saved.originalUrl)?.key,identity(saved.destinationUrl)?.key].includes(effective?.key);
 const original=identity(savedApplies?saved.originalUrl:source.url);
 const migration=original?data.bySource.get(original.key):null;
 const rawAuthor=mod.author||mod.update?.author?.name||'';
 const handle=typeof rawAuthor==='string'?rawAuthor.trim():rawAuthor.handle||rawAuthor.name||'';
 let author=migration?data.authors.get(migration.authorId):null;
 if(!author&&original)author=data.byHandle.get(original.provider+':'+handle.toLowerCase());
 const observed=row.creatorObservation;
 if(!author&&original?.provider==='nexus'&&observed?.verified===true&&observed.state==='banned'&&observed.handle?.toLowerCase()===handle.toLowerCase()&&observed.profileUrl?.toLowerCase()===('https://www.nexusmods.com/profile/'+handle).toLowerCase()&&Number.isFinite(observed.checkedAt)&&observed.checkedAt*1000<=now+300000){
  author={id:'nexus:'+handle.toLowerCase(),name:handle,source:{provider:'nexus',handle,profileUrl:observed.profileUrl,accountId:observed.accountId},status:{state:'banned',observedAt:new Date(observed.checkedAt*1000).toISOString(),evidenceUrl:observed.profileUrl},destinations:[]};
 }
 if(!author)return null;
 if(author.status.state==='active'&&!saved&&!migration)return null;
 const observation=row.creatorObservation;
 const latest=observation?.verified===true&&(!author.source.accountId||observation.accountId===author.source.accountId)&&observation.handle?.toLowerCase()===author.source.handle.toLowerCase()&&observation.profileUrl?.toLowerCase()===author.source.profileUrl.toLowerCase()&&['banned','not-marked-banned'].includes(observation.state)&&Number.isFinite(observation.checkedAt)&&observation.checkedAt*1000>Date.parse(author.status.observedAt)&&observation.checkedAt*1000<=now+300000?observation:null;
 const stale=now-(latest?latest.checkedAt*1000:Date.parse(author.status.observedAt))>7*86400000;
 const target=migration&&identity(migration.to.url);
 const tracking=Boolean(saved?.state==='tracking'&&target&&effective?.key===target.key);
 return {author,migration,saved,latest,stale,tracking,match:migration?'exact-project':'saved-author-handle'};
}
function approve(mod,row,source,migrationId,{imported='',confirmed=false,now=new Date().toISOString()}={}){
 if(!confirmed)throw Error('Review the destination, author, game and variant first.');
 const data=index(imported),migration=data.migrations.get(migrationId),sourceId=identity(source.url);
 if(!migration||sourceId?.key!==identity(migration.from.url)?.key)throw Error('The selected source changed. Review its destination again.');
 const author=data.authors.get(migration.authorId),destination=author.destinations.find(d=>d.provider===migration.to.provider&&new URL(d.profileUrl).origin===new URL(migration.to.url).origin);
 if(!destination||!stamp(now))throw Error('Destination identity evidence is unavailable.');
 const previous=typeof row.url==='string'?row.url:'';
 return {...row,url:migration.to.url,updatedAt:now,creatorContinuity:{
  schemaVersion:1,candidateId:migration.id,authorId:author.id,expectedAuthor:destination.handle,
  originalUrl:source.url,previousOverride:previous,destinationUrl:migration.to.url,
  acceptedAt:now,evidenceRevision:base.revision,state:'tracking'
 }};
}
function changeTracking(row,action){
 const saved=row?.creatorContinuity;
 if(!saved||!['tracking','paused','undone'].includes(saved.state))throw Error('No reviewed destination is saved for this mod.');
 const next={...row,creatorContinuity:{...saved},updatedAt:new Date().toISOString()};
 if(action==='resume'){
  if(saved.state!=='paused'||(row.url||'')!==saved.previousOverride)throw Error('Your source changed. Review it again before resuming.');
  next.url=saved.destinationUrl;next.creatorContinuity.state='tracking';
 }else{
  if(action!=='pause'&&action!=='undo')throw Error('Unknown continuity action.');
  if(saved.state==='tracking'&&identity(row.url)?.key!==identity(saved.destinationUrl)?.key)throw Error('Your source changed. Keep your new source and review the saved migration.');
  if(saved.state==='tracking')next.url=saved.previousOverride;
  next.creatorContinuity.state=action==='undo'?'undone':'paused';
 }
 return next;
}
function chip(mod,row,source,imported){
 const n=notice(mod,row,source,imported);if(!n)return '';
 const label=n.tracking?'Creator followed on '+n.migration.to.provider:n.saved?.state==='paused'?'Creator tracking paused':n.stale?'Creator notice · check status':n.latest?.state==='not-marked-banned'?'Creator status refreshed':'Creator '+(n.latest?.state||n.author.status.state);
 return '<button class="creator-chip" type="button" data-source-act="inspect" data-id="'+esc(mod.id)+'" title="'+esc('Creator Continuity · evidence dated '+n.author.status.observedAt)+'">'+esc(label)+'</button>';
}
function target(mod,row,source,imported){
 const n=notice(mod,row,source,imported);if(n?.author.source.provider==='nexus')return n.author.source;
 const value=mod.author||mod.update?.author,handle=typeof value==='string'?value.trim():value?.handle||value?.name;
 return identity(source.url)?.provider==='nexus'&&/^[A-Za-z0-9_-]{1,80}$/.test(handle||'')?{provider:'nexus',handle,profileUrl:'https://www.nexusmods.com/profile/'+handle,...(row.creatorObservation?.handle?.toLowerCase()===handle.toLowerCase()?{accountId:row.creatorObservation.accountId}:{})}:null;
}
function details(mod,row,source,{imported='',live=false}={}){
 const n=notice(mod,row,source,imported);
 if(!n){
  const author=target(mod,row,source,imported);if(!author)return '';
  return '<section class="creator-panel"><span class="creator-premium">PREMIUM · CREATOR CONTINUITY</span><h3>Keep following this creator</h3><p>'+link(author.profileUrl,author.handle)+'</p><p class="creator-evidence">Check the Nexus profile saved with this mod. Missing pages and failed requests do not establish a ban.</p>'+(live?'<div class="creator-actions"><button type="button" data-creator-action="status" data-id="'+esc(mod.id)+'">Check creator status</button></div><label class="creator-review"><input type="checkbox" data-creator-watch="'+esc(mod.id)+'" '+(row.creatorWatch?'checked':'')+'> Check this creator with mod source updates.</label>':'<p class="creator-evidence">Connect MO2 for current creator status checks.</p>')+'</section>';
 }
 const {author,migration,saved,latest}=n;
 let html='<section class="creator-panel" aria-label="Creator Continuity"><div class="creator-heading"><div><span class="creator-premium">PREMIUM · CREATOR CONTINUITY</span><h3>'+esc(author.name)+'</h3></div><span class="creator-status">'+esc(n.tracking?'Tracking destination':latest?.state==='not-marked-banned'?'No ban badge observed':latest?.state||author.status.state)+'</span></div>';
 html+='<p>'+esc((author.imported?'An imported notice reports ':'The saved provider observation records ')+author.status.state+' on '+author.source.provider+'.')+'</p><p class="creator-evidence">Observed '+esc(new Date(author.status.observedAt).toLocaleString())+(n.stale?' · Recheck this historical status.':'')+' · '+link(author.status.evidenceUrl,'Status evidence')+'</p>';
 if(latest)html+='<p class="creator-evidence">Latest direct profile check: '+esc(latest.state==='banned'?'Banned badge confirmed':'No banned badge in the verified profile header')+' · '+esc(new Date(latest.checkedAt*1000).toLocaleString())+'. Earlier evidence is retained.</p>';
 if(live&&author.source.provider==='nexus')html+='<div class="creator-actions"><button type="button" data-creator-action="status" data-id="'+esc(mod.id)+'">Refresh creator status</button></div>';
 if(n.match==='saved-author-handle')html+='<p class="creator-evidence">Matches the author handle saved with this mod. No exact relocation is registered for this project.</p>';
 html+='<div class="creator-links">'+author.destinations.map(d=>link(d.profileUrl,'Creator on '+d.provider)).join('')+'</div>';
 if(migration){
  html+='<div class="creator-route"><div><small>Original project</small>'+link(migration.from.url,'Open '+migration.from.provider+' #'+migration.from.projectId)+'</div><div><small>Destination to review</small>'+link(migration.to.url,'Open '+migration.to.provider+' #'+migration.to.projectId)+'</div></div>';
  html+='<p class="creator-evidence">'+esc(migration.note||'Review the author, project, game and required variant before changing update tracking.')+'</p>';
  const tracked=saved?.state==='tracking',paused=saved?.state==='paused';
  if(tracked||paused){
   html+='<p class="creator-evidence">User-reviewed mapping · '+esc(new Date(saved.acceptedAt).toLocaleString())+'. The original source is retained.</p><div class="creator-actions"><button type="button" data-creator-action="'+(paused?'resume':'pause')+'" data-id="'+esc(mod.id)+'">'+(paused?'Resume destination tracking':'Pause destination tracking')+'</button><button type="button" data-creator-action="undo" data-id="'+esc(mod.id)+'">Restore original source</button></div>';
  }else{
   html+='<label class="creator-review"><input type="checkbox" data-creator-confirm="'+esc(migration.id)+'"> I reviewed this author, mod, game and variant.</label><div class="creator-actions"><button class="button-bright" type="button" disabled data-creator-action="approve" data-candidate="'+esc(migration.id)+'" data-id="'+esc(mod.id)+'">Track reviewed destination</button></div>';
  }
  html+='<p class="creator-evidence">'+(live?'The next source check uses the approved destination. File installation still needs its own review.':'This choice is saved locally. Connect MO2 to run version checks; the destination page remains available.')+'</p>';
 }
 return html+'</section>';
}
function summary(items,getRow,getSource,imported=''){
 let affected=0,tracking=0,review=0;for(const mod of items){const n=notice(mod,getRow(mod.id),getSource(mod),imported);if(n){affected++;if(n.tracking)tracking++;else if(n.migration)review++}}
 return {affected,tracking,review};
}
function importRegistry(raw,current=''){
 const validated=validateRegistry(raw),serialized=JSON.stringify(validated);if(serialized.length>1048576)throw Error('Creator registry is larger than 1 MiB.');
 index(serialized);return serialized;
}
function registryError(imported=''){try{index(imported);return ''}catch{return 'Saved creator notices could not be read. Their original data is retained for export; dated bundled observations are shown.'}}
const api={identity,safeURL,validateRegistry,notice,target,approve,changeTracking,chip,details,summary,importRegistry,registryError,base};
root.ModhunterContinuity=api;if(typeof module==='object'&&module.exports)module.exports=api;
})(typeof window==='object'?window:globalThis);
