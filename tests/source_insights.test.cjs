'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const sources=require('../mo2_update_arsenal/web/source_insights.js');
const row=()=>({id:'mod-a',name:'Example',installedVersion:'1.0',source:{type:'gitlab',verified:true,url:'https://gitlab.com/group/project'},update:{provider:'gitlab',verdict:'candidate',outcome:'release',latestVersion:'2.0',releaseId:'v2',releaseUrl:'https://gitlab.com/group/project/-/releases/v2',freshness:'fresh',checkedAt:Date.now()/1000,expiresAt:Date.now()/1000+900,capabilities:{checkMode:'public-api',reviewedDownload:true,authentication:'none'},releaseAssets:[{assetKey:'gitlab:group/project:v2:5',assetProof:'a'.repeat(64),name:'Mod-v2.zip',url:'https://gitlab.com/group/project/-/releases/v2/downloads/Mod-v2.zip',size:2048,hashes:{sha256:'b'.repeat(64)}}]}});

test('each provider has its own release label and compact asset count enables review',()=>{
 for(const provider of ['github','gitlab','modrinth','thunderstore']){
  const mod=row();mod.update.provider=provider;delete mod.update.releaseAssets;mod.update.releaseAssetCount=2;
  assert.equal(sources.hasAssets(mod),true);assert.match(sources.details(mod,{live:true}),/Review release files/);
  assert.ok(sources.chips(mod).includes(sources.label(provider)));
 }
});
test('an expired candidate visibly needs refresh and has no review action',()=>{
 const mod=row();mod.update.expiresAt=1;
 assert.equal(sources.state(mod).stale,true);assert.match(sources.details(mod,{live:true}),/Refresh needed/);
 assert.doesNotMatch(sources.details(mod,{live:true}),/data-source-act="files"/);
});
test('rate limits remain distinct from absent updates',()=>{
 const mod=row();mod.update.freshness='unverified';mod.update.availability='rate-limited';mod.update.retryAt=Date.now()/1000+60;
 assert.equal(sources.state(mod).rateLimited,true);assert.match(sources.chips(mod),/Provider cooldown/);
});
test('manual and key-required adapters do not advertise unattended API capability',()=>{
 for(const [checkMode,expected] of [['manual','Source-page review'],['api-key','Provider key required']]){
  const mod=row();mod.update={provider:'curseforge',capabilities:{checkMode,reviewedDownload:false,authentication:'api-key'}};
  assert.equal(sources.state(mod).status,expected);assert.doesNotMatch(sources.details(mod,{live:true}),/data-source-act="files"/);
 }
});
test('provider declarations and original authors are not inferred from uploaders',()=>{
 const mod=row();mod.update.publisher={name:'Release maintainer',url:'https://gitlab.com/publisher'};mod.update.dependencies=[{projectId:'dep',kind:'required',url:'https://gitlab.com/group/dep'}];
 const html=sources.details(mod);assert.match(html,/Release publisher/);assert.doesNotMatch(html,/>Author</);assert.match(html,/do not establish installed versions/);
});
test('remote release notes and names cannot inject markup or executable links',()=>{
 const mod=row();mod.update.changelog='<script>alert(1)</script><img onerror="x">';mod.update.publisher={name:'<svg onload=x>',url:'javascript:alert(1)'};mod.update.releaseUrl='https://secret:password@example.com/file';
 const html=sources.details(mod);assert.doesNotMatch(html,/<script|<svg|<img|javascript:|secret:password/);assert.match(html,/&lt;script&gt;/);
});
test('file choices carry stable key and reviewed proof without accepting a client URL',()=>{
 const mod=row(),asset=mod.update.releaseAssets[0];asset.name='a" onclick="bad.zip';
 const html=sources.assetCards(mod);assert.match(html,/data-asset-key="gitlab:group\/project:v2:5"/);assert.ok(html.includes('data-asset-proof="'+'a'.repeat(64)+'"'));
 assert.doesNotMatch(html,/data-url=|href=|onclick="bad/);assert.match(html,/&quot; onclick=&quot;/);
});
test('legacy numeric GitHub assets remain selectable',()=>{
 const mod=row();mod.update.provider='github';mod.update.releaseAssets=[{assetId:123,name:'legacy.zip',size:1}];
 assert.match(sources.assetCards(mod),/data-file-id="123"/);assert.match(sources.assetCards(mod),/GitHub/);
});
test('malformed checksum algorithms and lengths are not presented as hashes',()=>{
 const mod=row();mod.update.releaseAssets[0].hashes={sha256:'c'.repeat(64),sha512:'d'.repeat(32),password:'token'};
 const html=sources.assetCards(mod);assert.match(html,/SHA256/);assert.doesNotMatch(html,/SHA512|password|token/);
});
test('provider mismatch is searchable and filterable without a compatibility guarantee',()=>{
 const mod=row();mod.update.compatibility={status:'incompatible',reason:'Wrong game'};
 assert.equal(sources.matches(mod,'source-incompatible'),true);assert.match(sources.searchText(mod),/incompatible/);assert.match(sources.details(mod),/Wrong game/);
});
test('real adapter context and deprecation states stay actionable on rows and cannot offer file review',()=>{
 for(const status of ['game-mismatch','context-required','community-required','deprecated']){
  const mod=row();mod.update.compatibility={status};
  assert.equal(sources.matches(mod,'source-incompatible'),true);
  assert.equal(sources.state(mod).tone,'warning');
  assert.doesNotMatch(sources.chips(mod),/Fresh API evidence/);
  assert.doesNotMatch(sources.details(mod,{live:true}),/data-source-act="files"/);
 }
 const mod=row();mod.update.compatibility={status:'matched'};
 assert.match(sources.details(mod),/Declared game \/ loader match/);
 mod.update.outcome='unavailable';mod.update.freshness='unverified';
 assert.equal(sources.state(mod).status,'Source unavailable');
});
test('compact dependencies and coverage need no changelog or full asset list',()=>{
 const mod=row();delete mod.update.releaseAssets;mod.update.releaseAssetCount=7;mod.update.dependencyCount=4;
 assert.equal(sources.matches(mod,'source-dependencies'),true);assert.match(sources.chips(mod),/4 declared dependencies/);assert.match(sources.coverage([mod,mod]),/<td>2<\/td>/);
});
test('CSV evidence columns have matching values and preserve unknown metadata',()=>{
 const mod=row();assert.equal(sources.csvHeaders.length,sources.csv(mod).length);
 const unlinked={id:'x',source:{type:'unknown'}};const values=sources.csv(unlinked);
 assert.equal(values[1],'');assert.equal(values[4],'');assert.equal(values[5],'');assert.equal(values[7],'');
});
