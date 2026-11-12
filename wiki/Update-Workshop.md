# ⚒ Update Workshop | MO2 Mod Updating

> **Modhunter v0.8 development line · reviewed download planner**

Modhunter now presents a unified **Update Workshop** in the standalone HTML, Electron desktop app, and MO2 plugin's browser dashboard. **Only a live MO2 plugin connection can authorize Nexus downloads or install them.** The standalone HTML keeps its own mod checklist and can export an advisory plan without MO2.

## The update workflow

1. Start the MO2 tool using **Tools → Update Arsenal - Quest Board**. The plugin reads your active game's **profile, installed mod identities, Nexus project IDs, installed versions and saved per-profile progress**.
2. Use **MO2 cached**, **Fast Nexus sweep**, or **Full audit**. A fast sweep checks the latest Nexus activity with caching; a full audit fetches current Nexus metadata for all linked projects and is required after version changes for final verification. An API key is held in memory for the current session only.
3. Open **The Update Workshop** and **Build update plan**. Every row remains visible in one of these review lanes: **Update candidates**, **Needs verification**, **Manual / unlinked**, or **Current**. The plan is specific to the active MO2 profile and chosen game runtime.
4. On a verified candidate, choose **Review files**. Select an **exact Nexus file** and read its description, requirements, version, compatible game executable, SKSE/Address Library and FOMOD instructions. The planner does not equate a newer project version with a compatible install.
5. Acknowledge compatibility-watch warnings for DLLs, hard plugins, behavior generation and known hold/avoid advice. **Queue reviewed files** sends each selected file to MO2's native download manager. Duplicate identical Nexus file IDs are skipped.
6. Once each download completes, use **Install with MO2**. Modhunter first saves a timestamped copy of the profile's load-order text files, then opens MO2's **standard interactive installer** so that FOMOD choices, overwrite conflicts and prompts remain under your control.
7. Check the updated mod's enabled state, load order, dependencies and test in-game. **Refresh profile**, then run a **Full audit** to validate what is current. Rank and verified Forge Sigils are awarded only from real MO2 evidence, never manual checklist clicks.

## Three independent editions

| Edition | Installation | What it can do alone |
| --- | --- | --- |
| **Portable HTML** | Download [Modhunter.html](https://github.com/Herbertofury/Modhunter/blob/main/web/Modhunter.html), open in Chromium/Firefox | Complete sample checklist, offline Siege and Shiverfell, visual Workshop, JSON export, personal progress |
| **Desktop app** | Download [desktop source ZIP](https://github.com/Herbertofury/Modhunter/blob/main/dist/Modhunter-Desktop-Source.zip); install Node.js and launch the included desktop command | Native Electron Chromium tab system, standalone games and Workshop; optionally connects to MO2 |
| **MO2 plugin** | Download [MO2 plugin ZIP](https://github.com/Herbertofury/Modhunter/blob/main/dist/Modhunter-MO2-Plugin.zip) and run `Install_to_MO2.bat` | Live profile scan, exact-file review, native Nexus downloads, interactive install, backups, profile-scope Chronicle |

The three editions have **no installation dependencies on one another**. Their optional shared transport is a **127.0.0.1 loopback session**, with a per-launch secret and origin checks. The browser does not receive the Nexus API key. The Electron browser shell connects to the same already-running plugin session if explicitly asked.

## Important controls and limitations

- **No mass overwrites or blind automatic installations.** Queue downloads are separate from native installation. The existing single-file picker remains available.
- A newer Nexus project version is **a candidate**, not a guarantee of compatibility or the right file.
- Manual Patreon, Discord, GitHub, Creation Club, locally packaged, hidden and renamed mods remain visible, searchable and exportable even without a Nexus identifier; their external updates remain manual.
- MO2 profile backups contain profile **text files**, not the entire mod installation or a disk image.
- Verify FOMOD selections, overwrite priority, masters and patches after installation.
- A pending or completed download is session scoped. Reopen MO2's actual Downloads tab to cross-check if restarting MO2.
- No default automatic monitoring or unattended installation is used.
- Standalone HTML and desktop without MO2 **cannot** change installed MO2 files and cannot claim official verified achievements.

## Quality assurance

The repository contains unit tests for exact Nexus ID matching, duplicate project handling, profile boundaries, compatibility acknowledgment and authoritative per-mod version checks. The workflow [Verify and package Modhunter Update Workshop](https://github.com/Herbertofury/Modhunter/actions/workflows/build-workshop.yml) builds HTML, validates embedded scripts and repackages all three distribution paths on source changes. Native **Windows** MO2 and Electron acceptance still require a Windows test machine with an actual MO2 profile.

For architecture, see [Architecture](Architecture.md). For onboarding, see [Getting Started](Getting-Started.md). For launch troubleshooting, see [Troubleshooting](Troubleshooting.md).