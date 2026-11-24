use serde_json::{Value, json};
use std::{
    io::{BufRead, BufReader, Write},
    process::{Child, ChildStdin, Command, Stdio},
    sync::mpsc::{self, Receiver},
    time::Duration,
};

struct Client {
    process: Child,
    input: Option<ChildStdin>,
    output: Receiver<String>,
}

impl Client {
    fn start(extra: &[&str]) -> Self {
        let mut process = Command::new(env!("CARGO_BIN_EXE_modhunter-cli"))
            .arg("mcp-stdio")
            .args(extra)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap();
        let input = process.stdin.take();
        let stdout = process.stdout.take().unwrap();
        let (tx, output) = mpsc::channel();
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                if tx.send(line).is_err() {
                    break;
                }
            }
        });
        Self {
            process,
            input,
            output,
        }
    }

    fn send(&mut self, request: Value) {
        let input = self.input.as_mut().unwrap();
        writeln!(input, "{request}").unwrap();
        input.flush().unwrap();
    }

    fn recv(&self) -> Value {
        let frame = self.output.recv_timeout(Duration::from_secs(6)).unwrap();
        serde_json::from_str(&frame).expect("stdout must be JSON-RPC only")
    }

    fn exchange(&mut self, request: Value) -> Value {
        let id = request["id"].clone();
        self.send(request);
        let reply = self.recv();
        assert_eq!(reply["id"], id);
        assert_eq!(reply["jsonrpc"], "2.0");
        reply
    }

    fn shutdown(&mut self) {
        drop(self.input.take());
        assert_eq!(
            self.output.recv_timeout(Duration::from_secs(6)),
            Err(mpsc::RecvTimeoutError::Disconnected)
        );
        assert!(self.process.wait().unwrap().success());
        let mut errors = String::new();
        std::io::Read::read_to_string(&mut self.process.stderr.take().unwrap(), &mut errors)
            .unwrap();
        assert!(errors.is_empty(), "{errors}");
    }
}

impl Drop for Client {
    fn drop(&mut self) {
        let _ = self.process.kill();
        let _ = self.process.wait();
    }
}

fn modern(id: u64, method: &str, mut params: Value) -> Value {
    params["_meta"] = json!({
      "io.modelcontextprotocol/protocolVersion":"2026-07-28",
      "io.modelcontextprotocol/clientCapabilities":{},
      "io.modelcontextprotocol/clientInfo":{"name":"modhunter-test-client","version":"0"}
    });
    json!({"jsonrpc":"2.0","id":id,"method":method,"params":params})
}

fn call_content(name: &str, text: &str) -> Value {
    json!({"name":"spid_preview","arguments":{"sourceName":name,"text":text}})
}

#[test]
fn modern_real_stdio_discovers_lists_and_reuses_existing_spid_parser() {
    let mut client = Client::start(&[]);
    let result = client.exchange(modern(1, "server/discover", json!({})));
    assert_eq!(result["result"]["supportedVersions"], json!(["2026-07-28"]));
    assert_eq!(result["result"]["resultType"], "complete");
    assert_eq!(
        result["result"]["_meta"]["io.modelcontextprotocol/serverInfo"]["name"],
        "modhunter-read-only"
    );
    assert_eq!(result["result"]["capabilities"], json!({"tools":{}}));
    let listed = client.exchange(modern(2, "tools/list", json!({})));
    assert_eq!(listed["result"]["tools"][0]["name"], "spid_preview");
    assert_eq!(
        listed["result"]["tools"][0]["annotations"]["readOnlyHint"],
        true
    );
    assert_eq!(
        listed["result"]["tools"][0]["inputSchema"]["additionalProperties"],
        false
    );
    let from_text = "\u{feff}Spell = ExampleSpell|NONE|NONE\nSpell = 0x19~Example.esp|NONE";
    let reply = client.exchange(modern(
        3,
        "tools/call",
        call_content("Example_DISTR.ini", from_text),
    ));
    let report = &reply["result"]["structuredContent"];
    assert_eq!(reply["result"]["resultType"], "complete");
    assert_eq!(reply["result"]["isError"], false);
    assert_eq!(report["sourceName"], "Example_DISTR.ini");
    assert_eq!(report["rows"][0]["target"]["editorId"], "ExampleSpell");
    assert_eq!(report["rows"][1]["target"]["plugin"], "Example.esp");
    let legacy_text: Value =
        serde_json::from_str(reply["result"]["content"][0]["text"].as_str().unwrap()).unwrap();
    assert_eq!(&legacy_text, report);
    client.shutdown();
}

#[test]
fn modern_is_stateless_but_rejects_unsupported_scope_and_bad_parameters() {
    let mut client = Client::start(&[]);
    // Calls are legal before discovery and lack any separate authentication state.
    let response = client.exchange(modern(
        1,
        "tools/call",
        call_content("Example_DISTR.ini", "Spell = Example"),
    ));
    assert_eq!(response["result"]["isError"], false);
    let mut wrong = modern(2, "tools/list", json!({}));
    wrong["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] = json!("2099-01-01");
    let reply = client.exchange(wrong);
    assert_eq!(reply["error"]["code"], -32022);
    assert_eq!(reply["error"]["data"]["supported"], json!(["2026-07-28"]));
    let old = client.exchange(json!({"jsonrpc":"2.0","id":3,"method":"initialize","params":{"protocolVersion":"2025-11-25"}}));
    assert_eq!(old["error"]["code"], -32022);
    let mis = client.exchange(modern(
        4,
        "tools/call",
        json!({"name":"external_files","arguments":{}}),
    ));
    assert_eq!(mis["error"]["code"], -32602);
    let mut call = call_content("Example_DISTR.ini", "Spell = Example");
    call["arguments"]["unexpectedProperty"] = json!("not examined");
    assert_eq!(
        client.exchange(modern(5, "tools/call", call))["error"]["code"],
        -32602
    );
    let name = client.exchange(modern(
        6,
        "tools/call",
        call_content("nested/Example_DISTR.ini", "Spell = Example"),
    ));
    assert_eq!(name["result"]["isError"], true);
    assert!(name["result"]["structuredContent"].is_null());
    client
        .send(json!({"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":7}}));
    let ping = client.exchange(modern(7, "ping", json!({})));
    assert_eq!(ping["result"]["resultType"], "complete");
    client.shutdown();
}

#[test]
fn legacy_requires_true_initialize_then_initialized_and_does_not_advertise_unsupported_protocol() {
    let mut client = Client::start(&["--legacy-2025-11-25"]);
    assert_eq!(
        client.exchange(modern(1, "server/discover", json!({})))["error"]["code"],
        -32601
    );
    assert_eq!(
        client.exchange(json!({"jsonrpc":"2.0","id":2,"method":"tools/list"}))["error"]["code"],
        -32002
    );
    let init = client.exchange(json!({"jsonrpc":"2.0","id":3,"method":"initialize","params":{
        "protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"fixture","version":"1"}}}));
    assert_eq!(init["result"]["protocolVersion"], "2025-11-25");
    client.send(json!({"jsonrpc":"2.0","method":"notifications/initialized"}));
    let tools = client.exchange(json!({"jsonrpc":"2.0","id":4,"method":"tools/list"}));
    assert_eq!(tools["result"]["tools"][0]["name"], "spid_preview");
    assert!(tools["result"]["resultType"].is_null());
    let response = client.exchange(
        json!({"jsonrpc":"2.0","id":"legacy-call","method":"tools/call",
         "params":call_content("Pack_DISTR.ini","Perk = ExamplePerk")}),
    );
    assert_eq!(
        response["result"]["structuredContent"]["rows"][0]["target"]["editorId"],
        "ExamplePerk"
    );
    client.shutdown();
}

#[test]
fn input_budget_reports_mod_specific_limit_without_truncation() {
    let mut client = Client::start(&[]);
    let too_big = "a".repeat(512 * 1024 + 1);
    let reply = client.exchange(modern(
        1,
        "tools/call",
        call_content("Pack_DISTR.ini", &too_big),
    ));
    assert_eq!(reply["result"]["isError"], true);
    assert!(reply["result"]["structuredContent"].is_null());
    assert!(
        reply["result"]["content"][0]["text"]
            .as_str()
            .unwrap()
            .contains("512 KiB")
    );
    client.shutdown();
}

#[test]
fn oversized_transport_is_refused_and_connection_ends() {
    let mut client = Client::start(&[]);
    let input = client.input.as_mut().unwrap();
    let bytes = vec![b' '; 6 * 512 * 1024 + 4097];
    let _ = input.write_all(&bytes); // Server may finish at the cap before all bytes are written.
    let _ = input.flush();
    let frame = client.recv();
    assert_eq!(frame["error"]["code"], -32700);
    assert!(frame["id"].is_null());
    client.shutdown();
}

fn bsa_fixture(n: usize) -> Vec<u8> {
    let folder = format!("{}\0", "D".repeat(253));
    let names = (0..n)
        .map(|i| format!("{}-{i:04}.nif\0", "F".repeat(245)))
        .collect::<String>();
    assert_eq!(folder.len(), 254);
    let folder_pos = 36 + 24;
    let payload = folder_pos + 1 + folder.len() + 16 * n + names.len();
    let mut archive: Vec<_> = [
        u32::from_le_bytes(*b"BSA\0"),
        105,
        36,
        3,
        1,
        n as u32,
        folder.len() as u32,
        names.len() as u32,
        1,
    ]
    .into_iter()
    .flat_map(u32::to_le_bytes)
    .collect();
    archive.extend([0; 8]);
    archive.extend((n as u32).to_le_bytes());
    archive.extend([0; 4]);
    archive.extend(((folder_pos + names.len()) as u64).to_le_bytes());
    archive.push(folder.len() as u8);
    archive.extend(folder.bytes());
    for index in 0..n {
        archive.extend([0; 8]);
        archive.extend(1u32.to_le_bytes());
        archive.extend(((payload + index) as u32).to_le_bytes());
    }
    archive.extend(names.bytes());
    archive.resize(archive.len() + n, 16); // Opaque disposable payload; never read by BSA parser.
    archive
}

fn archive_call(arguments: Value) -> Value {
    json!({"name":"bsa_directory_page","arguments":arguments})
}

#[test]
fn absent_launch_files_are_neither_listed_nor_callable() {
    let mut client = Client::start(&[]);
    let listed = client.exchange(modern(1, "tools/list", json!({})));
    assert_eq!(listed["result"]["tools"].as_array().unwrap().len(), 1);
    let refused = client.exchange(modern(
        2,
        "tools/call",
        archive_call(json!({"archiveAlias":"example"})),
    ));
    assert_eq!(refused["error"]["code"], -32602);
    assert!(refused["result"].is_null());
    let no_plugin = client.exchange(modern(
        3,
        "tools/call",
        plugin_call(json!({"pluginAlias":"example"})),
    ));
    assert_eq!(no_plugin["error"]["code"], -32602);
    assert!(no_plugin["result"].is_null());
    client.shutdown();
}

#[test]
fn launcher_archive_pages_are_bounded_and_the_open_file_is_released_before_stdio() {
    let temp = tempfile::tempdir().unwrap();
    let selected = temp.path().join("Private launcher path.bsa");
    let n = 130;
    std::fs::write(&selected, bsa_fixture(n)).unwrap();
    let mut client = Client::start(&["--bsa", "Example-Archive", selected.to_str().unwrap()]);
    let listed = client.exchange(modern(1, "tools/list", json!({})));
    let tool = &listed["result"]["tools"][1];
    assert_eq!(tool["name"], "bsa_directory_page");
    assert_eq!(
        tool["inputSchema"]["properties"]["archiveAlias"]["enum"],
        json!(["Example-Archive"])
    );
    assert!(tool["inputSchema"]["properties"]["path"].is_null());

    #[cfg(target_os = "linux")]
    {
        let links = std::fs::read_dir(format!("/proc/{}/fd", client.process.id()))
            .unwrap()
            .filter_map(Result::ok)
            .filter_map(|handle| std::fs::read_link(handle.path()).ok())
            .collect::<Vec<_>>();
        assert!(
            links
                .iter()
                .any(|path| path.to_string_lossy().starts_with("pipe:"))
        );
        assert!(!links.contains(&selected.canonicalize().unwrap()));
    }
    // Windows file sharing would keep this selected path non-removable if the
    // shared-read startup handle were still open. A later read would fail on any OS.
    std::fs::remove_file(&selected).unwrap();

    for (id, offset, expected, next) in [
        (2, 0, 64, json!(64)),
        (3, 64, 64, json!(128)),
        (4, 128, 2, Value::Null),
        (5, 130, 0, Value::Null),
    ] {
        let result = client.exchange(modern(
            id,
            "tools/call",
            archive_call(json!({
                "archiveAlias":"Example-Archive","offset":offset
            })),
        ));
        let encoded = result.to_string();
        assert!(encoded.len() < 75_000);
        assert!(!encoded.contains("Private launcher path"));
        let info = &result["result"]["structuredContent"];
        assert_eq!(info["entryCount"], n);
        assert_eq!(info["nextOffset"], next);
        let paths = info["paths"].as_array().unwrap();
        assert_eq!(paths.len(), expected);
        if expected != 0 {
            assert!(
                paths[0]
                    .as_str()
                    .unwrap()
                    .ends_with(&format!("-{offset:04}.nif"))
            );
        }
    }
    let unknown_alias = client.exchange(modern(
        6,
        "tools/call",
        archive_call(json!({"archiveAlias":"different"})),
    ));
    assert_eq!(unknown_alias["result"]["isError"], true);
    assert!(unknown_alias["result"]["structuredContent"].is_null());
    let no_path = client.exchange(modern(
        7,
        "tools/call",
        archive_call(json!({
            "archiveAlias":"Example-Archive","path":"not an accepted argument"
        })),
    ));
    assert_eq!(no_path["error"]["code"], -32602);
    let wrong_selector = client.exchange(modern(
        8,
        "tools/call",
        archive_call(json!({"archiveAlias":"Example-Archive","offset":"64"})),
    ));
    assert_eq!(wrong_selector["error"]["code"], -32602);
    let outside = client.exchange(modern(
        9,
        "tools/call",
        archive_call(json!({"archiveAlias":"Example-Archive","offset":131})),
    ));
    assert_eq!(outside["result"]["isError"], true);
    assert!(outside["result"]["structuredContent"].is_null());
    client.shutdown();
}

#[test]
fn invalid_startup_selectors_refuse_without_starting_stdio() {
    let temp = tempfile::tempdir().unwrap();
    let selected = temp.path().join("Private selected.bsa");
    std::fs::write(&selected, bsa_fixture(1)).unwrap();
    let path = selected.to_str().unwrap();
    let missing = temp.path().join("Private nonexistent.bsa");
    for flags in [
        vec!["--watchtower", "write", temp.path().to_str().unwrap()],
        vec!["--watchtower", "read", path],
        vec!["--bsa", "wrong/alias", path],
        vec!["--bsa", "one", path, "--bsa", "two", path],
        vec!["--bsa", "one", missing.to_str().unwrap()],
    ] {
        let run = Command::new(env!("CARGO_BIN_EXE_modhunter-cli"))
            .arg("mcp-stdio")
            .args(flags)
            .stdin(Stdio::null())
            .output()
            .unwrap();
        assert!(!run.status.success());
        assert!(run.stdout.is_empty());
        assert!(!String::from_utf8_lossy(&run.stderr).contains("Private "));
    }
}

fn plugin_fixture(masters: &[String], flags: u32) -> Vec<u8> {
    fn field(tag: &[u8; 4], data: &[u8]) -> Vec<u8> {
        [tag.as_slice(), &(data.len() as u16).to_le_bytes(), data].concat()
    }
    let mut payload = field(
        b"HEDR",
        &[
            1.7_f32.to_le_bytes(),
            41_u32.to_le_bytes(),
            0x800_u32.to_le_bytes(),
        ]
        .concat(),
    );
    for master in masters {
        payload.extend(field(b"MAST", &[master.as_bytes(), &[0]].concat()));
        payload.extend(field(b"DATA", &[0; 8]));
    }
    payload.extend(field(b"CNAM", b"private-author-marker\0"));
    payload.extend(field(b"SNAM", b"private-description-marker\0"));
    [
        b"TES4".as_slice(),
        &(payload.len() as u32).to_le_bytes(),
        &flags.to_le_bytes(),
        &[0; 8],
        &44_u16.to_le_bytes(),
        &[0; 2],
        &payload,
        // Structurally not 41 framed records. Header-only must not deep-parse,
        // count or report this payload.
        b"private-payload-marker",
    ]
    .concat()
}

fn plugin_call(arguments: Value) -> Value {
    json!({"name":"plugin_header", "arguments":arguments})
}

#[test]
fn launcher_plugin_snapshot_composes_with_bsa_and_is_immutable_on_modern_transport() {
    let temp = tempfile::tempdir().unwrap();
    let selected = temp.path().join("private-file-name.esp");
    let archive = temp.path().join("private-archive-name.bsa");
    let masters = ["Named second.esm".to_owned(), "Named first.esp".to_owned()];
    std::fs::write(&selected, plugin_fixture(&masters, 0x201)).unwrap();
    std::fs::write(&archive, bsa_fixture(1)).unwrap();
    let mut client = Client::start(&[
        "--plugin",
        "PluginFixture",
        selected.to_str().unwrap(),
        "--bsa",
        "ArchiveFixture",
        archive.to_str().unwrap(),
    ]);
    let listed = client.exchange(modern(1, "tools/list", json!({})));
    let tools = listed["result"]["tools"].as_array().unwrap();
    assert_eq!(tools.len(), 3);
    assert_eq!(tools[0]["name"], "spid_preview");
    assert_eq!(tools[1]["name"], "bsa_directory_page");
    assert_eq!(tools[2]["name"], "plugin_header");
    assert_eq!(tools[2]["inputSchema"]["additionalProperties"], false);
    assert_eq!(
        tools[2]["inputSchema"]["properties"],
        json!({
            "pluginAlias":{"type":"string","enum":["PluginFixture"]}
        })
    );
    assert_eq!(tools[2]["annotations"]["readOnlyHint"], true);

    #[cfg(target_os = "linux")]
    {
        let opened = std::fs::read_dir(format!("/proc/{}/fd", client.process.id()))
            .unwrap()
            .filter_map(Result::ok)
            .filter_map(|handle| std::fs::read_link(handle.path()).ok())
            .collect::<Vec<_>>();
        assert!(!opened.contains(&selected.canonicalize().unwrap()));
        assert!(!opened.contains(&archive.canonicalize().unwrap()));
    }
    std::fs::remove_file(&selected).unwrap();
    let replacement = plugin_fixture(&["Unselected.esm".into()], 0);
    std::fs::write(&selected, &replacement).unwrap();
    std::fs::remove_file(&archive).unwrap();

    let received = client.exchange(modern(
        2,
        "tools/call",
        plugin_call(json!({"pluginAlias":"PluginFixture"})),
    ));
    assert_eq!(received["result"]["resultType"], "complete");
    assert_eq!(received["result"]["isError"], false);
    let report = &received["result"]["structuredContent"];
    assert_eq!(
        report,
        &json!({
            "scope":"launch-time-skyrim-se-format-header-assumed",
            "pluginAlias":"PluginFixture","headerFlags":513,
            "headerVersion":f64::from(1.7_f32),"formVersion":44,
            "masterFlag":true,"lightFlag":true,"declaredRecordAndGroupCount":41,
            "declaredMasters":masters,
            "masterResolution":"declarations_only_not_resolved","payloadDecoding":"not_evaluated"
        })
    );
    let legacy_text: Value =
        serde_json::from_str(received["result"]["content"][0]["text"].as_str().unwrap()).unwrap();
    assert_eq!(&legacy_text, report);
    for private in [
        "private-file-name",
        "private-author-marker",
        "private-description-marker",
        "private-payload-marker",
        "Unselected.esm",
    ] {
        assert!(!received.to_string().contains(private));
        assert!(!listed.to_string().contains(private));
    }
    let replacement_bsa = client.exchange(modern(
        3,
        "tools/call",
        archive_call(json!({"archiveAlias":"ArchiveFixture"})),
    ));
    assert_eq!(
        replacement_bsa["result"]["structuredContent"]["entryCount"],
        1
    );

    let bad_case = client.exchange(modern(
        4,
        "tools/call",
        plugin_call(json!({"pluginAlias":"pluginfixture"})),
    ));
    assert_eq!(bad_case["result"]["isError"], true);
    assert!(bad_case["result"]["structuredContent"].is_null());
    for (id, arguments) in [
        (
            5,
            json!({"pluginAlias":"PluginFixture","path":"private-client-path"}),
        ),
        (
            6,
            json!({"pluginAlias":"PluginFixture","resolveMasters":true}),
        ),
        (
            7,
            json!({"pluginAlias":"PluginFixture","outputPath":"private-client-path"}),
        ),
        (8, json!({"pluginAlias":9})),
    ] {
        let refused = client.exchange(modern(id, "tools/call", plugin_call(arguments)));
        assert_eq!(refused["error"]["code"], -32602);
        assert!(!refused.to_string().contains("private-client-path"));
    }
    assert_eq!(std::fs::read(&selected).unwrap(), replacement);
    assert_eq!(std::fs::read_dir(temp.path()).unwrap().count(), 1);
    client.shutdown();
}

#[test]
fn launcher_plugin_snapshot_works_in_legacy_mode_with_largest_allowed_declaration_set() {
    let temp = tempfile::tempdir().unwrap();
    let source = temp.path().join("private legacy plugin.esm");
    let archive = temp.path().join("private legacy archive.bsa");
    let names: Vec<_> = (0..256)
        .map(|i| format!("{}-{i:03}.esm", "X".repeat(247)))
        .collect();
    assert_eq!(names[0].len(), 255);
    std::fs::write(&source, plugin_fixture(&names, 1)).unwrap();
    std::fs::write(&archive, bsa_fixture(1)).unwrap();
    let mut client = Client::start(&[
        "--legacy-2025-11-25",
        "--bsa",
        "LegacyArchive",
        archive.to_str().unwrap(),
        "--plugin",
        "LegacyPlugin",
        source.to_str().unwrap(),
    ]);
    assert_eq!(
        client.exchange(json!({"jsonrpc":"2.0","id":1,"method":"tools/list"}))["error"]["code"],
        -32002
    );
    let hello = client.exchange(json!({"jsonrpc":"2.0","id":2,"method":"initialize","params":{
        "protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"fixture","version":"1"}
    }}));
    assert_eq!(hello["result"]["protocolVersion"], "2025-11-25");
    client.send(json!({"jsonrpc":"2.0","method":"notifications/initialized"}));
    let tools = client.exchange(json!({"jsonrpc":"2.0","id":3,"method":"tools/list"}));
    assert_eq!(tools["result"]["tools"][1]["name"], "bsa_directory_page");
    assert_eq!(tools["result"]["tools"][2]["name"], "plugin_header");
    std::fs::remove_file(&source).unwrap();
    let received = client.exchange(json!({"jsonrpc":"2.0","id":4,"method":"tools/call",
        "params":plugin_call(json!({"pluginAlias":"LegacyPlugin"}))}));
    assert_eq!(received["result"]["isError"], false);
    assert!(received["result"]["resultType"].is_null());
    assert_eq!(
        received["result"]["structuredContent"]["declaredMasters"],
        json!(names)
    );
    assert!(received.to_string().len() < 140_000);
    assert!(!received.to_string().contains("private legacy plugin"));
    client.shutdown();
}

#[test]
fn malformed_or_oversized_plugin_launch_selections_never_start_stdio_or_echo_paths() {
    let temp = tempfile::tempdir().unwrap();
    let selected = temp.path().join("private valid.esp");
    let malformed = temp.path().join("private malformed.esp");
    let oversized = temp.path().join("private too-many.esp");
    let wide = temp.path().join("private wide.esp");
    let unicode = temp.path().join("private encoded.esp");
    std::fs::write(&selected, plugin_fixture(&[], 0)).unwrap();
    std::fs::write(&malformed, b"not a header").unwrap();
    let many: Vec<_> = (0..257).map(|i| format!("Master-{i}.esm")).collect();
    std::fs::write(&oversized, plugin_fixture(&many, 0)).unwrap();
    std::fs::write(
        &wide,
        plugin_fixture(&[format!("{}.esp", "x".repeat(252))], 0),
    )
    .unwrap();
    std::fs::write(&unicode, plugin_fixture(&["Nonascii-é.esm".into()], 0)).unwrap();
    let path = selected.to_str().unwrap();
    let archive = temp.path().join("private archive.bsa");
    std::fs::write(&archive, bsa_fixture(1)).unwrap();
    let ids = [
        vec!["--plugin", "private/wrong", path],
        vec!["--plugin", "Alias", path, "--plugin", "Second", path],
        vec![
            "--plugin",
            "Alias",
            path,
            "--bsa",
            "ALIAS",
            archive.to_str().unwrap(),
        ],
        vec!["--plugin", "Alias"],
        vec!["--plugin", "Alias", malformed.to_str().unwrap()],
        vec!["--plugin", "Alias", oversized.to_str().unwrap()],
        vec!["--plugin", "Alias", wide.to_str().unwrap()],
        vec!["--plugin", "Alias", unicode.to_str().unwrap()],
    ];
    let run = |args: &[&str]| {
        let result = Command::new(env!("CARGO_BIN_EXE_modhunter-cli"))
            .arg("mcp-stdio")
            .args(args)
            .stdin(Stdio::null())
            .output()
            .unwrap();
        assert!(!result.status.success(), "{args:?}");
        assert!(result.stdout.is_empty(), "{args:?}");
        let error = String::from_utf8_lossy(&result.stderr);
        assert!(!error.is_empty());
        assert!(
            !error.contains("private"),
            "path or metadata leaked: {error}"
        );
    };
    for args in ids {
        run(&args);
    }
    #[cfg(unix)]
    {
        let link = temp.path().join("private link.esp");
        std::os::unix::fs::symlink(&selected, &link).unwrap();
        run(&["--plugin", "Alias", link.to_str().unwrap()]);
    }
}

#[test]
fn public_watch_mcp_scopes_management_and_respects_the_desktop_owner_and_private_rows() {
    use modhunter_core::watchtower::WatchStore;
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("watchtower.json");
    let named = dir.path().to_str().unwrap();
    let store = WatchStore::open(path.clone());
    let cmd = |action, payload| store.command(action, &payload, 123).unwrap();
    let session = cmd(
        "saveSession",
        json!({"origin":"https://github.com","label":"PRIVATE SESSION","remember":false}),
    );
    cmd(
        "saveWatch",
        json!({"url":"https://github.com/isolated/private","sessionId":session["sessionId"],"title":"PRIVATE SESSION WATCH"}),
    );
    cmd(
        "saveWatch",
        json!({"url":"https://github.com/isolated/browser","title":"PRIVATE BROWSER WATCH"}),
    );
    cmd(
        "saveWatch",
        json!({"url":"https://github.com/isolated/query?access_token=PRIVATE_SECRET","selector":"json:"}),
    );
    let removed = cmd(
        "saveWatch",
        json!({"url":"https://github.com/isolated/removed","selector":"json:","title":"PRIVATE REMOVED"}),
    );
    cmd("removeWatch", json!({"id":removed["watchId"]}));
    let paused = cmd(
        "saveWatch",
        json!({"url":"https://github.com/isolated/paused","enabled":false,"selector":"json:"}),
    );
    let saved_session = store
        .session(session["sessionId"].as_str().unwrap())
        .unwrap();
    let mut reader = Client::start(&["--watchtower", "read", named]);
    let mut writer = Client::start(&["--watchtower", "manage", named]);
    let call = |client: &mut Client, name: &str, args: Value| {
        client.exchange(modern(
            4,
            "tools/call",
            json!({"name":name,"arguments":args}),
        ))
    };
    let tools = reader.exchange(modern(1, "tools/list", json!({})));
    assert!(tools.to_string().contains("watchtower_search"));
    assert!(!tools.to_string().contains("watchtower_add"));
    assert!(
        call(
            &mut reader,
            "watchtower_add",
            json!({"url":"https://github.com/public.json","format":"json"})
        )["error"]
            .is_object()
    );
    assert!(!dir.path().join("watchtower.lock").exists());
    let before = std::fs::read(&path).unwrap();
    let page =
        call(&mut reader, "watchtower_search", json!({}))["result"]["structuredContent"].clone();
    assert_eq!(page["total"], 1);
    let row = &page["watches"][0];
    assert_eq!(row["watchId"], paused["watchId"]);
    assert_eq!(row["eligible"], false);
    assert!(!page.to_string().contains("PRIVATE"));
    assert_eq!(before, std::fs::read(&path).unwrap());
    let refresh = json!({"watchId":row["watchId"],"watchRevision":row["watchRevision"]});
    assert_eq!(
        call(&mut writer, "watchtower_refresh", refresh)["result"]["isError"],
        true
    );
    for url in [
        "http://127.0.0.1/",
        "https://github.com/isolated/removed",
        "https://api.github.com/foo?signature=PRIVATE",
    ] {
        assert_eq!(
            call(
                &mut writer,
                "watchtower_add",
                json!({"url":url,"format":"json"})
            )["result"]["isError"],
            true
        );
    }
    assert_eq!(before, std::fs::read(&path).unwrap());
    let mut lock = std::fs::OpenOptions::new();
    lock.read(true).write(true).create(true).truncate(false);
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        lock.share_mode(0);
    }
    let owner = lock.open(dir.path().join("watchtower.lock")).unwrap();
    #[cfg(not(windows))]
    owner.try_lock().unwrap();
    let add = json!({"url":"https://api.github.com/repos/fixture/public/ref?sort=PRIVATE_QUERY_VALUE","format":"json","title":"Branch"});
    assert_eq!(
        call(&mut writer, "watchtower_add", add.clone())["result"]["isError"],
        true
    );
    assert_eq!(
        call(&mut reader, "watchtower_search", json!({}))["result"]["structuredContent"]["total"],
        1
    );
    drop(owner);
    let added =
        call(&mut writer, "watchtower_add", add.clone())["result"]["structuredContent"].clone();
    assert_eq!(added["created"], true);
    assert!(!added.to_string().contains("PRIVATE_QUERY_VALUE"));
    let after = std::fs::read(&path).unwrap();
    assert_eq!(
        call(&mut writer, "watchtower_add", add)["result"]["structuredContent"]["created"],
        false
    );
    assert_eq!(std::fs::read(&path).unwrap(), after);
    let reopened = WatchStore::open_for_public_watches(path, false).unwrap();
    assert_eq!(reopened.session(&saved_session.id).unwrap(), saved_session);
    assert_eq!(reopened.public_watch_rows().len(), 2);
    let query_changed = json!({"query":"branch","offset":1,"revision":page["revision"]});
    assert_eq!(
        call(&mut reader, "watchtower_search", query_changed)["result"]["isError"],
        true
    );
}

#[test]
fn public_watch_search_pages_keep_their_revision_and_respect_privacy_and_mutes() {
    use modhunter_core::watchtower::WatchStore;
    let dir = tempfile::tempdir().unwrap();
    let source = dir.path().join("watchtower.json");
    let store = WatchStore::open(source);
    for index in 0..68 {
        store.command("saveWatch",&json!({"url":format!("https://github.com/public/release/{index}"),"title":format!("Named fixture {index}"),"selector":"feed:"}),123).unwrap();
    }
    let mut reader = Client::start(&["--watchtower", "read", dir.path().to_str().unwrap()]);
    let mut search = |args| {
        reader.exchange(modern(
            1,
            "tools/call",
            json!({"name":"watchtower_search","arguments":args}),
        ))["result"]
            .clone()
    };
    let first = search(json!({"query":"named fixture"}))["structuredContent"].clone();
    assert_eq!(first["total"], 68);
    assert_eq!(first["nextOffset"], 64);
    let next = json!({"query":"named fixture","offset":64,"revision":first["revision"]});
    let tail = search(next.clone())["structuredContent"].clone();
    assert_eq!(tail["watches"].as_array().unwrap().len(), 4);
    assert!(tail["nextOffset"].is_null());
    store
        .command(
            "settings",
            &json!({"mutedOrigins":["https://github.com"],"hideTitles":true,"privacyScreen":true}),
            200,
        )
        .unwrap();
    assert_eq!(search(next)["isError"], true);
    let public = search(json!({"query":"github.com"}))["structuredContent"].clone();
    assert_eq!(public["total"], 68);
    let row = &public["watches"][0];
    assert_eq!(row["eligible"], false);
    assert_eq!(row["title"], "github.com");
    assert_eq!(row["url"], "https://github.com/");
    assert!(!public.to_string().contains("Named fixture"));
    assert!(!public.to_string().contains("/public/release/"));
    assert!(
        store
            .begin_public_check(
                row["watchId"].as_str().unwrap(),
                row["watchRevision"].as_str().unwrap(),
                300
            )
            .is_err()
    );
}
