# ✦ Getting Started
[Home](Home.md) / Installation

## Standalone HTML
Open [Modhunter.html](../web/Modhunter.html) in Chromium, Firefox or another modern browser. This mode does not need MO2, Node or a server. Offline game progression is practice progression and never fabricates official verified-update currency.

## Desktop application
Download [desktop source](../dist/Modhunter-Desktop-Source.zip) and extract it. Follow [Desktop App](Desktop-App.md) for running the app using Node.js and Electron. The app is useful offline and can optionally connect to an MO2 session.

## MO2 plugin
Download [MO2 plugin package](../dist/Modhunter-MO2-Plugin.zip), extract it, run `Install_to_MO2.bat` and restart Mod Organizer 2. Launch **Tools → Update Arsenal - Quest Board**. Use [MO2 Plugin](MO2-Plugin.md) for its live dashboard and update features.

## Connect the surfaces
Launch the MO2 plugin and use its displayed loopback address (`127.0.0.1`) in the desktop app's **Connect** field. The browser dashboard can use the same local API when explicitly connected. MO2 is the source of truth for installed-mod identity, download/install operations, and verified update rewards. Never expose the loopback service to the public internet.

## Choosing an edition
* **Play anywhere:** portable HTML.
* **Persistent desktop workspace:** Electron source package.
* **Maintain real MO2 profiles:** MO2 plugin.
* **Combine:** optionally connect them without disabling standalone functionality.
