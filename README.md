<div align="center">

# ⚔ MODHUNTER

### The Unfinished World · A Skyrim-inspired mod-maintenance RPG

**Repair your load order. Hunt your unfinished mods. Build a legend.**

[![Edition](https://img.shields.io/badge/Edition-Rust%200.10.0-cfa76d?style=for-the-badge&labelColor=182b30)](wiki/Rust-Desktop.md)
[![MO2](https://img.shields.io/badge/MO2-Optional%20Live%20Link-78cfab?style=for-the-badge&labelColor=182b30)](wiki/MO2-Plugin.md)
[![Modes](https://img.shields.io/badge/Modes-Siege%20%7C%20Survival%20%7C%20Atlas-9bb8f0?style=for-the-badge&labelColor=182b30)](wiki/Game-Modes.md)

</div>

---

## Rust desktop 0.10.0

The new Windows app runs its desktop shell, profile import/cache engine, connection discovery and native file operations in **Rust**, with WebView2 displaying the complete existing dashboard and games. Running the compiled app requires no Node or Electron. The Python plugin remains MO2's in-process adapter for authenticated Nexus requests and its native download/installer API.

**[Windows releases](https://github.com/Herbertofury/Modhunter/releases)** · **[Install and use the Rust app](wiki/Rust-Desktop.md)** · **[Build and test results](https://github.com/Herbertofury/Modhunter/actions/workflows/native-rust.yml)**

- **Find MO2** discovers running plugin sessions and validates their protocol and profile before connecting. A lightweight health request replaces full-dashboard connection probing.
- Parallel, cached imports support portable and split-path MO2 configurations. In reproducible Linux fixtures, importing 2,500 mods took a median **70.78 ms** in Rust versus **477.03 ms** in the previous Node importer; 10,000 mods took **330.42 ms** versus **1,629.28 ms**. These include fresh metadata-content verification and root plugin inventory on generated fixtures without ESP payloads; they are not deep-audit, Windows GUI startup or Nexus download timings. [Raw measurements and method](native/evidence/import-comparison.json).
- Profile changes invalidate stale scans and delayed UI replies. Expired queued commands cannot execute later. Requests share exact file lookups, metadata caches and connection resources, while search and background polling avoid redundant work.
- Windows CI builds the executable and installer and launches the actual WebView2 app; separate browser and MO2 fixture checks enforce their own contracts. Real account downloads, FOMOD decisions and a user's game setup remain environment-specific acceptance checks.

### 0.10.0 Watchtower: site sign-ins and page tracking

**Watchtower** brings page tracking and site-session management into the Rust desktop. Add a release page, creator feed or changelog; choose an isolated site sign-in when the page needs your account. The first successful read establishes a baseline. Later checks show changed text, retained history and an explicit **Mark seen** action with guarded undo. Folders, tags, search, site filters, pause/mute controls and recoverable removal keep the full list manageable.

Sign in on the website itself. Named sessions use separate WebView2 profiles; passwords and cookies stay in the browser engine. Remembered sessions survive restarts. Turning off **Remember this sign-in** uses a temporary profile on disk for the current app run, with a fresh profile next launch. Reset closes that session's windows and invalidates old work. Session confirmation is your indication that you are ready to check; actual page access still has to succeed.

Checks are manual by default. Optional launch and background due checks run while Modhunter is open, with bounded work and per-site pacing. CSS region/ignore selectors help compare the useful page content. Import previews validate and deduplicate pages before adding them; exports contain watchlist data, never browser credentials. Last-good observations remain available after failures, cancellation or login challenges.

This independently implements useful behavior identified in a static audit of **WhimWatch 0.4.2**. Its Sims-specific installer is not used: Nexus downloads, exact-file release review, FOMOD choices and native installation remain with MO2 and the existing Update Workshop. A page changing does not prove a compatible mod update. See the [Watchtower guide](wiki/Watchtower.md) and [exact reference audit, transfer decisions and release gates](native/evidence/whimwatch-parity-0.10.0.md).

### 0.9.2 refresh speed and reliable upgrades

- Repeated live MO2 reads share one complete encoded snapshot for unchanged state; ordinary progress saves avoid rebuilding the catalog. On the 10,000-mod synthetic HTTP fixture, repeated complete snapshots fell from **285.561 ms to 4.217 ms**, and one-row progress saves from **235.108 ms to 1.288 ms**. Source, profile, metadata and audit changes invalidate the response, and source-health freshness stays bounded. These local component timings exclude native MO2, browser rendering, Nexus/downloads and installation. [Raw samples and scope](native/evidence/mo2-catalog-delivery-comparison-0.9.2.json).
- The full checklist and Workshop cache unchanged row markup as well as DOM nodes. Source expiry/cooldown changes refresh both row status and search results. Download buttons retain focus through ticket changes and reordering; all records remain available.
- **CANCEL** interrupts native imports, refreshes and deep plugin audits while preserving the last complete catalog. **CANCELING…** stays visible until workers settle, then the same engine is ready to retry. Cancellation checkpoints retain fresh hashes and complete audit evidence; the [0.9.2 equivalence comparison](native/evidence/plugin-audit-comparison-0.9.2.json) shows audit medians within roughly 2% of 0.9.1 on its four synthetic workloads.
- Source-link edits synchronize before update execution. Clearing an override restores the native MO2 identity. Delayed profile/source replies and canceled scans cannot replace newer state; Chronicle loading preserves edits and retries failed reads without replacing unseen server progress.
- The MO2 installer verifies packaged file checksums and build/plugin version agreement before replacing an installation. Damaged, incomplete and mixed packages leave the prior plugin and its progress intact. The release gate verifies every internal ZIP checksum and rejects Windows filename collisions.

[0.9.2 measurements, verification and remaining acceptance checks](native/evidence/readiness-0.9.2.md)

### 0.9.1 responsiveness and update execution

- Rust plugin audits stream payloads directly into fresh SHA-256 checks. On identical synthetic Linux fixtures, 100,000 small records took **11.68 ms versus 150.30 ms** before; larger payloads remain primarily hashing/I/O work. [Exact workloads, raw samples and equivalence checks](native/evidence/plugin-audit-comparison-0.9.1.json).
- One-mod Quick Update reads that mod instead of copying the entire profile. On the cached ambiguous-file fixture, local preparation for 10,000 mods fell from **143.853 ms to 0.168 ms**. This excludes Nexus requests, native MO2 and installation. [Raw samples and scope](native/evidence/mo2-preflight-comparison.json).
- Changing worker counts releases idle pools; import and audit reuse the same workers. Find MO2 considers the newest descriptors even after hundreds of old sessions and reuses the validated connection result.
- Checklist and Workshop updates replace only changed rows while retaining the full list, unrelated controls and keyboard focus. Large Workshop searches coalesce within one animation frame; no records are capped or virtualized.
- Audits keep at most five Nexus requests submitted per scan. Cancelled/profile-superseded work cannot publish stale cache or execute an old reviewed download. A prior installed ticket remains history and allows the next release after MO2 observes the installed-version change.

[0.9.1 verification and remaining acceptance checks](native/evidence/readiness-0.9.1.md)

## Per-mod ESL, conversion and source checks

The checklist and Update Workshop now show **actual winning light/full plugin state**, **ESLifier changes**, **missing/inactive/late masters**, and **checked Nexus bug-tab evidence** on the mod row. The inspector adds plugin copies, timestamps, fresh hash comparisons and next steps. The Mod health filter and CSV/update-plan exports carry these facts too. [Full guide](wiki/Mod-Intelligence.md).

Rust performs bounded TES4 record/ID inspection and fresh SHA-256 analysis. Live MO2 supplies the authoritative winning paths and load order. ESLifier's official MO2 setting is recognized, with native folder selection as a fallback. After profile/mod/plugin changes, the background audit invalidates old results and rechecks configured conversions. A changed original beneath an older converted output is visible on both relevant rows.

**Flag-only candidate**, **compaction review**, **blocked**, **already light** and **unknown** stay distinct. A conversion check establishes consistency of the scanned plugin bytes, not universal save, script, asset or dependency safety. Public bug checks retain the actual displayed count and separate zero, unavailable, unobserved and explicitly restricted tab states; no author action is inferred from a missing tab. Detailed reports load for the inspected row, keeping the main catalog compact.

## Rust source adapters and release intelligence

**GitHub, GitLab, Modrinth and Thunderstore** now have Rust public API adapters alongside MO2's native Nexus connection. They retain exact project/release/file identities, version evidence, release notes, publisher information, declared dependencies, compatibility context and published hashes when the provider supplies them. Nested GitLab groups and provider-specific game/loader/community checks are supported. Source chips, per-mod release details and a profile-wide source-coverage table show what was established and which action is available. [Provider matrix and workflow](wiki/Updater-Source-Coverage.md).

The file picker accepts vetted release archives from all four adapters. Queueing, native dispatch and install preflight recheck the reviewed file fingerprint, including its project, release, URL, size and published hashes. Newly observed asset changes under an unchanged release ID require a new review. Published hashes are metadata evidence; they do not imply independent verification of downloaded bytes.

Parallel requests, project deduplication, ETag/Last-Modified caches, provider cooldowns and bounded scan deadlines keep checks responsive. The catalog carries compact counts; large file lists and changelogs load for the inspected mod. Manual/account-only sources remain useful through their exact links and explicit capability state. CurseForge is labeled as requiring a provider key; JAR/MRPACK files and provider-managed Creations/Workshop installations keep their appropriate original workflows.

## One Modhunter. Everything combined.

The **[earlier EVERYTHING Combined archive](https://drive.google.com/file/d/1UZzjHQQX3hceSWcJWFX2yygH6htzXqHE/view)** and **[legacy All-in-One.zip](dist/Modhunter-All-in-One.zip)** preserve the Electron edition, standalone HTML, optional MO2 plugin, all game modes, source and Wiki from the previous release series. The Rust release is distributed separately through the Windows releases above. **Mod updating remains the primary purpose:** the shared interface includes Questbound achievements, AetherFX, Shiverfell/Mythforge survival, Modhunter Siege and Atlas. All [36 original release outputs](wiki/Original-Releases-and-Output-Audit.md) remain separately preserved.

The **[Unified Command Center guide](wiki/Unified-Modhunter.md)** documents the independent entry points and updater-first workflow. The new native MO2 smoke workflow targets the official **MO2 2.5.2** portable release. Earlier references to 2.5.3 describe a compatibility target, not a verified release.

## Visual direction and quality contract

**[Atelier design system & AAA-quality acceptance criteria](wiki/Design-System-and-Quality-Bar.md)** govern every surface: updater-first hierarchy, purposeful typography, restrained colors, refined interactions, responsive light/dark themes, accessible navigation and zero lost functionality. This is a testable target, not a claim that unperformed native Windows tests passed. The same compiled dashboard ships across HTML, desktop and MO2.

## Three ways to enter. No mandatory dependencies between them.

| Component | What it does | Needs another component? |
|---|---|---|
| **Rust Desktop App** | Native WebView2 tabs, **Import MO2** reads `modlist.txt` and metadata without the plugin, **Find MO2** connects live sessions | **No.** Compiled Windows app runs independently; the installer supplies WebView2 if missing |
| **Standalone HTML** | Full mod update dashboard + both games in a single portable file | **No.** Open locally in a modern browser. Progress and rewards are **practice only** without live MO2 |
| **MO2 Plugin** | Reads current profile, verifies updates, queues Nexus downloads, opens standard MO2 installer, exact row navigation, official Forge Sigil and boss-bounty economy | **No.** Runs in MO2 and serves its own browser dashboard |

**Together:** the app connects to MO2's `127.0.0.1` session so the same Game/Mod Vault tabs become live, with correct profile identities and MO2-authorized rewards. No cloud account or OpenAI API key needed.

## Every original deliverable, recovered

All **36 authentic files across v0.1–v0.8** are preserved unchanged in the **[original-release vault](wiki/Original-Releases-and-Output-Audit.md)**, including 16 historical plugin/source/desktop ZIPs, four original animated GIFs, Questbound's victory PNG, the Atlas map, and all versioned HTML builds. The originals are verified against all **16 available SHA-256 manifest entries**. The three later complete updater checkpoints are also preserved. **[Download the complete historical + current archive](https://drive.google.com/file/d/1tQBnUZ_vDF7tUTtiXf8WJC41OEXNVG_5/view)** and see its accompanying [machine-readable manifest](https://drive.google.com/file/d/1KxVD8EZ92eIP0TpPNC1at3B8WnEOF7tR/view).

## Start here

1. **Just play:** download [`web/Modhunter.html`](web/Modhunter.html), open it, and click **Modhunter Siege** or **Survival Realm**. This always works in offline practice.
2. **Use the Rust desktop:** download **Modhunter-Rust-Windows.zip** from [Windows releases](https://github.com/Herbertofury/Modhunter/releases), extract it and run `Modhunter.exe`, or use the NSIS installer. Click **Import MO2** and select `profiles/<name>/modlist.txt` for a read-only local snapshot. **Instance INI** and **Mods path** support custom layouts.
3. **Connect live MO2:** close MO2, run the portable package's `Install_MO2_Plugin.bat`, and select the directory containing `ModOrganizer.exe`. Restart MO2, open **Tools → Update Arsenal - Quest Board**, then click **Find MO2** in Modhunter. The prior plugin is backed up outside MO2's plugin search directory.

> **Evidence:** see [Rust Desktop](wiki/Rust-Desktop.md) for the exact native, browser, lifecycle and performance gates. The content catalogue describes 480 campaign clear conditions, not 80 hours of independently playtested authored content. An automated fixture does not prove account-specific Nexus downloads or compatibility of a particular mod list.

## SSS Update Workshop: exact-file guidance and effortless review

The latest polish pass adds **Review next update** for sequential, hands-on MO2 maintenance, searchable **full Nexus file lists**, and conservative **version-matched main-file highlighting** only when current Nexus metadata justifies it. Alternate/older/optional files remain accessible. Multiple equally plausible main files never receive a misleading automatic choice; unknown runtime compatibility stays visibly unresolved. You can remove a reviewed file without downloading, preview mod names before queueing, and see clearer native MO2 download/install tickets. Server-side submission rejects changed target versions when the reviewed release is specified. **No unattended installs** or game-file changes are introduced.

Automated evidence includes Python/HTTP guards, a simulated MO2 lifecycle, browser-interaction proof in Chromium and a large-list ticket-lookup performance benchmark. The native workflow targets official MO2 **2.5.2** and records its tested commit and scope separately. See the [SSS review workflow](wiki/Update-Workshop.md) and [development tests](wiki/Development-and-Testing.md).

**Open mods without detours:** Click a mod name in either the installed-mod list or Update Workshop to open its exact original Nexus/GitHub/other source. A small adjacent **⌖** button still reveals the mod inside MO2. Unknown source identities are labeled as searches instead of pretending to be verified project pages. Custom URLs in the inspector can be previewed and copied before saving. These links work in all three editions and never interfere with **Update now**.

### Faster recoveries without more approval screens

The live MO2 queue now shows ready, running, failed and verified counts. Click **Install all ready** to open ready archives in MO2 one at a time, preserving its real FOMOD choices. A failed ticket offers **Retry now** to revalidate and requeue the same reviewed file, or **Choose another file** when a release changed. Instant native completion events are reconciled, and the queue refreshes even when it was previously empty. See the [recovery workflow](wiki/Update-Workshop.md).

## Update Workshop: maintain an entire MO2 profile

**SSS updater cockpit:** the live Workshop offers a reviewed-file manifest and per-archive installation briefing when you want to inspect a batch or file. An unambiguous, freshly validated quick update can proceed directly to MO2's download manager and interactive installer. Every install is rechecked against the exact profile, game/runtime, mod version, target release, and newly introduced warnings. Downloads remain in MO2; FOMOD, overwrite choices and installed mods remain under your control. One-click dashboard lanes surface candidates, manual sources, compatibility issues and ready-to-install archives, with `Alt+U` to focus the Workshop and `Alt+R` to review the next update. A profile backup covers load-order text files, **not** prior mod directories.

The **[Update Workshop](wiki/Update-Workshop.md)** ([source coverage](wiki/Updater-Source-Coverage.md)) keeps all mods in full-width review lanes, including missing-source mods. In live MO2, reuse MO2's authenticated Nexus connection, inspect exact Nexus files or public release archives, compare source evidence and queue a reviewed batch. Each download goes through the native MO2 download manager; each installation stays interactive with profile load-order backups. Patreon, LoversLab, Mod DB and other gated sources retain exact links and manual-review status. The Rust desktop can import a user-selected portable or split-path MO2 profile **without the plugin** for local inspection; standalone HTML remains an offline checklist. Native downloads and installations require the MO2 plugin, and offline checklist clicks never award verified updates.

## What makes it different?

**Modhunter Siege:** a survivor roguelite with five hero classes, automatic squad attacks, 22 upgrade paths, six weapon evolutions, timed boss battles and an Unfinished Bestiary made from real outdated MO2 mods. Follower mods can become named rivals, weapons become cursed wielders, armor becomes colossi, and spell mods become casters.

**The Atlas:** 12 authored regions × 8 distinct chapter missions × 5 mastery tiers = **480 first-clear challenges**. Each mission has an objective, regional hazard and lore; permanent legacy perks use *game-only Embers*, not fabricated MO2 currency.

**Shiverfell:** a persistent top-down open-world survival game, with quests, resource gathering, crafting, companions, weather, region discovery, the Dawnshard world-boss quest, and two roguelite modes.

**The Forge:** rank XP, animations, achievements, fireworks, runic selection highlights and 10 Forge Sigils per real verified update. Killing an identified outdated-mod boss awards run points; *actually updating the mod* unlocks the larger verified bounty.

## Project map

| Source | Description |
|---|---|
| [`native/`](native/) | Rust desktop, profile/cache engine, CLI, Windows packaging and native smoke tests |
| [`mo2_update_arsenal/`](mo2_update_arsenal/) | MO2 tool plugin, local API, Nexus scanner, identity and rewards |
| [`mo2_update_arsenal/web/`](mo2_update_arsenal/web/) | Editable HTML, CSS and JavaScript game engines |
| [`desktop/`](desktop/) | Preserved earlier Electron source and comparison importer |
| [`wiki/`](wiki/) | Full Wiki source, styled navigation, screenshots, guides and architecture |
| [`tests/`](tests/) | Python and real Chromium interaction/visual test suite |
| [`dist/`](dist/) | Preserved release packages from the earlier Electron series; current Rust packages are in GitHub Releases |

📖 **[Read the Modhunter Wiki](wiki/Home.md)** · [Install & Connect](wiki/Getting-Started.md) · [Atlas Campaign](wiki/Atlas-Campaign.md) · [The Unfinished Bestiary](wiki/Unfinished-Bestiary.md) · [Verification and Safety](wiki/Rewards-and-Integrity.md)

The canonical [`wiki/`](wiki/) pages are synchronized to the separate **[live GitHub Wiki](https://github.com/Herbertofury/Modhunter/wiki)** by the repository's Wiki workflow. Its status is visible in GitHub Actions.

### Fast MO2 updates: one click, no confirmation maze

With the MO2 plugin connected, click **ϟ Update now** on a newer mod. A uniquely version-matched main Nexus file (or one eligible public release archive) goes directly to MO2 Downloads, then opens **MO2's interactive installer** when it finishes. The standard FOMOD prompts remain available; Modhunter skips its extra confirmations. For multiple editions or meaningful compatibility risks, **Choose file** opens immediately, with the release choices preserved. **Queue matched updates** downloads unambiguous matches in the background without flooding you with installers. Existing reviewed selections dispatch immediately, while the manifest and install briefing are optional. See [Update Workshop](wiki/Update-Workshop.md).



## Competing apps and differentiation

[Source-linked comparison and evaluation backlog](wiki/Competing-Apps.md) records comparable tools, opportunities to test, evidence requirements and dated review status. Planned differentiation is not a claim of measured superiority.
