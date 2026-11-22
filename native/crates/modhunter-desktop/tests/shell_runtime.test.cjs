'use strict';

// Execute the shipped toolbar with a minimal DOM and controlled IPC replies.
// These tests cover command/state races, not Windows WebView2 rendering.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../../../ui/shell.js'), 'utf8');
const markup = fs.readFileSync(path.join(__dirname, '../../../ui/shell.html'), 'utf8');
const injection = fs.readFileSync(path.join(__dirname, '../../../ui/catalog_injection.js'), 'utf8');

function snapshot(overrides = {}) {
  return { epoch: 1, importOperationId: 0, iniViewRevision: 1, tabs: [{ id: 1, title: 'Mod Vault', url: 'http://tauri.localhost/Modhunter.html', loading: false }],
    activeId: 1, connected: false, endpoint: null, importing: false, auditing: false,
    offlineProfile: 'Fixture A', offlineModCount: 4, warnings: [],
    stats: { mods: 4, elapsedMs: 2, metadataCacheHits: 4 }, ...overrides };
}

async function harness(initial = snapshot()) {
  const nodes = new Map(), events = new Map(), calls = [], pending = new Map(), deferred = new Set(), failures = new Map();
  const document = { activeElement: null, addEventListener() {}, createElement: element,
    getElementById: id => nodes.get(id) };
  function element() {
    const classes = new Set();
    return { children: [], title: '', textContent: '', value: '', disabled: false, attributes: {},
      classList: { toggle(name, on) { on ? classes.add(name) : classes.delete(name); }, contains: name => classes.has(name) },
      append(...children) { this.children.push(...children); },
      replaceChildren(...children) { this.children = children; },
      get firstElementChild() { return this.children[0]; },
      get lastElementChild() { return this.children.at(-1); },
      setAttribute(name, value) { this.attributes[name] = value; },
      addEventListener() {}, remove() {}, focus() { document.activeElement = this; } };
  }
  for (const [, id] of markup.matchAll(/\bid="([^"]+)"/g)) nodes.set(id, element());
  let current = initial, timer = 0;
  const context = { document, console, setTimeout: () => ++timer, clearTimeout() {}, addEventListener() {},
    __TAURI__: { core: { invoke: async (name, args) => {
      assert.equal(name, 'shell_command'); calls.push(args);
      if (failures.has(args.kind)) { const message = failures.get(args.kind); failures.delete(args.kind); throw new Error(message); }
      if (deferred.has(args.kind)) return new Promise(resolve => pending.set(args.kind, resolve));
      return { ok: true, state: current };
    } }, event: { listen: async (name, callback) => { events.set(name, callback); return () => {}; } } } };
  context.window = context;
  vm.runInNewContext(source, context, { filename: 'shell.js' });
  const settle = async () => { for (let i = 0; i < 4; i++) await new Promise(resolve => setImmediate(resolve)); };
  await settle();
  return { nodes, calls, pending, deferred, failures, settle,
    emit(next) { current = next; events.get('modhunter-state')({ payload: next }); },
    resolve(kind, next, extra = {}) { pending.get(kind)({ ok: true, state: next, ...extra }); pending.delete(kind); deferred.delete(kind); } };
}

test('busy feedback follows the owned command and actual native audit until completion', async () => {
  const h = await harness(snapshot({ revision: 1 }));
  assert.equal(h.nodes.get('diagnostics').disabled, true);
  assert.equal(h.nodes.get('diagnostics').attributes['aria-busy'], 'false');
  h.deferred.add('pluginAudit');
  const audit = h.nodes.get('pluginAudit').onclick();
  assert.equal(h.nodes.get('pluginAudit').attributes['aria-busy'], 'true');
  h.resolve('pluginAudit', snapshot({ revision: 2, auditing: true }));
  await audit;
  assert.equal(h.nodes.get('pluginAudit').attributes['aria-busy'], 'true');
  h.emit(snapshot({ revision: 3, auditing: false }));
  assert.equal(h.nodes.get('pluginAudit').attributes['aria-busy'], 'false');
  assert.equal(h.nodes.get('pluginAudit').disabled, false);
});

test('caught command failure has error feedback and a later actual message clears that error', async () => {
  const h = await harness();
  h.failures.set('copy', 'Fixture copy failure');
  await h.nodes.get('copy').onclick();
  assert.equal(h.nodes.get('message').attributes['data-feedback'], 'error');
  assert.match(h.nodes.get('message').textContent, /Fixture copy failure/);
  h.deferred.add('copy');
  const copy = h.nodes.get('copy').onclick();
  h.resolve('copy', snapshot(), { message: 'Copied current address' });
  await copy;
  assert.equal(h.nodes.get('message').attributes['data-feedback'], 'info');
  assert.equal(h.nodes.get('message').textContent, 'Copied current address');
});

test('plugin audit stays asynchronous, rejects duplicate clicks and leaves tab commands usable', async () => {
  const h = await harness();
  h.deferred.add('pluginAudit');
  const audit = h.nodes.get('pluginAudit').onclick();
  const duplicate = h.nodes.get('pluginAudit').onclick();
  await h.settle();
  assert.equal(h.calls.filter(c => c.kind === 'pluginAudit').length, 1);
  assert.equal(h.nodes.get('pluginAudit').disabled, true);
  assert.match(h.nodes.get('sourceText').textContent, /Auditing plugins/);
  await h.nodes.get('newDash').onclick();
  assert.equal(h.calls.filter(c => c.kind === 'new').length, 1);
  h.resolve('pluginAudit', snapshot({ epoch: 2, pluginAnalysis: {
    depth: 'records', checkedAt: '2026-11-12T12:00:00Z', stats: { elapsedMs: 12.25, winningPlugins: 3 }
  } }));
  await Promise.all([audit, duplicate]);
  assert.equal(h.nodes.get('pluginAudit').disabled, false);
  assert.equal(h.nodes.get('pluginAudit').textContent, 'PLUGIN AUDIT');
  assert.match(h.nodes.get('metrics').textContent, /12\.3 ms · 3 winning plugins/);
});

test('an old audit reply cannot replace a newer profile or connection snapshot', async () => {
  const h = await harness();
  h.deferred.add('pluginAudit');
  const audit = h.nodes.get('pluginAudit').onclick();
  h.emit(snapshot({ epoch: 2, offlineProfile: 'Fixture B' }));
  h.emit(snapshot({ epoch: 3, offlineProfile: 'Fixture A refreshed', offlineModCount: 9 }));
  h.resolve('pluginAudit', snapshot({ epoch: 1, offlineProfile: 'Obsolete result' }));
  await audit;
  assert.equal(h.nodes.get('sourceText').textContent, 'Fixture A refreshed · 9 MODS');
});

test('ESLifier path selection and clearing retain no stale selected title', async () => {
  const dataPath = 'C:\\Tools\\ESLifier\\ESLifier_Data';
  const h = await harness(snapshot({ eslifierDataPath: dataPath }));
  assert.equal(h.nodes.get('configureEslifierData').title, dataPath);
  assert.equal(h.nodes.get('configureEslifierData').classList.contains('selected'), true);
  assert.equal(h.nodes.get('clearEslifierData').disabled, false);
  h.emit(snapshot({ epoch: 2, eslifierDataPath: null }));
  assert.equal(h.nodes.get('configureEslifierData').classList.contains('selected'), false);
  assert.equal(h.nodes.get('clearEslifierData').disabled, true);
  assert.match(h.nodes.get('configureEslifierData').title, /Choose ESLifier_Data/);
});

test('audit requires an offline profile or directs a live session to its dashboard', async () => {
  const h = await harness(snapshot({ offlineProfile: null, offlineModCount: 0 }));
  assert.equal(h.nodes.get('pluginAudit').disabled, true);
  h.emit(snapshot({ epoch: 2, endpoint: 'http://127.0.0.1:50000/', connected: true }));
  assert.equal(h.nodes.get('pluginAudit').disabled, false);
  assert.match(h.nodes.get('pluginAudit').title, /live MO2 dashboard/);
});

test('late snapshots from the same context cannot undo a newer tab or connection state', async () => {
  const h = await harness(snapshot({ revision: 1 }));
  h.deferred.add('new');
  const opening = h.nodes.get('newDash').onclick();
  h.emit(snapshot({ revision: 3, connected: true, endpoint: 'http://127.0.0.1:50000/' }));
  h.resolve('new', snapshot({ revision: 2 }), { message: 'Obsolete completion' });
  await opening;
  assert.equal(h.nodes.get('sourceText').textContent, 'LIVE MO2 · CONNECTED');
  assert.equal(h.nodes.get('endpoint').value, 'http://127.0.0.1:50000/');
  assert.equal(h.nodes.get('message').textContent, '');
});

test('Cancel targets the observed import operation and context and retains the completed profile', async () => {
  const h = await harness(snapshot({ revision: 1 }));
  assert.equal(h.nodes.get('cancelImport').hidden, true);
  h.emit(snapshot({ epoch: 9, revision: 2, importOperationId: 14, importing: true, auditing: true }));
  assert.equal(h.nodes.get('cancelImport').hidden, false);
  h.deferred.add('cancelImport');
  const cancel = h.nodes.get('cancelImport').onclick();
  const duplicate = h.nodes.get('cancelImport').onclick();
  assert.equal(h.calls.filter(c => c.kind === 'cancelImport').length, 1);
  const request = h.calls.find(c => c.kind === 'cancelImport').value;
  assert.equal(request.epoch, 9);
  assert.equal(request.importOperationId, 14);
  h.resolve('cancelImport', snapshot({ epoch: 9, revision: 3, importOperationId: 14, importing: true, auditing: true, cancelling: true }));
  await Promise.all([cancel, duplicate]);
  assert.equal(h.nodes.get('cancelImport').disabled, true);
  assert.equal(h.nodes.get('cancelImport').textContent, 'CANCELING…');
  assert.equal(h.nodes.get('sourceText').textContent, 'Canceling local work…');
  h.emit(snapshot({ epoch: 9, revision: 4, importOperationId: 14 }));
  assert.equal(h.nodes.get('cancelImport').hidden, true);
  assert.equal(h.nodes.get('refreshProfile').disabled, false);
  assert.equal(h.nodes.get('sourceText').textContent, 'Fixture A · 4 MODS');
});

test('a delayed Cancel keeps its original operation ID when another import starts in the same context', async () => {
  const h = await harness(snapshot({ epoch: 9, revision: 1, importOperationId: 41, importing: true }));
  h.deferred.add('cancelImport');
  const delayed = h.nodes.get('cancelImport').onclick();
  h.emit(snapshot({ epoch: 9, revision: 2, importOperationId: 41 }));
  h.emit(snapshot({ epoch: 9, revision: 3, importOperationId: 42, importing: true }));
  const requests = () => h.calls.filter(c => c.kind === 'cancelImport');
  assert.equal(requests()[0].value.epoch, 9);
  assert.equal(requests()[0].value.importOperationId, 41);
  h.resolve('cancelImport', snapshot({ epoch: 9, revision: 2, importOperationId: 41 }));
  await delayed;
  assert.equal(h.nodes.get('cancelImport').hidden, false);
  assert.equal(h.nodes.get('cancelImport').disabled, false);
  assert.equal(h.nodes.get('sourceText').textContent, 'Working in background…');
  await h.nodes.get('cancelImport').onclick();
  assert.equal(requests().length, 2);
  assert.equal(requests()[1].value.epoch, 9);
  assert.equal(requests()[1].value.importOperationId, 42);
});

function injectionHarness(href) {
  const imported = [];
  const context = { URL, location: { href }, modhunterImportCatalog: c => imported.push(c) };
  context.window = context;
  return { context, imported, run(revision, name = 'Fixture') {
    vm.runInNewContext(`${injection}(${JSON.stringify({ profileName: name })},${revision});`, context);
  } };
}

test('catalog injection checks the execution-time origin and excludes live pages and games', () => {
  for (const href of ['http://tauri.localhost/Modhunter.html', 'tauri://localhost/Modhunter.html']) {
    const h = injectionHarness(href); h.run(1); assert.equal(h.imported.length, 1, href);
  }
  for (const href of ['http://127.0.0.1:50000/', 'https://example.com/Modhunter.html',
    'http://tauri.localhost:9000/Modhunter.html', 'http://tauri.localhost/shell.html',
    'http://tauri.localhost/Modhunter.html?realm=1', 'http://tauri.localhost/Modhunter.html?siege=1',
    'http://user@tauri.localhost/Modhunter.html']) {
    const h = injectionHarness(href); h.run(1); assert.equal(h.imported.length, 0, href);
  }
});

test('catalog injections retain the newest revision and can retry a failed import', () => {
  const h = injectionHarness('http://tauri.localhost/Modhunter.html');
  h.run(5, 'New'); h.run(4, 'Old'); h.run(5, 'Duplicate');
  assert.deepEqual(h.imported.map(c => c.profileName), ['New']);
  const receive = h.context.modhunterImportCatalog;
  h.context.modhunterImportCatalog = () => { throw new Error('Rendering failed'); };
  assert.throws(() => h.run(6), /Rendering failed/);
  assert.equal(h.context.__modhunterNativeCatalogRevision, 5);
  h.context.modhunterImportCatalog = receive;
  h.run(6, 'Retry');
  assert.deepEqual(h.imported.map(c => c.profileName), ['New', 'Retry']);
});


test('Watchtower opening coalesces repeated clicks without blocking MO2 navigation', async () => {
  const h = await harness(); h.deferred.add('watchtower');
  const first = h.nodes.get('openWatchtower').onclick();
  const second = h.nodes.get('openWatchtower').onclick(); await h.settle();
  assert.equal(h.calls.filter(c => c.kind === 'watchtower').length, 1);
  assert.equal(h.nodes.get('openWatchtower').disabled, true);
  await h.nodes.get('newDash').onclick();
  assert.equal(h.calls.filter(c => c.kind === 'new').length, 1);
  h.resolve('watchtower', snapshot()); await Promise.all([first, second]);
  assert.equal(h.nodes.get('openWatchtower').disabled, false);
});


test('live Refresh and Details are enabled and send the real live commands', async () => {
 const h=await harness(snapshot({endpoint:'http://127.0.0.1:50000/',connected:true,offlineProfile:null}));
 for(const id of ['refreshProfile','pluginAudit','diagnostics']){
  assert.equal(h.nodes.get(id).disabled,false,id);
  await h.nodes.get(id).onclick();
  assert.equal(h.calls.filter(c=>c.kind===id).length,1);
 }
 h.emit(snapshot({epoch:2,endpoint:'http://127.0.0.1:50000/',connected:false,offlineProfile:null}));
 for(const id of ['refreshProfile','pluginAudit','diagnostics'])assert.equal(h.nodes.get(id).disabled,true,id);
});

test('Compare INI sends no path authority, avoids duplicate work, and remains independent', async () => {
  const h = await harness(snapshot({ epoch: 4, iniViewRevision: 7 }));
  h.deferred.add('compareIni');
  const compare = h.nodes.get('compareIni');
  assert.equal(compare.disabled, false);
  const pending = compare.onclick();
  assert.equal(compare.disabled, true);
  assert.equal(compare.attributes['aria-busy'], 'true');
  await compare.onclick();
  const requests = h.calls.filter(call => call.kind === 'compareIni');
  assert.equal(requests.length, 1);
  assert.equal(JSON.stringify(requests[0].value), '{"epoch":4,"tab":1,"viewRevision":7}');
  assert.equal(h.nodes.get('configureIni').disabled, false);
  h.resolve('compareIni', snapshot({ epoch: 4, iniViewRevision: 7 }), { canceled: true });
  await pending;
  assert.equal(compare.disabled, false);
  assert.match(h.nodes.get('message').textContent, /canceled.*No files/);
});

test('Compare INI drops completion after A/B/A or a profile generation change', async () => {
  const h = await harness(snapshot({ epoch: 4, revision: 1, iniViewRevision: 5 }));
  const run = async transitions => {
    h.deferred.add('compareIni');
    const pending = h.nodes.get('compareIni').onclick();
    for (const transition of transitions) h.emit(snapshot(transition));
    h.resolve('compareIni', snapshot({ epoch: 4, revision: 1, iniViewRevision: 5 }), { canceled: true });
    await pending;
    assert.equal(h.nodes.get('message').textContent, '');
  };
  await run([{ epoch: 4, revision: 2, iniViewRevision: 6, activeId: 2 }, { epoch: 4, revision: 3, iniViewRevision: 7 }]);
  await run([{ epoch: 5, revision: 4, iniViewRevision: 7 }]);
});

function watchtowerQueueFixture(command, {initiallyHidden=false, initialContext, contextDuringReplay}={}) {
  const queueScript = fs.readFileSync(path.join(__dirname, '../../../ui/watchtower-queue.js'), 'utf8');
  const nodes = new Map(), events = new Map(), calls = [];
  function node() {
    return { children: [], dataset: {}, hidden: false, disabled: false, ownText: '',
      get textContent() { return this.ownText + this.children.map(value => value.textContent).join(''); },
      set textContent(value) { this.ownText = value; this.children = []; },
      append(...children) { this.children.push(...children); },
      prepend(...children) { this.children.unshift(...children); },
      replaceChildren(...children) { this.ownText = ''; this.children = children; },
      removeAttribute() {} };
  }
  for (const [, id] of queueScript.matchAll(/\bid="([^"]+)"/g)) nodes.set(id, node());
  nodes.set('downloadsView', node());
  nodes.get('downloadsView').hidden=initiallyHidden;
  const settings = { hideTitles: false };
  const context = { document: {
    createElement: node,
    createTextNode(value) { const element = node(); element.textContent = value; return element; },
    getElementById: id => nodes.get(id)
  }, MutationObserver: class { constructor(callback) { events.set('visibility', callback); } observe() {} }, clearTimeout() {}, setTimeout() { return 1; },
  addEventListener(kind, callback) { events.set(kind, callback); },
  ModhunterWatchtower: { state: () => ({ settings }),
    installQueueContext() {
      if(contextDuringReplay)events.get('modhunter-install-queue-context')({detail:contextDuringReplay});
      return initialContext;
    },
    confirmAction: async () => true, showToast(message) { calls.push(message); },
    command(kind, value) { calls.push({ kind, value }); return command(kind, value); }
  } };
  context.window = context;
  vm.runInNewContext(queueScript, context, { filename: 'watchtower-queue.js' });
  function profile(revision, profileId, executionEpoch) {
    events.get('modhunter-install-queue-context')?.({detail:{revision,scope:profileId?{profileId,executionEpoch}:null}});
  }
  return {nodes, events, calls, settings, profile};
}
const flushQueue = () => new Promise(resolve => setImmediate(resolve));
test('Watchtower queue receipts stay exact/read only and quick hide redacts identity', async () => {
  const digest = 'abcdef12'.repeat(8);
  const response = { profileId: 'A', executionEpoch: 2, mode: 'running', active: null, activeInOtherProfile: true, history: [
    { kind: 'native', modName: 'Private target', state: 'complete', sourceArchive: {
      state: 'reviewedApiSelection', provider: 'nexus', filename: 'Selected exact.zip', modId: 44, fileId: 901 } },
    { kind: 'page', modName: 'Page target', state: 'complete', sourceArchive: {
      state: 'verifiedOriginalPage', bytes: 42, sha256: digest, filename: '../../private.zip' } },
    { kind: 'page', modName: 'Legacy target', state: 'failed', error: 'Private diagnostic' }
  ] };
  const {nodes, events, calls, settings, profile}=watchtowerQueueFixture(async()=>({ok:true,installQueue:response}));
  profile(1,'A',2);
  await flushQueue();
  const summary = nodes.get('installQueueStatus').textContent;
  const history = nodes.get('installQueueHistory');
  assert.match(summary, /active in another profile/);
  assert.match(history.textContent, /Selected exact\.zip.*Nexus project 44 \/ file 901/);
  assert.match(history.textContent, new RegExp('Verified original page archive.*42 bytes.*' + digest));
  assert.doesNotMatch(history.textContent, /private\.zip/);
  assert.match(history.textContent, /Archive evidence unavailable/);
  assert.equal((history.textContent.match(/FOMOD choices not captured/g) || []).length, 3);
  settings.hideTitles = true;
  events.get('modhunter-native-state')();
  assert.doesNotMatch(history.textContent, /Selected exact|Nexus project|Private target|Page target|Private diagnostic|abcdef12/);
  assert.match(history.textContent, /Archive identifiers hidden/);
  assert.match(history.textContent, /FOMOD choices not captured/);
  assert.deepEqual(calls.map(call => call.kind), ['installQueue']);
});


test('Watchtower erases stale queue rows, fences late GET/control across A/B/A and never reads while hidden', async () => {
  const pending=[];
  const {nodes, events, calls, profile}=watchtowerQueueFixture(kind=>new Promise(resolve=>pending.push({kind,resolve})));
  const snapshot=(profileId,executionEpoch,modName)=>({ok:true,installQueue:{profileId,executionEpoch,mode:'running',
    active:{kind:'page',modName,state:'installing'},history:[{kind:'page',modName,state:'complete'}]},message:'stale-control-toast'});
  const history=nodes.get('installQueueHistory'),status=nodes.get('installQueueStatus');
  const show=(profileId)=>assert.match(history.textContent,new RegExp(profileId));
  const cleared=()=>{assert.equal(history.textContent,'');assert.doesNotMatch(status.textContent,/private/i);};
  profile(1,'A',2);
  nodes.get('installQueueRefresh').onclick();
  pending.shift().resolve(snapshot('A',2,'Private initial A'));await flushQueue();show('initial A');
  nodes.get('installQueueRefresh').onclick();
  assert.equal(pending[0].kind,'installQueue');
  profile(2,'B',3);cleared();
  profile(3,'A',4);cleared();
  profile(2,'B',3); // Delayed context update must not reset the current scope.
  pending.shift().resolve(snapshot('A',2,'Private stale GET'));await flushQueue();cleared();
  assert.equal(pending[0].kind,'installQueue');
  pending.shift().resolve(snapshot('A',4,'Private current A'));await flushQueue();show('current A');
  nodes.get('installQueueStop').onclick();
  assert.equal(pending[0].kind,'installQueueControl');
  profile(4,'B',5);cleared();
  pending[1].resolve(snapshot('B',5,'Private current B'));await flushQueue();show('current B');
  pending.shift().resolve(snapshot('A',4,'Private stale control'));await flushQueue();show('current B');
  assert.doesNotMatch(status.textContent,/stale control/);
  assert.doesNotMatch(history.textContent,/stale control/);
  assert.equal(calls.includes('stale-control-toast'),false);
  const before=calls.length;
  nodes.get('downloadsView').hidden=true;events.get('visibility')();cleared();
  profile(5,'A',6);cleared();
  nodes.get('installQueueRefresh').onclick();await flushQueue();
  assert.equal(calls.length,before);
  nodes.get('downloadsView').hidden=false;events.get('visibility')();
  pending.pop().resolve(snapshot('A',5,'Private bad exact epoch'));
  await flushQueue();cleared();
});


test('Watchtower late queue mount replays initial scope without a hidden read; a newer event wins', async () => {
  const initialContext={revision:1,scope:{profileId:'A',executionEpoch:4}};
  const contextDuringReplay={revision:2,scope:{profileId:'B',executionEpoch:5}};
  for(const options of [{initialContext},{initialContext,contextDuringReplay}]){
    const expected=options.contextDuringReplay||options.initialContext;
    const response={ok:true,installQueue:{...expected.scope,mode:'running',
      history:[{kind:'page',modName:'Queued before mount: '+expected.scope.profileId,state:'complete'}]}};
    const {nodes,events,calls,profile}=watchtowerQueueFixture(async()=>response,{...options,initiallyHidden:true});
    assert.deepEqual(calls,[]);
    nodes.get('downloadsView').hidden=false;events.get('visibility')();
    await flushQueue();
    assert.deepEqual(calls.map(call=>call.kind),['installQueue']);
    assert.match(nodes.get('installQueueHistory').textContent,new RegExp('Queued before mount: '+expected.scope.profileId));
    profile(1,'A',4);await flushQueue();
    assert.equal(calls.length,1);
  }
});
