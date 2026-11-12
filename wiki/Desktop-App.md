# Desktop App

[Home](Home.md) / Desktop

The current desktop is **Modhunter 0.9 for Windows**, implemented in Rust with WebView2 tabs. It contains the complete updater dashboard and existing games, imports MO2 profiles without a plugin, and discovers live MO2 sessions for native update operations.

**[Download Windows release](https://github.com/Herbertofury/Modhunter/releases)** · **[Installation, performance and verification](Rust-Desktop.md)** · **[Build instructions](../native/README.md)**

Extract the portable package and run `Modhunter.exe`, or run the NSIS installer. No Node or Electron is required to run the compiled app. Select **Import MO2** for read-only metadata inspection; choose **Instance INI** or **Mods path** for custom directory layouts. **Refresh** reuses unchanged metadata. Install the included MO2 plugin, launch its Quest Board and select **Find MO2** to connect the desktop.

Exports save in Windows Downloads. Moving from the earlier Electron runtime requires exporting and importing dashboard progress because each runtime has its own browser storage. Live MO2 Chronicle data remains with the plugin.

## Preserved Electron edition

The original [Electron source](../desktop/) and [legacy desktop source archive](../dist/Modhunter-Desktop-Source.zip) remain available. Follow `desktop/README.md` to run that older edition with Node. The earlier screenshots and historical packages describe that version; the Rust guide describes the current desktop.
