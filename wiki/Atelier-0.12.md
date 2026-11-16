# Atelier 0.12 / The Mod Vault

[Home](Home) · [Design standard](Design-System-and-Quality-Bar) · [Rust desktop](Rust-Desktop) · [Update Workshop](Update-Workshop) · [Watchtower](Watchtower)

**Atelier 0.12 is the implemented visual update for Modhunter's Rust desktop and shared mod dashboard.** Candidate 6 completed native and browser acceptance and a full release-packaging dry run. Publication remains blocked by slow view changes in the complete 11,207-mod library; the measured checkpoint and remaining release steps are recorded below.

## Your library, with more control over the view

The left navigation opens **Library**, **Updates**, **Downloads**, **History**, **Tools** and **Adventures**. Expand the rail when you want labels, or keep it compact to make more room for your collection. **Collections** opens the existing searchable chapter list. On a narrow window the workspace destinations become a compact top row.

The active profile and connection state remain above the library. A demonstration catalog is labeled as a demonstration; an imported profile is read-only; a disconnected profile retains its last known information. The **Connection** control explains the current state rather than pretending that offline data is live.

| Control | What it does |
| --- | --- |
| **List** | Compact rows with preview, name, installed/observed versions, detected status, source and real actions |
| **Gallery** | Image-forward cards built from the same complete mod records, with version/status context and Details |
| **All mods / By chapter** | In Gallery, choose a continuous globally sorted collection or retain chapter headers and their saved collapse state |
| **Compact / Comfortable** | Changes spacing and, in Gallery, card size without dropping records |
| **Source** | Filters the current library to an actual provider or source state; the option reports its count |
| **Search** | Searches names, authors, versions, notes and existing source/health evidence |
| **All / Updates / Maintenance / Needs attention / Up to date** | Narrows the library using the current detected evidence |
| **Reset filters** | Clears library search, status, source and health filters |
| **Details** or the preview | Opens the existing mod inspector and its real source, notes and compatibility controls |
| Right-click or **Shift+F10** on a mod control | Opens contextual Details, source, bookmark and applicable file/MO2 actions |

Text search retains qualified terms such as `author:`, `version:`, `note:` and `provider:`. **Ctrl+K** returns to the library and focuses search. The full library remains available: no result cap or viewport virtualization is used. Changing view or density preserves surviving row objects instead of rebuilding the catalog or repeating a provider scan.

Gallery defaults to **All mods**, so small chapters do not leave most of a row empty. Every matching record appears, including records in chapters you previously collapsed. Chapter labels stay on the cards and in Details. **By chapter** restores the saved chapter organization; returning to List keeps the same saved collapsed chapters. Priority, Name and Installed version sort globally in All mods and within chapters in the organized views. **J / K** follows the displayed order. Selecting a chapter through Collections opens the organized view and reveals that chapter.

A source filter stays active until changed or reset. Explicit chapter navigation and **Next pending** clear conflicting library filters so the requested target is actually revealed. Presentation preferences persist with the profile, including in the MO2 Chronicle. They do not alter enabled mods, installed files, load order or earned rewards.

## Real artwork, honest absence

A mod picture appears when the exact provider record supplies it. Current compact records carry Nexus project pictures, Modrinth gallery images/project icons and Thunderstore package icons. Gallery images fill their image area; icons retain their proportions. The frontend and native policy restrict these previews to the supported public CDN hosts.

The application does not guess a thumbnail from a filename or use a creator's portrait as mod artwork. **No preview supplied** is an intentional fallback. Failed images leave the card's name, source, versions and controls intact. Images decode asynchronously and use normal browser image loading; every mod remains in the result set whether its image is loaded or not.

The original source link remains the authoritative way to inspect the project. A beautiful thumbnail does not establish file compatibility, ownership, an authenticated session or installation permission.

## Updates remain the main operation

**Check for updates** uses the existing source-checking owner. **Updates** opens the revised Workshop with compact status counts, useful lanes, exact versions, source links, reviewed file selections and real download/installation states.

The existing rules remain in force:

1. Confirm the active profile and inspect the observed release evidence.
2. Review an exact file when the edition, runtime or archive is ambiguous. A version change alone does not authorize installation.
3. Approve the existing download/install workflow. MO2 retains its supported Nexus authentication and native installer, including FOMOD decisions.
4. Preserve the actual backup and verify the resulting installation. Use the existing guarded recovery workflow if restoration is required.

The appearance does not change queue authority, profile generations, cancellation, stale-result rejection, backup ownership or rollback rules. A context menu opened for an older catalog closes before it can act on a replacement profile. Watchtower source-page changes remain separate from verified compatible releases.

## Appearance and adventures

Dark mode uses warm charcoal and oxidized green; light mode uses parchment and warm white. Brass identifies the primary action. Status is written in text as well as color. Shared controls, the native shell, Watchtower and preference surfaces use the same material and type language.

**Appearance** switches the theme; a narrow window exposes the same action in its header. In the Rust desktop this shortcut updates the native appearance preference and waits for its acknowledgement, keeping the shell, dashboard and Watchtower consistent after profile imports. Native preferences also offer **System**. Standalone HTML retains its independent local theme preference. Keyboard outlines and reduced motion remain supported. Comfortable density is available when larger spacing is more useful than maximum rows.

**Adventures** retains Questbound, AetherFX/FX Studio, Shiverfell, Mythforge, Siege and Atlas. Their individual visual identities, progression, saves and customization remain intact. Maintenance stays available without playing a game, enabling effects or connecting Watchtower.

## Design reference

We studied the official [Sims 4 Mod Manager](https://gametimedev.de/S4MM/) and [tabs documentation](https://s4mm-wiki.gametimedev.de/en/how-to-use/tabs). Its image-forward grouping and compact controls informed the layout work. Modhunter's source evidence, version comparison, reviewed-update workflow and fantasy identity remain its own. The [official historical gallery screenshot](https://www.gametimedev.de/images/screenshot.png) used for composition study is labeled Alpha v0.1; it is not presented as the current version or a measured competitor benchmark.

## Candidate verification and release evidence

The authoritative pipeline is [native-rust.yml](https://github.com/Herbertofury/Modhunter/actions/workflows/native-rust.yml). The completed checkpoint is **candidate 6**, commit [`2246748a6b02566d1521e2506d73b2cbbbfb81b8`](https://github.com/Herbertofury/Modhunter/tree/2246748a6b02566d1521e2506d73b2cbbbfb81b8), tested by [Actions run 43886408821](https://github.com/Herbertofury/Modhunter/actions/runs/43886408821). Its source was clean and the receipts bind the tested files and executable. A later performance repair must earn equivalent evidence for its own commit before publication.

| Acceptance layer | Current evidence / status |
| --- | --- |
| Source and lifecycle contracts | C6 contract job passed, including Rust, Python and JavaScript checks; 44 focused frontend runtime tests, 5 Atelier contracts and 33 release-validator self-tests passed |
| Gallery, filtering, keyboard and packaged style policy | **11/11 actual Chromium Gallery tests passed**, including row reuse, saved grouping, context menus, thumbnail failure and Gallery sizing under the packaged style-nonce policy |
| Before/after visual acceptance | **112 actual Chromium screenshots** retained: v0.11.0 versus C6, dark/light, desktop/narrow, public source artwork, dialogs, Workshop and preserved game surfaces |
| Actual Windows desktop | Offline import/export passed; **16/16 MO2-connected desktop checks passed**, including native theme acknowledgement, 1440/900-wide layouts, Gallery, Details and focus restoration |
| Native Watchtower | **26/26 checks passed** for owned page/session, download, privacy, browser-resource and notification fixtures, including the anonymous public HTTPS observation |
| Official MO2 2.5.2 fixture | **30/30 checks passed**, including the production plugin, actual Qt binding, source/profile lifecycle, native archive installation and content recovery |
| Exact Windows installer | **15/15 checks passed**: install, matching executable/archive-helper bytes, installed WebView2 launch, uninstall, registration removal and preservation of the external fixture profile |
| Complete 11,207-mod native renderer | **16/16 completeness/runtime checks passed**; every ordered record remained laid out after import, search restoration and all six view/density transitions. Both configuration folders and temporary WebView2 policies were restored |
| Release packaging | The clean C6 checkout and retained artifacts passed a full `prepare_release.py` dry run, producing all six release assets plus release notes |
| Responsiveness | **Release blocker remains:** complete-library Gallery/List changes take several seconds in both Chromium and actual Windows WebView2; measurements below preserve that result |
| Public release and live Wiki | Release/tag publication, public asset download verification, final image placement and live Wiki publish/readback remain pending |

The run retains separate [Windows evidence](https://github.com/Herbertofury/Modhunter/actions/runs/43886408821/artifacts/14236615652), [MO2 evidence](https://github.com/Herbertofury/Modhunter/actions/runs/43886408821/artifacts/14235781341), [Chromium interaction evidence](https://github.com/Herbertofury/Modhunter/actions/runs/43886408821/artifacts/14235021901) and [visual comparison evidence](https://github.com/Herbertofury/Modhunter/actions/runs/43886408821/artifacts/14234813883). The original Windows artifact's downloaded bytes matched GitHub's SHA-256 and passed ZIP integrity checks. The portable, native-tested and installed executable hashes also matched. Final releases will carry the public receipt and screenshot allowlist in the Verification ZIP.

These results use owned fixtures. They do not establish the user's personal MO2 beta installation, authenticated Nexus account/download behavior, real FOMOD choices or in-game mod compatibility. The local Linux executor's Chromium launch was blocked by its IPC environment; the browser evidence above comes from actual Chromium in CI.

### Complete-library performance: unresolved

Both runtimes use the same **11,207-record** snapshot from `test_frontend_responsiveness.catalog`, with `offline=True`, SHA-256 `afd855e4ac76f284630ffb4ed6d9dc37280113c9559aef4402516795b7fef0a0`. All records remain present and laid out, including the last searchable item; no cap, viewport virtualization or content-visibility skipping is used. Import is timed through the shipped import entrypoint and two animation frames; view/density changes use real controls and two frames. [Raw comparison and native receipts are retained in the run artifacts](https://github.com/Herbertofury/Modhunter/actions/runs/43886408821).

| Operation | Chromium v0.11.0 median | Chromium C6 median | Windows WebView2 C6 raw sample |
| --- | --- | --- | --- |
| Complete renderer import | 4.016 s | 4.753 s | 3.705 s |
| Exact last-item search | 0.530 s | 0.587 s | 0.637 s |
| List density change | 0.814 s | 2.361 s | 1.817 s |
| List → Gallery | New view | **17.399 s** | **13.759 s** |
| Gallery density change | New view | **5.282 s** | **3.069 s** |
| Gallery → List | New view | **22.428 s** | **8.612 s** |

Chromium **157.0.8103.0** ran on Linux at 1440 × 900 with reduced motion, using one warmup and three measured rounds. The native result is **one raw sample** from WebView2 **156.0.4314.58**, at its actual 1028 × 633 viewport, DPR 1, light theme and normal motion; foreground ownership and document visibility were checked. Hardware, viewport and motion differ, so the two runtime columns are not a speedup comparison. The native sample measures Windows renderer import and interactions; Rust filesystem import, MO2/provider I/O and installation are outside that timing scope.

The C6 measurements confirm a real responsiveness problem despite preserved functionality and complete results. Performance investigation remains open; a green functional job or a successful package build does not clear this blocker.

### Actual screenshot record and publication slots

The captures below already exist in the C6 artifacts and were inspected. Final versioned image placement remains pending; no planned path is presented as a published image.

| Capture | Proven candidate artifact file | Final Wiki placement |
| --- | --- | --- |
| Before library | Visual: `before/library-1440-dark.png` — v0.11.0 baseline, same catalog | Pending selection/copy |
| After library | Visual: `after/library-1440-dark.png` — C6, same catalog | Pending selection/copy |
| Authentic source artwork | Visual: `after/public-projects-gallery-1440-dark.png` and light equivalent | Pending selection/copy |
| Light and narrow layouts | Visual: `after/library-900-light.png`, `after/library-390-light.png` | Pending selection/copy |
| Actual native wide/narrow | Windows: `live-mo2-smoke/desktop-window-1440-dark.png`, `desktop-window-900-light.png` | Pending selection/copy |
| Actual native Gallery/Details | Windows: `live-mo2-smoke/desktop-window-gallery-light.png`, `desktop-window-inspector-light.png` | Pending selection/copy |
| Actual installed application | Windows: `installer-smoke/native-launch/fixture-desktop.png` | Pending selection/copy |
| Workshop, Watchtower, dialogs and games | Visual comparison and Chromium interaction artifacts retain the other captured states | Pending final selection |

### Release slots

The dry run staged the installer, portable ZIP, MO2 plugin ZIP, standalone HTML ZIP, Verification ZIP and `SHA256SUMS.txt`, plus release notes. Public links will be filled only after the final candidate passes, publication completes and downloaded assets are verified.

| Final release field | Status |
| --- | --- |
| Release tag, final commit and successful run | Pending final candidate; C6 identity above remains the proven checkpoint |
| GitHub Release URL | Pending publication |
| Windows installer | `Modhunter_0.12.0_x64-setup.exe` staged; public download pending |
| Portable build | `Modhunter-Rust-Windows.zip` staged; public download pending |
| MO2 components | `Modhunter-0.12.0-MO2-Plugin.zip` staged; public download pending |
| Standalone HTML | `Modhunter-0.12.0-Standalone.zip` staged; public download pending |
| Verification and checksums | `Modhunter-0.12.0-Verification.zip` and `SHA256SUMS.txt` staged; public download pending |
| Live Wiki readback | Pending publication of the final page and selected real screenshots |
