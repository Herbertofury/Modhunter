# ✦ Getting Started
[Home](Home.md) / Installation

## Standalone HTML
Open [Modhunter.html](https://github.com/Herbertofury/Modhunter/blob/main/web/Modhunter.html) in Chromium, Firefox or another modern browser. This mode does not need MO2, Node or a server. Offline game progression is practice progression and never fabricates official verified-update currency.

## Desktop application
Download the compiled app from [Windows releases](https://github.com/Herbertofury/Modhunter/releases). Extract **Modhunter-Rust-Windows.zip** and run `Modhunter.exe`, or use the NSIS installer. No Node or Electron is required. The app can **import a real MO2 profile without the plugin** via **Import MO2 → profiles/<name>/modlist.txt**. **Instance INI** and **Mods path** support split data/configuration locations. See [Rust Desktop](Rust-Desktop.md).

## MO2 plugin
Use the plugin included in the current Rust portable package. Close MO2, run `Install_MO2_Plugin.bat`, select the directory containing `ModOrganizer.exe`, then restart Mod Organizer 2. Launch **Tools → Update Arsenal - Quest Board**. Use [MO2 Plugin](MO2-Plugin.md) for its live dashboard and update features. The installer preserves a previous plugin in `ModhunterBackups` outside MO2's plugin directory.

## Connect the surfaces
Launch the MO2 plugin, then click **Find MO2** in the Rust desktop. A single validated running session connects; multiple sessions are offered for selection. You can also paste the displayed loopback address (`127.0.0.1`) into **Connect**. MO2 is the source of truth for installed-mod identity, download/install operations, and verified update rewards.

## Choosing an edition
* **Play anywhere:** portable HTML.
* **Inspect your real local modlist without installing a plugin:** Rust desktop → Import MO2.
* **Persistent desktop workspace:** Rust Windows package.
* **Maintain real MO2 profiles:** MO2 plugin.
* **Combine:** optionally connect them without disabling standalone functionality.
