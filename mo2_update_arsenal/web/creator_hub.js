/* Creator Hub presentation. No network, persistence or account side effects. */
(function(root) {
'use strict';
const escape=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const fold=value=>String(value??'').normalize('NFKD').replace(/\p{Mark}/gu,'').toLocaleLowerCase();
const providers={nexus:'Nexus Mods',modhold:'ModHold',modrinth:'Modrinth',github:'GitHub',gitlab:'GitLab',thunderstore:'Thunderstore'};
const provider=value=>providers[value]||String(value||'Creator');
function safeURL(value) {
 try {
  if(typeof value!=='string'||/[\u0000-\u0020\u007f\\]/.test(value))return '';
  const url=new URL(value);
  if(url.protocol!=='https:'||url.username||url.password||url.port||!url.hostname.includes('.')||/^[\d.]+$|[:\[\]]/.test(url.hostname)||/(^|\.)(localhost|local|internal|test|invalid)$/.test(url.hostname)||url.href.length>2048)return '';
  for(const key of url.searchParams.keys())if(/token|secret|auth|password|key|signature|session/i.test(key))return '';
  url.hash='';return url.href;
 } catch {return ''}
}
function link(url,label,cls='') {
 const safe=safeURL(url);
 return safe?'<a class="'+escape(cls)+'" href="'+escape(safe)+'" target="_blank" rel="noopener noreferrer">'+escape(label)+' <span aria-hidden="true">↗</span></a>':'';
}
function time(value,now=Date.now()/1000) {
 const stamp=Number(value),seconds=Math.max(0,now-stamp);
 if(!Number.isFinite(stamp)||stamp<=0)return 'Not checked yet';
 if(seconds<60)return 'Just now';
 if(seconds<3600)return Math.floor(seconds/60)+' min ago';
 if(seconds<86400)return Math.floor(seconds/3600)+' hr ago';
 if(seconds<7*86400)return Math.floor(seconds/86400)+' days ago';
 return new Date(stamp*1000).toLocaleDateString(undefined,{month:'short',day:'numeric'});
}
function projects(creator){return Array.isArray(creator.projects)?creator.projects:[]}
function unread(creator){return Number.isSafeInteger(creator.unreadCount)&&creator.unreadCount>0?creator.unreadCount:0}
function attention(creator){
 const notice=creator.notice,normal=notice&&typeof notice==='object'&&['active','not-marked-banned'].includes(notice.state);
 return Boolean((notice&&!normal)||creator.error||creator.catalogError||creator.migrationHold||creator.continuity?.affected||creator.needsReview);
}
function avatar(creator) {
 const initials=String(creator.name||'?').trim().split(/[\s_-]+/).slice(0,2).map(word=>[...word][0]||'').join('').toLocaleUpperCase();
 const url=safeURL(creator.avatarUrl);
 const allowed=url&&/^(avatars\.nexusmods\.com|modholdmods\.com|cdn\.modrinth\.com|avatars\.githubusercontent\.com)$/.test(new URL(url).hostname);
 return '<span class="hub-avatar" aria-hidden="true"><span>'+escape(initials)+'</span>'+(allowed?'<img src="'+escape(url)+'" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer">':'')+'</span>';
}
const indexCache=new WeakMap();
function index(snapshot) {
 if(indexCache.has(snapshot))return indexCache.get(snapshot);
 const creators=Array.isArray(snapshot.creators)?snapshot.creators:[],review=Array.isArray(snapshot.review)?snapshot.review:[];
 const entries=creators.map(creator=>({creator,query:fold([creator.name,creator.provider,provider(creator.provider),...projects(creator).map(project=>project.title)].join(' '))}));
 const counts={following:0,updates:0,attention:0,paused:0,unfollowed:0,unread:0,projects:0,review:review.length};
 const sourceCounts=new Map();
 for(const creator of creators) {
  const state=creator.state||'following';if(state==='following')counts.following++;
  if(state==='paused')counts.paused++;if(state==='unfollowed')counts.unfollowed++;
  if(unread(creator)>0&&state!=='unfollowed')counts.updates++;
  if(attention(creator)&&state!=='unfollowed')counts.attention++;
  counts.unread+=state==='unfollowed'?0:unread(creator);counts.projects+=projects(creator).length;
  sourceCounts.set(creator.provider,(sourceCounts.get(creator.provider)||0)+1);
 }
 for(const record of review)if(record.provider)sourceCounts.set(record.provider,(sourceCounts.get(record.provider)||0)+1);
 const result={entries,counts,sourceCounts,review};indexCache.set(snapshot,result);return result;
}
function view(snapshot,{query='',filter='following',source='all',sort='recent',page=0,pageSize=24}={}) {
 const data=index(snapshot),needle=fold(query.trim());
 const filtered=data.entries.filter(({creator,query:haystack})=>{
  const state=creator.state||'following';
  if(source!=='all'&&creator.provider!==source)return false;
  if(needle&&!haystack.includes(needle))return false;
  if(filter==='all')return state!=='unfollowed';
  if(filter==='updates')return state!=='unfollowed'&&unread(creator)>0;
  if(filter==='attention')return state!=='unfollowed'&&attention(creator);
  return state===filter;
 }).map(entry=>entry.creator);
 filtered.sort((a,b)=>{
  if(sort==='name')return String(a.name).localeCompare(String(b.name),undefined,{sensitivity:'base'})||String(a.id).localeCompare(String(b.id));
  if(sort==='followed')return (Number(b.followedAt)||0)-(Number(a.followedAt)||0)||String(a.id).localeCompare(String(b.id));
  return unread(b)-unread(a)||(Number(b.lastUpdateAt||b.followedAt)||0)-(Number(a.lastUpdateAt||a.followedAt)||0)||String(a.name).localeCompare(String(b.name));
 });
 const size=Math.max(1,Math.min(100,Math.floor(pageSize)||24)),pages=Math.max(1,Math.ceil(filtered.length/size));
 const current=Math.max(0,Math.min(pages-1,Math.floor(page)||0));
 return {...data,filtered,items:filtered.slice(current*size,(current+1)*size),total:filtered.length,page:current,pages,pageSize:size};
}
function card(creator,{selected=false,busy=false,now=Date.now()/1000}={}) {
 const count=projects(creator).length,newItems=unread(creator),state=creator.state||'following';
 const origin=creator.origin==='download'?'From a download':creator.origin==='installed'?'From your library':'Added by you';
 const badge=state==='paused'?'Paused':state==='unfollowed'?'Unfollowed':newItems?newItems+' new':attention(creator)?'Needs attention':'Following';
 const titles=projects(creator).slice(0,2).map(project=>project.title||'Linked project');
 return '<article class="hub-card'+(selected?' is-selected':'')+(newItems?' has-updates':'')+'" data-hub-card="'+escape(creator.id)+'">'+
  '<button type="button" class="hub-card-main" data-hub-select="'+escape(creator.id)+'" aria-pressed="'+selected+'"'+(busy?' disabled':'')+'>'+
  '<span class="hub-card-head">'+avatar(creator)+'<span class="hub-card-identity"><strong data-no-translate>'+escape(creator.name||'Creator')+'</strong><small>'+escape(provider(creator.provider)+(creator.role?' · '+creator.role:''))+'</small></span><span class="hub-state '+(newItems?'is-new':'')+'">'+escape(badge)+'</span></span>'+
  '<span class="hub-card-projects">'+titles.map(title=>'<span data-no-translate>'+escape(title)+'</span>').join('')+(count>2?'<small>+ '+(count-2)+' more projects</small>':'')+(count===0?'<span>Creator profile followed</span>':'')+'</span>'+
  '<span class="hub-card-foot"><span>'+escape(origin)+'</span><span>'+escape(time(creator.lastCheckedAt,now))+'</span></span></button></article>';
}
function detail(creator,{busy=false,live=false,projectLimit=20,now=Date.now()/1000}={}) {
 if(!creator)return '<div class="hub-detail-placeholder"><span class="hub-detail-orbit" aria-hidden="true">✦</span><h3>Your next favorite update.</h3><p>Select a creator to see their projects, recent releases, and follow settings.</p></div>';
 const disabled=busy||!live?' disabled':'',state=creator.state||'following',list=projects(creator),visible=list.slice(0,projectLimit);
 const button=(action,label,cls='quiet-button')=>'<button type="button" class="'+cls+'" data-hub-action="'+action+'" data-hub-creator="'+escape(creator.id)+'"'+disabled+'>'+label+'</button>';
 let html='<div class="hub-detail-identity">'+avatar(creator)+'<div><span class="hub-overline">'+escape(provider(creator.provider)+(creator.role?' · '+creator.role:''))+'</span><h3 data-no-translate>'+escape(creator.name||'Creator')+'</h3>'+link(creator.profileUrl,'Open creator profile','hub-profile-link')+'</div></div>';
 html+='<div class="hub-detail-metrics"><span><b>'+list.length+'</b> linked projects</span><span><b>'+unread(creator)+'</b> unseen updates</span></div>';
 html+='<div class="hub-detail-actions">';
 if(state==='unfollowed')html+=button('restore','Follow again','button-bright');
 else html+=button('check','Check now','button-bright')+button(state==='paused'?'resume':'pause',state==='paused'?'Resume':'Pause')+button('mark-seen','Mark seen')+button('unfollow','Unfollow');
 html+='</div>';
 if(state==='unfollowed')html+='<p class="hub-explanation">Automatic downloads respect this choice. Follow again when you want this creator back.</p>';
 else if(state==='paused')html+='<p class="hub-explanation">Creator monitoring is paused. Your installed mods and their update checks stay available.</p>';
 else html+='<p class="hub-explanation">'+escape(creator.origin==='download'?'Followed automatically after a completed download.':creator.origin==='installed'?'Discovered from verified metadata in your installed library.':'You chose to follow this creator.')+' Last checked '+escape(time(creator.lastCheckedAt,now))+'.</p>';
 if(creator.coverage)html+='<p class="hub-explanation"><strong>What you follow:</strong> '+escape(creator.coverage)+'</p>';
 if(creator.catalogError)html+='<div class="hub-notice"><strong>Creator catalog needs a retry</strong><p>'+escape(creator.catalogError)+' Previous discoveries are retained.</p></div>';
 if(creator.migrationHold)html+='<div class="hub-notice"><strong>Project identity needs review</strong><p>'+escape(creator.migrationHold.reason||'The project now identifies a different creator. The saved follow and original source are retained.')+'</p>'+link(creator.migrationHold.sourceUrl,'Review project identity')+'</div>';
 const notice=creator.notice||creator.error;
 if(notice){
  const state=notice.state,normal=['active','not-marked-banned'].includes(state),title=state==='banned'?'Account marked banned':state==='not-marked-banned'?'No ban badge observed':normal?'Account status checked':'Review creator evidence';
  html+='<div class="hub-notice'+(normal?' hub-notice-info':'')+'"><strong>'+title+'</strong><p>'+escape(typeof notice==='string'?notice:notice.message||notice.reason||'A source or identity change needs review.')+'</p>'+(notice.checkedAt?'<p>Observed '+escape(new Date(notice.checkedAt*1000).toLocaleString())+'</p>':'')+link(notice.evidenceUrl||notice.profileUrl,'Open evidence')+'</div>';
 }
 if(creator.lastStatusAttempt?.verified===false)html+='<p class="hub-explanation">The latest creator-status check could not be verified. Earlier evidence is retained.</p>';
 const affected=Number.isSafeInteger(creator.continuity?.affected)?creator.continuity.affected:0;
 if(affected>0)html+='<div class="hub-notice"><strong>Creator Continuity</strong><p>'+affected+' linked '+(affected===1?'project has':'projects have')+' dated author or source notices. Open mod details to review the evidence and any new destination.</p></div>';
 html+='<div class="hub-project-heading"><h4>Projects &amp; releases</h4><span>'+list.length+'</span></div><div class="hub-project-list">';
 for(const project of visible) {
  const title=project.title||'Linked project',latest=project.latestVersion||'',installed=project.installedVersion||'';
  html+='<article class="hub-project"><div><strong data-no-translate>'+escape(title)+'</strong><span class="hub-project-version">'+escape(latest?'Latest '+latest:installed?'Installed '+installed:'Version check pending')+(latest&&installed?' <span>· installed '+escape(installed)+'</span>':'')+'</span>'+(project.origin==='creator-catalog'?'<span class="hub-project-origin">From the creator’s catalog · not installed</span>':'')+'</div><div class="hub-project-actions">'+link(project.url,'Project')+(project.itemId?'<button type="button" class="hub-text-button" data-hub-mod="'+escape(project.itemId)+'">Mod details</button>':'')+'</div></article>';
 }
 if(!list.length)html+='<p class="hub-explanation">Projects appear when their exact source is linked to this creator.</p>';
 html+='</div>';
 if(list.length>visible.length)html+='<button class="quiet-button hub-more" type="button" data-hub-more>Show '+Math.min(20,list.length-visible.length)+' more projects <span>('+list.length+' total)</span></button>';
 return html;
}
function reviewCards(records,{query='',source='all'}={}) {
 const needle=fold(query.trim());
 return records.filter(record=>(source==='all'||record.provider===source)&&(!needle||fold([record.title,record.reason,record.provider].join(' ')).includes(needle))).map(record=>
  '<article class="hub-review-card"><span class="hub-review-symbol" aria-hidden="true">◇</span><div><span class="hub-overline">Creator discovery</span><h3 data-no-translate>'+escape(record.title||'Downloaded project')+'</h3><p>'+escape(record.reason||'The creator could not be verified from this project. Open its source and check the author.')+'</p><div class="hub-project-actions">'+link(record.sourceUrl||record.url,'Open project')+(record.itemId?'<button type="button" class="hub-text-button" data-hub-mod="'+escape(record.itemId)+'">Review mod details</button>':'')+'</div></div></article>');
}
function empty({hasCreators=false,filter='following',query='',live=false}={}) {
 let title='The people behind your load order.',copy='Completed downloads can add their verified creators here automatically. Discover creators from your current library to get started.';
 if(!live){title='Your creators, connected to your profile.';copy='Connect MO2 to discover verified creators, keep their updates together, and manage follows for this profile.'}
 else if(query){title='No creators match this search.';copy='Try a creator name, project title, or a different provider.'}
 else if(hasCreators||filter!=='following'){title=filter==='updates'?'You are caught up.':filter==='review'?'No unresolved creator discoveries.':filter==='paused'?'No paused creators.':filter==='unfollowed'?'No unfollowed creators.':filter==='attention'?'No creator notices need attention.':'No creators in this view.';copy=filter==='updates'?'New release observations will appear here. Your existing follows are still being tracked.':'Change the filter to see your other follows.'}
 return '<div class="hub-empty"><span class="hub-empty-symbol" aria-hidden="true">✦</span><h3>'+title+'</h3><p>'+copy+'</p>'+(live&&!hasCreators&&!query?'<button type="button" class="button-bright" data-hub-action="discover">Discover from my library</button>':'')+'</div>';
}
const api={escape,fold,safeURL,time,provider,index,view,card,detail,reviewCards,empty};
root.ModhunterCreatorHub=api;if(typeof module==='object'&&module.exports)module.exports=api;
})(typeof window==='object'?window:globalThis);
