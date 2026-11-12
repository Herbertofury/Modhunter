# 🛠 Development & Testing

## Rust 0.9 release gates

The [native workflow](https://github.com/Herbertofury/Modhunter/actions/workflows/native-rust.yml) compiles and launches the actual Windows app, tests the production plugin inside an official MO2 2.5.2 fixture instance, runs Rust tests and strict Clippy, and executes browser and lifecycle regressions. See [Rust Desktop](Rust-Desktop.md) for the evidence boundaries and [native/README.md](../native/README.md) for build commands. Downloadable receipts identify each passing check and the compiled commit. The older acceptance notes below describe their original checkpoints.

`tests/test_scan_lifecycle.py` covers stale completions, errors and progress, including A→B→A; `tests/test_source_identity.py` covers source authority and route-preserving URL cleanup. `tests/frontend_runtime.test.cjs` checks polling, stale reads, cosmetic rendering and source identity. `tests/test_frontend_responsiveness.py` runs the compiled dashboard in actual Chromium. CI requires the browser with `MODHUNTER_REQUIRE_BROWSER=1`, so a missing browser cannot silently pass that gate.

`native/scripts/windows_smoke.ps1` launches the compiled WebView2 app and reads its import/export/IPC receipt. `native/scripts/mo2_native_smoke.ps1` creates a disposable native MO2 instance with a synthetic BasicGame and test-only tool, never a user's instance; those harness plugins are excluded from user packages. Fixture tests do not authenticate to Nexus, install real mod archives or launch a commercial game.

`scripts/benchmark_native_import.py` compares complete Rust and previous Node import calls over identical metadata fixtures; all samples and environment details are stored in `native/evidence/import-comparison.json`. Full GUI startup and internet download speeds are outside that benchmark.

## Developer quick start

```shell
python build.py
python -m pytest -q tests/test_core.py tests/test_bridge.py tests/test_gameplay.py tests/test_siege.py tests/test_lore.py tests/test_realm.py tests/test_focus.py
python tests/atlas_qa.py
cd desktop && npm run check
```

The browser tests use Chromium/Playwright. See `tests/` for actual game flows and screenshot checks; they do not substitute for testing native MO2 on Windows.

## What the tests cover

- Standalone boot and mobile layouts.
- Actual mod rows, source and compatibility indicators.
- Exact MO2 focus and profile separation.
- Verified bounties cannot be forged or double-claimed.
- Siege auto-combat, upgrades, boss names, Bestiary and pending-update prompt.
- Atlas 12-region / 96-chapter uniqueness, 480 first-clear slots, unlocks, idempotent rewards and save/reload.
- Shiverfell movement, crafting, survival, world-boss quest and roguelite entry.
- AetherFX combat finishers and reduced-motion behavior.

## Known unverified gates

- Native Windows MO2 plugin installation and Qt live-focus testing.
- Compiled Windows Electron portable `.exe` verification (source build is available).
- Real Nexus auth/download workflows on a connected MO2 installation.
- Long-form unique-content quality and 80-hour player study.

## Release layout

The GitHub repository holds source code as normal text files and the complete release under `dist/`. The separate GitHub Wiki is sourced from `wiki/` and can be synchronized after its first Home page is initialized. The intended GitHub Wiki URL is [`Herbertofury/Modhunter/wiki`](https://github.com/Herbertofury/Modhunter/wiki).

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
