# ⚒ Update Workshop | MO2 Mod Updating

> **Modhunter Updater Expansion · MO2 login, Nexus and public release checks**

Modhunter now presents a unified **Update Workshop** in the standalone HTML, Electron desktop app, and MO2 plugin's browser dashboard. **Only a live MO2 plugin connection can authorize Nexus downloads or install them.** The standalone HTML keeps its own mod checklist and can export an advisory plan without MO2.

## One-click MO2 updates (default experience)

1. Open Modhunter from **MO2 → Tools → Update Arsenal**, using your normal active profile and Nexus login. Run **Check all mod sources** or your preferred fast audit. The current MO2 game/profile/installed versions remain authoritative.
2. Press **ϟ Update now** on an outdated mod. When there is **one main Nexus archive matching the published version** (or one validated public GitHub release archive), Modhunter resolves that exact file on its own and sends it to MO2 Downloads immediately. There is **no extra Modhunter confirmation prompt or batch manifest**.
3. After the file finishes downloading, that particular **user-requested** quick update opens MO2's native, interactive installer automatically. Keep your normal FOMOD choices, replacements, and load-order decisions. A profile-text backup is made before the native installer opens, as before. This is not a silent overwrite.
4. If the update has multiple editions, DLL/native binary requirements, unknown file versions, or other meaningful ambiguity, Modhunter opens the **Choose file** picker directly. All files remain listed and searchable. Click the archive you want to begin the download; the optional compatibility notes stay inline. There is **no separate “I reviewed this” confirmation**. A general "check compatibility" note is informational and does not by itself block a unique version-matched main file.
5. To process many mods, press **ϟ Queue matched updates**. It works in the background, adding unambiguous files to MO2 without opening a barrage of installers. Mods needing an exact edition choice stay available under **Choose file**, with the reason visible next to them. There is no artificial cap on the number of installed mods.
6. Already reviewed multiple files? **Queue selected files** dispatches them immediately, without forcing another review screen. **Inspect selection** remains available when you want to see the full manifest before queuing.
7. For any completed download, **ϟ Install now** opens MO2 immediately. **Details** opens the optional installation briefing with the archive, versions, source, runtime, and backup scope. The backend still rechecks profile/game/version/file identity before native dispatch to prevent cross-profile or stale-file installation.
8. After installation, review FOMOD/overwrite and enabled state as appropriate, then refresh the profile or run a full audit. Modhunter records installation state and separately verifies whether the installed version actually advanced.

Nothing silently replaces mod files. A click on **Update now** deliberately authorizes the associated native MO2 installer after that file downloads; **Queue matched updates** downloads only. The no-friction UX removes redundant *Modhunter* prompts, not MO2's actual installer controls. Free Nexus accounts may still need to initiate downloads from the website using **Mod Manager Download** when Nexus requires it.

## Three independent editions

| Edition | Installation | What it can do alone |
| --- | --- | --- |
| **Portable HTML** | Download [Modhunter.html](https://github.com/Herbertofury/Modhunter/blob/main/web/Modhunter.html), open in Chromium/Firefox | Complete sample checklist, offline Siege and Shiverfell, visual Workshop, JSON export, personal progress |
| **Desktop app** | Download [desktop source ZIP](https://github.com/Herbertofury/Modhunter/blob/main/dist/Modhunter-Desktop-Source.zip); install Node.js and launch the desktop command | Native Electron Chromium tabs, **Import MO2** for a real read-only `modlist.txt` snapshot without the plugin, standalone games; optionally connects for native updating |
| **MO2 plugin** | Download [MO2 plugin ZIP](https://github.com/Herbertofury/Modhunter/blob/main/dist/Modhunter-MO2-Plugin.zip) and run `Install_to_MO2.bat` | Live profile scan, exact-file review, native Nexus downloads, interactive install, backups, profile-scope Chronicle |

The three editions have **no installation dependencies on one another**. Their optional shared transport is a **127.0.0.1 loopback session**, with a per-launch secret and origin checks. The browser does not receive the Nexus API key. The Electron browser shell connects to the same already-running plugin session if explicitly asked.

## Important controls and limitations

- **Interactive status dashboard.** Click Candidates, Investigate, Manual sources, Current, Compatibility watch or Ready to install to filter the full update list. Search operates within the selected lane, and a visible counter shows every matching mod. `Alt+U` opens the Workshop search and `Alt+R` reviews the next eligible update.
- **No mass overwrites or blind automatic installations.** The user-requested per-mod fast path may open MO2’s native installer on download completion; batch queueing remains download-only. The file picker and optional full manifest remain available.
- **No double install on a finished ticket.** Completed, installing and installed tickets are tracked per profile and mod identity; an attempted second install is rejected. Cancelled native installers return their ticket to a retryable state.
- **Stale plan protection.** A switched profile or mod-version change invalidates installation of a previously queued reviewed archive. Fresh scans invalidate cached Nexus file choices.
- **Live updater refresh.** The foreground dashboard refreshes tickets without rebuilding unchanged rows. Only the download initiated with **Update now** can open the MO2 native installer upon completion, and it happens only in that live MO2 session.
- **Long FOMOD sessions.** The install request is nonblocking; MO2 remains the source of truth and only a completed native callback updates the ticket. Native installers run one at a time. Closing the application stops session-only work; no unattended install is resumed on restart.
- **Stale file reviews.** Changing the published target version clears previous UI file approvals. Reviewed batch submissions also validate an included target version **server-side** against the current scan. Legacy requests without that optional field continue through the existing exact-file/profile preflight.
- **Per-row file guidance.** Multiple installed MO2 rows sharing one Nexus mod ID keep separate advisories, based on their actual recorded source and update evidence. A GitHub release can never be mistaken for a confirmed Nexus file-version match.
- **No hidden lists.** Every update lane and every provider file remains reachable. Search and filters are user-controlled views, not internal quantity caps; ticket state joins are indexed to avoid quadratic scans of large MO2 libraries.
- A newer Nexus project version is **a candidate**, not a guarantee of compatibility or the right file.
- Patreon, Discord, LoversLab, Mod DB, Bethesda Creations and other login-dependent sources remain visible with their real links but are not automatically declared current. Public **GitHub/GitLab release versions are checked**, and vetted GitHub archive assets may be downloaded via MO2. See [Source Coverage](Updater-Source-Coverage.md).
- **Honest backup scope.** The pre-install backup includes load-order/profile **text files**, not the mod directories themselves. It is not a full rollback of the previous installed mod files. Every preflight briefing states this explicitly.
- MO2 profile backups contain profile **text files**, not the entire mod installation or a disk image.
- Verify FOMOD selections, overwrite priority, masters and patches after installation.
- A pending or completed download is session scoped. Reopen MO2's actual Downloads tab to cross-check if restarting MO2.
- No default automatic monitoring or unattended installation is used.
- Standalone HTML and desktop without the MO2 plugin **cannot** perform native authenticated update scans, install mods or claim official verified achievements. The desktop app **can read a user-selected portable MO2 profile independently** without changing its files.

## Persistent update operation history

The Workshop now has a searchable **Update Timeline** beneath live MO2 downloads. It records native download and installer state transitions in a profile-scoped, append-only JSONL ledger in MO2 plugin data. Each receipt retains the reviewed archive, provider, installed/target versions, observed status, a timestamped status timeline, and whether a profile-text backup was recorded. **No Nexus tokens, personal download URLs, local download paths, or backup paths are saved to the journal.**

The timeline can be filtered by all operations, current session, verified updates, or items needing review. **Export receipts** writes the complete active-profile history as JSON. Unlike the gaming Chronicle, this is an operational audit log: a finished installer without a demonstrated version change is marked unverified.

After MO2 closes and reopens, earlier operations remain visible but **are never treated as active download tickets**. A previous session that ended while downloading, paused, waiting to install, or inside the interactive installer is flagged for review. Open MO2 Downloads, verify the archive and current mod version, and requeue through normal approval if necessary. Nothing automatically resumes or overwrites mods.

## Quality assurance

The repository contains unit and simulated MO2-signal tests for exact Nexus ID matching, duplicate project handling, duplicate queue rejection, profile boundaries, installer state transitions, stale-version blocking, per-row file recommendations, multiple-main-file ambiguity, compatibility acknowledgment and authoritative per-mod version checks. The `tests/updater_sss_browser_qa.py` script exercises the **actual compiled HTML** in Chromium using deterministic MO2 API fixtures: open the guided reviewer, inspect a version-matched main file, filter the full file list, review and cancel a complete queue manifest, approve a download, inspect the exact pre-install briefing, simulate native installation, reject a stale runtime, and render a mobile briefing. It is browser/UI proof, **not** a native Windows MO2/FOMOD acceptance test. A 2,500-mod / 800-ticket equivalence benchmark is in `scripts/benchmark_workshop_tickets.cjs`. The workflow runs this Chromium interaction proof before packaging, and uploads screenshots of the simulated workflow. The workflow [Verify and package Modhunter Update Workshop](https://github.com/Herbertofury/Modhunter/actions/workflows/build-workshop.yml) builds HTML, validates embedded scripts and repackages all three distribution paths on source changes. Native **Windows** MO2 and Electron acceptance still require a Windows test machine with an actual MO2 profile.

For architecture, see [Architecture](Architecture.md). For onboarding, see [Getting Started](Getting-Started.md). For launch troubleshooting, see [Troubleshooting](Troubleshooting.md).

**[Full provider support and updater behavior](Updater-Source-Coverage.md)** includes an explicit source-by-source support matrix, verification levels, manual fallbacks and limitations.
