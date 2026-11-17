/* Actual packaged Windows WebView2 manager + isolated filesystem fixtures.
 * The inert PE markers are never executed. This is not an MO2 runtime claim.
 */
'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),os=require('node:os');
const {spawnSync}=require('node:child_process');
const args=Object.fromEntries(process.argv.slice(2).map(value=>{const at=value.indexOf('=');return at<0?[value,true]:[value.slice(0,at),value.slice(at+1)];}));
const hash=file=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const digest=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const names={fresh:'A Fresh',upgrade:'B Upgrade 日本語 [selected]',unselected:'C Leave Unchanged'};
function write(file,text){fs.mkdirSync(path.dirname(file),{recursive:true});fs.writeFileSync(file,text);}
function marker(){const bytes=Buffer.alloc(512);bytes.write('MZ');bytes.writeUInt32LE(128,60);bytes.write('PE\0\0',128,'binary');bytes.writeUInt16LE(0x8664,132);return bytes;}
function tree(root){
 const result={};
 function walk(directory,prefix){
  for(const name of fs.readdirSync(directory).sort()){
   const file=path.join(directory,name),relative=prefix+name,info=fs.lstatSync(file);
   if(info.isSymbolicLink())throw Error('Fixture contains an unexpected linked path');
   if(info.isDirectory()){result[relative]='<directory>';walk(file,relative+'/');}
   else if(info.isFile())result[relative]=hash(file);else throw Error('Fixture contains a non-regular file');
  }
 }
 walk(root,'');return result;
}
const equal=(left,right)=>JSON.stringify(left)===JSON.stringify(right);
function fixture(root){
 if(fs.existsSync(root))throw Error('Fixture destination must be new');
 fs.mkdirSync(root,{recursive:true});
 for(const [role,name] of Object.entries(names)){
  const app=path.join(root,name);
  write(path.join(app,'ModOrganizer.exe'),marker());
  write(path.join(app,'ModOrganizer.ini'),'[General]\ngameName=Skyrim Special Edition\nselected_profile=Daily\n[Settings]\nbase_directory=.\n');
  for(const profile of ['Daily','Testing'])write(path.join(app,'profiles',profile,'modlist.txt'),'# Inert instance discovery fixture\n+Fixture Mod\n');
  fs.mkdirSync(path.join(app,'mods'),{recursive:true});
  if(role!=='fresh'){
   const plugin=path.join(app,'plugins/mo2_update_arsenal');
   write(path.join(plugin,'version.py'),`VERSION = "${role==='upgrade'?'0.12.1':'99.0.0'}"\n`);
   write(path.join(plugin,'__init__.py'),'# Inert previous plugin fixture; never import.\n');
   write(path.join(plugin,'previous-plugin.txt'),'Preserve the complete previous plugin in its backup.\n');
   write(path.join(plugin,'data/chronicle.json'),'{"fixture":"preserved-progress","score":2468}\n');
   write(path.join(plugin,'data/history/operations.json'),'[{"fixture":"prior-operation"}]\n');
   fs.mkdirSync(path.join(plugin,'data/empty-directory'),{recursive:true});
  }
  if(role==='unselected')write(path.join(app,'portable.txt'),'');
 }
 const global=path.join(root,'global-instances/Shared Global');
 write(path.join(global,'ModOrganizer.ini'),'[General]\ngameName=Fallout 4\nselected_profile=Shared Daily\n[Settings]\nbase_directory=.\n');
 write(path.join(global,'profiles/Shared Daily/modlist.txt'),'# Shared global instance fixture\n+Global Fixture Mod\n');
 fs.mkdirSync(path.join(global,'mods'),{recursive:true});
 const facts={schemaVersion:1,inertExecutable:true,executedMO2:false,applications:3,instances:4,profiles:7,
  portableAssociations:3,sharedGlobalAssociations:2,peMarkerSha256:digest(marker()),names};
 write(path.join(root,'fixture-marker.json'),JSON.stringify(facts,null,2)+'\n');return facts;
}
function key(value){let text=String(value);if(text.toUpperCase().startsWith('\\\\?\\UNC\\'))text='\\\\'+text.slice(8);else if(text.startsWith('\\\\?\\'))text=text.slice(4);return text.replaceAll('\\','/').replace(/\/+$/,'').toLowerCase();}
function bundle(root){
 const manifest=fs.readFileSync(path.join(root,'SHA256SUMS.txt'));
 const files={},seen=new Set();
 for(const line of manifest.toString('utf8').replace(/^\uFEFF/,'').trimEnd().split(/\r?\n/)){
  const match=/^([a-f0-9]{64})  (.+)$/i.exec(line);if(!match)throw Error('Invalid runtime bundle manifest');
  const name=match[2];if(name.includes('\\')||name.startsWith('/')||name.split('/').some(part=>!part||part==='.'||part==='..'||part.includes(':'))||seen.has(name.toLowerCase()))throw Error('Unsafe runtime bundle manifest');
  seen.add(name.toLowerCase());files[name]=match[1].toLowerCase();
  if(hash(path.join(root,name))!==files[name])throw Error('Runtime bundle does not match its manifest: '+name);
 }
 const actual=Object.fromEntries(Object.entries(tree(root)).filter(([,value])=>value!=='<directory>'));
 const expected={...files,'SHA256SUMS.txt':digest(manifest)};
 if(!equal(Object.keys(actual).sort(),Object.keys(expected).sort()))throw Error('Runtime bundle contains unexpected files');
 const identity=JSON.parse(fs.readFileSync(path.join(root,'BUILD_INFO.json'),'utf8').replace(/^\uFEFF/,''));
 const prefix='MO2/mo2_update_arsenal/';
 return {identity,manifestSha256:digest(manifest),resourceHashes:actual,
  payload:Object.fromEntries(Object.entries(files).filter(([name])=>name.startsWith(prefix)).map(([name,sha])=>[name.slice(prefix.length),sha]))};
}
async function pointer(page,selector){
 await page.evaluate(selector=>{const item=document.querySelector(selector);if(!item)throw Error('Missing pointer target');item.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'});},selector);
 await page.waitForFunction(selector=>{
  const item=document.querySelector(selector);if(!item||item.disabled)return false;
  const r=item.getBoundingClientRect(),s=getComputedStyle(item),x=r.x+r.width/2,y=r.y+r.height/2;
  return r.width>0&&r.height>0&&s.visibility==='visible'&&s.display!=='none'&&x>=0&&y>=0&&x<innerWidth&&y<innerHeight&&item.contains(document.elementFromPoint(x,y));
 },selector);
 const point=await page.evaluate(async selector=>{
  const item=document.querySelector(selector),first=item.getBoundingClientRect();
  await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
  const r=item.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2;
  if(item.disabled||!item.isConnected||['x','y','width','height'].some(name=>Math.abs(first[name]-r[name])>.5)||!item.contains(document.elementFromPoint(x,y)))throw Error('Native pointer target is not stable');
  return {x,y};
 },selector);
 await page.mouse.click(point.x,point.y);
}
function nativeWindowScript(pid,executable,manager,capture,close=false){
 const target=capture?`'${path.resolve(capture).replaceAll("'","''")}'`:'$null';
 return `$ErrorActionPreference='Stop'
$owned=Get-Process -Id ${pid}
if($owned.Path -ine '${executable.replaceAll("'","''")}'){throw 'Owned process executable differs from the candidate'}
Add-Type -AssemblyName System.Windows.Forms,System.Drawing
Add-Type @'
using System; using System.Collections.Generic; using System.Runtime.InteropServices; using System.Text;
public static class ModhunterInstancesWindow {
 public delegate bool Visit(IntPtr h,IntPtr l);
 [StructLayout(LayoutKind.Sequential)] public struct Rect { public int Left,Top,Right,Bottom; }
 [DllImport("user32.dll")] static extern bool EnumWindows(Visit visitor,IntPtr value);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h,out uint id);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr h,StringBuilder title,int length);
 [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
 [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h,IntPtr after,int x,int y,int w,int height,uint flags);
 [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h,int command);
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h,out Rect rect);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h,uint message,IntPtr w,IntPtr l);
 [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
 public static IntPtr Find(uint pid,bool manager) {
  var found=new List<IntPtr>();
  EnumWindows(delegate(IntPtr h,IntPtr l) { uint owner;GetWindowThreadProcessId(h,out owner);
   var text=new StringBuilder(512);GetWindowText(h,text,512);var title=text.ToString();
   if(owner==pid&&IsWindowVisible(h)&&title.Contains("Modhunter")&&(manager?title.Contains("MO2 Instances"):title.Contains("Rust")))found.Add(h);return true;
  },IntPtr.Zero);
  if(found.Count!=1)throw new InvalidOperationException("Expected exactly one owned native window");return found[0];
 }
}
'@
$null=[ModhunterInstancesWindow]::SetProcessDPIAware()
$handle=[ModhunterInstancesWindow]::Find(${pid},$${manager?'true':'false'})
${close?`if(![ModhunterInstancesWindow]::PostMessage($handle,0x10,[IntPtr]::Zero,[IntPtr]::Zero)){throw 'Could not close owned manager window'}
@{closed=$true;nativeProcessId=${pid}} | ConvertTo-Json -Compress`:`
$bounds=[Windows.Forms.Screen]::PrimaryScreen.WorkingArea
$width=[Math]::Min(1100,$bounds.Width-20);$height=[Math]::Min(820,$bounds.Height-20)
if($width -lt 800 -or $height -lt 620){throw 'CI display is too small for complete native window evidence'}
$null=[ModhunterInstancesWindow]::ShowWindow($handle,9)
if(![ModhunterInstancesWindow]::SetWindowPos($handle,[IntPtr]::Zero,$bounds.Left+10,$bounds.Top+10,$width,$height,0x44)){throw 'Native window positioning failed'}
$activation=New-Object -ComObject WScript.Shell
try {
 $deadline=(Get-Date).AddSeconds(3);$focused=$false
 do {$null=$activation.AppActivate(${pid});$null=[ModhunterInstancesWindow]::SetForegroundWindow($handle);Start-Sleep -Milliseconds 100
  $focused=[ModhunterInstancesWindow]::GetForegroundWindow() -eq $handle
 }while(!$focused -and (Get-Date) -lt $deadline)
 if(!$focused){throw 'The actual manager window did not own the foreground'}
 $rect=[ModhunterInstancesWindow+Rect]::new()
 if(![ModhunterInstancesWindow]::GetWindowRect($handle,[ref]$rect)){throw 'Native window bounds are unavailable'}
 if($rect.Left -lt $bounds.Left -or $rect.Top -lt $bounds.Top -or $rect.Right -gt $bounds.Right -or $rect.Bottom -gt $bounds.Bottom){throw 'Refusing a clipped native window capture'}
 $capture=${target}
 if($capture){
  $image=[Drawing.Bitmap]::new($rect.Right-$rect.Left,$rect.Bottom-$rect.Top);$graphics=[Drawing.Graphics]::FromImage($image)
  try{$graphics.CopyFromScreen([Drawing.Point]::new($rect.Left,$rect.Top),[Drawing.Point]::Empty,$image.Size);$image.Save($capture,[Drawing.Imaging.ImageFormat]::Png)}finally{$graphics.Dispose();$image.Dispose()}
 }
 @{foreground=$true;nativeProcessId=${pid};width=$rect.Right-$rect.Left;height=$rect.Bottom-$rect.Top} | ConvertTo-Json -Compress
}finally{[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($activation)}`}`;
}
function nativeWindow(pid,executable,manager,capture,close=false){
 const script=nativeWindowScript(pid,executable,manager,capture,close);
 const result=spawnSync(path.join(process.env.WINDIR,'System32/WindowsPowerShell/v1.0/powershell.exe'),['-NoProfile','-NonInteractive','-EncodedCommand',Buffer.from(script,'utf16le').toString('base64')],{windowsHide:true,encoding:'utf8',timeout:15000});
 if(result.error||result.status!==0)throw Error('Owned native window operation failed: '+(result.error?.message||result.stderr||result.stdout));
 return JSON.parse(result.stdout.trim());
}
async function main(){
 if(args['--write-fixture']){console.log(JSON.stringify(fixture(path.resolve(args['--write-fixture']))));return;}
 if(args['--self-test']){
  const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'modhunter-manager-fixture-'));
  try{const root=path.join(temporary,'fixture');const facts=fixture(root);if(facts.applications!==3||facts.profiles!==7||tree(root)[names.upgrade+'/plugins/mo2_update_arsenal/data/empty-directory']!=='<directory>')throw Error('Incomplete manager fixture');if(key('\\\\?\\C:\\MO2')!==key('C:\\MO2')||key('\\\\?\\UNC\\host\\share')!==key('\\\\host\\share'))throw Error('Canonical path comparison differs');console.log(JSON.stringify({ok:true,fixture:facts,scope:'Fixture generation and comparison helpers only; no native UI'}));}finally{fs.rmSync(temporary,{recursive:true,force:true});}return;
 }
 if(process.platform!=='win32'||process.env.GITHUB_ACTIONS!=='true'||process.env.RUNNER_OS!=='Windows'||process.env.MODHUNTER_NATIVE_SMOKE!=='1')throw Error('Requires the isolated Windows CI launcher');
 const output=path.resolve(args['--output']),fixtureRoot=path.resolve(args['--fixture']),executable=path.resolve(args['--exe']),pid=Number(args['--pid']);
 const endpoint=new URL(args['--endpoint']);if(endpoint.protocol!=='http:'||endpoint.hostname!=='127.0.0.1'||endpoint.pathname!=='/'||!Number.isInteger(pid)||pid<=0)throw Error('Invalid owned native process or endpoint');
 const facts=JSON.parse(fs.readFileSync(path.join(fixtureRoot,'fixture-marker.json'),'utf8'));if(!equal(facts.names,names)||!facts.inertExecutable||facts.executedMO2)throw Error('Missing isolated fixture identity');
 const resource=path.join(path.dirname(executable),'mo2-installer'),packageInfo=bundle(resource);
 const applications=Object.fromEntries(Object.entries(names).map(([role,name])=>[role,path.join(fixtureRoot,name)]));
 const before=Object.fromEntries(Object.entries(applications).map(([role,root])=>[role,tree(root)]));
 const oldPlugin=tree(path.join(applications.upgrade,'plugins/mo2_update_arsenal'));
 const report={schemaVersion:1,ok:false,nativeDesktop:true,nativeWebView2:true,nativeMO2:false,isolatedFilesystemFixture:true,accountAuthentication:false,
  nativeProcessId:pid,executableSha256:hash(executable),scriptSha256:hash(__filename),phase:'attach-native-webview2',checks:[],errors:[],timingsMs:{},
  bundle:{version:packageInfo.identity.version,commit:packageInfo.identity.commit,manifestSha256:packageInfo.manifestSha256},
  fixture:{...facts,names:undefined},targets:[],captures:[],
  scope:'Actual packaged Windows WebView2 MO2 Instances UI and native IPC with inert PE markers and isolated filesystem targets. No MO2 process, game, credentials, Nexus request or remote download is executed.'};
 const checkpoint=phase=>{if(phase)report.phase=phase;fs.writeFileSync(output,JSON.stringify(report,null,2)+'\n');console.log('INSTANCES_MANAGER '+report.phase);};
 const check=(name,passed)=>{report.checks.push({name,passed:!!passed});if(!passed)throw Error('Failed: '+name);};
 async function until(fn,ms=30000){const end=Date.now()+ms;while(Date.now()<end){const result=await fn();if(result)return result;await pause(150);}throw Error('Deadline at '+report.phase);}
 let browser,manager,shell,content;
 const hardDeadline=setTimeout(()=>{report.errors.push('Native manager fixture exceeded its 450-second deadline');checkpoint('hard-deadline');process.exit(124);},450000);
 checkpoint();
 try{
  const {chromium}=require('playwright');const began=Date.now();
  await until(async()=>{try{browser=await chromium.connectOverCDP(endpoint.href,{timeout:1500});return true;}catch{return false;}},30000);
  const pages=()=>browser.contexts().flatMap(context=>context.pages());
  shell=await until(()=>pages().find(page=>page.url().endsWith('/shell.html')));
  content=await until(()=>pages().find(page=>page.url().endsWith('/Modhunter.html')));
  const browserSession=await browser.newBrowserCDPSession();
  try{
   report.engine=await browserSession.send('Browser.getVersion');
   const command=await browserSession.send('Browser.getBrowserCommandLine');
   const actual=command.arguments.find(value=>value.startsWith('--user-data-dir='))?.slice('--user-data-dir='.length),expected=path.resolve(args['--browser-profile']);
   check('owned_webview_profile',!!actual&&(key(path.resolve(actual))===key(expected)||key(path.resolve(actual)).startsWith(key(expected)+'/')));
   report.engine.executable=command.arguments[0];
  }finally{await browserSession.detach();}
  nativeWindow(pid,executable,false);shell.setDefaultTimeout(20000);
  checkpoint('open-real-manager');await pointer(shell,'#openInstances');
  manager=await until(()=>pages().find(page=>page.url().endsWith('/instances.html')));manager.setDefaultTimeout(30000);
  manager.on('pageerror',error=>report.errors.push(String(error)));
  check('real_manager_opens_from_shell',new URL(manager.url()).hostname==='tauri.localhost');
  nativeWindow(pid,executable,true);check('owned_native_foreground',true);
  const invoke=(kind,value={})=>manager.evaluate(({kind,value})=>window.__TAURI__.core.invoke('instances_command',{kind,value}),{kind,value});
  let state=await until(async()=>{const result=await invoke('state');return ['complete','failed','cancelled'].includes(result.state.scan.status)?result.state:null;});
  check('complete_application_instance_discovery',state.scan.status==='complete'&&state.inventory.complete&&state.inventory.installations.length===facts.applications&&state.inventory.instances.length===facts.instances&&state.inventory.instances.reduce((sum,item)=>sum+item.profiles.length,0)===facts.profiles);
  check('matching_bundle_identity',state.package.available&&state.package.valid&&state.package.version===packageInfo.identity.version&&state.package.commit===packageInfo.identity.commit);
  const byRole=Object.fromEntries(Object.entries(applications).map(([role,directory])=>[role,state.inventory.installations.find(row=>key(row.directory)===key(directory))]));
  if(Object.values(byRole).some(row=>!row))throw Error('Native inventory omitted an exact owned application');
  const globals=state.inventory.instances.filter(instance=>instance.kind==='global');
  const shared=state.inventory.associations.filter(row=>row.kind==='availableGlobal');
  check('shared_global_associations',globals.length===1&&shared.length===2&&shared.every(row=>row.instanceId===globals[0].id&&[byRole.fresh.id,byRole.upgrade.id].includes(row.installationId))&&state.inventory.associations.filter(row=>row.kind==='portable').length===3);
  await manager.waitForFunction(()=>document.getElementById('installationCount').textContent==='3'&&document.querySelectorAll('[data-installation-select]').length===3);
  report.timingsMs.discovery=Date.now()-began;
  const capture=name=>{const window=nativeWindow(pid,executable,true,path.join(path.dirname(output),name));report.captures.push({file:name,...window});};
  capture('instances-discovery.png');
  await manager.evaluate(()=>{window.__instancesEvidence=[];document.addEventListener('pointerdown',event=>{window.__instancesEvidence.push({trusted:event.isTrusted,id:event.target.id||'',selection:event.target.dataset?.installationSelect||''});},true);});
  checkpoint('select-review-install');
  for(const role of ['fresh','upgrade'])await pointer(manager,'input[data-installation-select='+JSON.stringify(byRole[role].id)+']');
  const selected=await manager.evaluate(()=>[...document.querySelectorAll('[data-installation-select]:checked')].map(item=>item.dataset.installationSelect).sort());
  check('real_pointer_target_selection',equal(selected,[byRole.fresh.id,byRole.upgrade.id].sort()));
  const previewStart=Date.now();await pointer(manager,'#reviewSelected');
  await manager.waitForFunction(()=>document.getElementById('installPreview').open&&!document.getElementById('confirmInstall').disabled&&document.querySelectorAll('.preview-target').length===2);
  const reviewed=await manager.evaluate(()=>[...document.querySelectorAll('.preview-target')].map(item=>({id:item.dataset.installationId,ready:item.dataset.ready})).sort((a,b)=>a.id.localeCompare(b.id)));
  check('reviewed_exact_selected_targets',reviewed.length===2&&reviewed.every(row=>row.ready==='true')&&equal(reviewed.map(row=>row.id).sort(),selected));
  check('preview_leaves_all_targets_unchanged',Object.entries(applications).every(([role,root])=>equal(tree(root),before[role])));
  report.timingsMs.preview=Date.now()-previewStart;capture('instances-preview.png');
  const installStart=Date.now();await pointer(manager,'#confirmInstall');
  state=await until(async()=>{const result=await invoke('state');return ['complete','failed','cancelled'].includes(result.state.install.status)?result.state:null;},120000);
  check('selected_installation_results_complete',state.install.status==='complete'&&state.install.completed===2&&state.install.total===2&&state.install.results.length===2&&state.install.results.every(result=>['installed','updated'].includes(result.status)));
  report.timingsMs.selectedInstall=Date.now()-installStart;
  for(const role of ['fresh','upgrade']){
   const result=state.install.results.find(row=>row.installationId===byRole[role].id),destination=path.join(applications[role],'plugins/mo2_update_arsenal');
   const installedFiles=Object.fromEntries(Object.keys(packageInfo.payload).sort().map(name=>[name,hash(path.join(destination,name))]));
   check(role+'_payload_matches_bundle',Object.entries(packageInfo.payload).every(([name,sha])=>installedFiles[name]===sha)&&result.version===packageInfo.identity.version&&result.commit===packageInfo.identity.commit&&result.status===(role==='fresh'?'installed':'updated'));
   const target={fixture:role,status:result.status,version:result.version,installedFiles,preservedData:!!result.preservedData,backupPreserved:false,emptyDirectoryPreserved:false};
   if(role==='upgrade'){
    const backup=result.backupDirectory;if(!backup||!key(backup).startsWith(key(path.join(applications.upgrade,'ModhunterBackups'))+'/'))throw Error('Upgrade returned a foreign backup path');
    target.backupPreserved=equal(tree(backup),oldPlugin);target.emptyDirectoryPreserved=fs.statSync(path.join(destination,'data/empty-directory')).isDirectory();
    check('previous_plugin_backup_unchanged',target.backupPreserved);
    check('progress_and_empty_directories_preserved',target.preservedData&&target.emptyDirectoryPreserved&&equal(tree(path.join(destination,'data')),tree(path.join(backup,'data'))));
   }
   report.targets.push(target);
  }
  await manager.waitForFunction(()=>document.querySelectorAll('#installResults .result-row').length===2&&[...document.querySelectorAll('#installResults .result-row')].every(item=>['installed','updated'].includes(item.dataset.status)));
  const inputs=await manager.evaluate(()=>window.__instancesEvidence);report.trustedPointerEvents=inputs.filter(item=>item.trusted).length;
  check('trusted_pointer_review_and_confirmation',inputs.some(item=>item.trusted&&item.id==='reviewSelected')&&inputs.some(item=>item.trusted&&item.id==='confirmInstall')&&selected.every(id=>inputs.some(item=>item.trusted&&item.selection===id)));
  check('unselected_application_unchanged',equal(tree(applications.unselected),before.unselected));
  const completed=state.install,journal=path.join(fixtureRoot,'manager-state/settings.json');
  const saved=await until(()=>{try{const value=JSON.parse(fs.readFileSync(journal,'utf8'));return value.install?.status==='complete'?value:null;}catch{return null;}});
  check('persisted_results_match_completed_targets',equal(saved.install,completed));capture('instances-installed.png');
  checkpoint('reopen-completed-results');nativeWindow(pid,executable,true,null,true);await until(()=>manager.isClosed());
  nativeWindow(pid,executable,false);await pointer(shell,'#openInstances');
  manager=await until(()=>pages().find(page=>page.url().endsWith('/instances.html')));manager.setDefaultTimeout(30000);manager.on('pageerror',error=>report.errors.push(String(error)));
  nativeWindow(pid,executable,true);await manager.waitForFunction(()=>document.querySelectorAll('#installResults .result-row').length===2);
  state=(await invoke('state')).state;
  check('reopened_manager_retains_results',equal(state.install,completed));capture('instances-reopened.png');
  checkpoint('authority-and-stale-review');
  const review=(await invoke('previewInstall',{installationIds:[byRole.fresh.id]})).preview;if(!review.canInstall)throw Error('Secondary reviewed fixture target is blocked');
  async function denied(page){return page.evaluate(async value=>{const call=window.__TAURI__?.core?.invoke||window.__TAURI_INTERNALS__?.invoke;if(typeof call!=='function')return {denied:true,apiAvailable:false};try{await call('instances_command',{kind:'install',value});return {denied:false,apiAvailable:true};}catch{return {denied:true,apiAvailable:true};}},{token:review.token});}
  report.foreignAuthority={shell:await denied(shell),content:await denied(content)};
  check('foreign_shell_install_denied',report.foreignAuthority.shell.denied);check('foreign_content_install_denied',report.foreignAuthority.content.denied);
  const changed=path.join(applications.fresh,'plugins/mo2_update_arsenal/data/new-progress.json');write(changed,'{"fixture":"progress changed after review","score":97531}\n');
  const afterChange=tree(applications.fresh);await invoke('install',{token:review.token});
  state=await until(async()=>{const value=(await invoke('state')).state;return ['failed','complete','cancelled'].includes(value.install.status)?value:null;},60000);
  check('changed_reviewed_target_refused',state.install.status==='failed'&&state.install.results.length===1&&state.install.results[0].code==='target_changed');
  check('failed_review_preserves_current_data',equal(tree(applications.fresh),afterChange)&&equal(tree(applications.unselected),before.unselected));
  let rejected=false;try{await invoke('install',{token:review.token});}catch{rejected=true;}
  check('consumed_review_cannot_replay',rejected&&equal(tree(applications.fresh),afterChange));
  check('source_resources_unchanged',equal(bundle(resource).resourceHashes,packageInfo.resourceHashes));check('executable_unchanged',hash(executable)===report.executableSha256);
  check('no_renderer_errors',report.errors.length===0);report.ok=true;checkpoint('complete');
 }catch(error){report.errors.push(String(error.message));checkpoint('failed-'+report.phase);throw error;}
 finally{clearTimeout(hardDeadline);if(browser)try{await browser.close();}catch{}}
}
module.exports={marker,fixture,tree,bundle,key,nativeWindowScript};
if(require.main===module)main().catch(error=>{console.error(error.stack||error);process.exitCode=1;});
