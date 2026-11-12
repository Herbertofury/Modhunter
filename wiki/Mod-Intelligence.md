# Per-mod plugin and source intelligence

[Home](Home.md) / [Rust desktop](Rust-Desktop.md) / [Update Workshop](Update-Workshop.md)

Modhunter shows plugin state, conversion changes, master problems and checked source facts directly on each checklist and Workshop row. Open the mod inspector for the underlying plugin copies, observations and suggested next steps. The **Mod health** filter narrows the list to maintenance, ESL review, winning light copies, ESLifier changes, master issues, restricted bug tabs or unverified bug evidence. Search includes plugin names and analysis states; `health:source-changed` and `esl:compaction` are useful examples.

The Rust desktop and Rust analyzer perform local inventory, record analysis and hashing. The Python/Qt adapter captures MO2's actual state and paths on MO2's UI thread, then sends owned data to a background Rust helper. The shared dashboard presents the results. No analysis command changes a plugin's flags, renumbers records, rewrites installed files or launches a conversion tool.

## What a mod row tells you

| Row evidence | Meaning | Next step |
|---|---|---|
| **Light / full plugin** | The winning plugin's header reports its current format. Active, inactive and unknown activation states remain distinct in details. | Inspect the actual winning copy if the state differs from your expectation. |
| **Light copy wins** | A different light plugin copy overrides a full copy in this mod. | Open the winning mod. This observation alone does not identify a conversion tool. |
| **ESL candidate** | The deeply scanned own-record IDs meet the conditional flag-only format checks. | Review in xEdit or ESLifier, including external references, runtime, save and asset concerns. |
| **Compaction review** | Flagging alone is insufficient; renumbering would need a separate review. | Review dependencies and the conversion tool's qualification checks. |
| **ESL check blocked** | A format, runtime or dependency check failed. A light header can coexist with invalid records. | Read the precise reasons and resolve them before considering conversion. |
| **Record scan incomplete** | Declared records/groups do not match the observed scan, or another scan boundary prevents a conclusion. | Investigate the file with an appropriate plugin editor; eligibility remains unverified. |
| **ESLifier needs review** | Recorded source/output hashes or the winning conversion changed. | Inspect the source, converted output and file priority; review or rebuild through ESLifier's own workflow. |
| **Master issues** | Missing, inactive or late-loading masters were observed. | Install/enable the correct dependency or fix plugin order before using the affected plugin. |
| **Bugs N** | The public Nexus Bugs tab displayed that badge count at the stated check time. | Read relevant reports and their versions. This badge is not treated as an open, resolved or total report count. |
| **Bugs tab not observed** | The fetched page did not establish a visible Bugs tab. | Check the source and support instructions. This does not establish why the tab is absent. |
| **Disabled / hidden / locked bugs** | Explicit page evidence established that state. | Read the author's support instructions. No author attribution is inferred. |
| **Bug check unavailable / unverified** | The request failed, was challenged, or has not established the state. | Retry or open the original page. Unknown never becomes zero. |

The inspector includes direct source, Files, Requirements, Changelog and Bugs/Issues links where the provider identity is established. Nexus requirements and changelogs link to the description, where those sections live; the site activity log is not mislabeled as a changelog. GitHub sources retain their issue/release links and do not inherit Nexus actions from an old metadata ID.

## Connect ESLifier evidence

The recognized integration is [MaskPlague's ESLifier](https://github.com/MaskPlague/ESLifier). Other converters can still produce observable light overrides, but their provenance is not invented.

### Live MO2

If ESLifier's official MO2 plugin has its **ESLifier Folder** configured, Modhunter reads that setting and locates `ESLifier_Data` for Skyrim or `ESLifier_FO4_Data` for Fallout 4. These folders belong beside ESLifier's executable; they are not assumed to live in MO2's mods directory.

Alternatively, open a mod inspector and choose **Connect ESLifier records**. The native folder dialog asks for the actual data folder containing ESLifier's JSON records. The dashboard cannot supply an arbitrary executable or filesystem path through its HTTP API.

### Offline Rust desktop

Import a real MO2 profile, select **ESLIFIER DATA** in the native toolbar, and choose the matching data folder. **Plugin audit** runs a deep read in the background. Normal profile import and startup use the faster header path. The live dashboard's **Analyze plugins** button uses the live organizer; the desktop's own audit control is for the imported offline profile.

### How conversion changes are established

Modhunter checks ESLifier's real `mod_manager_mode: 2` settings, profile name when recorded, owned output folder, `original_files.json`, `new_file_hashes.json`, and the flagging/compaction membership files. Paths in those records are matched only to files already supplied by MO2 or the local import. Recorded arbitrary paths are never opened as extra sources.

Deep analysis hashes the current original and converted plugin bytes afresh. It distinguishes:

- **Matches recorded ESLifier output:** original and winning light-output hashes match the selected records, and ESLifier does not mark the output externally changed.
- **Original changed:** the original hash changed while the winning converted output still matches the older record.
- **Output changed:** the converted bytes differ, or ESLifier's sticky external-change flag is set.
- **Original and output changed:** both need review.
- **Recorded conversion no longer wins:** another physical copy now wins in this profile.
- **Unknown:** records are missing, conflicting, outside the supplied sources, for another profile, unreadable or otherwise insufficient.

ESLifier's files are mutable tool state, not an immutable receipt. Membership in a conversion list does not by itself prove that the operation succeeded. A matching plugin hash also does not establish that all patched scripts, FaceGen, voice files, other assets or newly installed dependents are current. Modhunter states this scope in the inspector.

## What happens after an update

The MO2 adapter subscribes to profile, mod installation/removal/movement/enabled-state changes and plugin refresh/movement/state changes. An explicit Refresh also invalidates earlier local conclusions.

With the matching Rust helper installed, it starts background header analysis. When ESLifier data is configured, or a deep audit has already been requested for this profile during the session, the refreshed profile receives a new deep record/hash audit. Changed source bytes can therefore flag an old winning converted copy even when the plugin name remains the same. Explicit **Analyze plugins** always requests the deeper audit.

Results carry a context epoch. Switching A → B → A, changing paths, or closing the service prevents an older worker from replacing the current facts. Selecting a different ESLifier folder invalidates previous provenance before the new scan. A failed native folder selection/save does not silently replace the previous effective setting.

In the live bridge, a superseded scan also stops its own read-only helper process so the latest profile does not wait behind an obsolete deep scan.

The Update Workshop carries actual conversion changes and active master/record problems into its existing compatibility review. Bug-report counts and a valid light header do not independently become installation blockers. CSV exports include light/full/override counts, candidate/compaction/conversion/master facts and observation times; JSON update-plan exports carry the row evidence.

## Nexus checks and freshness

Opening a Nexus-backed mod's inspector requests its public Bugs page, reusing fresh cached evidence. **Refresh Bugs page** explicitly checks again, subject to provider rate limits. The last observed result also appears on the checklist and Workshop row. Modhunter does not fetch thousands of public mod pages during startup.

The public reader has two concurrent workers, same-mod redirect validation, a 1 MiB page limit, a 10-second request budget and scalar-only persistence. It does not use browser cookies or retrieve Nexus credentials. Successful public observations are cached for 15 minutes, transient failures for a shorter retry period, and HTTP 429 responses retain a bounded cooldown. A verification challenge remains unavailable; it is not bypassed.

Project availability and update timestamps can also come from the existing Nexus update audit. The inspector shows the source-check time separately from the bug-check time. Refreshing the Bugs page does not claim to refresh stale API metadata. Hidden/removed/moderated provider statuses remain separate from a generic HTTP 404 or request failure.

The public badge is displayed as **Nexus displayed bug count**. The current API does not supply an authoritative mod bug-tab enabled flag or all the desired count semantics, and page structures can change. Absence of a tab is never reported as an author disabling it. Explicit disabled/hidden/locked parser paths have fixture coverage; their presence on every live Nexus layout is not guaranteed.

## Performance and bounded work

Normal import caches metadata and header facts; it does not recursively scan asset trees. Deep audits stream plugin bytes through bounded buffers and use fresh SHA-256 values. File, profile, record-count and nesting limits turn excessive or malformed inputs into explicit incomplete/unknown outcomes.

Live catalog responses contain compact summaries. Full paths, hashes, notes and detailed actions are fetched for the inspected row. Native selection and per-download membership use exact indexes, and Qt capture avoids building provider health for the whole load order. The existing lightweight revision poll remains independent of deep scans.

See [Rust Desktop](Rust-Desktop.md#measured-import-performance) for measured import results and [local QoL verification](../native/evidence/qol-local-verification.md) for the candidate's executed checks and remaining native gates.

## Qualification boundaries

The record audit reads TES4 header fields, MAST names, record/group framing and own-record FormIDs. It does not decode every record payload or establish existing-save, external-reference, complete asset, DLL or dependent-plugin safety. Extended ESL and VR support require evidence for the selected runtime. Starfield's different small/update/medium flags are recognized, but conversion qualification remains unsupported.

The supported next step is a precise **candidate**, **blocked**, **review**, **already light** or **unknown** result. A green conversion-safety guarantee would require evidence this scan does not possess.

### Primary implementation references

- [ESLifier source and documentation](https://github.com/MaskPlague/ESLifier), especially `src/compact_form_ids.py`, `src/data_holder.py`, and the official MO2 integration plugin.
- [xEdit ESL candidate script](https://github.com/TES5Edit/TES5Edit/blob/dev-4.1.6/Build/Edit%20Scripts/Find%20ESP%20plugins%20which%20could%20be%20turned%20into%20ESL.pas) and [xEdit releases](https://github.com/TES5Edit/TES5Edit/releases).
- [MO2 2.5.2 plugin-list API](https://github.com/ModOrganizer2/modorganizer/blob/v2.5.2/src/pluginlistproxy.h) and [organizer API](https://github.com/ModOrganizer2/modorganizer/blob/v2.5.2/src/organizerproxy.h).
- [Nexus GraphQL documentation](https://graphql.nexusmods.com/) and [official ModInfo schema](https://github.com/Nexus-Mods/node-nexus-api/blob/master/docs/interfaces/_types_.imodinfo.md).
