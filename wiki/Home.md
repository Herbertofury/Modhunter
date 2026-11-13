<div align="center">

# ⚔️ MODHUNTER
### THE UNFINISHED WORLD

**Repair your load order · Hunt unfinished mods · Build a legend**

![Version](https://img.shields.io/badge/Rust-v0.9-cfa76d?style=for-the-badge&labelColor=17272c)
![Play](https://img.shields.io/badge/Play-Offline-70d6b0?style=for-the-badge&labelColor=17272c)
![Integrations](https://img.shields.io/badge/MO2-Optional-9abcf5?style=for-the-badge&labelColor=17272c)

</div>

![Modhunter hero](https://raw.githubusercontent.com/Herbertofury/Modhunter/main/screenshots/modhunter-menu.png)

> **Welcome, Hunter.** Your unfinished mod list is more than a maintenance backlog. It becomes your bestiary, quests and battles.

| Enter the world | Standalone requirement | Optional link |
| :-- | :-- | :-- |
| **[Portable HTML](https://github.com/Herbertofury/Modhunter/blob/main/web/Modhunter.html)** | A modern browser | MO2 localhost |
| **[Rust Desktop](Rust-Desktop.md)** | Windows and WebView2; compiled EXE/installer | MO2 localhost + Find MO2 |
| **[MO2 Plugin](MO2-Plugin.md)** | Mod Organizer 2 | Desktop or browser |

**[MO2 2.5.3b12 / Skyrim SE testing](MO2-253-Testing.md)** | **[GameSync update-checking technique map](GameSync-Update-Techniques.md)**

## ✦ One product · One Command Center

**[Download the Rust Windows app](https://github.com/Herbertofury/Modhunter/releases)** · **[Rust 0.9 installation and test evidence](Rust-Desktop.md)**

The current desktop uses Rust for the shell, parallel profile importer, metadata cache, native operations and MO2 discovery. The shared dashboard and complete games continue in WebView2; MO2's Python adapter retains its native Nexus/download/install integration. Earlier combined archives below preserve the previous Electron release series.

**[Earlier Everything Combined archive](https://drive.google.com/file/d/1UZzjHQQX3hceSWcJWFX2yygH6htzXqHE/view)** · **[Legacy All-in-One archive](https://github.com/Herbertofury/Modhunter/blob/main/dist/Modhunter-All-in-One.zip)** · **[Unified guide](Unified-Modhunter.md)**

Keep the real MO2 load order updated first. The combined interface includes the complete **Update Workshop**, **Questbound**, **AetherFX**, **Shiverfell**, **Mythforge**, **Modhunter Siege**, and **Atlas**, alongside the original 36-file historical archive. Each of the HTML, Desktop and MO2 plugin editions can launch independently. The live MO2 updater is the optional authoritative connection when verifying/queueing/installing updates.

## 🌌 The realms

| Game | Identity |
| :-- | :-- |
| **Modhunter Siege** | Survivor roguelite with 5 classes, 22 upgrades, 6 weapon evolutions and boss fights |
| **Atlas Campaign** | 12 regions, 96 authored contracts, 5 mastery tiers, 480 first-clear milestones |
| **Shiverfell Chronicles** | Persistent top-down exploration, crafting, weather, companions and quests |
| **Update Workshop** | Full MO2 profile audit, native Nexus connection, Rust GitHub/GitLab/Modrinth/Thunderstore adapters, exact-file downloads and interactive MO2 installs |

## ✧ Craft standard

**[Modhunter Design & Quality Standard](Design-System-and-Quality-Bar)** defines the updater-first information hierarchy, warm mineral visual language, restrained motion, accessible navigation, deliberate microcopy, and a strict no-clutter/no-feature-loss acceptance checklist. Shared UI changes must meet the same standard in portable HTML, desktop and MO2.

## 📖 Field guide

**[Complete Original Release Vault](Original-Releases-and-Output-Audit.md)** · **[Update Workshop](Update-Workshop.md)** · **[Updater Source Coverage](Updater-Source-Coverage.md)** · **[Getting Started](Getting-Started.md)** · **[Game Modes](Game-Modes.md)** · **[Atlas Campaign](Atlas-Campaign.md)** · **[Unfinished Bestiary](Unfinished-Bestiary.md)** · **[Rewards & Integrity](Rewards-and-Integrity.md)** · **[Architecture](Architecture.md)** · **[Troubleshooting](Troubleshooting.md)**

## 📦 Eight original releases. No missing historical ZIPs or GIFs.

**[Browse the authentic original-release vault](Original-Releases-and-Output-Audit.md)**, with every v0.1–v0.8 HTML, plugin/source archive, four original gameplay GIFs, the original victory PNG, original Atlas map, release manifests and latest checked update packages. [Download the complete 58 MB bundle from Google Drive](https://drive.google.com/file/d/1tQBnUZ_vDF7tUTtiXf8WJC41OEXNVG_5/view).

## A single world, three independent gateways

The HTML is an entirely portable play surface. The desktop app is a dedicated multi-tab Chromium shell. The MO2 plugin alone owns live profile inspection and verified update rewards. Optional loopback communication allows them to share the same active game/profile identity without requiring any of the other installations.

**Release preservation:** all 36 original v0.1–v0.8 deliverables, including the authentic GIFs and historical source/plugin ZIPs, are recovered and SHA-256 audited. The [Rust release workflow](https://github.com/Herbertofury/Modhunter/actions/workflows/native-rust.yml) adds actual Windows application and MO2 fixture gates; its run receipts distinguish them from account-specific Nexus and FOMOD acceptance.
