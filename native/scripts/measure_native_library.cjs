/* Actual shipped WebView2 renderer benchmark. Launched only by benchmark_native_library.ps1.
 * The exact Linux acceptance snapshot enters the shipped offline import function.
 * This measures renderer import/interactions, not Rust filesystem import or MO2 I/O.
 */
'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {spawnSync}=require('node:child_process');
const {performance}=require('node:perf_hooks');
const ROOT=path.resolve(__dirname,'../..'),COUNT=11207;
const FIXTURE_SHA='afd855e4ac76f284630ffb4ed6d9dc37280113c9559aef4402516795b7fef0a0';
const args=Object.fromEntries(process.argv.slice(2).map(value=>{const at=value.indexOf('=');return[value.slice(0,at),value.slice(at+1)];}));
const sha=file=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
function writeFixture(file){
 const code="import sys,json; from pathlib import Path; sys.path.insert(0,str(Path(sys.argv[1])/'tests')); from test_frontend_responsiveness import catalog; value=catalog(count=11207); value['offline']=True; Path(sys.argv[2]).write_bytes(json.dumps(value,sort_keys=True).encode())";
 const result=spawnSync(process.env.PYTHON||'python',['-c',code,ROOT,file],{encoding:'utf8',timeout:30000,windowsHide:true});
 if(result.error||result.status!==0)throw Error('Shared fixture generation failed: '+(result.error?.message||result.stderr));
 if(sha(file)!==FIXTURE_SHA)throw Error('The shared 11,207-record fixture changed; compare both acceptance paths before updating its identity');
 console.log(JSON.stringify({fixtureSha256:FIXTURE_SHA,bytes:fs.statSync(file).size,records:COUNT}));
}
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function focusOwnedWindow(pid,executable){
 const expected=executable.replaceAll("'","''");
 const script=`$ErrorActionPreference='Stop'
$owned=Get-Process -Id ${pid}
if($owned.Path -ine '${expected}'){throw 'Owned process executable differs from the candidate'}
Add-Type @'
using System; using System.Runtime.InteropServices;
public static class ModhunterLibraryFocus {
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr window,out uint id);
}
'@
$activation=New-Object -ComObject WScript.Shell
try {
 $deadline=(Get-Date).AddSeconds(3);$focused=$false
 do {
  $null=$activation.AppActivate(${pid});Start-Sleep -Milliseconds 100
  [uint32]$owner=0
  $null=[ModhunterLibraryFocus]::GetWindowThreadProcessId([ModhunterLibraryFocus]::GetForegroundWindow(),[ref]$owner)
  $focused=$owner -eq ${pid}
 } while(!$focused -and (Get-Date) -lt $deadline)
 if(!$focused){throw 'Native benchmark must own the actual foreground window'}
}finally{[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($activation)}`;
 const result=spawnSync('powershell.exe',['-NoProfile','-NonInteractive','-EncodedCommand',Buffer.from(script,'utf16le').toString('base64')],{windowsHide:true,encoding:'utf8',timeout:10000});
 if(result.error||result.status!==0)throw Error('Owned native foreground verification failed: '+(result.error?.message||result.stderr||result.stdout));
}
async function settle(page){return page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>{
 const list=document.getElementById('modList'),row=list?.querySelector(':scope > .mod-row'),style=row&&getComputedStyle(row);
 resolve({structuralBatchActive:!!list?.hasAttribute('data-layout-change'),transitionProperty:style?.transitionProperty,transitionDuration:style?.transitionDuration});
}))));}
async function completeGeometry(page){
 const value=await page.evaluate(count=>{
  const rows=[...document.querySelectorAll('#modList .mod-row')],ids=rows.map(row=>row.dataset.id);
  const valid=ids.length===count&&new Set(ids).size===count&&ids.every((id,index)=>id==='item-'+index);
  const first=rows[0]?.getBoundingClientRect(),last=rows.at(-1)?.getBoundingClientRect();
  return {rows:rows.length,completeOrderedIds:valid,lastId:ids.at(-1),
   allLaidOut:rows.every(row=>{const rect=row.getBoundingClientRect();return row.getClientRects().length>0&&rect.width>0&&rect.height>0;}),
   noContentVisibility:rows.every(row=>getComputedStyle(row).contentVisibility==='visible'),
   viewport:{width:innerWidth,height:innerHeight,devicePixelRatio},
   view:document.body.dataset.libraryView,density:document.body.dataset.density,
   firstRect:first?.toJSON(),lastRect:last?.toJSON(),documentNodes:document.querySelectorAll('*').length};
 },COUNT);
 if(!value.completeOrderedIds||!value.allLaidOut||!value.noContentVisibility)throw Error('Complete native library geometry failed: '+JSON.stringify(value));
 return value;
}
async function main(){
 if(args['--write-fixture']){writeFixture(path.resolve(args['--write-fixture']));return;}
 if(process.platform!=='win32'||process.env.GITHUB_ACTIONS!=='true'||process.env.RUNNER_OS!=='Windows'||process.env.MODHUNTER_NATIVE_SMOKE!=='1')throw Error('Requires the isolated disposable Windows CI launcher');
 const output=path.resolve(args['--output']),fixturePath=path.resolve(args['--fixture']);
 const executable=path.resolve(args['--exe']),pid=Number(args['--pid']);
 const endpoint=new URL(args['--endpoint']);
 if(endpoint.protocol!=='http:'||endpoint.hostname!=='127.0.0.1'||endpoint.pathname!=='/'||!Number.isInteger(pid)||pid<=0)throw Error('Invalid owned native process or CDP endpoint');
 if(sha(fixturePath)!==FIXTURE_SHA)throw Error('Native and Linux fixtures differ');
 const fixture=JSON.parse(fs.readFileSync(fixturePath,'utf8'));
 if(fixture.offline!==true||fixture.items.length!==COUNT)throw Error('Incomplete offline fixture');
 const report={schemaVersion:1,ok:false,nativeWebView2:true,nativeProcessId:pid,
  executableSha256:sha(executable),fixtureSha256:FIXTURE_SHA,fixtureBytes:fs.statSync(fixturePath).size,records:COUNT,
  scriptSha256:sha(__filename),phase:'attach-native-webview2',checks:[],timingsMs:{},interactions:{},errors:[],
  method:'One raw native sample. Import: performance.now → shipped modhunterImportCatalog → two animation frames. Search: actual input fill → exact last record. View/density: real click → two frames, with CDP metrics outside timed interval. Complete all-row geometry is checked outside timing. No viewport emulation, CSS replacement, result limit or virtualization.',
  scope:'Actual compiled Windows WebView2 renderer with the same synthetic 11,207-record offline snapshot as Linux acceptance. Excludes Rust filesystem import, live MO2, accounts, provider I/O and installation.'};
 const checkpoint=phase=>{if(phase)report.phase=phase;fs.writeFileSync(output,JSON.stringify(report,null,2)+'\n');console.log('NATIVE_LIBRARY '+report.phase);};
 const check=(name,passed)=>{report.checks.push({name,passed:!!passed});if(!passed)throw Error('Failed: '+name);};
 let browser,session,page;
 const timeout=setTimeout(()=>{report.errors.push('Native renderer worker exceeded its 450-second hard deadline');checkpoint('hard-deadline');process.exit(124);},450000);
 checkpoint();
 try{
  const {chromium}=require('playwright');
  const until=Date.now()+30000;
  while(Date.now()<until&&!browser){try{browser=await chromium.connectOverCDP(endpoint.href,{timeout:1500});}catch{await pause(150);}}
  if(!browser)throw Error('Owned WebView2 did not expose the bounded loopback endpoint');
  const views=()=>browser.contexts().flatMap(context=>context.pages());
  const pageDeadline=Date.now()+20000;
  while(Date.now()<pageDeadline&&!page){page=views().find(view=>{try{const url=new URL(view.url());return url.hostname==='tauri.localhost'&&url.protocol==='http:'&&url.pathname==='/Modhunter.html';}catch{return false;}});if(!page)await pause(100);}
  if(!page)throw Error('The actual packaged offline dashboard was not found');
  page.setDefaultTimeout(60000);
  page.on('pageerror',error=>report.errors.push(String(error)));
  await page.waitForFunction(()=>typeof window.modhunterImportCatalog==='function'&&!!window.__ARSENAL_QA__);
  check('packaged_offline_dashboard',await page.evaluate(()=>window.__ARSENAL_QA__.live===false&&typeof window.modhunterRequestTheme==='function'));
  const browserSession=await browser.newBrowserCDPSession();
  try{
   report.engine=await browserSession.send('Browser.getVersion');
   const command=await browserSession.send('Browser.getBrowserCommandLine');
   const profileArgument=command.arguments.find(value=>value.startsWith('--user-data-dir='));
   const actual=profileArgument?.slice('--user-data-dir='.length),expected=path.resolve(args['--browser-profile']);
   check('owned_webview_profile',!!actual&&(path.resolve(actual).toLowerCase()===expected.toLowerCase()||path.resolve(actual).toLowerCase().startsWith(expected.toLowerCase()+path.sep)));
   report.engine.executable=command.arguments[0];
  }finally{await browserSession.detach();}
  focusOwnedWindow(pid,executable);
  check('owned_native_foreground',true);
  report.initial=await page.evaluate(()=>({url:location.href,width:innerWidth,height:innerHeight,devicePixelRatio,
   view:document.body.dataset.libraryView,density:document.body.dataset.density,theme:document.body.dataset.theme,
   nativeTheme:window.ModhunterAppearance?.resolvedTheme,userAgent:navigator.userAgent,
   prefersReducedMotion:matchMedia('(prefers-reduced-motion: reduce)').matches,
   visibilityState:document.visibilityState,documentHasFocus:document.hasFocus()}));
  check('visible_native_document',report.initial.visibilityState==='visible');
  check('fresh_list_compact',report.initial.view==='list'&&report.initial.density==='compact'&&report.initial.prefersReducedMotion===false);
  session=await page.context().newCDPSession(page);await session.send('Performance.enable');
  const metricNames=['ScriptDuration','LayoutDuration','RecalcStyleDuration','TaskDuration','LayoutCount','RecalcStyleCount','Nodes','JSHeapUsedSize'];
  const metrics=async()=>Object.fromEntries((await session.send('Performance.getMetrics')).metrics.filter(value=>metricNames.includes(value.name)).map(value=>[value.name,value.value]));
  const geometry=selector=>page.evaluate(selector=>({scrollX,scrollY,viewport:{width:innerWidth,height:innerHeight},control:document.querySelector(selector)?.getBoundingClientRect().toJSON()}),selector);
  async function click(selector,label,expectedView,expectedDensity){
   checkpoint(label+'-starting');
   const before=await metrics(),geometryBefore=await geometry(selector),start=performance.now();
   await page.locator(selector).click();const frameEndpoint=await settle(page);
   const elapsed=performance.now()-start,after=await metrics();
   report.timingsMs[label]=elapsed;
   report.interactions[label]={wallMs:elapsed,metricsBefore:before,metricsAfter:after,
    metricDeltas:Object.fromEntries(metricNames.filter(key=>key in before&&key in after).map(key=>[key,after[key]-before[key]])),
    frameEndpoint,geometryBefore,geometryAfter:await geometry(selector)};
   if(frameEndpoint.structuralBatchActive||frameEndpoint.transitionProperty!=='background'||frameEndpoint.transitionDuration!=='0.12s')throw Error('Normal hover transitions were not restored inside the measured two-frame interval: '+label+' '+JSON.stringify(frameEndpoint));
   const complete=await completeGeometry(page);
   check(label+'_complete',complete.view===expectedView&&complete.density===expectedDensity);
   report.interactions[label].completeLibrary=complete;checkpoint(label+'-complete');
  }
  checkpoint('import-starting');
  report.timingsMs.render=await page.evaluate(async fixture=>{const start=performance.now();window.modhunterImportCatalog(fixture);await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));return performance.now()-start;},fixture);
  report.importGeometry=await completeGeometry(page);
  check('import_complete',report.importGeometry.rows===COUNT);
  await page.evaluate(()=>{window.__NATIVE_LIBRARY_SURVIVOR__=document.querySelector('#modList .mod-row:last-child');});
  checkpoint('import-complete');
  const searchStart=performance.now();
  await page.locator('#search').fill('Unique Fixture 11206');
  await page.waitForFunction(()=>{const rows=document.querySelectorAll('#modList .mod-row');return rows.length===1&&rows[0].dataset.id==='item-11206';});
  report.timingsMs.lastItemSearch=performance.now()-searchStart;
  check('exact_last_item_search',await page.evaluate(()=>document.querySelector('#modList .mod-row')===window.__NATIVE_LIBRARY_SURVIVOR__));
  checkpoint('search-complete');
  const restoreStart=performance.now();await page.locator('#search').fill('');
  await page.waitForFunction(count=>document.querySelectorAll('#modList .mod-row').length===count,COUNT);await settle(page);
  report.timingsMs.searchRestore=performance.now()-restoreStart;
  report.restoreGeometry=await completeGeometry(page);
  check('search_restore_survivor',await page.evaluate(()=>document.getElementById('row-item-11206')===window.__NATIVE_LIBRARY_SURVIVOR__));
  checkpoint('search-restored');
  await click('#densityToggle','listDensity','list','comfy');
  await click('#densityToggle','listDensityReset','list','compact');
  await click('#libraryGallery','gallery','gallery','compact');
  await click('#densityToggle','galleryDensity','gallery','comfy');
  await click('#libraryList','list','list','comfy');
  await click('#densityToggle','finalDensityReset','list','compact');
  check('no_renderer_errors',report.errors.length===0);
  check('unchanged_executable',sha(executable)===report.executableSha256);
  report.ok=true;checkpoint('complete');
 }catch(error){report.errors.push(String(error.stack||error));checkpoint();throw error;}
 finally{clearTimeout(timeout);if(session)try{await session.detach();}catch{}if(browser)try{await browser.close();}catch{}}
}
main().catch(error=>{console.error(error.stack||error);process.exitCode=1;});
