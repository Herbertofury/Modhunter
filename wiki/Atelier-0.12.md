# Atelier 0.12 / The Mod Vault

[Home](Home) · [Design standard](Design-System-and-Quality-Bar) · [Rust desktop](Rust-Desktop) · [Update Workshop](Update-Workshop) · [Watchtower](Watchtower)

**Atelier 0.12 is the implemented visual update for Modhunter's Rust desktop and shared mod dashboard.** Candidate 7 completed its functional pipeline, including actual Windows, MO2 and installer checks. Release preparation rejected its native Gallery timing against the fixed performance limit. The C8 source now includes the accepted font, transparent-preview, count-copy and filtered-row-cache followups; its final integrated pipeline and publication remain pending.

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

The authoritative pipeline is [native-rust.yml](https://github.com/Herbertofury/Modhunter/actions/workflows/native-rust.yml). The latest completed functional checkpoint is **candidate 7**, commit [`18883b32e0683dad8aed1dc87c6fe9ec577e12f2`](https://github.com/Herbertofury/Modhunter/tree/18883b32e0683dad8aed1dc87c6fe9ec577e12f2), tested by [Actions run 43926185167](https://github.com/Herbertofury/Modhunter/actions/runs/43926185167). Its clean source, tested executable and retained receipts were checked against that commit. Successful functional jobs do not constitute release acceptance: C7 exceeded the native Gallery limit, and its Linux screenshots exposed missing font coverage.

| Verification layer | Proven C7 result / remaining boundary |
| --- | --- |
| Source and lifecycle contracts | Rust, Python and JavaScript contract jobs passed, including **48 frontend runtime tests** and **35 release-validator self-tests** |
| Gallery and native-manager browser controls | **14/14 actual Chromium Gallery cases** and **20/20 native-manager browser fixture cases passed**; the latter are browser tests, not Windows execution |
| Before/after visual record | **112 actual Chromium screenshots** retained and audited against 18 authored asset hashes; missing CJK, Runic and symbol glyphs prevent treating this as complete visual acceptance |
| Actual Windows desktop | Offline import/export passed; **16/16 MO2-connected desktop checks passed**, including native theme acknowledgement, wide/narrow layouts, Gallery, Details and focus restoration |
| Native Watchtower | **26/26 checks passed** for owned page/session, download, privacy, browser-resource and notification fixtures, including the anonymous public HTTPS observation |
| Official MO2 2.5.2 fixture | **30/30 checks passed** with the production plugin and actual Qt binding, including exact archive installation, backup, original-byte recovery, wrong-target rejection and later-edit preservation |
| Exact Windows installer | **15/15 checks passed** for installation, matching executable/archive-helper bytes, installed WebView2 launch, uninstallation and preservation of the external fixture profile |
| Complete 11,207-mod native renderer | **16/16 structural/runtime checks passed**; every ordered record remained laid out, and all six view/density endpoints removed the temporary guard and restored the original hover transition |
| Release preparation | **Rejected:** native List → Gallery was **5711.6653 ms**, above the unchanged **5000 ms** limit. No C7 release asset set was created |
| Final integrated candidate and publication | Pending a successful same-commit pipeline, release preparation, public asset downloads and live Wiki readback |

C7 retains separate [Windows evidence](https://github.com/Herbertofury/Modhunter/actions/runs/43926185167/artifacts/14257168216), [MO2 evidence](https://github.com/Herbertofury/Modhunter/actions/runs/43926185167/artifacts/14257096633), [Chromium interaction evidence](https://github.com/Herbertofury/Modhunter/actions/runs/43926185167/artifacts/14256577119) and [visual comparison evidence](https://github.com/Herbertofury/Modhunter/actions/runs/43926185167/artifacts/14255599458). Downloaded artifact bytes matched their API SHA-256 digests and passed ZIP integrity checks. The portable, native-tested and installed executable hashes matched. These CI artifacts are verification inputs, not a published release.

### Accepted followups in the C8 source

| Followup | Actual evidence and its scope |
| --- | --- |
| Linux CI font coverage | [D17 run 43930545746](https://github.com/Herbertofury/Modhunter/actions/runs/43930545746), commit `ef28a2caf684f4b6aea8e685e08a5ec7ae463fe6`: **20/20 native-manager browser cases passed**. Chromium reported the expected real font faces for all eight glyphs in the Chinese heading and six dashboard/game probes. The [artifact](https://github.com/Herbertofury/Modhunter/actions/runs/43930545746/artifacts/14257714055) includes `ci-fonts.json`, `ci-font-rendering.json` and inspected Chinese/library/expedition PNGs. Pinned fonts are a CI prerequisite; they do not alter the product font stack or retroactively repair C7 captures |
| Transparent artwork and failed-image fallback | [D24 run 43936598227](https://github.com/Herbertofury/Modhunter/actions/runs/43936598227), commit `8f0927fe747d66c65a22b40015c086543500162e`: **14/14 actual Gallery cases passed**, with six retained PNGs in the [artifact](https://github.com/Herbertofury/Modhunter/actions/runs/43936598227/artifacts/14261945234). Genuine, hash-verified transparent artwork has a clean backdrop in Gallery and Details; a real 404 exposes the intended fallback. Cover-fit use of the icon fixture is explicitly an edge case, not a provider screenshot claim |
| Singular library counts | The [C8 source checkpoint](https://github.com/Herbertofury/Modhunter/tree/e1761de4d4b27af977ddb666b40afbe8765c7e95) preserves the accepted preview fix and adds singular mod/source/chapter wording with all four locale entries. Local string/translation checks preserved plural output. This copy change is not represented as a separately completed native run |
| Repeated-filter restoration | [D27 run 43941770501](https://github.com/Herbertofury/Modhunter/actions/runs/43941770501), commit `0373ddd9a78b0bb0f8db5c2af1129b5cbe201531`: **51 Node contracts and 19/19 actual Gallery cases passed**. The [artifact](https://github.com/Herbertofury/Modhunter/actions/runs/43941770501/artifacts/14263391190) retains all 32 complete-library states and 24 restored transition endpoints. Search restoration improved in both paired rounds; Gallery had no consistent improvement. The cache retains only unchanged inactive rows removed by explicit filters/collapsed chapters, with profile/catalog/locale ownership and image-failure checks. It does not remove matching results |

D27's strict pixel comparison still recorded small border-raster differences, including in an unchanged control; the raw failures remain in its receipt. Its acceptance is limited to the reviewed restoration improvement and functional/geometry checks, not a claim that every comparison pixel matched or that the Windows Gallery limit has passed. C8 integrates these followups, but final native, MO2, browser, visual and packaging evidence must bind the final combined commit.

These results use owned fixtures. They do not establish the user's personal MO2 beta installation, authenticated Nexus account/download behavior, arbitrary FOMOD choices or in-game mod compatibility. The local Linux executor cannot launch Chromium because of its IPC environment; the browser evidence above comes from actual Chromium in CI.

### Complete-library performance: release gate still open

Both runtimes use the same **11,207-record** snapshot from `test_frontend_responsiveness.catalog`, with `offline=True`, SHA-256 `afd855e4ac76f284630ffb4ed6d9dc37280113c9559aef4402516795b7fef0a0`. Every record remains present and laid out, including the last searchable item; no result cap, viewport virtualization or content-visibility skipping is used. Import runs through the shipped entrypoint and two animation frames; view/density measurements use real controls and the existing two-frame endpoint.

The following are **C7 measurements**, not measurements of the final combined C8 source. The visual artifact contains `visual-release/visual-release.json`; the Windows artifact contains `native-library-performance/native-library-performance.json`.

| Operation | Chromium v0.11.0 median, same C7 run | Chromium C7 median | Windows WebView2 C7 raw sample |
| --- | --- | --- | --- |
| Complete renderer import | 7.699 s | 9.416 s | 4.334 s |
| Exact last-item search | 0.773 s | 0.894 s | 0.646 s |
| Search restoration | Not recorded separately | Not recorded separately | 3.246 s |
| List density change | 1.091 s | 3.689 s | 1.843 s |
| List → Gallery | New view | **12.203 s** | **5.712 s** |
| Gallery density change | New view | **7.896 s** | **3.132 s** |
| Gallery → List | New view | **8.233 s** | **3.840 s** |

Chromium **157.0.8103.0** ran on Linux at 1440 × 900 with reduced motion, one warmup and three measured rounds with alternating variant order. The Windows result is **one raw sample** from WebView2 **156.0.4314.58**, at its actual 1028 × 633 viewport, DPR 1, light theme and normal motion, with foreground ownership and document visibility checked. Different hardware, viewport and motion settings prevent treating these columns as a cross-runtime speedup comparison. Rust filesystem import, MO2/provider I/O and installation are outside the renderer timings.

C7 repairs the measured bulk-transition defect and restores normal hover feedback within the tested endpoint, but its Gallery result still exceeds the native release ceiling by **711.6653 ms**. D27's browser search-restoration samples improved from **3695.5021 → 2659.2301 ms** and **3189.9253 → 2694.3523 ms** (28.0% and 15.5%); that is a narrower benefit, not native Gallery acceptance. The fixed performance limit remains in force for the final pipeline.

### Actual screenshots and publication slots

These files already exist in their stated artifacts. Final versioned screenshot placement and live Wiki publication remain pending; no planned filename is presented as a published image.

| Capture | Actual artifact file / qualification |
| --- | --- |
| Before/after library | C7 Visual: `before/library-1440-dark.png` and `after/library-1440-dark.png`, same catalog; preserve the C7 font-coverage caveat |
| Authentic source artwork | C7 Visual: `after/public-projects-gallery-1440-dark.png` and light equivalent |
| Light and narrow library | C7 Visual: `after/library-900-light.png`, `after/library-390-light.png` |
| Actual native desktop and Details | C7 Windows: `live-mo2-smoke/desktop-window-1440-dark.png`, `desktop-window-900-light.png`, `desktop-window-gallery-light.png`, `desktop-window-inspector-light.png` |
| Actual installed application | C7 Windows: `installer-smoke/native-launch/fixture-desktop.png` |
| Verified glyph rendering | D17: `watchtower-preferences-zh.png`, `ci-font-library.png`, `ci-font-expedition.png` |
| Transparent and failed artwork | D24: `thumbnail-alpha-library-1440-night.png`, `thumbnail-alpha-inspector-1440-night.png`, their `390-snow` counterparts, `thumbnail-failed-library-390-snow.png` and `thumbnail-failed-inspector-390-snow.png` |

### Release slots

**C7 release preparation stopped at the performance gate and created no release asset set.** Its tested CI installer and portable bundle remain verification inputs. Final filenames below follow the repository convention; they are not staged or published downloads. Links will be added only after the final candidate passes, publication completes and downloaded bytes are verified.

| Final release field | Status |
| --- | --- |
| Release tag, final commit and successful run | Pending final integrated candidate; C7 is the completed functional checkpoint above |
| GitHub Release URL | Pending publication |
| Windows installer | Expected `Modhunter_0.12.0_x64-setup.exe`; final packaging and public download pending |
| Portable build | Expected `Modhunter-Rust-Windows.zip`; final packaging and public download pending |
| MO2 components | Expected `Modhunter-0.12.0-MO2-Plugin.zip`; final packaging and public download pending |
| Standalone HTML | Expected `Modhunter-0.12.0-Standalone.zip`; final packaging and public download pending |
| Verification and checksums | Expected `Modhunter-0.12.0-Verification.zip` and `SHA256SUMS.txt`; final packaging and public download pending |
| Live Wiki readback | Pending publication of the final page and selected actual screenshots |
