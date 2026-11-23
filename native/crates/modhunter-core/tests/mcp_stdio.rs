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
fn absent_launcher_archive_is_neither_listed_nor_callable() {
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
