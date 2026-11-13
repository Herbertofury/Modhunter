# Modhunter 0.9.1: responsiveness and execution evidence

Prepared November 12, 2026 (America/Denver). Baseline: `17e4a9fc93712f6f4db4aafd762a5e5aedb2647d`, whose application code matches the published 0.9.0 release.

## What changed

| Area | Change | Preserved behavior |
| --- | --- | --- |
| Rust record audit | Hash buffered payload slices directly; allocate a record signature once per distinct type | Every byte receives a fresh SHA-256 check; malformed/truncated records remain errors |
| Rust workers | Retain one worker configuration; share import workers with the plugin phase | Existing active jobs retain their pool; worker bounds remain 1–32 |
| Find MO2 | Select the newest descriptor metadata across the entire directory; reuse its validated health result | At most 256 descriptor candidates, 32 endpoint probes and 8 discovery workers; local protocol validation remains required |
| One-mod Quick Update | Select only the requested mod instead of cloning the full catalog | Exact file choice, source/risk review and MO2's interactive installation remain authoritative |
| Nexus audit | Keep at most five submitted requests; stop submitting after cancellation/authentication/quota failure | Unverified rows stay unknown; cancelled metadata cannot overwrite newer evidence |
| Download execution | Recheck review context at file lookup and the native Qt handoff, including profile-session changes | No automatic replay of old commands after reconnect/profile switching |
| Repeat updates | Treat an installed ticket as history after MO2 observes a different installed version | Unconfirmed same-version installations and active downloads still block duplicates |
| Complete lists | Patch changed checklist/Workshop rows and coalesce rapid Workshop searches | All matching mods remain rendered; source links, notes, exports, keyboard actions and games remain available |
| Packaging | Validate one release version across Rust, Tauri, plugin, lockfile and build metadata | Existing published assets remain immutable; only passing native builds can publish a new version |

## Measured local component results

These are generated fixtures on Linux with warm OS caches, not measurements of a user's mod list, Windows desktop startup, Nexus network, downloaded archives, or FOMOD installation. Raw samples and workload identity are included.

### Rust plugin audit

Seven alternating baseline/candidate samples per workload, after one warmup for each. Every non-timing result field matched; the file digest, record count and scanned byte count were independently checked.

| Generated records | Payload per record | Previous median | 0.9.1 median |
| --- | ---: | ---: | ---: |
| 100,000 | 0 bytes | 136.55 ms | 8.66 ms |
| 100,000 | 32 bytes | 150.30 ms | 11.68 ms |
| 10,000 | 1,024 bytes | 23.34 ms | 10.93 ms |
| 1,024 | 65,537 bytes | 63.17 ms | 60.55 ms |

[Raw audit evidence](plugin-audit-comparison-0.9.1.json) · [Repeatable runner](../../scripts/benchmark_plugin_audit.py)

### Quick Update preparation

Twenty calls after warmup. The selected mod has two ambiguous cached files, so every call stops at file choice and makes no native download/install call. This isolates the cost of preparing one update on a large profile.

| Generated profile | Previous median | 0.9.1 median |
| --- | ---: | ---: |
| 2,500 mods | 34.923 ms | 0.166 ms |
| 10,000 mods | 143.853 ms | 0.168 ms |

[Raw preparation evidence](mo2-preflight-comparison.json) · [Repeatable runner](../../scripts/benchmark_live_preflight.py)

### Dashboard rendering

The [Chromium benchmark](../../scripts/benchmark_dashboard_render.py) compares the old and new `app.js` over the same complete 2,500-mod catalog and all other identical assets. It measures actual favorite clicks and download-ticket updates, handler/layout time, DOM additions/removals, retained rows and focus. It asserts that every action occurs and all 2,500 records remain available. Timing evidence is produced by the required Chromium CI job as `dashboard-render-comparison.json`, with source and fixture hashes.

The local execution runner cannot start Chromium because IPC sockets are prohibited. No local browser timing or rendering success is claimed. The CI browser job executes the actual dashboard, nine responsiveness scenarios and the benchmark; a failed or skipped required browser run cannot satisfy release readiness.

## Verification gates

The native workflow tests the following on its exact checkout before release:

- Rust workspace tests, format and Clippy on Linux and Windows; current local source passed 80 Rust tests.
- The Python update lifecycle suite, including bounded 12,000-project submission, profile switching, late callbacks, old tickets, changed release evidence and cache cancellation races.
- JavaScript runtime tests, including future updates after old installations, active-download duplicate blocking, search coalescing and profile isolation.
- Actual Python-to-Rust helper protocols: 17 plugin-audit assertions and 28 source-adapter assertions passed locally with the compiled CLI.
- Real Chromium interaction tests plus full-list mutation/focus tests. Structural filtering, sorting and DOM repair use complete lists.
- The compiled Windows WebView2 app: profile import, actual backup export/readback, tab controls and native-command boundaries.
- Official MO2 2.5.2 with a disposable fixture: real plugin initialization, Qt dispatch, profile inventory, focus, narrow C++ Nexus binding and Rust plugin audit.
- Source hashes for the tested production plugin, including the scanner and version module, checked against the packaged copies.
- Matching release versions, installer filename, build identity, helper identity, native receipts and screenshots before immutable publication.

See the [native workflow](https://github.com/Herbertofury/Modhunter/actions/workflows/native-rust.yml) and the exact build/run URLs in the release's `BUILD_INFO.json` and verification ZIP. The published release is the acceptance result; local source checks alone are not Windows evidence.

## Remaining personal-installation acceptance

The automated MO2 fixture does not use the user's real Nexus account, populated profile, game executable, saves, downloaded archives or FOMOD selections. Verify those in the intended installation before treating its complete update workflow as accepted. ESL eligibility and conversion hashes remain conditional facts about inspected plugin data; they do not certify every dependency, asset, script or save. Provider availability and account-specific failures cannot be guaranteed absent.
