//! Subprocess MCP with launcher-selected file, profile, and state-directory scopes.
mod housecarl;
mod watchtower;
use modhunter_core::{BridgeClient, validate_bridge_endpoint};
use serde::Deserialize;
use serde_json::{Value, json};
use std::{
    io::{self, BufRead, Write},
    path::Path,
    sync::{Mutex, mpsc},
};

const MODERN: &str = "2026-07-28";
const LEGACY: &str = "2025-11-25";
const VERSION_KEY: &str = "io.modelcontextprotocol/protocolVersion";
const CAPS_KEY: &str = "io.modelcontextprotocol/clientCapabilities";
const SERVER_KEY: &str = "io.modelcontextprotocol/serverInfo";
const MAX_FRAME: usize = 6 * 512 * 1024 + 4096;
const MAX_REPLY: usize = 4 * 1024 * 1024;
const TOOL: &str = "spid_preview";
const BSA_TOOL: &str = "bsa_directory_page";
const BSA_PAGE_SIZE: usize = 64;
const PLUGIN_TOOL: &str = "plugin_header";
const PLUGIN_MASTER_LIMIT: usize = 256;
const PLUGIN_MASTER_NAME_LIMIT: usize = 255;
const ENABLE_TOOL: &str = "mo2_set_enabled";
const EVIDENCE_TOOL: &str = "mo2_mod_evidence";
const LIVE_TOOL: &str = "mo2_mods";
pub(super) const USAGE: &str = "mcp-stdio [--legacy-2025-11-25] [--bsa ALIAS PATH] [--plugin ALIAS PATH] [--mo2 PROFILE_ID LOOPBACK_URL] [--allow-mod PROFILE_ID INTERNAL_NAME] [--housecarl EXE DIRECTORIES_JSON] [--watchtower read|manage CONFIG_DIRECTORY]";

#[derive(Clone, Copy, PartialEq, Eq)]
enum Mode {
    Modern,
    Legacy,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum LegacyPhase {
    New,
    Negotiated,
    Ready,
}

struct ArchiveSnapshot {
    alias: String,
    directory: super::bsa_directory::Directory,
}

// This is an allowlist projection, not a serializable file-inspection report.
struct PluginSnapshot {
    alias: String,
    masters: Vec<String>,
    flags: u32,
    version: f32,
    form_version: u16,
    master_flag: bool,
    light_flag: bool,
    declared_count: u32,
}

impl PluginSnapshot {
    fn capture(alias: &str, path: &Path) -> Result<Self, &'static str> {
        let info = modhunter_core::plugin_analysis::inspect_selected_skyrim_plugin(path, true)
            .map_err(|_| "Could not capture the selected Skyrim-format plugin header")?;
        let header = info.header;
        if header.masters.len() > PLUGIN_MASTER_LIMIT
            || header.masters.iter().any(|name| {
                name.len() > PLUGIN_MASTER_NAME_LIMIT
                    || !name.bytes().all(|byte| (0x20..=0x7e).contains(&byte))
            })
        {
            return Err(
                "Plugin master declarations exceed the snapshot limits or need unsupported name encoding",
            );
        }
        Ok(Self {
            alias: alias.into(),
            masters: header.masters,
            flags: header.flags,
            version: header.version,
            form_version: header.record_header_version,
            master_flag: header.master_flag,
            light_flag: header.light_flag,
            declared_count: header.declared_record_and_group_count,
        })
    }
}

struct LiveProfile {
    permitted_names: Vec<String>,
    profile_id: String,
    endpoint: String,
    client: BridgeClient,
}

struct Selections {
    housecarl: Option<housecarl::Companion>,
    watchtower: Option<watchtower::Scope>,
    live: Option<LiveProfile>,
    archive: Option<ArchiveSnapshot>,
    plugin: Option<PluginSnapshot>,
}

fn alias_name(alias: &str) -> bool {
    (1..=32).contains(&alias.len())
        && alias
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'-'))
}

fn configuration(args: &[String]) -> Result<(Mode, Selections), &'static str> {
    let (mode, rest) = match args.split_first() {
        Some((flag, rest)) if flag == "--legacy-2025-11-25" => (Mode::Legacy, rest),
        _ => (Mode::Modern, args),
    };
    let (triples, remainder) = rest.as_chunks::<3>();
    if !remainder.is_empty() {
        return Err(USAGE);
    }
    let (mut archive, mut plugin) = (None, None);
    let mut live = None;
    let mut permissions = Vec::new();
    let mut companion = None;
    let mut watch_scope = None;
    for [flag, alias, path] in triples {
        if flag == "--allow-mod" {
            if alias.is_empty() || path.is_empty() || path.chars().any(char::is_control) {
                return Err(USAGE);
            }
            permissions.push((alias.as_str(), path.clone()));
            continue;
        }
        if flag == "--watchtower" {
            if watch_scope.replace((alias, path)).is_some() {
                return Err(USAGE);
            }
            continue;
        }
        if flag == "--housecarl" {
            if companion.replace((alias, path)).is_some() {
                return Err(USAGE);
            }
            continue;
        }
        if flag == "--mo2" {
            if live.is_some()
                || alias.is_empty()
                || alias.len() > 256
                || alias.chars().any(char::is_control)
            {
                return Err(USAGE);
            }
            live = Some(LiveProfile {
                permitted_names: Vec::new(),
                profile_id: alias.clone(),
                endpoint: validate_bridge_endpoint(path)
                    .map_err(|_| "Invalid MO2 loopback endpoint")?,
                client: BridgeClient::new().map_err(|_| "Cannot initialize the MO2 connection")?,
            });
            continue;
        }
        let slot = match flag.as_str() {
            "--bsa" => &mut archive,
            "--plugin" => &mut plugin,
            _ => return Err(USAGE),
        };
        if !alias_name(alias) || slot.replace((alias, path)).is_some() {
            return Err(USAGE);
        }
    }
    for (profile, name) in permissions {
        let selected = live
            .as_mut()
            .filter(|selected| selected.profile_id == profile)
            .ok_or("Mod permissions require a matching --mo2 profile ID")?;
        if !selected.permitted_names.contains(&name) {
            selected.permitted_names.push(name);
        }
    }
    if let (Some((archive, _)), Some((plugin, _))) = (archive, plugin)
        && archive.eq_ignore_ascii_case(plugin)
    {
        return Err("Each launch-time file selection must have a distinct alias");
    }
    // Finish validation of the whole launcher argument shape before any reads.
    let archive = archive
        .map(|(alias, path)| {
            super::bsa_directory::snapshot(Path::new(path)).map(|directory| ArchiveSnapshot {
                alias: alias.clone(),
                directory,
            })
        })
        .transpose()?;
    let plugin = plugin
        .map(|(alias, path)| PluginSnapshot::capture(alias, Path::new(path)))
        .transpose()?;
    Ok((
        mode,
        Selections {
            archive,
            plugin,
            live,
            watchtower: watch_scope
                .map(|(mode, path)| watchtower::Scope::configure(mode, Path::new(path)))
                .transpose()?,
            housecarl: companion
                .map(|(exe, config)| {
                    housecarl::Companion::configure(Path::new(exe), Path::new(config))
                })
                .transpose()?,
        },
    ))
}

pub(super) fn run(args: &[String]) -> Result<(), Box<dyn std::error::Error>> {
    let (mode, selections) = configuration(args)?;
    // Archive and plugin selections are immutable snapshots. Live scopes reopen their selected state.
    serve(io::stdin().lock(), io::stdout(), mode, &selections)?;
    Ok(())
}

// Avoid read_until's unlimited growth on an untrusted length or missing newline.
enum Frame {
    Ready,
    Eof,
    TooLarge,
    Incomplete,
}

fn read_frame(input: &mut impl BufRead, frame: &mut Vec<u8>) -> io::Result<Frame> {
    frame.clear();
    loop {
        let data = input.fill_buf()?;
        if data.is_empty() {
            return Ok(if frame.is_empty() {
                Frame::Eof
            } else {
                Frame::Incomplete
            });
        }
        let end = data.iter().position(|byte| *byte == b'\n');
        let count = end.unwrap_or(data.len());
        if frame.len() + count > MAX_FRAME {
            return Ok(Frame::TooLarge);
        }
        frame.extend_from_slice(&data[..count]);
        input.consume(count + usize::from(end.is_some()));
        if end.is_some() {
            return Ok(Frame::Ready);
        }
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct DeclarationRequest {
    #[serde(rename = "sourceName")]
    source_name: String,
    text: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ArchivePageRequest {
    #[serde(rename = "archiveAlias")]
    archive_alias: String,
    #[serde(default)]
    offset: usize,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PluginRequest {
    #[serde(rename = "pluginAlias")]
    plugin_alias: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct LivePage {
    #[serde(default)]
    offset: usize,
    revision: Option<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct EvidencePage {
    item_id: String,
    revision: String,
    #[serde(default)]
    offset: usize,
    evidence_revision: Option<String>,
}

#[derive(Deserialize, serde::Serialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct SetEnabled {
    item_id: String,
    name: String,
    revision: String,
    expected_enabled: bool,
    enabled: bool,
}

enum ToolRequest {
    SetEnabled(SetEnabled),
    Skyrim(housecarl::Query),
    Watchtower(watchtower::Query),
    Live(LivePage),
    Evidence(EvidencePage),
    Spid(DeclarationRequest),
    Bsa(ArchivePageRequest),
    Plugin(PluginRequest),
}

struct Work {
    id: Value,
    request: ToolRequest,
}

struct InFlight {
    id: Value,
    cancelled: bool,
    mutation_dispatched: bool,
}

struct Wire<W> {
    writer: W,
    inflight: Option<InFlight>,
}

impl<W: Write> Wire<W> {
    fn cancel(&mut self, id: &Value) {
        if let Some(call) = self
            .inflight
            .as_mut()
            .filter(|call| call.id == *id && !call.mutation_dispatched)
        {
            call.cancelled = true;
        }
    }

    fn finish(&mut self, id: Value, result: Value) -> io::Result<()> {
        if self
            .inflight
            .take()
            .is_some_and(|call| call.id == id && !call.cancelled)
        {
            self.reply(ok(id, result))?;
        }
        Ok(())
    }

    fn reply(&mut self, reply: Value) -> io::Result<()> {
        let mut data = serde_json::to_vec(&reply)?;
        if data.len() > MAX_REPLY {
            data = serde_json::to_vec(&error(
                reply["id"].clone(),
                -32603,
                "MCP response exceeds its byte limit",
            ))?;
        }
        self.writer.write_all(&data)?;
        self.writer.write_all(b"\n")?;
        self.writer.flush()
    }
}

fn server_info(selections: &Selections) -> Value {
    let name = if selections
        .watchtower
        .as_ref()
        .is_some_and(|scope| scope.manage)
        || selections
            .live
            .as_ref()
            .is_some_and(|live| !live.permitted_names.is_empty())
    {
        "modhunter-scoped-control"
    } else {
        "modhunter-read-only"
    };
    json!({"name":name,"version":env!("CARGO_PKG_VERSION")})
}

fn ok(id: Value, result: Value) -> Value {
    json!({"jsonrpc":"2.0","id":id,"result":result})
}

fn error(id: Value, code: i64, message: &'static str) -> Value {
    json!({"jsonrpc":"2.0","id":id,"error":{"code":code,"message":message}})
}

fn valid_id(id: &Value) -> bool {
    id.as_i64().is_some() || id.as_u64().is_some() || id.as_str().is_some_and(|id| id.len() <= 128)
}

fn unsupported(id: Value, requested: &str) -> Value {
    let requested =
        if requested.len() <= 32 && requested.bytes().all(|c| c.is_ascii_digit() || c == b'-') {
            requested
        } else {
            ""
        };
    json!({"jsonrpc":"2.0","id":id,"error":{"code":-32022,"message":"Unsupported protocol version",
        "data":{"supported":[MODERN],"requested":requested}}})
}

fn complete(mode: Mode, selections: &Selections, mut result: Value) -> Value {
    if mode == Mode::Modern {
        result["resultType"] = json!("complete");
        result["_meta"] = json!({SERVER_KEY: server_info(selections)});
    }
    result
}

fn list(mode: Mode, selections: &Selections) -> Value {
    let mut result = json!({"tools":[{
        "name":TOOL,
        "title":"Preview supplied Skyrim SPID declarations",
        "description":"Review ordinary declarations and declared targets in supplied UTF-8 text only. No files are opened, directives applied, or in-game matches verified. Filters and chance slots are not evaluated.",
        "inputSchema":{"type":"object","additionalProperties":false,"required":["sourceName","text"],
          "properties":{"sourceName":{"type":"string","minLength":1,"maxLength":160},
                        "text":{"type":"string","maxLength":524288}}},
        "annotations":{"readOnlyHint":true,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}
    }]});
    if let Some(scope) = &selections.watchtower {
        result["tools"]
            .as_array_mut()
            .expect("tools array")
            .extend(scope.tools());
    }
    if let Some(archive) = &selections.archive {
        result["tools"].as_array_mut().expect("tools array").push(json!({
            "name":BSA_TOOL,
            "title":"List the launcher-selected BSA directory snapshot",
            "description":"Return one page of relative archive names captured at startup. No paths can be supplied, and files are not reopened. Names do not establish content equivalence, conflicts, or in-game winners.",
            "inputSchema":{"type":"object","additionalProperties":false,"required":["archiveAlias"],
              "properties":{"archiveAlias":{"type":"string","enum":[archive.alias]},
                            "offset":{"type":"integer","minimum":0,"maximum":archive.directory.paths.len()} }},
            "annotations":{"readOnlyHint":true,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}
        }));
    }
    if let Some(plugin) = &selections.plugin {
        result["tools"].as_array_mut().expect("tools array").push(json!({
            "name":PLUGIN_TOOL,
            "title":"Read a launcher-selected Skyrim-format plugin header snapshot",
            "description":"Return header flags and ordered declared master names from the single launch-time selection. No files are opened during a call. Does not resolve masters, read records, or establish game compatibility.",
            "inputSchema":{"type":"object","additionalProperties":false,"required":["pluginAlias"],
              "properties":{"pluginAlias":{"type":"string","enum":[plugin.alias]}}},
            "annotations":{"readOnlyHint":true,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}
        }));
    }
    if selections.housecarl.is_some() {
        result["tools"]
            .as_array_mut()
            .expect("tools array")
            .extend(housecarl::tools());
    }
    if let Some(live) = selections
        .live
        .as_ref()
        .filter(|live| !live.permitted_names.is_empty())
    {
        result["tools"].as_array_mut().expect("tools array").push(json!({
            "name":ENABLE_TOOL, "title":"Set an explicitly permitted MO2 mod's enabled state",
            "description":"Use an exact item from a freshly read live inventory. Both the launcher and MO2 settings must permit the exact name. Inspect the inventory again after a change or uncertain response.",
            "inputSchema":{"type":"object","required":["itemId","name","revision","expectedEnabled","enabled"],"additionalProperties":false,
                "properties":{"itemId":{"type":"string"},"name":{"enum":live.permitted_names,"type":"string"},"revision":{"type":"string"},
                     "expectedEnabled":{"type":"boolean"},"enabled":{"type":"boolean"}}},
            "annotations":{"readOnlyHint":false,"destructiveHint":true,"idempotentHint":false,"openWorldHint":false}
        }));
    }
    if selections.live.is_some() {
        result["tools"].as_array_mut().expect("tools array").push(json!({
            "name":EVIDENCE_TOOL,
            "title":"Inspect one installed mod's update decision and health evidence",
            "description":"Use a row ID and revision from mo2_mods. Returns the installed file, matching provider file, project version, freshness, health actions and published advisory applicability already observed. Follow nextOffset with evidenceRevision for subsequent pages.",
            "inputSchema":{"type":"object","additionalProperties":false,"required":["itemId","revision"],"properties":{
                "itemId":{"type":"string","minLength":1,"maxLength":256},
                "revision":{"type":"string","minLength":1,"maxLength":64},
                "offset":{"type":"integer","minimum":0},
                "evidenceRevision":{"type":"string","maxLength":64}
            }},
            "annotations":{"readOnlyHint":true,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}
        }));
        result["tools"].as_array_mut().expect("tools array").push(json!({
            "name":LIVE_TOOL,
            "title":"Read the running MO2 profile's mods",
            "description":"Page through the live inventory selected at launch. Start with empty arguments, then pass nextOffset as offset and the same revision until nextOffset is null. Restart traversal if the inventory changes.",
            "inputSchema":{"type":"object","additionalProperties":false,"properties":{
                "offset":{"type":"integer","minimum":0},
                "revision":{"type":"string","maxLength":64}
            }},
            "annotations":{"readOnlyHint":true,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}
        }));
    }
    if mode == Mode::Modern {
        result["ttlMs"] = json!(0);
        result["cacheScope"] = json!("private");
    }
    complete(mode, selections, result)
}

fn archive_page(selection: &ArchiveSnapshot, request: ArchivePageRequest) -> Result<Value, String> {
    let paths = &selection.directory.paths;
    if request.archive_alias != selection.alias {
        return Err("The requested archive alias is not available".into());
    }
    if request.offset > paths.len() {
        return Err("The requested BSA page offset is outside the snapshot".into());
    }
    let next = (request.offset + BSA_PAGE_SIZE).min(paths.len());
    Ok(json!({
        "scope":"launch-time-named-bsa-directory-page",
        "archiveAlias": selection.alias,
        "version": selection.directory.version,
        "metadataBytesRead": selection.directory.metadata_bytes,
        "entryCount": paths.len(),
        "offset":request.offset,
        "nextOffset": (next < paths.len()).then_some(next),
        "paths": &paths[request.offset..next],
    }))
}

fn plugin_header(selection: &PluginSnapshot, request: PluginRequest) -> Result<Value, String> {
    if request.plugin_alias != selection.alias {
        return Err("The requested plugin alias is not available".into());
    }
    Ok(json!({
        "scope":"launch-time-skyrim-se-format-header-assumed",
        "pluginAlias":selection.alias,
        "headerFlags":selection.flags,
        "headerVersion":selection.version,
        "formVersion":selection.form_version,
        "masterFlag":selection.master_flag,
        "lightFlag":selection.light_flag,
        "declaredRecordAndGroupCount":selection.declared_count,
        "declaredMasters":selection.masters,
        "masterResolution":"declarations_only_not_resolved",
        "payloadDecoding":"not_evaluated",
    }))
}

fn execute(mode: Mode, request: ToolRequest, selections: &Selections) -> Value {
    let generated = match request {
        ToolRequest::Watchtower(query) => selections.watchtower.as_ref().expect("registered Watchtower").execute(query),
        ToolRequest::Skyrim(query) => selections
            .housecarl
            .as_ref()
            .expect("registered houseCARL")
            .read(query),
        ToolRequest::SetEnabled(request) => selections
            .live
            .as_ref()
            .filter(|live| live.permitted_names.contains(&request.name))
            .ok_or_else(|| "The launcher did not permit this mod".to_owned())
            .and_then(|live| {
                let mut body = serde_json::to_value(request).map_err(|e| e.to_string())?;
                body["profileId"] = json!(live.profile_id);
                live.client
                    .native_content(&live.endpoint, "agent-set-enabled", &body)
                    .map_err(|e| e.to_string())
            }),
        ToolRequest::Evidence(page) => selections
            .live
            .as_ref()
            .ok_or_else(|| "No live MO2 profile was selected".to_owned())
            .and_then(|live| {
                if page.item_id.is_empty() || page.item_id.len() > 256 || page.revision.is_empty() || page.revision.len() > 64 {
                    return Err("Invalid row identity or inventory revision".to_owned());
                }
                let mut query = json!({"profileId":live.profile_id, "itemId":page.item_id, "revision":page.revision, "offset":page.offset.to_string()});
                if let Some(revision) = page.evidence_revision {
                    if revision.len() != 64 {
                        return Err("Invalid evidence revision".to_owned());
                    }
                    query["evidenceRevision"] = json!(revision);
                }
                live.client.native_content(&live.endpoint, "agent-evidence", &query).map_err(|e| e.to_string())
            }),
        ToolRequest::Live(page) => selections
            .live
            .as_ref()
            .ok_or_else(|| "No live MO2 profile was selected".to_owned())
            .and_then(|live| {
                let mut query =
                    json!({"profileId": live.profile_id, "offset":page.offset.to_string()});
                if let Some(revision) = page.revision {
                    if revision.len() > 64 {
                        return Err("Invalid inventory revision".to_owned());
                    }
                    query["revision"] = json!(revision);
                }
                live.client
                    .native_content(&live.endpoint, "agent-mods", &query)
                    .map_err(|e| e.to_string())
            }),
        ToolRequest::Spid(request) => super::spid_preview::run(
            &request.source_name,
            request.text.as_bytes(),
        )
        .and_then(|report| {
            serde_json::to_value(report).map_err(|_| "Cannot encode SPID preview".to_owned())
        }),
        ToolRequest::Bsa(request) => selections
            .archive
            .as_ref()
            .ok_or_else(|| "No BSA was selected by the launcher".to_owned())
            .and_then(|selection| archive_page(selection, request)),
        ToolRequest::Plugin(request) => selections
            .plugin
            .as_ref()
            .ok_or_else(|| "No plugin was selected by the launcher".to_owned())
            .and_then(|selection| plugin_header(selection, request)),
    };
    match generated {
        Ok(report) => complete(
            mode,
            selections,
            json!({"content":[{"type":"text","text":report.to_string()}],
                                "structuredContent":report,"isError":false}),
        ),
        Err(reason) => complete(
            mode,
            selections,
            json!({"content":[{"type":"text","text":reason}],"isError":true}),
        ),
    }
}

fn handle<W: Write>(
    mut message: Value,
    mode: Mode,
    phase: &mut LegacyPhase,
    wire: &Mutex<Wire<W>>,
    worker: &mpsc::SyncSender<Work>,
    selections: &Selections,
) -> io::Result<()> {
    let id = message.get("id").cloned();
    if message["jsonrpc"] != "2.0"
        || message["method"].as_str().is_none()
        || id.as_ref().is_some_and(|id| !valid_id(id))
    {
        return wire
            .lock()
            .unwrap()
            .reply(error(Value::Null, -32600, "Invalid JSON-RPC request"));
    }
    let method = message["method"].as_str().unwrap().to_owned();
    let mut params = message
        .get_mut("params")
        .map(std::mem::take)
        .unwrap_or_else(|| json!({}));
    let Some(id) = id else {
        if method == "notifications/cancelled" && valid_id(&params["requestId"]) {
            wire.lock().unwrap().cancel(&params["requestId"]);
        } else if mode == Mode::Legacy
            && method == "notifications/initialized"
            && *phase == LegacyPhase::Negotiated
        {
            *phase = LegacyPhase::Ready;
        }
        return Ok(());
    };
    if wire
        .lock()
        .unwrap()
        .inflight
        .as_ref()
        .is_some_and(|active| active.id == id)
    {
        // A client must not reuse a request ID while its first call is active.
        return Ok(());
    }
    let reply = |value| wire.lock().unwrap().reply(value);
    if !params.is_object() {
        return reply(error(id, -32602, "Expected request parameters object"));
    }
    if mode == Mode::Modern {
        if method == "initialize" {
            return reply(unsupported(
                id,
                params["protocolVersion"].as_str().unwrap_or(""),
            ));
        }
        let meta = &params["_meta"];
        let Some(version) = meta[VERSION_KEY].as_str() else {
            return reply(error(id, -32602, "Missing MCP protocol version metadata"));
        };
        if version != MODERN {
            return reply(unsupported(id, version));
        }
        if !meta[CAPS_KEY].is_object() {
            return reply(error(
                id,
                -32602,
                "Missing MCP client capabilities metadata",
            ));
        }
    } else {
        if method == "initialize" {
            if *phase != LegacyPhase::New
                || !params["protocolVersion"].is_string()
                || !params["capabilities"].is_object()
                || !params["clientInfo"]["name"].is_string()
                || !params["clientInfo"]["version"].is_string()
            {
                return reply(error(id, -32602, "Invalid MCP initialization"));
            }
            *phase = LegacyPhase::Negotiated;
            return reply(ok(
                id,
                json!({"protocolVersion":LEGACY,"capabilities":{"tools":{}},
                                      "serverInfo":server_info(selections)}),
            ));
        }
        if method == "server/discover" {
            return reply(error(id, -32601, "Method not found in legacy mode"));
        }
        if *phase != LegacyPhase::Ready && method != "ping" {
            return reply(error(id, -32002, "Complete MCP initialization first"));
        }
    }
    match method.as_str() {
        "server/discover" => reply(ok(
            id,
            complete(
                mode,
                selections,
                json!({
                    "supportedVersions":[MODERN],"capabilities":{"tools":{}},"ttlMs":0,"cacheScope":"private"
                }),
            ),
        )),
        "ping" => reply(ok(id, complete(mode, selections, json!({})))),
        "tools/list" if params.get("cursor").is_none() => reply(ok(id, list(mode, selections))),
        "tools/list" => reply(error(id, -32602, "No next tool catalog page")),
        "tools/call" => {
            let arguments = params
                .get_mut("arguments")
                .map(std::mem::take)
                .unwrap_or_default();
            let request = match params["name"].as_str() {
                Some(TOOL) => serde_json::from_value(arguments).map(ToolRequest::Spid),
                Some(BSA_TOOL) if selections.archive.is_some() => {
                    serde_json::from_value(arguments).map(ToolRequest::Bsa)
                }
                Some(housecarl::TOOL) if selections.housecarl.is_some() => {
                    serde_json::from_value(arguments)
                        .map(housecarl::Query::Record)
                        .map(ToolRequest::Skyrim)
                }
                Some(housecarl::INSPECT) if selections.housecarl.is_some() => {
                    serde_json::from_value(arguments)
                        .map(housecarl::Query::Inspect)
                        .map(ToolRequest::Skyrim)
                }
                Some(housecarl::STATUS) if selections.housecarl.is_some() => {
                    serde_json::from_value(arguments)
                        .map(housecarl::Query::Status)
                        .map(ToolRequest::Skyrim)
                }
                Some(ENABLE_TOOL)
                    if selections
                        .live
                        .as_ref()
                        .is_some_and(|live| !live.permitted_names.is_empty()) =>
                {
                    serde_json::from_value(arguments).map(ToolRequest::SetEnabled)
                }
                Some(watchtower::SEARCH) if selections.watchtower.is_some() => {
                    serde_json::from_value(arguments)
                        .map(watchtower::Query::Search)
                        .map(ToolRequest::Watchtower)
                }
                Some(watchtower::ADD)
                    if selections.watchtower.as_ref().is_some_and(|s| s.manage) =>
                {
                    serde_json::from_value(arguments)
                        .map(watchtower::Query::Add)
                        .map(ToolRequest::Watchtower)
                }
                Some(watchtower::REFRESH)
                    if selections.watchtower.as_ref().is_some_and(|s| s.manage) =>
                {
                    serde_json::from_value(arguments)
                        .map(watchtower::Query::Refresh)
                        .map(ToolRequest::Watchtower)
                }
                Some(EVIDENCE_TOOL) if selections.live.is_some() => {
                    serde_json::from_value(arguments).map(ToolRequest::Evidence)
                }
                Some(LIVE_TOOL) if selections.live.is_some() => {
                    serde_json::from_value(arguments).map(ToolRequest::Live)
                }
                Some(PLUGIN_TOOL) if selections.plugin.is_some() => {
                    serde_json::from_value(arguments).map(ToolRequest::Plugin)
                }
                _ => return reply(error(id, -32602, "Unknown or unselected MCP tool")),
            };
            let Ok(request) = request else {
                return reply(error(
                    id,
                    -32602,
                    "Arguments do not match the advertised tool schema",
                ));
            };
            let mut live = wire.lock().unwrap();
            if let Some(active) = &live.inflight {
                if active.id == id {
                    // Never emit another response carrying an active ID.
                    return Ok(());
                }
                return live.reply(error(id, -32000, "Another local preview is still running"));
            }
            live.inflight = Some(InFlight {
                id: id.clone(),
                cancelled: false,
                mutation_dispatched: false,
            });
            if worker
                .try_send(Work {
                    id: id.clone(),
                    request,
                })
                .is_err()
            {
                live.inflight = None;
                return live.reply(error(id, -32603, "Local preview worker is unavailable"));
            }
            Ok(())
        }
        _ => reply(error(id, -32601, "Method not found")),
    }
}

fn serve(
    mut input: impl BufRead,
    writer: impl Write + Send,
    mode: Mode,
    selections: &Selections,
) -> io::Result<()> {
    let mut phase = LegacyPhase::New;
    let mut frame = Vec::new();
    let wire = Mutex::new(Wire {
        writer,
        inflight: None,
    });
    let (tx, rx) = mpsc::sync_channel::<Work>(1);
    std::thread::scope(|scope| {
        let wire = &wire;
        let engine = scope.spawn(move || -> io::Result<()> {
            while let Ok(Work { id, request }) = rx.recv() {
                // CPU work is bounded by the actual parser. If a cancellation
                // arrives during it, discard its output rather than replying.
                if matches!(request, ToolRequest::SetEnabled(_))
                    || matches!(&request, ToolRequest::Watchtower(q) if q.writes())
                {
                    let mut locked = wire.lock().unwrap();
                    if let Some(active) = locked.inflight.as_mut() {
                        if active.cancelled {
                            locked.inflight = None;
                            continue;
                        }
                        active.mutation_dispatched = true;
                    }
                }
                let result = execute(mode, request, selections);
                wire.lock().unwrap().finish(id, result)?;
            }
            Ok(())
        });
        let receiving = (|| -> io::Result<()> {
            loop {
                match read_frame(&mut input, &mut frame)? {
                    Frame::Ready => match serde_json::from_slice(&frame) {
                        Ok(message) => handle(message, mode, &mut phase, wire, &tx, selections)?,
                        Err(_) => wire.lock().unwrap().reply(error(
                            Value::Null,
                            -32700,
                            "Malformed JSON frame",
                        ))?,
                    },
                    Frame::Eof => return Ok(()),
                    Frame::Incomplete => {
                        return wire.lock().unwrap().reply(error(
                            Value::Null,
                            -32700,
                            "Missing JSON frame delimiter",
                        ));
                    }
                    Frame::TooLarge => {
                        return wire.lock().unwrap().reply(error(
                            Value::Null,
                            -32700,
                            "MCP frame exceeds its byte limit",
                        ));
                    }
                }
            }
        })();
        drop(tx);
        engine
            .join()
            .map_err(|_| io::Error::other("MCP worker stopped unexpectedly"))??;
        receiving
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn recognized_inflight_cancellation_suppresses_only_the_exact_response() {
        let mut wire = Wire {
            writer: Vec::new(),
            inflight: Some(InFlight {
                id: json!("call-1"),
                cancelled: false,
                mutation_dispatched: false,
            }),
        };
        wire.cancel(&json!("not-call-1"));
        wire.finish(json!("call-1"), json!({"fixture":1})).unwrap();
        assert!(!wire.writer.is_empty());
        wire.writer.clear();
        wire.inflight = Some(InFlight {
            id: json!("call-2"),
            cancelled: false,
            mutation_dispatched: false,
        });
        wire.cancel(&json!("call-2"));
        wire.finish(json!("call-2"), json!({"fixture":2})).unwrap();
        assert!(wire.writer.is_empty());
        assert!(wire.inflight.is_none());
    }
}
