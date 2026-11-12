# 🖥️ Desktop App
[Home](Home.md) / Desktop

![Desktop tabs](../screenshots/desktop-tabbar.png)

The Electron desktop provides dedicated Chromium tabs for the **Mod Vault**, **Modhunter Siege**, and **Shiverfell**. The source package runs standalone, with no MO2 installation necessary for offline play.

1. Download [desktop source](../dist/Modhunter-Desktop-Source.zip).
2. Extract and read `desktop/README.md`.
3. Install compatible Node.js and project dependencies.
4. Run `desktop/Run_ModHunter.bat` on Windows.
5. Optionally paste the session URL from the MO2 plugin into **Connect**.

**Runtime proof:** the recovered baseline reports Chromium tab interaction and JavaScript syntax passing. Native Windows Electron packaging remains unverified; this is **source**, not a prebuilt Windows EXE.
