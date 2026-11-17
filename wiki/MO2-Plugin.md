# 🛠️ MO2 Plugin

[Home](Home.md) / MO2 Integration

The independent Mod Organizer 2 Python tool provides the live Update Arsenal dashboard, installed-mod identity, local API and verified rewards. Its bundled Rust helper performs public source checks and plugin analysis; a small Qt helper connects the native Nexus file-list callbacks.

### Installation

In the desktop app, open **MO2 Instances** to discover your MO2 applications, select one or several destinations, review them, and install the matching bundled plugin. Both the Windows installer and portable desktop include the complete plugin resource. Existing plugin data and previous-plugin backups are retained. See [MO2 Instances](MO2-Instances.md) for shared global configurations, per-target results and stopping safely.

The manual portable workflow remains available:

Download **Modhunter-Rust-Windows.zip** from [Windows releases](https://github.com/Herbertofury/Modhunter/releases) and extract the complete archive. Close MO2, run `Check_MO2_Readiness.bat`, and select the folder containing `ModOrganizer.exe`. Before installation, the saved report's `readyToInstall` should be `true`; `plugin_installed` can show **REVIEW** for an absent or older plugin.

Run `Install_MO2_Plugin.bat` and select the same folder. The installer includes both native helpers and preserves existing plugin data and a backup of the previous plugin. Rerun the readiness checker: `plugin_installed` should pass with matching installed and expected versions, and `readyForConnectionTest` should be `true`. See [Daily use](Daily-Use.md) for the complete setup sequence. The older ZIP in `dist/` belongs to the historical Electron release series.

Restart MO2, select the intended profile, and wait for it to load. The bridge starts automatically. For desktop use, launch Modhunter and use automatic discovery or **Find MO2**. **Tools → Update Arsenal - Quest Board** opens the optional browser dashboard.

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
