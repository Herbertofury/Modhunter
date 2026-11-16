/* Real Windows desktop + real MO2 fixture. Never shipped in the portable app. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const net=require('node:net'),{spawn,spawnSync}=require('node:child_process');
const {chromium}=require('playwright');
const sourceFiles=['native/scripts/verify_live_desktop.cjs','native/ui/shell.js',
 'native/ui/shell.html','native/ui/native.css','native/crates/modhunter-desktop/src/windows.rs',
 'native/crates/modhunter-desktop/src/policy.rs','mo2_update_arsenal/plugin.py',
 'mo2_update_arsenal/bridge.py','mo2_update_arsenal/download_ids.py',
 'mo2_update_arsenal/scanner.py','mo2_update_arsenal/gameplay.py','mo2_update_arsenal/native_nexus.py','mo2_update_arsenal/web/atelier.css',
 'mo2_update_arsenal/web/app.js','mo2_update_arsenal/web/app.template.html'];
const root=path.resolve(__dirname,'../..');
const hash=f=>crypto.createHash('sha256').update(fs.readFileSync(f)).digest('hex');
const options=Object.fromEntries(process.argv.slice(2).map(s=>{const at=s.indexOf('=');return[s.slice(0,at),s.slice(at+1)];}));
const exe=options['--exe'],output=options['--output'],endpoint=options['--endpoint'];
const checks=[],timings={},policyValues=[];let phase='preflight',child,browser,launchError='',attachError='',childOutput='',ownedDesktopWindow=null,originalDisplay=null;
// Runtime 150+ correctly ignores user-controlled debugging overrides in an
// elevated process. Set only this disposable runner's exact application policy,
// never a wildcard, HKCU override, runtime downgrade, or shipped app change.
// Microsoft: WebView2Feedback#5645 and CreateCoreWebView2EnvironmentWithOptions.
function setCiPolicy(name,value){
 if(process.platform!=='win32'||process.env.GITHUB_ACTIONS!=='true'||process.env.RUNNER_OS!=='Windows'||process.env.MODHUNTER_NATIVE_SMOKE!=='1')throw Error('Machine policy is restricted to the disposable Windows CI fixture');
 const app=path.basename(exe);if(app!=='Modhunter.exe'||!['AdditionalBrowserArguments','UserDataFolder'].includes(name))throw Error('Invalid application-scoped test policy');
 const key='HKLM\\SOFTWARE\\Policies\\Microsoft\\Edge\\WebView2\\'+name;
 const query=spawnSync('reg.exe',['query',key,'/v',app,'/reg:64'],{windowsHide:true,encoding:'utf8',timeout:10000});
 if(query.error||query.status!==1)throw Error('Refusing to replace or assume ownership of existing WebView2 policy: '+name);
 const added=spawnSync('reg.exe',['add',key,'/v',app,'/t','REG_SZ','/d',value,'/f','/reg:64'],{windowsHide:true,encoding:'utf8',timeout:10000});
 if(added.error||added.status!==0)throw Error('Could not set isolated app policy: '+name);
 policyValues.push({key,app,value});
 const verified=spawnSync('reg.exe',['query',key,'/v',app,'/reg:64'],{windowsHide:true,encoding:'utf8',timeout:10000});
 if(verified.error||verified.status!==0||!verified.stdout.includes(value))throw Error('Could not verify isolated app policy: '+name);
}
function clearCiPolicies(){
 for(const {key,app,value} of policyValues.reverse()){
  const observed=spawnSync('reg.exe',['query',key,'/v',app,'/reg:64'],{windowsHide:true,encoding:'utf8',timeout:10000});
  if(observed.error||observed.status!==0||!observed.stdout.includes(value))throw Error('Test policy changed unexpectedly; refusing to delete another value');
  const removed=spawnSync('reg.exe',['delete',key,'/v',app,'/f','/reg:64'],{windowsHide:true,encoding:'utf8',timeout:10000});
  if(removed.error||removed.status!==0)throw Error('Could not remove temporary app policy');
 }
}
function check(name,value){checks.push({name,passed:!!value});if(!value)throw Error('Failed: '+name);}
function configureCiDisplay(width,height){
 if(process.platform!=='win32'||process.env.GITHUB_ACTIONS!=='true'||process.env.RUNNER_OS!=='Windows'||process.env.MODHUNTER_NATIVE_SMOKE!=='1')throw Error('Display changes require the disposable Windows CI fixture');
 const change=Number.isInteger(width)&&Number.isInteger(height)&&width>0&&height>0;
 const script=`$ErrorActionPreference='Stop'
Add-Type @'
using System; using System.Runtime.InteropServices;
public static class ModhunterCiDisplay {
 [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)] public struct Mode {
  [MarshalAs(UnmanagedType.ByValTStr,SizeConst=32)] public string DeviceName;
  public short SpecVersion,DriverVersion,Size,DriverExtra; public int Fields;
  public int PositionX,PositionY,Orientation,FixedOutput;
  public short Color,Duplex,YResolution,TTOption,Collate;
  [MarshalAs(UnmanagedType.ByValTStr,SizeConst=32)] public string FormName;
  public short LogPixels; public int BitsPerPel,Width,Height,DisplayFlags,Frequency;
  public int ICMMethod,ICMIntent,MediaType,DitherType,Reserved1,Reserved2,PanningWidth,PanningHeight;
 }
 [DllImport("user32.dll",EntryPoint="EnumDisplaySettingsW",CharSet=CharSet.Unicode,ExactSpelling=true)] private static extern bool EnumDisplaySettings(string name,int mode,ref Mode value);
 // PowerShell converts $null to an empty .NET string. Keep the API's required
 // null device name inside C# so Windows selects the actual current display.
 public static bool CurrentMode(ref Mode value) {
  value.Size=(short)Marshal.SizeOf(typeof(Mode)); value.DriverExtra=0;
  return EnumDisplaySettings(null,-1,ref value);
 }
 [DllImport("user32.dll",EntryPoint="ChangeDisplaySettingsW",CharSet=CharSet.Unicode,ExactSpelling=true)] public static extern int ChangeDisplaySettings(ref Mode mode,int flags);
 [DllImport("user32.dll")] public static extern int GetSystemMetrics(int index);
 [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
}
'@
$null=[ModhunterCiDisplay]::SetProcessDPIAware()
$mode=[ModhunterCiDisplay+Mode]::new()
if(![ModhunterCiDisplay]::CurrentMode([ref]$mode)){throw "Current CI display mode unavailable (DEVMODE size $($mode.Size), screen $([ModhunterCiDisplay]::GetSystemMetrics(0)) x $([ModhunterCiDisplay]::GetSystemMetrics(1)))"}
$previous=@{width=$mode.Width;height=$mode.Height}
${change?`$mode.Width=${width};$mode.Height=${height};$mode.Fields=0x180000
if([ModhunterCiDisplay]::ChangeDisplaySettings([ref]$mode,2) -ne 0){throw 'Requested real CI display mode is unsupported'}
if([ModhunterCiDisplay]::ChangeDisplaySettings([ref]$mode,0) -ne 0){throw 'Real CI display mode change failed'}
$deadline=(Get-Date).AddSeconds(3)
while(([ModhunterCiDisplay]::GetSystemMetrics(0) -ne ${width} -or [ModhunterCiDisplay]::GetSystemMetrics(1) -ne ${height}) -and (Get-Date) -lt $deadline){Start-Sleep -Milliseconds 100}
if([ModhunterCiDisplay]::GetSystemMetrics(0) -ne ${width} -or [ModhunterCiDisplay]::GetSystemMetrics(1) -ne ${height}){throw 'Actual primary screen did not adopt the requested dimensions'}`:''}
$previous | ConvertTo-Json -Compress`;
 const result=spawnSync('powershell.exe',['-NoProfile','-NonInteractive','-EncodedCommand',Buffer.from(script,'utf16le').toString('base64')],{windowsHide:true,encoding:'utf8',timeout:15000});
 if(result.error||result.status!==0)throw Error('Real CI display configuration failed: '+(result.error?.message||result.stderr||result.stdout));
 return JSON.parse(result.stdout.trim());
}
// Resize and capture only the actual child owned by this disposable fixture.
// This exercises native WebView bounds rather than emulating a browser viewport.
function nativeWindow(width,height,capture){
 const target=capture?`'${path.resolve(capture).replaceAll("'","''")}'`:'$null';
 const script=`$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms,System.Drawing
Add-Type @'
using System; using System.Runtime.InteropServices;
public static class ModhunterOwnedWindowProbe {
 [StructLayout(LayoutKind.Sequential)] public struct Rect { public int Left,Top,Right,Bottom; }
 [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h,IntPtr after,int x,int y,int w,int hgt,uint flags);
 [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h,int command);
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h,out Rect rect);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h,System.Text.StringBuilder text,int count);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h,out uint id);
 [DllImport("user32.dll")] public static extern int GetSystemMetrics(int index);
 [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
}
'@
$null=[ModhunterOwnedWindowProbe]::SetProcessDPIAware()
$owned=Get-Process -Id ${child.pid};$handle=${ownedDesktopWindow?`[IntPtr]::new(${ownedDesktopWindow})`:'$owned.MainWindowHandle'}
[uint32]$owner=0
$null=[ModhunterOwnedWindowProbe]::GetWindowThreadProcessId($handle,[ref]$owner)
if($handle -eq [IntPtr]::Zero -or $owner -ne ${child.pid}){throw 'Owned application window is unavailable'}
${width?`$null=[ModhunterOwnedWindowProbe]::ShowWindow($handle,9)
if(![ModhunterOwnedWindowProbe]::SetWindowPos($handle,[IntPtr]::Zero,40,40,${width},${height},0x44)){throw 'Native window resize failed'}`:''}
$capture=${target}
if($capture){
 $activation=New-Object -ComObject WScript.Shell
 try{
  $deadline=(Get-Date).AddSeconds(3);$focused=$false
  $title=[Text.StringBuilder]::new(512);$null=[ModhunterOwnedWindowProbe]::GetWindowText($handle,$title,512)
  do{$null=$activation.AppActivate($title.ToString());$null=[ModhunterOwnedWindowProbe]::SetForegroundWindow($handle);Start-Sleep -Milliseconds 100
   $focused=[ModhunterOwnedWindowProbe]::GetForegroundWindow() -eq $handle
  }while(!$focused -and (Get-Date) -lt $deadline)
  if(!$focused){throw 'Owned application could not own the screenshot'}
  $rect=[ModhunterOwnedWindowProbe+Rect]::new()
  if(![ModhunterOwnedWindowProbe]::GetWindowRect($handle,[ref]$rect)){throw 'Window bounds unavailable'}
  $left=[ModhunterOwnedWindowProbe]::GetSystemMetrics(76);$top=[ModhunterOwnedWindowProbe]::GetSystemMetrics(77)
  $right=$left+[ModhunterOwnedWindowProbe]::GetSystemMetrics(78);$bottom=$top+[ModhunterOwnedWindowProbe]::GetSystemMetrics(79)
  if($rect.Left -lt $left -or $rect.Top -lt $top -or $rect.Right -gt $right -or $rect.Bottom -gt $bottom){throw 'Actual application window is outside the real desktop; refusing a clipped capture'}
  $bitmap=[Drawing.Bitmap]::new($rect.Right-$rect.Left,$rect.Bottom-$rect.Top);$graphics=[Drawing.Graphics]::FromImage($bitmap)
  try{$graphics.CopyFromScreen([Drawing.Point]::new($rect.Left,$rect.Top),[Drawing.Point]::Empty,$bitmap.Size);$bitmap.Save($capture,[Drawing.Imaging.ImageFormat]::Png)}
  finally{$graphics.Dispose();$bitmap.Dispose()}
 }finally{[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($activation)}
}
Write-Output $handle.ToInt64()`;
 const result=spawnSync('powershell.exe',['-NoProfile','-NonInteractive','-EncodedCommand',Buffer.from(script,'utf16le').toString('base64')],{windowsHide:true,encoding:'utf8',timeout:15000});
 if(result.error||result.status!==0)throw Error('Owned native window operation failed: '+(result.error?.message||result.stderr||result.stdout));
 const observed=result.stdout.trim();if(!/^[0-9]+$/.test(observed))throw Error('Owned desktop handle was not observed');
 if(ownedDesktopWindow&&observed!==ownedDesktopWindow)throw Error('Owned desktop window changed during acceptance');
 ownedDesktopWindow=observed;
}
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(fn,ms=20000){const end=Date.now()+ms;while(Date.now()<end){const value=await fn();if(value)return value;await pause(100);}throw Error('Deadline at '+phase);}
async function main(){
 if(process.platform!=='win32'||process.env.MODHUNTER_NATIVE_SMOKE!=='1')throw Error('Requires isolated Windows MO2 CI fixture');
 const fixture=process.env.MODHUNTER_NATIVE_SMOKE_INSTANCE;
 if(!fixture||!fs.existsSync(path.join(fixture,'modhunter-smoke-fixture.json')))throw Error('Missing isolated fixture marker');
 const base=new URL(endpoint);if(base.protocol!=='http:'||base.hostname!=='127.0.0.1'||base.pathname!=='/')throw Error('Invalid fixture endpoint');
 if(!exe||!output||fs.readFileSync(exe).subarray(0,2).toString()!=='MZ')throw Error('Expected compiled Windows executable');
 const reservation=net.createServer();await new Promise(resolve=>reservation.listen(0,'127.0.0.1',resolve));
 const port=reservation.address().port;await new Promise(resolve=>reservation.close(resolve));
 const started=Date.now();
 // A previous smoke's still-exiting browser must not own this test's user
 // data directory or suppress its new loopback debugging arguments. This
 // override is scoped to this disposable child, never the shipped launcher.
 const browserProfile=fs.mkdtempSync(path.join(path.dirname(output),'desktop-profile-'));
 phase='configure-real-ci-display';
 originalDisplay=configureCiDisplay(null,null);
 configureCiDisplay(1920,1080);
 phase='configure-isolated-webview2';
 setCiPolicy('UserDataFolder',browserProfile);
 setCiPolicy('AdditionalBrowserArguments',`--remote-debugging-port=${port} --remote-debugging-address=127.0.0.1`);
 child=spawn(exe,[],{cwd:path.dirname(exe),windowsHide:false,stdio:['ignore','pipe','pipe'],env:{...process.env,
  WEBVIEW2_USER_DATA_FOLDER:browserProfile,
  WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:`--remote-debugging-port=${port} --remote-debugging-address=127.0.0.1`}});
 child.on('error',error=>{launchError=error.message;});
 for(const stream of [child.stdout,child.stderr])stream.on('data',data=>{childOutput=(childOutput+data.toString()).slice(-8000);});
 phase='attach-real-webview2';
 await until(async()=>{
  if(launchError)throw Error('Desktop could not start: '+launchError);
  if(child.exitCode!==null)throw Error('Desktop exited before attachment: '+child.exitCode);
  try{browser=await chromium.connectOverCDP(`http://127.0.0.1:${port}`,{timeout:1500});return true;}
  catch(error){attachError=error.message.slice(0,2000);return false;}
 },30000);
 const pages=()=>browser.contexts().flatMap(c=>c.pages());
 const shell=await until(()=>pages().find(p=>p.url().endsWith('/shell.html')));
 shell.setDefaultTimeout(15000);
 phase='automatic-discovery';
 await shell.waitForFunction(()=>document.getElementById('sourceText')?.textContent.includes('LIVE MO2'));
 check('automatic_discovery',true);timings.automaticDiscoveryMs=Date.now()-started;
 let dashboard=await until(()=>pages().find(p=>{try{const u=new URL(p.url());return u.origin===base.origin&&u.pathname==='/'&&!u.search;}catch{return false;}}));
 dashboard.setDefaultTimeout(15000);
 phase='live-profile';
 await dashboard.waitForFunction(()=>window.__ARSENAL_QA__?.getCatalog()?.profileName==='Modhunter Smoke'&&document.getElementById('livePill')?.classList.contains('connected'));
 const facts=await dashboard.evaluate(()=>({names:window.__ARSENAL_QA__.getCatalog().items.map(m=>m.name),welcomeHidden:document.getElementById('nativeWelcome').hidden}));
 check('live_catalog_not_demo',facts.welcomeHidden&&facts.names.includes('Smoke Enabled')&&facts.names.includes('Smoke Disabled')&&facts.names.length<10);
 phase='compact-workspace';
 const compact=await dashboard.evaluate(()=>({workspace:document.body.dataset.workspace,
  rows:document.querySelectorAll('.mod-row').length,
  visible:document.getElementById('workbench').getBoundingClientRect().height>0,
  legacy:document.getElementById('unifiedCommand').getBoundingClientRect().height,
  header:document.querySelector('.workspace-header').getBoundingClientRect().height}));
 check('compact_workspace',compact.workspace==='mods'&&compact.rows>=2&&compact.visible&&compact.legacy===0&&compact.header<110);
 phase='automatic-source-check';
 const scan=await until(async()=>dashboard.evaluate(async()=>{
   const reply=await fetch('/api/scan',{headers:{'X-Arsenal-Token':window.__MO2_BRIDGE_BOOT__.token}});
   const value=await reply.json();return value.ok&&value.scan?.mode==='startup'&&value.scan.state!=='running'?value.scan:null;
 }),30000);
 check('automatic_source_check',scan.mode==='startup'&&['completed','partial'].includes(scan.state));
 phase='toolbar-refresh';let tick=Date.now();
 const refreshing=dashboard.waitForResponse(r=>r.url()===endpoint+'api/refresh'&&r.request().method()==='POST');
 await shell.locator('#refreshProfile').click();
 check('toolbar_refresh',(await(await refreshing).json()).ok===true);timings.refreshMs=Date.now()-tick;
 phase='toolbar-audit';tick=Date.now();
 await dashboard.waitForFunction(()=>!['queued','running'].includes(window.__ARSENAL_QA__.getCatalog().pluginAuditState?.state));
 const auditing=dashboard.waitForResponse(r=>r.url()===endpoint+'api/plugin-audit'&&r.request().method()==='POST');
 await shell.locator('#pluginAudit').click();
 const audit=await(await auditing).json();check('toolbar_plugin_audit',audit.ok===true);timings.auditDispatchMs=Date.now()-tick;
 phase='connection-details';
 await shell.locator('#diagnostics').click();
 await dashboard.locator('#connectionDetails[open]').waitFor();
 await dashboard.waitForFunction(()=>document.getElementById('connectionDetailsText').textContent.includes('automaticStartup'));
 const diagnostics=JSON.parse(await dashboard.locator('#connectionDetailsText').textContent());
 check('connection_details',diagnostics.ok===true&&diagnostics.native?.automaticStartup===true&&diagnostics.native?.callbackWarnings?.length===0&&diagnostics.rustHelperAvailable===true);
 await dashboard.locator('#connectionDetailsClose').click();
 phase='live-content-ipc-boundary';
 const denied=await dashboard.evaluate(async()=>{try{await window.__TAURI_INTERNALS__.invoke('shell_command',{kind:'state',value:null});return false;}catch{return true;}});
 check('content_native_ipc_denied',denied);
 phase='manual-rediscovery';
 await shell.locator('#discover').click();
 await shell.waitForFunction(()=>document.getElementById('sourceText')?.textContent.includes('LIVE MO2')&&!document.getElementById('discover').disabled);
 check('manual_rediscovery',true);
 phase='disconnected-welcome';
 await shell.locator('#disconnect').click();
 const local=await until(()=>pages().find(p=>p.url().includes('/Modhunter.html')));
 await local.locator('#nativeWelcome').waitFor({state:'visible'});
 check('disconnected_welcome',await local.locator('.shell').evaluate(e=>e.inert));
 await local.screenshot({path:path.join(path.dirname(output),'desktop-welcome.png')});
 phase='setup-reconnect';
 await local.locator('a[href="modhunter-shell://discover"]').click();
 await shell.waitForFunction(()=>document.getElementById('sourceText')?.textContent.includes('LIVE MO2'));
 dashboard=await until(()=>pages().find(p=>{try{return new URL(p.url()).origin===base.origin;}catch{return false;}}));
 await dashboard.waitForFunction(()=>window.__ARSENAL_QA__?.getCatalog()?.profileName==='Modhunter Smoke'&&document.getElementById('nativeWelcome').hidden);
 check('setup_reconnect',true);
 phase='capture';
 await dashboard.screenshot({path:path.join(path.dirname(output),'desktop-live.png')});
 phase='native-theme-and-window-acceptance';
 nativeWindow(1440,960);
 await shell.locator('#openWatchtower').click();
 const watchtower=await until(()=>pages().find(p=>p.url().endsWith('/watchtower.html')));
 watchtower.setDefaultTimeout(15000);
 await watchtower.locator('#navigation [data-view="settings"]').click();
 await watchtower.locator('#globalTheme').waitFor();
 await watchtower.waitForFunction(()=>document.getElementById('globalReleaseStatus')?.textContent.includes('Installed'));
 const originalTheme=await watchtower.locator('#globalTheme').inputValue();
 const activateDashboard=async()=>{
  const tabs=shell.locator('.tab-open');
  for(let i=0;i<await tabs.count();i++)if(await tabs.nth(i).getAttribute('title')===dashboard.url()){await tabs.nth(i).click();return;}
  throw Error('Live profile tab disappeared during native acceptance');
 };
 const setNativeTheme=async theme=>{
  await shell.locator('#openWatchtower').click();
  await watchtower.locator('#navigation [data-view="settings"]').click();
  await watchtower.locator('#globalTheme').selectOption(theme);
  await watchtower.locator('#globalPreferencesSave').click();
  await watchtower.waitForFunction(()=>document.getElementById('globalPreferencesStatus').textContent==='App preferences saved.');
  await activateDashboard();
  await dashboard.waitForFunction(theme=>document.body.dataset.theme===(theme==='light'?'snow':'night'),theme);
  await shell.waitForFunction(theme=>document.documentElement.dataset.theme===theme,theme);
 };
 const nativeCaptures=[];
 for(const [width,height,theme] of [[1440,960,'dark'],[900,850,'light']]){
  nativeWindow(width,height);
  await setNativeTheme(theme);
  await dashboard.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const layout=await dashboard.evaluate(()=>({width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,rows:document.querySelectorAll('#modList .mod-row').length,theme:document.body.dataset.theme}));
  check('native_layout_'+width+'_'+theme,layout.width<=width&&layout.width>=width-60&&layout.scrollWidth<=layout.width+1&&layout.rows===facts.names.length);
  const capture='desktop-window-'+width+'-'+theme+'.png';nativeWindow(null,null,path.join(path.dirname(output),capture));
  nativeCaptures.push({file:capture,layout,window:{width,height}});
 }
 // The shared dashboard asks its native owner for a theme change; it cannot
 // invoke desktop commands or optimistically report an unsaved preference.
 // WebView2 deliberately intercepts modhunter-appearance:// without navigating
 // the document. Wait for both canonical acknowledgements below, not for the
 // browser navigation that the native on_navigation handler intentionally denies.
 phase='native-theme-shortcut-dark';
 await dashboard.locator('#railTheme').click({noWaitAfter:true});
 await dashboard.waitForFunction(()=>document.body.dataset.theme==='night');
 await shell.waitForFunction(()=>document.documentElement.dataset.theme==='dark');
 phase='native-theme-shortcut-light';
 await dashboard.locator('#railTheme').click({noWaitAfter:true});
 await dashboard.waitForFunction(()=>document.body.dataset.theme==='snow');
 await shell.waitForFunction(()=>document.documentElement.dataset.theme==='light');
 check('native_dashboard_theme_control_acknowledged',true);
 phase='native-gallery';
 await dashboard.locator('#libraryGallery').click();
 await dashboard.waitForFunction(()=>document.body.dataset.libraryView==='gallery');
 check('native_gallery_retains_live_catalog',await dashboard.locator('#modList .mod-row').count()===facts.names.length);
 nativeWindow(null,null,path.join(path.dirname(output),'desktop-window-gallery-light.png'));
 phase='native-inspector';
 await dashboard.locator('#modList .inspect-btn').first().click();
 await dashboard.locator('#detailDrawer.open').waitFor();
 nativeWindow(null,null,path.join(path.dirname(output),'desktop-window-inspector-light.png'));
 await dashboard.keyboard.press('Escape');
 check('native_inspector_returns_focus',await dashboard.locator('#modList .inspect-btn').first().evaluate(e=>document.activeElement===e));
 await dashboard.locator('#libraryList').click();
 // Restore an explicit baseline theme; system preferences are preserved too.
 await shell.locator('#openWatchtower').click();
 await watchtower.locator('#globalTheme').selectOption(originalTheme);
 await watchtower.locator('#globalPreferencesSave').click();
 await watchtower.waitForFunction(()=>document.getElementById('globalPreferencesStatus').textContent==='App preferences saved.');
 await activateDashboard();
 nativeWindow(1440,960);
 const changes=spawnSync('git',['diff','--quiet','HEAD','--',...sourceFiles],{cwd:root,windowsHide:true});
 const report={schemaVersion:1,ok:true,nativeDesktop:true,nativeMO2:true,nativeWebView2:true,
  candidateCommit:process.env.GITHUB_SHA,sourceDirty:changes.status!==0,executableSha256:hash(exe),
  sourceFiles:Object.fromEntries(sourceFiles.map(name=>[name,hash(path.join(root,name))])),
  checks,timingsMs:timings,nativeCaptures,fixture:{profile:'Modhunter Smoke',accountAuthentication:false,ownedGameFiles:false},
  scope:'Actual compiled desktop connected to the official MO2 2.5.2 synthetic fixture. No personal beta-12, Nexus account, remote download or installation.'};
 fs.writeFileSync(output,JSON.stringify(report,null,2));
 console.log(JSON.stringify({ok:true,checks:checks.length,timingsMs:timings}));
}
main().catch(async error=>{
 const failureViews=[];
 if(browser)for(const page of browser.contexts().flatMap(c=>c.pages())){
  try{
   const url=new URL(page.url());
   const state=await page.evaluate(()=>({title:document.title,source:document.getElementById('sourceText')?.textContent,message:document.getElementById('message')?.textContent,sessionChoices:document.querySelectorAll('#mo2Sessions option').length,rootTheme:document.documentElement.dataset.theme,dashboardTheme:document.body?.dataset.theme}));
   failureViews.push({path:url.pathname,...state});
   if(url.pathname.endsWith('/shell.html'))await page.screenshot({path:path.join(path.dirname(output),'desktop-failure.png')});
  }catch{}
 }
 if(output)fs.writeFileSync(output,JSON.stringify({schemaVersion:1,ok:false,phase,error:String(error.message),checks,childExitCode:child?.exitCode??null,launchError,attachError,childOutput,failureViews},null,2));
 console.error('Live desktop validation failed at '+phase+': '+error.message);process.exitCode=1;
}).finally(async()=>{
 if(browser)try{await browser.close();}catch{}
 if(child&&child.exitCode===null)child.kill();
 try{clearCiPolicies();}catch(error){
  process.exitCode=1;console.error('Temporary CI policy cleanup failed: '+error.message);
  if(output){const report=JSON.parse(fs.readFileSync(output,'utf8'));report.ok=false;report.cleanupError=error.message;fs.writeFileSync(output,JSON.stringify(report,null,2));}
 }
 if(originalDisplay)try{configureCiDisplay(originalDisplay.width,originalDisplay.height);}catch(error){
  process.exitCode=1;console.error('Temporary CI display restoration failed: '+error.message);
  if(output){const report=JSON.parse(fs.readFileSync(output,'utf8'));report.ok=false;report.displayRestoreError=error.message;fs.writeFileSync(output,JSON.stringify(report,null,2));}
 }
});
