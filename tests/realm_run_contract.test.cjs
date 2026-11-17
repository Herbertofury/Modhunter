const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
function harness(saved=new Map(),live=false,transport=null){
 const timers=[],pending=[],writes=[];
 const gradient={addColorStop(){}},ctx=new Proxy({createRadialGradient:()=>gradient,createLinearGradient:()=>gradient,measureText:()=>({width:10})},{get:(o,k)=>o[k]||(()=>{})}),nodes=new Map();
 function node(id){if(!nodes.has(id))nodes.set(id,{children:[],hidden:false,value:'novice',innerHTML:'',textContent:'',style:{},classList:{add(){},remove(){},toggle(){}},addEventListener(){},focus(){},getContext:()=>ctx,getBoundingClientRect:()=>({width:900,height:620}),remove(){},appendChild(){},append(){},setAttribute(){},parentElement:{getBoundingClientRect:()=>({width:900,height:620})}});return nodes.get(id)}
 const c={clearTimeout(){},console,Math,Date,JSON,URL,URLSearchParams,location:{search:''},performance:{now:()=>0},localStorage:{getItem:k=>saved.get(k),setItem:(k,v)=>saved.set(k,v)},navigator:{},setTimeout(fn){timers.push(fn)},__MO2_BRIDGE_BOOT__:{active:live,token:'fixture'},fetch:async(url,opts)=>{if(opts.method==='POST'){const body=JSON.parse(opts.body);writes.push(body);await new Promise(resolve=>pending.push(resolve));return {ok:true,json:async()=>({revision:writes.length})}}return {ok:true,json:async()=>url.endsWith('/realm')?{profileId:'a',available:0}:url.endsWith('/state')?{revision:0}:url.endsWith('/targets')?{profileId:'a',targets:[],companions:[]}:{bounties:[]}}},requestAnimationFrame:()=>1,cancelAnimationFrame(){},document:{documentElement:{classList:{add(){}}},querySelectorAll:()=>[],querySelector:()=>node('selector'),getElementById:node,createElement:node,body:{style:{}},addEventListener(){}},__ARSENAL_REALM_ROUTE__:true,__SIEGE_TEST__:true,addEventListener(){},matchMedia:()=>({matches:true}),devicePixelRatio:1};
 node('realmRunDuration');c.window=c;c.innerWidth=1200;vm.createContext(c);vm.runInContext(fs.readFileSync(path.join(__dirname,'../mo2_update_arsenal/web/run_contract.js'),'utf8'),c);let src=fs.readFileSync(path.join(__dirname,'../mo2_update_arsenal/web/realm.js'),'utf8');src=src.replace('init();\n})();',`loading=false;window.__RUN_TEST__={state:()=>S,start:startExpedition,rift:startRift,tick:sim,clearWave:()=>{enemies.clear();riftEnemyKilled({})},resume:()=>{closeModal()},bless:()=>bless('iron')};\n})();`);vm.runInContext(src,c);return {q:c.__RUN_TEST__,nodes,c};
}
for(const length of ['20','30','60','120','endless'])test(`Realm expedition honors ${length} simulation-time contract`,()=>{
 const h=harness();h.nodes.get('realmRunDuration').value=length;h.q.start();const r=h.q.state().rift;r.relics=3;r.seconds=180;h.q.tick(0);assert.equal(h.q.state().mode,'expedition');
 r.seconds=length==='endless'?100000:Number(length)*60-1;h.q.tick(0);assert.equal(h.q.state().mode,'expedition');
 if(length!=='endless'){r.seconds=Number(length)*60;h.q.tick(0);assert.equal(h.q.state().mode,'overworld');assert.equal(h.q.state().records.expeditionWins,1);h.q.tick(0);assert.equal(h.q.state().records.expeditionWins,1);}
});
test('Realm rift repeats waves and cannot win at five waves before selected time',()=>{
 const h=harness();h.nodes.get('realmRunDuration').value='30';h.q.state().inventory.riftKeys=1;h.q.rift();let r=h.q.state().rift;r.wave=5;r.seconds=300;h.q.clearWave();assert.equal(h.q.state().mode,'rift');assert.equal(r.phase,'blessing');h.q.bless();assert.equal(r.wave,6);r.seconds=1800;h.q.clearWave();assert.equal(h.q.state().mode,'overworld');assert.equal(h.q.state().records.riftWins,1);
});
test('Realm endless rift never time-completes and stores JSON null rather than Infinity',()=>{
 const h=harness();h.nodes.get('realmRunDuration').value='endless';h.q.state().inventory.riftKeys=1;h.q.rift();const r=h.q.state().rift;r.wave=500;r.seconds=100000;h.q.clearWave();assert.equal(h.q.state().mode,'rift');assert.equal(JSON.parse(JSON.stringify(r)).duration,null);
});
test('shared contract migrates legacy missing duration to twenty-minute minimum',()=>{
 const {c}=harness(),r=c.ModhunterRunContract;assert.equal(r.complete({},1199),false);assert.equal(r.complete({},1200),true);for(const bad of [-1,0,300,NaN,Infinity])assert.equal(r.duration({duration:bad}),1200);assert.equal(r.seconds('10'),1800);
});

test('repeated Rift blessings retain effects without duplicate save entries',()=>{const h=harness();h.q.state().inventory.riftKeys=1;h.q.rift();const r=h.q.state().rift;for(let i=0;i<100;i++){r.phase='blessing';h.q.bless();}assert.equal(r.boons.length,1);assert.equal(r.boons[0],'iron');});
