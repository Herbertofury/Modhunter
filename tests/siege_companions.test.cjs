const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
function harness(saved=new Map(),live=false){
 const timers=[],pending=[],writes=[];
 const gradient={addColorStop(){}},ctx=new Proxy({createRadialGradient:()=>gradient,createLinearGradient:()=>gradient,measureText:()=>({width:10})},{get:(o,k)=>o[k]||(()=>{})}),nodes=new Map();
 function node(id){if(!nodes.has(id))nodes.set(id,{children:[],hidden:false,value:'novice',innerHTML:'',textContent:'',style:{},classList:{add(){},remove(){}},addEventListener(){},focus(){},getContext:()=>ctx,getBoundingClientRect:()=>({width:900,height:620}),remove(){},appendChild(){},append(){},setAttribute(){},parentElement:{getBoundingClientRect:()=>({width:900,height:620})}});return nodes.get(id)}
 const c={console,Math,Date,JSON,URL,URLSearchParams,location:{search:''},performance:{now:()=>0},localStorage:{getItem:k=>saved.get(k),setItem:(k,v)=>saved.set(k,v)},navigator:{},setTimeout(fn){timers.push(fn)},__MO2_BRIDGE_BOOT__:{active:live,token:'fixture'},fetch:async(url,opts)=>{if(opts.method==='POST'){const body=JSON.parse(opts.body);writes.push(body);await new Promise(resolve=>pending.push(resolve));return {ok:true,json:async()=>({revision:writes.length})}}return {ok:true,json:async()=>url.endsWith('/realm')?{profileId:'a',available:0}:url.endsWith('/state')?{revision:0}:url.endsWith('/targets')?{profileId:'a',targets:[],companions:[]}:{bounties:[]}}},requestAnimationFrame:()=>1,cancelAnimationFrame(){},document:{getElementById:node,createElement:node,body:{style:{}},addEventListener(){}},__ARSENAL_REALM_ROUTE__:true,__SIEGE_TEST__:true,addEventListener(){},matchMedia:()=>({matches:true}),devicePixelRatio:1};
 c.window=c;vm.createContext(c);vm.runInContext(fs.readFileSync(path.join(__dirname,'../mo2_update_arsenal/web/siege_art.js'),'utf8'),c);vm.runInContext(fs.readFileSync(path.join(__dirname,'../mo2_update_arsenal/web/siege.js'),'utf8'),c);return {q:c.__SIEGE_QA__,nodes,saved,timers,pending,writes};
}
test('travel, confirm, roles, duplicate camp, fallback, capacity, persistence',async()=>{
 const {q,saved}=harness();await q.open();q.setCompanions([{modId:'a',name:'Lyra <Follower>',evidence:['name'],needsReview:false}]);q.begin('warden','novice');
 assert.equal(q.camp(),false);q.move(9,5);assert.equal(q.camp(),true);assert.equal(q.state().run.squad.length,1);
 q.choice(0);assert.equal(q.dialog(),'camp-role');q.choice(2);let r=q.state().run;assert.equal(r.squad[1].modId,'a');assert.equal(r.squad[1].name,'Lyra <Follower>');assert.equal(r.squad[1].id,'mage');assert.equal(r.companionCamps[0].recruited,true);
 assert.equal(q.camp(),false);q.move(-12,-8);assert.equal(q.camp(),true);q.choice(0);assert.equal(q.state().run.squad.length,3);assert.equal(q.camp(),false);await q.save();
 const reopened=harness(saved);await reopened.q.open();assert.equal(reopened.q.state().run.squad.length,3);assert.equal(reopened.q.state().run.companionCamps.filter(c=>c.recruited).length,2);
});
test('cancel/reopen retains unrecruited roster, level rewards do not recruit',async()=>{
 const {q,nodes}=harness();await q.open();q.begin('warden','novice');q.setXP(200);assert.equal(q.dialog(),'upgrade');assert.equal(q.state().run.squad.length,1);
 assert.ok(!nodes.get('siegeSquad').innerHTML.includes('LEVEL 3 / 6'));
});

test('original allies remain available even with only a support-mod match',async()=>{
 const {q,nodes}=harness();await q.open();q.setCompanions([{modId:'patch',name:'Follower Framework',evidence:['name'],needsReview:true}]);q.begin('warden','novice');q.move(9,5);q.camp();assert.match(nodes.get('siegeChoices').innerHTML,/Huntress/);assert.match(nodes.get('siegeChoices').innerHTML,/SUPPORT MOD/);q.choice(1);assert.equal(q.state().run.squad.length,2);assert.equal(q.state().run.squad[1].modId,undefined);
});
test('recruitment during outstanding save is queued, not cleared by old acknowledgement',async()=>{
 const h=harness(new Map(),true),q=h.q;await q.open();q.begin('warden','novice');assert.equal(h.pending.length,1);q.move(9,5);q.camp();q.choice(0);assert.equal(q.state().run.squad.length,2);h.pending.shift()();await new Promise(resolve=>setImmediate(resolve));for(const fn of h.timers.splice(0))fn();await new Promise(resolve=>setImmediate(resolve));assert.equal(h.writes.length,2);assert.equal(h.writes[0].state.run.squad.length,1);assert.equal(h.writes[1].state.run.squad.length,2);h.pending.shift()();
});

test('boss source handoff contains identity only; resolved sources preserve exact ownership',async()=>{
 const {q}=harness(new Map(),true);await q.open();const boss={modId:'m',profileId:'a'};
 const intent=new URL(q.sourceIntent(boss));assert.equal(intent.protocol,'modhunter-siege:');assert.deepEqual([...intent.searchParams.keys()],['profileId','modId']);assert.equal(q.sourceIntent({modId:'m',profileId:'other'}),'');
 const catalog={profileId:'a',items:[{id:'m',source:{verified:true,url:'https://www.nexusmods.com/skyrimspecialedition/mods/42?tab=description'}}]};
 assert.equal(q.sourceURL(catalog,boss),'https://www.nexusmods.com/skyrimspecialedition/mods/42?tab=files');
 assert.throws(()=>q.sourceURL({...catalog,profileId:'b'},boss));assert.throws(()=>q.sourceURL(catalog,{...boss,modId:'missing'}));
 for(const host of ['127.0.0.1','172.16.0.1','100.64.0.1','0.0.0.0','a.lan','a.local.','a.onion']){catalog.items[0].source.url='https://'+host+'/secret';assert.throws(()=>q.sourceURL(catalog,boss));}catalog.items[0].source.url='https://github.com/example/mod/releases';assert.equal(q.sourceURL(catalog,boss),catalog.items[0].source.url);catalog.items[0].source.verified=false;assert.throws(()=>q.sourceURL(catalog,boss));
});

test('delayed victory cannot reopen dialog after a newer run starts',async()=>{
 const h=harness(new Map(),true),q=h.q;await q.open();q.begin('warden','novice');const victory=q.defeated({boss:{modId:'m',profileId:'a',name:'Fixture'}});assert.equal(h.pending.length,2);q.begin('ranger','novice');h.pending[1]();await victory;assert.equal(q.dialog(),'');assert.equal(q.state().run.hero,'ranger');h.pending[0]();
});
