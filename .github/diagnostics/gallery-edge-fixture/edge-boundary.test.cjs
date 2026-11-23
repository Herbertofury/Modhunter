'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {launchOptions,checkCommandLine,checkBrowser}=require('./edge-boundary.cjs');
const {stripTrial,stripMemory}=require('./accept.cjs');
const cmd=['C:\\edge-build\\msedge.exe','--enable-automation','--remote-debugging-pipe','--user-data-dir=C:\\disposable\\ephemeral'];
test('uses only the installed Edge channel with Chromium sandbox required and protective defaults restored',()=>{
 const options=launchOptions();
 assert.equal(options.channel,'msedge');
 assert.equal(options.chromiumSandbox,true);
 assert.equal(Object.hasOwn(options,'executablePath'),false);
 assert.ok(options.ignoreDefaultArgs.includes('--disable-ipc-flooding-protection'));
 assert.ok(options.ignoreDefaultArgs.some(s=>s.startsWith('--disable-features=')&&s.includes('AutoDeElevate')));
 assert.ok(!options.args);
});
test('read-only actual-command acceptance rejects missing pipe, wrong exe and sandbox/security overrides',()=>{
 const value=JSON.stringify(checkCommandLine(cmd));
 assert.match(value,/forbiddenLaunchSwitchesAbsent/);
 assert.doesNotMatch(value,/disposable|\\\\|ephemeral|edge-build/);
 for(const suffix of ['--no-sandbox','--disable-web-security','--remote-debugging-port=0','--disable-features=AutoDeElevate','--ignore-certificate-errors'])
  assert.throws(()=>checkCommandLine([...cmd,suffix]));
 assert.throws(()=>checkCommandLine(cmd.filter(x=>x!=='--remote-debugging-pipe')));
 assert.throws(()=>checkCommandLine(['chromium.exe',...cmd.slice(1)]));
});
test('metadata probe detaches on success and failure without exporting launch arguments',async()=>{
 let count=0;
 const browser={version:()=> '155.0.67.1',newBrowserCDPSession:async()=>({
  send:async name=>{assert.equal(name,'Browser.getBrowserCommandLine');return {arguments:cmd};},
  detach:async()=>count++,
 })};
 assert.equal((await checkBrowser(browser)).version,'155.0.67.1');
 browser.version=()=>'/private/path';
 await assert.rejects(()=>checkBrowser(browser));
 assert.equal(count,2);
});
test('bounded report omits browser logs/memory paths/screenshot coordinates and refuses inverted elapsed measures',()=>{
 const mock={first:{frameBoundaryAt:11,requestedAt:10,retained:11263,hits:[{x:17,y:5}]},
  full:{cleanupAt:12,completeLayoutAt:13,geometryDigest:'fixed',contentDigest:'fixed'},
  spans:{frameOpportunityMs:1,observedCompleteAfterReleaseMs:1,manualWitnessDelayMs:1,auditMs:4},
  firstPixels:{digest:'fixed'},completePixels:{digest:'fixed'}};
 const record=stripTrial(mock);
 assert.equal(record.first.frameOpportunityMs,1);
 assert.equal(record.completion.fromHintReleaseToFullLayoutMs,1);
 assert.ok(!JSON.stringify(record).includes('hits'));
 assert.throws(()=>stripTrial({...mock,full:{cleanupAt:9,completeLayoutAt:10}}));
 assert.deepEqual(stripMemory({hinted:{heap:{status:'unavailable',detail:'C:/secret'}}}),{hinted:{heap:{status:'unavailable'}}});
});
