//! Optional stdio client for the upstream houseCARL release. The launcher selects
//! the executable and directories. MCP calls can only choose a record and view.
use super::{Frame, read_frame};
use serde::Deserialize;
use serde_json::{Value, json};
use std::{
    fs::File,
    io::{BufReader, Read, Write},
    path::{Path, PathBuf},
    process::{Child, ChildStdin, Command, Stdio},
    sync::{Mutex, mpsc},
    time::{Duration, Instant},
};

pub(super) const TOOL: &str = "skyrim_record";
pub(super) const INSPECT: &str = "skyrim_inspect";
pub(super) const STATUS: &str = "skyrim_load_order";
const STATUS_UPSTREAM: &str = "housecarl_load_order_status";
const RECORD_UPSTREAM: &str = "housecarl_records";

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Directories {
    data_dir: PathBuf,
    mods_dir: PathBuf,
    profile_dir: PathBuf,
    overwrite_dir: PathBuf,
}

pub(super) struct Companion {
    exe: PathBuf,
    dirs: Directories,
    runtime: Mutex<Option<Session>>,
}

impl Companion {
    pub(super) fn configure(exe: &Path, config: &Path) -> Result<Self, &'static str> {
        if !exe.is_absolute() || !exe.is_file() {
            return Err("Select an absolute path to the houseCARL release executable");
        }
        let mut bytes = Vec::new();
        File::open(config)
            .and_then(|file| file.take(65537).read_to_end(&mut bytes))
            .map_err(|_| "Cannot read the selected houseCARL directories document")?;
        if bytes.len() > 65536 {
            return Err("houseCARL directories document is too large");
        }
        let dirs: Directories =
            serde_json::from_slice(bytes.strip_prefix(&[0xef, 0xbb, 0xbf]).unwrap_or(&bytes))
                .map_err(|_| "Invalid houseCARL directories document")?;
        if [
            &dirs.data_dir,
            &dirs.mods_dir,
            &dirs.profile_dir,
            &dirs.overwrite_dir,
        ]
        .iter()
        .any(|p| !p.is_absolute() || !p.is_dir())
            || !dirs.profile_dir.join("modlist.txt").is_file()
            || !dirs.profile_dir.join("plugins.txt").is_file()
        {
            return Err(
                "Select existing absolute game Data, mods, profile and overwrite directories",
            );
        }
        Ok(Self {
            exe: exe.to_owned(),
            dirs,
            runtime: Mutex::new(None),
        })
    }

    pub(super) fn read(&self, query: Query) -> Result<Value, String> {
        let (name, arguments) = match query {
            Query::Status(_) => (STATUS_UPSTREAM, json!({})),
            Query::Record(record) => (RECORD_UPSTREAM, record.arguments()?),
            Query::Inspect(inspect) => inspect.arguments(&self.dirs.data_dir)?,
        };
        let mut runtime = self
            .runtime
            .lock()
            .map_err(|_| "houseCARL client stopped")?;
        if runtime.is_none() {
            *runtime = Some(Session::start(&self.exe, &self.dirs)?);
        }
        let upstream = runtime.as_mut().expect("initialized above");
        let result = upstream.request("tools/call", json!({"name":name, "arguments":arguments}));
        let reply = match result {
            Ok(value) => value,
            Err(error) => {
                *runtime = None;
                return Err(error);
            }
        };
        let content = reply["content"]
            .as_array()
            .ok_or("Invalid houseCARL tool response")?;
        let messages: Vec<_> = content
            .iter()
            .filter_map(|part| part["text"].as_str())
            .collect();
        if messages.is_empty() {
            return Err("houseCARL returned no text".into());
        }
        if reply["isError"].as_bool() == Some(true) {
            return Err(messages.join("\n"));
        }
        Ok(
            json!({"engine":"houseCARL", "version":upstream.version, "tool":name, "messages":messages}),
        )
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
pub(super) struct Record {
    form_id: String,
    #[serde(default)]
    view: View,
}
#[derive(Default, Deserialize, serde::Serialize)]
#[serde(rename_all = "lowercase")]
enum View {
    #[default]
    Summary,
    Tree,
    Everything,
}

fn validate_form_id(form_id: &str) -> Result<(), String> {
    let valid_hex = |s: &str| s.bytes().all(|b| b.is_ascii_hexdigit());
    let valid = match form_id.split_once(':') {
        None => form_id.len() == 8 && valid_hex(form_id),
        Some((id, plugin)) => {
            (6..=8).contains(&id.len())
                && valid_hex(id)
                && !plugin.is_empty()
                && !plugin.contains(['/', '\\', ':', '@'])
                && !plugin.chars().any(char::is_control)
                && [".esm", ".esp", ".esl"]
                    .iter()
                    .any(|ext| plugin.to_ascii_lowercase().ends_with(ext))
        }
    };
    if !valid {
        return Err(
            "Use an eight-digit runtime FormID or a hex ID followed by a plugin filename".into(),
        );
    }
    Ok(())
}

impl Record {
    fn arguments(self) -> Result<Value, String> {
        validate_form_id(&self.form_id)?;
        Ok(json!({"formids":[self.form_id],"project":{"form":self.view}}))
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct NoArguments {}

pub(super) enum Query {
    Status(NoArguments),
    Record(Record),
    Inspect(Diagnostic),
}

#[derive(Deserialize)]
#[serde(tag = "kind", rename_all = "lowercase", deny_unknown_fields)]
pub(super) enum Diagnostic {
    Archive {
        name: String,
    },
    Check {
        #[serde(rename = "formId")]
        form_id: String,
        family: CheckFamily,
    },
    Asset {
        path: String,
    },
    Nif {
        path: String,
    },
    Facegen {
        #[serde(rename = "formId")]
        form_id: String,
    },
    Distributor {
        layer: Layer,
        filter: Option<String>,
    },
    Skse {
        filter: Option<String>,
    },
}

#[derive(Deserialize, serde::Serialize)]
#[serde(rename_all = "lowercase")]
pub(super) enum Layer {
    Skypatcher,
    Spid,
    Kid,
    Bos,
    Flm,
}

#[derive(Deserialize, serde::Serialize)]
#[serde(rename_all = "lowercase")]
pub(super) enum CheckFamily {
    Errors,
    Scripts,
    Facegen,
    Dialogue,
}

impl Diagnostic {
    fn arguments(self, data_dir: &Path) -> Result<(&'static str, Value), String> {
        match self {
            Self::Archive { name } => {
                if name.starts_with(['@', '.'])
                    || name.contains(['/', '\\', ':'])
                    || name.chars().any(char::is_control)
                    || !name.to_ascii_lowercase().ends_with(".bsa")
                {
                    return Err("Choose one BSA filename in the configured game Data folder".into());
                }
                let root = data_dir
                    .canonicalize()
                    .map_err(|_| "Game Data folder is unavailable")?;
                let path = root
                    .join(name)
                    .canonicalize()
                    .map_err(|_| "Selected Data archive is unavailable")?;
                if path.parent() != Some(root.as_path()) || !path.is_file() {
                    return Err("Selected BSA is outside the configured game Data folder".into());
                }
                Ok((
                    "housecarl_bsa_list",
                    json!({"archive":path,"counts_only":true}),
                ))
            }
            Self::Check { form_id, family } => {
                validate_form_id(&form_id)?;
                // houseCARL scopes dialogue by its seed topic or quest. The
                // other families scope their sweep by a list of record IDs.
                let args = match family {
                    CheckFamily::Dialogue => json!({"seeds":[form_id],"findings":[family]}),
                    _ => json!({"formids":[form_id],"findings":[family]}),
                };
                Ok(("housecarl_check", args))
            }
            Self::Asset { ref path } | Self::Nif { ref path } => {
                if path.starts_with('@')
                    || path.contains(':')
                    || path.chars().any(char::is_control)
                    || path
                        .split(['/', '\\'])
                        .any(|p| p.is_empty() || p == "." || p == "..")
                {
                    return Err("Select a Data-relative asset path".into());
                }
                let upstream = if matches!(self, Self::Nif { .. }) {
                    if !path.to_ascii_lowercase().ends_with(".nif") {
                        return Err("Select a Data-relative NIF file".into());
                    }
                    "housecarl_nif_inspect"
                } else {
                    "housecarl_asset_status"
                };
                Ok((upstream, json!({"paths":[path]})))
            }
            Self::Facegen { form_id } => {
                validate_form_id(&form_id)?;
                Ok(("housecarl_asset_status", json!({"formids":[form_id]})))
            }
            Self::Distributor { layer, filter } => Ok((
                "housecarl_layer",
                json!({"distributor":[layer],"filter":filter}),
            )),
            Self::Skse { filter } => Ok(("housecarl_skse", json!({"filter":filter}))),
        }
    }
}

pub(super) fn tools() -> [Value; 3] {
    let annotations = json!({"readOnlyHint":true,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false});
    [
        json!({"name":STATUS,"title":"Inspect Skyrim load-order status","description":"Read active plugins and reported load failures from the configured houseCARL engine.",
        "inputSchema":{"type":"object","properties":{},"additionalProperties":false},"annotations":annotations}),
        json!({"name":INSPECT,"title":"Inspect Skyrim assets, FaceGen, SKSE and distributor rules",
            "description":"Resolve an asset winner, inspect a NIF, compare an NPC's FaceGen providers, read distributor rules or inspect the configured SKSE layer. Check one record for link, script or FaceGen findings, or count all entries in one game Data BSA.",
            "inputSchema":{"type":"object","required":["kind"],"oneOf":[
                {"properties":{"kind":{"const":"check"},"formId":{"type":"string"},"family":{"enum":["errors","scripts","facegen","dialogue"]}},"required":["formId","family"],"additionalProperties":false},
                {"properties":{"kind":{"const":"archive"},"name":{"type":"string"}},"required":["name"],"additionalProperties":false},
                {"properties":{"kind":{"enum":["asset","nif"]},"path":{"type":"string"}},"required":["path"],"additionalProperties":false},
                {"properties":{"kind":{"const":"facegen"},"formId":{"type":"string"}},"required":["formId"],"additionalProperties":false},
                {"properties":{"kind":{"const":"distributor"},"layer":{"enum":["skypatcher","spid","kid","bos","flm"]},"filter":{"type":"string"}},"required":["layer"],"additionalProperties":false},
                {"properties":{"kind":{"const":"skse"},"filter":{"type":"string"}},"additionalProperties":false}
            ]},"annotations":annotations}),
        json!({"name":TOOL,"title":"Inspect a Skyrim record and its overrides","description":"Read a selected Skyrim record from the configured profile. The tree view compares providers and identifies the winner. Preserve any upstream exclusions.",
        "inputSchema":{"type":"object","required":["formId"],"additionalProperties":false,
        "properties":{"formId":{"type":"string"},"view":{"type":"string","enum":["summary","tree","everything"]}}},"annotations":annotations}),
    ]
}

struct Session {
    child: Child,
    stdin: ChildStdin,
    replies: mpsc::Receiver<Value>,
    next_id: u64,
    version: String,
    _data: tempfile::TempDir,
}

impl Drop for Session {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

impl Session {
    fn start(exe: &Path, dirs: &Directories) -> Result<Self, String> {
        let data = tempfile::Builder::new()
            .prefix("modhunter-housecarl-")
            .tempdir()
            .map_err(|_| "Cannot create separate houseCARL session directory")?;
        let mut command = Command::new(exe);
        command
            .current_dir(data.path())
            .env("HOUSECARL_DATA_DIR", data.path())
            .env("HouseCarl__DataDir", &dirs.data_dir)
            .env("HouseCarl__ModsDir", &dirs.mods_dir)
            .env("HouseCarl__ProfileDir", &dirs.profile_dir)
            .env("HouseCarl__OverwriteDir", &dirs.overwrite_dir)
            .env("HouseCarl__MaxPlugins", "0")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
        let mut child = command
            .spawn()
            .map_err(|_| "Cannot start the selected houseCARL server")?;
        let stdin = child.stdin.take().expect("piped stdin");
        let mut reader = BufReader::new(child.stdout.take().expect("piped stdout"));
        let (tx, replies) = mpsc::sync_channel(1);
        std::thread::spawn(move || {
            let mut frame = Vec::new();
            while matches!(read_frame(&mut reader, &mut frame), Ok(Frame::Ready)) {
                let Ok(value) = serde_json::from_slice(&frame) else {
                    break;
                };
                if tx.send(value).is_err() {
                    break;
                }
            }
        });
        let mut session = Self {
            child,
            stdin,
            replies,
            next_id: 0,
            version: String::new(),
            _data: data,
        };
        let hello=session.request("initialize",json!({"protocolVersion":super::LEGACY,"capabilities":{},"clientInfo":{"name":"Modhunter","version":env!("CARGO_PKG_VERSION")}}))?;
        if hello["serverInfo"]["name"] != "houseCARL" || hello["protocolVersion"] != super::LEGACY {
            return Err("The selected program is not a compatible houseCARL MCP server".into());
        }
        session.version = hello["serverInfo"]["version"]
            .as_str()
            .ok_or("Missing houseCARL version")?
            .to_owned();
        session.send(&json!({"jsonrpc":"2.0","method":"notifications/initialized"}))?;
        Ok(session)
    }

    fn send(&mut self, frame: &Value) -> Result<(), String> {
        writeln!(self.stdin, "{frame}")
            .and_then(|_| self.stdin.flush())
            .map_err(|_| "Lost the houseCARL process connection".into())
    }

    fn request(&mut self, method: &str, params: Value) -> Result<Value, String> {
        self.next_id += 1;
        self.send(&json!({"jsonrpc":"2.0","id":self.next_id,"method":method,"params":params}))?;
        let deadline = Instant::now() + Duration::from_secs(600);
        loop {
            let message = self
                .replies
                .recv_timeout(deadline.saturating_duration_since(Instant::now()))
                .map_err(|_| "houseCARL stopped or exceeded its response deadline")?;
            if message["jsonrpc"] != "2.0" {
                return Err("Invalid houseCARL protocol frame".into());
            }
            if message.get("method").is_some() && message.get("id").is_none() {
                continue;
            }
            if message["id"] != self.next_id {
                return Err("Unexpected houseCARL response ID".into());
            }
            if message.get("error").is_some() {
                return Err("houseCARL rejected the MCP request".into());
            }
            return message
                .get("result")
                .cloned()
                .ok_or_else(|| "Missing houseCARL result".into());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn archive_and_check_accept_only_scoped_inputs() {
        let fixture = tempfile::tempdir().unwrap();
        std::fs::write(fixture.path().join("Example.bsa"), b"test").unwrap();
        for name in [
            "@list.bsa",
            "../Example.bsa",
            "C:/Example.bsa",
            "Example.ini",
            "Missing.bsa",
        ] {
            assert!(
                Diagnostic::Archive { name: name.into() }
                    .arguments(fixture.path())
                    .is_err()
            );
        }
        let (tool, args) = Diagnostic::Archive {
            name: "Example.bsa".into(),
        }
        .arguments(fixture.path())
        .unwrap();
        assert_eq!(tool, "housecarl_bsa_list");
        assert_eq!(args["counts_only"], true);
        assert!(
            Diagnostic::Check {
                form_id: "@file".into(),
                family: CheckFamily::Errors
            }
            .arguments(fixture.path())
            .is_err()
        );
    }

    #[test]
    fn record_read_does_not_accept_file_lists_paths_or_write_options() {
        for key in [
            "@file",
            "00000F:../foo.esp",
            "00000F:C:\\fake.esm",
            "broken",
        ] {
            let value: Record = serde_json::from_value(json!({"formId":key})).unwrap();
            assert!(value.arguments().is_err());
        }
        assert!(
            serde_json::from_value::<Record>(
                json!({"formId":"00000F:Skyrim.esm","to_file":"file"})
            )
            .is_err()
        );
        let value: Record =
            serde_json::from_value(json!({"formId":"00000F:Skyrim.esm","view":"tree"})).unwrap();
        assert_eq!(
            value.arguments().unwrap(),
            json!({"formids":["00000F:Skyrim.esm"],"project":{"form":"tree"}})
        );
    }

    #[test]
    fn dialogue_check_uses_the_upstream_seed_scope() {
        let check: Diagnostic = serde_json::from_value(json!({
            "kind":"check", "family":"dialogue", "formId":"0F1AC1:Skyrim.esm"
        }))
        .unwrap();
        let (tool, args) = check.arguments(Path::new(".")).unwrap();
        assert_eq!(tool, "housecarl_check");
        assert_eq!(
            args,
            json!({"seeds":["0F1AC1:Skyrim.esm"],"findings":["dialogue"]})
        );
    }
}
