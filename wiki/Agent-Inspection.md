# Agent inspection

The Modhunter CLI exposes Skyrim inspection over MCP stdio. Launch it with `mcp-stdio --legacy-2025-11-25` for clients using initialization and the 2025-11-25 protocol, including compatible Codex and Claude Code clients. For example, a client configuration can set its command to the absolute path to `modhunter-cli.exe` and its arguments to `["mcp-stdio", "--legacy-2025-11-25"]`. A client using the 2026-07-28 protocol can omit the legacy flag.

### Live MO2 inventory and per-mod evidence

Append `--mo2 PROFILE_ID http://127.0.0.1:PORT/` to the command when the Modhunter plugin is installed. The plugin publishes its profile ID and endpoint in a descriptor under `%LOCALAPPDATA%\Modhunter\mo2-sessions`. Choose the descriptor for the intended running instance and profile. The CLI obtains its native session on the loopback connection.

Call `mo2_mods` with empty arguments. Each page includes the profile, revision, total and rows with their Mod Organizer names, state, priority, version and available Nexus ID. Pass the returned `revision` and `nextOffset` (as `offset`) for the next page. A null next offset marks completion. A profile switch, revision change or plugin restart invalidates the cursor. Start a new traversal to obtain a consistent inventory. The plugin builds each page directly from the live inventory. Raw notes and session tokens stay out of the MCP output.

Call `mo2_mod_evidence` with a row's `itemId` and the inventory `revision`. Its update decision includes the installed and matched Nexus file IDs when known, the matched file version, the project version, the reason, and the available check and expiration times. Its health results include available source and Bugs-page observations, published GitHub advisory ranges and their installed-package applicability, and suggested actions. Follow `nextOffset` with the same `evidenceRevision` to read every receipt, advisory and action for that row. If the observations change, start the row again. This query reads cached observations without issuing provider requests.

In the mod library, **File review** shows rows where the installed archive, provider release history or version needs review. **Security advisories** shows mods with a current published advisory observation whose affected range includes the installed package version. Open **Details** to see the matched Nexus file alongside the project's version, or inspect the repository notice and affected range.

The local tools also accept `--bsa ALIAS PATH` and `--plugin ALIAS PATH` to expose selected launch-time BSA directory and plugin header snapshots. `spid_preview` parses text supplied by the caller. These inspection tools operate independently of a running MO2 connection.

### Public Watchtower watches

Use `--watchtower read CONFIG_DIRECTORY` to inspect the saved Watchtower configuration chosen at launch. Use `--watchtower manage CONFIG_DIRECTORY` to also register anonymous JSON or RSS/Atom sources and explicitly check one source. The directory must exist. An empty directory supports an independent Watchtower store.

- Call `watchtower_search` with an optional `query` to find a public watch by title or URL path. Follow `nextOffset` with the same `query` and `revision` to read the next page. Results include observation time, hash, state and retained history count. Account-bound, removed and credential-parameter watches stay private. Public URL query values, personal notes and page bodies stay in the selected store. Hide page titles uses the site name for search and display. Privacy screen also hides paths.
- Call `watchtower_add` with `url`, `format` (`json` or `feed`) and an optional `title`. A repeated add returns the matching watch. The first `watchtower_refresh` records its baseline.
- Call `watchtower_refresh` with the `watchId` and `watchRevision` returned by search or add. The shared reader checks the complete public document and records the result in Watchtower. A changed document has a different observation hash and a retained history entry. Paused, muted and rate-limited watches retain their previous observations.

Search works while the Windows app owns Watchtower. Add and refresh use the app's exclusive ownership lock for the duration of the operation. Close the owning app to write to that same store, or select a separate configuration directory at launch.

### Enable and disable selected mods

MO2 and the MCP launcher must both grant access. In MO2 Settings → Plugins → MO2 Update Arsenal, set `agent_mod_control_scope` to JSON mapping the intended profile ID to exact internal mod names. For instance, `{"my-profile-id":["My Disposable Mod"]}` permits one mod. The default is an empty object. Add `--allow-mod "my-profile-id" "My Disposable Mod"` alongside the corresponding `--mo2` argument. Repeat the permission flag for further names.

Read `mo2_mods`, then call `mo2_set_enabled` with its `itemId` (the row's `id`), `name` (the row's `mo2InternalName`), `revision`, `expectedEnabled` and desired `enabled`. The plugin validates membership, native path, version, state, profile and revision in the Qt thread, then calls MO2's mod list and reads the result back. MO2 can also update plugin visibility as part of its usual mod activation behavior. A changed profile, stale revision, active content operation, running game or modal dialog rejects the operation. After a lost reply, inspect fresh state before retrying. Mod Hunter's production acceptance keeps the user's established mods outside the permission list.

### Skyrim records and load order

Install the Windows .NET 10 runtime and extract the [houseCARL 2.2.0 release](https://github.com/Avick3110/houseCARL/releases/tag/v2.2.0) into a trusted local tools directory. Add `--housecarl EXE DIRECTORIES_JSON` to the Modhunter MCP launch arguments. Point EXE at the upstream `housecarl-mcp.exe`. Create a private JSON file with four absolute directory paths, for example:

```json
{
  "dataDir": "D:/Games/Skyrim Special Edition/Data",
  "modsDir": "D:/MO2/mods",
  "profileDir": "D:/MO2/profiles/Test",
  "overwriteDir": "D:/MO2/overwrite"
}
```

Call `skyrim_load_order` with empty arguments to inspect the configured profile. Call `skyrim_record` with `{"formId":"00000F:Skyrim.esm","view":"tree"}` to read the ordered providers and winning override of Gold001. The view also accepts `summary` and `everything`. Results carry the upstream version, tool name and diagnostic messages, including plugin load failures.

Use `skyrim_inspect` for these additional reads. Each call returns the underlying engine's result and its scope.

- `{"kind":"check","formId":"00000F:Skyrim.esm","family":"errors"}` runs houseCARL's reference checks on that record. Select `scripts` or `facegen` to examine that family. The response retains exclusions and upstream diagnostics.
- `{"kind":"archive","name":"Skyrim - Meshes0.bsa"}` returns the complete upstream archive entry count for a file in the configured game Data directory. Use the separate launch-selected BSA directory tool to page through paths.
- `{"kind":"asset","path":"meshes/example.nif"}` checks a Data-relative asset and its providers.
- `{"kind":"nif","path":"meshes/example.nif"}` reads the mesh structure and reports any unavailable blocks.
- `{"kind":"facegen","formId":"0A2C8E:Skyrim.esm"}` inspects the NPC's mesh and tint providers.
- `{"kind":"distributor","layer":"spid","filter":"example"}` reads distributor declarations. The supported layers are skypatcher, spid, kid, bos and flm. The filter is optional.
- `{"kind":"skse","filter":"EngineFixes"}` inspects the SKSE plugin inventory and matching config. The filter is optional.

Modhunter starts houseCARL on the first read and reuses it across reads in the same MCP process. The configured roots stay fixed for the session and houseCARL uses a private temporary configuration directory. Keep installation paths, directory selections and local results private.

### Reference implementations

The author Antiarc123 distributes MCP–MO2 version 1 and its source on [Nexus Mods](https://www.nexusmods.com/site/mods/2549). The suggested GitHub repository returned 404 during verification on November 24, 2026. The Nexus description documents a separate MCP server on port 47525, with optional writes and launches. Its published workflows include mod and plugin state, conflicts, virtual files, logs, downloads, installers and executable control. The archive tool list and runtime parity remain unverified.

The [houseCARL 2.2.0 release](https://github.com/Avick3110/houseCARL/releases/tag/v2.2.0) advertised 31 tools during an MCP handshake on November 24, 2026. Its release archive SHA-256 is `677c0c2eadedc4c55135fcb5cf1afe5a6efe5a5f2e27b2c65d0a42e43e4dab20`. Independent read-only calls to `housecarl_load_order_status` and `housecarl_records` read an actual Skyrim SE profile. These observations verify those upstream paths. Modhunter's own live catalog, scoped asset previews, and access to a separately launched upstream server require distinct acceptance checks.

### houseCARL capability coverage

The names below omit the upstream `housecarl_` prefix. Each group reflects the 2.2.0 runtime tool listing. “Isolated” describes a disposable mod tree with a copied game master. Modhunter's restricted companion delegates specific reads and keeps upstream warnings visible.

| Upstream tools | Local evidence through November 24, 2026 |
| --- | --- |
| records, load_order_status | Production read via Modhunter. Record tree resolved competing providers and reported one plugin load failure. |
| asset_status, nif_inspect | Production read via Modhunter. Verified an enabled mesh's provider and internal NIF blocks, absent-path handling and an NPC's FaceGen pair. |
| layer, skse | Production read via Modhunter. Verified SPID inspection and matching SKSE DLL/config inventory. Other distributor formats and SKSE finding modes need individual runtime coverage. |
| create_plugin, apply | Isolated direct upstream calls wrote a new header-only plugin and a separate single-record override and read back the override. |
| bsa_list | The product MCP companion and its independent native archive index both counted 136 entries in the installed MarketplaceTextures.bsa. The native index also read all 23 game Data BSAs (172,961 entries). |
| check | Product MCP checked the selected Skyrim.esm gold record through houseCARL on the real profile. Its result reported zero dangling references for that record, 3,628 successfully scanned plugins and one explicit parse exclusion. Script and FaceGen check families are available with separate selection. |
| create, remove, forward, copy, write_seq, compact_plugin, merge_plugins | Upstream registration observed. Integrated workflow and mutation verification pending. |
| place, nif_set, compile_script, decompile_script, bsa_extract, bsa_repack | Upstream registration observed. Integrated editing and external-tool verification pending. |
| nexus_search, nexus_mod, nexus_graphql, nexus_check_updates, nexus_identify | Upstream registration observed. Real-provider matching and authorization require separate acceptance. |
| set_mo2_instance, set_tool_path, update_status | Upstream registration observed. Modhunter selects explicit roots at process launch. Direct upstream calls unverified. |

### Protected new installations

The stock public MO2 plugin API describes the second argument of `installMod` as a changeable name suggestion. Its core asks how to handle a collision and can merge or replace the selected mod before returning to the caller. Modhunter checks that a reviewed new name is absent, rechecks immediately before installation and inspects the returned target. These checks cannot prevent a subsequent native installer name change, merge or replace. The target's state after the call also cannot establish that it was disabled throughout installation. Production QA that requires this guarantee must wait for an enforceable native destination and enable-state guard. The existing isolated install/update fixture only covers its explicitly observed native dialog sequence.

[MO2 plugin interface](https://github.com/ModOrganizer2/modorganizer-uibase/blob/edc09d390348244c2e00d189058140bae5d92934/include/uibase/imoinfo.h#L248-L257) · [MO2 collision handling](https://github.com/ModOrganizer2/modorganizer/blob/4ba22f0d8b6f9e08e44008b8395cd774551b4ebe/src/installationmanager.cpp#L376-L455) · [MO2 destination and extraction](https://github.com/ModOrganizer2/modorganizer/blob/4ba22f0d8b6f9e08e44008b8395cd774551b4ebe/src/installationmanager.cpp#L481-L503)


To use protected acceptance mode, set the process environment variable `MODHUNTER_PROTECTED_INSTALLS=1` before starting both Modhunter and MO2 with the updated plugin. The native desktop adapter rejects an install request before contacting MO2. The plugin independently refuses installations requested through its shared queue and checks again at the Qt installer entry points. Other MO2 plugins and direct user actions are outside this control. Native API clients can require this contract per `content-install` request with `"requireProtectedInstall":true`; its rejection code is `protected-install-unavailable` and `nativeStarted` is false. The capability response at `/api/capabilities` reports support and whether the host requires this mode. The refusal preserves interactive installs for ordinary sessions.

A custom installer can ask the stock installation manager to extract temporary files during an installation. Its plugin API still offers no transaction that both fixes the final destination and vetoes activation across the installed lifecycle. MO2 selects its configured shared mods directory, refreshes it and notifies third-party plugins, which may activate a newly installed mod. A final-state audit alone cannot prove its prior state. [Native extraction API](https://github.com/ModOrganizer2/modorganizer-uibase/blob/edc09d390348244c2e00d189058140bae5d92934/include/uibase/iinstallationmanager.h#L48-L95) · [Installation callbacks and active directory](https://github.com/ModOrganizer2/modorganizer/blob/4ba22f0d8b6f9e08e44008b8395cd774551b4ebe/src/organizercore.cpp#L795-L827)

The supported plugin installer stages and swaps the Modhunter plugin folder while MO2 is closed. The desktop NSIS installer targets the current user's application. These package operations do not require a write into a managed mod. Starting MO2 is a different operation. Its profile constructor refreshes mod status, flushes pending changes and schedules a rewrite when the mod list and disk differ. A production startup or restart therefore needs the profile baseline and comparison even if Modhunter itself makes a read-only request. [MO2 profile implementation](https://github.com/ModOrganizer2/modorganizer/blob/4ba22f0d8b6f9e08e44008b8395cd774551b4ebe/src/profile.cpp#L392-L580)

### Feed subscriptions

Watchtower's **Import watchlist** accepts OPML subscription exports from feed readers. Preview a pasted list or select a JSON, OPML or XML file. The preview preserves nested folder labels and category tags and reports invalid feed entries before any subscriptions are saved. Lists containing linked outlines need inline feed URLs. Existing watches retain their settings. Opening the import preview performs no network checks.
