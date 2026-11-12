# Modhunter QoL candidate: local verification

Recorded on **2026-11-12** for the Rust/plugin-intelligence expansion built on local commit `990c27671bc450ad50c2e203220244c978a6f63c`.

This document records the candidate's executed local checks. The latest Windows application, installer and real-MO2 gates have been prepared but have not run for this candidate. It is not a released-build acceptance certificate.

## Implemented scope

- Rust header inventory and streaming record/FormID audit, real winning-copy/activation/order facts, master issues, bounded fresh SHA-256 hashing and selected ESLifier provenance.
- Live Qt-owned input capture, background helper execution, superseded-read cancellation, context epochs, native selection/download indexes, compact catalog facts and detailed exact-row reads.
- Provider-aware source availability and public Nexus Bugs observations, with explicit unknown/zero/restricted/unavailable distinctions and per-observation timestamps.
- Checklist and Workshop chips, inspector evidence/actions, maintenance/ESL/master/bug-state filters, searchable plugin states and CSV/plan exports.
- Native desktop audit/folder controls, bundled MO2 Rust helper, progress-preserving installer updates and stronger release evidence requirements.

The [feature guide](../../wiki/Mod-Intelligence.md) explains the controls, automatic invalidation and qualification boundaries.

## Executed local checks

| Check | Executed result | Evidence scope |
|---|---|---|
| Rust workspace tests | **46 passed** | 38 core tests and 8 portable desktop/policy tests. Linux does not compile the Windows-only runtime module. |
| Rust formatting | **Passed** | `cargo fmt --all --check`. |
| Strict Rust Clippy | **Passed** | Workspace/all-targets with `-D warnings`, on Linux. |
| Full Python suite | **274 tests run; OK, one browser class skipped** | Final run: 26.520 seconds. Includes native facts, actual temporary subprocess IO/cancellation/limits, lifecycle races, caches, exact-row APIs and existing updater contracts. |
| JavaScript tests | **39 passed** | 12 actual dashboard-closure runtime tests, 19 insights tests, 4 native-toolbar runtime tests, 4 preserved legacy-import tests. These are not browser rendering tests. |
| Actual Python → compiled Rust boundary | **17 checks passed** | Production request builder and subprocess runner invoke the actual Linux release CLI on disposable binary/plugin/provenance fixtures. |
| Native audit probe self-tests | **5 passed** | Bounded HTTP/proof contract, including rejected bad profiles, incomplete jobs and helper mismatches. No native MO2 execution. |
| Release preparation self-tests | **8 passed** | Missing/degraded native audit receipts, source/helper mismatches, artifact constraints and valid assembly. No release publication. |
| Dashboard asset build | **Passed** | Rebuilt complete shared dashboard and existing game assets from source. |
| Whitespace/diff validation | **Passed** | `git diff --check`. |

The compiled shared dashboard is **705,522 bytes**, SHA-256:

```text
81df83f1683be3f8da5ad39fc7b4ed8ce7ca65445269d49097da91f8eb11a0ec
```

### Actual JSON-protocol evidence

[plugin-bridge-protocol.json](plugin-bridge-protocol.json) binds the exercised production Python/Rust files and executable to their hashes. It establishes that:

1. The selected original/output files cross the JSON boundary with MO2-like owned paths and winning state.
2. Matching recorded plugin bytes yield `current` and an observed light override.
3. Changing the original's valid bytes while leaving installed-version metadata unchanged yields a new hash and `source-changed` on both relevant rows.
4. The scan preserves source, output and ESLifier records.
5. The real-MO2 fixture's 42-byte disabled TES4 header receives a fresh hash while its synthetic unknown game remains unsupported for conversion.

Organizer methods in this local proof are fixture doubles. The parser and Python subprocess runner are production code. Single-run timings in that receipt are not performance benchmarks.

### Confirmed regression fixes

The tests cover source/output/winner changes, mutable ESLifier flags, malformed records, missing or ambiguous masters, native/profile changes during queued work, provider identity changes, response bounds and unsaved inspector notes. Independent review found and reproduced an EOF-at-record-boundary case: a valid TES4 header declaring nonzero records could otherwise resemble an empty candidate. The rebuilt CLI now returns an incomplete scan and unknown eligibility for that case.

The subprocess output limit is enforced while the helper runs, including partial final reads. Superseded reads stop their own helper, and no stale result may replace a later A → B → A profile epoch.

## Performance evidence

[import-comparison.json](import-comparison.json) contains five-sample medians and all samples for identical generated Linux metadata fixtures, with the new fast plugin-inventory path enabled. Its importer source hash matches this candidate.

| Fixture | Rust fresh engine | Previous Node engine | Rust persistent warm | Previous Node persistent warm |
|---|---:|---:|---:|---:|
| 2,500 mods | 59.45 ms | 286.40 ms | 42.10 ms | 261.45 ms |
| 10,000 mods | 297.39 ms | 1,075.33 ms | 213.43 ms | 979.68 ms |

These fixtures contain metadata and DLL markers, not actual ESP payloads. They measure imports, not deep audits, GUI startup, native MO2 or Nexus. OS caches were not forcibly evicted. The earlier metadata-only results remain in [import-comparison-metadata-only.json](import-comparison-metadata-only.json) and are not substituted for this candidate's timings.

## Pending native acceptance

The local runner blocks Chromium IPC sockets. The actual Chromium test class was skipped; the workflow requires it on its browser-capable runner.

The latest candidate still needs these prepared CI gates:

- Windows-specific Rust compilation/Clippy, application and NSIS build.
- Actual WebView2 launch, import, export and IPC-isolation smoke.
- Actual Windows PowerShell installation, upgrade, data-preservation and linked-path rejection fixtures. The installer has a rollback branch, but these fixtures do not inject a post-backup failure to exercise it.
- Official MO2 2.5.2 fixture, including real Qt dispatch into the shipped Rust helper, a completed deep audit, exact fixture-row decoration and fresh synthetic-plugin hash evidence.

Final native code review also changed ESLifier-folder persistence to save prospective preferences before replacing the effective path or invalidating analysis. A failed write therefore returns before those state changes. This Windows-only control flow was reviewed and formatted locally; runtime validation remains pending with the native candidate.

Release preparation rejects a missing `rust_plugin_audit` receipt, stale intelligence source hashes, or a missing/mismatched helper in the portable package. The source hash contract includes the three new intelligence modules. Independent Windows jobs bind their own helper builds; they are not assumed byte-identical across jobs.

An earlier Windows/browser run, [43181501964](https://github.com/Herbertofury/Modhunter/actions/runs/43181501964), succeeded for remote commit `ec1b058fd6f3c1b5a3050d76ba78314f4dd82f47`. It predates this QoL expansion and is not evidence that these new native paths passed.

The test fixtures contain no user's mod list, Nexus account, commercial game or save. Real-account downloads/FOMOD choices and in-game behavior remain outside their scope. Conditional ESL eligibility and plugin-hash consistency do not certify save or complete asset/dependency safety.

## Delivery status

All work above is local. Automatic approval review previously rejected external source publication because explicit end-user approval to publish to `Herbertofury/Modhunter` was required. No retry or alternate publication route has been used for this candidate. Publishing the candidate, running its final native CI, merging after success and releasing the verified Windows artifacts remain awaiting that authorization.
