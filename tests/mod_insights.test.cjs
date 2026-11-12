'use strict';

const assert = require('node:assert/strict');
const { test } = require('node:test');
const insights = require('../mo2_update_arsenal/web/mod_insights.js');

// Cases follow the actual serialized PluginCopy/ModPluginAudit contract and the
// binary fixtures in native/crates/modhunter-core/tests/plugin_analysis.rs.
// A local run of the real audit-plugins CLI confirmed candidate, losing full
// original, current light output, invalid light records, and missing-master
// outputs. This is presentation coverage, not native MO2 or save-safety proof.
function plugin(overrides = {}) {
  return { name: 'Example.esp', ownerModId: 'Source', ownerModName: 'Source',
    path: '/fixture/mods/Source/Example.esp', priority: 0, installedVersion: '1.0',
    enabled: true, ghosted: false, winner: true, winnerKnown: true, winnerBy: 'priority',
    winnerModId: 'Source', winnerLight: false, lightOverride: false, active: true, loadOrder: 0,
    header: { flags: 0, version: 1.7, recordHeaderVersion: 44, masters: [], lightFlag: false,
      lightByExtension: false, lightEffective: false, masterFlag: false, mediumFlag: false, updateFlag: false },
    runtimeSupport: 'supported',
    eligibility: { status: 'flag-only-candidate', reasonCodes: ['record-range'],
      notes: ['Own record IDs fit the light range; external references and save safety are not assessed.'], saveSafety: 'not-assessed' },
    records: { totalRecords: 1, newRecords: 1, maximumNewLocalId: 0x800 },
    fingerprint: { sha256: 'a'.repeat(64) }, masterIssues: [], unresolvedMasters: [],
    provenance: { state: 'unknown', tool: null, evidencePath: null, originalPath: null, outputPath: null,
      originalSha256: null, outputSha256: null, reason: 'A current light flag does not establish conversion history.' },
    errors: [], ...overrides };
}

function mod(plugins = [], { analysis = {}, health = {}, ...overrides } = {}) {
  return { id: 'Source', name: 'Fixture mod', nexusId: 145168,
    localAnalysis: { status: 'mixed', plugins, warnings: [], checkedAt: '2026-11-12T12:00:00Z',
      depth: 'records', inventoryComplete: true, ...analysis },
    modHealth: { provider: 'nexus', page: { status: 'available' }, bugs: { state: 'unknown', displayedCount: null }, ...health },
    ...overrides };
}

function light(overrides = {}) {
  return plugin({ header: { ...plugin().header, flags: 0x200, lightFlag: true, lightEffective: true },
    winnerLight: true, eligibility: { status: 'already-light', notes: [], saveSafety: 'not-assessed' }, ...overrides });
}

test('an observed zero is preserved and an unknown bug count never becomes zero', () => {
  const zero = mod([], { health: { bugs: { state: 'available', displayedCount: 0 } } });
  const unknown = mod();
  assert.equal(insights.bugLabel(zero.modHealth.bugs), 'Bugs 0');
  assert.equal(insights.summary(zero).bugCount, 0);
  assert.equal(insights.csv(zero)[9], 0);
  assert.equal(insights.summary(unknown).bugCount, null);
  assert.equal(insights.csv(unknown)[9], '');
  assert.equal(insights.bugLabel(unknown.modHealth.bugs), 'Bugs unverified');
  assert.doesNotMatch(insights.details(unknown), /Nexus displayed tab count: 0/);
});

test('stale public counts keep their age and never infer author removal or an open total', () => {
  const row = mod([], { health: { bugs: { state: 'available', displayedCount: 1,
    checkedAt: '2026-11-10T12:00:00Z', freshness: { state: 'stale' }, reason: 'Observed Nexus tab badge.' } } });
  assert.match(insights.chips(row), /Bugs 1 · stale/);
  assert.match(insights.details(row), /refresh needed/);
  assert.match(insights.details(row), /Open, resolved and total counts are not established/);
  assert.doesNotMatch(insights.details(row), /author (?:removed|disabled)|1 open bugs?/i);
  assert.equal(insights.csv(row)[10], '2026-11-10T12:00:00Z');
});

test('disabled, hidden, locked and unavailable evidence have distinct visible labels', () => {
  const labels = new Set();
  for (const state of ['disabled', 'hidden', 'locked', 'unavailable', 'unknown']) {
    const row = mod([], { health: { bugs: { state, displayedCount: null } } });
    labels.add(insights.bugLabel(row.modHealth.bugs));
    assert.doesNotMatch(insights.details(row), /author (?:removed|disabled)|Bugs 0/i);
    assert.equal(insights.matches(row, 'bugs-disabled'), ['disabled', 'hidden', 'locked'].includes(state));
    assert.equal(insights.matches(row, 'bugs-unknown'), ['unknown', 'unavailable'].includes(state));
  }
  assert.equal(labels.size, 5);
});

test('a real flag-only-candidate result remains a candidate and never a safety guarantee', () => {
  const row = mod([plugin()]);
  assert.equal(insights.summary(row).candidates, 1);
  assert.match(insights.chips(row), /ESL candidate/);
  assert.match(insights.details(row), /Flag-only candidate/);
  assert.match(insights.details(row), /still needs xEdit \/ ESLifier review/);
  assert.doesNotMatch(insights.details(row), /safe to (?:convert|ESL)|safely (?:convert|ESL)|guaranteed safe|conversion verified safe/i);
});

test('invalid light records retain their header fact but visibly require maintenance', () => {
  // Emitted by the actual Rust fixture with ESL flag 0x200 and own FormID 0x1000.
  const row = mod([light({ eligibility: { status: 'blocked', notes: ['Own records exceed the light range.'], saveSafety: 'not-assessed' } })]);
  assert.equal(insights.summary(row).light, 1);
  assert.equal(insights.matches(row, 'maintenance'), true);
  assert.match(insights.chips(row), /blocked/i);
  assert.match(insights.details(row), /Conversion blocked/);
});

test('incomplete record framing remains visible in both full and compact row facts', () => {
  for (const evidence of [{ records: { scanComplete: false } }, { recordScanComplete: false }]) {
    const row = mod([plugin({ ...evidence, eligibility: { status: 'unknown', notes: ['Declared record count differs.'] } })]);
    assert.equal(insights.matches(row, 'maintenance'), true);
    assert.equal(insights.matches(row, 'esl-review'), true);
    assert.match(insights.chips(row), /Record scan incomplete/);
    assert.equal(insights.summary(row).candidates, 0);
  }
});

test('inactive light plugins are distinct from active light plugins', () => {
  const row = mod([light({ active: false, loadOrder: null })]);
  assert.equal(insights.summary(row).light, 0);
  assert.equal(insights.summary(row).inactive, 1);
  assert.equal(insights.matches(row, 'light'), false);
  assert.match(insights.chips(row), /inactive plugin/);
  assert.match(insights.details(row), /Light · Inactive/);
});

test('an inactive light override does not imply an active ESL in the load order', () => {
  const row = mod([plugin({ winner: false, active: false, loadOrder: null,
    winnerModId: 'ESLifier Output', winnerLight: true, lightOverride: true })]);
  assert.equal(insights.summary(row).lightOverrides, 0);
  assert.equal(insights.matches(row, 'light'), false);
  assert.match(insights.chips(row), /inactive/i);
  assert.doesNotMatch(insights.chips(row), />Light copy wins</);
});

test('an active winning light override does not fabricate conversion history', () => {
  const row = mod([plugin({ winner: false, winnerModId: 'ESLifier Output', winnerLight: true, lightOverride: true })]);
  assert.equal(insights.summary(row).lightOverrides, 1);
  assert.equal(insights.summary(row).light, 0);
  assert.match(insights.chips(row), /Light copy wins/);
  assert.match(insights.details(row), /Conversion history unverified/);
  assert.match(insights.details(row), /do not establish which tool converted it/);
  assert.doesNotMatch(insights.details(row), /Matches recorded ESLifier output/);
});

test('activation-unknown evidence stays explicit in the plugin detail', () => {
  const row = mod([light({ active: null, loadOrder: null })]);
  assert.match(insights.details(row), /Light · Activation unknown/);
  assert.match(insights.details(row), /Load order<\/dt><dd>Not established/);
});

test('missing, inactive and late masters remain separate maintenance evidence', () => {
  const row = mod([plugin({ eligibility: { status: 'blocked', notes: [], saveSafety: 'not-assessed' }, masterIssues: [
    { name: 'Missing.esm', status: 'missing' }, { name: 'Disabled.esm', status: 'inactive' }, { name: 'Late.esm', status: 'loads-after' }
  ] })]);
  assert.equal(insights.summary(row).masterIssues, 3);
  assert.equal(insights.matches(row, 'masters'), true);
  assert.equal(insights.matches(row, 'maintenance'), true);
  const html = insights.details(row);
  assert.match(html, /Missing\.esm · missing/);
  assert.match(html, /Disabled\.esm · inactive/);
  assert.match(html, /Late\.esm · loads after this plugin/);
});

test('each recorded conversion mismatch calls for review without inventing an author', () => {
  for (const state of ['source-changed', 'output-changed', 'source-and-output-changed', 'winner-changed']) {
    const row = mod([plugin({ provenance: { state, tool: 'ESLifier', reason: 'Fresh owned-file evidence differs from the recorded state.' } })]);
    assert.equal(insights.summary(row).stale, 1, state);
    assert.equal(insights.matches(row, 'stale-conversion'), true, state);
    assert.equal(insights.matches(row, 'maintenance'), true, state);
    assert.match(insights.chips(row), /ESLifier needs review/);
    assert.doesNotMatch(insights.details(row), /author (?:removed|converted)|Matches recorded ESLifier output/i);
  }
});

test('invalidated record and conversion evidence cannot retain a current claim', () => {
  const row = mod([plugin({ provenance: { state: 'current', tool: 'ESLifier', reason: 'Prior recorded hashes matched.' } })],
    { analysis: { needsRecheck: true } });
  assert.equal(insights.summary(row).candidates, 0);
  assert.equal(insights.matches(row, 'maintenance'), true);
  assert.equal(insights.matches(row, 'esl-review'), true);
  assert.match(insights.details(row), /fresh record scan|Recheck before using/);
  assert.doesNotMatch(insights.details(row), /Matches recorded ESLifier output/);
  assert.doesNotMatch(insights.details(row), /Current SHA256/);
});

test('unsafe links and untrusted names are escaped throughout details and related actions', () => {
  const attack = '\"><img src=x onerror=alert(1)><script>alert(2)</script>&';
  const row = mod([plugin({ name: attack, winner: false, winnerModId: attack,
    header: { ...plugin().header, masters: [attack] }, masterIssues: [{ name: attack, status: attack }],
    eligibility: { status: 'unknown', notes: [attack] }, provenance: { state: 'unknown', reason: attack }, errors: [attack] })],
    { id: attack, health: { bugs: { state: 'unknown', reason: attack }, links: {
      bugs: 'javascript:alert(1)', requirements: 'https://user:password@example.com/', changelog: 'data:text/html,test',
      files: 'https://www.nexusmods.com/skyrimspecialedition/mods/145168?tab=files&fixture=1'
    } } });
  const html = insights.details(row, { live: true, auditState: { error: attack } });
  assert.doesNotMatch(html, /<script|<img|href="(?:javascript|data):|href="https:\/\/user:/i);
  assert.match(html, /&lt;img/);
  assert.match(html, /data-owner="&quot;&gt;&lt;img/);
  assert.match(html, /href="https:\/\/www\.nexusmods\.com\/skyrimspecialedition\/mods\/145168\?tab=files&amp;fixture=1"/);
  assert.match(html, /rel="noopener noreferrer"/);
});

test('invalid bug numbers do not leak a negative or invented count into details or CSV', () => {
  for (const displayedCount of [-1, 1.5, Number.NaN, Number.MAX_SAFE_INTEGER + 1, '0']) {
    const row = mod([], { health: { bugs: { state: 'available', displayedCount } } });
    assert.equal(insights.summary(row).bugCount, null);
    assert.equal(insights.csv(row)[9], '');
    assert.equal(insights.bugLabel(row.modHealth.bugs), 'Bugs tab visible');
    assert.doesNotMatch(insights.details(row), /Nexus displayed tab count:/);
  }
});

test('an explicit non-Nexus provider does not expose Nexus actions from an old metadata ID', () => {
  const row = mod([], { nexusId: 145168, source: { type: 'github', verified: true,
    url: 'https://github.com/fixture/owned-project' }, health: { provider: 'github', bugs: { state: 'unsupported', displayedCount: null },
    links: { bugs: 'https://github.com/fixture/owned-project/issues' } } });
  assert.doesNotMatch(insights.chips(row), /Bugs unverified|Refresh public Nexus evidence/);
  assert.doesNotMatch(insights.details(row, { live: true }), /Refresh Nexus health|data-insight-act="health"/);
  assert.match(insights.details(row), /href="https:\/\/github\.com\/fixture\/owned-project\/issues"/);
});

test('a row without local analysis exports unknown facts as blank instead of zero', () => {
  const row = { id: 'unscanned', name: 'Unscanned source' };
  assert.deepEqual(insights.csv(row).slice(0, 8), ['', '', '', '', '', '', '', '']);
  assert.equal(insights.matches(row, 'light'), false);
  assert.equal(insights.matches(row, 'stale-conversion'), false);
});

test('no-plugin facts require completed inventory rather than an empty partial result', () => {
  assert.match(insights.chips(mod([], { analysis: { inventoryComplete: true } })), /No plugin files/);
  const partial = mod([], { analysis: { inventoryComplete: false } });
  assert.doesNotMatch(insights.chips(partial), /No plugin files/);
  assert.match(insights.details(partial), /No owned plugin copies have been established/);
});

test('a multi-plugin mod retains separate active light, active full and inactive counts', () => {
  const row = mod([light({ name: 'Light.esp' }), plugin({ name: 'Full.esp' }), light({ name: 'Disabled.esp', active: false })]);
  const result = insights.summary(row);
  assert.equal(result.plugins, 3);
  assert.equal(result.light, 1);
  assert.equal(result.full, 1);
  assert.equal(result.inactive, 1);
  assert.match(insights.chips(row), /1 light · 1 full/);
});
