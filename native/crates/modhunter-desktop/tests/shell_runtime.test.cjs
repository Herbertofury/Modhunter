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
  return { epoch: 1, importOperationId: 0, tabs: [{ id: 1, title: 'Mod Vault', url: 'http://tauri.localhost/Modhunter.html', loading: false }],
    activeId: 1, connected: false, endpoint: null, importing: false, auditing: false,
    offlineProfile: 'Fixture A', offlineModCount: 4, warnings: [],
    stats: { mods: 4, elapsedMs: 2, metadataCacheHits: 4 }, ...overrides };
}

async function harness(initial = snapshot()) {
  const nodes = new Map(), events = new Map(), calls = [], pending = new Map(), deferred = new Set();
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
      if (deferred.has(args.kind)) return new Promise(resolve => pending.set(args.kind, resolve));
      return { ok: true, state: current };
    } }, event: { listen: async (name, callback) => { events.set(name, callback); return () => {}; } } } };
  context.window = context;
  vm.runInNewContext(source, context, { filename: 'shell.js' });
  const settle = async () => { for (let i = 0; i < 4; i++) await new Promise(resolve => setImmediate(resolve)); };
  await settle();
  return { nodes, calls, pending, deferred, settle,
    emit(next) { current = next; events.get('modhunter-state')({ payload: next }); },
    resolve(kind, next, extra = {}) { pending.get(kind)({ ok: true, state: next, ...extra }); pending.delete(kind); deferred.delete(kind); } };
}

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
