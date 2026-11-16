const test=require('node:test');
const assert=require('node:assert/strict');
const C=require('../mo2_update_arsenal/web/creator_continuity.js');
const candidate=C.base.migrations[0];
const mod={id:'estate',name:'A renamed installed folder',author:'SethSaidThat',source:{url:candidate.from.url,verified:true},installedVersion:'0.1.20'};
const approve=(row={})=>C.approve(mod,row,mod.source,candidate.id,{confirmed:true});
const clone=value=>structuredClone(value);

test('matches exact game/project even when the local folder name changed',()=>{
 const notice=C.notice(mod,{},mod.source);
 assert.equal(notice.migration.id,candidate.id);
 assert.equal(notice.match,'exact-project');
 assert.equal(notice.author.status.state,'banned');
 assert.equal(C.notice({...mod,name:'Estates of Skyrim',author:'Other'}, {},{url:'https://www.nexusmods.com/skyrim/mods/196696'}),null);
});
test('a title alone never establishes creator or destination identity',()=>{
 assert.equal(C.notice({id:'wrong',name:candidate.title,author:'Other'}, {},{url:'https://www.nexusmods.com/skyrimspecialedition/mods/1'}),null);
 const notice=C.notice({...mod,id:'different'},{},{url:'https://www.nexusmods.com/skyrimspecialedition/mods/7'});
 assert.equal(notice.migration,undefined);
 assert.equal(notice.match,'saved-author-handle');
});
test('canonical identity rejects spoof hosts and wrong game IDs',()=>{
 assert.notEqual(C.identity('https://modholdmods.com.evil.example/g/skyrim-se/mods/11/name').provider,'modhold');
 assert.notEqual(C.identity('https://www.nexusmods.com.evil.example/skyrimspecialedition/mods/196696').provider,'nexus');
 assert.equal(C.identity(candidate.from.url+'?tab=files').key,C.identity(candidate.from.url).key);
 assert.notEqual(C.identity(candidate.to.url.replace('skyrim-se','skyrim-vr')).key,C.identity(candidate.to.url).key);
});
test('unreviewed migration cannot change a source',()=>{
 assert.throws(()=>C.approve(mod,{},mod.source,candidate.id),/Review/);
 assert.throws(()=>C.approve(mod,{},{url:'https://www.nexusmods.com/skyrimspecialedition/mods/7'},candidate.id,{confirmed:true}),/source changed/);
});
test('review preserves exact original source, previous override, notes and installed metadata',()=>{
 const row={url:candidate.from.url+'?tab=files',note:'My load order notes',favorite:true,installed:'0.1.20'};
 const changed=C.approve(mod,row,{url:row.url},candidate.id,{confirmed:true});
 assert.equal(row.url,candidate.from.url+'?tab=files');
 assert.equal(changed.url,candidate.to.url);
 assert.equal(changed.note,row.note);
 assert.equal(changed.favorite,true);
 assert.equal(changed.installed,'0.1.20');
 assert.equal(changed.creatorContinuity.expectedAuthor,'SethSaidThat');
 assert.equal(changed.creatorContinuity.previousOverride,row.url);
 assert.equal(changed.creatorContinuity.originalUrl,row.url);
});
test('pause/resume/undo survive serialization without erasing provenance',()=>{
 const tracked=JSON.parse(JSON.stringify(approve({note:'kept'})));
 const paused=C.changeTracking(tracked,'pause');
 assert.equal(paused.url,'');
 assert.equal(paused.creatorContinuity.state,'paused');
 const resumed=C.changeTracking(paused,'resume');
 assert.equal(resumed.url,candidate.to.url);
 const restored=C.changeTracking(resumed,'undo');
 assert.equal(restored.url,'');
 assert.equal(restored.note,'kept');
 assert.equal(restored.creatorContinuity.candidateId,candidate.id);
 assert.equal(restored.creatorContinuity.state,'undone');
});
test('a later custom source edit is never overwritten by old migration controls',()=>{
 const tracked={...approve(),url:'https://www.nexusmods.com/skyrimspecialedition/mods/99'};
 assert.throws(()=>C.changeTracking(tracked,'pause'),/source changed/);
 assert.throws(()=>C.changeTracking({...C.changeTracking(approve(),'pause'),url:'https://example.com/new'},'resume'),/source changed/);
 assert.equal(C.notice({...mod,author:'Other'},tracked,{url:tracked.url}),null);
});
test('safe URLs reject credentials, private endpoints and token-bearing links',()=>{
 for(const url of ['http://example.com/x','https://127.0.0.1/x','https://localhost/x','https://internal.local/x','https://user:pass@example.com/x','https://example.com:8000/x','https://example.com/x?access_token=secret','file:///tmp/test','javascript:alert(1)'])assert.equal(C.safeURL(url),'',url);
 assert.equal(C.safeURL('https://example.com/mod#section'),'https://example.com/mod');
});
test('registries require dated evidence, matching IDs and explicit review',()=>{
 for(const change of [
  r=>r.migrations[0].reviewRequired=false,
  r=>r.migrations[0].to.projectId='12',
  r=>r.migrations[0].to.url='https://modholdmods.com.evil.example/g/skyrim-se/mods/11/name',
  r=>r.authors[0].status.evidenceUrl='http://example.com',
  r=>r.authors[0].status.observedAt='2099-01-01T00:00:00Z',
  r=>r.migrations.push({...r.migrations[0],id:'duplicate-source'}),
  r=>r.authors.push({...r.authors[0],id:'duplicate-handle'}),
 ]){const registry=clone(C.base);change(registry);assert.throws(()=>C.importRegistry(registry))}
});
test('imported overlay cannot invalidate a base migration relationship',()=>{
 const registry=clone(C.base);registry.authors[0].status.observedAt=new Date().toISOString();
 registry.authors[0].destinations=[];registry.migrations=[];
 assert.throws(()=>C.importRegistry(registry),/linked to this creator/);
});
test('different creators and new migrations use the same generic schema',()=>{
 const registry=clone(C.base);registry.authors[0].id='nexus:another';registry.authors[0].name='Another';
 registry.authors[0].source.handle='Another';registry.authors[0].source.profileUrl='https://www.nexusmods.com/profile/Another';
 registry.authors[0].status.state='moved';registry.authors[0].status.evidenceUrl=registry.authors[0].source.profileUrl;
 registry.authors[0].destinations[0].handle='Another';registry.authors[0].destinations[0].profileUrl='https://modholdmods.com/u/Another';
 registry.migrations=[{...registry.migrations[0],id:'another-99',authorId:'nexus:another',from:{provider:'nexus',game:'skyrimspecialedition',projectId:'99',url:'https://www.nexusmods.com/skyrimspecialedition/mods/99'}}];
 const imported=C.importRegistry(registry);
 const other={id:'new',author:'Another',source:{url:registry.migrations[0].from.url}};
 const notice=C.notice(other,{},other.source,imported);
 assert.equal(notice.author.name,'Another');assert.equal(notice.author.imported,true);
 assert.equal(C.approve(other,{},other.source,'another-99',{imported,confirmed:true}).creatorContinuity.expectedAuthor,'Another');
});
test('provider errors do not create a ban observation',()=>{
 const other={id:'new',author:'Other',source:{url:'https://www.nexusmods.com/skyrimspecialedition/mods/99'},update:{outcome:'unavailable',availability:'not-found',reason:'BANNED commenter'}};
 assert.equal(C.notice(other,{},other.source),null);
 assert.equal(C.target(other,{},other.source).handle,'Other');
});
test('a fresh verified observation can warn for an author absent from the bundled registry',()=>{
 const other={id:'new',author:'Other',source:{url:'https://www.nexusmods.com/skyrimspecialedition/mods/99'}};
 const observation={state:'banned',verified:true,handle:'Other',profileUrl:'https://www.nexusmods.com/profile/Other',checkedAt:Date.now()/1000};
 assert.equal(C.notice(other,{creatorObservation:observation},other.source).author.status.state,'banned');
 assert.equal(C.notice(other,{creatorObservation:{...observation,handle:'Wrong'}},other.source),null);
});
test('fresh no-badge observation is distinct from an old confirmed ban',()=>{
 const row={creatorObservation:{state:'not-marked-banned',verified:true,handle:'SethSaidThat',accountId:'199502561',profileUrl:C.base.authors[0].source.profileUrl,checkedAt:Date.now()/1000}};
 const details=C.details(mod,row,mod.source,{live:true});
 assert.match(details,/No ban badge observed/);assert.match(details,/Earlier evidence is retained/);
 assert.doesNotMatch(C.chip(mod,row,mod.source),/>Creator banned</);
});
test('status evidence and imported text remain inert HTML',()=>{
 const registry=clone(C.base);registry.authors[0].name='<img src=x onerror=alert(1)>';
 registry.authors[0].status.observedAt=new Date().toISOString();
 const imported=C.importRegistry(registry),html=C.details(mod,{},mod.source,{imported,live:true});
 assert.match(html,/&lt;img/);assert.doesNotMatch(html,/<img src=x/);
 assert.match(html,/An imported notice reports/);
});
test('stale historical evidence remains dated and does not gain a fresh timestamp',()=>{
 const now=Date.parse(C.base.authors[0].status.observedAt)+8*86400000;
 const n=C.notice(mod,{},mod.source,'',now);
 assert.equal(n.stale,true);assert.equal(n.author.status.observedAt,C.base.authors[0].status.observedAt);
});
test('summary retains the complete inventory and reuses repeated notice evaluations',()=>{
 const items=Array.from({length:11207},(_,index)=>({id:'mod-'+index,author:index===0?'SethSaidThat':'Other',source:{url:'https://www.nexusmods.com/skyrimspecialedition/mods/'+(index===0?196696:index+1)}}));
 const rows=new Map([[items[0].id,approve()]]),getRow=id=>rows.get(id)||{},getSource=item=>({url:getRow(item.id).url||item.source.url});
 const a=C.summary(items,getRow,getSource),b=C.summary(items,getRow,getSource);
 assert.deepEqual(a,b);assert.equal(a.affected,1);assert.equal(a.tracking,1);assert.equal(items.length,11207);
});
