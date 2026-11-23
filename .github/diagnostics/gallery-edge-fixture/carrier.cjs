'use strict';
const {createHash}=require('node:crypto');
const {createServer}=require('node:http');
const {readFile}=require('node:fs/promises');
const {join}=require('node:path');
const {inflateSync}=require('node:zlib');
const sha=b=>createHash('sha256').update(b).digest('hex');
const urlFiles=new Map([['/',['fixture.html','text/html']],['/fixture.css',['fixture.css','text/css']],
 ['/fixture.mjs',['fixture.mjs','text/javascript']],['/warm-geometry.mjs',['warm-geometry.mjs','text/javascript']]]);
async function createCarrier(){
 const inputs=new Map();
 for(const [path,[filename,type]] of urlFiles)inputs.set(path,{data:await readFile(join(__dirname,filename)),type});
 const inputsSha=Object.fromEntries([...inputs.values()].map(({data},i)=>[[...urlFiles.values()][i][0],sha(data)]));
 if(inputsSha['warm-geometry.mjs']!=='e93d1c1776ac1d473f7c3785c6949e17c432d13f290d1b96767baff4ab16e40e')throw Error('unreviewed exact warm model');
 const config={cards:11207,count:11263,referenceSource:'1d50b1ddb4c9ec1268994664f0e6b95249a67517',
   fixtureHashes:{content:sha(Buffer.concat([inputs.get('/').data,inputs.get('/fixture.mjs').data])),css:inputsSha['fixture.css']}};
 inputs.set('/fixture-config.mjs',{type:'text/javascript',data:Buffer.from('export const config='+JSON.stringify(config)+';\n')});
 const server=createServer((req,res)=>{
  const permitted=req.method==='GET'&&req.headers.host===origin?.host&&inputs.get(req.url);
  if(!permitted){res.writeHead(404);res.end();return;}
  res.writeHead(200,{'content-type':permitted.type,'content-length':permitted.data.length,'cache-control':'no-store',
   'content-security-policy':"default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'none'; base-uri 'none'; frame-ancestors 'none'",
   'x-content-type-options':'nosniff'});res.end(permitted.data);
 });
 let origin;
 await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
 origin=new URL('http://127.0.0.1:'+server.address().port+'/');
 return {origin:origin.href,config,inputsSha,close:()=>new Promise((resolve,reject)=>server.close(e=>e?reject(e):resolve()))};
}
const paeth=(a,b,c)=>{const x=a+b-c,pa=Math.abs(x-a),pb=Math.abs(x-b),pc=Math.abs(x-c);return pa<=pb&&pa<=pc?a:pb<=pc?b:c;};
function decodeOwnedScreenshot(png){
 const sig='89504e470d0a1a0a';
 if(png.subarray(0,8).toString('hex')!==sig)throw Error('expected lossless PNG screenshot');
 let w,h,bpp,finished=false;
 const packed=[];
 for(let off=8;off+12<=png.length;){
  const size=png.readUInt32BE(off),kind=png.toString('ascii',off+4,off+8),b=png.subarray(off+8,off+8+size);
  off+=12+size;
  if(off>png.length)throw Error('PNG truncated chunk');
  if(kind==='IHDR'){
   if(b.length!==13||w||b[8]!==8||![2,6].includes(b[9])||b[10]||b[11]||b[12])throw Error('unexpected PNG profile');
   w=b.readUInt32BE(0);h=b.readUInt32BE(4);bpp=b[9]===6?4:3;
   if(!w||!h||w>2048||h>1536)throw Error('unsupported owned viewport dimensions');
  }
  if(kind==='IDAT')packed.push(b);
  if(kind==='IEND'){finished=off===png.length;break;}
 }
 if(!finished||!w||!packed.length)throw Error('bad PNG chunks');
 const stride=w*bpp, raw=inflateSync(Buffer.concat(packed),{maxOutputLength:(stride+1)*h}), rows=[];
 if(raw.length!==(stride+1)*h)throw Error('incorrect PNG pixel data length');
 for(let y=0;y<h;y++){
  const start=y*(stride+1),type=raw[start],row=Buffer.from(raw.subarray(start+1,start+1+stride));
  if(type>4)throw Error('unknown PNG row filter');
  for(let x=0;type&&x<stride;x++){
   const a=x>=bpp?row[x-bpp]:0,b=rows[y-1]?.[x]??0,c=(x>=bpp?rows[y-1]?.[x-bpp]:0)??0;
   row[x]=(row[x]+[0,a,b,(a+b)>>1,paeth(a,b,c)][type])&255;
  }
  rows.push(row);
 }
 const all=Buffer.concat(rows);
 return {w,h,pixel(point){
  const x=Math.floor(point?.x),y=Math.floor(point?.y);
  if(!(x>=0&&y>=0&&x<w&&y<h))throw Error('sample outside viewport');
  return rows[y].subarray(x*bpp,x*bpp+3).toString('hex');
 },pixelsSha:sha(all)};
}
module.exports={createCarrier,decodeOwnedScreenshot,sha};
