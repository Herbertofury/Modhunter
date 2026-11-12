<div align="center">

# ⚔ MODHUNTER

### The Unfinished World · A Skyrim-inspired mod-maintenance RPG

**Repair your load order. Hunt your unfinished mods. Build a legend.**

[![Edition](https://img.shields.io/badge/Edition-Atlas%200.8-cfa76d?style=for-the-badge&labelColor=182b30)](wiki/Atlas-Campaign.md)
[![MO2](https://img.shields.io/badge/MO2-Optional%20Live%20Link-78cfab?style=for-the-badge&labelColor=182b30)](wiki/MO2-Plugin.md)
[![Modes](https://img.shields.io/badge/Modes-Siege%20%7C%20Survival%20%7C%20Atlas-9bb8f0?style=for-the-badge&labelColor=182b30)](wiki/Game-Modes.md)

</div>

---

## One Modhunter. Everything combined.

**[Download Modhunter EVERYTHING Combined]( https://drive.google.com/file/d/1UZzjHQQX3hceSWcJWFX2yygH6htzXqHE/view)** includes the current working editions, source, documentation and all 36 authentic historical outputs. Alternatively, **[Download the complete Modhunter All-in-One.zip](dist/Modhunter-All-in-One.zip)**. One extracted folder contains a runnable standalone HTML, an Electron desktop edition, the optional MO2 updater plugin, all game modes, full source, and the Wiki. **Mod updating remains the primary purpose:** the same interface includes Questbound achievements, AetherFX, Shiverfell/Mythforge survival, Modhunter Siege and Atlas. No historical feature is replaced or removed, and all [36 original release outputs](wiki/Original-Releases-and-Output-Audit.md) remain separately preserved.

The **[Unified Command Center guide](wiki/Unified-Modhunter.md)** documents all three independent entry points and the updater-first workflow for **MO2 2.5.x, including 2.5.3 as a target**. Native Windows 2.5.3 integration testing is still required; a successful source build is not an installation guarantee.

## Visual direction and quality contract

**[Atelier design system & AAA-quality acceptance criteria](wiki/Design-System-and-Quality-Bar.md)** govern every surface: updater-first hierarchy, purposeful typography, restrained colors, refined interactions, responsive light/dark themes, accessible navigation and zero lost functionality. This is a testable target, not a claim that unperformed native Windows tests passed. The same compiled dashboard ships across HTML, desktop and MO2.

## Three ways to enter. No mandatory dependencies between them.

| Component | What it does | Needs another component? |
|---|---|---|
| **Desktop App** | Real Chromium tabs, **Import MO2 Profile** (reads actual `modlist.txt` and installed metadata without the plugin), optional live MO2 updating | **No.** Native read-only import and offline games work alone; Electron/Node starts the source package |
| **Standalone HTML** | Full mod update dashboard + both games in a single portable file | **No.** Open locally in a modern browser. Progress and rewards are **practice only** without live MO2 |
| **MO2 Plugin** | Reads current profile, verifies updates, queues Nexus downloads, opens standard MO2 installer, exact row navigation, official Forge Sigil and boss-bounty economy | **No.** Runs in MO2 and serves its own browser dashboard |

**Together:** the app connects to MO2's `127.0.0.1` session so the same Game/Mod Vault tabs become live, with correct profile identities and MO2-authorized rewards. No cloud account or OpenAI API key needed.

## Every original deliverable, recovered

All **36 authentic files across v0.1–v0.8** are preserved unchanged in the **[original-release vault](wiki/Original-Releases-and-Output-Audit.md)**, including 16 historical plugin/source/desktop ZIPs, four original animated GIFs, Questbound's victory PNG, the Atlas map, and all versioned HTML builds. The originals are verified against all **16 available SHA-256 manifest entries**. The three later complete updater checkpoints are also preserved. **[Download the complete historical + current archive](https://drive.google.com/file/d/1tQBnUZ_vDF7tUTtiXf8WJC41OEXNVG_5/view)** and see its accompanying [machine-readable manifest](https://drive.google.com/file/d/1KxVD8EZ92eIP0TpPNC1at3B8WnEOF7tR/view).

## Start here

1. **Just play:** download [`web/Modhunter.html`](web/Modhunter.html), open it, and click **Modhunter Siege** or **Survival Realm**. This always works in offline practice.
2. **Use MO2:** download the [`dist/Modhunter-MO2-Plugin.zip`](dist/Modhunter-MO2-Plugin.zip), unzip it and run `Install_to_MO2.bat`; restart MO2 and open **Tools → Update Arsenal - Quest Board**.
3. **Use the desktop app:** download [`dist/Modhunter-Desktop-Source.zip`](dist/Modhunter-Desktop-Source.zip), install Node.js, run `Run_ModHunter.bat` in `desktop/`. Click **⌁ Import MO2** and select `profiles/<name>/modlist.txt` for an actual read-only profile without the plugin. Paste a plugin loopback URL into **Connect** only when you want native update actions.

> **Current stage:** v0.8 development build. Browser gameplay, Python/HTTP unit tests, and the standalone desktop-import Node test suite are covered by automated checks. A prebuilt Windows executable and native Windows MO2 integration have **not** been verified in this environment. The content catalogue describes 480 campaign clear conditions, not 80 hours of independently playtested authored content.

## SSS Update Workshop: exact-file guidance and effortless review

The latest polish pass adds **Review next update** for sequential, hands-on MO2 maintenance, searchable **full Nexus file lists**, and conservative **version-matched main-file highlighting** only when current Nexus metadata justifies it. Alternate/older/optional files remain accessible. Multiple equally plausible main files never receive a misleading automatic choice; unknown runtime compatibility stays visibly unresolved. You can remove a reviewed file without downloading, preview mod names before queueing, and see clearer native MO2 download/install tickets. Server-side submission rejects changed target versions when the reviewed release is specified. **No unattended installs** or game-file changes are introduced.

Automated evidence includes Python/HTTP guards, a simulated MO2 lifecycle, browser-interaction proof in Chromium and a large-list ticket-lookup performance benchmark. The Windows MO2 2.5.3 native integration gate is still unverified. See the [SSS review workflow](wiki/Update-Workshop.md) and [development tests](wiki/Development-and-Testing.md).

**Open mods without detours:** Click a mod name in either the installed-mod list or Update Workshop to open its exact original Nexus/GitHub/other source. A small adjacent **⌖** button still reveals the mod inside MO2. Unknown source identities are labeled as searches instead of pretending to be verified project pages. Custom URLs in the inspector can be previewed and copied before saving. These links work in all three editions and never interfere with **Update now**.

## Update Workshop: maintain an entire MO2 profile

**SSS updater cockpit:** the live Workshop now presents a complete reviewed-file manifest before queueing any downloads and a polished per-archive installation briefing before opening MO2. Every install is rechecked against the exact profile, game/runtime, mod version, target release, and newly introduced warnings. Downloads remain in MO2; FOMOD, overwrite choices and installed mods remain under your control. One-click dashboard lanes surface candidates, manual sources, compatibility issues and ready-to-install archives, with `Alt+U` to focus the Workshop and `Alt+R` to review the next update. A profile backup covers load-order text files, **not** prior mod directories.

The new **[Update Workshop](wiki/Update-Workshop.md)** ([source coverage](wiki/Updater-Source-Coverage.md)) keeps all mods in full-width review lanes, including missing-source mods. In live MO2, reuse MO2's authenticated Nexus connection, inspect exact Nexus files and public GitHub release assets, check GitHub/GitLab release versions, acknowledge compatibility risks, and queue a reviewed batch. Each download still goes through the native MO2 download manager; each installation stays interactive with profile load-order backups. Patreon, LoversLab, Mod DB and other gated sources retain exact links and unverified/manual-review status rather than imaginary update claims. The desktop edition can import an actual user-selected portable MO2 profile **without the plugin** for local inspection; standalone HTML remains an offline checklist. Native downloads and installations require the MO2 plugin, and offline checklist clicks never award verified updates.

## What makes it different?

**Modhunter Siege:** a survivor roguelite with five hero classes, automatic squad attacks, 22 upgrade paths, six weapon evolutions, timed boss battles and an Unfinished Bestiary made from real outdated MO2 mods. Follower mods can become named rivals, weapons become cursed wielders, armor becomes colossi, and spell mods become casters.

**The Atlas:** 12 authored regions × 8 distinct chapter missions × 5 mastery tiers = **480 first-clear challenges**. Each mission has an objective, regional hazard and lore; permanent legacy perks use *game-only Embers*, not fabricated MO2 currency.

**Shiverfell:** a persistent top-down open-world survival game, with quests, resource gathering, crafting, companions, weather, region discovery, the Dawnshard world-boss quest, and two roguelite modes.

**The Forge:** rank XP, animations, achievements, fireworks, runic selection highlights and 10 Forge Sigils per real verified update. Killing an identified outdated-mod boss awards run points; *actually updating the mod* unlocks the larger verified bounty.

## Project map

| Source | Description |
|---|---|
| [`mo2_update_arsenal/`](mo2_update_arsenal/) | MO2 tool plugin, local API, Nexus scanner, identity and rewards |
| [`mo2_update_arsenal/web/`](mo2_update_arsenal/web/) | Editable HTML, CSS and JavaScript game engines |
| [`desktop/`](desktop/) | Native Chromium-tabbed Electron app source and install script |
| [`wiki/`](wiki/) | Full Wiki source, styled navigation, screenshots, guides and architecture |
| [`tests/`](tests/) | Python and real Chromium interaction/visual test suite |
| [`dist/`](dist/) | Portable HTML and complete release packages |

📖 **[Read the Modhunter Wiki](wiki/Home.md)** · [Install & Connect](wiki/Getting-Started.md) · [Atlas Campaign](wiki/Atlas-Campaign.md) · [The Unfinished Bestiary](wiki/Unfinished-Bestiary.md) · [Verification and Safety](wiki/Rewards-and-Integrity.md)

The canonical [`wiki/`](wiki/) pages are synchronized to the separate **[live GitHub Wiki](https://github.com/Herbertofury/Modhunter/wiki)** by the repository's Wiki workflow. Its status is visible in GitHub Actions.

### Fast MO2 updates: one click, no confirmation maze

With the MO2 plugin connected, click **ϟ Update now** on a newer mod. A uniquely version-matched main Nexus file (or a single verified GitHub release archive) goes directly to MO2 Downloads, then opens **MO2's interactive installer** when it finishes. The standard FOMOD prompts remain available; Modhunter skips its extra confirmations. For multiple editions or meaningful compatibility risks, **Choose file** opens immediately, with every release option preserved. **Queue matched updates** downloads all unambiguous matches in the background without flooding you with installers. Existing reviewed selections dispatch immediately, while the manifest and install briefing are optional. See [Update Workshop](wiki/Update-Workshop.md).

