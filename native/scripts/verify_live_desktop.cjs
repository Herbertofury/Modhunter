/* Real Windows desktop + real MO2 fixture. Never shipped in the portable app. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const net=require('node:net'),{spawn,spawnSync}=require('node:child_process');
const {chromium}=require('playwright');
const sourceFiles=['native/scripts/verify_live_desktop.cjs','native/ui/shell.js',
 'native/ui/shell.html','native/ui/native.css','native/crates/modhunter-desktop/src/windows.rs',
 'native/crates/modhunter-desktop/src/policy.rs','mo2_update_arsenal/plugin.py',
 'mo2_update_arsenal/bridge.py','mo2_update_arsenal/download_ids.py',
 'mo2_update_arsenal/web/app.js','mo2_update_arsenal/web/app.template.html'];
const root=path.resolve(__dirname,'../..');
const hash=f=>crypto.createHash('sha256').update(fs.readFileSync(f)).digest('hex');
const options=Object.fromEntries(process.argv.slice(2).map(s=>{const at=s.indexOf('=');return[s.slice(0,at),s.slice(at+1)];}));
const exe=options['--exe'],output=options['--output'],endpoint=options['--endpoint'];
const checks=[],timings={};let phase='preflight',child,browser;
function check(name,value){checks.push({name,passed:!!value});if(!value)throw Error('Failed: '+name);}
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
 child=spawn(exe,[],{cwd:path.dirname(exe),windowsHide:false,stdio:'ignore',env:{...process.env,
  WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:`--remote-debugging-port=${port} --remote-debugging-address=127.0.0.1`}});
 child.on('error',()=>{});
 phase='attach-real-webview2';
 await until(async()=>{try{browser=await chromium.connectOverCDP(`http://127.0.0.1:${port}`,{timeout:1500});return true;}catch{return false;}},30000);
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
 const changes=spawnSync('git',['diff','--quiet','HEAD','--',...sourceFiles],{cwd:root,windowsHide:true});
 const report={schemaVersion:1,ok:true,nativeDesktop:true,nativeMO2:true,nativeWebView2:true,
  candidateCommit:process.env.GITHUB_SHA,sourceDirty:changes.status!==0,executableSha256:hash(exe),
  sourceFiles:Object.fromEntries(sourceFiles.map(name=>[name,hash(path.join(root,name))])),
  checks,timingsMs:timings,fixture:{profile:'Modhunter Smoke',accountAuthentication:false,ownedGameFiles:false},
  scope:'Actual compiled desktop connected to the official MO2 2.5.2 synthetic fixture. No personal beta-12, Nexus account, remote download or installation.'};
 fs.writeFileSync(output,JSON.stringify(report,null,2));
 console.log(JSON.stringify({ok:true,checks:checks.length,timingsMs:timings}));
}
main().catch(error=>{if(output)fs.writeFileSync(output,JSON.stringify({schemaVersion:1,ok:false,phase,error:String(error.message),checks},null,2));console.error('Live desktop validation failed at '+phase+': '+error.message);process.exitCode=1;}).finally(async()=>{
 if(browser)try{await browser.close();}catch{}
 if(child&&child.exitCode===null)child.kill();
});
