<div align="center">

# ⚔ MODHUNTER

### The Unfinished World · A Skyrim-inspired mod-maintenance RPG

**Repair your load order. Hunt your unfinished mods. Build a legend.**

[![Edition](https://img.shields.io/badge/Edition-Atlas%200.8-cfa76d?style=for-the-badge&labelColor=182b30)](wiki/Atlas-Campaign.md)
[![MO2](https://img.shields.io/badge/MO2-Optional%20Live%20Link-78cfab?style=for-the-badge&labelColor=182b30)](wiki/MO2-Plugin.md)
[![Modes](https://img.shields.io/badge/Modes-Siege%20%7C%20Survival%20%7C%20Atlas-9bb8f0?style=for-the-badge&labelColor=182b30)](wiki/Game-Modes.md)

</div>

---

## Three ways to enter. No mandatory dependencies between them.

| Component | What it does | Needs another component? |
|---|---|---|
| **Desktop App** | Real Chromium tabs for Mod Vault, Modhunter Siege and Shiverfell; optional connection to running MO2 | **No.** Standalone offline mode; requires Electron/Node runtime to start the development source package |
| **Standalone HTML** | Full mod update dashboard + both games in a single portable file | **No.** Open locally in a modern browser. Progress and rewards are **practice only** without live MO2 |
| **MO2 Plugin** | Reads current profile, verifies updates, queues Nexus downloads, opens standard MO2 installer, exact row navigation, official Forge Sigil and boss-bounty economy | **No.** Runs in MO2 and serves its own browser dashboard |

**Together:** the app connects to MO2's `127.0.0.1` session so the same Game/Mod Vault tabs become live, with correct profile identities and MO2-authorized rewards. No cloud account or OpenAI API key needed.

## Start here

1. **Just play:** download [`web/Modhunter.html`](web/Modhunter.html), open it, and click **Modhunter Siege** or **Survival Realm**. This always works in offline practice.
2. **Use MO2:** download the [`dist/Modhunter-MO2-Plugin.zip`](dist/Modhunter-MO2-Plugin.zip), unzip it and run `Install_to_MO2.bat`; restart MO2 and open **Tools → Update Arsenal - Quest Board**.
3. **Use the desktop app:** download [`dist/Modhunter-Desktop-Source.zip`](dist/Modhunter-Desktop-Source.zip), install Node.js, run `Run_ModHunter.bat` in `desktop/`, and optionally paste the MO2 plugin's loopback URL into **Connect**.

> **Current stage:** v0.8 development build. Browser gameplay and Python tests are exercised. A prebuilt Windows executable and native Windows MO2 integration have **not** been verified in this environment. The content catalogue describes 480 campaign clear conditions, not 80 hours of independently playtested authored content.

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

The pages in `wiki/` are also ready to sync to the separate [GitHub Wiki](https://github.com/Herbertofury/Modhunter/wiki) using the included `Sync GitHub Wiki` workflow after that Wiki has an initial Home page. A repository `wiki/` directory alone does not create the live GitHub Wiki.
