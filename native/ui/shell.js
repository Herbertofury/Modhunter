'use strict';
(() => {
 const $=id=>document.getElementById(id);
 const api=window.__TAURI__;
 let state={tabs:[],activeId:null,connected:false},messageTimer=0,disposed=false;
 const busy=new Set(),tabNodes=new Map();
 function message(text){clearTimeout(messageTimer);$('message').textContent=String(text||'');$('message').title=String(text||'');messageTimer=setTimeout(()=>{$('message').textContent=''},12000)}
 function render(next){
  if(Number.isFinite(next.epoch)&&Number.isFinite(state.epoch)&&next.epoch<state.epoch)return;
  state=next;
  const connected=!!state.connected,auditing=!!state.auditing||busy.has('pluginAudit'),waiting=auditing||busy.has('connect')||busy.has('discover')||!!state.importing;
  $('status').className='status'+(waiting?' busy':connected?' live':state.endpoint?' error':'');
  $('sourceText').textContent=auditing?'Auditing plugins in background…':waiting?'Working in background…':connected?'LIVE MO2 · CONNECTED':state.endpoint?'MO2 DISCONNECTED':state.offlineProfile?state.offlineProfile+' · '+state.offlineModCount.toLocaleString()+' MODS':'OFFLINE PRACTICE';
  $('sourceText').title=state.connectionError||$('sourceText').textContent;
  $('connect').textContent=busy.has('connect')?'CONNECTING…':connected?'LINKED':'CONNECT';
  $('connect').disabled=busy.has('connect');
  $('discover').disabled=busy.has('discover');
  $('discover').textContent=busy.has('discover')?'FINDING…':'FIND MO2';
  $('importMo2').disabled=!!state.endpoint||!!state.importing||busy.has('importMo2')||busy.has('refreshProfile');
  $('refreshProfile').disabled=!!state.endpoint||!state.offlineProfile||!!state.importing||busy.has('importMo2')||busy.has('refreshProfile');
  $('pluginAudit').disabled=!!state.importing||busy.has('pluginAudit')||busy.has('importMo2')||busy.has('refreshProfile')||(!state.endpoint&&!state.offlineProfile);
  $('pluginAudit').textContent=auditing?'AUDITING…':'PLUGIN AUDIT';
  $('pluginAudit').title=state.endpoint?'Use Plugin audit in the live MO2 dashboard for its active profile':'Read plugin records and check recorded ESLifier output for the imported profile';
  for(const id of ['configureIni','configureMods','clearPaths','configureEslifierData'])$(id).disabled=!!state.importing||busy.has(id);
  $('clearEslifierData').disabled=!state.eslifierDataPath||!!state.importing||busy.has('clearEslifierData');
  $('disconnect').disabled=!state.endpoint&&!busy.has('connect')&&!busy.has('discover');
  if(state.endpoint&&document.activeElement!==$('endpoint'))$('endpoint').value=state.endpoint;
  const stats=state.stats;
  const audit=state.pluginAnalysis,auditStats=audit?.stats;
  $('metrics').textContent=connected&&Number.isFinite(state.latencyMs)?'MO2 link '+state.latencyMs.toFixed(1)+' ms · live updates':audit?.depth==='records'&&auditStats&&Number.isFinite(auditStats.elapsedMs)?'Plugin audit · '+auditStats.elapsedMs.toFixed(1)+' ms · '+(auditStats.winningPlugins||0).toLocaleString()+' winning plugins':stats?stats.mods.toLocaleString()+' mods · '+stats.elapsedMs.toFixed(1)+' ms · '+stats.metadataCacheHits.toLocaleString()+' cached':'Ready · choose a profile or connect to MO2';
  $('metrics').title=(state.warnings||[]).join('\n')||(audit?.checkedAt?'Plugin evidence checked at '+audit.checkedAt:'Measured import and connection times from the Rust engine');
  for(const [id,path,title] of [['configureIni',state.configPath,'Optional: select the instance configuration for separate MO2 data paths'],['configureMods',state.modsPath,'Optional: explicitly select the mods folder'],['configureEslifierData',state.eslifierDataPath,'Choose ESLifier_Data or ESLifier_FO4_Data for offline conversion history']]){
   $(id).classList.toggle('selected',!!path);$(id).title=path||title;
  }
  const present=new Set(state.tabs.map(t=>t.id));
  for(const [id,node] of tabNodes)if(!present.has(id)){node.remove();tabNodes.delete(id)}
  for(const tab of state.tabs){
   let node=tabNodes.get(tab.id);
   if(!node){
    node=document.createElement('div');node.className='tab';
    const open=document.createElement('button');open.type='button';open.className='tab-open';open.setAttribute('role','tab');open.onclick=()=>command('activate',tab.id);
    const text=document.createElement('span');open.append(text);
    const close=document.createElement('button');close.type='button';close.className='x';close.textContent='×';close.onclick=()=>command('close',tab.id);
    node.append(open,close);tabNodes.set(tab.id,node);$('tabs').append(node);
   }
   const active=state.activeId===tab.id;node.classList.toggle('active',active);
   const open=node.firstElementChild;open.setAttribute('aria-selected',String(active));open.title=tab.url;open.firstElementChild.textContent=(tab.loading?'◌ ':'')+tab.title;
   node.lastElementChild.title='Close '+tab.title;node.lastElementChild.setAttribute('aria-label','Close '+tab.title);
  }
 }
 async function command(kind,value){
  const exclusive=['connect','discover','importMo2','refreshProfile','pluginAudit','configureIni','configureMods','configureEslifierData','clearEslifierData'].includes(kind);
  if(exclusive&&busy.has(kind))return null;
  if(exclusive){busy.add(kind);render(state)}
  try{
   const result=await api.core.invoke('shell_command',{kind,value:value??null});
   if(result?.state)render(result.state);
   if(result?.sessions){$('mo2Sessions').replaceChildren();for(const session of result.sessions){const option=document.createElement('option');option.value=session.endpoint;option.label=session.profileName;$('mo2Sessions').append(option)}$('endpoint').focus()}
   if(result?.message)message(result.message);
   return result;
  }catch(error){message(String(error));return null}
  finally{if(exclusive){busy.delete(kind);render(state)}}
 }
 for(const [id,kind,value] of [['newDash','new','dashboard'],['newSiege','new','siege'],['newRealm','new','realm'],['back','back'],['forward','forward'],['reload','reload'],['open','external'],['copy','copy'],['disconnect','disconnect'],['importMo2','importMo2'],['refreshProfile','refreshProfile'],['pluginAudit','pluginAudit'],['configureIni','configureIni'],['configureMods','configureMods'],['clearPaths','clearPaths'],['configureEslifierData','configureEslifierData'],['clearEslifierData','clearEslifierData']])$(id).onclick=()=>command(kind,value);
 $('connect').onclick=()=>command('connect',$('endpoint').value.trim());
 $('discover').onclick=()=>command('discover');
 $('endpoint').addEventListener('keydown',event=>{if(event.key==='Enter')$('connect').click()});
 document.addEventListener('keydown',event=>{
  if(event.ctrlKey&&event.key.toLowerCase()==='r'){event.preventDefault();command('reload')}
  if(event.ctrlKey&&event.key.toLowerCase()==='w'&&state.activeId){event.preventDefault();command('close',state.activeId)}
  if(event.ctrlKey&&event.key.toLowerCase()==='t'){event.preventDefault();command('new','dashboard')}
 });
 const unlisten=[];
 window.addEventListener('beforeunload',()=>{disposed=true;for(const stop of unlisten)stop()},{once:true});
 async function poll(){if(disposed)return;if(state.endpoint)await command('probe');if(!disposed)setTimeout(poll,5000)}
 async function start(){
  if(!api?.core||!api?.event){message('Open Modhunter.exe to use the Rust desktop.');return}
  unlisten.push(await api.event.listen('modhunter-state',event=>render(event.payload)));
  unlisten.push(await api.event.listen('modhunter-message',event=>message(event.payload)));
  await command('state');await command('ready');poll();
 }
 start().catch(error=>message('Desktop startup: '+error));
})();
