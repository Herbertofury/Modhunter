use serde_json::{Value, json};
use std::io::Write;
use std::path::Path;
use std::process::{Command, Output, Stdio};

fn call(cwd: &Path, args: &[&str], bytes: &[u8]) -> Output {
    let mut child = Command::new(env!("CARGO_BIN_EXE_modhunter-cli"))
        .arg("plugin-order-preview")
        .args(args)
        .current_dir(cwd)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    // Invalid argument forms can refuse before reading stdin.
    let write = child.stdin.take().unwrap().write_all(bytes);
    let out = child.wait_with_output().unwrap();
    if out.status.success() {
        write.unwrap();
    }
    out
}

fn json_call(cwd: &Path, args: &[&str], body: &Value) -> Output {
    call(cwd, args, &serde_json::to_vec(body).unwrap())
}

fn ok(out: &Output) -> Value {
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(out.stderr.is_empty());
    serde_json::from_slice(&out.stdout).unwrap()
}

fn refused(out: Output, why: &str) {
    assert!(!out.status.success());
    assert!(out.stdout.is_empty());
    assert!(String::from_utf8_lossy(&out.stderr).contains(why));
}

fn snapshot() -> Value {
    json!({
        "profileId":"0123456789abcdef","executionEpoch":14,"rows":[
            {"id":"base","fileName":"Base.esm","enabled":true,"orderClass":"locked_master","declaredMasters":[]},
            {"id":"first","fileName":"First.esp","enabled":true,"orderClass":"regular","declaredMasters":[]},
            {"id":"second","fileName":"Second.esp","enabled":true,"orderClass":"regular","declaredMasters":["First.esp"]}
        ]
    })
}

#[test]
fn captures_and_replays_exact_move_without_changing_any_working_directory_files() {
    let temp = tempfile::tempdir().unwrap();
    let dir = temp.path();
    let profile = dir.join("plugins.txt");
    let sentinel = dir.join("README.txt");
    let initial = b"*Base.esm\r\n*First.esp\r\n*Second.esp\r\n";
    let inert = b"disposable fixture, not a live profile";
    std::fs::write(&profile, initial).unwrap();
    std::fs::write(&sentinel, inert).unwrap();
    let before = profile.metadata().unwrap().modified().unwrap();

    let s = snapshot();
    let receipt = ok(&json_call(dir, &["capture"], &s));
    assert_eq!(receipt["profileId"], s["profileId"]);
    assert_eq!(receipt["executionEpoch"], 14);
    assert_eq!(receipt["snapshotSha256"].as_str().unwrap().len(), 64);
    let r = json!({"expected": receipt, "snapshot": s,
                   "action":{"type":"move_after","id":"first","anchorId":"second"}});
    let one = json_call(dir, &["preview"], &r);
    let result = ok(&one);
    assert_eq!(one.stdout, json_call(dir, &["preview"], &r).stdout);
    assert_eq!(result["scope"], "caller_supplied_snapshot_only");
    assert_eq!(result["status"], "proposed_not_applied");
    assert_eq!(result["before"], receipt);
    assert_eq!(result["beforeOrder"][1]["id"], "first");
    assert_eq!(result["afterOrder"][1]["id"], "second");
    assert_eq!(result["afterOrder"][2]["id"], "first");
    assert_ne!(result["after"]["snapshotSha256"], receipt["snapshotSha256"]);
    assert_eq!(
        result["afterDirectMasterFindings"],
        json!([{
            "pluginId":"second","declaredMaster":"First.esp",
            "status":"declared_master_after_plugin_in_snapshot"
        }])
    );
    assert_eq!(result["recordConflicts"], "not_evaluated");
    assert_eq!(result["assetConflicts"], "not_evaluated");
    assert_eq!(result["actualProfileAccepted"], "not_evaluated");
    assert_eq!(std::fs::read(&profile).unwrap(), initial);
    assert_eq!(std::fs::read(&sentinel).unwrap(), inert);
    assert_eq!(profile.metadata().unwrap().modified().unwrap(), before);
    assert_eq!(std::fs::read_dir(dir).unwrap().count(), 2);
}

#[test]
fn staleness_locks_and_missing_ids_return_reasons_not_a_plan() {
    let temp = tempfile::tempdir().unwrap();
    let dir = temp.path();
    let s = snapshot();
    let receipt = ok(&json_call(dir, &["capture"], &s));
    let mut req = json!({
        "expected":receipt, "snapshot":s,
        "action":{"type":"set_enabled","id":"first","enabled":false}
    });
    let preview = ok(&json_call(dir, &["preview"], &req));
    assert_eq!(preview["afterOrder"][1]["enabled"], false);
    assert_eq!(
        preview["afterDirectMasterFindings"][0]["status"],
        "declared_master_disabled_in_snapshot"
    );

    req["snapshot"]["rows"].as_array_mut().unwrap().swap(1, 2);
    refused(
        json_call(dir, &["preview"], &req),
        "snapshot digest changed",
    );
    req["snapshot"] = snapshot();
    req["snapshot"]["executionEpoch"] = json!(15);
    refused(json_call(dir, &["preview"], &req), "execution epoch");
    req["snapshot"] = snapshot();
    req["snapshot"]["profileId"] = json!("1234567890abcdef");
    refused(json_call(dir, &["preview"], &req), "Profile");
    req["snapshot"] = snapshot();
    req["action"]["id"] = json!("base");
    refused(json_call(dir, &["preview"], &req), "Locked master");
    req["action"]["id"] = json!("FIRST");
    refused(json_call(dir, &["preview"], &req), "stable ID");
}

#[test]
fn help_and_refused_arguments_never_advertise_or_accept_files_or_apply() {
    let temp = tempfile::tempdir().unwrap();
    let dir = temp.path();
    refused(
        call(dir, &["preview", "--profile", "plugins.txt"], b""),
        "one JSON object on stdin",
    );
    refused(call(dir, &["apply"], b""), "requires capture or preview");
    refused(call(dir, &["capture"], b"{\"profileId\":\"0123456789abcdef\",\"executionEpoch\":14,\"rows\":[],\"file\":\"plugins.txt\"}"), "Invalid snapshot JSON");
    let help = Command::new(env!("CARGO_BIN_EXE_modhunter-cli"))
        .arg("--help")
        .output()
        .unwrap();
    assert!(help.status.success());
    assert!(String::from_utf8_lossy(&help.stdout).contains("plugin-order-preview capture|preview"));
    assert_eq!(std::fs::read_dir(dir).unwrap().count(), 0);
}
