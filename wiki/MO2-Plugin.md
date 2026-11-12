# 🛠️ MO2 Plugin
[Home](Home.md) / MO2 Integration

The independent Mod Organizer 2 Python tool provides the live Update Arsenal dashboard, installed-mod identity, local API and verified rewards. Its bundled Rust helper performs public source checks and plugin analysis; a small Qt helper connects the native Nexus file-list callbacks.

### Installation
Download **Modhunter-Rust-Windows.zip** from [Windows releases](https://github.com/Herbertofury/Modhunter/releases) and extract it. Close MO2, run the included `Install_MO2_Plugin.bat`, and select the folder containing `ModOrganizer.exe`. Restart MO2, then launch **Tools → Update Arsenal - Quest Board**. The installer includes both native helpers and preserves existing plugin data and a backup of the previous plugin. The older ZIP in `dist/` belongs to the historical Electron release series.

The native integration gate targets official **MO2 2.5.2** with its Qt 6.7.1 runtime. See [Rust Desktop](Rust-Desktop.md) for tested scope and connection instructions. Installing the desktop app is optional when using the plugin's browser dashboard.

### Update Workshop
Open **The Update Workshop** in the live dashboard. It separates candidates, mods requiring investigation, manual/unlinked mods and current versions. **Update now** can send an unambiguous, freshly validated file directly to MO2 and open its interactive installer when the download completes. Multiple editions or compatibility risks open **Choose file**; batch queueing keeps installers under your control. The optional manifest and installation briefing expose the same exact-file checks. Every dispatch and installation is independently revalidated. See **[Update Workshop](Update-Workshop.md)** and **[Provider Coverage](Updater-Source-Coverage.md)** for the full supported source matrix and limitations.

### Live features
* Read the current MO2 profile, mods and game.
* Reuse MO2's own Nexus connection for remote metadata and exact file lists, with an optional in-memory API-key fallback.
* Check public GitHub, GitLab, Modrinth and Thunderstore releases, with exact project identities, freshness, dependencies and compatibility context.
* Queue vetted ZIP/7Z/RAR release assets through MO2's native URL downloader; unsupported formats and gated providers retain clear manual-review states.
* Queue Nexus downloads via supported MO2 pathways, opening the standard installer.
* Show winning plugin light/full state, ESL candidate or compaction-review findings, stale conversion output, master problems and checked bug-tab evidence on individual mods.
* Jump to the exact relevant mod row.
* Award verified Forge Sigils only upon authorized real update evidence.
* Serve an optional loopback dashboard for independent app and browser clients.

**Security model:** localhost only, no exposed remote control endpoint, no trust in client-supplied fake install completion. The host MO2 plugin remains authoritative for verified action completion.

### Independent operation
No standalone HTML or desktop app is required to run the plugin or its self-hosted dashboard. The other clients must also continue operating if MO2 is closed.
