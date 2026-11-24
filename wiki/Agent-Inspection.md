# Agent inspection

The Modhunter CLI exposes Skyrim inspection over MCP stdio. Launch it with `mcp-stdio --legacy-2025-11-25` for clients using initialization and the 2025-11-25 protocol, including compatible Codex and Claude Code clients. For example, a client configuration can set its command to the absolute path to `modhunter-cli.exe` and its arguments to `["mcp-stdio", "--legacy-2025-11-25"]`. A client using the 2026-07-28 protocol can omit the legacy flag.

### Live MO2 inventory

Append `--mo2 PROFILE_ID http://127.0.0.1:PORT/` to the command when the Modhunter plugin is installed. The plugin publishes its profile ID and endpoint in a descriptor under `%LOCALAPPDATA%\Modhunter\mo2-sessions`. Choose the descriptor for the intended running instance and profile. The CLI obtains its native session on the loopback connection.

Call `mo2_mods` with empty arguments. Each page includes the profile, revision, total and rows with their Mod Organizer names, state, priority, version and available Nexus ID. Pass the returned `revision` and `nextOffset` (as `offset`) for the next page. A null next offset marks completion. A profile switch, revision change or plugin restart invalidates the cursor. Start a new traversal to obtain a consistent inventory. The plugin builds each page directly from the live inventory. Raw notes and session tokens stay out of the MCP output.

The local tools also accept `--bsa ALIAS PATH` and `--plugin ALIAS PATH` to expose selected launch-time BSA directory and plugin header snapshots. `spid_preview` parses text supplied by the caller. These inspection tools operate independently of a running MO2 connection.

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

Modhunter starts houseCARL on the first read and reuses it across reads in the same MCP process. The configured roots stay fixed for the session and houseCARL uses a private temporary configuration directory. Keep installation paths, directory selections and local results private.

### Reference implementations

The author Antiarc123 distributes MCP–MO2 version 1 and its source on [Nexus Mods](https://www.nexusmods.com/site/mods/2549). The suggested GitHub repository returned 404 during verification on November 24, 2026. The Nexus description documents a separate MCP server on port 47525, with optional writes and launches. Its published workflows include mod and plugin state, conflicts, virtual files, logs, downloads, installers and executable control. The archive tool list and runtime parity remain unverified.

The [houseCARL 2.2.0 release](https://github.com/Avick3110/houseCARL/releases/tag/v2.2.0) advertised 31 tools during an MCP handshake on November 24, 2026. Its release archive SHA-256 is `677c0c2eadedc4c55135fcb5cf1afe5a6efe5a5f2e27b2c65d0a42e43e4dab20`. Independent read-only calls to `housecarl_load_order_status` and `housecarl_records` read an actual Skyrim SE profile. These observations verify those upstream paths. Modhunter's own live catalog, scoped asset previews, and access to a separately launched upstream server require distinct acceptance checks.
