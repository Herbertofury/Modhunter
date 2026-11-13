# Modhunter 0.9.2: responsive refreshes, cancellation and verified upgrades

Prepared November 12, 2026 (America/Denver). This pass starts from the published **0.9.1** source, `63e5df3e3ef218ae72ce58b5e4502165c40ca604`. Earlier import and 0.9.1 audit measurements remain historical evidence with their original baselines.

## Implemented behavior

| Area | Change | Preserved behavior |
| --- | --- | --- |
| Offline imports and plugin audits | The native toolbar offers **CANCEL**, then **CANCELING…** while workers settle. Superseding profile/settings/connection operations also cancel old work. | The last complete catalog stays available; an interrupted read cannot become a successful partial catalog or hash. The same engine can run another refresh/audit. |
| Rust payload reads | Cancellation is checked between files and at bounded 64 KiB payload intervals. | Fresh metadata, plugin header and SHA-256 checks remain enabled. Cancellation is cooperative; an individual operating-system read must return before its next checkpoint. |
| Native tabs and toolbar | Dashboard tabs share the encoding of an immutable catalog. Ordered state revisions reject delayed toolbar replies; page-side origin/revision checks reject stale catalog injections. | The full catalog is available in every intended local dashboard tab. Live MO2 pages and game views cannot receive an offline catalog injection. |
| MO2 snapshot delivery | Concurrent requests share one complete encoded snapshot for the same observed state. JSON encoding occurs outside the native execution guard. The cache holds at most one response, up to 32 MiB. | Catalog/profile/source/cache/audit changes invalidate it, and the existing 15-second health freshness window remains bounded. Mutation preflights read current authoritative state rather than the response cache. |
| Progress and source edits | Ordinary progress writes avoid catalog decoration. Source changes advance execution/scan epochs and preserve original MO2 source identity beneath the explicit override. Delayed Chronicle reads preserve fields edited while loading. | Clearing an override restores the original source; a previous source's release result cannot authorize the new source. Pending source edits must synchronize before executing an update. Failed initial progress reads retry without overwriting unseen server progress. |
| External adapter cache | A scan must still own the current generation when it updates the shared cache. | A canceled or superseded scan cannot overwrite a newer scan's cached metadata. |
| Complete dashboard lists | Row markup is reused until its catalog, row values, analysis, source evidence or freshness phase changes. Cosmetic progress changes retain the local Workshop plan. | No pagination, virtualization or record cap is introduced. Expiry/cooldown transitions also invalidate searchable source status. |
| Download controls | Ticket rows are reconciled by identity through updates and moves, preserving the applicable focused control. | Existing download states, exact-file choices, retry and native installation behavior remain available. |
| Plugin installation | Released packages must have matching build/plugin versions and matching SHA-256 entries for every installed packaged plugin file before replacement. | The previous plugin and fallback progress are retained; rejected incomplete, damaged, unlisted or mixed files leave them untouched. Documented source-tree installation with compiled helpers remains supported. |
| Release validation | The portable archive's checksum manifest must cover exactly its files and match every file. Windows filename collisions are rejected. | Existing source/helper identity, native receipt, version and immutable-release gates remain required. |

## Reproducible measurements

The required Chromium job runs [the dashboard comparison](../../scripts/benchmark_dashboard_render.py) against the exact published 0.9.1 baseline above. It compares a complete 2,500-mod catalog with identical shared assets and measures favorite changes and download-ticket updates over alternating runs. Its `dashboard-render-comparison.json` records the actual candidate commit, fixture/source hashes, retained rows, focus and timings, and is included in the release verification ZIP.

### Complete MO2 HTTP delivery

[Raw samples, environment, fixture and source hashes](mo2-catalog-delivery-comparison-0.9.2.json) · [reproduction script](../../scripts/benchmark_catalog_delivery.py)

The delivery comparison uses the actual Python loopback HTTP service against the complete 0.9.1 Python package. Each case has two warmups followed by nine samples with alternating baseline/current order. Both variants receive the same fixed source-freshness clock; elapsed time uses the performance clock. Every returned catalog is byte-for-byte equal, and progress writes persist equal data to owned temporary Chronicle files.

| HTTP workload | Mods | 0.9.1 median | 0.9.2 median | Median ratio |
| --- | ---: | ---: | ---: | ---: |
| Repeated complete snapshot | 2,500 | 56.658 ms | 1.829 ms | 30.97× |
| One-row progress save | 2,500 | 47.789 ms | 1.313 ms | 36.39× |
| Uncached snapshot after plugin analysis | 2,500 | 186.752 ms | 105.276 ms | 1.77× |
| Repeated complete snapshot | 10,000 | 285.561 ms | 4.217 ms | 67.72× |
| One-row progress save | 10,000 | 235.108 ms | 1.288 ms | 182.52× |
| Uncached snapshot after plugin analysis | 10,000 | 917.622 ms | 594.698 ms | 1.54× |

The repeated snapshot workload returns every mod; its gain comes from reusing the encoded response for unchanged observed state. The uncached analyzed fixture has one plugin per mod and 32 record counters. The progress workload changes one saved row; it does not stand for saving a fully populated 10,000-row Chronicle. These synthetic Linux measurements exclude native MO2, browser rendering, Nexus, downloads, FOMOD installation, Windows startup and the user's populated profile. The raw sample spread is retained, and the ratios are not timing guarantees for other machines.

### Cancellation without changing audit results

[Raw audit samples and equality checks](plugin-audit-comparison-0.9.2.json) · [reproduction script](../../scripts/benchmark_plugin_audit.py)

The cancellable reader checks the token on physical reads beneath its buffer, with reads bounded to 64 KiB. These five-sample engine medians compare the published 0.9.1 CLI and 0.9.2 on identical deterministic plugin bytes, after one warmup per revision and with alternating sample order:

| Records | Payload per record | 0.9.1 median | 0.9.2 median |
| ---: | ---: | ---: | ---: |
| 100,000 | 0 B | 8.425 ms | 8.466 ms |
| 100,000 | 32 B | 10.963 ms | 11.167 ms |
| 10,000 | 1,024 B | 9.938 ms | 9.791 ms |
| 1,024 | 65,537 B | 54.161 ms | 53.130 ms |

All non-timing result fields match, and exact file hashes and record counts are independently verified. These samples are within roughly 2% of the baseline and do not establish a meaningful audit-speed gain. They show that adding cancellation preserves the prior performance level on these workloads. OS caches are warm; actual Windows GUI/MO2 behavior and download/install throughput are outside this comparison. The larger 0.9.1 improvement remains documented in its original evidence file.

The local execution environment cannot launch Chromium because its IPC sockets are prohibited. Browser acceptance therefore comes from the required real Chromium CI job; a skipped local browser check is not presented as browser proof.

## Verification contracts

The exact candidate and `main` checkouts run the native release workflow before publication:

- Rust cancellation tests interrupt both metadata imports and deep plugin reads, require an explicit canceled result, and then successfully reuse the same engine.
- Native catalog tests verify shared encoding, copy-on-write invalidation and concurrent tabs. Toolbar JavaScript tests cover canceled work, late replies and injection origin/order checks.
- Python catalog-delivery tests cover concurrent response reuse, owned-copy isolation, profile/source/cache/audit invalidation, serialization outside the execution lock, health freshness and cache size bounds.
- Source-cache ownership tests pause old writes and overlap scans to check that newer metadata survives.
- Frontend runtime tests cover row/search cache dependencies, provider expiry, same-profile execution changes, delayed progress hydration and source edits during queued updates. Real Chromium tests check actual DOM retention and focus behavior.
- The shipped plugin installer runs under **Windows PowerShell 5.1** in disposable Windows fixtures. They verify progress preservation, complete-package checks, exact version output, source-tree installation and backup-directory handling.
- Release self-tests check invalid versions, missing proof, source/helper identity, all internal checksums, duplicate Windows filenames and immutable output.
- Actual Windows WebView2 and official **MO2 2.5.2** fixtures remain required. These cover the compiled app, real import/export readback, native dispatch, plugin inventory, Qt binding and fresh Rust plugin analysis.

The passing run, exact build commit and selected receipts are recorded in the published release's `BUILD_INFO.json` and verification ZIP. A workflow definition or a local unit-test result alone does not establish Windows/MO2 acceptance.

## Remaining personal-installation acceptance

The automated MO2 fixture does not use the user's real Nexus account, populated profile, commercial game executable, saves, mod archives or FOMOD selections. Those require acceptance in the intended installation. Plugin evidence remains conditional on the files and dependencies actually inspected. The Windows application is not code-signed; package checksums detect changed or mixed release files and do not provide publisher code signing.
