# ⚒ Update Workshop | MO2 Mod Updating

> **Modhunter Updater Expansion · MO2 login, Nexus and public release checks**

Modhunter now presents a unified **Update Workshop** in the standalone HTML, Electron desktop app, and MO2 plugin's browser dashboard. **Only a live MO2 plugin connection can authorize Nexus downloads or install them.** The standalone HTML keeps its own mod checklist and can export an advisory plan without MO2.

## The update workflow

1. Start the MO2 tool using **Tools → Update Arsenal - Quest Board**. The plugin reads your active game's **profile, installed mod identities, Nexus project IDs, installed versions and saved per-profile progress**.
2. Use **✧ Check all mod sources**, **MO2 cached**, **Fast Nexus / cached**, or **Full Nexus audit**. When available, Modhunter reuses MO2's own existing Nexus sign-in through its native bridge, with no separate API-key entry; an optional personal key is held in RAM only as a fallback. An all-source audit also checks public GitHub/GitLab releases, and marks account-gated websites unverified rather than current.
3. Open **The Update Workshop** and **Build update plan**. Every row remains visible in one of these review lanes: **Update candidates**, **Needs verification**, **Manual / unlinked**, or **Current**. The plan is specific to the active MO2 profile and chosen game runtime.
4. On a verified candidate, choose **Review files**. Select an **exact Nexus file or GitHub release asset** and read its description, requirements, version, compatible game executable, SKSE/Address Library and FOMOD instructions. The planner does not equate a newer project version with a compatible install.
5. Acknowledge compatibility-watch warnings for DLLs, hard plugins, behavior generation and known hold/avoid advice. **Queue reviewed files** sends approved Nexus IDs and public GitHub release archives to MO2's native download manager. The whole queue is validated before dispatch; duplicate identical Nexus file IDs are skipped, and already-queued mods cannot be queued again until their ticket is resolved.
6. Use the new **Discover → Review → Install** command surface to follow the next action. Download tickets show recognizable mod names, live MO2 status, ready-to-install actions and review/retry prompts for failed archives. Both Nexus files and public GitHub release assets can be presented for the same mod; if Nexus is temporarily unavailable, available vetted GitHub release assets stay selectable.
7. Once a download completes, use **Install with MO2**. Modhunter saves a timestamped copy of the profile's load-order text files, then opens MO2's **standard interactive installer** so that FOMOD choices, overwrite conflicts and prompts remain under your control. The request now returns immediately while the real dialog runs: the browser remains responsive through long FOMOD decisions, and an exclusive native install lease stays held until MO2 actually finishes. A profile-scoped ticket rejects stale versions and prevents duplicate installs.
8. Check the updated mod's enabled state, load order, dependencies and test in-game. **Refresh profile**, then run a **Full audit** to validate what is current. Rank and verified Forge Sigils are awarded only from real MO2 evidence, never manual checklist clicks. Installer completion alone does not prove the update: when MO2 cannot confirm a changed installed version, the ticket is marked unverified.

## Three independent editions

| Edition | Installation | What it can do alone |
| --- | --- | --- |
| **Portable HTML** | Download [Modhunter.html](https://github.com/Herbertofury/Modhunter/blob/main/web/Modhunter.html), open in Chromium/Firefox | Complete sample checklist, offline Siege and Shiverfell, visual Workshop, JSON export, personal progress |
| **Desktop app** | Download [desktop source ZIP](https://github.com/Herbertofury/Modhunter/blob/main/dist/Modhunter-Desktop-Source.zip); install Node.js and launch the desktop command | Native Electron Chromium tabs, **Import MO2** for a real read-only `modlist.txt` snapshot without the plugin, standalone games; optionally connects for native updating |
| **MO2 plugin** | Download [MO2 plugin ZIP](https://github.com/Herbertofury/Modhunter/blob/main/dist/Modhunter-MO2-Plugin.zip) and run `Install_to_MO2.bat` | Live profile scan, exact-file review, native Nexus downloads, interactive install, backups, profile-scope Chronicle |

The three editions have **no installation dependencies on one another**. Their optional shared transport is a **127.0.0.1 loopback session**, with a per-launch secret and origin checks. The browser does not receive the Nexus API key. The Electron browser shell connects to the same already-running plugin session if explicitly asked.

## Important controls and limitations

- **No mass overwrites or blind automatic installations.** Queue downloads are separate from native installation. The existing single-file picker remains available.
- **No double install on a finished ticket.** Completed, installing and installed tickets are tracked per profile and mod identity; an attempted second install is rejected. Cancelled native installers return their ticket to a retryable state.
- **Stale plan protection.** A switched profile or mod-version change invalidates installation of a previously queued reviewed archive. Fresh scans invalidate cached Nexus file choices.
- **Live updater refresh.** The foreground dashboard refreshes download-ticket status without rebuilding unchanged tickets, so buttons and keyboard focus remain stable. It never silently installs a completed archive.
- **Long FOMOD sessions.** The install request is nonblocking; MO2 remains the source of truth and only a completed native callback updates the ticket. Closing the application stops outstanding session-only work; no unattended install is resumed on restart.
- **Stale file reviews.** Changing the published target version clears previous UI file approvals. The user always reviews the actual file selection again.
- A newer Nexus project version is **a candidate**, not a guarantee of compatibility or the right file.
- Patreon, Discord, LoversLab, Mod DB, Bethesda Creations and other login-dependent sources remain visible with their real links but are not automatically declared current. Public **GitHub/GitLab release versions are checked**, and vetted GitHub archive assets may be downloaded via MO2. See [Source Coverage](Updater-Source-Coverage.md).
- MO2 profile backups contain profile **text files**, not the entire mod installation or a disk image.
- Verify FOMOD selections, overwrite priority, masters and patches after installation.
- A pending or completed download is session scoped. Reopen MO2's actual Downloads tab to cross-check if restarting MO2.
- No default automatic monitoring or unattended installation is used.
- Standalone HTML and desktop without the MO2 plugin **cannot** perform native authenticated update scans, install mods or claim official verified achievements. The desktop app **can read a user-selected portable MO2 profile independently** without changing its files.

## Quality assurance

The repository contains unit and simulated MO2-signal tests for exact Nexus ID matching, duplicate project handling, duplicate queue rejection, profile boundaries, installer state transitions, stale-version blocking, compatibility acknowledgment and authoritative per-mod version checks. The workflow [Verify and package Modhunter Update Workshop](https://github.com/Herbertofury/Modhunter/actions/workflows/build-workshop.yml) builds HTML, validates embedded scripts and repackages all three distribution paths on source changes. Native **Windows** MO2 and Electron acceptance still require a Windows test machine with an actual MO2 profile.

For architecture, see [Architecture](Architecture.md). For onboarding, see [Getting Started](Getting-Started.md). For launch troubleshooting, see [Troubleshooting](Troubleshooting.md).

**[Full provider support and updater behavior](Updater-Source-Coverage.md)** includes an explicit source-by-source support matrix, verification levels, manual fallbacks and limitations.
