# 🛠 Development & Testing

## Rust 0.12.0 release gates

The [native workflow](https://github.com/Herbertofury/Modhunter/actions/workflows/native-rust.yml) compiles and launches the actual Windows app, tests the production plugin inside an official MO2 2.5.2 fixture instance, runs Rust tests and strict Clippy, and executes browser and lifecycle regressions. See [Rust Desktop](Rust-Desktop.md) for the evidence boundaries and [native/README.md](../native/README.md) for build commands. Downloadable receipts identify each passing check and the compiled commit. The older acceptance notes below describe their original checkpoints.

These are required gates for each release. The [Atelier evidence guide](Atelier-0.12.md) records the published v0.12.0 build, its actual main-run measurements, public download verification and runtime limits. Source snapshots, diagnostic experiments and synthetic release-validator tests retain their own scope; they do not replace final native acceptance.

`tests/test_scan_lifecycle.py` covers stale completions, errors and progress, including A→B→A; `tests/test_source_identity.py` covers source authority and route-preserving URL cleanup. `tests/frontend_runtime.test.cjs` checks polling, stale reads, cosmetic rendering and source identity. `tests/test_frontend_responsiveness.py` runs the compiled dashboard in actual Chromium. CI requires the browser with `MODHUNTER_REQUIRE_BROWSER=1`, so a missing browser cannot silently pass that gate.

The 0.9.2 cases in `tests/test_catalog_delivery.py` cover concurrent full-response reuse, owned-copy isolation, cache/profile/source/audit invalidation, serialization outside the execution guard, health freshness and size bounds. `tests/test_source_cache_ownership.py` pauses older provider writes while a newer scan completes. The frontend runtime tests cover row/search cache dependencies, same-profile execution epochs, source synchronization and delayed/failed Chronicle reads. Chromium additionally checks actual download-control focus through ticket reordering, replacement and removal. Rust cancellation tests interrupt import and deep payload reads, reject partial success and reuse the same engine; the native shell tests cover ordered state replies and catalog injection guards.

`native/scripts/windows_smoke.ps1` launches the compiled WebView2 app and reads its import/export/IPC receipt. `native/scripts/mo2_native_smoke.ps1` creates a disposable native MO2 instance with a synthetic BasicGame and test-only tool, never a user's instance; those harness plugins are excluded from user packages. The native fixture installs a generated archive, verifies its bytes and restores the original mod through the production recovery engine. It does not authenticate to Nexus, download a public mod or launch a commercial game.

The 0.12 browser checks in `tests/test_gallery_workspace.py` and `tests/test_compact_completion.py` exercise gallery/list switching, grouping, filters, keyboard completion, saved preferences, source artwork, CSP, theme behavior and restoration of hover transitions after structural changes. `tests/visual_release_qa.py` compares actual screenshots and complete 11,207-record libraries with the recorded baseline; every matching row must remain present and laid out. `native/scripts/benchmark_native_library.ps1` measures the same complete fixture in the actual packaged WebView2 app. Its fixed publication ceilings include 5,000 ms for Gallery and return-to-List; the endpoint must already have removed the structural transition guard and restored the normal hover transition. These are CI fixture limits, not performance promises for other computers.

### Linux CI font evidence

`tests/prepare_ci_fonts.py` installs checksum- and Git-blob-pinned Noto CJK, Runic and Symbols2 fallbacks for observed Linux CI glyph gaps. It requires Linux CI and fontconfig. These fonts are test prerequisites, not bundled application assets. After installing the pinned Playwright dependency and a usable Chromium, the browser job uses one evidence directory for the helper and both actual-font tests:

```shell
# Run inside Linux CI, where CI=true is already set.
export MODHUNTER_REQUIRE_BROWSER=1
export MODHUNTER_QA_OUTPUT=/tmp/modhunter-proof
python tests/prepare_ci_fonts.py --output "$MODHUNTER_QA_OUTPUT/ci-fonts.json"
PYTHONPATH=. python tests/test_watchtower_native_ui.py
PYTHONPATH=. python tests/ci_font_rendering_qa.py
```

The existing four-locale test must prove that Chromium actually used the pinned CJK face for the visible Chinese heading. The separate glyph report checks the MO2 locate symbol, three visible runes and the actual practice-game zoom controls, retaining screenshots and source identities. Release preparation requires both reports, the complete pinned font/license identities, and the screenshot dimensions and PNG checksums. It preserves the original `ci-fonts.json` bytes so both reports' receipt hashes remain verifiable. Linux font evidence does not replace Windows WebView2 or live MO2 acceptance.

### Earlier engine and workflow coverage

The 0.10.0 WatchStore Rust tests cover validated watch/session metadata, operation ownership, baseline/diff/seen history, cancellation/reset, pause/mute and bounded persistence/import. `tests/test_watchtower_browser.py` loads the actual Watchtower HTML/CSS/JavaScript in Chromium with a native-command fixture and records a separate renderer receipt. `native/scripts/watchtower_smoke.ps1` launches the compiled Windows app with its internally owned loopback server and requires actual WebView2 account isolation, remembered-profile close/reopen within one process, generation-selected fresh profiles, authenticated capture/change, selected text, incomplete/stale/canceled rejection, same-session popups and remote IPC denial. Its profile-reopen check does not restart the application. The `public_https_end_to_end` check requires an anonymous `https://example.com/` request through production capture and durable WatchStore baseline completion. The current wrapper includes the later exact-download, privacy and browser-data cases; its required checks and a clean process exit must pass within 240 seconds. Release preparation rejects missing, failed, duplicate or mismatched native Watchtower receipts; real account sign-in is outside this fixture.

`scripts/benchmark_native_import.py` compares complete Rust and previous Node import calls over identical metadata fixtures; all samples and environment details are stored in `native/evidence/import-comparison.json`. Full GUI startup and internet download speeds are outside that benchmark.

The historical 0.9.2 `scripts/benchmark_catalog_delivery.py` comparison measures the complete Python loopback service against published 0.9.1 on generated 2,500- and 10,000-mod catalogs, including full response equivalence and real temporary Chronicle writes. `scripts/benchmark_dashboard_render.py` still runs the actual compiled dashboard in Chromium against that same release baseline and identifies the current candidate commit. `scripts/benchmark_plugin_audit.py` records fresh-hash equivalence and cancellation-checkpoint overhead. See [0.9.2 readiness](../native/evidence/readiness-0.9.2.md) for exact evidence and scope; historical comparison files retain their original baselines rather than being relabeled as 0.10.0 measurements.

`python native/scripts/prepare_release.py --print-version` validates workspace, lockfile, installer and MO2 version agreement. `python native/scripts/prepare_release.py --self-test` also checks stale portable/installer rejection, every embedded file checksum, Windows file/folder aliases and names that cannot extract unchanged, native proof requirements and source/helper identity. `native/scripts/test_plugin_installer.ps1` invokes the shipped installer in Windows PowerShell 5.1 and verifies preserved progress, rejected damaged/incomplete/mixed packages, source-tree installation and backup-directory safety. CI derives its package metadata and release tag from the validated workspace version. Release assets are immutable, so every newly distributed build needs a new version.

## Developer quick start

```shell
python build.py
python -m pytest -q tests/test_core.py tests/test_bridge.py tests/test_gameplay.py tests/test_siege.py tests/test_lore.py tests/test_realm.py tests/test_focus.py
python tests/atlas_qa.py
cd desktop && npm run check
```

The browser tests use Chromium/Playwright. See `tests/` for actual game flows and screenshot checks; they do not substitute for testing native MO2 on Windows.

For the current Rust build and native packaging commands, use [native/README.md](../native/README.md). The `desktop` commands above check the retained legacy companion; Node/Electron is not required to run the Rust release. Run `node --test tests/frontend_runtime.test.cjs` and `python native/scripts/prepare_release.py --self-test` for the frontend ownership contracts and synthetic packaging-rejection tests.

## What the tests cover

- Standalone boot and mobile layouts.
- Actual mod rows, source and compatibility indicators.
- Exact MO2 focus and profile separation.
- Verified bounties cannot be forged or double-claimed.
- Siege auto-combat, upgrades, boss names, Bestiary and pending-update prompt.
- Atlas 12-region / 96-chapter uniqueness, 480 first-clear slots, unlocks, idempotent rewards and save/reload.
- Shiverfell movement, crafting, survival, world-boss quest and roguelite entry.
- AetherFX combat finishers and reduced-motion behavior.

## Remaining environment-specific checks

- The user's populated MO2 installation, installed-mod selection and account-specific native update workflow. The official MO2 2.5.2 disposable fixture covers native plugin installation, Qt dispatch and live focus separately.
- Historical Electron portable `.exe` verification is separate from the current Rust Windows release.
- Real Nexus auth/download workflows on a connected MO2 installation.
- Long-form unique-content quality and 80-hour player study.

## Release layout

The GitHub repository holds current source code as normal text files. The `Rust desktop and MO2 readiness` workflow owns current Windows/MO2 release packaging and publishes only after its native, browser and installer gates pass. Its portable package includes the matching Rust helper required by the plugin's deep audit and source adapters.

The 0.12 packaging layout also derives independent MO2 and standalone ZIPs from the validated portable bytes, with build identity and internal checksums. The public Verification ZIP contains the accepted JSON/PNG evidence, including the font receipts. Check the release page for actual publication; a successful source build or a diagnostic artifact does not imply that the final downloadable release exists.

The separate `Verify Modhunter dashboard and standalone HTML` workflow preserves dashboard, legacy-source and Chromium checks, then uploads a `Modhunter-Standalone-HTML` CI artifact containing the current HTML, commit identity and checksum. It has read-only repository permissions and does not publish legacy Electron/MO2 archives or commit generated packages back to `main`.

Files already under `dist/` and `web/` preserve earlier release outputs. They are not refreshed as substitutes for the tested Rust release. The current compiled standalone dashboard is `mo2_update_arsenal/web/index.html`; see [Standalone HTML](Standalone-HTML.md). The `release_v08.py` script remains available for reproducing the historical package layout, but it does not produce the current Windows/MO2 release.

The separate GitHub Wiki is sourced from `wiki/`. The `sync-wiki.yml` workflow copies Markdown pages after changes merge to `main`; it does not copy screenshot directories. Use published absolute image URLs for the live pages and verify the resulting [GitHub Wiki](https://github.com/Herbertofury/Modhunter/wiki) after synchronization.

[[Architecture]] · [[Frequently Asked Questions]]


## SSS Update Workshop acceptance coverage (November 2026)

- `python3 -m unittest discover -s tests -p 'test_*.py' -q` validates source resolution, per-row Nexus file advisory, risk and target-version confirmation, duplicate guards and native install lifecycle via deterministic fakes.
- `python3 build.py` compiles the exact shared dashboard used in the plugin, Electron and standalone editions.
- `PYTHONPATH=. python3 tests/updater_sss_browser_qa.py` uses headless Chromium with in-memory MO2 API fixtures to exercise full UI flows and capture screenshots. This is a developer-only test tool; the shipped app has no Playwright dependency.
- `node scripts/benchmark_workshop_tickets.cjs` checks semantic equivalence and measures performance of full-list ticket lookup over 2,500 mods and 800 tickets. It is not a hard time threshold.
- **Not yet proven:** native Windows MO2 2.5.3/Qt signal integration, premium/free Nexus behavior, actual FOMOD reinstall on a populated profile, post-install game runtime or dependency correctness. CI checks are not substitutes for these gates.

Official upstream reference: [MO2 Python plugin development](https://www.modorganizer.org/python-plugins-doc/).

## Installation briefing QA and upstream API reference

The `mo2_update_arsenal/install_brief.py` module creates an advisory, non-mutating pre-install snapshot for a single MO2 download. `tests/test_install_brief.py` checks exact archive provenance, authorization, stale profile/game/runtime/installed-version/target-version rejection, new compatibility warnings, and truthful profile-backup scope. The installer dispatch runs the same checks again rather than trusting browser text.

The 2026 SSS interface adds a complete batch review manifest, explicit install briefing with source/file/version details, clickable filter metrics, readiness lane, count of displayed mods, keyboard shortcuts (`Alt+U` and `Alt+R`), and stable responsive ticket cards. `tests/updater_sss_browser_qa.py` exercises approved, canceled and blocked interactions and checks a 390px mobile viewport. It uses deterministic fake MO2 replies and does **not** prove the native Windows API. CI installs a pinned Playwright Chromium to prevent silently shipping broken UI interactions.

Upstream API reference: [Mod Organizer 2 Python plugin documentation](https://www.modorganizer.org/python-plugins-doc/autoapi/mobase/index.html), including `IDownloadManager.startDownloadNexusFile`, `startDownloadURLs`, `downloadPath`, and native download callbacks. MO2 does not expose a documented synchronous `downloadState` through that interface, so ticket states remain native-event-driven and session-scoped.


## No-prompt MO2 quick-update acceptance (2026-11-11)

`tests/test_quick_update.py` exercises exact numeric-version main selection, ambiguous/optional files, GitHub single-archive selection, general advisory notes, DLL/hold review fallback, one-click download→native installer handoff, duplicate-click protection, immediate user-selected risky download, profile/runtime invalidation and a background full-profile queue. The server never treats a browser-provided Nexus file ID as a verified auto match; recommendation and exact provider file verification occur inside the authenticated MO2 bridge.

`tests/updater_sss_browser_qa.py` exercises the actual built HTML in Chromium: one-click per-mod update, automatic native-install intent, direct Install now action, optional Details and full-manifest screens, ambiguous/risky file-picker fallback with no confirmation dialogs, and asynchronous bulk queue progress. This is browser/MO2-contract testing with deterministic native responses, not actual Windows MO2 2.5.x or Nexus authentication validation.
## Measuring a selected MO2 profile

Build the release-mode core CLI and pass the selected profile's actual modlist and instance INI. For split-path installations, use the same paths used by the desktop importer.

~~~text
modhunter-cli benchmark-profile --modlist "<profile>/modlist.txt" --ini "<instance>/ModOrganizer.ini" --mods "<shared mods directory>" --workers 8 --rounds 20
~~~

The command completes one cold import with a new application cache and repeats the entire import with that cache warm. It checks the full catalog and warnings for equality across rounds. The JSON receipt includes every engine completion time, the warm median and nearest-rank p95, throughput, I/O counts and cache hits. The operating system's disk cache remains under normal OS control. Record the binary hash, wall time, process CPU time and peak working set alongside the receipt when comparing builds.

For production acceptance, capture a private baseline with `python scripts/protected_inventory.py capture <new-receipt.sqlite> --full <protected-root> --workers 8`. Repeat `--full` for each separate root. The command records directory entries and hashes every regular file. Place the receipt outside all protected roots. A second capture can be compared using `python scripts/protected_inventory.py compare <before.sqlite> <after.sqlite>`. Exit 0 means equality within the recorded scope, exit 1 means differences, and exit 2 signals an error. Keep inventories and paths local.

### Complete-library import measurement, November 24, 2026

The release-mode Windows core imported 11,325 real mods and 834 groups with eight workers. Each catalog contained 10,352 plugin copies and 3,629 winning plugins, with zero import warnings. Moving the completed JSON arrays into the catalog avoided serializing them into a second in-memory tree. An independent comparison of all returned catalog fields and warnings, excluding timestamps and instrumentation, produced the same SHA-256 across the two binaries.

Four processes ran in reference, candidate, candidate, reference order. Each process completed one cold application-cache import and 12 warm imports. The OS managed its filesystem cache and a concurrent SHA-256 inventory used the same disk. The 24 warm samples per build gave a median of 2,706.7 ms before and 1,635.1 ms after, with nearest-rank p95 of 4,845.4 ms and 2,002.9 ms respectively. Peak benchmark process working set ranged from 1,236.7 to 1,240.9 MB before and from 903.9 to 904.6 MB after. These peaks include the reference catalog retained for equivalence checking.

The two cold imports took 26.69 and 1.99 seconds on the reference build and 1.88 and 1.46 seconds on the candidate. The large first-run variation makes a cold-start guarantee inappropriate. Aggregate process CPU time over the 26 imports was 108.55 seconds for the reference and 78.72 seconds for the candidate. Both binaries read and validated the complete workload. Re-run the full equivalence and resource checks when the importer's output grows.

The [benchmark receipt](https://github.com/Herbertofury/Modhunter/blob/main/docs/benchmarks/real-profile-import-20261124.json) contains the binary hashes, all 48 warm engine times, throughput, completed process durations, CPU samples and peak working sets.
