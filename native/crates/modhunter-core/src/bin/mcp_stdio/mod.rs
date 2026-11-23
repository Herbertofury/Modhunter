//! Read-only subprocess MCP. Files are selectable only before stdio starts.
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
const USAGE: &str = "mcp-stdio [--legacy-2025-11-25] [--bsa ALIAS PATH]";

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

fn alias_name(alias: &str) -> bool {
    (1..=32).contains(&alias.len())
        && alias
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'-'))
}

fn configuration(args: &[String]) -> Result<(Mode, Option<ArchiveSnapshot>), &'static str> {
    let (mode, rest) = match args.split_first() {
        Some((flag, rest)) if flag == "--legacy-2025-11-25" => (Mode::Legacy, rest),
        _ => (Mode::Modern, args),
    };
    match rest {
        [] => Ok((mode, None)),
        [flag, alias, path] if flag == "--bsa" && alias_name(alias) => {
            let directory = super::bsa_directory::snapshot(Path::new(path))?;
            Ok((
                mode,
                Some(ArchiveSnapshot {
                    alias: alias.clone(),
                    directory,
                }),
            ))
        }
        _ => Err(USAGE),
    }
}

pub(super) fn run(args: &[String]) -> Result<(), Box<dyn std::error::Error>> {
    let (mode, archive) = configuration(args)?;
    // The launcher file handle is already closed. Requests see only memory.
    serve(io::stdin().lock(), io::stdout(), mode, archive.as_ref())?;
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

enum ToolRequest {
    Spid(DeclarationRequest),
    Bsa(ArchivePageRequest),
}

struct Work {
    id: Value,
    request: ToolRequest,
}

struct InFlight {
    id: Value,
    cancelled: bool,
}

struct Wire<W> {
    writer: W,
    inflight: Option<InFlight>,
}

impl<W: Write> Wire<W> {
    fn cancel(&mut self, id: &Value) {
        if let Some(call) = self.inflight.as_mut().filter(|call| call.id == *id) {
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

fn server_info() -> Value {
    json!({"name":"modhunter-read-only","version":env!("CARGO_PKG_VERSION")})
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

fn complete(mode: Mode, mut result: Value) -> Value {
    if mode == Mode::Modern {
        result["resultType"] = json!("complete");
        result["_meta"] = json!({SERVER_KEY: server_info()});
    }
    result
}

fn list(mode: Mode, archive: Option<&ArchiveSnapshot>) -> Value {
    let mut result = json!({"tools":[{
        "name":TOOL,
        "title":"Preview supplied Skyrim SPID declarations",
        "description":"Review ordinary declarations and declared targets in supplied UTF-8 text only. No files are opened, directives applied, or in-game matches verified. Filters and chance slots are not evaluated.",
        "inputSchema":{"type":"object","additionalProperties":false,"required":["sourceName","text"],
          "properties":{"sourceName":{"type":"string","minLength":1,"maxLength":160},
                        "text":{"type":"string","maxLength":524288}}},
        "annotations":{"readOnlyHint":true,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}
    }]});
    if let Some(archive) = archive {
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
    if mode == Mode::Modern {
        result["ttlMs"] = json!(0);
        result["cacheScope"] = json!("private");
    }
    complete(mode, result)
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

fn execute(mode: Mode, request: ToolRequest, archive: Option<&ArchiveSnapshot>) -> Value {
    let generated = match request {
        ToolRequest::Spid(request) => super::spid_preview::run(
            &request.source_name,
            request.text.as_bytes(),
        )
        .and_then(|report| {
            serde_json::to_value(report).map_err(|_| "Cannot encode SPID preview".to_owned())
        }),
        ToolRequest::Bsa(request) => archive
            .ok_or_else(|| "No BSA was selected by the launcher".to_owned())
            .and_then(|selection| archive_page(selection, request)),
    };
    match generated {
        Ok(report) => complete(
            mode,
            json!({"content":[{"type":"text","text":report.to_string()}],
                                "structuredContent":report,"isError":false}),
        ),
        Err(reason) => complete(
            mode,
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
    archive: Option<&ArchiveSnapshot>,
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
                                      "serverInfo":server_info()}),
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
                json!({
                    "supportedVersions":[MODERN],"capabilities":{"tools":{}},"ttlMs":0,"cacheScope":"private"
                }),
            ),
        )),
        "ping" => reply(ok(id, complete(mode, json!({})))),
        "tools/list" if params.get("cursor").is_none() => reply(ok(id, list(mode, archive))),
        "tools/list" => reply(error(id, -32602, "No next tool catalog page")),
        "tools/call" => {
            let arguments = params
                .get_mut("arguments")
                .map(std::mem::take)
                .unwrap_or_default();
            let request = match params["name"].as_str() {
                Some(TOOL) => serde_json::from_value(arguments).map(ToolRequest::Spid),
                Some(BSA_TOOL) if archive.is_some() => {
                    serde_json::from_value(arguments).map(ToolRequest::Bsa)
                }
                _ => return reply(error(id, -32602, "Unknown read-only MCP tool")),
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
    archive: Option<&ArchiveSnapshot>,
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
                let result = execute(mode, request, archive);
                wire.lock().unwrap().finish(id, result)?;
            }
            Ok(())
        });
        let receiving = (|| -> io::Result<()> {
            loop {
                match read_frame(&mut input, &mut frame)? {
                    Frame::Ready => match serde_json::from_slice(&frame) {
                        Ok(message) => handle(message, mode, &mut phase, wire, &tx, archive)?,
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
            }),
        };
        wire.cancel(&json!("not-call-1"));
        wire.finish(json!("call-1"), json!({"fixture":1})).unwrap();
        assert!(!wire.writer.is_empty());
        wire.writer.clear();
        wire.inflight = Some(InFlight {
            id: json!("call-2"),
            cancelled: false,
        });
        wire.cancel(&json!("call-2"));
        wire.finish(json!("call-2"), json!({"fixture":2})).unwrap();
        assert!(wire.writer.is_empty());
        assert!(wire.inflight.is_none());
    }
}
