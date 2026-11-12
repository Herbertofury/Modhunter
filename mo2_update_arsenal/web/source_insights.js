/* Provider evidence presentation. All remote text is inert; downloads use server-owned IDs. */
(function(root){
'use strict';
const text=value=>String(value??'');
const list=value=>Array.isArray(value)?value:[];
const esc=value=>text(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const names={nexus:'Nexus Mods',github:'GitHub',gitlab:'GitLab',modrinth:'Modrinth',thunderstore:'Thunderstore',curseforge:'CurseForge',patreon:'Patreon',loverslab:'LoversLab',moddb:'Mod DB',bethesda:'Bethesda Creations',creations:'Bethesda Creations',dwemer:'Dwemer Mods','modding-guild':'Modding Guild','ko-fi':'Ko-fi',boosty:'Boosty',afdian:'Afdian',tumblr:'Tumblr',subscribestar:'SubscribeStar',modcollective:'Mod Collective',simsfinds:'SimsFinds',itch:'itch.io','itch.io':'itch.io',steam:'Steam Workshop',steamworkshop:'Steam Workshop',discord:'Discord',website:'Original website',unknown:'Unlinked source'};
const safeURL=value=>{try{const u=new URL(value);return u.protocol==='https:'&&!u.username&&!u.password&&!u.port&&!/^(localhost|127\.|0\.|\[)/i.test(u.hostname)?u.href:''}catch{return''}};
const link=(url,label)=>{const href=safeURL(url);return href?'<a href="'+esc(href)+'" target="_blank" rel="noopener noreferrer">'+esc(label)+' ↗</a>':''};
const number=value=>Number.isSafeInteger(value)&&value>=0?value:null;
const compatibilityAlerts={incompatible:'Game / loader mismatch',mismatch:'Game / loader mismatch','game-mismatch':'Game / loader mismatch','context-required':'Game / loader context needed','community-required':'Community review needed',deprecated:'Deprecated package',unsupported:'Unsupported game context'};
const compatibilityLabels={compatible:'Declared game / loader match',matched:'Declared game / loader match','community-matched':'Declared community match',unknown:'Compatibility not established','not-declared':'Compatibility not declared',review:'Compatibility review required',...compatibilityAlerts};
function provider(mod){return text(mod.update?.provider||mod.provider||mod.source?.type||(Number(mod.nexusId)>0?'nexus':'unknown')).toLowerCase()}
function label(value){return names[value]||text(value||'Original source').replace(/[-_]/g,' ')}
function checked(value){if(value===null||value===undefined||value==='')return'Not checked';const d=new Date(typeof value==='number'?value*1000:value);return Number.isFinite(d.getTime())?d.toLocaleString():'Time unavailable'}
function hasAssets(mod){const update=mod.update||mod;return (number(update.releaseAssetCount)??list(update.releaseAssets).length)>0}
function canReview(mod){const s=state(mod);return hasAssets(mod)&&mod.update?.verdict==='candidate'&&!s.stale&&!s.rateLimited&&!compatibilityAlerts[mod.update?.compatibility?.status]}
function state(mod,now=Date.now()/1000){
 const u=mod.update||{},caps=u.capabilities||mod.sourceCapabilities||(provider(mod)==='nexus'?{checkMode:'native-api',reviewedDownload:true,authentication:'mo2',reason:'Uses the connected MO2 Nexus integration.'}:{});
 const stale=u.freshness==='stale'||(Number(u.expiresAt)>0&&Number(u.expiresAt)<=now);
 const rateLimited=u.availability==='rate-limited'||Number(u.retryAt)>now;
 let status=stale?'Refresh needed':rateLimited?'Provider cooldown':({fresh:'Fresh API evidence',cached:'Cached API evidence',revalidated:'Revalidated API evidence',unverified:'Not verified'})[u.freshness]||'Not checked';
 if(!stale&&!rateLimited&&u.outcome==='unavailable')status='Source unavailable';
 if(!stale&&!rateLimited&&['manual','api-key'].includes(caps.checkMode))status=caps.checkMode==='api-key'?'Provider key required':'Source-page review';
 const compatibilityAlert=compatibilityAlerts[u.compatibility?.status];
 if(compatibilityAlert)status=compatibilityAlert;
 const tone=stale||rateLimited||compatibilityAlert?'warning':u.outcome==='unavailable'?'danger':u.freshness&&u.freshness!=='unverified'?'positive':'muted';
 return {provider:provider(mod),caps,stale,rateLimited,status,tone,assets:number(u.releaseAssetCount)??list(u.releaseAssets).length,assetsKnown:number(u.releaseAssetCount)!==null||Array.isArray(u.releaseAssets),dependencies:number(u.dependencyCount)??list(u.dependencies).length,dependenciesKnown:number(u.dependencyCount)!==null||Array.isArray(u.dependencies)};
}
function chips(mod){
 const u=mod.update||{},s=state(mod);
 if(!u.capabilities&&!u.freshness&&!u.releaseId&&!u.outcome&&!u.checkedAt&&!u.expiresAt&&!u.retryAt)return'';
 const detail=s.caps.reason||u.reason||'Open source details for evidence and supported actions.';
 const tag=(value,tone,title)=>'<button type="button" class="insight-chip '+tone+'" data-source-act="inspect" data-id="'+esc(mod.id)+'" title="'+esc(title)+'">'+esc(value)+'</button>';
 return '<div class="mod-insights source-chips" aria-label="Source adapter evidence">'+tag(label(s.provider),'neutral',detail)+tag(s.status,s.tone,u.reason||s.status)+(s.dependencies?tag(s.dependencies+' declared dependenc'+(s.dependencies===1?'y':'ies'),'info','Provider declarations; installed dependency compatibility requires review.'):'')+'</div>';
}
function person(value,role){
 if(!value)return'';
 const p=typeof value==='string'?{name:value}:value;
 const name=p.name||p.login||p.username||p.id;
 if(!name)return'';
 return '<div class="source-person"><span>'+esc(role)+'</span><strong>'+(link(p.url||p.profileUrl,name)||esc(name))+'</strong>'+(p.avatarUrl?'<span>'+link(p.avatarUrl,'Profile image')+'</span>':'')+'</div>';
}
function hashes(asset){return Object.entries(asset.hashes||{}).filter(([algorithm,value])=>typeof value==='string'&&value.length===({sha256:64,sha512:128,sha1:40,md5:32})[algorithm.toLowerCase()]&&/^[a-f0-9]+$/i.test(value))}
function assetIdentity(asset){const id=Number(asset.assetId);return {assetKey:typeof asset.assetKey==='string'?asset.assetKey:'',assetProof:/^[a-f0-9]{64}$/i.test(asset.assetProof||'')?asset.assetProof:'',...(Number.isSafeInteger(id)&&id>0?{assetId:id}:{})}}
function assetCards(mod){
 const u=mod.update||{},p=provider(mod);
 return list(u.releaseAssets).slice(0,256).map(asset=>{
  const identity=assetIdentity(asset),digest=hashes(asset),size=number(asset.size);
  if(!identity.assetKey&&!identity.assetId)return'';
  const evidence=[label(asset.provider||p),size===null?'Size unavailable':new Intl.NumberFormat().format(size)+' bytes'];
  if(asset.primary===true)evidence.push('Provider primary file');
  return '<div class="picker-file"><div class="picker-file-main"><strong>'+esc(asset.name||'Release archive')+'</strong><span>'+esc(evidence.join(' · '))+'</span>'+(asset.contentType?'<span>'+esc(asset.contentType)+'</span>':'')+(digest.length?'<details class="source-file-hashes"><summary>Published checksums</summary>'+digest.map(([k,v])=>'<p>'+esc(k.toUpperCase())+' <code>'+esc(v)+'</code></p>').join('')+'<p>Provider metadata. Downloaded bytes still need verification.</p></details>':'<p class="picker-hint">No checksum published in this release record.</p>')+'<p class="picker-hint">'+esc(u.compatibility?.reason||'Review game runtime, variant and required dependencies.')+'</p></div><button class="button-bright" data-file-id="'+esc(identity.assetId||'')+'" data-asset-key="'+esc(identity.assetKey)+'" data-asset-proof="'+esc(identity.assetProof)+'" data-archive-name="'+esc(asset.name)+'" data-source-kind="release" data-source-provider="'+esc(asset.provider||p)+'">Select release file</button></div>';
 }).join('');
}
function details(mod,{live=false,loading=false,error=''}={}){
 const u=mod.update||{},s=state(mod),caps=s.caps;
 const mode=({'native-api':'MO2 native API','public-api':'Public release API','manual':'Original page review','api-key':'Provider API key required'})[caps.checkMode]||'Run a source check';
 let html='<section class="insight-panel source-panel"><div class="insight-heading"><div><span class="drawer-label">SOURCE & RELEASE</span><h3>'+esc(label(s.provider))+'</h3></div><span class="insight-depth">'+esc(s.status)+'</span></div>';
 html+='<div class="source-release-grid"><div><small>Installed</small><strong>'+esc(mod.installedVersion||'Unknown')+'</strong></div><div><small>Published release</small><strong>'+esc(u.latestVersion||'Not established')+'</strong></div></div>';
 if(loading)html+='<p class="insight-note" role="status">Loading release details…</p>';
 if(error)html+='<p class="insight-alert">'+esc(error)+'</p>';
 html+='<p class="insight-note">'+esc(u.reason||caps.reason||'Check this exact source to establish its release information.')+'</p>';
 html+='<dl class="source-facts"><dt>Adapter</dt><dd>'+esc(mode)+'</dd><dt>Source checked</dt><dd>'+esc(checked(u.checkedAt))+'</dd><dt>Release published</dt><dd>'+esc(checked(u.publishedAt))+'</dd><dt>Download support</dt><dd>'+esc(caps.reviewedDownload?'Reviewed provider files → MO2':caps.authentication==='mo2'?'MO2 native download manager':'Open the original provider')+'</dd>'+(u.moderationStatus?'<dt>Provider moderation</dt><dd>'+esc(u.moderationStatus)+' · provider listing status; not a compatibility assessment</dd>':'')+'</dl>';
 if(u.retryAt)html+='<p class="insight-note">Provider retry time: '+esc(checked(u.retryAt))+'.</p>';
 if(u.summary)html+='<p class="source-summary">'+esc(text(u.summary).slice(0,4000))+'</p>';
 html+='<div class="source-people">'+person(u.author,'Author')+person(u.publisher,'Release publisher')+'</div>';
 const media=list(u.media).filter(x=>x&&safeURL(x.url));
 if(u.iconUrl||media.length)html+='<details class="source-disclosure" data-source-section="media"><summary>Project images & media</summary><div class="insight-links">'+link(u.iconUrl,'Project icon')+media.slice(0,6).map((x,i)=>link(x.url,x.caption||'Project '+(x.type||'media')+' '+(i+1))).join('')+'</div></details>';
 const compatibility=u.compatibility||{};
 if(compatibility.status||compatibility.reason)html+='<div class="source-compatibility"><strong>'+esc(compatibilityLabels[compatibility.status]||'Compatibility review')+'</strong><p>'+esc(compatibility.reason||'Review the exact file for this profile.')+'</p>'+(list(compatibility.gameVersions).length?'<p>Game versions: '+list(compatibility.gameVersions).slice(0,40).map(esc).join(', ')+'</p>':'')+(list(compatibility.loaders).length?'<p>Loaders: '+list(compatibility.loaders).slice(0,20).map(esc).join(', ')+'</p>':'')+'</div>';
 const dependencies=list(u.dependencies);
 if(dependencies.length)html+='<details class="source-disclosure" data-source-section="dependencies"><summary>'+dependencies.length+' declared dependenc'+(dependencies.length===1?'y':'ies')+'</summary><ul>'+dependencies.slice(0,128).map(d=>'<li><strong>'+(link(d.url,d.name||d.projectId||d.versionId||'Dependency')||esc(d.name||d.projectId||d.versionId||'Unresolved dependency'))+'</strong><span>'+esc(d.kind||'Requirement')+(d.versionId?' · version '+esc(d.versionId):'')+'</span></li>').join('')+'</ul><p class="insight-note">These are provider declarations. They do not establish installed versions or satisfy plugin-master checks.</p></details>';
 else if(s.dependencies)html+='<p class="insight-note">'+s.dependencies+' declared dependencies; open details to inspect them.</p>';
 if(u.changelog)html+='<details class="source-disclosure" data-source-section="changelog"><summary>Release notes / changelog</summary><pre class="source-changelog">'+esc(text(u.changelog).slice(0,24000))+'</pre><p class="insight-note">Text supplied by the provider. Open the release for the original formatting and full content.</p></details>';
 if(s.assets)html+='<p class="insight-note">'+s.assets+' published file'+(s.assets===1?'':'s')+' in this release.</p>';
 html+='<div class="insight-links">'+link(u.releaseUrl,'Release & changelog')+link(mod.source?.url,'Original project')+'</div>';
 if(live)html+='<div class="insight-actions"><button type="button" data-source-act="scan" data-id="'+esc(mod.id)+'">Check mod sources</button>'+(canReview(mod)?'<button type="button" data-source-act="files" data-id="'+esc(mod.id)+'">Review release files</button>':'')+'</div>';
 if(!live)html+='<p class="insight-note">Connect the MO2 plugin for source checks and reviewed downloads.</p>';
 return html+'</section>';
}
function coverage(items){
 const groups=new Map();
 for(const mod of list(items)){
  const s=state(mod),key=s.provider+'|'+(s.caps.checkMode||'unverified');
  const row=groups.get(key)||{provider:s.provider,caps:s.caps,count:0,stale:0};row.count++;if(s.stale)row.stale++;groups.set(key,row);
 }
 const rows=[...groups.values()].sort((a,b)=>b.count-a.count||a.provider.localeCompare(b.provider));
 return '<div class="source-coverage-table"><table><thead><tr><th scope="col">Source</th><th scope="col">Mods</th><th scope="col">Update checks</th><th scope="col">File workflow</th></tr></thead><tbody>'+rows.map(r=>'<tr><th scope="row">'+esc(label(r.provider))+'</th><td>'+r.count+(r.stale?' · '+r.stale+' stale':'')+'</td><td>'+esc(({'native-api':'MO2 native API','public-api':'Public release API','manual':'Original page review','api-key':'Provider key required'})[r.caps.checkMode]||'Check sources to establish support')+'</td><td>'+esc(r.caps.reviewedDownload?'Reviewed files in MO2':r.caps.authentication==='mo2'?'MO2 download manager':'Original provider page')+'</td></tr>').join('')+'</tbody></table></div>';
}
function matches(mod,filter){const s=state(mod);return filter==='source-stale'?s.stale||s.rateLimited:filter==='source-manual'?['manual','api-key'].includes(s.caps.checkMode):filter==='source-dependencies'?s.dependencies>0:filter==='source-incompatible'?!!compatibilityAlerts[mod.update?.compatibility?.status]:filter==='all'}
function searchText(mod){const u=mod.update||{},s=state(mod);return [s.provider,label(s.provider),s.status,u.latestVersion,u.releaseId,u.availability,u.compatibility?.status,u.author?.name,u.publisher?.name,...list(u.dependencies).slice(0,128).map(d=>d.name||d.projectId||d.versionId)].map(text).join(' ').toLowerCase()}
const csvHeaders=['Release provider','Release version','Release ID','Release URL','Release published at','Source checked at','Source expires at','Source freshness','Source availability','Adapter mode','Provider compatibility','Declared dependencies','Release files'];
function csv(mod){const u=mod.update||{},s=state(mod);return [s.provider,u.latestVersion||'',u.releaseId||'',safeURL(u.releaseUrl),u.publishedAt||'',u.checkedAt||'',u.expiresAt||'',u.freshness||'',u.availability||'',s.caps.checkMode||'',u.compatibility?.status||'',s.dependenciesKnown?s.dependencies:'',s.assetsKnown?s.assets:'']}
const api={provider,label,checked,state,hasAssets,canReview,chips,details,coverage,searchText,matches,csv,csvHeaders,assetCards,assetIdentity,safeURL};
root.ModhunterSources=api;if(typeof module==='object'&&module.exports)module.exports=api;
})(typeof window==='object'?window:globalThis);
