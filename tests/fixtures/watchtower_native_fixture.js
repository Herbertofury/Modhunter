// Renderer acceptance fixture only. Native WebView2 behavior is tested separately.
(() => {
  const clone = value => structuredClone(value);
  const state = __WATCHTOWER_INITIAL_STATE__;
  const listeners = new Set();
  let counter = 100;
  let runtimeRevision = 0;
  const now = () => Date.now();
  const f = window.__WATCH_FIX__ = {state, calls: [], failNext: {}, pending: [], defer: {}, active: {}, max: {}, exports: []};
  const enrich = watch => {
    watch.muted = state.settings.mutedOrigins.includes(new URL(watch.url).origin) || state.settings.mutedFolders.includes(watch.folder);
    watch.hasChanges = Boolean(watch.latest && watch.seenObservationId !== watch.latest.id && watch.state !== 'baseline');
    watch.needsCheck = !watch.latest;
    return watch;
  };
  const snapshot = () => {
    state.watches.forEach(enrich);
    const value = clone(state);
    // Native runtime replies have an ordered delivery revision separate from
    // persisted state. Read-only detail replies must not recursively fetch.
    value.storeRevision = state.revision;
    value.revision = ++runtimeRevision;
    for (const watch of value.watches) {
      delete watch.history;
      if (watch.latest) delete watch.latest.textPreview;
    }
    return value;
  };
  f.publish = value => {
    const payload = value ? clone(value) : snapshot();
    for (const listener of listeners) listener({payload});
  };
  f.change = mutate => { mutate(state); state.revision++; f.publish(); };
  f.release = kind => {
    f.defer[kind] = false;
    for (const pending of f.pending.filter(p => p.kind === kind)) pending.resolve();
    f.pending = f.pending.filter(p => p.kind !== kind);
  };
  const find = id => state.watches.find(w => w.id === id);
  const changed = extra => { state.revision++; return {ok: true, state: snapshot(), ...extra}; };
  const add = value => ({
    id: 'watch-' + counter++, url: value.url, title: value.title || new URL(value.url).host,
    folder: '', tags: [], notes: '', enabled: true, sessionId: null, requiresSession: false,
    selector: '', ignoreSelectors: [], intervalMinutes: 60, association: null,
    createdAt: now(), updatedAt: now(), addedAt: now(), removedAt: null,
    generation: 1, operationId: 0, state: 'new', lastAttemptAt: null, lastSuccessAt: null,
    nextCheckAt: 0, retryAt: 0, consecutiveErrors: 0, lastError: '', latest: null,
    seenHash: null, seenObservationId: null, seenEventId: null, history: [], ...clone(value),
  });
  const preview = document => {
    if (document.kind !== 'modhunter-watchtower' || document.schemaVersion !== 1) throw Error('This is not a supported Modhunter Watchtower export.');
    const entries = document.watches || [];
    const seen = new Set();
    const counts = {new: 0, duplicates: 0, restore: 0, invalid: 0, sessionRequired: 0};
    const items = entries.map((entry, index) => {
      let status = 'new', reason = '';
      try {
        const url = new URL(entry.url);
        if (url.protocol !== 'https:' || url.username || url.password || url.hostname === 'localhost') throw Error('Use a public HTTPS URL');
        if (seen.has(url.href) || state.watches.some(w => w.url === url.href)) status = 'duplicates';
        seen.add(url.href);
      } catch (error) { status = 'invalid'; reason = error.message; }
      counts[status]++;
      return {index, url: entry.url, title: entry.title || '', status, reason};
    });
    return {counts, items, warnings: []};
  };
  window.__TAURI__ = {
    event: {listen: async (event, listener) => {
      if (event !== 'watchtower-state') throw Error('Unexpected fixture event: ' + event);
      listeners.add(listener); return () => listeners.delete(listener);
    }},
    core: {invoke: async (command, args) => {
      if (command !== 'watchtower_command') throw Error('Unexpected fixture command: ' + command);
      const {kind, value = {}} = args;
      f.calls.push(clone({kind, value}));
      f.active[kind] = (f.active[kind] || 0) + 1;
      f.max[kind] = Math.max(f.max[kind] || 0, f.active[kind]);
      try {
        if (f.failNext[kind]) { const message = f.failNext[kind]; delete f.failNext[kind]; throw Error(message); }
        const captured = kind === 'state' ? snapshot() : null;
        if (f.defer[kind]) await new Promise(resolve => f.pending.push({kind, resolve}));
        if (kind === 'state') return {ok: true, state: captured};
        if (kind === 'detail') return {ok: true, state: snapshot(), watch: clone(enrich(find(value.id)))};
        if (kind === 'saveWatch') {
          const duplicate = state.watches.find(w => w.url === value.url && w.sessionId === value.sessionId && w.id !== value.id);
          if (duplicate) return {ok: true, state: snapshot(), duplicate: true, watchId: duplicate.id};
          let watch = find(value.id);
          if (watch) Object.assign(watch, clone(value), {generation: watch.generation + 1, updatedAt: now()});
          else { watch = add(value); state.watches.push(watch); }
          if (watch.sessionId) watch.requiresSession = true;
          return changed({watchId: watch.id});
        }
        if (kind === 'check' || kind === 'checkAll') {
          const targets = kind === 'check' ? (value.ids || [value.id]).map(find) : state.watches.filter(w => w.enabled && !w.muted && !w.removedAt);
          for (const watch of targets.filter(Boolean)) {
            watch.operationId++; watch.lastAttemptAt = now(); watch.lastSuccessAt = now();
            if (!watch.latest) {
              watch.latest = {id: 'observation-' + counter++, hash: 'fixture-baseline', title: watch.title,
                finalUrl: watch.url, startedAt: now(), checkedAt: now(), textBytes: 17,
                textPreview: 'Fixture release 1', previewTruncated: false};
              watch.seenObservationId = watch.latest.id; watch.seenHash = watch.latest.hash;
              watch.history.push({observationId: watch.latest.id, kind: 'baseline', checkedAt: now(), added: [], removed: []});
              watch.state = 'baseline';
            }
          }
          return changed();
        }
        if (kind === 'cancel' || kind === 'cancelAll') {
          const targets = kind === 'cancel' ? [find(value.id)] : state.watches;
          for (const watch of targets.filter(Boolean)) if (watch.state === 'checking') watch.state = 'cancelled';
          state.work = {active: [], queued: []}; return changed();
        }
        if (kind === 'markSeen') {
          const observations = value.observations || [value];
          if (observations.some(entry => find(entry.id).latest.id !== entry.observationId)) return {ok: false, error: 'Observation changed; review again'};
          const eventIds = [];
          for (const entry of observations) {
            const watch = find(entry.id);
            const event = {id: 'seen-' + counter++, watchId: watch.id, title: watch.title, at: now(),
              observationId: watch.latest.id, hash: watch.latest.hash, previousObservationId: watch.seenObservationId,
              previousHash: watch.seenHash, undone: false};
            state.history.push(event); watch.seenObservationId = watch.latest.id; watch.seenHash = watch.latest.hash; watch.seenEventId = event.id;
            eventIds.push(event.id);
          }
          return changed({eventId: eventIds[0], eventIds});
        }
        if (kind === 'undoSeen') {
          const event = state.history.find(e => e.id === value.eventId), watch = find(event.watchId);
          event.undone = true; watch.seenObservationId = event.previousObservationId; watch.seenHash = event.previousHash;
          return changed();
        }
        if (kind === 'pauseWatch') { for (const id of value.ids || [value.id]) { const watch = find(id); watch.enabled = value.enabled; watch.generation++; } return changed(); }
        if (kind === 'removeWatch' || kind === 'restoreWatch') { for (const id of value.ids || [value.id]) { const watch = find(id); watch.removedAt = kind === 'removeWatch' ? now() : null; watch.generation++; } return changed(); }
        if (kind === 'saveSession') {
          let session = state.sessions.find(s => s.id === value.id);
          if (session) Object.assign(session, clone(value), {updatedAt: now()});
          else { session = {id: 'session-' + counter++, state: 'notOpened', storageGeneration: 1, createdAt: now(), updatedAt: now(), lastOpenedAt: null, lastConfirmedAt: null, ...clone(value)}; state.sessions.push(session); }
          return changed({sessionId: session.id});
        }
        if (kind === 'openSession' || kind === 'confirmSession' || kind === 'resetSession') {
          const session = state.sessions.find(s => s.id === value.id);
          session.state = {openSession: 'opened', confirmSession: 'confirmed', resetSession: 'reset'}[kind];
          if (kind === 'openSession') session.lastOpenedAt = now();
          if (kind === 'confirmSession') session.lastConfirmedAt = now();
          if (kind === 'resetSession') session.storageGeneration++;
          return changed();
        }
        if (kind === 'removeSession') { state.sessions = state.sessions.filter(s => s.id !== value.id); return changed(); }
        if (kind === 'settings') { Object.assign(state.settings, clone(value)); return changed(); }
        if (kind === 'import') {
          const result = preview(value.document);
          if (value.preview) return {ok: true, preview: true, ...result, revision: state.revision};
          if (value.previewRevision !== state.revision) return {ok: false, error: 'Watchlist changed; preview again'};
          if (result.counts.invalid) return {ok: false, error: 'Invalid import'};
          result.items.filter(item => item.status === 'new').forEach(item => state.watches.push(add(value.document.watches[item.index])));
          return changed();
        }
        if (kind === 'exportFile') { f.exports.push(clone(value)); return {ok: true, message: 'Watchlist exported to Downloads/Modhunter-watchlist.json'}; }
        if (kind === 'openPage' || kind === 'openExternal') return {ok: true};
        if (kind === 'recover') { state.watches = []; state.readOnly = false; state.recoveryWarning = ''; return changed(); }
        throw Error('Unsupported native fixture operation: ' + kind);
      } finally { f.active[kind]--; }
    }},
  };
})();
