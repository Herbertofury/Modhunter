'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const os=require('node:os');
const path=require('node:path');
const {readMo2Profile,parseModlist,gameSlug,sourceFor}=require('./offline_mo2.cjs');

test('import a complete MO2 profile, exact URLs, enabled state, sections, no record caps or filesystem writes',async t=>{
 const root=await fs.mkdtemp(path.join(os.tmpdir(),'modhunter-m2-'));
 t.after(()=>fs.rm(root,{recursive:true,force:true}));
 const profile=path.join(root,'profiles','A Hero'),mods=path.join(root,'mods');
 await fs.mkdir(profile,{recursive:true});
 for(const name of ['First','Second','Side Missions_separator','Third','Unlisted'])
  await fs.mkdir(path.join(mods,name),{recursive:true});
 await fs.mkdir(path.join(mods,'First','SKSE','Plugins'),{recursive:true});
 await fs.writeFile(path.join(mods,'First','SKSE','Plugins','example.dll'),'dll');
 await fs.writeFile(path.join(root,'ModOrganizer.ini'),'[General]\ngameName=Skyrim Special Edition\ngameVersion=1.5.97\n');
 await fs.writeFile(path.join(profile,'modlist.txt'),
  '# MO2 generated\n+First\n-Second\n+Side Missions_separator\n+Third\n+../evil\n');
 await fs.writeFile(path.join(mods,'First','meta.ini'),
   '[General]\nmodid=44\nversion=1.0\nnewestVersion=2.0\nurl=https://github.com/example/mod/releases?utm_source=secret\n');
 await fs.writeFile(path.join(mods,'Second','meta.ini'),
   '[General]\nmodid=55\nversion=3.0\n');
 const before=(await fs.readFile(path.join(profile,'modlist.txt'))).toString();
 const c=await readMo2Profile(path.join(profile,'modlist.txt'));
 assert.equal(c.items.length,4);
 assert.equal(c.game,'skyrimspecialedition');
 assert.equal(c.gameRuntime,'1.5.97');
 assert.equal(c.profileName,'A Hero');
 const one=c.items.find(x=>x.name==='First'),two=c.items.find(x=>x.name==='Second'),
   three=c.items.find(x=>x.name==='Third'),extra=c.items.find(x=>x.name==='Unlisted');
 assert.ok(one.enabled);assert.deepEqual(one.flags,['DLL']);
 assert.equal(one.source.url,'https://github.com/example/mod/releases');
 assert.equal(one.installedVersion,'1.0');
 assert.equal(two.enabled,false);
 assert.equal(two.source.url,'https://www.nexusmods.com/skyrimspecialedition/mods/55?tab=files');
 assert.equal(extra.enabled,false);
 assert.notEqual(three.group,'Imported load order');
 assert.equal(extra.group,'Installed but not in this profile');
 assert.equal(c.items.filter(x=>x.name.includes('evil')).length,0);
 assert.equal(JSON.stringify(c).includes(root),false);
 assert.equal((await fs.readFile(path.join(profile,'modlist.txt'))).toString(),before);
 const same=await readMo2Profile(path.join(profile,'modlist.txt'));
 assert.deepEqual(same.items.map(x=>x.id),c.items.map(x=>x.id));
});
test('offline parsing denies arbitrary paths, unknown games and fake update claims',async()=>{
 assert.deepEqual(parseModlist('#header\n+Good\n-Bad\n+../evil\n+Good'),[
  {name:'Good',enabled:true},{name:'Bad',enabled:false}]);
 assert.equal(gameSlug('an unknown game'),'unknown');
 assert.equal(sourceFor('https://github.com/owner/repo?apikey=SECRET',0,'unknown').url,
  'https://github.com/owner/repo');
 assert.equal(sourceFor('http://127.0.0.1/private',0,'unknown').verified,false);
});
test('no missing mods even with a very large profile',async t=>{
 const root=await fs.mkdtemp(path.join(os.tmpdir(),'modhunter-m2-big-'));
 t.after(()=>fs.rm(root,{recursive:true,force:true}));
 const profile=path.join(root,'profiles','Many'),mods=path.join(root,'mods');
 await fs.mkdir(profile,{recursive:true});await fs.mkdir(mods,{recursive:true});
 const count=520;
 for(let i=0;i<count;i++)await fs.mkdir(path.join(mods,'Mod '+i));
 await fs.writeFile(path.join(profile,'modlist.txt'),
   Array.from({length:count},(_,i)=>(i%2===0?'+':'-')+'Mod '+i).join('\n'));
 const c=await readMo2Profile(path.join(profile,'modlist.txt'));
 assert.equal(c.items.length,count);
 assert.equal(c.groups.reduce((n,g)=>n+g.count,0),count);
});
test('reject the wrong profile file and split-path folder instead of guessing',async t=>{
 const root=await fs.mkdtemp(path.join(os.tmpdir(),'modhunter-m2-error-'));
 t.after(()=>fs.rm(root,{recursive:true,force:true}));
 const profile=path.join(root,'profiles','Beta');
 await fs.mkdir(profile,{recursive:true});
 await fs.writeFile(path.join(profile,'modlist.txt'),'+Missing');
 await assert.rejects(readMo2Profile(path.join(profile,'modlist.txt')),/mods\/ directory/);
 await assert.rejects(readMo2Profile(path.join(profile,'plugins.txt')),/modlist.txt/);
});

test('listed mod symlinks remain catalog entries without reading external DLLs or metadata',async t=>{
 const root=await fs.mkdtemp(path.join(os.tmpdir(),'modhunter-m2-link-'));
 t.after(()=>fs.rm(root,{recursive:true,force:true}));
 const profile=path.join(root,'profiles','Links'),mods=path.join(root,'mods');
 const external=path.join(root,'outside-profile');
 await fs.mkdir(profile,{recursive:true});await fs.mkdir(mods,{recursive:true});
 await fs.mkdir(path.join(external,'SKSE','Plugins'),{recursive:true});
 await fs.writeFile(path.join(external,'SKSE','Plugins','private.dll'),'private fixture');
 await fs.writeFile(path.join(external,'meta.ini'),'[General]\nversion=private-version\n');
 await fs.symlink(external,path.join(mods,'Linked'),'junction');
 await fs.writeFile(path.join(profile,'modlist.txt'),'+Linked\n+Missing');
 const c=await readMo2Profile(path.join(profile,'modlist.txt'));
 assert.deepEqual(c.items.map(x=>x.name),['Linked','Missing']);
 for(const item of c.items){
  assert.deepEqual(item.flags,[]);
  assert.deepEqual(item.audit,{});
  assert.equal(item.installedVersion,'?');
  assert.equal(item.enabled,true);
 }
 assert.equal(await fs.readFile(path.join(external,'SKSE','Plugins','private.dll'),'utf8'),'private fixture');
});
