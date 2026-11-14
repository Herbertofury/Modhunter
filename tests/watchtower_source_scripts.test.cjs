'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const root=path.resolve(__dirname,'..');
const resolver=fs.readFileSync(path.join(root,'native/ui/resolve-offers.js'),'utf8');
const observer=fs.readFileSync(path.join(root,'native/ui/observe-access.js'),'utf8');
const megaFolder=fs.readFileSync(path.join(root,'native/ui/mega-folder.js'),'utf8');
function link(href,text='',download=null,row=null){return {textContent:text,innerText:text,getAttribute:key=>({href,download}[key]??null),closest:()=>row,getClientRects:()=>[{}]};}
function documentFixture({title='Release',links=[],passwords=[],content='A complete public release description with enough meaningful text for a normal readable page observation.',body='',controls=[],metadata=[]}={}){
 return {title,body:{innerText:body||content},querySelectorAll:selector=>selector==='input[type=password]'?passwords:selector==='a[href]'?links:selector==='a[href],button,[role=menuitem]'?[...links,...controls]:selector==='main,article,[role=main]'?[{innerText:content,getClientRects:()=>[{}]}]:selector==='script[type="application/ld+json"]'?metadata.map(item=>({textContent:JSON.stringify(item)})):[]};
}
const parser=class {parseFromString(html){const links=[...html.matchAll(/href=["']([^"']+)["']/g)].map(match=>link(match[1].replaceAll('&amp;','&')));return documentFixture({links});}};
async function discover({url='https://www.patreon.com/posts/release-111',source=url,docs={},responses={},document=documentFixture(),includeNewContent=false}={}){
 const requests=[];const sandbox={window:{},cfg:{key:'result',source,includeNewContent},location:new URL(url),document,URL,Set,Map,DOMParser:parser,getComputedStyle:()=>({visibility:'visible'}),AbortSignal,TextDecoder,Uint8Array,fetch:async(target,options)=>{requests.push(target);assert.equal(options.credentials,'include');assert.equal(options.redirect,'error');if(responses[target]){const {body,status=200,headers={}}=responses[target];return new Response(body,{status,headers});}const id=/\/api\/posts\/(\d+)/.exec(target)?.[1];if(!docs[id])throw Error('Unexpected fixture request '+target);return new Response(JSON.stringify(docs[id]),{status:200});}};
 vm.runInNewContext(resolver,sandbox,{timeout:1000});
 for(let i=0;i<100&&sandbox.window.result?.state==='pending';i++)await new Promise(setImmediate);
 assert.notEqual(sandbox.window.result?.state,'pending','resolver did not settle');return {result:JSON.parse(JSON.stringify(sandbox.window.result)),requests};
}
function post(id='111',media=['10'],extra={}){
 return {data:{id,type:'post',attributes:{current_user_can_view:true,published_at:'2026-01-01T00:00:00Z',content:'',...extra},relationships:{campaign:{data:{id:'creator-1',type:'campaign'}},attachments_media:{data:media.map(id=>({id,type:'media'}))}}},included:media.map(id=>({id,type:'media',attributes:{file_name:'release-'+id+'.zip',download_url:'https://www.patreon.com/files/'+id+'.zip?token=private',created_at:'2026-01-01T00:00:00Z'}}))};
}
function observe({url='https://www.patreon.com/posts/111',sessionOrigin='https://www.patreon.com',document=documentFixture()}={}){
 const location=new URL(url),result=vm.runInNewContext(observer,{location,document,cfg:{origin:location.origin,sessionOrigin},getComputedStyle:()=>({visibility:'visible'}),URL});
 return result==null?null:JSON.parse(JSON.stringify(result));
}
test('Patreon accepts only media referenced by the exact post relationship',async()=>{
 const doc=post();doc.included.push({id:'999',type:'media',attributes:{file_name:'unrelated.zip',download_url:'https://www.patreon.com/files/999.zip'}});
 const {result}=await discover({docs:{111:doc}});assert.equal(result.state,'ready');assert.equal(result.files.length,1);assert.equal(result.files[0].identity,'patreon:post:111:media:10');assert.equal(result.files[0].related,false);
});
test('rotating download tokens do not change the durable provider file identity',async()=>{
 const first=post(),second=post();second.included[0].attributes.download_url='https://www.patreon.com/files/10.zip?token=new-private-token';
 const a=(await discover({docs:{111:first}})).result.files[0],b=(await discover({docs:{111:second}})).result.files[0];assert.equal(a.identity,b.identity);assert.notEqual(a.url,b.url);assert(!a.identity.includes('private'));
});
test('missing referenced media fails the entire result without returning partial files',async()=>{
 const doc=post('111',['10','11']);doc.included.pop();const {result}=await discover({docs:{111:doc}});assert.equal(result.state,'error');assert.match(result.message,/omitted/);assert.equal(result.files,undefined);
});
test('login is not treated as entitlement to a Patreon post',async()=>{
 const {result}=await discover({docs:{111:post('111',['10'],{current_user_can_view:false})}});assert.equal(result.state,'error');assert.match(result.message,/not confirmed accessible/);assert.equal(result.files,undefined);
});
test('unknown archive metadata with no URL fails instead of fabricating a relative link',async()=>{
 const doc=post();delete doc.included[0].attributes.download_url;const {result}=await discover({docs:{111:doc}});assert.equal(result.state,'error');assert.match(result.message,/omitted a file address/);
});
test('creator opt-in exposes linked releases without classifying them as installed updates',async()=>{
 const current=post('111',['10'],{content:'<a href="https://www.patreon.com/posts/new-222">New release</a>'});
 const without=await discover({docs:{111:current,222:post('222',['20'])}});assert.equal(without.requests.length,1);assert.equal(without.result.files.length,1);
 const opted=await discover({docs:{111:current,222:post('222',['20'])},includeNewContent:true});assert.equal(opted.result.files.length,2);assert.equal(opted.result.files[1].related,true);assert.equal(opted.result.files[1].sourcePostId,'222');assert.equal(opted.result.files[1].association,undefined);
});
test('a linked release from another creator is never folded into this creator',async()=>{
 const current=post('111',[],{content:'<a href="https://www.patreon.com/posts/other-222">Other creator</a>'}),linked=post('222',['20']);linked.data.relationships.campaign.data.id='another-creator';
 const {result}=await discover({docs:{111:current,222:linked}});assert.equal(result.state,'ready');assert.equal(result.files.length,0);
});
test('a bounded result rejects a 201st file rather than silently dropping it',async()=>{
 const links=Array.from({length:201},(_,i)=>link('https://mods.example.com/'+i+'.zip'));
 const {result}=await discover({url:'https://mods.example.com/release',document:documentFixture({links})});assert.equal(result.state,'error');assert.match(result.message,/more than 200/);assert.equal(result.files,undefined);
});
test('a page over the full link inspection limit does not claim a complete empty result',async()=>{
 const links=Array.from({length:10001},()=>link('https://mods.example.com/other'));
 const {result}=await discover({url:'https://mods.example.com/release',document:documentFixture({links})});assert.equal(result.state,'error');assert.match(result.message,/too many links/);
});
test('LoversLab inspects the complete chooser even when the page already exposes one attachment',async()=>{
 const url='https://www.loverslab.com/files/file/111-mod/';
 const links=[link('?do=download&r=77&csrfKey=secret','Version A'),link('?do=download','Chooser'),link('/files/file/222-other/?do=download&r=88','Wrong mod')];
 const responses={[url+'?do=download']:{body:'<a href="?do=download&amp;r=77&amp;csrfKey=secret">A</a><a href="?do=download&amp;r=78&amp;csrfKey=other">B</a>',headers:{'content-type':'text/html'}}};
 const {result,requests}=await discover({url,document:documentFixture({links}),responses});assert.equal(result.state,'ready');assert.deepEqual(result.files.map(row=>row.identity),['loverslab:file:111:attachment:77','loverslab:file:111:attachment:78']);assert(result.files.every(row=>row.kind==='attachment'));assert.equal(requests.length,1);assert(!JSON.stringify(result.files.map(row=>row.identity)).includes('secret'));
});
test('LoversLab resolves direct archive response headers without accepting a chooser as a file',async()=>{
 const url='https://www.loverslab.com/files/file/111-mod/';const links=[link('?do=download','Download')];
 const responses={[url+'?do=download']:{body:'archive body is cancelled',headers:{'content-disposition':'attachment; filename="exact-release.zip"','content-type':'application/zip'}}};
 const {result}=await discover({url,document:documentFixture({links}),responses});assert.equal(result.state,'ready');assert.equal(result.files.length,1);assert.equal(result.files[0].identity,'loverslab:file:111:direct');assert.equal(result.files[0].name,'exact-release.zip');assert.equal(result.files[0].kind,'attachment');
});
test('LoversLab refuses an unresolved or restricted chooser without returning a guessed attachment',async()=>{
 const url='https://www.loverslab.com/files/file/111-mod/';const links=[link('?do=download','Download')];
 for(const response of [{body:'<main>Sign in to view this attachment</main>',headers:{'content-type':'text/html'}},{body:'Access restricted',status:403}]){
  const {result}=await discover({url,document:documentFixture({links}),responses:{[url+'?do=download']:response}});assert.equal(result.state,'error');assert.equal(result.files,undefined);
 }
});
test('shared Drive and MEGA folders remain visibly distinct from direct files',async()=>{
 const links=[link('https://drive.google.com/drive/folders/abc12345?resourcekey=secret','Drive folder'),link('https://mega.nz/folder/xyz12345#private-key','MEGA folder'),link('https://mega.nz/#F!old12345!private-key','Legacy folder')];
 const {result}=await discover({url:'https://mods.example.com/release',document:documentFixture({links})});assert.equal(result.state,'ready');assert.deepEqual(result.files.map(row=>row.kind),['folder','folder','folder']);assert.deepEqual(result.files.map(row=>row.identity),['drive:folder:abc12345','mega:folder:xyz12345','mega:folder:old12345']);
});
test('LoversLab redirect to a different mod never supplies choices for the original source',async()=>{
 const {result}=await discover({url:'https://www.loverslab.com/files/file/222-other/',source:'https://www.loverslab.com/files/file/111-mod/'});assert.equal(result.state,'error');assert.match(result.message,/exact LoversLab/);
});
test('Drive and MEGA identities exclude private resource and decryption keys',async()=>{
 const links=[link('https://drive.google.com/file/d/abc12345/view?resourcekey=secret','Drive archive'),link('https://mega.nz/file/xyz12345#private-decryption-key','MEGA archive')];
 const {result}=await discover({url:'https://mods.example.com/release',document:documentFixture({links})});assert.equal(result.state,'ready');assert.deepEqual(result.files.map(row=>row.identity),['drive:file:abc12345','mega:file:xyz12345']);
});
test('restricted page stays restricted when a visible sign-out control proves an account indicator',()=>{
 const result=observe({document:documentFixture({body:'Join to unlock this post',controls:[link('/logout','Log out')]})});assert.equal(result.loginState,'signedIn');assert.equal(result.status,'restricted');
});
test('password settings are not mistaken for a signed-out account',()=>{
 const password={getClientRects:()=>[{}]};const result=observe({url:'https://www.patreon.com/settings/security',document:documentFixture({title:'Account settings',passwords:[password]})});assert.equal(result.loginState,'unknown');assert.notEqual(result.status,'signInRequired');
});
test('a visible password on the site login page asks for explicit account resume',()=>{
 const result=observe({url:'https://www.patreon.com/login',document:documentFixture({title:'Log in',passwords:[{getClientRects:()=>[{}]}]})});assert.equal(result.loginState,'signedOut');assert.equal(result.status,'signInRequired');assert.equal(result.guidance,'siteLogin');
});
test('embedded Google refusal is scoped to the legitimate identity provider',()=>{
 const document=documentFixture({body:'This browser or app may not be secure'});const google=observe({url:'https://accounts.google.com/signin/rejected',document});assert.equal(google.status,'embeddedLoginBlocked');assert.equal(google.guidance,'embeddedIdentityProvider');assert.equal(observe({url:'https://other.example.com/signin/rejected',document}),null);
});
test('an OAuth provider success page cannot assert login at the owning website',()=>{
 const result=observe({url:'https://accounts.google.com/home',document:documentFixture({controls:[link('/logout','Sign out')]})});assert.equal(result,null);
});
test('observations return only categorical facts, never page content or identifiers',()=>{
 const result=observe({document:documentFixture({body:'user@example.com secret page content',controls:[link('/logout','Log out')]})});assert.deepEqual(Object.keys(result).sort(),['guidance','loginState','origin','status']);assert(!JSON.stringify(result).includes('user@example.com'));assert(!JSON.stringify(result).includes('secret'));
});

test('visible Patreon membership restrictions cannot become a new page baseline',()=>{
 const policy=fs.readFileSync(path.join(root,'native/crates/modhunter-desktop/src/watchtower_policy.rs'),'utf8');
 const body=/const CAPTURE_BODY: &str = r#"([\s\S]*?)"#;/.exec(policy)[1];
 const location=new URL('https://www.patreon.com/posts/release-111');
 const document=documentFixture({body:'Join to unlock this post'});document.readyState='complete';document.documentURI=location.href;
 const result=vm.runInNewContext('(()=>{'+body+'})()',{cfg:{url:location.href,selector:'',ignore:[]},location,document,URL,getComputedStyle:()=>({visibility:'visible',display:'block'})});
 assert.equal(result.status,'accessRestricted');assert.equal(result.text,'');
});

test('provider dates retain their provenance and unknown/future dates never become check time',async()=>{
 const known=(await discover({docs:{111:post()}})).result.files[0];
 assert.equal(known.publishedAt,'2026-01-01T00:00:00Z');assert.equal(known.publicationEvidence,'patreonPost');assert.equal(known.accessEvidence,'patreonAttachment');
 for(const date of ['', 'not a date','2099-01-01T00:00:00Z']){
  const row=(await discover({docs:{111:post('111',['10'],{published_at:date})}})).result.files[0];assert.equal(row.publishedAt,'');
 }
 const data=post('111',[],{content:'<a href="https://mega.nz/file/abcdefgh#private-key">External file</a>'});
 const row=(await discover({docs:{111:data}})).result.files[0];assert.equal(row.accessEvidence,'visibleLink','post membership cannot assert access to an external file');
});
test('LoversLab release metadata must name the exact page and attachment dates are distinct evidence',async()=>{
 const url='https://www.loverslab.com/files/file/111-mod/';
 const metadata=[{'@type':'SoftwareApplication',url:'https://www.loverslab.com/files/file/222-other/',dateModified:'2026-03-01T00:00:00Z'},{'@type':'SoftwareApplication',url,dateModified:'2026-01-01T00:00:00Z'}];
 const first=await discover({url,document:documentFixture({metadata,links:[link('?do=download&r=77','A.zip')]})});
 assert.equal(first.result.files[0].publishedAt,'2026-01-01T00:00:00Z');assert.equal(first.result.files[0].publicationEvidence,'loversLabRelease');
 const row={querySelector:selector=>selector==='time[datetime]'?{getAttribute:()=> '2026-02-01T00:00:00Z'}:null};
 const second=await discover({url,document:documentFixture({metadata,links:[link('?do=download&r=77','A.zip',null,row)]})});
 assert.equal(second.result.files[0].publishedAt,'2026-02-01T00:00:00Z');assert.equal(second.result.files[0].publicationEvidence,'loversLabAttachment');
 const undated=await discover({url,document:documentFixture({metadata:metadata.slice(0,1),links:[link('?do=download&r=77','A.zip')]})});assert.equal(undated.result.files[0].publishedAt,'');
});

function folderFixture(extra={}){
 const key='abcdefghijklmnopqrstuv',url='https://mega.nz/folder/pub12345#'+key;
 const file={h:'file1234',p:'root1234',t:0,name:'Release.zip',s:64,ts:100};
 const rootNode={h:'root1234',t:1,name:'Shared release',tf:1,td:0,tb:64};
 const M={RootID:'root1234',d:{root1234:rootNode,file1234:file},c:{root1234:{file1234:1}}};
 const listeners={};const document={addEventListener:(event,listener)=>{listeners[event]=listener}};
 Object.defineProperty(document,'cookie',{get(){throw Error('Cookies must never be read')}});
 Object.defineProperty(file,'k',{get(){throw Error('Decryption keys must never be read')}});
 const window={M,pfid:'pub12345',fminitialized:true,loadfm:{loaded:1,loading:false},$: {},dl_queue:[]};
 Object.defineProperty(window,'u_sid',{get(){throw Error('Account tokens must never be read')}});
 const sandbox={window,document,location:new URL(url),URL,Set,cfg:{source:url,key:'proof_record',mode:'inspect',...extra}};
 const run=()=>JSON.parse(JSON.stringify(vm.runInNewContext(megaFolder,sandbox,{timeout:1000})));
 return {sandbox,window,M,file,rootNode,listeners,run,url,key};
}
test('MEGA folder inspection requires a complete tree and returns one exact child without exporting keys',()=>{
 const f=folderFixture(),result=f.run();assert.equal(result.state,'ready');assert.equal(result.file.fileId,'file1234');assert.equal(result.url,f.url+'/file/file1234');assert.equal(result.nodeCount,2);assert(!JSON.stringify(result.file).includes(f.key));
 f.rootNode.tf=2;assert.match(f.run().message,/complete folder inventory/);
 f.rootNode.tf=1;delete f.M.d.file1234;assert.equal(f.run().state,'error');
});
test('MEGA ambiguous, multipart, oversized, undecrypted and still-loading folders never produce an exact offer',()=>{
 const cases=[
  f=>{f.M.d.file5678={h:'file5678',p:'root1234',t:0,name:'Other.7z',s:64,ts:100};f.M.c.root1234.file5678=1;f.rootNode.tf=2;f.rootNode.tb=128;},
  f=>{f.file.name='Release.part1.rar'},
  f=>{f.file.s=17179869185;f.rootNode.tb=f.file.s},
  f=>{f.file.a='undecrypted attributes'},
  f=>{f.window.loadfm.loading=true},
 ];
 for(const mutate of cases){const f=folderFixture();mutate(f);assert.notEqual(f.run().state,'ready');}
});
test('MEGA exact blob observation rejects another child and rechecks the complete folder at download time',()=>{
 const f=folderFixture(),expected=f.run().file;f.sandbox.cfg={...f.sandbox.cfg,mode:'arm',expected};assert.equal(f.run().state,'ready');
 const anchor={id:'dllink',href:'blob:https://mega.nz/exact-blob',download:'Release.zip',closest(){return this}};
 f.window.$.memIOSaveAttempt='file1234';f.window.dl_queue=[{id:'file1234',n:'Release.zip',size:64}];
 f.listeners.click({target:anchor});const proof=f.window.proof_record.proof;assert.equal(proof.blobUrl,anchor.href);assert.equal(proof.file.fileId,'file1234');assert(!JSON.stringify(proof).includes(f.key));
 f.window.$.memIOSaveAttempt='other123';f.window.dl_queue=[{id:'other123',n:'Release.zip',size:64}];f.listeners.click({target:anchor});assert.equal(f.window.proof_record.proof,null);
 f.window.$.memIOSaveAttempt='file1234';f.window.dl_queue=[{id:'file1234',n:'Release.zip',size:64}];f.rootNode.tf=2;f.listeners.click({target:anchor});assert.equal(f.window.proof_record.proof,null);
});
test('MEGA navigation and changed child metadata invalidate a prior selection',()=>{
 const f=folderFixture();f.sandbox.cfg.expected=f.run().file;f.file.s=65;f.rootNode.tb=65;assert.match(f.run().message,/exact file changed/);
 f.sandbox.location=new URL('https://mega.nz/folder/other123#'+f.key);assert.match(f.run().message,/different MEGA folder/);
});
