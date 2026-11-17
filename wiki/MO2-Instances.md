# MO2 Instances

[Home](Home) / [Daily use](Daily-Use) / [Rust Desktop](Rust-Desktop)

Open **MO2 Instances** in the desktop toolbar to find MO2 and install the matching Modhunter plugin into the applications you choose. Both the Windows installer and portable desktop include the complete plugin resource. You do not need a separate plugin download for this workflow.

## Discover your installations

Opening the manager checks known locations automatically: saved folders, imported profile/configuration hints, registered applications, running MO2 executables, Windows shortcuts and global instance configurations. Use **Add folder** for a modding folder, secondary disk, portable collection or explicitly selected network location. **Search drives** lets you choose native drives and remembered folders for a complete recursive search.

A completed scan describes the scope it actually checked. Known-location discovery does not claim to search every folder on every drive. Deep discovery has no installation-count or directory-depth cap. Unreadable locations, malformed configurations and linked directories appear as explicit issues. Links are not followed into loops or unrelated locations.

You can stop a scan. Valid findings and the previous inventory remain available, and the unfinished scope is marked incomplete. Search and status filters change what is visible without discarding your selected applications.

## Applications, instances and profiles

The plugin belongs in the **application's** `plugins/mo2_update_arsenal` directory. A global instance is a configuration and collection of profiles; it is not necessarily a separate MO2 application or plugin destination.

| Item | What the manager shows |
| --- | --- |
| MO2 application | Executable folder, observed version/architecture, plugin version and running-process observations. This is the selectable plugin destination. |
| Portable instance | A `ModOrganizer.ini` beside that application's executable. A `portable.txt` file locks the application to portable mode; an adjacent INI can exist without that lock. |
| Global instance | A configuration in MO2's shared global-instance location. Several unlocked applications may be able to open it. |
| Shortcut relationship | An actual executable target and explicit instance choice observed in a Windows shortcut. |
| Profile | Profile name, configured location, selected-state observation and modlist path. Missing modlists stay visible as issues. |
| Live session | A running Modhunter bridge whose endpoint, protocol and profile have passed a health probe. The manager rechecks it when you connect. |

Shared global relationships are labeled as available choices, not exclusive ownership. A remembered profile name or process ID alone does not establish a live connection. Running sessions without a proven owning application remain visible separately.

## Install or update selected applications

1. Select the application checkboxes you want. You can select more than one.
2. Choose **Review selected**. Read the exact destination, current and bundled versions, affected instances, backup root and any blocker for every selected application.
3. Close MO2 for the selected application if requested. An independently identified MO2 process at another application path may remain running. An unreadable process identity requires you to resolve the ambiguity.
4. Confirm the reviewed targets. The app installs them sequentially and shows a separate result for each one.
5. Open MO2, choose the desired instance/profile, and let it load. Use the manager's verified live-session **Connect** action, or **Find MO2** in the desktop toolbar.

Every selected target must pass the preview before confirmation is enabled. A review expires after two minutes. A new review or scan replaces it, and a consumed review cannot be replayed. The installer rechecks the complete installed-plugin fingerprint and MO2 executable before mutation; a changed target requires another review.

The matching plugin resource is verified against a manifest embedded in the compiled desktop. Editing an adjacent manifest cannot make altered files trusted. An incomplete or mixed release blocks installation and explains how to restore the matching package.

## Progress, backups and stopping

Existing plugin data is retained. The previous complete plugin is backed up under the selected application's `ModhunterBackups` folder, outside MO2's plugin search directory. A failed replacement uses the existing rollback path. Success is shown only after the resulting plugin files match the bundled hashes.

**Stop after current installation** leaves the current staging/replace/rollback transaction to finish and stops before touching later targets. Closing the main desktop during an installation requests the same safe stop and shows the manager; close the app again after its result appears. Application reset is unavailable while a plugin installation is active.

The manager remembers selected discovery folders and its last installation results. Reopening its window preserves current work. If the whole application session ends unexpectedly during installation, the journal reports an unverified interrupted outcome; it does not replay an old approval automatically.

Removing the Modhunter desktop app does not remove an independently installed MO2 plugin, mod collection or its retained data.

## Other entry points

The portable package still includes `Install_MO2_Plugin.bat` and the read-only `Check_MO2_Readiness.bat` tool. The independent MO2 plugin archive remains available for users who prefer its browser dashboard. Offline **Import MO2**, Watchtower and every game mode remain separate choices.

The native acceptance receipt distinguishes actual Windows/WebView2 installation into disposable application folders from the separate real-MO2 integration tests. Neither fixture proves a particular personal account, modlist, FOMOD choice or beta MO2 build.
