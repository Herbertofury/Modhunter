<div align="center">

# ⚔️ MODHUNTER
### THE UNFINISHED WORLD

**Browse your mods · Review every update · Keep your world intact**

![Version](https://img.shields.io/badge/Rust-v0.13.0-b9dcff?style=for-the-badge&labelColor=000000)
![Play](https://img.shields.io/badge/Play-Offline-a8b8d0?style=for-the-badge&labelColor=000000)
![Integrations](https://img.shields.io/badge/MO2-Optional-9abcf5?style=for-the-badge&labelColor=000000)

</div>

![Modhunter Gallery with authentic project artwork](https://raw.githubusercontent.com/Herbertofury/Modhunter/main/screenshots/v0.12.1/library-gallery-dark.png)

*Earlier 0.12.1 shared-dashboard capture. Public project names and icons are authentic; installed versions and update states are test fixtures. The 0.13.0 library adds the persistent bottom search and visible Bugs/health columns.*

> **Welcome to the Mod Vault.** Browse your complete collection, review exact sources and files, and keep verified backups when installing updates. Your unfinished world still holds quests, battles and rewards when you want to explore it.

| Choose an entry point | Requirement | Optional connection |
| :-- | :-- | :-- |
| **[Rust Desktop](Rust-Desktop.md)** | Windows and WebView2; installer or portable app | Find MO2 for live updating |
| **[MO2 Plugin](MO2-Plugin.md)** | Mod Organizer 2 | Desktop or browser |
| **[Standalone HTML](Standalone-HTML.md)** | A modern browser | MO2 local bridge |

**[MO2 2.5.3b12 / Skyrim SE testing](MO2-253-Testing.md)** | **[GameSync update-checking technique map](GameSync-Update-Techniques.md)**

## Library and updates

**[Daily use and setup](Daily-Use)** · **[MO2 instance discovery and plugin setup](MO2-Instances)**

**[Windows installer](https://github.com/Herbertofury/Modhunter/releases/download/v0.13.0/Modhunter_0.13.0_x64-setup.exe)** · **[Portable package](https://github.com/Herbertofury/Modhunter/releases/download/v0.13.0/Modhunter-Rust-Windows.zip)** · **[Release, components and checksums](https://github.com/Herbertofury/Modhunter/releases/tag/v0.13.0)**

Open **MO2 Instances** to find installed and portable applications, inspect their instances/profiles, and install the matching plugin into your chosen destinations. Both desktop distributions include the complete verified plugin resource, with per-target results, preserved data and previous-plugin backups.

The complete library fills the available window. Its compact bottom search/control bar stays available while rows scroll beneath a sticky header. Bugs, health and notes are visible, column choices are remembered, and MO2 separator colors are honored. Source checking distinguishes each installed file or addon from the main release on the same page.

The release's verification archive records exact-source Windows/WebView2, installer, browser, MO2 and full-library performance evidence. The [0.12.1 OLED guide](OLED-0.12.1) and [Atelier guide](Atelier-0.12) preserve earlier design captures and measurements.

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

**[Modhunter Design & Quality Standard](Design-System-and-Quality-Bar)** defines the updater-first information hierarchy, OLED-black utility palette and authored colored chapters, restrained motion, accessible navigation, deliberate microcopy, and a strict no-clutter/no-feature-loss acceptance checklist. Shared UI changes must meet the same standard in portable HTML, desktop and MO2.

## 📖 Field guide

**[Complete Original Release Vault](Original-Releases-and-Output-Audit.md)** · **[Update Workshop](Update-Workshop.md)** · **[Updater Source Coverage](Updater-Source-Coverage.md)** · **[Getting Started](Getting-Started.md)** · **[Game Modes](Game-Modes.md)** · **[Atlas Campaign](Atlas-Campaign.md)** · **[Unfinished Bestiary](Unfinished-Bestiary.md)** · **[Rewards & Integrity](Rewards-and-Integrity.md)** · **[Architecture](Architecture.md)** · **[Troubleshooting](Troubleshooting.md)**

## 📦 Eight original releases. No missing historical ZIPs or GIFs.

**[Browse the authentic original-release vault](Original-Releases-and-Output-Audit.md)**, with every v0.1–v0.8 HTML, plugin/source archive, four original gameplay GIFs, the original victory PNG, original Atlas map, release manifests and latest checked update packages. [Download the complete 58 MB bundle from Google Drive](https://drive.google.com/file/d/1tQBnUZ_vDF7tUTtiXf8WJC41OEXNVG_5/view).

## A single world, three independent gateways

The standalone HTML carries the complete dashboard and games in a portable browser file. The desktop app uses Rust for its window and native operations, with WebView2 displaying the shared interface. The MO2 plugin owns live profile inspection and verified update rewards. Optional loopback communication allows them to share the same active game/profile identity without requiring any of the other installations.

**Release preservation:** all 36 original v0.1–v0.8 deliverables, including the authentic GIFs and historical source/plugin ZIPs, are recovered and SHA-256 audited. The [Rust release workflow](https://github.com/Herbertofury/Modhunter/actions/workflows/native-rust.yml) adds actual Windows application and MO2 fixture gates; its run receipts distinguish them from account-specific Nexus and FOMOD acceptance.
