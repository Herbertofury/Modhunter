'use strict';
function renderNexusHistory(data, allowLinks = false) {
  const get = (id) => document.getElementById(id);
  const labels = {
    candidate: 'Actionable',
    current: 'Current',
    review: 'Review',
    unverified: 'Unverified',
    unavailable: 'Unavailable',
    ignored: 'Ignored',
    unknown: 'Unknown',
    'newer-installed': 'Newer installed',
  };
  const fmt = (n) => n.toLocaleString();
  function dateText(value) {
    return value
      ? new Date(value)
          .toISOString()
          .replace('T', ' ')
          .replace(/\.\d{3}Z$/, ' UTC')
      : 'No recorded observation';
  }
  get('profile').textContent = data.profile;
  get('observation-date').textContent = data.lastObservation
    ? 'Latest recorded provider observation · ' + dateText(data.lastObservation)
    : 'No recorded provider observation.';
  get('dates').textContent =
    'Profile ' +
    data.profileId +
    ' · Imported ' +
    dateText(data.importedAt) +
    ' · Report generated ' +
    dateText(data.exportedAt) +
    (data.firstObservation
      ? ' · Public observations ' +
        dateText(data.firstObservation) +
        ' to ' +
        dateText(data.lastObservation)
      : ' · No provider observations');
  get('hash').textContent = data.catalogSHA256;
  const pageSize = 75;
  let selected = '',
    page = 0,
    matches = [];
  const searchable = data.rows.map((r) =>
    [
      r.name,
      r.verdict,
      labels[r.verdict],
      r.reason,
      r.installed,
      r.release,
      r.projectVersion,
      r.project,
      r.installedFile,
      r.targetFile,
      r.game,
      r.seen,
      r.sourceUrl,
      r.providerUrl,
      JSON.stringify(r.receipts),
    ]
      .join(' ')
      .toLowerCase(),
  );
  function element(tag, text, cls) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  }
  function link(parent, url, title) {
    if (!url) return;
    let safe;
    try {
      safe = new URL(url);
    } catch {
      return;
    }
    if (safe.protocol !== 'https:' || safe.username || safe.password) return;
    if (!allowLinks) {
      parent.append(element('span', title), element('span', safe.href, 'minor'));
      return;
    }
    const a = element('a', title);
    a.href = safe.href;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    parent.append(a);
  }
  for (const key of ['', ...new Set([...Object.keys(labels), ...Object.keys(data.totals)])]) {
    const count = key ? data.totals[key] || 0 : data.rows.length;
    if (key && !count) continue;
    const button = element('button', (key ? labels[key] || key : 'All') + ' ' + fmt(count));
    button.dataset.verdict = key;
    button.addEventListener('click', () => {
      selected = key;
      filter();
    });
    get('counts').append(button);
  }
  function render() {
    for (const button of get('counts').children)
      button.setAttribute('aria-pressed', String(button.dataset.verdict === selected));
    const pages = Math.max(1, Math.ceil(matches.length / pageSize));
    page = Math.min(page, pages - 1);
    const first = page * pageSize,
      last = Math.min(first + pageSize, matches.length);
    get('matches').textContent = matches.length
      ? 'Showing ' + fmt(first + 1) + '–' + fmt(last) + ' of ' + fmt(matches.length) + ' matching mods'
      : '0 matching mods';
    get('page').textContent = 'Page ' + fmt(page + 1) + ' of ' + fmt(pages);
    get('prev').disabled = page === 0;
    get('next').disabled = page + 1 === pages;
    get('empty').hidden = matches.length > 0;
    const fragment = document.createDocumentFragment();
    for (const i of matches.slice(first, last)) {
      const r = data.rows[i],
        tr = element('tr'),
        mod = element('td'),
        versions = element('td'),
        files = element('td'),
        when = element('td', dateText(r.seen));
      mod.append(
        element('span', labels[r.verdict] || r.verdict, 'state ' + r.verdict),
        element('br'),
        element('strong', r.name),
        element('p', r.reason || 'Evidence insufficient for an exact decision.', 'reason'),
      );
      versions.append(
        element('div', 'Installed ' + (r.installed || 'unknown')),
        element('span', 'Exact release ' + (r.release || 'unknown'), 'minor'),
      );
      if (r.projectVersion) versions.append(element('span', 'Project ' + r.projectVersion, 'minor'));
      if (r.cached) versions.append(element('span', 'MO2 cached ' + r.cached, 'minor'));
      link(
        files,
        r.providerUrl,
        r.targetFile ? 'Nexus file ' + r.targetFile : 'Nexus project ' + r.project,
      );
      if (!r.providerUrl) link(files, r.sourceUrl, 'Recorded source page');
      if (!r.providerUrl && !r.sourceUrl) files.append(element('span', 'No recorded source page'));
      if (r.game && r.project > 0)
        files.append(element('span', r.game + ' · project ' + r.project, 'minor'));
      if (r.installedFile) files.append(element('span', 'Installed file ' + r.installedFile, 'minor'));
      if (r.receipts.length) {
        const receipts = element('details');
        receipts.append(
          element('summary', 'MO2 file receipts (' + r.receipts.length + ')'),
          element('span', r.receipts.map((p) => p[0] + ' / ' + p[1]).join(', '), 'minor'),
        );
        files.append(receipts);
      }
      if (r.scope) files.append(element('span', r.scope, 'minor'));
      tr.append(mod, versions, files, when);
      fragment.append(tr);
    }
    get('rows').replaceChildren(fragment);
  }
  function filter() {
    const terms = get('query').value.toLowerCase().trim().split(/\s+/).filter(Boolean);
    matches = [];
    page = 0;
    data.rows.forEach((r, i) => {
      if ((!selected || r.verdict === selected) && terms.every((t) => searchable[i].includes(t)))
        matches.push(i);
    });
    render();
  }
  get('query').addEventListener('input', filter);
  get('prev').addEventListener('click', () => {
    page--;
    render();
  });
  get('next').addEventListener('click', () => {
    page++;
    render();
  });
  filter();
}
const embedded = document.getElementById('data').textContent.trim();
if (embedded) renderNexusHistory(JSON.parse(embedded), true);
