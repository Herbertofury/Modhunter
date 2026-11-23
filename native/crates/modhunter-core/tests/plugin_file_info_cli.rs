use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::fs;
use std::path::Path;
use std::process::{Command, Output};

fn data(tag: &[u8; 4], payload: &[u8]) -> Vec<u8> {
    [
        tag.as_slice(),
        &(payload.len() as u16).to_le_bytes(),
        payload,
    ]
    .concat()
}

fn record(tag: &[u8; 4], flags: u32, id: u32, payload: &[u8]) -> Vec<u8> {
    [
        tag.as_slice(),
        &(payload.len() as u32).to_le_bytes(),
        &flags.to_le_bytes(),
        &id.to_le_bytes(),
        &[0; 4],
        &44_u16.to_le_bytes(),
        &[0; 2],
        payload,
    ]
    .concat()
}

fn fixture(declared: u32, text: &[u8]) -> Vec<u8> {
    let mut hedr = Vec::new();
    hedr.extend(1.7_f32.to_le_bytes());
    hedr.extend(declared.to_le_bytes());
    hedr.extend([0; 4]);
    let header = record(
        b"TES4",
        0,
        0,
        &[
            data(b"HEDR", &hedr),
            data(b"MAST", b"NotPresent.esm\0"),
            data(b"DATA", &[0; 8]),
            text.to_vec(),
        ]
        .concat(),
    );
    let payload = [
        record(b"WEAP", 0, 0x0100_0800, b"not a decoded payload"),
        record(b"WEAP", 0, 0x0000_0810, b"other opaque payload"),
        record(b"MUST", 0x40000, 0x0000_0820, b"opaque compressed flag"),
        record(b"MUST", 0x20, 0, b"deleted, unresolved"),
        record(b"SNDR", 0, 0x0200_0821, b"unresolved master index"),
    ]
    .concat();
    [
        header,
        b"GRUP".to_vec(),
        ((payload.len() + 24) as u32).to_le_bytes().to_vec(),
        vec![0; 16],
        payload,
    ]
    .concat()
}

fn call(path: &Path, args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_modhunter-cli"))
        .args(["plugin-file-info", "--file"])
        .arg(path)
        .args(args)
        .output()
        .expect("start CLI")
}

fn good(output: Output) -> Value {
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(output.stderr.is_empty());
    serde_json::from_slice(&output.stdout).unwrap()
}

fn refused(output: Output) {
    assert!(!output.status.success());
    assert!(output.stdout.is_empty());
    assert!(!output.stderr.is_empty());
}

#[test]
fn exact_selected_file_census_distinguishes_all_index_classes_and_opaque_payloads() {
    let dir = tempfile::tempdir().unwrap();
    let selected = dir.path().join("Selected.esp.ghost");
    let bytes = fixture(
        6,
        &[
            data(b"CNAM", "Synthetic author é\0".as_bytes()),
            data(b"SNAM", b"Explicit test data\0"),
        ]
        .concat(),
    );
    fs::write(&selected, &bytes).unwrap();
    fs::write(dir.path().join("Unselected.esp"), b"must not be parsed").unwrap();
    let out = good(call(&selected, &[]));
    assert_eq!(out["fileName"], "Selected.esp.ghost");
    assert_eq!(out["ghostSuffix"], true);
    assert_eq!(out["sha256"], format!("{:x}", Sha256::digest(&bytes)));
    assert_eq!(out["header"]["masters"], json!(["NotPresent.esm"]));
    assert_eq!(out["masterResolution"], "declarations_only_not_resolved");
    assert_eq!(out["payloadDecoding"], "not_evaluated");
    assert_eq!(
        out["author"],
        json!({"state":"utf8","value":"Synthetic author é"})
    );
    assert_eq!(out["description"]["value"], "Explicit test data");
    assert_eq!(out["records"]["totalRecords"], 5);
    assert_eq!(out["records"]["groups"], 1);
    assert_eq!(out["records"]["scanComplete"], true);
    assert_eq!(out["records"]["compressedRecords"], 1);
    assert_eq!(out["records"]["deletedRecords"], 1);
    assert_eq!(out["records"]["newRecordTypes"], json!({"WEAP":1}));
    assert_eq!(
        out["allRecordTypes"],
        json!({
            "WEAP":{"localIndex":1,"declaredMasterIndex":1,"unresolvedIndex":0},
            "MUST":{"localIndex":0,"declaredMasterIndex":1,"unresolvedIndex":1},
            "SNDR":{"localIndex":0,"declaredMasterIndex":0,"unresolvedIndex":1},
        })
    );
    assert_eq!(fs::read(&selected).unwrap(), bytes);
    assert_eq!(
        fs::read(dir.path().join("Unselected.esp")).unwrap(),
        b"must not be parsed"
    );
    assert!(!dir.path().join("NotPresent.esm").exists());
    assert!(!out.to_string().contains(&*dir.path().to_string_lossy()));
}

#[test]
fn header_only_preserves_unknown_and_duplicate_text_and_does_not_claim_full_read() {
    let dir = tempfile::tempdir().unwrap();
    let selected = dir.path().join("Partial.esl");
    let mut bytes = fixture(
        6,
        &[
            data(b"CNAM", b"First\0"),
            data(b"CNAM", b"Second\0"),
            data(b"SNAM", b"\xff\0"),
        ]
        .concat(),
    );
    bytes.truncate(bytes.len() - 1);
    fs::write(&selected, bytes).unwrap();
    let out = good(call(&selected, &["--header-only"]));
    assert_eq!(out["author"], json!({"state":"duplicate"}));
    assert_eq!(out["description"], json!({"state":"unavailable"}));
    assert_eq!(out["header"]["lightByExtension"], true);
    for key in ["records", "sha256", "allRecordTypes"] {
        assert!(out[key].is_null());
    }
    refused(call(&selected, &[]));

    fs::write(&selected, fixture(99, &data(b"CNAM", b"\0"))).unwrap();
    let incomplete = good(call(&selected, &[]));
    assert_eq!(incomplete["records"]["scanComplete"], false);
    assert_eq!(incomplete["records"]["totalRecords"], 5);
    assert_eq!(incomplete["author"], json!({"state":"utf8","value":""}));
    assert_eq!(incomplete["description"], json!({"state":"absent"}));
}

#[test]
fn existing_guards_reject_nonplugins_wrong_selection_and_a_sparse_oversize_file() {
    let dir = tempfile::tempdir().unwrap();
    let selected = dir.path().join("Oversized.esp");
    fs::File::create(&selected)
        .unwrap()
        .set_len(256 * 1024 * 1024 + 1)
        .unwrap();
    refused(call(&selected, &[]));
    refused(call(&selected, &["--header-only"]));
    refused(call(dir.path(), &[]));
    let too_much = dir.path().join("Valid.esp");
    fs::write(&too_much, fixture(6, &[])).unwrap();
    refused(call(&too_much, &["--header-only", "--other"]));
    let wrong = dir.path().join("Wrong.txt");
    fs::write(&wrong, fixture(6, &[])).unwrap();
    refused(call(&wrong, &[]));
}

#[cfg(unix)]
#[test]
fn selected_symlink_and_fifo_refuse_promptly_with_no_writer_or_selected_bytes() {
    use std::process::Stdio;
    use std::time::{Duration, Instant};

    let dir = tempfile::tempdir().unwrap();
    let actual = dir.path().join("Synthetic.esp");
    let linked = dir.path().join("Link.esp");
    let fifo = dir.path().join("Pipe.esp");
    let bytes = fixture(6, &[]);
    fs::write(&actual, &bytes).unwrap();
    std::os::unix::fs::symlink(&actual, &linked).unwrap();
    let fifo_path = std::ffi::CString::new(fifo.as_os_str().as_encoded_bytes()).unwrap();
    // The test creates a disposable FIFO with no writer and never sends bytes.
    assert_eq!(unsafe { libc::mkfifo(fifo_path.as_ptr(), 0o600) }, 0);
    for selected in [&linked, &fifo] {
        let mut child = Command::new(env!("CARGO_BIN_EXE_modhunter-cli"))
            .args(["plugin-file-info", "--file"])
            .arg(selected)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap();
        let deadline = Instant::now() + Duration::from_secs(5);
        while child.try_wait().unwrap().is_none() {
            if Instant::now() >= deadline {
                let _ = child.kill();
                let _ = child.wait();
                panic!("Selected-file inspection stalled on a non-regular input");
            }
            std::thread::sleep(Duration::from_millis(5));
        }
        let result = child.wait_with_output().unwrap();
        assert!(String::from_utf8_lossy(&result.stderr).contains("Linked or non-regular"));
        refused(result);
    }
    assert_eq!(fs::read(actual).unwrap(), bytes);
}
