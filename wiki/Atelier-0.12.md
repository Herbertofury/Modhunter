# Atelier 0.12 / The Mod Vault

[Home](Home) · [Design standard](Design-System-and-Quality-Bar) · [Rust desktop](Rust-Desktop) · [Update Workshop](Update-Workshop) · [Watchtower](Watchtower)

**Atelier 0.12 is the implemented visual update for Modhunter's Rust desktop and shared mod dashboard.** This page is being prepared with the release candidate. Packaging, final native acceptance, screenshots and publication remain pending until their exact candidate receipts are attached below.

## Your library, with more control over the view

The left navigation opens **Library**, **Updates**, **Downloads**, **History**, **Tools** and **Adventures**. Expand the rail when you want labels, or keep it compact to make more room for your collection. **Collections** opens the existing searchable chapter list. On a narrow window the workspace destinations become a compact top row.

The active profile and connection state remain above the library. A demonstration catalog is labeled as a demonstration; an imported profile is read-only; a disconnected profile retains its last known information. The **Connection** control explains the current state rather than pretending that offline data is live.

| Control | What it does |
| --- | --- |
| **List** | Compact rows with preview, name, installed/observed versions, detected status, source and real actions |
| **Gallery** | Image-forward cards built from the same complete mod records, with version/status context and Details |
| **Compact / Comfortable** | Changes spacing and, in Gallery, card size without dropping records |
| **Source** | Filters the current library to an actual provider or source state; the option reports its count |
| **Search** | Searches names, authors, versions, notes and existing source/health evidence |
| **All / Updates / Maintenance / Needs attention / Up to date** | Narrows the library using the current detected evidence |
| **Reset filters** | Clears library search, status, source and health filters |
| **Details** or the preview | Opens the existing mod inspector and its real source, notes and compatibility controls |
| Right-click or **Shift+F10** on a mod control | Opens contextual Details, source, bookmark and applicable file/MO2 actions |

Text search retains qualified terms such as `author:`, `version:`, `note:` and `provider:`. **Ctrl+K** returns to the library and focuses search. The full library remains available: no result cap or viewport virtualization is used. Changing view or density preserves the existing row objects instead of rebuilding the catalog or repeating a provider scan.

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

The authoritative pipeline is [native-rust.yml](https://github.com/Herbertofury/Modhunter/actions/workflows/native-rust.yml). **Pending** below means the candidate has not yet earned that claim; historical v0.11.0 results are not substituted for 0.12 evidence.

| Acceptance layer | Current evidence / status |
| --- | --- |
| Shared UI and source syntax | Implemented; local JavaScript syntax checked after the normal build placeholder expansion |
| Focused source regressions | Local targeted checks: 74 frontend/source/mod-intelligence Node tests, 5 Atelier Python contracts, 7 Chronicle tests passed before final candidate assembly; exact-head CI still required |
| New gallery, filtering and keyboard interactions | Browser regression suite added; exact-head Chromium execution pending |
| Equivalent 11,207-mod comparison | Harness checks the complete DOM, search of the last record, view/density preservation and alternating baseline/candidate measurements; results pending |
| Actual native Windows WebView2 | Candidate launch, import, shell actions and screenshot acceptance pending |
| Real Windows MO2 fixture | Candidate install/restore/profile acceptance pending; this does not substitute for the user's personal MO2 beta installation or accounts |
| Installer / portable / MO2 checksums | Candidate pipeline packaging and downloadable-asset verification pending |
| Final source identity | Release tag, commit and successful run URL to be filled from the released candidate |
| Live GitHub Wiki | Mirror prepared; live publish/readback pending |

The local Linux executor could not launch Chromium because its IPC socket operation was denied. No local screenshot or browser pass is claimed from that attempt. Browser and Windows acceptance must come from an actual successful run.

### Actual screenshot record

Final images must be captured from the running application or the shipped shared dashboard, with fixture versus native data identified. The following are the intended versioned destinations; they are not finished screenshots yet.

| Capture | Intended destination / scope |
| --- | --- |
| Before library | `screenshots/v0.12.0/before-library.png` — v0.11 baseline / same catalog |
| After library | `screenshots/v0.12.0/after-library.png` — candidate / same catalog |
| Gallery with source artwork | `screenshots/v0.12.0/after-gallery.png` — identified public source artwork fixture |
| Light and narrow layouts | Versioned screenshots to be selected from the actual visual QA artifact |
| Inspector, Workshop, native shell and dialogs | Versioned captures to be selected after actual interaction and native acceptance |

Release publication may replace these planned names with the actual artifact names. The final record must identify the release commit and CI run, and state any account-, hardware- or personal-profile-specific behavior that remains unexecuted.
