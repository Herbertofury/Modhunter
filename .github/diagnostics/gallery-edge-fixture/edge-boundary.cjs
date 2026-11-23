'use strict';
const assert=require('node:assert/strict');
const path=require('node:path');
const sandboxRestorations=Object.freeze([
 '--disable-client-side-phishing-detection','--disable-ipc-flooding-protection','--disable-popup-blocking',
 '--unsafely-disable-devtools-self-xss-warnings','--password-store=basic','--use-mock-keychain',
 '--disable-component-update','--edge-skip-compat-layer-relaunch',
 // Exact Playwright v1.57.0 stock list. Restore the engine's normal default
 // for all named features (e.g. HttpsUpgrades, ThirdPartyStoragePartitioning).
 '--disable-features=AcceptCHFrame,AvoidUnnecessaryBeforeUnloadCheckSync,DestroyProfileOnBrowserClose,DialMediaRouteProvider,GlobalMediaControls,HttpsUpgrades,LensOverlay,MediaRouter,PaintHolding,ThirdPartyStoragePartitioning,Translate,AutoDeElevate,RenderDocument,OptimizationHints',
]);
const forbidden=new Set([...sandboxRestorations.map(x=>x.split('=')[0]),
 '--no-sandbox','--disable-setuid-sandbox','--disable-gpu-sandbox','--disable-namespace-sandbox',
 '--disable-web-security','--ignore-certificate-errors','--ignore-certificate-errors-spki-list',
 '--allow-insecure-localhost','--allow-running-insecure-content','--remote-debugging-port']);
function launchOptions(){
 return {channel:'msedge',headless:true,chromiumSandbox:true,ignoreDefaultArgs:[...sandboxRestorations],timeout:30000};
}
function checkCommandLine(raw){
 assert.ok(Array.isArray(raw)&&raw.length>1&&raw.every(s=>typeof s==='string'));
 assert.equal(path.win32.basename(raw[0]).toLowerCase(),'msedge.exe');
 assert.ok(raw.includes('--remote-debugging-pipe')&&!raw.some(s=>forbidden.has(s.split('=')[0])));
 assert.equal(raw.filter(s=>s.startsWith('--user-data-dir=')).length,1);
 // Nothing about the actual executable, pipe or profile path leaves this function.
 return {brand:'Microsoft Edge',transport:'process-owned pipe',sandboxOptionRequired:true,forbiddenLaunchSwitchesAbsent:true};
}
async function checkBrowser(browser){
 const session=await browser.newBrowserCDPSession();
 try{
  const {arguments:args}=await session.send('Browser.getBrowserCommandLine');
  const boundary=checkCommandLine(args);
  const version=browser.version();assert.match(version,/^\d{1,3}\.\d{1,6}\.\d{1,6}\.\d{1,6}$/);
  return {...boundary,version};
 }finally{await session.detach();}
}
module.exports={launchOptions,checkCommandLine,checkBrowser};
