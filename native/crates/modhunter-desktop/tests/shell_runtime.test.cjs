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

function snapshot(overrides = {}) {
  return { epoch: 1, tabs: [{ id: 1, title: 'Mod Vault', url: 'http://tauri.localhost/Modhunter.html', loading: false }],
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
    resolve(kind, next) { pending.get(kind)({ ok: true, state: next }); pending.delete(kind); deferred.delete(kind); } };
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
