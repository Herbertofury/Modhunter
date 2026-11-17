const {test}=require('node:test'),a=require('node:assert/strict'),art=require('../mo2_update_arsenal/web/siege_art.js');
const rows=[{kind:'mod',url:'https://cdn.modrinth.com/a.png'},{kind:'author',url:'https://staticdelivery.nexusmods.com/b.png'}];
test('explicit modes and stable mixed choice',()=>{a.equal(art.select(rows,'off',0),null);a.equal(art.select(rows,'author',0).kind,'author');a.equal(art.select(rows,'mod',1).kind,'mod');a.equal(art.select(rows,'random',1).kind,'author');a.equal(art.select(rows,'random',2).kind,'mod')});
test('reject unsafe sources and malformed inputs',()=>{for(const u of ['http://cdn.modrinth.com/a','https://user:secret@cdn.modrinth.com/a','https://cdn.modrinth.com.evil.test/a','data:image/svg+xml,x','file:///tmp/a','https://cdn.modrinth.com:123/a'])a.equal(art.valid(u),'');a.equal(art.select(null,'random',1),null)});
test('cover crop preserves ratio for portrait and landscape',()=>{a.deepEqual(art.cover(200,100,10),[-20,-10,40,20]);a.deepEqual(art.cover(100,200,10),[-10,-20,20,40])});
test('one fetch per URL, fail closed, clipping, clear',async()=>{
 let loads=0;global.createImageBitmap=async()=>({width:128,height:64,close(){}});
 global.fetch=async(url,opts)=>{loads++;a.equal(opts.redirect,'error');a.equal(opts.credentials,'omit');a.equal(opts.referrerPolicy,'no-referrer');const bytes=new Uint8Array(33);bytes.set([137,80,78,71,13,10,26,10,0,0,0,13,73,72,68,82]);new DataView(bytes.buffer).setUint32(16,200);new DataView(bytes.buffer).setUint32(20,100);return new Response(bytes)};
 const renderer=art.create(),calls=[],ctx={save:()=>calls.push('save'),clip:()=>calls.push('clip'),drawImage:()=>calls.push('draw'),restore:()=>calls.push('restore')};
 a.equal(renderer.draw(ctx,rows,'mod',0,10),false);await new Promise(r=>setImmediate(r));renderer.draw(ctx,rows,'mod',0,10);a.equal(loads,1);a.deepEqual(calls,['save','clip','draw','restore']);renderer.clear();renderer.draw(ctx,rows,'mod',0,10);a.equal(loads,2);renderer.clear();
});

test('reject bytes and dimensions before image decoding',async()=>{await a.rejects(art.boundedImage(new Response('bad')));await a.rejects(art.boundedImage(new Response('',{headers:{'content-length':9*1024*1024}})));const bytes=new Uint8Array(33);bytes.set([137,80,78,71,13,10,26,10,0,0,0,13,73,72,68,82]);new DataView(bytes.buffer).setUint32(16,100000);new DataView(bytes.buffer).setUint32(20,100000);await a.rejects(art.boundedImage(new Response(bytes)));});
