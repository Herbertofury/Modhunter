# WhimWatch reference audit and Modhunter 0.10.0 acceptance

## Exact reference and inspection scope

The reference is **WhimWatch 0.4.2**, a Sims 4/WickedWhims desktop updater. Its account, page-management, recovery and privacy behavior informed an independent Modhunter Watchtower implementation. WhimWatch source, UI bundles, fonts and other assets are not included in Modhunter.

| Identity | Verified value |
| --- | --- |
| Uploaded portable asset | `WhimWatch-0.4.2-portable.exe` |
| Bytes | 113,646,560 |
| SHA-256 | `6464d807873c9c536a8613ce7b01e68279bfacaee156ce7cdff494ebf5ae6ad1` |
| Official release | [forthewhimsy/whimwatch v0.4.2](https://github.com/forthewhimsy/whimwatch/releases/tag/v0.4.2) |
| Exact source commit | [`7f782633aa4d07b2466f80c9b57cd62ee8ec51f2`](https://github.com/forthewhimsy/whimwatch/tree/7f782633aa4d07b2466f80c9b57cd62ee8ec51f2) |
| License | [MIT, copyright 2026 WhimWatch contributors](https://github.com/forthewhimsy/whimwatch/blob/7f782633aa4d07b2466f80c9b57cd62ee8ec51f2/LICENSE) |
| Inspection | Static executable extraction, compiled main/preload/renderer inspection, exact-tag first-party source and test inventory |
| First-party source inventory | 96 `src` files, 22,020 lines, 939,725 bytes |
| ASAR inventory | 673 entries; 22 entries outside `node_modules`, including app bundles, fonts, licenses and package metadata |
| Test inventory | 23 files with 313 statically declared `it`/`test` calls; these tests were not executed |

The official release API reports the same portable filename, size and SHA-256 as the upload. The embedded archive was extracted without executing the application. ASAR paths and available SHA-256 integrity records were checked before extraction. The exact public tag supplies the original module boundaries for the compiled code audit. Electron, Chromium and dependency internals were inventoried as packaged dependencies rather than counted as first-party application features.

**This is static reference inspection.** WhimWatch was not launched, no reference account was signed in, no user cookies or passwords were read, and no personal mod installation was scanned. Modhunter's browser and Windows fixtures are separate acceptance evidence; they do not turn this audit into a WhimWatch runtime test or a real website login test.

## Transfer decisions

Reference paths below are relative to the pinned source commit. Modhunter implementation lives in [`core/watchtower`](../crates/modhunter-core/src/watchtower/), the desktop Watchtower modules and [`ui`](../ui/). The [Watchtower guide](../../wiki/Watchtower.md) documents the user workflow. This matrix distinguishes the delivered manager, existing MO2 ownership and features intentionally outside this release.

| Reference capability and evidence | Modhunter treatment |
| --- | --- |
| Dedicated account rows, native site login and sign-out: `main/auth.ts`, `renderer/src/SettingsView.tsx` | Watchtower has named site sessions and native sign-in windows. Site credentials remain inside WebView2 profiles; saved metadata contains labels, origins and lifecycle state only. Nexus authentication stays with MO2. |
| One browser partition per site, optional memory-only use, clearing sign-ins: `main/browser.ts`, `session-mode.ts`, `privacy.ts` | Isolated native sessions separate account data from the privileged local manager. Remembered sessions and temporary sessions have explicit ownership; reset advances the session generation so old work cannot republish authenticated observations. |
| Legitimate same-session login popups: `main/auth.ts` | Native popup handling retains the selected session while remote website content remains unable to invoke local manager, filesystem or MO2 commands. |
| Known OAuth refusal explanation and site-cookie recognition: `main/sign-in-provider.ts`, `auth.ts` | A generic browser cannot prove account entitlement. Modhunter distinguishes opening/confirming a session from observed page access. Provider challenges and blocked embedded sign-in remain visible user actions; universal provider compatibility is not claimed. |
| Add a page, normalize identity, immediate targeted read, preserve newer additions: `core/sources/urls.ts`, `core/link-prefs.ts`, `main/controller.ts` | Validated HTTPS page tracking, canonical deduplication, a first-observation baseline, targeted checking and operation identities. Path/query case is retained; exact origins reject lookalike domains. |
| Hidden/removed page history and Show again: `core/link-prefs.ts`, `main/controller.ts` | Removed watches remain recoverable. Pause, folders, tags and search organize the complete watchlist. |
| Per-page Mark seen, history and guarded Undo: `core/compare.ts`, `main/controller.ts`, `renderer/src/HistoryView.tsx` | Acknowledgment is tied to a concrete observation identity. History and undo cannot erase a newer acknowledgment or turn a newly observed change into an old one. |
| Verification holds, actionable Sign in / Verify / Check again: `main/verification.ts`, `site-access.ts`, `renderer/src/eligibility.ts` | Page failures, login/verification barriers and incomplete observations preserve last-good content. Human interaction happens in the site's own session window; an access failure is not recorded as an update. |
| Site queues, cancellation, in-flight mute changes, incremental last-good results: `core/fetcher.ts`, `core/check.ts`, `main/controller.ts` | Bounded native work, exact job/session generations, cancellation, pause/mute controls and incremental watch status. A late response cannot replace an edited, removed, canceled or reset watch. |
| Manual default and optional launch check: `main/controller.ts`, `renderer/src/SettingsView.tsx` | Manual checking is the default. Optional launch/due checking is explicitly enabled. Any interval checking occurs while the application runs; it is not an always-on OS service. |
| Discreet notifications, names hidden by default: `main/controller.ts` | Optional notification and title/privacy controls remain user choices. Page text, notes and URLs can be sensitive local data; credential values do not enter the watch store or export. |
| Search, filters, row disclosure, keyboard use, loading/error recovery: renderer modules | A dedicated native manager keeps watch status, page details, history, accounts and recovery controls accessible alongside the existing updater. Real Chromium tests exercise this presentation; native tests separately exercise WebView2 session behavior. |
| Atomic state, bounded logs, corruption recovery: `core/store.ts`, `main/log.ts` | Versioned atomic Rust state with explicit recovery errors and no silent destructive reset. Export/import validates bounded watch data and excludes browser credentials and session persistence. |
| Newest accessible source selection: `core/source-choice.ts`, `shared/updatable.ts` | Existing Modhunter source adapters retain exact project/release/file fingerprints and compatibility evidence. Date-only Sims comparisons do not replace these stronger identities. |
| Per-file review, sequential updates, failed-item recovery: `core/batch.ts`, `main/updater.ts`, update dialogs | Existing Update Workshop and MO2 remain authoritative for downloads, FOMOD prompts, replacement decisions, retries and native completion. A watched page changing is an observation, not authorization to install a linked archive. |
| Hash equivalence, direct file backup/restore, duplicate removal: `core/installer.ts`, `backups.ts`, `duplicates.ts` | Modhunter retains fresh plugin hashes and its existing MO2 lifecycle guards. Arbitrary MO2/FOMOD rollback and Sims duplicate-file deletion are not implemented by copying the reference's direct file installer. |
| Sims DBPF discovery, creator matching, game flags, process checks: `core/dbpf.ts`, `scanner.ts`, `creators.ts`, `game.ts`, `process.ts` | Game-specific features are not ported. Rust MO2 profile metadata, winning plugins, ESL/master analysis and existing native game context remain authoritative. |
| Archive rules accepting `.package` / `.ts4script`, rejecting executable payloads: `core/archive.ts` | MO2 owns supported archive/FOMOD behavior. Sims rules are unsuitable for legitimate Skyrim DLL mods and are not applied to them. |
| App release discovery: `core/app-update.ts` | Existing immutable Modhunter releases and exact-build verification remain. A WhimWatch-style automatic binary installer is not implied; the reference exposes a version check and release link. |
| System/light/dark, four UI languages, first-run privacy presets: renderer and localization modules | Existing Modhunter presentation is retained. Complete WhimWatch localization, privacy presets, backup-usage dashboards and setup screens are not claimed as transferred features. |

### Modhunter extensions beyond the reference

WhimWatch 0.4.2 accepts specific wicked.cc, LoversLab file and Patreon creator pages; it is not an arbitrary website monitor. It has no watchlist import/export UI, no multi-account-per-site selector, no recurring background polling service and no Nexus/MO2 integration. Modhunter's generic page watches, named sessions, folders/tags, optional due scheduling, credential-free interchange and MO2 associations are Modhunter additions rather than claims about reference functionality.

The reference does not provide a password vault or universal authentication-success API. A valid browser session and a successful rendered observation do not establish a paid subscription, download permission, mod compatibility or installation success.

## Required acceptance for this release

The [Rust desktop and MO2 readiness workflow](https://github.com/Herbertofury/Modhunter/actions/workflows/native-rust.yml) retains all 0.9.2 gates and adds a required native Watchtower run. A workflow definition is a contract; the passing run and its matching release receipts identify the actual accepted build.

| Layer | Required evidence and meaning |
| --- | --- |
| Rust WatchStore | URL/origin validation, first baseline and changed content, stable acknowledgment/undo, remove/restore, pause/mute, session reset invalidation, canceled/stale work, bounded imports/exports and persistence/recovery tests. |
| Chromium manager | Ten required actual Chromium cases load the shipped manager with a fixture native-command adapter. The receipt binds the build commit and SHA-256 of the three UI files plus its two harness files, requires a clean source state and four valid screenshots. It covers forms, account selection, old preview/state rejection, draft retention, exact observation acknowledgments, privacy, recovery and all 2,500 rows including one batch pause invocation. This proves renderer behavior, not cookie isolation or real sign-in. |
| Native Windows Watchtower | Thirteen required checks in [`watchtower_smoke.ps1`](../scripts/watchtower_smoke.ps1) launch the compiled app. Internally owned loopback pages exercise trusted manager IPC, denied remote IPC, account isolation, reuse of a remembered profile after closing/reopening views within one process, a fresh profile selected by a new storage generation, authenticated capture/change, selected text, oversized capture rejection, stale/canceled work and same-session popup behavior. The reopen check does not restart the app. Local durable-job checks use a separate public-shaped URL without network access or relabeling captured source URLs. The additional `public_https_end_to_end` check makes an anonymous request to `https://example.com/` through the production WebView2 capture and WatchStore completion path and requires a real durable baseline/hash. |
| Existing native Windows | Fixture MO2-profile import, real dashboard rows, actual backup export/readback and rejection of toolbar IPC from the game view remain mandatory. |
| Existing official MO2 2.5.2 | Production plugin, actual QObject/Qt sender binding, protected live loopback API, native selection, Rust helper audit and exact source/helper identity remain mandatory. No live Nexus account is used. |
| Package/release | Every portable file remains checksummed. Source versions, commit/run, packaged MO2 code and both embedded native receipts must match the tested artifacts. A missing/false/duplicate/incomplete Watchtower receipt cannot publish. Only bounded allowlisted JSON/PNG proof is exposed. |

The public verification ZIP includes `windows/watchtower-smoke.json` and `browser/watchtower-browser.json`; the portable ZIP includes the same native receipt as `Evidence/watchtower-smoke.json`. Browser profiles, cookies, settings, private page observations and diagnostic logs are not public release proof. The existing five historical benchmark JSON files retain their original filenames, baselines and measured scope; no 0.9.2 latency measurement is relabeled as a new 0.10.0 measurement.

Remaining real-environment acceptance includes each provider's login/OAuth/2FA/challenge behavior, permission-specific content, long-running user sessions, real Nexus downloads and FOMOD choices. Neither these fixtures nor the static reference audit guarantee every website or personal MO2 installation has zero issues.

## Complete first-party source inventory

All 96 source paths were included. Paths in the transfer matrix are shortened by omitting the `src/` prefix. The inventory includes styles, declaration files and localization, not just code that appears relevant to account management.

### Core: 31 files

```text
src/core/app-update.ts
src/core/archive.ts
src/core/backups.ts
src/core/batch.ts
src/core/browsers.ts
src/core/check.ts
src/core/compare.ts
src/core/creators.ts
src/core/dbpf.ts
src/core/downloads.ts
src/core/duplicates.ts
src/core/fetcher.ts
src/core/game.ts
src/core/hash.ts
src/core/installer.ts
src/core/link-prefs.ts
src/core/overrides.ts
src/core/ownership.ts
src/core/pack-files.ts
src/core/process.ts
src/core/report.ts
src/core/scanner.ts
src/core/source-choice.ts
src/core/sources/html.ts
src/core/sources/loverslab.ts
src/core/sources/patreon.ts
src/core/sources/types.ts
src/core/sources/urls.ts
src/core/sources/wickedcc.ts
src/core/sources/wwmod.ts
src/core/store.ts
```

### Main process: 18 files

```text
src/main/auth.ts
src/main/browser.ts
src/main/browsers.ts
src/main/controller.ts
src/main/downloads.ts
src/main/index.ts
src/main/ipc.ts
src/main/log.ts
src/main/modsdir.ts
src/main/open.ts
src/main/privacy.ts
src/main/request-filter.ts
src/main/session-mode.ts
src/main/sign-in-provider.ts
src/main/site-access.ts
src/main/smoke.ts
src/main/updater.ts
src/main/verification.ts
```

### Preload: 2 files

```text
src/preload/api.d.ts
src/preload/index.ts
```

### Renderer: 27 files

```text
src/renderer/index.html
src/renderer/src/App.tsx
src/renderer/src/CreatorList.tsx
src/renderer/src/DuplicatesDialog.tsx
src/renderer/src/Feedback.tsx
src/renderer/src/Header.tsx
src/renderer/src/HistoryView.tsx
src/renderer/src/Home.tsx
src/renderer/src/OtherFiles.tsx
src/renderer/src/PlayCard.tsx
src/renderer/src/SettingsView.tsx
src/renderer/src/Setup.tsx
src/renderer/src/UpdateAllDialog.tsx
src/renderer/src/UpdateDialog.tsx
src/renderer/src/dialog.tsx
src/renderer/src/eligibility.ts
src/renderer/src/env.d.ts
src/renderer/src/format.ts
src/renderer/src/health.ts
src/renderer/src/history.ts
src/renderer/src/main.tsx
src/renderer/src/rich.tsx
src/renderer/src/styles.css
src/renderer/src/toast.tsx
src/renderer/src/ui.tsx
src/renderer/src/useApp.ts
src/renderer/src/useSiteToggle.ts
```

### Shared contracts: 11 files

```text
src/shared/api.ts
src/shared/config.ts
src/shared/dates.ts
src/shared/game.ts
src/shared/labels.ts
src/shared/muted.ts
src/shared/privacy.ts
src/shared/problems.ts
src/shared/types.ts
src/shared/updatable.ts
src/shared/version.ts
```

### Localization: 7 files

```text
src/shared/i18n/catalogs/en.ts
src/shared/i18n/catalogs/es.ts
src/shared/i18n/catalogs/it.ts
src/shared/i18n/catalogs/zh-TW.ts
src/shared/i18n/format.ts
src/shared/i18n/index.ts
src/shared/i18n/locales.ts
```

## Packaged application entries outside node_modules

These 22 entries were inventoried in the uploaded ASAR. Fonts and third-party notices are listed for completeness; they are not claimed to be original WhimWatch code or reused Modhunter assets. Hashes identify the extracted reference bytes.

| Packaged entry | Bytes | SHA-256 |
| --- | ---: | --- |
| `out/main/chunks/report-Cu-3xaNO.js` | 1,535 | `af9e8c321ba072c9fb5914bd1f05d799ddc84c06652d3462b18b732ae9cd5a1d` |
| `out/main/chunks/smoke-GhJ6fsew.js` | 3,580 | `500e281df77a1681eb29afa8d33baae2e889c713ad99e718bd74daeeb279839e` |
| `out/main/index.js` | 482,059 | `8b6e188f607be299dc741ea8a1cd91d1005a0a800d125f073fcbc0a48dfd9537` |
| `out/preload/index.cjs` | 1,603 | `43ed3a799c6a748a10b9c1c1ea22e276dc8a1e3aa4b40ba43d1a19ea222d5ebb` |
| `out/renderer/assets/index-6hNS0BcE.js` | 1,112,947 | `6072df976bb3a20a11d4c0f4c8899e47aafe0a510f6ff3bcf4a814052ec11e01` |
| `out/renderer/assets/index-BeSAKBAq.css` | 52,457 | `949c9f961dfa4cf98899d562336308b603a7063edc8a7500cd69d2357f759592` |
| `out/renderer/assets/jetbrains-mono-cyrillic-ext-wght-normal-EocZY2iu.woff2` | 2,028 | `593ccd6fe36e299e151aeae4521be9bfe83ac487d96d7cdb44f50be4a04041c7` |
| `out/renderer/assets/jetbrains-mono-cyrillic-wght-normal-D73BlboJ.woff2` | 12,108 | `d274604c40757f98f07df623a8b8fb5194508a6fa4f5413a7a107f09e3b7452a` |
| `out/renderer/assets/jetbrains-mono-greek-wght-normal-Bw9x6K1M.woff2` | 9,004 | `ee863198077f3093ffa81c072581cc4bd608a04e60a8a9c0b846396ce4f14109` |
| `out/renderer/assets/jetbrains-mono-latin-ext-wght-normal-DBQx-q_a.woff2` | 15,196 | `79bfdab9ba467e26eea4122e6f2567e188dd8a09a8c730d501fc487c4ab99c6e` |
| `out/renderer/assets/jetbrains-mono-latin-wght-normal-B9CIFXIH.woff2` | 40,404 | `18be452724bfdc236c074ca94a249a7f41a86752c7d04ab258ce9ed5651f6a7e` |
| `out/renderer/assets/jetbrains-mono-vietnamese-wght-normal-Bt-aOZkq.woff2` | 7,504 | `2288795da89dd97e648954b96e22b4501bd85dfc453264460b252f888b582ca2` |
| `out/renderer/assets/manrope-cyrillic-ext-wght-normal-C8S-KRRz.woff2` | 2,552 | `de37de877dc17e4577341fa68bb5cb526b53d54cb29721e674208546a3c7849d` |
| `out/renderer/assets/manrope-cyrillic-wght-normal-Dvxsihut.woff2` | 14,500 | `c268b459a9329e59fecf39a17618efd44c71735532048d60b12aab76a8c14914` |
| `out/renderer/assets/manrope-greek-wght-normal-DL7QRZyv.woff2` | 9,444 | `2fb2e8cbfd52ae46179a8b6024eb162f1f9c6de3d22d6e3a30d8f395de7220be` |
| `out/renderer/assets/manrope-latin-ext-wght-normal-Ch3YOpNY.woff2` | 15,120 | `3911b66d9f2e005a4b989223405d0e5032619c668597ba467cc76a23c8fffcfb` |
| `out/renderer/assets/manrope-latin-wght-normal-DHIcAJRg.woff2` | 24,836 | `a30ddcd349703aff7464c34bef3fffdff405ee50c113440d7c8693c02d210972` |
| `out/renderer/assets/manrope-vietnamese-wght-normal-usUDDRr7.woff2` | 8,520 | `6bbb044ab420e07edb0a3042d2eb314b85e83a0182e945a15ab3b9092668dfd5` |
| `out/renderer/index.html` | 723 | `e87d95c94735fedd5720b6894ad212fed771733076c2b8b3c0cca07f43845aec` |
| `out/renderer/licenses/LICENSE.txt` | 1,079 | `00f0afbea96396d8a3865df74a337f75cbe8b466ca7b181c3a1dd658559dda9f` |
| `out/renderer/licenses/THIRD_PARTY_LICENSES.txt` | 76,344 | `ec6208945d70cb599cfbcc8e88bdf5988844159026a0018c0aa9328e8b575102` |
| `package.json` | 730 | `fea4962c6402b31f67d1a31ad2ce4dd00be5d7dad185521fbca46ba05ef4efe0` |
