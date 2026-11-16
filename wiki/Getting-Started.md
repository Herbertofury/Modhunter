# ✦ Getting Started
[Home](Home.md) / Installation

## Standalone HTML

Download the [current compiled dashboard](https://github.com/Herbertofury/Modhunter/raw/refs/heads/main/mo2_update_arsenal/web/index.html), save it as `Modhunter.html`, then open the local file in Chromium, Firefox or another modern browser. The independent [Modhunter-0.12.0-Standalone.zip](https://github.com/Herbertofury/Modhunter/releases/download/v0.12.0/Modhunter-0.12.0-Standalone.zip) provides the tested release HTML with its build identity and checksums. This mode does not need MO2, Node or a server. Offline game progression is practice progression and never fabricates official verified-update currency. See [Standalone HTML](Standalone-HTML.md).

## Desktop application

Download the compiled app from [Windows releases](https://github.com/Herbertofury/Modhunter/releases). Extract **Modhunter-Rust-Windows.zip** and run `Modhunter.exe`, or use the NSIS installer. No Node or Electron is required. The app can **import a real MO2 profile without the plugin** via **Import MO2 → profiles/<name>/modlist.txt**. **Instance INI** and **Mods path** support split data/configuration locations. See [Rust Desktop](Rust-Desktop.md).

## MO2 plugin

Use the matching plugin included in the Rust portable package, or the independent [Modhunter-0.12.0-MO2-Plugin.zip](https://github.com/Herbertofury/Modhunter/releases/download/v0.12.0/Modhunter-0.12.0-MO2-Plugin.zip). Keep the extracted package together. Close MO2, run `Install_MO2_Plugin.bat`, select the directory containing `ModOrganizer.exe`, then restart Mod Organizer 2 and wait for the profile to load. The bridge starts automatically; opening **Tools → Update Arsenal - Quest Board** is optional. Use [MO2 Plugin](MO2-Plugin.md) for its live dashboard and update features. The installer preserves a previous plugin in `ModhunterBackups` outside MO2's plugin directory and validates a released package before replacing it.

## Connect the surfaces

Start MO2 with the installed plugin, wait for its profile, then launch the Rust desktop. Automatic discovery connects a single validated running session; **Find MO2** retries discovery on demand, and multiple sessions are offered for selection. You can also paste the displayed loopback address (`http://127.0.0.1:PORT/`) into **Connect**. MO2 is the source of truth for installed-mod identity, download/install operations, and verified update rewards.

## Choosing an edition

* **Play anywhere:** portable HTML.
* **Inspect your real local modlist without installing a plugin:** Rust desktop → Import MO2.
* **Persistent desktop workspace:** Rust Windows package.
* **Maintain real MO2 profiles:** MO2 plugin.
* **Combine:** optionally connect them without disabling standalone functionality.
