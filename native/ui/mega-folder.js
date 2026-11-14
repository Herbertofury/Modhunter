// Same-session, read-only MEGA folder inspection and exact native-download
// observation. No cookie, account token, node key or decrypted bytes are read.
// The share fragment is used only in the transient exact child page address.
// The provider performs its normal browser download; this code observes it.
(() => {
 const fail=message=>{throw Error(message)},valid=id=>typeof id==='string'&&/^[A-Za-z0-9_-]{8}$/.test(id);
 const here=new URL(location.href),fixture=cfg.fixtureOrigin===here.origin&&/^https?:\/\/(?:127\.0\.0\.1|\[::1\])(?::\d+)?$/.test(here.origin);
 if(!fixture&&!['https://mega.nz','https://mega.io'].includes(here.origin))return {state:'error',message:'Open the original MEGA folder in its own browser session.'};
 function folderAddress(raw){
  const url=new URL(raw,here);if(url.origin!==here.origin&&!['https://mega.nz','https://mega.io'].includes(url.origin))fail('The folder moved to an unsupported site.');
  let id,key,selected='';const modern=/^\/folder\/([A-Za-z0-9_-]{8})\/?$/.exec(url.pathname);
  if(modern){id=modern[1];const hash=/^#([A-Za-z0-9_-]{22})(?:\/(file|folder)\/([A-Za-z0-9_-]{8}))?$/.exec(url.hash);if(!hash)fail('The folder link needs its valid access key. Open it and complete the supported website flow.');key=hash[1];if(hash[2]==='file')selected=hash[3];}
  else{const legacy=/^#F!([A-Za-z0-9_-]{8})!([A-Za-z0-9_-]{22})(?:[!?]([A-Za-z0-9_-]{8}))?$/.exec(url.hash);if(!legacy)fail('This is not a supported exact MEGA folder link.');id=legacy[1];key=legacy[2];if(url.hash.includes('?'))selected=legacy[3]||'';}
  return {id,key,selected};
 }
 function inspect(){
  const requested=folderAddress(cfg.source),actual=folderAddress(location.href);
  if(requested.id!==actual.id)fail('The browser opened a different MEGA folder.');
  const model=window.M,loader=window.loadfm;
  if(window.pfid!==actual.id||!window.fminitialized||!loader?.loaded||loader.loading)return {state:'pending'};
  if(!model||!valid(model.RootID)||!model.d||!model.c)fail('MEGA did not expose a complete folder tree.');
  const root=model.d[model.RootID];
  if(!root||root.t!==1||root.a||typeof root.name!=='string')fail('This folder is inaccessible or not fully decrypted in its browser.');
  const keys=Object.keys(model.d);if(keys.length>10000)fail('This folder exceeds the complete 10,000-node inspection limit. No partial choice was accepted.');
  const seen=new Set(),pending=[model.RootID],files=[];let folders=0,total=0;
  while(pending.length){
   const id=pending.pop();if(!valid(id)||seen.has(id))fail('The folder tree contains an invalid or repeated node.');seen.add(id);
   const node=model.d[id];if(!node||node.h!==id||node.a||typeof node.name!=='string'||!node.name||node.name.length>240||/[\u0000-\u001f\u007f]/.test(node.name))fail('A folder node is missing or cannot be inspected completely.');
   if(node.t===1){
    if(id!==model.RootID)folders++;
    const children=Object.keys(model.c[id]||{});if(children.length>10000||seen.size+pending.length+children.length>10000)fail('This folder exceeds the complete inspection limit.');
    for(const child of children){if(model.d[child]?.p!==id)fail('The folder ownership tree changed during inspection.');pending.push(child)}
   }else if(node.t===0){
    if(node.fv||!Number.isSafeInteger(node.s)||node.s<0||!Number.isSafeInteger(node.ts)||node.ts<0)fail('A current file has incomplete provider metadata.');
    total+=node.s;if(!Number.isSafeInteger(total))fail('The folder size cannot be represented safely.');files.push(node);
   }else fail('The folder contains an unsupported node type.');
  }
  if(keys.some(id=>!seen.has(id)&&!model.d[id]?.fv))fail('The folder contains current nodes outside its complete ownership tree.');
  // Provider totals describe the complete current tree. A partial display,
  // filtered view, missing child, undecrypted node or incremental load fails.
  if(!Number.isSafeInteger(root.tf)||!Number.isSafeInteger(root.td)||!Number.isSafeInteger(root.tb)||root.tf!==files.length||root.td!==folders||root.tb!==total)fail('The browser has not provided the complete folder inventory.');
  const multipart=/\.(?:part\d+\.rar|(?:7z|zip)\.\d{3}|r\d{2}|z\d{2}|\d{3})$/i;
  if(files.some(node=>multipart.test(node.name)))fail('Multipart archives require manual review; no single-file choice was accepted.');
  const eligible=files.filter(node=>/\.(zip|7z|rar)$/i.test(node.name));
  if(eligible.length!==1)fail(eligible.length?'This folder contains multiple eligible archives. Choose an exact file manually.':'This folder has no eligible archive.');
  const node=eligible[0];if(node.s===0||node.s>17179869184)fail('This archive is empty or exceeds the 16 GiB download bound.');
  if(requested.selected&&requested.selected!==node.h)fail('The selected child is not this folder’s unique eligible archive.');
  const file={publicId:actual.id,rootId:model.RootID,fileId:node.h,parentId:node.p,name:node.name,bytes:node.s,modifiedAt:node.ts};
  if(cfg.expected&&Object.keys(file).some(key=>file[key]!==cfg.expected[key]))fail('The folder’s exact file changed. Find files again before downloading.');
  return {state:'ready',file,url:here.origin+'/folder/'+actual.id+'#'+actual.key+'/file/'+node.h,nodeCount:seen.size,totalBytes:total};
 }
 try{
  const result=inspect();if(result.state!=='ready'||cfg.mode!=='arm')return result;
  const existing=window[cfg.key];if(existing?.armed)return {state:'ready',file:result.file};
  const record={armed:true,proof:null,error:''};window[cfg.key]=record;
  document.addEventListener('click',event=>{
   const anchor=event.target?.closest?.('a');if(anchor?.id!=='dllink')return;
   record.proof=null;
   try{
    const checked=inspect();if(checked.state!=='ready')fail('Folder access changed before download.');
    const file=checked.file,uri=new URL(anchor.href),active=window.$?.memIOSaveAttempt;
    if(uri.protocol!=='blob:'||!uri.href.startsWith('blob:'+here.origin+'/'))fail('The provider did not issue its exact browser archive blob.');
    if(!valid(active))fail('MEGA did not identify the exact file being saved.');
    const queue=window.dl_queue;if(!Array.isArray(queue)||queue.length>10000)fail('The provider download queue is unavailable or too large.');
    const matches=queue.filter(row=>row&&row.id===file.fileId&&(row.ph||row.id)===active&&!row.zipid&&!row.preview&&row.n===file.name&&row.size===file.bytes);
    if(matches.length!==1||anchor.download!==file.name)fail('The provider download no longer identifies one exact file.');
    record.proof={file,blobUrl:uri.href,observedAt:Date.now()};record.error='';
   }catch(error){record.error=String(error.message||error).slice(0,300)}
  },true);
  return {state:'ready',file:result.file};
 }catch(error){return {state:'error',message:String(error.message||error).slice(0,500)}}
})()
