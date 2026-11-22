# Audit response integrity at the MO2 adapter boundary

Based on canonical main `3231bb689bebb050e597044c4f001e006f7ab82a`.

## Failure and repair

The runner previously checked only schema number, profile identity and the outer mod map. A helper could omit a requested mod, answer a records request with header-only results, or return malformed report/plugin containers. Such responses replaced the last-good analysis; nested malformed values could also break compact/detailed projection.

Validate before returning from the runner and before merging native warnings:

- Exact requested header/records depth and coverage of every requested mod ID.
- Root metadata/container types and mod report status, warnings and plugin lists.
- Plugin dictionaries, nullable header/records/fingerprint objects, eligibility/provenance objects and consumed status/state strings, string errors and master-issue objects/string fields.
- Winning-plugin source-copy lists, with the same plugin checks.

Additional game-data/overwrite roots are allowed. Configured root paths do not imply separate reserved IDs because Rust deduplicates physical roots. Explicit unknown reports, null optional objects and `inventoryComplete=false` remain valid. This is structural/request-coverage validation, not exhaustive scalar/semantic validation or count/owner reconciliation. No inventory is silently truncated.

The coordinator's existing exception path retains the same-profile last-good result, reports failure and marks retained data `needsRecheck`. A later valid retry replaces it and clears the marker. Profile/epoch cancellation behavior is unchanged.

## Rebuilt verification

- Four actual subprocess regressions failed on the unchanged base: missing mod, wrong depth, invalid plugin row, scalar nested header.
- 34 focused Python tests pass after the repair, including the actual runner/coordinator failure-retention-retry lifecycle.
- Full Python discovery: 494 tests, 465 passed and 29 environment-dependent skips. Skips are not native acceptance.
- Independent review found no material contract or lifecycle blocker. Three pure validation test methods independently passed; independent temp-file tests were blocked by that reviewer's read-only execution context.
- Whitespace check passes.

This environment has no Rust toolchain or bundled native analyzer. A fresh Rust build/protocol verification, Windows/MO2 runtime acceptance and original startup/lag attribution remain open. The earlier workspace's protocol/performance measurements are not claimed for this rebuilt patch. No speed improvement is claimed. This draft is not a merge or release.
