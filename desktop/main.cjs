'use strict';
// Modhunter: a real Chromium-tabbed local-first shell. No exposed Node APIs to
// the game/HTML and no privileged iframe of a remote site.
const {app,BrowserWindow,WebContentsView,ipcMain,shell,clipboard,net,session,dialog}=require('electron');
const path=require('node:path');
const {URL,pathToFileURL,fileURLToPath}=require('node:url');
const fs=require('node:fs');
const {readMo2Profile}=require('./offline_mo2.cjs');

const BAR_HEIGHT=88;
// All-in-One ships Desktop/Modhunter.html beside the Electron source.
// Development mode still uses the canonical repository build as a fallback.
const nearbyHTML=path.join(__dirname,'Modhunter.html');
const sourceHTML=path.join(__dirname,'..','mo2_update_arsenal','web','index.html');
const SAFE_LOCAL_FILE=path.resolve(app.isPackaged
  ?path.join(process.resourcesPath,'Modhunter.html')
  :fs.existsSync(nearbyHTML)?nearbyHTML:sourceHTML);
const CHROME=path.join(__dirname,'chrome.html');
let mainWindow=null,bar=null,nextTab=1,activeId=null,mo2Url=null,offlineCatalog=null;
const tabs=new Map();
function loc(s){try{return new URL(s)}catch{return null}}
function validBridge(v){
  const u=loc(String(v||''));if(!u||u.protocol!=='http:'||!['localhost','127.0.0.1'].includes(u.hostname)||!/^[0-9]+$/.test(u.port))return null;
  const port=Number(u.port);if(port<1024||port>65535||u.username||u.password||u.pathname!=='/'||u.search||u.hash)return null;
  return `http://127.0.0.1:${port}/`;
}
function makePage(mode='dashboard'){
  if(mo2Url){const u=new URL(mo2Url);if(mode==='realm')u.search='?realm=1';else if(mode==='siege')u.search='?realm=1&siege=1';return u.href}
  const u=pathToFileURL(SAFE_LOCAL_FILE);if(mode==='realm')u.search='?realm=1';else if(mode==='siege')u.search='?realm=1&siege=1';return u.href;
}
function viewIsOwned(s){
  const u=loc(s);if(!u)return false;
  if(u.protocol==='file:'){try{return path.resolve(fileURLToPath(u.href))===SAFE_LOCAL_FILE}catch{return false}}
  const base=loc(mo2Url);return !!base&&u.protocol==='http:'&&u.hostname==='127.0.0.1'&&u.port===base.port&&u.pathname==='/'
}
function tabMode(s){const u=loc(s);if(!u)return 'dashboard';return u.searchParams.get('siege')==='1'?'siege':u.searchParams.get('realm')==='1'?'realm':'dashboard'}
function titleFor(mode){return mode==='siege'?'⚔ Modhunter Siege':mode==='realm'?'ᛟ Shiverfell':'✦ Mod Vault'}
function state(){return {tabs:[...tabs.values()].map(t=>({id:t.id,title:t.title,mode:t.mode,url:t.url,loading:t.loading})),activeId,connected:Boolean(mo2Url),offlineProfile:!mo2Url?(offlineCatalog?.profileName||''):'',offlineModCount:!mo2Url?(offlineCatalog?.items?.length||0):0,endpoint:mo2Url||'',version:app.getVersion()}}
function notify(){if(bar&&!bar.webContents.isDestroyed())bar.webContents.send('modhunter:state',state())}
function sizeView(v,isBar){if(!mainWindow)return;const [w,h]=mainWindow.getContentSize();v.setBounds(isBar?{x:0,y:0,width:w,height:BAR_HEIGHT}:{x:0,y:BAR_HEIGHT,width:w,height:Math.max(1,h-BAR_HEIGHT)})}
function activate(id){
  const t=tabs.get(Number(id));if(!t||!mainWindow)return false;
  if(activeId!==null&&tabs.has(activeId)){const old=tabs.get(activeId);mainWindow.contentView.removeChildView(old.view)}
  activeId=t.id;mainWindow.contentView.addChildView(t.view);sizeView(t.view,false);notify();return true
}
function storageFile(){return path.join(app.getPath('userData'),'modhunter-offline-profile.json')}
async function persistImportedCatalog(){
 if(!offlineCatalog)return;
 const destination=storageFile(),tmp=destination+'.tmp';
 await fs.promises.mkdir(path.dirname(destination),{recursive:true});
 await fs.promises.writeFile(tmp,JSON.stringify(offlineCatalog),'utf8');
 await fs.promises.rename(tmp,destination);
}
function loadOfflineCatalog(){
 try{
  const value=JSON.parse(fs.readFileSync(storageFile(),'utf8'));
  if(value&&value.offline===true&&typeof value.profileId==='string'&&Array.isArray(value.items)&&Array.isArray(value.groups))
    offlineCatalog=value;
 }catch{offlineCatalog=null}
}
async function injectOfflineCatalog(tab){
 if(mo2Url||!offlineCatalog||!tab||tab.mode!=='dashboard'||!tab.url.startsWith('file:')||
    tab.view.webContents.isDestroyed())return {ok:false};
 // JSON is encoded as a JS literal; HTML tags/line separators are escaped.
 const data=JSON.stringify(offlineCatalog).replace(/</g,'\\u003c').replace(/\u2028/g,'\\u2028').replace(/\u2029/g,'\\u2029');
 return tab.view.webContents.executeJavaScript('window.modhunterImportCatalog?.('+data+')',true);
}
async function importProfile(){
 if(mo2Url)return {ok:false,error:'Unlink live MO2 before importing a read-only offline profile'};
 const selected=await dialog.showOpenDialog(mainWindow,{title:'Choose profiles/<name>/modlist.txt from your MO2 instance',
  filters:[{name:'MO2 modlist',extensions:['txt']}],properties:['openFile']});
 if(selected.canceled||!selected.filePaths?.length)return {ok:false,error:'Import canceled'};
 try{
  const snapshot=await readMo2Profile(selected.filePaths[0]);
  offlineCatalog=snapshot;
  await persistImportedCatalog();
  let current=[...tabs.values()].find(t=>t.mode==='dashboard'&&t.url.startsWith('file:'));
  if(!current){const id=openTab('dashboard');current=tabs.get(id)}
  if(!current)return {ok:false,error:'Could not open Mod Vault tab'};
  activate(current.id);
  if(!current.view.webContents.isLoading())await injectOfflineCatalog(current);
  notify();
  return {ok:true,count:snapshot.items.length,profileName:snapshot.profileName,
   message:'Imported '+snapshot.items.length+' actual MO2 entries from '+snapshot.profileName+'. Read-only mode.'};
 }catch(ex){return {ok:false,error:'MO2 profile import: '+String(ex?.message||ex)}}
}
function navigateTab(t,s){if(!viewIsOwned(s))return false;t.url=s;t.mode=tabMode(s);t.title=titleFor(t.mode);t.loading=true;notify();if(s.startsWith('file:'))t.view.webContents.loadURL(s);else t.view.webContents.loadURL(s);return true}
function openTab(mode='dashboard',url=null){
  const u=url||makePage(mode);if(!viewIsOwned(u))return false;
  const id=nextTab++;
  const v=new WebContentsView({webPreferences:{nodeIntegration:false,contextIsolation:true,sandbox:true,webSecurity:true,spellcheck:false,webviewTag:false}});
  const t={id,view:v,title:titleFor(tabMode(u)),mode:tabMode(u),url:u,loading:true};tabs.set(id,t);
  v.webContents.setWindowOpenHandler(({url:newUrl})=>{setImmediate(()=>{
    if(viewIsOwned(newUrl))openTab(tabMode(newUrl),newUrl);
    else {const ex=loc(newUrl);if(ex&&['https:','http:'].includes(ex.protocol))shell.openExternal(ex.href).catch(()=>{})}
  });return {action:'deny'}});
  v.webContents.on('will-navigate',(event,dest)=>{if(!viewIsOwned(dest)){event.preventDefault();const ex=loc(dest);if(ex&&['https:','http:'].includes(ex.protocol))shell.openExternal(ex.href).catch(()=>{})}});
  v.webContents.on('did-start-loading',()=>{t.loading=true;notify()});
  v.webContents.on('did-stop-loading',()=>{t.loading=false;notify()});
  v.webContents.on('did-finish-load',()=>{if(!mo2Url&&t.mode==='dashboard')injectOfflineCatalog(t).catch(e=>console.warn('Offline profile import:',e.message))});
  v.webContents.on('page-title-updated',(event,title)=>{event.preventDefault();/* stable short tab labels */});
  v.webContents.on('did-fail-load',(_,code,description)=>{if(code!==-3){t.title='⚠ '+titleFor(t.mode);notify();console.warn('Modhunter tab error',description)}});
  v.webContents.on('destroyed',()=>{if(tabs.has(id))tabs.delete(id)});
  navigateTab(t,u);activate(id);return id;
}
function closeTab(id){const t=tabs.get(Number(id));if(!t)return false;const wasActive=activeId===t.id;if(wasActive){mainWindow.contentView.removeChildView(t.view);activeId=null}tabs.delete(t.id);t.view.webContents.close();if(wasActive&&tabs.size)activate([...tabs.keys()][Math.max(0,tabs.size-1)]);if(!tabs.size)openTab('dashboard');notify();return true}
async function connect(v){
  const endpoint=validBridge(v);if(!endpoint)return {ok:false,error:'Paste the MO2 plugin address, for example http://127.0.0.1:51234/'};
  try{
    const rsp=await net.fetch(endpoint,{method:'GET',cache:'no-store'});
    if(!rsp.ok||!String(rsp.headers.get('content-type')||'').includes('text/html'))throw new Error('Not an active MO2 Arsenal service');
    const html=await rsp.text();if(!html.includes('__MO2_BRIDGE_BOOT__')||!html.includes('"active": true'))throw new Error('The address is not the MO2 Arsenal dashboard');
    mo2Url=endpoint;
    for(const t of tabs.values())navigateTab(t,makePage(t.mode));notify();return {ok:true,connected:mo2Url};
  }catch(e){return {ok:false,error:String(e?.message||e)}}
}
function disconnect(){mo2Url=null;for(const t of tabs.values())navigateTab(t,makePage(t.mode));notify();return {ok:true}}
function trustedSender(event){return bar&&event.sender.id===bar.webContents.id}
function action(event,{kind,value}={}){
  if(!trustedSender(event))return {ok:false,error:'Untrusted app surface'};
  if(typeof kind!=='string')return {ok:false,error:'Invalid command'};
  if(kind==='state')return {ok:true,state:state()};
  if(kind==='importMo2')return importProfile();
  if(kind==='new')return {ok:!!openTab(['dashboard','realm','siege'].includes(value)?value:'dashboard')};
  if(kind==='activate')return {ok:activate(value)};
  if(kind==='close')return {ok:closeTab(value)};
  if(kind==='connect')return connect(value);
  if(kind==='disconnect')return disconnect();
  const t=tabs.get(activeId);if(!t)return {ok:false,error:'No tab selected'};
  if(kind==='reload'){t.view.webContents.reload();return {ok:true}}
  if(kind==='back'){if(t.view.webContents.canGoBack())t.view.webContents.goBack();return {ok:true}}
  if(kind==='forward'){if(t.view.webContents.canGoForward())t.view.webContents.goForward();return {ok:true}}
  if(kind==='external'){const u=loc(t.url);if(u&&['http:','https:'].includes(u.protocol))shell.openExternal(u.href).catch(()=>{});else shell.showItemInFolder(SAFE_LOCAL_FILE);return {ok:true}}
  if(kind==='copy'){clipboard.writeText(t.url);return {ok:true}}
  return {ok:false,error:'Unsupported command'};
}
app.whenReady().then(()=>{
  session.defaultSession.setPermissionRequestHandler((_,__,reply)=>reply(false));
  session.defaultSession.setPermissionCheckHandler(()=>false);
  if(!fs.existsSync(SAFE_LOCAL_FILE))throw Error('The bundled standalone HTML is missing: '+SAFE_LOCAL_FILE);
  loadOfflineCatalog();
  mainWindow=new BrowserWindow({width:1480,height:940,minWidth:640,minHeight:490,backgroundColor:'#071016',autoHideMenuBar:true,show:false,webPreferences:{contextIsolation:true,nodeIntegration:false,sandbox:true,preload:path.join(__dirname,'preload.cjs')}});
  mainWindow.once('ready-to-show',()=>{if(mainWindow&&!mainWindow.isDestroyed())mainWindow.show()});
  mainWindow.on('resize',()=>{if(bar)sizeView(bar,true);if(tabs.has(activeId))sizeView(tabs.get(activeId).view,false)});
  mainWindow.on('closed',()=>{for(const t of tabs.values())if(!t.view.webContents.isDestroyed())t.view.webContents.close();tabs.clear();mainWindow=null});
  bar=new WebContentsView({webPreferences:{preload:path.join(__dirname,'preload.cjs'),contextIsolation:true,nodeIntegration:false,sandbox:true,webSecurity:true}});
  bar.webContents.loadFile(CHROME);
  mainWindow.contentView.addChildView(bar);sizeView(bar,true);
  bar.webContents.on('did-finish-load',()=>{notify();if(mainWindow&&!mainWindow.isVisible())mainWindow.show()});
  ipcMain.handle('modhunter:action',action);
  const arg=process.argv.find(x=>x.startsWith('--mo2-url='));
  openTab('dashboard');
  if(arg)void connect(arg.slice(10));
});
app.on('window-all-closed',()=>app.quit());
