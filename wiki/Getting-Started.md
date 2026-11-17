# ✦ Getting Started
[Home](Home.md) / Installation

For the complete Windows setup and everyday update workflow, start with [Daily use](Daily-Use.md).

## Standalone HTML

Download the [current compiled dashboard](https://github.com/Herbertofury/Modhunter/raw/refs/heads/main/mo2_update_arsenal/web/index.html), save it as `Modhunter.html`, then open the local file in Chromium, Firefox or another modern browser. The independent [Modhunter-0.13.0-Standalone.zip](https://github.com/Herbertofury/Modhunter/releases/download/v0.13.0/Modhunter-0.13.0-Standalone.zip) provides the tested release HTML with its build identity and checksums. This mode does not need MO2, Node or a server. Offline game progression is practice progression and never fabricates official verified-update currency. See [Standalone HTML](Standalone-HTML.md).

## Desktop application

Download the compiled app from [Windows releases](https://github.com/Herbertofury/Modhunter/releases). Extract **Modhunter-Rust-Windows.zip** and run `Modhunter.exe`, or use the NSIS installer. No Node or Electron is required. The app can **import a real MO2 profile without the plugin** via **Import MO2 → profiles/<name>/modlist.txt**. **Instance INI** and **Mods path** support split data/configuration locations. See [Rust Desktop](Rust-Desktop.md).

## MO2 plugin

The easiest desktop setup is **MO2 Instances**: discover installations, select the applications you use, review the exact destinations, then install the matching bundled plugin. Both installer and portable desktop packages include it. Close selected MO2 applications when the preview requests it, then restart MO2 and wait for the profile to load. Existing plugin data is retained and the previous plugin is backed up outside MO2's plugin directory. [Instance setup guide](MO2-Instances.md).

For independent browser use, the matching [Modhunter-0.13.0-MO2-Plugin.zip](https://github.com/Herbertofury/Modhunter/releases/download/v0.13.0/Modhunter-0.13.0-MO2-Plugin.zip) and its manual installer remain available. Keep the extracted package together. Opening **Tools → Update Arsenal - Quest Board** is optional; the live bridge starts automatically. See [MO2 Plugin](MO2-Plugin.md).

## Connect the surfaces

Start MO2 with the installed plugin, wait for its profile, then launch the Rust desktop. Automatic discovery connects a single validated running session; **Find MO2** retries discovery on demand, and multiple sessions are offered for selection. You can also paste the displayed loopback address (`http://127.0.0.1:PORT/`) into **Connect**. MO2 is the source of truth for installed-mod identity, download/install operations, and verified update rewards.

## Choosing an edition

* **Play anywhere:** portable HTML.
* **Inspect your real local modlist without installing a plugin:** Rust desktop → Import MO2.
* **Persistent desktop workspace:** Rust Windows package.
* **Maintain real MO2 profiles:** MO2 plugin.
* **Combine:** optionally connect them without disabling standalone functionality.
