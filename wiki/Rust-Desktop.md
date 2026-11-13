# Rust Desktop 0.10.0

[Home](Home.md) / [Windows releases](https://github.com/Herbertofury/Modhunter/releases) / [Native build results](https://github.com/Herbertofury/Modhunter/actions/workflows/native-rust.yml)

## What changed

Modhunter now has a compiled Rust Windows desktop. Rust owns the window and tab lifecycle, file dialogs, background profile import, cached metadata parsing, local preferences, MO2 discovery, connection health, export destinations and plugin record/hash analysis. WebView2 renders the shared updater and all existing game modes. The shipped app does not run Electron or require Node.

The presentation remains HTML/CSS/JavaScript. The Python/Qt plugin remains inside MO2 because it owns the organizer, authenticated Nexus interface, native download callbacks and interactive installer. This is a Rust desktop and engine with the existing supported MO2 adapter.

## Install and connect

1. Download **Modhunter-Rust-Windows.zip** from the linked release and extract it. Run **Modhunter.exe**. The NSIS installer is another option and bootstraps Microsoft Edge WebView2 when needed.
2. For local inspection, click **Import MO2** and select the profile's `modlist.txt`. Portable instances are inferred. For separate configuration/data directories, first select **Instance INI**; **Mods path** provides an explicit override. **Auto paths** clears those overrides.
3. Click **Refresh** after changing files. The toolbar reports measured import duration, metadata cache hits and warnings. **CANCEL** interrupts a native import/refresh or plugin audit; **CANCELING…** remains visible until its workers settle. The last complete catalog stays available and another refresh/audit can then run. Importing never changes the MO2 profile or installed mod files.
4. To enable live updating, close MO2 and run **Install_MO2_Plugin.bat** from the portable package. Select the directory containing `ModOrganizer.exe`. The previous plugin is preserved in `ModhunterBackups`, outside MO2's plugin directory, and a failed replacement is rolled back.
5. Start MO2 and open **Tools → Update Arsenal – Quest Board**. In the Rust app, click **Find MO2**. A single validated session connects; multiple sessions are offered for selection. Manual loopback URLs still work.
6. Use the existing **Update now**, **Choose file**, background queue and **Install all ready** controls. MO2 supplies Nexus sign-in and its real FOMOD/replace dialogs. Network and disk analysis run in background workers. Native installer calls cross MO2's Qt queue; the dashboard stays responsive while MO2 owns its installer prompts.

The MO2 plugin and Rust executable should come from the same release. The plugin installer validates the released package’s SHA-256 manifest and exact build/plugin version before replacing anything; keep `SHA256SUMS.txt`, `BUILD_INFO.json` and the `MO2` directory together when extracting the ZIP. An incomplete or mixed package leaves the active plugin and its progress untouched. Old plugins without protocol-1 health support receive an upgrade error. Session descriptors contain the local address/profile identity and process ID, never an API key or HTTP session secret. Discovery validates every candidate with the actual running service.

## Watchtower pages and accounts

The native **Watchtower** manager tracks release pages, creator pages and changelogs with separate named site sessions. Add a page, select its session when needed, and establish a first observation. Later changes retain text/history and stay unread until marked seen. Search, folders, tags, site filters, pause/mute, recoverable removal, guarded acknowledgment undo and import previews organize the list. Optional launch/background checks run while the app is open; manual checking is the default.

Passwords stay in the website's own sign-in flow. Remembered WebView2 profiles persist across launches; non-remembered profiles are temporary on-disk profiles scoped to the app run, with a fresh profile on the next launch. Reset closes the session windows and invalidates pending work. Confirming a session is user-supplied readiness, not proof of a paid subscription or guaranteed page access. MO2 continues to own Nexus authentication, downloads and FOMOD installation.

See [Watchtower](Watchtower.md) for controls, privacy and troubleshooting, and the [WhimWatch 0.4.2 audit](../native/evidence/whimwatch-parity-0.10.0.md) for the exact reference, independent adaptation and excluded Sims-specific behavior.

## Plugin and source intelligence

Use **Plugin audit** in the native toolbar for an imported offline profile, or **Analyze plugins** in the live dashboard. **ESLIFIER DATA** connects the converter's real data folder for offline checks; live MO2 recognizes ESLifier's official setting or offers **Connect ESLifier records** in the inspector. The matching MO2 package includes `bin/modhunter-cli.exe`; no Node process is needed for plugin analysis.

[Per-mod intelligence](Mod-Intelligence.md) documents winning-copy state, conditional ESL candidates, stale conversion hashes, master/order issues, source availability and public bug-tab observations. Compact row summaries are supplemented by exact-row details on demand. The analyzer is read-only.

## Progress and exports

Read-only profile identifiers preserve the old Windows import identity scheme, so compatible exported progress still maps to the same profile and rows. WebView2 has its own browser storage; it does not automatically read Electron's private storage database. Export your backup in the earlier edition and import it in the new dashboard when moving runtimes. The live MO2 Chronicle remains plugin-owned.

JSON backups, CSV reports and PowerShell exports save to Windows **Downloads** with unique filenames. The toolbar shows the full saved path. Downloads are limited to exports created by the current Modhunter page; ordinary source links open in the system browser.

## Responsiveness and update reliability

- Import workers are bounded and reused. Each import verifies bounded fresh `meta.ini` and TES4 header bytes before reusing cached parsing; root plugin membership and DLL locations are checked again. Same-size changes with preserved timestamps cannot retain old installed versions, source URLs, masters or ESL flags. The engine reads metadata rather than traversing entire mod asset trees.
- MO2 connection checks use a small `/api/health` response, bounded deadlines and a reused HTTP client. A failed health check marks the connection unavailable; read-only recovery reloads the current session without replaying update commands.
- Large-list search is coalesced to animation frames. Cosmetic updates avoid rebuilding mod rows. Concurrent reads are shared, progress writes are serialized, and scan/queue polls do not pile up behind slow replies.
- In 0.9.2, unchanged rows reuse their complete markup as well as their DOM nodes; catalog/source/analysis changes and freshness transitions invalidate both displayed and searchable status. Download controls retain keyboard focus through ticket moves and updates. The complete list remains available.
- Repeated MO2 snapshot requests reuse one immutable encoded response for the same observed state, up to 32 MiB, with bounded health freshness. Encoding happens outside the execution guard; ordinary progress saves avoid catalog decoration. Profile, source, cache and audit changes invalidate cached delivery.
- Profile changes carry epochs through browser reads, background scans and queued commands. Results from profile A cannot replace profile B, including A→B→A switches. A command that times out before execution is canceled; an operation already executing retains its duplicate guard until the native result arrives.
- Source-link edits also advance the execution context. Update commands wait for a pending edit to synchronize; clearing the override restores MO2's original identity. Delayed Chronicle reads preserve edits made while loading, and failed reads retry without replacing unseen progress.
- Metadata snapshots and exact-row indexes avoid copying an entire catalog for one download. Shared Nexus lookups and correct callback matching prevent duplicate work and stale response reuse.
- Source links preserve meaningful forum query routes. Notes-only URLs remain advisory and cannot displace authoritative Nexus identity. Unknown games cannot silently become Skyrim Nexus URLs.

See the [0.9.2 readiness evidence](../native/evidence/readiness-0.9.2.md) for measured HTTP delivery, progress saves, browser changes and cancellation equivalence against published 0.9.1.

## Historical import performance

Five-sample medians on identical generated Linux metadata fixtures, with 8 Rust workers and the previous Node importer's 24 workers:

| Import measurement | 2,500 mods: previous Node | 2,500 mods: Rust | 10,000 mods: previous Node | 10,000 mods: Rust |
|---|---:|---:|---:|---:|
| Fresh importer, full engine call | 477.03 ms | 70.78 ms | 1,629.28 ms | 330.42 ms |
| Worker launch, import, serialize and exit | 581.38 ms | 119.26 ms | 1,781.75 ms | 512.82 ms |
| Repeated import, persistent process | 323.25 ms | 45.36 ms | 1,870.32 ms | 239.74 ms |

Fresh import-engine medians improved **6.74×** and **4.93×**, respectively. These measurements include fresh metadata-content verification and root plugin inventory. Cache hits mean verified unchanged content or membership with parsed results reused; they do not mean the filesystem was skipped. The generated fixtures contain no ESP payloads, so deep-audit speed, Windows GUI startup, real MO2/Nexus operations and download speed are not measured. OS filesystem caches were not forcibly evicted; “fresh” means an empty application cache. The comparison validates common metadata field by field and separately verifies Rust's corrected MO2 priority order. [Raw measurements, all samples, environment and source hashes](../native/evidence/import-comparison.json); [reproduction script](../scripts/benchmark_native_import.py).

The [earlier metadata-only comparison](../native/evidence/import-comparison-metadata-only.json), [pre-freshness comparison](../native/evidence/import-comparison-before-freshness.json) and [first QoL local receipt](../native/evidence/qol-local-verification.md) remain historical evidence. Current [live public API observations](../native/evidence/source-adapter-live.json) and [Python-to-Rust source protocol proof](../native/evidence/source-bridge-protocol.json) bind their tested source and helper hashes. Consult the workflow below for the latest Windows/MO2 commit and its acceptance receipts.

## Release verification

The [Rust desktop and MO2 readiness workflow](https://github.com/Herbertofury/Modhunter/actions/workflows/native-rust.yml) runs separate gates:

| Gate | What it demonstrates |
|---|---|
| Rust format, tests and Clippy | Parser/cache correctness, explicit cancellation and retry, shared catalog encoding, split paths and encodings, bridge validation, discovery and navigation/export policies |
| Python and Node regressions | MO2 command/download/install lifecycle, response-cache invalidation, scan/source epochs, delayed Chronicle hydration, frontend polling/rendering/focus and preserved legacy importer |
| Actual Chromium | Built dashboard interactions, update actions, search responsiveness, profile changes, connection recovery and existing update workshop flows |
| Actual Chromium Watchtower | Shipped manager forms, session actions, watch/history controls, import preview, error recovery and responsive layout with a fixture native-command adapter |
| Actual Windows WebView2 | Compiled application launch, native toolbar IPC, three-mod profile import into the real dashboard, actual JSON backup download and rejection of native IPC from game content |
| Actual Windows Watchtower | Account isolation, remembered-profile close/reopen within one process, generation-selected fresh profiles, same-session popups, authenticated page capture/change, selected-region capture, incomplete/stale/canceled rejection and denied website IPC against internally owned loopback pages; an anonymous `https://example.com/` production check must also complete a durable baseline. No application restart is performed |
| Actual MO2 2.5.2 fixture | Production plugin loading in official Windows MO2, actual C++ QObject/raw QVariant binding with synthetic callbacks, real loopback API and Qt focus, and a completed Rust plugin/hash audit of a disposable fixture |
| Windows PowerShell 5.1 installer | Fresh install, upgrade/progress preservation, rejected incomplete/damaged/mixed packages, source-tree installation and linked-directory handling |

CI uploads JSON receipts and browser proof alongside the portable app and NSIS installer. Inspect the linked run for the exact commit and outcome; a workflow definition is not proof that a particular run passed.

The additional Watchtower receipt is required before packaging and must exactly match the copy inside the portable ZIP before publication. Browser/session data and diagnostic logs stay out of the public verification archive. The native fixture uses owned local pages and synthetic sign-in state, not a live website account; the reference WhimWatch audit was static and did not execute its uploaded EXE.

The release version comes from the Rust workspace and is checked against the Tauri installer, Cargo lockfile and packaged MO2 plugin before building. The portable build identity and release tag use that validated value. Release preparation rejects a stale-version installer or portable ZIP and verifies that the plugin's version module and rebuilt dashboard match the source tested inside native MO2. Published releases are immutable; new code requires a new version to become a new download.

The MO2 fixture has no Nexus credentials, commercial game assets or actual mod downloads. Real free/premium account behavior, provider availability, native FOMOD choices, long-running sessions and the user's in-game result still require that environment. The executable is not code-signed. No universal zero-defect or mod-compatibility claim is made.

## Build

See [native/README.md](../native/README.md) for pinned-toolchain build commands and CLI examples. The previous Electron source and historical archives remain available for preservation and comparison.
