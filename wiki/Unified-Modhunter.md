# ⚔ MODHUNTER · ONE UNIFIED COMMAND CENTER

> **Primary purpose:** Keep your real Mod Organizer 2 mods up to date, with an elegant, traceable, profile-specific workflow. Questbound, AetherFX, Shiverfell, Mythforge, Modhunter Siege and the Atlas are all part of the **same product**, not separate replacement apps.

![Modhunter dashboard](https://raw.githubusercontent.com/Herbertofury/Modhunter/main/screenshots/modhunter-menu.png)

## One product. Three independent entry points.

| Entry point | Works by itself | Mod review | Live MO2 updating | World and game content |
|---|---|---|---|---|
| **Standalone HTML** | Yes, a self-contained HTML file | Complete offline checklist, search, source links, export | Not without MO2 connection; never fabricates update status | Questbound, AetherFX, Shiverfell/Mythforge, Siege/Atlas |
| **Desktop App** | Yes, Node + Electron installed | All offline features **plus** on-demand read-only MO2 modlist import | Connect optionally to MO2 plugin's tokenized loopback session | Same HTML, native tabs for mods, Siege, Shiverfell |
| **MO2 Python Tool** | Yes, when loaded by MO2 | Actual active profile, installed versions, exact Nexus IDs, active game runtime | Nexus and public release checks; MO2 downloads, interactive installs, backups, rescan | Same bundled HTML, verified Forge Sigils and actual mod-based bosses |

All three editions ship the **same byte-for-byte self-contained dashboard**. Desktop never needs the MO2 plugin to play games or inspect an imported portable profile. The MO2 plugin never needs the desktop app to serve its live dashboard. Standalone HTML requires neither to launch.

## Command Center navigation

The shared landing page now has four coordinated operations:

1. **Update every MO2 mod** — the primary mission, with the live or offline mode explicitly marked. In MO2, **Check all mod sources** runs Nexus and supported public release checks. The Workshop sorts every installed mod into update candidates, manual/unlinked, needs verification and current. It provides exact file review and a safe reviewed download queue.
2. **Questbound + AetherFX** — update achievements, actual install-verified rewards, rank, dramatic finishers and the adjustable FX Studio.
3. **Shiverfell + Mythforge** — persistent survival, exploration, weather, crafting, Dawnshard story and Ashen Warden.
4. **Modhunter Siege + Atlas** — survivor squad, mod-sourced enemies, Unfinished Bestiary, 12 campaign regions, 96 contracts and five mastery tiers.

The worlds and effects must **never interrupt, replace, fake or prioritize themselves ahead of** real MO2 update tasks. No artificial row caps, sample-only overlays in live mode, silent updates or fabricated verified achievements.

## Recommended Windows setup

1. Download **[Modhunter All-in-One.zip](https://github.com/Herbertofury/Modhunter/blob/main/dist/Modhunter-All-in-One.zip)** and extract to a permanent folder.
2. For a portable standalone experience, open **Modhunter.html**. All game modes and the offline checklist work without a program installer.
3. For real MO2 2.5.x updates, run **MO2/Install_to_MO2.bat**; open MO2 and launch **Tools → Update Arsenal - Quest Board**. Its dashboard scans the actual current profile. The tool is designed for MO2 2.5.x, with **2.5.3 as a compatibility target**; native acceptance on a real Windows MO2 2.5.3 installation remains unverified.
4. For native desktop tabs without an MO2 dependency, run **Desktop/Run_ModHunter.bat** (Node.js required). Use **Import MO2** to inspect a local portable profile without the plugin.
5. If you want all live features *inside the desktop shell*, connect its address field to the plugin's local session. This pairing is optional.

Inside the All-in-One archive, **Installers/** has separate edition ZIPs, **Source/** contains the full editable source ZIP, **Docs/** contains the full Wiki, and **bundle-index.json** contains individual file hashes. The root HTML is identical to the copy in Desktop and MO2.

## Actual updater workflow

**Inspect actual profile → identify each original source → query real version evidence → review exact compatible files → queue approved downloads to MO2 → install interactively → backup and re-scan.**

- **Nexus Mods:** MO2's own Nexus authorization when the bridge is available, exact project/file IDs, selected archive in MO2 Downloads.
- **Public GitHub/GitLab releases:** release-tag comparisons, with GitHub release archive selection and native MO2 download.
- **Patreon, LoversLab, Mod DB, Bethesda Creations and other gated sources:** retained original URLs, manual-review states until a provider-authenticated, reliable check is implemented. Never display “current” without evidence.
- **Compatibility:** target game executable and SKSE runtime, native DLLs, FOMOD options, hard masters, dependency order, behavior generators and save warnings.
- **Updates:** per-profile backups and explicit file selection before queueing. No blind bulk installation.

For the exact coverage matrix and unsupported providers, see [Updater Source Coverage](Updater-Source-Coverage.md) and [Update Workshop](Update-Workshop.md).

## Preservation of all historical outputs

The v0.1 through v0.8 lineage is **merged functionally** into the current application, while the **36 authentic original outputs** remain separately preserved (including original ZIPs, GIFs, screenshots and manifests) instead of being rewritten. See [Original Releases and Full Output Audit](Original-Releases-and-Output-Audit.md) and the [original archive on Google Drive](https://drive.google.com/file/d/1tQBnUZ_vDF7tUTtiXf8WJC41OEXNVG_5/view).

## Verification boundary

The GitHub workflow checks the Python bridge/scanner tests, desktop-import Node tests, inline HTML JavaScript syntax, archive integrity, and full byte parity between all three dashboard copies. A successful CI run **does not** prove MO2 2.5.3 native Windows integration or automatic version checks on private sites. Those require runtime evidence with an actual MO2 profile.

**Next:** [Getting Started](Getting-Started.md) · [Desktop App](Desktop-App.md) · [MO2 Plugin](MO2-Plugin.md) · [All Original Releases](Original-Releases-and-Output-Audit.md).
