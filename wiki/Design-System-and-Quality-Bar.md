# Modhunter / Design & Quality Standard

[Home](Home) · [Atelier 0.12 guide](Atelier-0.12) · [Rust desktop](Rust-Desktop) · [Update Workshop](Update-Workshop)

> **North star:** A premium, handcrafted mod-management tool with a coherent game world around it. **Utility first. Beauty second. Delight everywhere. Slop nowhere.**

This is an active product contract. Implementation, browser proof, native Windows acceptance and a published package are separate milestones. A successful build does not complete visual acceptance.

## 01 / Product hierarchy

| Priority | Surface | What the user should learn or do |
| --- | --- | --- |
| 1 | Current MO2 profile, runtime and connection | Identify the installation in use and distinguish live data, read-only imports and the demonstration catalog |
| 2 | Library and Update Workshop | Find every installed mod, see installed and observed versions, understand what needs review and why |
| 3 | Exact source and file review | Inspect the actual release identity, runtime requirements and archive before approving work |
| 4 | Download, installation and recovery | Preserve MO2's installer decisions, actual backups, verified results and guarded recovery |
| 5 | Questbound, AetherFX, Shiverfell, Mythforge, Siege and Atlas | Keep each game's identity, progression and customization without obstructing maintenance |

**Do not market “update every mod” as a guarantee.** Some providers require manual page review or a provider key. Unknown versions, expired evidence and inaccessible accounts remain explicit. A newer project version is not installation approval.

## 02 / Art direction

**One visual language, independent entry points.** The current Windows product is a Rust/Tauri host with WebView2. The standalone HTML, native dashboard and MO2-hosted dashboard use the same compiled presentation. Historical Electron surfaces retain matching styling but are not the runtime of the new Windows release.

| Role | Dark | Light |
| --- | --- | --- |
| Canvas | OLED black `#000000` | `#f5f6f8` |
| Surface | Neutral near-black `#0b0c10` | `#ffffff` |
| Raised surface | `#111216` | `#eaedf2` |
| Divider | `#272b35` | `#cbd1dc` |
| Primary text | Cool white `#f4f6fa` | `#1b2230` |
| Secondary text | `#b7becb` | `#4e5b70` |
| Focus / selection | Ice blue `#b9dcff` | `#175b9c` |
| Chapter separators | Original authored chapter colors | Same chapter identities with contrast-adjusted bands |
| Operation feedback | Blue busy, mint complete, amber review/cancelled, coral error | Equivalent legible semantic colors |

- Use a local system UI font for controls and data. Reserve Georgia/Palatino-style serif typography for editorial headings. No remote font is required.
- Use compact navigation, aligned controls, hairline dividers and restrained 4–10 px corner radii. Spacious typography must not become oversized empty panels.
- Pair every status color with a readable label. Keep chapter bands, diamonds, collapse arrows, manual progress and cleared counts visible. Use amber for review and coral for errors; green does not establish compatibility by itself.
- The utility palette is true black and neutral, with cool primary controls. Do not reintroduce green/gold chrome. Optional game palettes remain independent.
- Give hover, keyboard focus, pressed/current tabs, selected rows and bookmarks distinct visible states. Keep manual completion labels separate from detected updates and verified installation.
- Busy indicators and progress must follow real operation state. Starting is indeterminate; checked/total progress becomes determinate only when the service supplies a positive total. Failed, cancelled, partial and unavailable checks must not revert to a reassuring idle dot.
- Prefer authentic source artwork. Never guess artwork from a mod's name, show a creator avatar as a mod preview, or fabricate an attractive image for missing data.
- Keep utility icons consistent. The shared navigation uses original line vectors; fantasy spectacle remains inside the optional games and FX Studio.
- Keep routine transitions below 180 ms and respect reduced motion. Cosmetic changes must not rebuild the complete mod catalog.

### Presentation ownership

| Surface | Source |
| --- | --- |
| Shared library, Workshop and inspector | [`mo2_update_arsenal/web/app.template.html`](https://github.com/Herbertofury/Modhunter/blob/main/mo2_update_arsenal/web/app.template.html), [`app.js`](https://github.com/Herbertofury/Modhunter/blob/main/mo2_update_arsenal/web/app.js), [`atelier.css`](https://github.com/Herbertofury/Modhunter/blob/main/mo2_update_arsenal/web/atelier.css) |
| Rust desktop shell | [`native/ui/shell.html`](https://github.com/Herbertofury/Modhunter/blob/main/native/ui/shell.html), [`native.css`](https://github.com/Herbertofury/Modhunter/blob/main/native/ui/native.css) |
| Watchtower | [`native/ui/watchtower.html`](https://github.com/Herbertofury/Modhunter/blob/main/native/ui/watchtower.html), [`watchtower.css`](https://github.com/Herbertofury/Modhunter/blob/main/native/ui/watchtower.css), [`watchtower-native.css`](https://github.com/Herbertofury/Modhunter/blob/main/native/ui/watchtower-native.css) |
| Native preferences | [`native/ui/preferences.css`](https://github.com/Herbertofury/Modhunter/blob/main/native/ui/preferences.css) |
| Preserved Electron companion | [`desktop/chrome.css`](https://github.com/Herbertofury/Modhunter/blob/main/desktop/chrome.css) |

Both light and dark themes require equivalent contrast and usable focus states. Optional game modes retain their own palettes and controls rather than inheriting a blanket utility restyle.

## 03 / Component acceptance

**Navigation:** Library, Updates, Downloads, History, Tools and Adventures remain one step away. A compact rail can expand to labels. Collections keeps every chapter searchable. A narrower window reorganizes navigation without dropping the underlying destinations. Visible controls must perform real actions.

**Mod library:** List and Gallery are presentations of the same keyed records. Switching view or density retains the complete result set, filters and notes. Default list rows remain compact; comfortable spacing is opt-in. Show the installed version, meaningful update state, exact provider identity and accessible Details. Full names remain available through the title and inspector even when a compact card wraps or truncates them.

**Gallery organization:** All mods is the default continuous gallery, globally sorted with a visible chapter label on each card. By chapter retains section headers and their saved collapse state. Entering All mods reveals every matching record without rewriting saved collapses; returning to List or By chapter restores that organization. Keyboard row navigation follows the displayed order. Sparse real libraries must receive the same visual scrutiny as an ideal artwork fixture.

**Artwork:** Only explicit artwork supplied by the exact source record is eligible. Current compact artwork paths use Nexus project pictures, Modrinth gallery/icon metadata and Thunderstore package icons. Public CDN hosts are validated in the data layer and image policy. Images use browser image loading and decoding, with no referrer. Missing, blocked or failed images retain a deliberate “No preview supplied” fallback. A thumbnail is not verification evidence.

**Filtering and preferences:** Text search, source/provider filtering, status filters, chapter navigation and existing mod-health filters must compose predictably. Reset clears the applied library filters. Jumping to a chapter or the next pending mod must reveal its target. View, gallery grouping, density and navigation preferences persist within the current profile; the MO2 Chronicle validates the new preference vocabulary.

**Update Workshop:** Keep the full list, exact versions, risk reasons, reviewed selections and install states. The shorter visual route must preserve the same profile-bound source and file authority. A reviewed selection survives scrolling; a changed profile, source or execution context invalidates stale work.

**Inspector and context menu:** Notes, bookmarks, source editing, file choices and reveal-in-MO2 remain real actions. Context menus work with pointer and keyboard, remain inside the window, and close on catalog/profile replacement. Opening a thumbnail opens the actual inspector. Connection details must remain truthful in standalone and disconnected modes.

**File review, installation and recovery:** Retain exact archive identity, explicit review and confirmation, MO2's native installer, backup before modification, verification after installation and guarded recovery. Do not make an operation look successful before its owner reports success.

**Games and customization:** Preserve Questbound, AetherFX/FX Studio, Shiverfell, Mythforge, Siege and Atlas, their saves, progression and identities. Ranked rewards require real MO2 evidence; offline practice is labeled. Effects never become a prerequisite for maintaining mods.

## 04 / Verification gates

The [native Rust workflow](https://github.com/Herbertofury/Modhunter/actions/workflows/native-rust.yml) is the release authority. Record the exact candidate commit and observed run results in the release evidence, rather than treating this checklist as a receipt.

- [ ] Relevant Rust tests, formatting and Clippy pass on the candidate source.
- [ ] Python lifecycle, provider, preference, profile isolation and package tests pass.
- [ ] JavaScript runtime/syntax checks and actual Chromium interaction checks pass.
- [ ] The rebuilt shared dashboard is byte-identical in standalone HTML, the native bundle and MO2 components.
- [ ] Desktop, mid-size and narrow windows have no meaningful overlap, offscreen action or clipped dialog.
- [ ] Light and dark themes, keyboard focus, hover/disabled states and reduced motion have been visually inspected.
- [ ] Normal, long-name, empty, error, image-missing and dense-library states have been exercised.
- [ ] Complete 10,000+ mod inventories remain rendered and searchable; compare equivalent baseline/candidate work and report measured results.
- [ ] Actual Windows WebView2 launches the packaged candidate and exercises the changed UI.
- [ ] The supported real Windows MO2 fixture verifies connection, exact-file workflow, installation, actual recovery and profile transitions.
- [ ] Release asset hashes, source commit, installer and portable package agree and public downloads are checked.
- [ ] Versioned actual screenshots and the source Wiki mirror are published to the live GitHub Wiki.

## 05 / Never regress

No cropped/mod-count-limited lists, no viewport virtualization, no hidden results to manufacture speed, no silent install, no fabricated proof/rewards and no removed game modes. The standalone HTML, Rust desktop and MO2 plugin remain independently usable entry points. Failed work retains the last complete catalog. Account sign-in, changed page text and update hints never become permission to overwrite installed mods.

Use cached markup, existing keyed DOM updates, bounded background work and measured allocation reductions. Changing a presentation preference must not repeat expensive source work. Lazily loading an image does not remove its mod record from the library.

Rows removed by an explicit filter or collapsed chapter may be retained without a parent for later reuse. Only unchanged inactive rows qualify. Catalog, profile, locale, metadata and image-failure checks prevent stale state from returning; selected controls and active effects keep their existing lifecycle. Every matching row remains in the rendered document. This cache has no offscreen container and no arbitrary record limit.

Compact rows omit only duplicate legacy cells that compact CSS already hid. Checklist state, J/K and Space, inspector completion, Undo and the explicit noncompact host fallback remain functional. Full galleries use flex wrapping with one container-width calculation; card contents keep their own grid. Resize observations are coalesced, and view/density changes do not reconfigure unrelated effects. These implementation choices require measured full-catalog proof rather than inferred performance claims.

## 06 / 0.12.1 OLED correction

The 0.12.1 source restores colored chapter separators and the visible manual checklist cues that the compact 0.12.0 redesign suppressed. It replaces the utility green/gold palette with OLED black, neutral surfaces and cool white/blue controls. It also repairs missing starting/error/polling feedback, preserves chapter keyboard focus and gives bookmarks an explicit pressed state. The same full-library, actual-browser, Windows WebView2, installer and MO2 release gates apply. Publication and exact-run results are recorded after those gates finish.

## 07 / 0.12 implementation and evidence

Atelier 0.12 implements the compact navigation rail, list/gallery switch, density control, provider filter, authentic artwork forwarding, themed details and context menus, and profile-persisted library preferences. Rust shell, Watchtower and native preferences share the restrained palette; the legacy companion remains preserved. The update, recovery and game owners remain authoritative.

The [Atelier 0.12 guide](Atelier-0.12) records the published [v0.12.0 release](https://github.com/Herbertofury/Modhunter/releases/tag/v0.12.0), actual before/after screenshots, exact Windows and browser results, full-library timings and runtime limits from [main run 43947174658](https://github.com/Herbertofury/Modhunter/actions/runs/43947174658), commit `0f2d399244dbe0cc69e82521c497f006832678c3`. These observations establish that release only; later changes must meet the same gates. Local browser execution blocked by runner IPC permissions is recorded as unexecuted, with the actual CI runtime proof identified separately.

### Reference study

The official [GameTimeDev Sims 4 Mod Manager](https://gametimedev.de/S4MM/) and its [tabs guide](https://s4mm-wiki.gametimedev.de/en/how-to-use/tabs) informed compact navigation and image-forward organization. An [official historical gallery screenshot](https://www.gametimedev.de/images/screenshot.png) illustrates tightly grouped thumbnails with adjacent names and actions; it is labeled Alpha v0.1 and is not evidence of the latest product. Modhunter adds its own source/version/review language and fantasy identity. No unmeasured superiority or performance comparison is claimed.

[Implementation](https://github.com/Herbertofury/Modhunter) · [Native release pipeline](https://github.com/Herbertofury/Modhunter/actions/workflows/native-rust.yml) · [Troubleshooting](Troubleshooting)
