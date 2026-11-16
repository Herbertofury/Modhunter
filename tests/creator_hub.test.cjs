'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const hub=require('../mo2_update_arsenal/web/creator_hub.js');
const creator=(id,extra={})=>({id:'nexus:'+id,name:'Creator '+id,provider:'nexus',role:'uploader',state:'following',origin:'download',profileUrl:'https://www.nexusmods.com/users/'+id,unreadCount:0,followedAt:1800000000,lastCheckedAt:1800000100,projects:[{itemId:'item-'+id,title:'Project '+id,url:'https://www.nexusmods.com/skyrimspecialedition/mods/'+id,installedVersion:'1',latestVersion:'2'}],...extra});
const snapshot=creators=>({profileId:'a',revision:1,autoFollow:true,creators,review:[]});

test('complete creator directory stays searchable across pagination without dropping records',()=>{
 const data=snapshot(Array.from({length:12002},(_,i)=>creator(i+1)));
 const first=hub.view(data),last=hub.view(data,{page:500});
 assert.equal(first.total,12002);assert.equal(first.items.length,24);
 assert.equal(first.pages,501);assert.equal(last.items.length,2);
 assert.equal(hub.view(data,{query:'Project 12002'}).items[0].id,'nexus:12002');
 assert.equal(hub.view(data,{page:99999}).page,500);
 assert.equal(hub.index(data),hub.index(data),'Repeated filtering reuses the complete normalized index');
});
test('search supports creator, project, site and accents without changing source identities',()=>{
 const original=creator(1,{name:'Élodie',projects:[{title:'Forêt de Skyrim',url:'https://modholdmods.com/g/skyrim-se/mods/1/forest'}]});
 const data=snapshot([original]),before=JSON.stringify(data);
 for(const query of ['elodie','foret','Nexus Mods'])assert.equal(hub.view(data,{query}).total,1);
 assert.equal(JSON.stringify(data),before);
});
test('state filters keep paused and explicitly unfollowed records distinct',()=>{
 const data=snapshot([creator(1,{unreadCount:2}),creator(2,{state:'paused',unreadCount:1}),creator(3,{state:'unfollowed',unreadCount:4}),creator(4,{notice:'Account evidence changed'})]);
 assert.equal(hub.view(data).total,2);
 assert.equal(hub.view(data,{filter:'paused'}).items[0].id,'nexus:2');
 assert.equal(hub.view(data,{filter:'unfollowed'}).items[0].id,'nexus:3');
 assert.equal(hub.view(data,{filter:'updates'}).total,2);
 assert.equal(hub.view(data).counts.unread,3);
 assert.equal(hub.view(data,{filter:'attention'}).total,1);
});
test('provider filter and sorting retain a deterministic complete result set',()=>{
 const data=snapshot([creator(1,{name:'Zeta',unreadCount:2}),creator(2,{name:'Alpha',provider:'modhold',followedAt:1800000090}),creator(3,{name:'Beta',unreadCount:3})]);
 assert.deepEqual(hub.view(data,{sort:'name'}).items.map(x=>x.name),['Alpha','Beta','Zeta']);
 assert.deepEqual(hub.view(data,{sort:'recent'}).items.map(x=>x.name),['Beta','Zeta','Alpha']);
 assert.equal(hub.view(data,{source:'modhold'}).total,1);
 assert.equal(hub.view(data,{sort:'followed'}).items[0].name,'Alpha');
});
test('creator and project text stays escaped and unsafe profiles never become links',()=>{
 const value=creator(1,{id:'"><script>',name:'<img src=x onerror=alert(1)>',profileUrl:'javascript:alert(1)',avatarUrl:'https://evil.example/x',notice:{reason:'<script>bad()</script>'},projects:[{title:'<svg/onload=alert(1)>',url:'https://user:password@example.com/private',itemId:'"><bad>'}]});
 const output=hub.card(value)+hub.detail(value,{live:true});
 assert.doesNotMatch(output,/<script>|<svg\/|<img|href="javascript|user:password|src="https:\/\/evil/);
 assert.match(output,/&lt;img/);assert.match(output,/&lt;script/);
});
test('public source links reject credentials, secret parameters and local network addresses',()=>{
 for(const url of ['http://example.com','https://127.0.0.1/x','https://[::1]/x','https://localhost/x','https://host.local/x','https://u:p@example.com','https://example.com?token=private','https://example.com:444/x','https://exa\\mple.com'])assert.equal(hub.safeURL(url),'',url);
 assert.equal(hub.safeURL('https://modholdmods.com/u/Author#about'),'https://modholdmods.com/u/Author');
});
test('unfollowed creators explain the durable choice and offer an explicit restore',()=>{
 const output=hub.detail(creator(1,{state:'unfollowed'}),{live:true});
 assert.match(output,/Automatic downloads respect this choice/);
 assert.match(output,/data-hub-action="restore"/);
 assert.doesNotMatch(output,/data-hub-action="check"/);
});
test('pause has independent creator scope and offline controls remain disabled',()=>{
 const output=hub.detail(creator(1,{state:'paused'}),{live:false});
 assert.match(output,/installed mods and their update checks stay available/);
 assert.match(output,/data-hub-action="resume"[^>]* disabled/);
 assert.match(output,/uploader/);
});
test('project detail has a bounded initial view and exposes the complete remaining set',()=>{
 const value=creator(1,{projects:Array.from({length:57},(_,i)=>({title:'Mod '+i,url:'https://modholdmods.com/g/skyrim-se/mods/'+(i+1)+'/mod'}))});
 assert.equal((hub.detail(value).match(/class="hub-project"/g)||[]).length,20);
 assert.match(hub.detail(value),/57 total/);
 assert.equal((hub.detail(value,{projectLimit:60}).match(/class="hub-project"/g)||[]).length,57);
 assert.doesNotMatch(hub.detail(value,{projectLimit:60}),/data-hub-more/);
});
test('unresolved discovery cards offer source review without fabricating a creator',()=>{
 const records=[{id:'one',title:'Lost mod',reason:'Publisher identity unavailable',sourceUrl:'https://modholdmods.com/g/skyrim-se/mods/1/lost',itemId:'item-1',provider:'modhold'}];
 const cards=hub.reviewCards(records,{query:'publisher',source:'modhold'});
 assert.equal(cards.length,1);assert.match(cards[0],/Publisher identity unavailable/);
 assert.match(cards[0],/Review mod details/);assert.doesNotMatch(cards[0],/data-hub-action="restore"|Follow again/);
});
test('empty states distinguish offline, caught-up and filtered views',()=>{
 assert.match(hub.empty({live:false}),/Connect MO2/);
 assert.match(hub.empty({live:true,hasCreators:true,filter:'updates'}),/caught up/);
 assert.match(hub.empty({live:true,query:'unknown'}),/No creators match/);
 assert.match(hub.empty({live:true}),/data-hub-action="discover"/);
});
test('untrusted numeric counters cannot become markup or misleading infinity',()=>{
 const value=creator(1,{unreadCount:Infinity,continuity:{affected:'<img src=x>'}});
 assert.equal(hub.view(snapshot([value])).counts.unread,0);
 assert.doesNotMatch(hub.detail(value),/<img|Infinity/);
});
test('normal account checks stay informational and ban observations retain their primary evidence',()=>{
 const normal=creator(1,{notice:{verified:true,state:'not-marked-banned',checkedAt:1800000010,profileUrl:'https://www.nexusmods.com/profile/Author',reason:'No badge in the verified header'}});
 const banned=creator(2,{notice:{verified:true,state:'banned',checkedAt:1800000010,profileUrl:'https://www.nexusmods.com/profile/Other',reason:'Banned badge in the verified header'}});
 assert.equal(hub.view(snapshot([normal,banned]),{filter:'attention'}).total,1);
 assert.match(hub.detail(normal),/No ban badge observed/);
 assert.match(hub.detail(banned),/Account marked banned/);
 assert.match(hub.detail(banned),/href="https:\/\/www.nexusmods.com\/profile\/Other"/);
 assert.match(hub.detail(banned),/Observed/);
});
test('routine check timestamps never reorder a quiet creator directory',()=>{
 const data=snapshot([creator(1,{followedAt:20,lastCheckedAt:900}),creator(2,{followedAt:30,lastCheckedAt:100})]);
 assert.deepEqual(hub.view(data).items.map(row=>row.id),['nexus:2','nexus:1']);
});
test('identity conflicts and incomplete catalogs stay visible with retained projects',()=>{
 const conflict=creator(1,{migrationHold:{reason:'Exact project changed uploader',sourceUrl:'https://www.nexusmods.com/skyrimspecialedition/mods/1'}});
 const incomplete=creator(2,{catalogError:'Pagination changed during this check'});
 assert.equal(hub.view(snapshot([conflict,incomplete]),{filter:'attention'}).total,2);
 assert.match(hub.detail(conflict),/Project identity needs review/);
 assert.match(hub.detail(conflict),/Exact project changed uploader/);
 assert.match(hub.detail(incomplete),/Previous discoveries are retained/);
 assert.match(hub.detail(conflict),/Project 1/);
});
test('providers with only unresolved discoveries remain available to filter',()=>{
 const data={...snapshot([]),review:[{id:'one',provider:'modrinth',title:'Unknown publisher'}]};
 assert.equal(hub.view(data).sourceCounts.get('modrinth'),1);
 assert.equal(hub.reviewCards(data.review,{source:'modrinth'}).length,1);
});
