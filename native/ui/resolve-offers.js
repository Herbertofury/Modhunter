// Host-injected, bounded discovery. Result is untrusted metadata, never permission
// to download. Cookies stay in the site's browser. No secrets are returned.
window[cfg.key]={state:'pending'};
(async()=>{
 const here=new URL(location.href),source=new URL(cfg.source);
 const finish=value=>{window[cfg.key]=value;};
 if(here.origin!==source.origin)throw Error('The source redirected to another site. Review its address.');
 const title=document.title.toLowerCase();
 if(/just a moment|verify you are human|checking your browser|attention required/.test(title))throw Error('Site verification required. Open this page and finish its challenge.');
 const visible=e=>e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden';
 if([...document.querySelectorAll('input[type=password]')].some(visible))throw Error('Sign in to this site, then find files again.');
 const files=[],seen=new Set();
 function add(raw,name){
  try{const u=new URL(raw,here);if(u.protocol!=='https:'||u.username||u.password||u.href.length>8192||seen.has(u.href)||files.length>=200)return;seen.add(u.href);files.push({url:u.href,name:String(name||'Review download').slice(0,240)});}catch{}
 }
 async function json(url){const r=await fetch(url,{credentials:'include',redirect:'error',signal:AbortSignal.timeout(12000)});if(!r.ok)throw Error('Source access failed ('+r.status+'). Sign-in and file access are separate.');if(!r.body)throw Error('Source returned no readable response.');const reader=r.body.getReader(),chunks=[];let size=0;try{for(;;){const part=await reader.read();if(part.done)break;size+=part.value.byteLength;if(size>2000000){await reader.cancel();throw Error('Source metadata too large; choose a specific release.');}chunks.push(part.value);}}finally{reader.releaseLock();}const bytes=new Uint8Array(size);let at=0;for(const part of chunks){bytes.set(part,at);at+=part.byteLength;}return JSON.parse(new TextDecoder().decode(bytes));}
 const archive=/\.(zip|7z|rar)$/i;
 if(/(^|\.)patreon\.com$/.test(here.hostname)){
  const postId=u=>/^\/(?:[^/]+\/)?posts\/(?:[^/]*-)?(\d+)\/?$/.exec(new URL(u,here).pathname)?.[1];
  const post=postId(here.href);if(!post)throw Error('Track a specific Patreon release post to resolve its attachments, not the creator feed.');
  const detail=id=>json(here.origin+'/api/posts/'+encodeURIComponent(id)+'?include=attachments_media,campaign&fields%5Bpost%5D=post_file,content,content_json_string,current_user_can_view,published_at&fields%5Bmedia%5D=file_name,download_url,created_at');
  const release=await detail(post),data=release.data,a=data?.attributes;
  if(data?.id!==post||a?.current_user_can_view!==true)throw Error('This post is not confirmed accessible to the selected account. Open it to review your access.');
  const campaign=data.relationships?.campaign?.data?.id;
  function attachments(doc,since){
   const a=doc.data?.attributes;if(a?.current_user_can_view!==true)return;
   for(const media of (doc.included||[]).slice(0,200)){const f=media.attributes||{};if(media.type==='media'&&archive.test(f.file_name||'')&&(!since||Date.parse(f.created_at)>=since))add(f.download_url,f.file_name);}
   if(!since&&archive.test(a.post_file?.name||''))add(a.post_file.url,a.post_file.name);
  }
  attachments(release,0);
  const links=[];const parsed=new DOMParser().parseFromString(String(a.content||'').slice(0,1000000),'text/html');for(const node of [...parsed.querySelectorAll('a[href]')].slice(0,1000))links.push(node.getAttribute('href'));
  try{const q=[JSON.parse(String(a.content_json_string||'null').slice(0,1000000))];let budget=5000;while(q.length&&budget-->0){const v=q.pop();if(typeof v==='string'&&/^https:\/\//.test(v))links.push(v);else if(Array.isArray(v))q.push(...v.slice(0,100));else if(v&&typeof v==='object')q.push(...Object.values(v).slice(0,100));}}catch{}
  if(!files.length&&campaign&&Number.isFinite(Date.parse(a.published_at))){let count=0;const ids=new Set();for(const link of links){try{const u=new URL(link,here);if(u.origin!==here.origin)continue;const id=postId(u.href);if(!id||id===post||ids.has(id)||count>=3)continue;ids.add(id);count++;const linked=await detail(id);if(linked.data?.id===id&&linked.data?.relationships?.campaign?.data?.id===campaign)attachments(linked,Date.parse(a.published_at)-14*86400000);}catch{}}}
  for(const link of links){try{const u=new URL(link,here);if(['mega.nz','mega.io','drive.google.com'].includes(u.hostname))add(u.href,'Review external download');}catch{}}
 }else if(/(^|\.)loverslab\.com$/.test(here.hostname)){
  const id=/\/files\/file\/(\d+)/.exec(source.pathname)?.[1];if(!id||/\/files\/file\/(\d+)/.exec(here.pathname)?.[1]!==id)throw Error('Use the exact LoversLab file page, not an unrelated topic or file.');
  for(const link of [...document.querySelectorAll('a[href]')].slice(0,10000)){try{const u=new URL(link.getAttribute('href'),here);if(u.origin!==here.origin||/\/files\/file\/(\d+)/.exec(u.pathname)?.[1]!==id||u.searchParams.get('do')!=='download')continue;const row=link.closest('li,tr,.ipsDataItem');const name=row?.querySelector('.ipsDataItem_title')?.textContent?.trim()||link.getAttribute('download')||link.textContent.trim()||'Open file chooser';add(u.href,name);}catch{}}
 }else{
  for(const a of [...document.querySelectorAll('a[href]')].slice(0,10000)){try{const u=new URL(a.getAttribute('href'),here);const name=a.getAttribute('download')||decodeURIComponent(u.pathname.split('/').pop()||'');if(archive.test(name))add(u.href,name);else if(['mega.nz','mega.io','drive.google.com'].includes(u.hostname))add(u.href,a.textContent.trim()||'Review external host');}catch{}}
 }
 finish({state:'ready',files});
})().catch(e=>{window[cfg.key]={state:'error',message:String(e.message||e).slice(0,500)};});
