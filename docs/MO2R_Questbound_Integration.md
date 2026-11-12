# MO2R-inspired integration decisions for MO2 Update Arsenal 0.3

## Canonical evidence (2026-11-11)

- The user's private GitHub repository, `Herbertofury/MO2R`, is reachable but its `main` branch currently contains only a minimal `README.md` reading `# MO2R`. No reusable MO2R implementation source was available on that branch. **Do not present this release as a direct code port from MO2R.**
- The MO2R project wiki (`PCX-035`) and its archived source directives describe stable native mod identity, full dependency/source evidence, event-driven invalidation, exact related-mod navigation, no stale proxy-row assumptions, no drag/drop delegate substitution, preserved compact rows, and truthful source provenance.
- The MO2R Image Column wiki (`PRJ-017`) explicitly warns against replacing MO2's native full-list item delegate, as doing so broke insertion markers, separator visuals, and drag/drop behavior in earlier generations.
- The Bethesda Creations Version Tracker wiki (`PRJ-016`) requires idempotent metadata persistence and no false version/source conflicts.

## Implementation adapted for Update Arsenal

1. Read-only MO2 left-pane interaction through Qt QTreeView's existing selection model, no delegates, model replacement, or drop-event handler. Native row identity comes from the same MO2 snapshot as the browser entry; navigation rejects duplicates and profile drift.
2. Browser-to-native loopback focus requests require the exact active profile ID, verified item ID, and active session token. Browser text is never accepted directly as a native widget target.
3. Passive native selection sync only updates the tiny selection field in `/api/revision`; it does not invalidate the full profile snapshot for every mouse click.
4. Per-profile Chronicle persists verified native version changes, XP, combos, achievements, crown victories, checklists, and leaderboards. Browser checklist flags cannot mint ranked rewards.
5. Ranked full-clear requires a post-upgrade fresh, complete deep Nexus metadata pass with no unresolved or candidate verdicts for all Nexus-linked mods. Non-Nexus/local/generated entries are explicitly outside the current automated comparison coverage.
6. Browser and native UI launch/mutation are separate; browser remains sandboxed and only native Qt-thread work can touch MO2.

## Original research references

- MO2R repository: https://github.com/Herbertofury/MO2R
- MO2R wiki: https://docs.google.com/document/d/1uk-lBLco2W0R5FiqNmHBW-lkJvda4LdCRoAlZtHuNu8/edit
- Image Column wiki: https://docs.google.com/document/d/1wqos23hyt4Db9-N9-JPksRa7sJNHJiqsfMFoIgJInak/edit
- Bethesda Creation Tracker wiki: https://docs.google.com/document/d/1mY022iTDkKBM3KTXESEO_S83pCn7p24GMpWyZOIHnXo/edit
- Official MO2 source: https://github.com/ModOrganizer2/modorganizer

## Verification scope

Implemented and tested in isolated Python fixtures and real headless Chromium. The user's native Windows MO2 installation, live MO2R column compatibility stack, Nexus authentication, and actual FOMOD update completion cannot be launched from the current execution environment. The release remains a development build until those are exercised on a real host.
