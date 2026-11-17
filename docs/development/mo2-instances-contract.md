# MO2 Instances native contract — 0.13.0

This feature discovers MO2 applications and instances, then installs the bundled matching plugin into explicitly reviewed application folders. It is independent of Watchtower and the existing offline/live dashboard. Global instances are shared choices; an application is never assigned exclusive ownership without evidence.

## Ownership

- Discovery: `mo2_discovery.rs` and `mo2_discovery/windows.rs` — application/instance/association inventory, platform seeds, complete cancellable scans, core fixtures.
- UI: `instances.html`, `instances.css`, `instances.js`, `locales/instances.json`, shell entry button/handler, focused browser tests.
- Installer/release: PowerShell script and harness, generated `resources/mo2-installer`, Tauri resource mapping, package workflow ordering and release/NSIS validation.
- Host integration: `instances_windows.rs`, `main.rs`, `windows.rs`, command capabilities/build registration, version bump, native UI smoke and user documentation.

## Transport and authority

The sole privileged renderer is label `mo2-instances`, exact bundled asset `/instances.html`. Command: `instances_command {kind, value}`. State event: `modhunter-instances-state`, payload is the raw snapshot. Responses are `{ok:true,state,...}`; rejected commands return a native error string. Renderer text is never filesystem authority for installation. Exact backend IDs and a single-use preview token authorize the reviewed targets.

All snapshots carry monotonic `revision`. Scanner/install work has distinct operation IDs. A canceled or superseded scan cannot replace the last complete inventory. Partial findings remain explicitly partial and can be shown alongside the last complete inventory.

## Snapshot

```text
{
 revision, version,
 package: {available, valid, version, commit, message},
 inventory: {
   installations: [{id,directory,executable,version?,architecture?,portableLocked,
     plugin:{version?,status},runningPids:[],sources:[]}],
   instances: [{id,name,kind,configPath,baseDirectory?,gameName?,gamePath?,
     selectedProfile?,profilesDirectory?,modsDirectory?,
     profiles:[{name,path,modlistPath,selected}],sources:[]}],
   associations: [{installationId,instanceId,kind,evidence}],
   issues: [{path?,kind,message}], scope, complete
 },
 scan: {id,status,mode,directoriesVisited,installationsFound,instancesFound,
        currentPath,elapsedMs,roots:[],message,partialInventory?},
 roots: [{path,label,kind}],
 sessions: [{id,endpoint,profileId,profileName,version,pid?,installationId?}],
 install: {id,status,total,completed,currentId?,stopRequested,results:[
   {installationId,status,message,version?,previousVersion?,pluginDirectory?,
    backupDirectory?,preservedData?,rollback?}
 ]},
 settings: {theme,locale}, error
}
```

Discovery installation status is an observed version/status, not a complete integrity claim. `plugin.status` values: `missing`, `matching`, `different`, `unknown`, `invalid`. A matching version may still need repair; only verified preflight decides installation readiness. Association kinds: `portable`, `shortcut`, `availableGlobal`. Instance kinds: `portable`, `global`, `external`.

Scan status: `idle`, `running`, `cancelling`, `complete`, `cancelled`, `failed`. Install status: `idle`, `running`, `stopping`, `complete`, `cancelled`, `failed`; result status: `queued`, `installing`, `installed`, `updated`, `failed`, `cancelled`.

## Commands

| Kind | Value | Behavior/result |
| --- | --- | --- |
| `state` | none | Current snapshot; initial open starts known-location discovery once |
| `scan` | `{mode:"known"}` or `{mode:"deep",roots:[path...]}` | Background scan; deep roots must come from native drives/remembered root selection; no result or depth cap |
| `cancelScan` | `{operationId}` | Cancel that scan, preserve complete prior inventory and label partial findings |
| `addFolder` | none | Native folder picker, remember the selected discovery root and scan it; selection is not permission to install |
| `previewInstall` | `{installationIds:[id...]}` | Native preflight; returns `preview` below |
| `install` | `{token}` | Consume exact unexpired preview, recheck source/targets, run selected transactions in the background |
| `cancelInstall` | `{operationId}` | Stop after the current transaction; never kill an installer during replace/rollback |
| `connect` | `{sessionId}` | Probe the selected exact live session again and connect via the existing host path |
| `launch` | `{installationId,instanceId?}` | Launch the selected known executable with the explicit native MO2 instance choice; no shell interpolation |
| `openFolder` | `{installationId}` | Reveal the known application folder |

```text
preview: {
 token, expiresAt, package:{version,commit}, canInstall,
 targets:[{installationId,directory,pluginDirectory,backupRoot,
   previousVersion?,version,action,canInstall,blockers:[],
   affectedInstances:[{id,name,kind,relation}],message}]
}
```

Preview is read-only. The install button is enabled only when every selected target has a successful preflight; users can deselect blocked targets. Preview tokens expire, are invalidated by new selection/scan as appropriate, and cannot be replayed. Root/source identities are rechecked before mutation. Progress comes from actual work phases and terminal receipts, with no invented percentages.

## Installer and package boundary

`resources/mo2-installer/SHA256SUMS.txt` is embedded in the compiled Windows app. The host verifies the installed resource against those pinned bytes, including the installer script, before invoking Windows PowerShell by its known system path. Native file arguments use process argument arrays, never a composed shell command.

Unattended script mode: `-MO2Directory ... -Json -ExpectedVersion ... -ExpectedCommit ... -ExpectedManifestSha256 ...`; `-Preview` performs read-only preflight and returns `installedFingerprint`. Installation adds `-ExpectedInstalledFingerprint ...` from the reviewed preview. JSON mode requires the verified packaged bundle. One bounded UTF-8 JSON result; exit 0 success, 2 expected refusal, 1 unexpected/rollback failure. Keep interactive .bat compatibility.

Result fields: `schemaVersion`, `ok`, `status`, `code`, `version`, `commit`, `targetDirectory`, `pluginDirectory`, `previousVersion`, `backupDirectory`, `backupRoot`, `preservedData`, `rollback`, `message`, `installedFingerprint` for preview. Existing plugin/data is staged, backed up and restored on failure. Known running MO2 at another executable path may remain open; the selected target or an unreadable ambiguous MO2 process blocks replacement. Recheck before commit. Concurrent installers for one target are excluded.

The same complete nested resource is included in NSIS and portable packages. Existing portable root tools remain compatible, and all duplicated plugin bytes must match. Outer manifests include the nested manifest. NSIS acceptance verifies real installed resource bytes and installation into an external disposable MO2 application, then verifies desktop uninstall preserves that external plugin/data.

## Acceptance

Exercise current/legacy global instances, portable/unlocked/locked apps, shortcuts, shared globals, Unicode/split paths, inaccessible/malformed/link/cancellation cases and complete uncapped scanning. Browser tests exercise selection, preview, real per-target state, stale replies, cancellation, empty/error states, dark/light and narrow geometry. Native WebView2 must open the real manager, discover disposable targets, preview and install through its actual IPC, then verify matching bytes, retained data/backups, unselected-target preservation and denied foreign-origin IPC. Existing Windows/MO2/library performance gates remain required.

## Additional daily-use acceptance

The active user request also covers the main library:

- Source checks must identify the installed source and file/addon lineage. An optional Fishing Integration Patch at 1.0.1 must not be compared with the main Siege at Icemoth file at 1.5.1 solely because they share a Nexus mod ID. Preserve uncertain and missing evidence, and show the installed/matched file identity.
- Improve update-check latency and request work on equivalent complete inventories. Reuse project file evidence across installed components with distinct per-row outcomes; respect freshness, explicit fresh audits, profile epochs, cancellation, ownership and last-good cache rules.
- Read actual MO2 separator colors from live and imported metadata and render them faithfully. A generated theme color is only a fallback when no configured color is available.
- Use the available window width and height for the library; preserve the complete uncapped result set, all modes, notes and source/health details.
- Keep a compact bottom search/control dock available while the list scrolls, reserve its space, retain keyboard access and sticky headers.
- Restore a real Bugs column using available evidence, with unknown distinct from zero. Preserve user column choices and expose the related mod details.

The frontend owner edits main library presentation and browser fixtures. The core owner edits file identity/check caching and separator metadata adapters. Host/release acceptance must cover their integrated exact source, including the packaged matching plugin.
