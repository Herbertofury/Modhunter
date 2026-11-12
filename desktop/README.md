# Modhunter Desktop

The **optional** Electron companion opens the same Modhunter HTML in native Chromium tabs. It works independently without the MO2 plugin and has its own **user-selected read-only profile import**. The other components do not need Electron.

## Start

1. On Windows install a current [Node.js LTS](https://nodejs.org/) once.
2. From the source root, run `cd desktop`, `npm install`, `npm start`; or run `Run_ModHunter.bat` in the desktop directory.
3. Use **Mods**, **Siege**, **Realm** to open the three in-app tabs. The same standalone HTML also works separately in a browser.

## Import a real MO2 profile without the plugin

Click **⌁ IMPORT MO2** in the desktop tab bar, then choose the exact `profiles/<your profile>/modlist.txt` file from an MO2 instance. Modhunter reads the nearby `mods/` directories and `meta.ini` records **on demand**, including enabled state, installed version, Nexus ID, source URL, notes and DLL warnings. Unlisted installed mods are shown too. The snapshot persists locally and reloads in the desktop app.

This mode **does not require the MO2 plugin, log into Nexus, silently download files, alter MO2, or award verified achievements.** For actual authenticated Nexus/GitHub checks and download/install operations, use the optional MO2 plugin connection. If MO2's `profiles` and `mods` directories live in different locations, use the live plugin instead of guessing paths. Re-import after changing your mods; no file system watchdog runs in the background.

## Optional live MO2

1. Install the MO2 plugin and use **Tools → Update Arsenal - Quest Board** to start its loopback server.
2. Copy the plugin's `http://127.0.0.1:PORT/` browser address into the desktop top bar and press **Connect**.
3. All desktop tabs switch to the plugin-owned origin, so its own session token and MO2 API remain same-origin. The app itself never stores or sends a Nexus API key.
4. **Unlink** returns to offline practice without touching MO2.

The desktop shell does not load external websites as privileged in-app content. It opens HTTP(S) project links in your system browser. All game/content web views have no Node or file-system access. Permission prompts are denied by default.

## Build

`npm run check` checks syntax. `npm run dist:win` can create a Windows portable build on a compatible host with Electron Builder and dependencies installed. **No Windows executable is included with this source-only release**; native Electron boot still requires a Windows host for final proof.

Based on Electron 45+ `WebContentsView`. The project intentionally does not use legacy `<webview>` tags or iframe-based live MO2 integration.
