# Modhunter 0.10.2: MO2 2.5.3b12 / Skyrim SE setup guide

## Full release and compatibility

This package retains the full Rust desktop, Watchtower, existing game modes and MO2 plugin. It adds a guarded Qt forward-runtime path, version comparison strategies, service-outage backoff and a local readiness check. Use the **complete portable ZIP**, not a Python-only copy and not the older 0.10.0 package.

The release must pass actual MO2 **2.5.2** tests and a separate actual Windows **Qt 6.11.1 / PyQt 6.11.0** compiled-receiver test using the **same Qt bridge DLL in your package**. The new runtime check is relevant to the newer MO2 development line, but it is **not an execution test of the exact 2.5.3b12 archive**. That archive was not available in the build environment. No claim is made that your account, mods, FOMOD choices or saves were tested.

The helper accepts only the explicitly covered Qt 6.7 and 6.11 minor lines, refuses an older runtime or a different loaded PyQt runtime, and performs a disposable live signal/receiver identity test on MO2's GUI thread. Unknown Qt lines remain disabled instead of bypassing the guard. Modhunter does not install or replace MO2's Qt, Python, OpenSSL or USVFS DLLs.

## Install and connect

1. Export your existing Modhunter progress backup. Close MO2 before installing the plugin. Extract the entire portable archive into a new folder; leave its checksum manifest, `BUILD_INFO.json`, `MO2` folder and helper binaries together.
2. Run **Check_MO2_Readiness.bat** and select the folder containing **ModOrganizer.exe**, not the Skyrim folder or the `mods` folder. It checks the executable's version/64-bit header, Qt in the installation root or `dlls` subfolder, and key package checksums. It saves a small JSON report beside the script without credentials, profile names or personal paths. An absent/older Modhunter plugin is expected before installation. The check does not launch MO2 or read game/mod contents.
3. Run **Install_MO2_Plugin.bat** and select that same MO2 folder. This is an **MO2 application plugin**, not an archive to install in the left-hand Skyrim mod list. The installer verifies the package, preserves the prior plugin under `ModhunterBackups`, carries forward its regular fallback `data` folder, and rolls back a failed replacement. It does not alter your mod archives, load order or saves.
4. Start your **MO2 2.5.3b12** installation. Check **Help > About** for the beta suffix because Windows file-version resources can omit it. Select the intended **Skyrim Special Edition** instance/profile. The Modhunter bridge starts automatically once the profile is ready; opening the Quest Board tool is optional. Keep Nexus sign-in in MO2. Start **Modhunter.exe**, use automatic discovery or select **Find MO2**, and choose the correct session when more than one instance is running.
5. Confirm the game, profile, mod count and runtime shown by the live connection before checking updates. An offline import is read-only and is not proof of an authenticated live connection. Rerun the readiness check after installation to confirm the installed plugin is 0.10.2.

**Details** in the desktop toolbar shows the connected plugin version, Qt runtime, callback status and native/helper readiness without credentials. **Refresh** and **Plugin audit** now operate on the live dashboard when connected.

The compiled desktop uses WebView2. The separate Windows installer can bootstrap WebView2 when missing; it does not replace the MO2 plugin installation step. There is no need to install Node, Rust, Qt SDKs or Python packages to use the release ZIP.

## Verify your connection and updates

| Test | Expected result |
| --- | --- |
| Correct instance | The live dashboard shows your selected Skyrim SE profile, not a fixture/sample or another running MO2 instance. |
| Native Nexus file list | Check one installed, accessible Nexus mod and open **Choose file**. File IDs, names and versions should appear. An unavailable native helper or authentication failure must be explicit, not an empty successful result. |
| Review before installation | Review one update's exact file, required Skyrim/SKSE runtime, dependencies and installation options. Project-version comparison alone does not prove DLL/SKSE compatibility. |
| One controlled update | With a backup and an expendable test profile where appropriate, download one reviewed update. MO2 must still own its native FOMOD/replace prompts. Confirm the installed result before trying a batch. |
| Responsiveness | Search and switch tabs while a scan runs. Cancel a scan, then run another. Last complete results and unsaved notes should remain. |
| ESL provenance | Run **Plugin audit** and select **ESLIFIER DATA** when applicable. Review recorded source/output hash changes. A conversion candidate is not a universal safe-to-compact guarantee, especially for existing saves. |
| Account-only page | In **Watchtower > Site sign-ins**, open the correct site session, sign in yourself, then check a chosen page. A login/challenge/failure must retain the prior baseline rather than announce a mod update. |
| Evidence of a new version | Calendar/build/prerelease labels should show the appropriate comparison; a stable-to-beta change or ambiguous numbering should require review. |

Do not start with an unattended update-all run on your only working setup. MO2 profiles can share installed mod directories, so a separate profile alone is not a backup of mod files.

## Troubleshooting without destructive workarounds

**Qt/native file-list error:** keep the report and the exact error text. Reinstall the complete matching Modhunter package with MO2 closed. Do not copy Qt DLLs from another application into MO2. The live helper self-test and static readiness checker cover different things; a static pass does not override a runtime failure.

**No connection:** open the plugin in the intended MO2 process, then use Find MO2. Multiple instances require an explicit selection. A loopback address from the plugin can be used in Connect. Never publish its authorization token.

**Hidden or removed Nexus listing:** keep the original source identity for review. Modhunter's checking code does not erase `nexusId` or rewrite `meta.ini` when a provider is unavailable. An upstream beta-12 report describes MO2's own bulk checker removing IDs for hidden mods; this is a reported MO2 behavior, not a demonstrated fix in this package. See [upstream issue 2414](https://github.com/ModOrganizer2/modorganizer/issues/2414).

**Provider outage or quota:** the Rust public-source adapters persist host cooldowns for 429 and 503, including bounded Retry-After dates. Force-refresh does not bypass the hold. An unavailable provider is not reported as up to date.

**Rollback:** close MO2 and preserve your current progress/export first. The installer prints the prior plugin backup location under `ModhunterBackups`. Restore only that plugin folder when needed; do not restore or delete game files as a side effect. Do not assume a plugin rollback reverses a mod installation or save change.

## Evidence scope

The portable `Evidence` folder includes actual WebView2 and Qt-forward receipts; the release's verification ZIP also includes the actual MO2 fixture and Chromium receipts. Hashes bind these to the built package and source. Browser interaction fixtures, native runtime fixtures, exact beta acceptance and your own live account acceptance are separate layers. The source audit and implementation map is in `GameSync-Update-Techniques.md` in the portable folder or the repository wiki.
