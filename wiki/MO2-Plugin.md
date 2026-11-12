# 🛠️ MO2 Plugin
[Home](Home.md) / MO2 Integration

The independent Mod Organizer 2 Python Tool provides the live Update Arsenal dashboard, installed-mod identity, scanner, local API and verified rewards.

### Installation
Download [MO2 plugin ZIP](https://github.com/Herbertofury/Modhunter/blob/main/dist/Modhunter-MO2-Plugin.zip), extract, run the included `Install_to_MO2.bat`, restart MO2, then launch **Tools → Update Arsenal - Quest Board**.

### Update Workshop
Open **The Update Workshop** in the live dashboard. It separates candidates, mods requiring investigation, manual/unlinked mods and current versions. Review each exact file, acknowledge runtime-sensitive warnings, and queue a whole approved batch into MO2. Downloads do not automatically install. The plugin independently validates the complete batch before invoking native MO2 downloads. See **[Update Workshop](Update-Workshop.md)** for full instructions and limitations.

### Live features
* Read the current MO2 profile, mods and game.
* Identify updates using remote metadata and version evidence.
* Queue Nexus downloads via supported MO2 pathways, opening the standard installer.
* Jump to the exact relevant mod row.
* Award verified Forge Sigils only upon authorized real update evidence.
* Serve an optional loopback dashboard for independent app and browser clients.

**Security model:** localhost only, no exposed remote control endpoint, no trust in client-supplied fake install completion. The host MO2 plugin remains authoritative for verified action completion.

### Independent operation
No standalone HTML or desktop app is required to run the plugin or its self-hosted dashboard. The other clients must also continue operating if MO2 is closed.
