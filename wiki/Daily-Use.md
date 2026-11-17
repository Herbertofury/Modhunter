# Daily use

[Home](Home) / [Install and connect](Getting-Started) / [Current Windows release](https://github.com/Herbertofury/Modhunter/releases/latest)

## Set up the complete Windows package

Use **Modhunter-Rust-Windows.zip** from the current release when you want the desktop app and live MO2 updating together. Extract the complete archive into its own folder; keep its files together. The Windows installer installs the desktop app, while the portable package also contains the MO2 installer and readiness tools.

1. If upgrading, export a progress backup from the existing app before changing versions. Keep the old portable folder until the new version opens your collection correctly. MO2 plugin upgrades preserve existing Chronicle progress and retain the previous plugin outside MO2's plugin search folder.
2. Close MO2. Run **Check_MO2_Readiness.bat** from the new package and select the folder containing **ModOrganizer.exe**. Before installation, the saved report's `readyToInstall` should be `true`; `plugin_installed` can show **REVIEW** when the plugin is absent or from an older release.
3. Run **Install_MO2_Plugin.bat** and select that same MO2 folder. The installer verifies the package before replacing the plugin.
4. Run the readiness checker again. Expect `plugin_installed` to pass, its installed and expected versions to match the verified package, and the report's `readyForConnectionTest` to be `true`. These static checks establish matching files and supported runtime observations; the next step establishes the live connection.
5. Start MO2, select the intended game and profile, and wait for it to finish loading. The bridge starts automatically; opening **Tools → Update Arsenal - Quest Board** is optional. Launch **Modhunter.exe**. If automatic discovery has not connected, select **Find MO2**; choose the intended session when more than one is running.
6. Confirm the profile name and mod count. Keep Nexus signed in through MO2. Open **Updates** and run **Check for updates** to inspect the actual source results.

For the exact MO2 2.5.3b12 setup notes, see [MO2 testing and readiness](MO2-253-Testing). The release's actual MO2 fixture uses official 2.5.2, with a separate Qt 6.11 forward-runtime check; those checks do not claim execution of every beta build or a personal mod collection.

For OLED black regardless of the Windows theme, open **Watch Pages → Preferences**, scroll to **App preferences / Appearance, privacy & upkeep**, set **Theme** to **Dark**, and select **Save app preferences**. The default is **System**. The Library's moon/**Appearance** control also switches and saves the global light/dark choice.

## Everyday update workflow

Browse the complete collection in **List** or **Gallery**, search by name, and narrow it with source and status filters. Colored chapter separators organize the collection; their counts and progress bars describe the manual checklist. Detected update status and verified installation remain separate.

**Check for updates** refreshes source evidence. A successful current result, available update, failed check, sign-in requirement and manual page review each keep their own status. If a source fails, use its retry or original-page action; a failed check does not establish that the mod is current. Canceling a scan retains completed results and the last complete catalog.

For an update with one validated eligible file, use **Update now**. If the project has multiple files, editions or unresolved compatibility, use **Choose file** and review the exact archive. Downloads stay with MO2; its normal installer and FOMOD choices remain available. The queue reports actual preparation, download, installation and verification results. A manual checklist tick does not install anything.

The recovery workflow retains actual mod-file backups. Use the exact recovery preview before restoring: later edits or changed ownership can stop a restore to protect current files. Do not treat a profile's load-order text backup as a backup of every mod directory. [Update Workshop](Update-Workshop) explains queue, review and recovery behavior.

## Pages outside the automatic adapters

Use **Watchtower** to track an exact release page, creator page or changelog. Choose a named site session when access needs sign-in. The first successful read establishes a baseline; later page changes stay available for review. Optional due checks run while the app is open. A changed page remains a page observation until its exact archive and compatibility have been reviewed.

The draft Creator Hub/source-continuity work is tracked separately in [PR #27](https://github.com/Herbertofury/Modhunter/pull/27). Automatic creator follows and cross-site migration management are not part of the 0.12.1 release contract.

## If the app is disconnected

Check that MO2 is running, its selected profile has finished loading, and the matching plugin is installed. Select **Find MO2** again and inspect **Details** if the bridge remains unavailable. Changing MO2 profiles invalidates old queued work rather than applying it to the new profile.

**Import MO2** remains independently usable for read-only local inspection: select the profile's `modlist.txt`, then use **Instance INI** or **Mods path** for split directory layouts. Offline inspection does not provide MO2-authenticated downloads or native installation. Watchtower and the game modes are optional and do not need to be enabled for mod maintenance.

## Release verification

The release page contains the installer, portable app, independent MO2 components, standalone HTML, verification archive and SHA-256 manifest. The Windows release gates cover the compiled desktop, actual WebView2, MO2 fixtures, plugin installation, installed-app launch/uninstall, source/lifecycle contracts, full-library rendering and package identity. The verification archive records the tested commit and executable identity. Account-specific Nexus access, particular FOMOD choices and in-game behavior still depend on the user's actual setup.
