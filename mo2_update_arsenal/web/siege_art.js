/* Decorative, opt-in artwork. Does not change combat or fetch arbitrary URLs. */
(()=>{'use strict';
const hosts=new Set(['staticdelivery.nexusmods.com','cdn.modrinth.com','thunderstore.io','cdn.thunderstore.io','gcdn.thunderstore.io']);
function valid(value){try{const u=new URL(value);return typeof value==='string'&&value.length<=8192&&u.protocol==='https:'&&!u.username&&!u.password&&(!u.port||u.port==='443')&&hosts.has(u.hostname)?u.href:''}catch{return ''}}
const choices=new WeakMap();
function candidates(rows,mode){if(!Array.isArray(rows)||!['mod','author','random'].includes(mode))return [];let modes=choices.get(rows);if(!modes){modes=new Map();choices.set(rows,modes)}if(!modes.has(mode))modes.set(mode,rows.filter(r=>r&&['mod','author'].includes(r.kind)&&(mode==='random'||r.kind===mode)&&valid(r.url)));return modes.get(mode)}
function select(rows,mode,serial){const list=candidates(rows,mode);return list.length?list[Math.abs(Math.trunc(serial)||0)%list.length]:null}
function cover(w,h,size){const ratio=Math.max(2*size/w,2*size/h);return [-w*ratio/2,-h*ratio/2,w*ratio,h*ratio]}
const MAX_BYTES=8*1024*1024,MAX_PIXELS=4*1024*1024;
function dimensions(bytes){
 const b=new Uint8Array(bytes),v=new DataView(bytes),ascii=(p,n)=>String.fromCharCode(...b.slice(p,p+n));
 if(b.length>=24&&ascii(1,3)==='PNG'&&b[0]===137&&b[4]===13&&b[5]===10&&b[6]===26&&b[7]===10&&ascii(12,4)==='IHDR'){for(let p=8;p+12<=b.length;){const len=v.getUint32(p);if(ascii(p+4,4)==='acTL')throw Error('Animated artwork unsupported');if(p+12+len>b.length)throw Error('Truncated PNG');p+=12+len}return [v.getUint32(16),v.getUint32(20)]}
 if(b.length>=30&&ascii(0,4)==='RIFF'&&ascii(8,4)==='WEBP'){
  const kind=ascii(12,4);
  if(kind==='VP8X'){if(b[20]&2)throw Error('Animated artwork unsupported');return [1+b[24]+(b[25]<<8)+(b[26]<<16),1+b[27]+(b[28]<<8)+(b[29]<<16)]}
  if(kind==='VP8 '&&b[23]===157&&b[24]===1&&b[25]===42)return [v.getUint16(26,true)&16383,v.getUint16(28,true)&16383];
  if(kind==='VP8L'&&b[20]===47)return [1+((b[21]|b[22]<<8)&16383),1+((b[22]>>6|b[23]<<2|b[24]<<10)&16383)];
 }
 if(b[0]===255&&b[1]===216){let p=2;while(p+4<=b.length){if(b[p++]!==255)break;while(b[p]===255)p++;const marker=b[p++];if(marker===217||marker===218)break;const len=v.getUint16(p);if(len<2||p+len>b.length)break;if([192,193,194,195,197,198,199,201,202,203,205,206,207].includes(marker)&&len>=7)return [v.getUint16(p+5),v.getUint16(p+3)];p+=len}}
 throw Error('Unsupported artwork encoding');
}
async function boundedImage(response){
 if(!response.ok||Number(response.headers.get('content-length'))>MAX_BYTES){await response.body?.cancel();throw Error('Artwork unavailable or too large')}
 const reader=response.body.getReader(),chunks=[];let size=0;
 try{while(true){const {done,value}=await reader.read();if(done)break;size+=value.byteLength;if(size>MAX_BYTES)throw Error('Artwork too large');chunks.push(value)}}catch(e){await reader.cancel();throw e}
 const blob=new Blob(chunks),bytes=await blob.arrayBuffer(),[width,height]=dimensions(bytes);
 if(!width||!height||width>8192||height>8192||width*height>MAX_PIXELS)throw Error('Artwork dimensions exceed display budget');
 return blob;
}
function create(){
 const images=new Map();
 return {clear(){for(const entry of images.values()){entry.abort.abort();entry.image?.close()}images.clear()},
 draw(ctx,rows,mode,serial,size){
  const row=select(rows,mode,serial);if(!row)return false;
  let entry=images.get(row.url);
  if(!entry){
   if(images.size>=128)return false; // Explicit session display budget; original art remains playable.
   entry={image:null,objectURL:null,abort:new AbortController()};images.set(row.url,entry);
   // Reject redirects before following them, including redirects off an approved CDN.
   fetch(valid(row.url),{mode:'cors',credentials:'omit',referrerPolicy:'no-referrer',redirect:'error',signal:entry.abort.signal})
    .then(boundedImage)
    .then(async blob=>{if(entry.abort.signal.aborted)return;const [w,h]=dimensions(await blob.arrayBuffer()),ratio=Math.min(1,128/Math.max(w,h));const bitmap=await createImageBitmap(blob,{resizeWidth:Math.max(1,Math.round(w*ratio)),resizeHeight:Math.max(1,Math.round(h*ratio)),resizeQuality:'high'});if(entry.abort.signal.aborted){bitmap.close();return}entry.image=bitmap})
    .catch(()=>{}); // Retain original silhouette; do not retry on every frame.
  }
  const image=entry.image;if(!image?.width)return false;
  ctx.save();ctx.clip();ctx.drawImage(image,...cover(image.width,image.height,size));ctx.restore();return true;
 }}
}

const api={valid,candidates,select,cover,dimensions,boundedImage,create};if(typeof module!=='undefined'&&module.exports)module.exports=api;else window.SiegeArt=api;
})();
