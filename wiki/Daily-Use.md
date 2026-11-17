# Daily use

[Home](Home) / [Install and connect](Getting-Started) / [Current Windows release](https://github.com/Herbertofury/Modhunter/releases/latest)

## Set up the complete Windows package

Use the Windows installer or **Modhunter-Rust-Windows.zip** from the current release. Both desktop packages include the complete matching MO2 plugin for the in-app **MO2 Instances** manager. Extract the complete portable archive into its own folder and keep its files together.

1. If upgrading, export a progress backup from the existing app before changing versions. Keep the old portable folder until the new version opens your collection correctly. MO2 plugin upgrades preserve existing Chronicle progress and retain the previous plugin outside MO2's plugin search folder.
2. Open Modhunter, then choose **MO2 Instances** in the desktop toolbar. Let known-location discovery finish. Use **Add folder** or **Search drives & folders** for portable installs and secondary modding locations.
3. Select the applications you want, then **Review selected installations**. Read the exact destinations and shared-instance relationships. If the preview asks you to close a selected MO2 application, close it and choose **Check targets again**. Choose **Install plugin** once the reviewed targets are ready. Existing plugin data and the previous plugin backup are retained.
4. Check each result. The app verifies the installed plugin files before showing success. The portable package's read-only **Check_MO2_Readiness.bat** remains available for a detailed runtime report, and its manual installer remains supported. [MO2 Instances](MO2-Instances) explains selection, backup, cancellation and recovery.
5. Start MO2, select the intended game and profile, and wait for it to finish loading. The bridge starts automatically; opening **Tools → Update Arsenal - Quest Board** is optional. Return to the already-open Modhunter window. If automatic discovery has not connected, select **Find MO2**; choose the intended session when more than one is running.
6. Confirm the profile name and mod count. Keep Nexus signed in through MO2. Open **Updates** and run **Check for updates** to inspect the actual source results.

For the exact MO2 2.5.3b12 setup notes, see [MO2 testing and readiness](MO2-253-Testing). The release's actual MO2 fixture uses official 2.5.2, with a separate Qt 6.11 forward-runtime check; those checks do not claim execution of every beta build or a personal mod collection.

For OLED black regardless of the Windows theme, open **Watch Pages → Preferences**, scroll to **App preferences / Appearance, privacy & upkeep**, set **Theme** to **Dark**, and select **Save app preferences**. The default is **System**. The Library's moon/**Appearance** control also switches and saves the global light/dark choice.

## Everyday update workflow

Browse the complete collection in **List** or **Gallery**. The library uses the window's available width and height, with a sticky column header and a compact bottom search/control bar. Search stays reachable while the list scrolls; **Ctrl+F** or **Ctrl+K** focuses it. Use source, status and health filters and choose which columns remain visible. Column choices are remembered on the current device.

The **Bugs** column displays available checked evidence; unknown evidence is not shown as zero. Click its badge to open the inspector and refresh public Nexus evidence or open the original Bugs page. The displayed Nexus tab count is kept distinct from an established open/total issue count. Related plugin-health details and notes remain visible. Separator colors configured in MO2 carry through to the library and chapter navigation, including a color-only change while connected. Counts and progress bars describe the manual checklist; detected update status and verified installation remain separate.

**Check for updates** evaluates source evidence for each installed component. Main files, optional files and addons on the same page can have different versions and update histories. An addon is not compared with the page's main-file version merely because both share a mod ID. The result identifies matched installed/replacement file evidence when available; missing or ambiguous identity requires review. Shared complete project-file evidence avoids repeating the same request for every installed component.

Normal checks can reuse complete Nexus file evidence for up to four hours. Cached results retain their original check time and are labeled accordingly. Choose **Tools → Full audit** to request fresh evidence immediately. Expired or incomplete evidence cannot authorize a queued update; a file or installed identity change invalidates the old review, even when its version label is unchanged. An unsuccessful refresh preserves the last complete observation while reporting the new failure.

The controlled 600-project/4,800-mod fixture measured repeated manual checks at **0.24 seconds**, compared with **1.32 seconds** previously, with repeated provider calls reduced from 600 to zero. The fixture retains all rows, artwork and authors and simulates 10 ms API latency; it does not measure a real account or PC. A cold exact-file check requires additional file/history data. Forced complete-evidence audits remained approximately level with the equivalent old workload. [Raw samples, request counts and method](https://github.com/Herbertofury/Modhunter/blob/main/native/evidence/nexus-file-checks-0.13.0.json).

A successful current result, available update, failed check, sign-in requirement and manual page review each keep their own status. If a source fails, use its retry or original-page action; a failed check does not establish that the mod is current. Canceling a scan retains completed results and the last complete catalog.

For an update with one validated eligible file, use **Update now**. If the project has multiple files, editions or unresolved compatibility, use **Choose file** and review the exact archive. Downloads stay with MO2; its normal installer and FOMOD choices remain available. The queue reports actual preparation, download, installation and verification results. A manual checklist tick does not install anything.

The recovery workflow retains actual mod-file backups. Use the exact recovery preview before restoring: later edits or changed ownership can stop a restore to protect current files. Do not treat a profile's load-order text backup as a backup of every mod directory. [Update Workshop](Update-Workshop) explains queue, review and recovery behavior.

## Pages outside the automatic adapters

Open **Watch Pages** in the desktop toolbar to use Watchtower for an exact release page, creator page or changelog. Choose a named site session when access needs sign-in. The first successful read establishes a baseline; later page changes stay available for review. Optional due checks run while the app is open. A changed page remains a page observation until its exact archive and compatibility have been reviewed.

The draft Creator Hub/source-continuity work is tracked separately in [PR #27](https://github.com/Herbertofury/Modhunter/pull/27). Automatic creator follows and cross-site migration management are separate work.

## If the app is disconnected

Check that MO2 is running, its selected profile has finished loading, and the matching plugin is installed. Select **Find MO2** again and inspect **Details** if the bridge remains unavailable. Changing MO2 profiles invalidates old queued work rather than applying it to the new profile.

**Import MO2** remains independently usable for read-only local inspection: select the profile's `modlist.txt`, then use **Instance INI** or **Mods path** for split directory layouts. Offline inspection does not provide MO2-authenticated downloads or native installation. Watchtower and the game modes are optional and do not need to be enabled for mod maintenance.

## Release verification

The release page contains the installer, portable app, independent MO2 components, standalone HTML, verification archive and SHA-256 manifest. The Windows release gates cover the compiled desktop, actual WebView2, MO2 fixtures, plugin installation, installed-app launch/uninstall, source/lifecycle contracts, full-library rendering and package identity. The verification archive records the tested commit and executable identity. Account-specific Nexus access, particular FOMOD choices and in-game behavior still depend on the user's actual setup.
