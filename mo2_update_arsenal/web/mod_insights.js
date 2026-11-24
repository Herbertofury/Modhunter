/* Evidence presentation only. No filesystem, native IPC, network, or conversions. */
(function(root){
'use strict';
const text=v=>String(v??'');
const esc=v=>text(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const list=v=>Array.isArray(v)?v:[];
const safeURL=value=>{try{const u=new URL(value);return u.protocol==='https:'&&!u.username&&!u.password?u.href:''}catch{return''}};
const staleStates=new Set(['source-changed','output-changed','source-and-output-changed','winner-changed']);
const eligibilityLabels={'already-light':'Already light','flag-only-candidate':'Flag-only candidate','requires-compaction':'Compaction needs review',blocked:'Conversion blocked',unsupported:'Conversion not supported',unknown:'Eligibility unverified'};
const provenanceLabels={current:'Matches recorded ESLifier output','source-changed':'Original changed · review conversion','output-changed':'Converted output changed','source-and-output-changed':'Original and converted output changed','winner-changed':'Recorded conversion no longer wins','not-configured':'ESLifier records not connected','not-recorded':'No ESLifier conversion record',unknown:'Conversion history unverified','review-required':'Conversion history needs review'};
function checked(value){if(value===null||value===undefined||value==='')return'Not checked';const d=new Date(typeof value==='number'?value*1000:value);return Number.isFinite(d.getTime())?d.toLocaleString():'Check time unavailable'}
function isNexus(mod){if(mod.modHealth?.provider)return mod.modHealth.provider==='nexus';const source=mod.source||{};if(source.verified&&source.url){try{const url=new URL(source.url);return url.protocol==='https:'&&['www.nexusmods.com','nexusmods.com'].includes(url.hostname)&&/^\/[a-z0-9_-]+\/mods\/[1-9]\d*\/?$/.test(url.pathname)}catch{return false}}return Number.isSafeInteger(Number(mod.nexusId))&&Number(mod.nexusId)>0}
function advisoryState(report,now=Date.now()/1000){
 if(report?.state!=='checked')return report?.state||'unknown';
 const age=now-Number(report.checkedAt);
 return !report.checkedAt||!Number.isFinite(age)||age < -300||age>14400?'stale':'checked';
}
function affectedAdvisory(mod){
 const report=mod.modHealth?.securityAdvisories;
 return Boolean(advisoryState(report)==='checked'&&Number(report.affectedCount)>0);
}
function fileReview(mod){
 return mod.update?.verdict==='review'&&['file-identity','exact-file','project-only'].includes(mod.update?.evidenceScope)&&isNexus(mod);
}
function summary(mod){
 const analysis=mod.localAnalysis||{},plugins=list(analysis.plugins),health=mod.modHealth||{},bugs=health.bugs||{};
 const result={plugins:plugins.length,light:0,full:0,lightOverrides:0,inactiveLightOverrides:0,candidates:0,compaction:0,blocked:0,incomplete:0,stale:0,masterIssues:0,invalid:0,inactive:0,unresolved:0,needsRecheck:analysis.needsRecheck===true,bugState:bugs.state||'unknown',bugCount:Number.isSafeInteger(bugs.displayedCount)&&bugs.displayedCount>=0?bugs.displayedCount:null};
 for(const p of plugins){
  if(p.winnerKnown===false)result.unresolved++;
  if(p.active===false&&(p.winner||p.lightOverride))result.inactive++;
  if(p.winner&&p.active!==false){if(p.header?.lightEffective===true)result.light++;else if(p.header?.lightEffective===false)result.full++}
  if(p.lightOverride&&p.winnerKnown&&p.winnerLight===true){if(p.active===false)result.inactiveLightOverrides++;else result.lightOverrides++}
  if(!result.needsRecheck&&p.eligibility?.status==='flag-only-candidate')result.candidates++;
  if(p.eligibility?.status==='requires-compaction')result.compaction++;
  if(p.eligibility?.status==='blocked')result.blocked++;
  if(p.records?.scanComplete===false||p.recordScanComplete===false)result.incomplete++;
  if(staleStates.has(p.provenance?.state))result.stale++;
  result.masterIssues+=list(p.masterIssues).length;
  if(list(p.errors).length)result.invalid++;
 }
 result.security=affectedAdvisory(mod);
 result.fileReview=fileReview(mod);
 result.maintenance=Boolean(result.security||result.stale||result.masterIssues||result.invalid||result.blocked||result.incomplete||result.needsRecheck||['hidden','removed','under-moderation','not-found','unavailable'].includes(health.page?.status));
 result.eslReview=Boolean(result.stale||result.candidates||result.compaction||result.blocked||result.incomplete||result.unresolved||result.needsRecheck);
 return result;
}
function bugLabel(bugs={}){
 const n=Number.isSafeInteger(bugs.displayedCount)&&bugs.displayedCount>=0?bugs.displayedCount:null;
 if(bugs.state==='unknown'&&bugs.reasonCode==='tab-not-observed')return'Bugs tab not observed';
 if(bugs.state==='available')return n===null?'Bugs tab visible':'Bugs '+n;
 return({disabled:'Bug reports disabled',hidden:'Bug reports hidden',locked:'Bug reports locked',unavailable:'Bug check unavailable',unsupported:'Provider issues'})[bugs.state]||'Bugs unverified';
}
function chips(mod){
 const s=summary(mod),a=mod.localAnalysis||{},health=mod.modHealth||{},bugs=health.bugs||{},parts=[];
 const add=(label,tone,title)=>parts.push('<button type="button" class="insight-chip '+tone+'" data-insight-act="inspect" data-id="'+esc(mod.id)+'" title="'+esc(title)+'">'+esc(label)+'</button>');
 if(s.fileReview)add('Review installed file','warning',mod.update.reason||'Review the installed archive and provider files.');
 if(s.security)add('Security advisory','danger','The installed package version is in a published advisory range.');
 if(s.stale)add('ESLifier needs review','warning','Recorded source, output, or winning plugin changed. Open details before trusting the converted copy.');
 if(s.masterIssues)add(s.masterIssues+' master issue'+(s.masterIssues===1?'':'s'),'danger','Missing, inactive, or late-loading masters in the current profile.');
 if(s.blocked)add('ESL check blocked','danger','The current plugin failed one or more format, runtime, or dependency checks. Inspect the exact reasons.');
 if(s.lightOverrides)add('Light copy wins','positive','A different winning plugin copy is light. Conversion history needs separate hash evidence.');
 else if(s.light)add(s.light+' light'+(s.full?' · '+s.full+' full':''),'positive','Winning active or activation-unknown plugin headers report light format. This is not save-compatibility proof.');
 else if(s.full)add(s.full+' full plugin'+(s.full===1?'':'s'),'neutral','Winning plugin headers report full format.');
 else if(s.inactive)add(s.inactiveLightOverrides?'Light override · inactive':s.inactive+' inactive plugin'+(s.inactive===1?'':'s'),'neutral','The winning plugin is disabled in this profile.');
 if(s.needsRecheck)add('Recheck queued','muted','Inventory changed; record and conversion evidence is being refreshed or needs retry.');
 else if(s.candidates)add(s.candidates+' ESL candidate'+(s.candidates===1?'':'s'),'info','Scanned own records fit flag-only limits. Save, dependency, and external asset safety still need review.');
 else if(s.compaction)add('Compaction review','warning','Flagging alone is insufficient. Renumbering can affect saves, dependent plugins, scripts and assets.');
 if(s.incomplete)add('Record scan incomplete','warning','The declared plugin records do not match the observed scan. Eligibility is unverified.');
 if(s.invalid)add('Plugin read issue','danger','Some plugin files are malformed, unreadable, unsupported, or changed during the scan.');
 if(s.unresolved)add('Winner unverified','muted','MO2 winning origin could not be matched to a known physical copy.');
 if(!s.plugins&&a.inventoryComplete===true)add('No plugin files','neutral','No root plugin files were found in this mod folder in the completed scan.');
 if(isNexus(mod)){
  const old=bugs.freshness?.state==='stale';
  add(bugLabel(bugs)+(old?' · stale':''),['disabled','hidden','locked'].includes(bugs.state)?'warning':old?'muted':'info',
   (bugs.reason||'Refresh public Nexus evidence in the inspector.')+' Checked: '+checked(bugs.checkedAt)+'. The displayed tab count is not an open/total count or proof of installation safety.');
 }
 if(health.page?.status&& !['unknown','available','published','release-observed'].includes(health.page.status))add('Source '+health.page.status,'warning','Provider listing status observed '+checked(health.page.checkedAt));
 return parts.length?'<div class="mod-insights" aria-label="Plugin and source health">'+parts.join('')+'</div>':'';
}
function link(url,label){const href=safeURL(url);return href?'<a href="'+esc(href)+'" target="_blank" rel="noopener noreferrer">'+esc(label)+' ↗</a>':''}
function related(id,label){return id&&!id.startsWith('__')?'<button type="button" class="insight-text-button" data-insight-act="related" data-owner="'+esc(id)+'">'+esc(label)+'</button>':esc(label||'Unknown origin')}
function fileDetails(mod,game){
 const u=mod.update||{};
 if(!isNexus(mod)||!['file-identity','exact-file','project-only'].includes(u.evidenceScope))return '';
 const displayed=[
  ['Installed in MO2',mod.installedVersion],['MO2 cached newest',mod.latestKnownVersion],
  ['Installed file ID',u.installedFileId],['Matched provider file ID',u.targetFileId],
  ['Matched file version',u.latestVersion],['Project version',u.projectVersion],['Ignored in MO2',mod.ignoredVersion],
 ];
 const rows=displayed.filter(([,value])=>value!==undefined&&value!==null&&value!=='').map(([label,value])=>'<dt>'+esc(label)+'</dt><dd>'+esc(value)+'</dd>').join('');
 let href='';
 if(u.evidenceScope==='exact-file'&&Number.isSafeInteger(u.targetFileId)&&u.targetFileId>0){
  try{
   const url=new URL(mod.source?.url),match=/^\/([a-z0-9_-]+)\/mods\/([1-9]\d*)\/?$/.exec(url.pathname);
   if(mod.source.verified&&url.protocol==='https:'&&!url.username&&!url.password&&!url.port&&['www.nexusmods.com','nexusmods.com'].includes(url.hostname)&&match&&(!game||game==='unknown'||game===match[1])&&(!mod.nexusId||Number(mod.nexusId)===Number(match[2]))){
    href='https://www.nexusmods.com/'+match[1]+'/mods/'+match[2]+'?tab=files&file_id='+u.targetFileId;
   }
  }catch{}
 }
 return '<div class="insight-section"><h4>Installed Nexus file</h4><p>'+esc(u.reason||'File identity needs review.')+'</p><dl>'+rows+'<dt>Provider check</dt><dd>'+esc(checked(u.checkedAt))+(Number(u.expiresAt)>0&&Number(u.expiresAt)<=Date.now()/1000?' · expired':'')+'</dd></dl>'+link(href,'View matched file')+'</div>';
}
const historicalLabels={
 candidate:'Actionable',current:'Current',review:'Review',unverified:'Unverified',unavailable:'Unavailable',
 ignored:'Ignored',unknown:'Unknown','newer-installed':'Newer installed'
};
function historicalDate(value){
 if(!value)return '';
 const date=new Date(value);
 return Number.isFinite(date.getTime())?date.toISOString().slice(0,19).replace('T',' ')+' UTC':'';
}
function historicalSummary(history){
 if(!history)return '';
 if(history.error)return 'Historical Nexus evidence unavailable. '+history.error;
 const counts=Object.entries(historicalLabels).filter(([key])=>history.totals?.[key]>0)
  .map(([key,label])=>history.totals[key].toLocaleString()+' '+label.toLowerCase());
 const date=historicalDate(history.lastObservation);
 return 'Historical Nexus snapshot · '+(date?'Last observed '+date:'No provider observation')+' · '+counts.join(' / ')+'.';
}
function historicalBadge(h){
 if(!h||!Object.hasOwn(historicalLabels,h.verdict))return '';
 const date=historicalDate(h.seen);
 return '<span class="detected-status" data-historical-verdict="'+esc(h.verdict)+'" title="'+esc((date||'No recorded provider observation')+' · '+h.reason)+'">History · '+esc(historicalLabels[h.verdict])+'</span>';
}
function historicalDetails(h){
 if(!h||!Object.hasOwn(historicalLabels,h.verdict))return '';
 const fields=[['Provider observation',historicalDate(h.seen)||'No recorded observation'],['Installed file ID',h.installedFile],
  ['Matched file ID',h.targetFile],['Matched release',h.release],['Project version',h.projectVersion],
  ['Evidence scope',h.scope],[h.seen&&h.providerUrl?'Observed source':'Source on record',h.providerUrl||h.sourceUrl]];
 const rows=fields.filter(([,v])=>v!=null&&v!=='').map(([label,value])=>'<dt>'+esc(label)+'</dt><dd>'+esc(value)+'</dd>').join('');
 return '<section class="insight-section" data-historical-evidence><h4>Historical Nexus evidence · '+esc(historicalLabels[h.verdict])+'</h4><p>'+esc(h.reason)+'</p><dl>'+rows+'</dl></section>';
}
function details(mod,{live=false,offline=false,game='',auditState={}}={}){
 const a=mod.localAnalysis||{},plugins=list(a.plugins),health=mod.modHealth||{},bugs=health.bugs||{},links=health.links||{},s=summary(mod);
 const id=esc(mod.id),known=Object.keys(a).length>0,deep=a.depth==='records',running=['queued','running'].includes(auditState.state);
 let html='<section class="insight-panel"><div class="insight-heading"><div><span class="drawer-label">PLUGIN & SOURCE INTELLIGENCE</span><h3>What this mod needs</h3></div><span class="insight-depth">'+esc(deep?'Record scan':known?'Header / native state':'Not analyzed')+'</span></div>'+chips(mod);
 html+='<div class="insight-actions"><button type="button" data-insight-act="audit" data-id="'+id+'"'+(running?' disabled':'')+'>'+esc(running?'Analyzing plugins…':'Analyze plugins')+'</button>'+(live?'<button type="button" data-insight-act="eslifier" data-id="'+id+'">Connect ESLifier records</button>':'')+(isNexus(mod)?'<button type="button" data-insight-act="health" data-id="'+id+'"'+(health.job?.state==='running'?' disabled':'')+'>'+esc(health.job?.state==='running'?'Checking Nexus…':'Refresh Bugs page')+'</button>':'')+'</div>';
 if(!live)html+='<p class="insight-note">Use the desktop Plugin audit control for a fresh scan of the imported profile. Live Nexus health uses the MO2 plugin.</p>';
 if(running)html+='<p class="insight-note" role="status">Rust is scanning in the background. You can keep using Modhunter.</p>';
 if(auditState.error)html+='<p class="insight-alert">'+esc(auditState.error)+'</p>';
 if(health.job?.state==='failed')html+='<p class="insight-alert">Nexus check: '+esc(health.job.error)+'</p>';
 if(s.needsRecheck)html+='<p class="insight-alert">The profile changed. Recheck before using older record or conversion conclusions.</p>';
 if(offline&&!live)html+=historicalDetails(mod.historicalNexus);
 html+=fileDetails(mod,game);
 const actions=list(health.actions).filter(x=>x.severity==='warning'||['review-update','review-page-change'].includes(x.id));
 if(actions.length)html+='<ul class="insight-next">'+actions.slice(0,8).map(x=>'<li><strong>'+esc(x.label)+'</strong><span>'+esc(x.detail)+'</span></li>').join('')+'</ul>';
 html+='<div class="insight-section"><h4>Plugin copies & load order</h4><p class="insight-note">'+esc(known?(a.checkedAt?'Checked '+checked(a.checkedAt):'Header facts from the profile import')+' · '+(deep?'records and fresh file hashes':'conversion eligibility needs a record scan'):'No local plugin analysis for this mod yet.')+'</p>';
 if(!plugins.length)html+='<p class="insight-note">'+esc(a.inventoryComplete===true?'No root plugin files were found in this mod folder.':'No owned plugin copies have been established yet. A losing copy may still be present in the mod folder.')+'</p>';
 for(const p of plugins.slice(0,256)){
  const eligibility=p.eligibility||{},provenance=p.provenance||{},header=p.header||{};
  const isLight=header.lightEffective===true,format=isLight?'Light':header.lightEffective===false?'Full':'Format unknown';
  const active=p.active===true?'Active':p.active===false?'Inactive':'Activation unknown';
  const winner=p.winnerKnown===false?'Winning origin unverified':p.winner?'This copy wins':p.winnerModId?'Overridden copy':'Winner unverified';
  const candidate=eligibility.status==='flag-only-candidate';
  html+='<details class="plugin-fact" data-plugin-name="'+esc(p.name)+'"'+(staleStates.has(provenance.state)||list(p.masterIssues).length||eligibility.status==='blocked'?' open':'')+'><summary><strong>'+esc(p.name)+'</strong><span>'+esc(format+' · '+active)+'</span></summary><dl><dt>MO2 copy</dt><dd>'+esc(winner)+(p.winnerModId&&!p.winner?' · '+related(p.winnerModId,p.winnerModName||(p.winnerModId==='__overwrite__'?'MO2 Overwrite':p.winnerModId==='__game_data__'?'Game Data':'Reveal winning mod')):'')+'</dd><dt>Load order</dt><dd>'+esc(p.loadOrder===null||p.loadOrder===undefined?'Not established':p.loadOrder)+'</dd><dt>ESL assessment</dt><dd>'+esc(s.needsRecheck&&candidate?'Needs a fresh record scan':eligibilityLabels[eligibility.status]||'Eligibility unverified')+'</dd><dt>ESLifier</dt><dd>'+esc(s.needsRecheck&&provenance.state==='current'?'Conversion history needs a fresh hash check':provenanceLabels[provenance.state]||'Conversion history unverified')+'</dd></dl>';
  if(p.lightOverride)html+='<p class="insight-note">A light copy overrides this original. Matching names and a light header alone do not establish which tool converted it.</p>';
  const masterIssues=list(p.masterIssues);
  if(masterIssues.length)html+='<ul class="insight-problems">'+masterIssues.map(x=>'<li>'+esc(x.name)+' · '+esc(x.status==='loads-after'?'loads after this plugin':x.status)+'</li>').join('')+'</ul>';
  if(list(header.masters).length)html+='<p class="insight-note">Masters: '+list(header.masters).map(esc).join(', ')+'</p>';
  const notes=[...list(eligibility.notes),...list(provenance.notes).map(n=>s.needsRecheck?'Previous scan: '+n:n),...list(p.errors)];
  if(provenance.reason)notes.push((s.needsRecheck?'Previous scan only: ':'')+provenance.reason);
  if(provenance.reasonCodes?.length&&!list(provenance.notes).length)notes.push(provenance.reasonCodes.join(', ').replace(/-/g,' '));
  if(notes.length)html+='<ul class="insight-evidence">'+notes.slice(0,14).map(n=>'<li>'+esc(n)+'</li>').join('')+'</ul>';
  if(p.fingerprint?.sha256)html+='<p class="insight-hash">'+(s.needsRecheck?'Previous scan SHA256':'Current scan SHA256')+' <code>'+esc(p.fingerprint.sha256)+'</code></p>';
  html+='</details>';
 }
 html+='<p class="insight-note">An ESLifier match covers the scanned plugin bytes; patched scripts, facegen, voices and other assets are not verified. A flag-only candidate still needs xEdit / ESLifier review. Compaction can affect saves, dependent plugins, numeric script references, DLLs and named assets. Modhunter does not convert or rewrite plugins.</p></div>';
 html+='<div class="insight-section"><h4>Project & bug reports</h4><dl><dt>Source page</dt><dd>'+esc(health.page?.status||'Unknown')+'</dd><dt>Source check</dt><dd>'+esc(checked(health.page?.checkedAt))+(health.page?.freshness?.state==='stale'?' · update scan needed':'')+'</dd><dt>'+esc(isNexus(mod)?'Nexus Bugs tab':'Issue reporting')+'</dt><dd>'+esc(bugLabel(bugs))+'</dd><dt>Bug check</dt><dd>'+esc(checked(bugs.checkedAt))+(bugs.freshness?.state==='stale'?' · refresh needed':'')+'</dd></dl><p class="insight-note">'+esc(bugs.reason||'No public bug-tab evidence has been checked.')+'</p>';
 if(health.page?.source==='nexus-api')html+='<p class="insight-note">Project availability comes from the last Nexus update audit. Refresh Bugs page checks the public bug tab separately.</p>';
 if(Number.isSafeInteger(bugs.displayedCount)&&bugs.displayedCount>=0)html+='<p class="insight-note">Nexus displayed tab count: '+esc(bugs.displayedCount)+'. Open, resolved and total counts are not established by this badge. Reports can concern other versions or setups.</p>';
 html+='<div class="insight-links">'+link(links.bugs,'Bugs / issues')+link(links.requirements,'Requirements')+link(links.changelog,'Changelog')+link(links.files,'Files')+'</div></div>';
 const advisories=health.securityAdvisories;
 if(advisories){
  html+='<div class="insight-section"><h4>Published security advisories</h4><p>'+esc(advisories.repository)+' · '+esc(advisoryState(advisories))+' · '+esc(checked(advisories.checkedAt))+'</p>';
  if(advisoryState(advisories)==='checked'&&Array.isArray(advisories.findings)&&!advisories.findings.length)html+='<p>No published repository advisories were returned at the last check.</p>';
  for(const n of list(advisories.findings)){
   const applicability=({'affected':'Installed package version affected','outside-range':'Installed package version outside the published range','unverified':'Installed package or version applicability unverified','publisher-retracted':'Publisher retracted this security finding'})[n.applicability];
   html+='<div class="plugin-fact"><strong>'+link(n.url,n.id)+'</strong> '+esc(n.title||'')+'<p>'+esc(n.severity||'Severity unknown')+' · '+esc(n.publishedAt||'Publication date unknown')+'</p><p>'+esc(n.package||'Package unspecified')+' · '+esc(n.affectedRange||'Affected range unspecified')+' · '+esc(applicability||'Unverified')+'</p>'+(n.patchedVersions?'<p>Patched versions '+esc(n.patchedVersions)+'</p>':'')+'</div>';
  }
  html+='</div>';
 }
 if(list(a.warnings).length)html+='<ul class="insight-evidence">'+a.warnings.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul>';
 return html+'</section>';
}
// A toolbar filter answers only its selected question. Rendering matched
// rows and the inspector still uses the full plugin counts and evidence.
function matches(mod,filter){
 if(filter==='all')return true;
 if(filter==='bugs-disabled'||filter==='bugs-unknown'){
  const bug=mod.modHealth?.bugs?.state||'unknown';
  return filter==='bugs-disabled'?(bug==='disabled'||bug==='hidden'||bug==='locked'):(bug==='unknown'||bug==='unavailable');
 }
 const analysis=mod.localAnalysis,plugins=list(analysis?.plugins);
 if(filter==='light')return plugins.some(p=>p.active!==false&&(p.winner&&p.header?.lightEffective===true||p.lightOverride&&p.winnerKnown&&p.winnerLight===true));
 if(filter==='stale-conversion')return plugins.some(p=>staleStates.has(p.provenance?.state));
 if(filter==='masters')return plugins.some(p=>list(p.masterIssues).length>0);
 if(filter==='security')return affectedAdvisory(mod);
 if(filter==='file-review')return fileReview(mod);
 if(filter==='maintenance')return affectedAdvisory(mod)||analysis?.needsRecheck===true||
  ['hidden','removed','under-moderation','not-found','unavailable'].includes(mod.modHealth?.page?.status)||
  plugins.some(p=>staleStates.has(p.provenance?.state)||list(p.masterIssues).length>0||list(p.errors).length>0||
   p.eligibility?.status==='blocked'||p.records?.scanComplete===false||p.recordScanComplete===false);
 if(filter==='esl-review')return analysis?.needsRecheck===true||
  plugins.some(p=>staleStates.has(p.provenance?.state)||p.eligibility?.status==='flag-only-candidate'||
   p.eligibility?.status==='requires-compaction'||p.eligibility?.status==='blocked'||
   p.records?.scanComplete===false||p.recordScanComplete===false||p.winnerKnown===false);
 return false;
}
function searchText(mod){const s=summary(mod);return[list(mod.localAnalysis?.plugins).map(p=>[p.name,p.eligibility?.status,p.provenance?.state,...list(p.masterIssues).map(x=>x.name+' '+x.status)].join(' ')).join(' '),s.security?'security advisory affected':'',s.fileReview?'installed file review':'',mod.update?.installedFileId,mod.update?.targetFileId,s.light?'already light esl':'',s.stale?'stale conversion eslifier update rebuild':'',s.lightOverrides?'light override eslified':'',bugLabel(mod.modHealth?.bugs),mod.modHealth?.page?.status].join(' ').toLowerCase()}
function csv(mod){const s=summary(mod),known=!!mod.localAnalysis;return[known?s.light:'',known?s.full:'',known?s.lightOverrides:'',known?s.candidates:'',known?s.compaction:'',known?s.stale:'',known?s.masterIssues:'',mod.localAnalysis?.checkedAt||'',s.bugState,s.bugCount??'',mod.modHealth?.bugs?.checkedAt||'']}
const api={historicalBadge,historicalSummary,advisoryState,summary,chips,details,matches,searchText,csv,bugLabel,isNexus};
root.ModhunterInsights=api;if(typeof module==='object'&&module.exports)module.exports=api;
})(typeof window==='object'?window:globalThis);
